"""Shared logging setup for the CarHelper device app.

One place configures the root logger so every module's
``logging.getLogger(__name__)`` inherits a consistent format. Stdlib only — no
new dependencies, and nothing here touches the car.

Level policy used across the app:
  ERROR   — an operation failed / was aborted (log with a stack trace)
  WARNING — recoverable / degraded (retry, skipped item, transient poll error)
  INFO    — lifecycle + key state transitions (startup, connect, trip start/stop)
  DEBUG   — detailed flow (per-sample poll, per-OBD-query, ws connect/disconnect)
"""
from __future__ import annotations

import logging
import os

_LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"

# Marks the handler this module installs so repeat calls replace it instead of
# stacking duplicates (idempotent setup).
_MARKER = "carhelper_stream_handler"


def _resolve_level(level: str | None) -> int:
    name = (level or os.environ.get("LOG_LEVEL") or "INFO").strip().upper()
    resolved = logging.getLevelName(name)  # int for known names, else "Level <name>"
    return resolved if isinstance(resolved, int) else logging.INFO


def setup_logging(level: str | None = None) -> None:
    """Configure the root logger idempotently.

    Level resolves from the ``level`` arg, else ``$LOG_LEVEL``, else ``INFO``
    (case-insensitive). Calling this twice must not add duplicate handlers.
    """
    resolved = _resolve_level(level)
    root = logging.getLogger()

    # Remove any handler we previously installed so a second call replaces
    # rather than duplicates it.
    for h in list(root.handlers):
        if getattr(h, "_carhelper", None) == _MARKER:
            root.removeHandler(h)

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
    handler._carhelper = _MARKER  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(resolved)

    # Let uvicorn's loggers propagate to our root handler instead of printing on
    # their own handlers (which would double-print with different formatting).
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
