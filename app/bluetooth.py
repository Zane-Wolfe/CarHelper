"""Bluetooth Classic (SPP) connection manager for the vLinker MC+ on Linux.

Drives the host BlueZ stack via ``bluetoothctl`` to scan, pair (auto-answering
the legacy PIN, default 1234), trust and connect the dongle, then binds it to a
serial device with ``rfcomm`` so python-OBD can open it as ``/dev/rfcomm0``.

This module only manages the *Bluetooth link*. It sends nothing to the vehicle;
all OBD traffic goes through app/obd_session.py.

Note: the vLinker MC+ must have its physical Connect button pressed to become
discoverable/pairable — that step cannot be automated and is surfaced to the UI.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import stat

from . import config

log = logging.getLogger(__name__)

_MAC_RE = re.compile(r"([0-9A-F]{2}(?::[0-9A-F]{2}){5})", re.IGNORECASE)
_PIN_PROMPT = re.compile(r"(PIN code|passkey|Enter PIN)", re.IGNORECASE)


class ConnectionError_(RuntimeError):
    pass


class BluetoothManager:
    def __init__(self) -> None:
        self.status: str = "disconnected"
        self.detail: str = ""
        self.mac: str | None = config.BT_MAC
        self.rfcomm_dev: str = config.RFCOMM_DEV

    def _set(self, status: str, detail: str = "") -> None:
        self.status = status
        self.detail = detail

    async def _run(self, *args: str, timeout: float = 20.0) -> tuple[int, str]:
        """Run a command, return (returncode, combined output)."""
        log.debug("running: %s", " ".join(args))
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            log.warning("command timed out after %.0fs: %s", timeout, " ".join(args))
            proc.kill()
            await proc.wait()  # reap the killed child so it doesn't linger as a zombie
            return 1, "timeout"
        return proc.returncode or 0, out.decode(errors="replace")

    async def scan_devices(self, seconds: int = 10) -> list[dict]:
        """Scan and return all nearby Bluetooth devices as [{mac, name}]."""
        prev = self.status if self.status != "scanning" else "disconnected"
        self._set("scanning", "Scanning for nearby devices…")
        await self._run("bluetoothctl", "--timeout", str(seconds), "scan", "on",
                        timeout=seconds + 5)
        _, out = await self._run("bluetoothctl", "devices")
        devices: list[dict] = []
        seen: set[str] = set()
        for line in out.splitlines():
            # "Device AA:BB:CC:DD:EE:FF vLinker MC-Android"
            m = _MAC_RE.search(line)
            if not m:
                continue
            mac = m.group(1).upper()
            if mac in seen:
                continue
            seen.add(mac)
            name = line.split(m.group(1), 1)[-1].strip() or mac
            devices.append({"mac": mac, "name": name})
        # named devices first, then alphabetical
        devices.sort(key=lambda d: (d["name"] == d["mac"], d["name"].lower()))
        self._set(prev, "")
        return devices

    async def _pair_and_trust(self, mac: str) -> None:
        """Pair (auto-PIN) and trust the device via an interactive bluetoothctl."""
        self._set("pairing", f"Pairing {mac}…")
        proc = await asyncio.create_subprocess_exec(
            "bluetoothctl",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        async def send(line: str) -> None:
            proc.stdin.write((line + "\n").encode())
            await proc.stdin.drain()

        for cmd in ("power on", "agent KeyboardOnly", "default-agent", f"pair {mac}"):
            await send(cmd)
            await asyncio.sleep(0.5)

        paired = False
        deadline = asyncio.get_event_loop().time() + 30
        try:
            while asyncio.get_event_loop().time() < deadline:
                try:
                    raw = await asyncio.wait_for(proc.stdout.readline(), timeout=5)
                except TimeoutError:
                    continue
                if not raw:
                    break
                line = raw.decode(errors="replace")
                if _PIN_PROMPT.search(line):
                    await send(config.BT_PIN)
                if "Pairing successful" in line or "already" in line.lower():
                    paired = True
                    break
                if "Failed to pair" in line or "AuthenticationFailed" in line:
                    raise ConnectionError_(f"Pairing failed: {line.strip()}")
            await send(f"trust {mac}")
            await asyncio.sleep(0.5)
            await send(f"connect {mac}")
            await asyncio.sleep(1.0)
        finally:
            await send("quit")
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except TimeoutError:
                proc.kill()
        if not paired:
            raise ConnectionError_(
                "Pairing did not complete. Press the dongle's Connect button and retry."
            )

    async def _bind_rfcomm(self, mac: str) -> str:
        self._set("connecting", f"Binding {self.rfcomm_dev}…")
        # Release any stale binding, then bind channel 1 (SPP) to the device.
        await self._run("rfcomm", "release", self.rfcomm_dev)
        rc, out = await self._run("rfcomm", "bind", self.rfcomm_dev, mac, "1")
        if rc != 0 and "exists" not in out.lower():
            raise ConnectionError_(f"rfcomm bind failed: {out.strip()}")
        await asyncio.to_thread(self._ensure_node)
        # Let the RFCOMM channel actually come up before anything opens the port.
        await asyncio.sleep(2.0)
        return self.rfcomm_dev

    def _ensure_node(self) -> None:
        """Create the rfcomm device node if it didn't appear.

        `rfcomm bind` allocates the channel kernel-side, but inside a container
        (no udev) the /dev node is not created automatically — so we make it.
        """
        if os.path.exists(self.rfcomm_dev):
            return
        major = 216  # standard RFCOMM TTY major
        try:
            for line in open("/proc/devices"):
                parts = line.split()
                if len(parts) == 2 and parts[1] == "rfcomm":
                    major = int(parts[0])
                    break
        except OSError:
            pass
        m = re.search(r"(\d+)$", self.rfcomm_dev)
        minor = int(m.group(1)) if m else 0
        try:
            os.mknod(self.rfcomm_dev, stat.S_IFCHR | 0o644, os.makedev(major, minor))
        except FileExistsError:
            log.debug("rfcomm node %s already exists", self.rfcomm_dev)
        except OSError as exc:
            log.error("could not create rfcomm node %s: %s", self.rfcomm_dev, exc)
            raise ConnectionError_(f"could not create {self.rfcomm_dev}: {exc}") from exc

    async def connect(self, mac: str) -> str:
        """Pair (if needed), connect, and bind the given device. Returns serial path."""
        try:
            if not mac:
                raise ConnectionError_("No device selected. Scan and choose your adapter.")
            self.mac = mac
            await self._pair_and_trust(mac)
            dev = await self._bind_rfcomm(mac)
            self._set("connected", f"Linked {mac} → {dev}")
            log.info("bluetooth linked %s → %s", mac, dev)
            return dev
        except ConnectionError_ as exc:
            self._set("error", str(exc))
            log.warning("bluetooth connect to %s failed: %s", mac, exc)
            raise

    async def disconnect(self) -> None:
        await self._run("rfcomm", "release", self.rfcomm_dev)
        if self.mac:
            await self._run("bluetoothctl", "disconnect", self.mac)
        self._set("disconnected", "")

    async def forget(self, mac: str | None) -> None:
        """Unpair the device entirely and release the serial binding."""
        await self._run("rfcomm", "release", self.rfcomm_dev)
        if mac:
            await self._run("bluetoothctl", "remove", mac)
        self.mac = None
        self._set("disconnected", "")
