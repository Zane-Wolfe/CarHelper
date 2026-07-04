"""Tracks which trips have been synced, so re-runs only push new trips.

Sync is also idempotent server-side (ingest is keyed by trip_id), so this file is
an optimization, not a correctness requirement — losing it re-pushes everything
harmlessly.
"""
from __future__ import annotations

import json
from pathlib import Path


class SyncState:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> set[str]:
        if not self.path.exists():
            return set()
        try:
            data = json.loads(self.path.read_text())
        except json.JSONDecodeError:
            return set()
        return set(data.get("synced", []))

    def mark(self, trip_id: str) -> None:
        synced = self.load()
        synced.add(trip_id)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"synced": sorted(synced)}, indent=2))
