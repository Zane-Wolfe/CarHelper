"""FastAPI app: connect, trip start/stop, live WebSocket, and trip history.

Holds a single CaptureService that owns the OBD source, the background poll
loop, and the in-progress trip buffer. On trip stop it computes features, runs
rules, and writes artifacts via writer.write_trip.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, device_store, features, rules, trips_repo, writer
from .bluetooth import BluetoothManager
from .logconfig import setup_logging
from .obd_session import OBDSource

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


class NeedDeviceError(Exception):
    """Raised when connecting but no adapter is saved/selected yet (UI should scan)."""


class CaptureService:
    def __init__(self) -> None:
        self.bt = BluetoothManager()
        self.source = None
        self.poll_task: asyncio.Task | None = None
        self.trip: dict | None = None
        self.latest: dict = {}
        self.supported: list[str] = []
        self.metrics: dict = {}
        self.findings: list[dict] = []
        self.clients: set[WebSocket] = set()
        self.vehicle: str | None = os.environ.get("VEHICLE")
        self.saved: dict | None = device_store.load()
        if self.saved and not config.SIMULATE:
            self.bt.mac = self.saved.get("mac")
        self._running = False

    @property
    def connected(self) -> bool:
        return self.source is not None

    async def connect(self, mac: str | None = None, name: str | None = None) -> None:
        if self.connected:
            log.debug("connect() ignored — already connected")
            return
        if config.SIMULATE:
            from .simulate import SimSource

            log.info("connecting in SIMULATE mode")
            self.source = SimSource()
            await self.source.connect()
            self.bt.status, self.bt.detail = "connected", "SIMULATE mode"
        else:
            target = mac or (self.saved or {}).get("mac") or config.BT_MAC
            if not target:
                log.info("connect requested but no saved adapter — UI must scan")
                raise NeedDeviceError("No saved adapter — scan and choose your device.")
            log.info("connecting to adapter %s", target)
            dev = await self.bt.connect(target)
            # Assign self.source only once the OBD handshake actually succeeds —
            # otherwise a failed connect() would leave a dead source attached and
            # the app stuck reporting connected=True (unable to retry without a
            # restart). On any failure, tear the half-open state back down.
            source = OBDSource(dev)
            try:
                await source.connect()
            except Exception:
                log.exception("OBD handshake failed on %s — tearing down half-open link", dev)
                await source.close()
                try:
                    await self.bt.disconnect()
                except Exception:
                    log.debug("bt.disconnect() during connect cleanup failed", exc_info=True)
                raise
            self.source = source
            nm = name or (self.saved or {}).get("name") or config.BT_NAME
            self.saved = {"mac": target, "name": nm}
            device_store.save(target, nm)
        self.supported = self.source.supported_pids
        log.info("connected — %d supported PIDs", len(self.supported))
        self.latest = {}
        self._running = True
        self.poll_task = asyncio.create_task(self._poll_loop())

    async def read_codes(self) -> dict:
        if not self.connected:
            raise RuntimeError("Connect to the car first to read diagnostic codes.")
        log.debug("reading diagnostic report (codes/monitors)")
        return await self.source.read_report()

    async def scan(self) -> list[dict]:
        if config.SIMULATE:
            return [{"mac": "SIMULATED", "name": "Simulated OBD adapter"}]
        devices = await self.bt.scan_devices()
        log.info("scan found %d bluetooth device(s)", len(devices))
        return devices

    async def forget(self) -> None:
        mac = (self.saved or {}).get("mac")
        log.info("forgetting saved adapter %s", mac)
        await self.disconnect()
        if not config.SIMULATE:
            await self.bt.forget(mac)
        self.saved = None
        device_store.clear()

    async def disconnect(self) -> None:
        log.info("disconnecting")
        if self.trip is not None:
            await self.stop_trip()
        self._running = False
        if self.poll_task:
            self.poll_task.cancel()
            try:
                await self.poll_task
            except asyncio.CancelledError:
                pass
            except Exception:
                log.debug("poll task raised during cancellation", exc_info=True)
            self.poll_task = None
        if self.source is not None:
            await self.source.close()
            self.source = None
        if not config.SIMULATE:
            await self.bt.disconnect()
        else:
            self.bt.status, self.bt.detail = "disconnected", ""

    async def start_trip(self) -> str:
        if not self.connected:
            raise RuntimeError("Not connected to the car.")
        if self.trip is not None:
            return self.trip["trip_id"]
        dtcs = await self.source.read_dtcs()
        started = time.time()
        self.trip = {
            "trip_id": f"t{int(started) % 100000:05d}",
            "started_at": started,
            "samples": [],
            "dtcs": dtcs,
        }
        self.metrics, self.findings = {}, []
        log.info("trip started trip_id=%s (%d pre-existing stored DTC(s))",
                 self.trip["trip_id"], len(dtcs))
        return self.trip["trip_id"]

    async def stop_trip(self) -> dict | None:
        if self.trip is None:
            return None
        trip, self.trip = self.trip, None
        metrics = features.compute(trip["samples"], trip["dtcs"])
        findings = rules.evaluate(metrics)
        path = writer.write_trip(
            trip["trip_id"], trip["started_at"], time.time(),
            trip["samples"], metrics, findings, self.vehicle,
        )
        self.metrics, self.findings = metrics, findings
        log.info("trip stopped trip_id=%s path=%s samples=%d findings=%d",
                 trip["trip_id"], path, len(trip["samples"]), len(findings))
        return {"trip_id": trip["trip_id"], "path": path,
                "metrics": metrics, "findings": findings}

    async def _poll_loop(self) -> None:
        period = 1.0 / max(0.1, config.SAMPLE_HZ)
        once_per_sec = max(1, int(round(config.SAMPLE_HZ)))
        i = 0
        log.debug("poll loop started (period=%.3fs)", period)
        while self._running:
            start = time.time()
            i += 1
            try:
                # Poll the extended (slower) sensor set every Nth cycle.
                full = (i % config.EXTENDED_EVERY == 0)
                sample = await self.source.poll(full=full)
            except Exception as exc:  # keep the loop alive on transient errors
                # Never crash mid-drive: preserve the UI detail and log the
                # transient error (with stack trace) so it can be found later.
                self.bt.detail = f"poll error: {exc}"
                log.warning("transient poll error (cycle %d): %s — continuing",
                            i, exc, exc_info=True)
                await asyncio.sleep(period)
                continue
            log.debug("poll cycle %d full=%s fields=%d", i, full, len(sample))
            # Merge into latest so extended readings persist between full polls.
            self.latest = {**self.latest, **sample}
            if self.trip is not None:
                self.trip["samples"].append(dict(self.latest))
                if i % once_per_sec == 0:
                    self.metrics = features.compute(self.trip["samples"], self.trip["dtcs"])
                    self.findings = rules.evaluate(self.metrics)
            await self._broadcast()
            await asyncio.sleep(max(0.0, period - (time.time() - start)))

    def status_payload(self) -> dict:
        return {
            "connected": self.connected,
            "simulate": config.SIMULATE,
            "bt_status": self.bt.status,
            "bt_detail": self.bt.detail,
            "trip_active": self.trip is not None,
            "trip_id": self.trip["trip_id"] if self.trip else None,
            "sample_count": len(self.trip["samples"]) if self.trip else 0,
            "latest": self.latest,
            "metrics": self.metrics,
            "findings": self.findings,
            "vehicle": self.vehicle,
            "saved_device": self.saved,
            "supported_pids": self.supported,
        }

    async def _broadcast(self) -> None:
        if not self.clients:
            return
        msg = json.dumps(self.status_payload())
        for ws in list(self.clients):
            try:
                await ws.send_text(msg)
            except Exception:
                log.debug("dropping websocket client after send failure", exc_info=True)
                self.clients.discard(ws)


service = CaptureService()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_logging(config.LOG_LEVEL)
    log.info("CarHelper starting (simulate=%s, sample_hz=%s)", config.SIMULATE, config.SAMPLE_HZ)
    yield
    # On shutdown: cancel the poll loop and close the OBD source so the process
    # exits promptly instead of lingering on a live connection.
    log.info("CarHelper shutting down")
    try:
        await service.disconnect()
    except Exception:
        log.exception("error during shutdown disconnect")


app = FastAPI(title="CarHelper", lifespan=lifespan)


@app.get("/api/status")
async def status():
    return service.status_payload()


@app.post("/api/connect")
async def connect(req: Request):
    try:
        body = await req.json()
    except Exception:
        log.debug("connect: no/invalid JSON body, defaulting to empty", exc_info=True)
        body = {}
    try:
        await service.connect(body.get("mac"), body.get("name"))
        return service.status_payload()
    except NeedDeviceError as exc:
        log.info("connect needs device selection: %s", exc)
        return JSONResponse(status_code=409, content={"error": str(exc), "need_scan": True})
    except Exception as exc:
        log.exception("connect failed")
        return JSONResponse(status_code=502, content={"error": str(exc),
                                                       "bt_detail": service.bt.detail})


@app.post("/api/scan")
async def scan():
    try:
        return {"devices": await service.scan()}
    except Exception as exc:
        log.exception("scan failed")
        return JSONResponse(status_code=502, content={"error": str(exc)})


@app.post("/api/forget")
async def forget():
    await service.forget()
    return service.status_payload()


@app.get("/api/device")
async def device():
    return {"saved": service.saved}


@app.get("/api/codes")
async def codes():
    """Read-only diagnostic report: MIL state, readiness monitors, stored & pending DTCs."""
    try:
        return await service.read_codes()
    except Exception as exc:
        log.exception("read_codes failed")
        return JSONResponse(status_code=409, content={"error": str(exc)})


@app.post("/api/disconnect")
async def disconnect():
    await service.disconnect()
    return service.status_payload()


@app.post("/api/trip/start")
async def trip_start():
    try:
        trip_id = await service.start_trip()
        return {"trip_id": trip_id}
    except Exception as exc:
        log.exception("trip start failed")
        return JSONResponse(status_code=400, content={"error": str(exc)})


@app.post("/api/trip/stop")
async def trip_stop():
    result = await service.stop_trip()
    if result is None:
        return JSONResponse(status_code=400, content={"error": "No active trip."})
    return result


@app.post("/api/trip/delete")
async def trip_delete(req: Request):
    try:
        body = await req.json()
    except Exception:
        log.debug("trip_delete: no/invalid JSON body, defaulting to empty", exc_info=True)
        body = {}
    dir_rel = body.get("dir")
    if not dir_rel:
        return JSONResponse(status_code=400, content={"error": "Missing trip dir."})
    try:
        writer.delete_trip(dir_rel)
        log.info("deleted trip dir=%s", dir_rel)
        return {"ok": True}
    except Exception as exc:
        log.exception("trip delete failed for dir=%s", dir_rel)
        return JSONResponse(status_code=400, content={"error": str(exc)})


@app.get("/api/trips")
async def trips(limit: int = 20):
    return {"trips": trips_repo.list_trips(config.DATA_DIR, limit)}


@app.get("/api/trips/{ref}")
async def trip_detail(ref: str):
    """Full detail for one saved trip (read-only): metrics, findings, summary."""
    try:
        return trips_repo.load_trip(config.DATA_DIR, ref)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    except FileNotFoundError:
        return JSONResponse(status_code=404, content={"error": "Trip not found."})


@app.get("/api/trips/{ref}/series")
async def trip_series(ref: str, fields: str | None = None, max_points: int = 300):
    """Downsampled per-sample time series for the trip's charts (read-only)."""
    field_list = [f for f in (fields or "").split(",") if f] or None
    try:
        return trips_repo.load_series(config.DATA_DIR, ref, field_list, max_points)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})


@app.websocket("/ws/live")
async def ws_live(ws: WebSocket):
    await ws.accept()
    service.clients.add(ws)
    log.debug("websocket connected (%d client(s))", len(service.clients))
    try:
        await ws.send_text(json.dumps(service.status_payload()))
        while True:
            await ws.receive_text()  # keepalive / ignore client messages
    except WebSocketDisconnect:
        log.debug("websocket disconnected")
    finally:
        service.clients.discard(ws)


# Static UI mounted last so /api and /ws routes win.
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
