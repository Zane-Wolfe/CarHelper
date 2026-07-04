"""Push unsynced trip summaries to a sync endpoint.

Reads the same artifacts the device writes (`index.jsonl` + per-trip
`summary.json`) and POSTs each summary to `{endpoint}/v1/trips`. The endpoint is
configurable — the hosted service or any self-hosted implementation of the
published OpenAPI sync contract.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from .state import SyncState
from .transport import Transport

log = logging.getLogger(__name__)


@dataclass
class SyncReport:
    pushed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # won't retry (bad payload / missing)
    failed: list[tuple[str, str]] = field(default_factory=list)  # retryable next run


def _iter_index(data_dir: Path) -> Iterator[dict]:
    index = data_dir / "index.jsonl"
    if not index.exists():
        return
    for line in index.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            log.warning("skipping malformed index line")


@dataclass
class SyncClient:
    endpoint: str  # base URL, e.g. http://localhost:8080
    token: str | None
    transport: Transport

    def push_pending(self, data_dir: str | Path, state: SyncState) -> SyncReport:
        data_dir = Path(data_dir)
        synced = state.load()
        report = SyncReport()
        url = f"{self.endpoint.rstrip('/')}/v1/trips"
        log.info("sync push starting → %s (%d already synced)", url, len(synced))

        for entry in _iter_index(data_dir):
            trip_id = entry.get("trip_id")
            rel = entry.get("dir")
            if not trip_id or not rel or trip_id in synced:
                continue

            summary_path = data_dir / rel / "summary.json"
            if not summary_path.exists():
                log.warning("trip %s: summary.json missing at %s — skipping", trip_id, summary_path)
                report.skipped.append(trip_id)
                continue
            payload = json.loads(summary_path.read_text())

            log.debug("trip %s: POST %s", trip_id, url)
            try:
                resp = self.transport.post_json(url, self.token, payload)
            except Exception as exc:  # noqa: BLE001 - network is retryable
                log.warning("trip %s: transport error: %s", trip_id, exc, exc_info=True)
                report.failed.append((trip_id, f"transport error: {exc}"))
                continue

            if resp.status in (200, 201):
                state.mark(trip_id)
                report.pushed.append(trip_id)
                log.debug("trip %s: synced (%s)", trip_id, resp.status)
            elif resp.status == 401:
                # Auth is misconfigured — stop; retrying every trip won't help.
                report.failed.append((trip_id, "unauthorized"))
                log.error("sync unauthorized — check the token; aborting run")
                break
            elif 400 <= resp.status < 500:
                # The server rejected the payload (contract mismatch); don't loop on it.
                report.skipped.append(trip_id)
                log.warning("trip %s rejected (%s): %s", trip_id, resp.status, resp.body[:200])
            else:
                report.failed.append((trip_id, f"server {resp.status}"))
                log.warning("trip %s: server error %s — will retry next run", trip_id, resp.status)

        log.info("sync push done: %d pushed, %d skipped, %d failed",
                 len(report.pushed), len(report.skipped), len(report.failed))
        return report
