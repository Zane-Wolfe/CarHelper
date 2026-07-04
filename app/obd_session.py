"""Read-only OBD-II access.

ALL communication with the car flows through ``safe_query``. It refuses any
command not on the read-only allowlist (``config.READ_ONLY_COMMANDS``), so the
app can never send a clear-codes / reset / control command to the vehicle —
even by mistake in some other module. This is the single enforcement point for
the project's non-negotiable read-only safety guarantee.

Sensor coverage is auto-detected: at connect time we ask the vehicle which of
our candidate Mode-01 PIDs it actually supports, and watch only those.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from . import config


class NonReadOnlyCommandError(PermissionError):
    """Raised if any code attempts an OBD command outside the read-only allowlist."""


def safe_query(connection: Any, command: Any) -> Any:
    """Query the car only if ``command`` is read-only. Otherwise refuse.

    This is the chokepoint: the car is queried exclusively from here.
    """
    name = getattr(command, "name", str(command))
    if name not in config.READ_ONLY_COMMANDS:
        raise NonReadOnlyCommandError(
            f"Blocked non-read-only OBD command '{name}'. CarHelper is read-only "
            f"and must never change vehicle state."
        )
    return connection.query(command)


def _command(name: str):
    import obd  # imported lazily so SIMULATE mode needs no obd install

    cmd = getattr(obd.commands, name, None)
    if cmd is None:
        raise ValueError(f"Unknown OBD command: {name}")
    return cmd


def _magnitude(value: Any) -> float | None:
    if value is None:
        return None
    mag = getattr(value, "magnitude", value)
    try:
        return float(mag)
    except (TypeError, ValueError):
        return None


class OBDSource:
    """Live, read-only telemetry from a connected ELM327 (e.g. vLinker MC+)."""

    name = "obd"

    def __init__(self, port: str):
        self.port = port
        self._conn = None
        self._core: list[str] = []
        self._extended: list[str] = []
        # Serializes all access to the single serial connection — the live poll
        # loop and an on-demand codes read must never query the ECU at once.
        self._lock = asyncio.Lock()
        self._use_elm_voltage: bool = False  # fallback when ECU doesn't support PID 0x42

    async def connect(self) -> None:
        import obd

        # fast=False keeps a conservative, well-supported query cadence.
        # Retry a couple of times — a freshly-bound rfcomm channel can take a
        # moment to come up on the first open.
        for _attempt in range(3):
            self._conn = await asyncio.to_thread(
                obd.OBD, self.port, fast=False, timeout=2.0
            )
            if self._conn.is_connected():
                break
            await asyncio.to_thread(self._conn.close)
            self._conn = None
            await asyncio.sleep(1.5)
        if self._conn is None or not self._conn.is_connected():
            raise ConnectionError(f"Could not establish OBD connection on {self.port}")
        # Keep only the candidate PIDs the vehicle actually supports.
        supports = self._conn.supports
        self._core = [n for n in config.CORE_PIDS if supports(_command(n))]
        self._extended = [n for n in config.EXTENDED_PIDS if supports(_command(n))]
        # ELM_VOLTAGE (AT RV) is an adapter command, not an OBD PID — the vehicle
        # won't declare support for it, so we probe it once and set a flag.
        if "CONTROL_MODULE_VOLTAGE" not in self._core + self._extended:
            try:
                resp = safe_query(self._conn, _command("ELM_VOLTAGE"))
                if not resp.is_null():
                    self._use_elm_voltage = True
            except Exception:
                pass

    @property
    def supported_pids(self) -> list[str]:
        return self._core + self._extended

    def _poll_sync(self, full: bool) -> dict:
        sample: dict = {"ts": time.time()}
        pids = self._core + (self._extended if full else [])
        for name in pids:
            resp = safe_query(self._conn, _command(name))
            sample[name] = None if resp.is_null() else _magnitude(resp.value)
        # If CONTROL_MODULE_VOLTAGE wasn't in the vehicle's supported PIDs, use the
        # adapter's own AT RV reading (OBD-II pin 16 = battery voltage) as a fallback.
        if self._use_elm_voltage and sample.get("CONTROL_MODULE_VOLTAGE") is None:
            try:
                resp = safe_query(self._conn, _command("ELM_VOLTAGE"))
                sample["CONTROL_MODULE_VOLTAGE"] = (
                    None if resp.is_null() else _magnitude(resp.value)
                )
            except Exception:
                pass
        return sample

    async def poll(self, full: bool = False) -> dict:
        async with self._lock:
            return await asyncio.to_thread(self._poll_sync, full)

    # --- diagnostic codes / status (read-only) ---
    def _report_sync(self) -> dict:
        report: dict = {
            "ts": time.time(),
            "mil": None, "dtc_count": None, "ignition_type": None,
            "monitors": [], "stored": [], "pending": [],
            "supported_count": len(self.supported_pids),
        }
        st = safe_query(self._conn, _command("STATUS"))
        if not st.is_null() and st.value is not None:
            s = st.value
            report["mil"] = bool(getattr(s, "MIL", False))
            report["dtc_count"] = int(getattr(s, "DTC_count", 0) or 0)
            report["ignition_type"] = getattr(s, "ignition_type", None)
            for attr in vars(s):
                # Skip the scalar fields and any non-string/reserved attribute
                # names (python-OBD can expose a None-named reserved monitor bit).
                if not isinstance(attr, str) or attr in ("MIL", "DTC_count", "ignition_type"):
                    continue
                t = getattr(s, attr)
                # Only include monitors the vehicle actually runs.
                if getattr(t, "available", False):
                    report["monitors"].append({
                        "name": getattr(t, "name", attr),
                        "complete": bool(getattr(t, "complete", False)),
                    })
        report["stored"] = self._dtc_list("GET_DTC")
        report["pending"] = self._dtc_list("GET_CURRENT_DTC")
        return report

    def _dtc_list(self, cmd_name: str) -> list[dict]:
        try:
            resp = safe_query(self._conn, _command(cmd_name))
        except Exception:
            return []
        if resp.is_null() or not resp.value:
            return []
        return [{"code": code, "description": desc} for code, desc in resp.value]

    async def read_report(self) -> dict:
        async with self._lock:
            return await asyncio.to_thread(self._report_sync)

    async def read_dtcs(self) -> list[dict]:
        async with self._lock:
            return await asyncio.to_thread(self._dtc_list, "GET_DTC")

    def _read_vin_sync(self) -> str | None:
        try:
            resp = safe_query(self._conn, _command("VIN"))
        except Exception:
            return None
        return None if resp.is_null() else str(resp.value)

    async def read_vin(self) -> str | None:
        async with self._lock:
            return await asyncio.to_thread(self._read_vin_sync)

    async def close(self) -> None:
        if self._conn is not None:
            await asyncio.to_thread(self._conn.close)
            self._conn = None
