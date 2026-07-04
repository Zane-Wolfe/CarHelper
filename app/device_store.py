"""Persist the user's chosen OBD adapter so Connect defaults to it next time.

Stored in DATA_DIR/device.json, which is mounted to the host ./data — so the
saved adapter survives container restarts. "Forget device" clears it.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from . import config

log = logging.getLogger(__name__)


def _path() -> Path:
    return Path(config.DATA_DIR) / "device.json"


def load() -> dict | None:
    p = _path()
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text())
        return d if d.get("mac") else None
    except Exception:
        log.warning("could not read saved device file %s — ignoring", p, exc_info=True)
        return None


def save(mac: str, name: str) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"mac": mac, "name": name}))


def clear() -> None:
    p = _path()
    if p.exists():
        p.unlink()
