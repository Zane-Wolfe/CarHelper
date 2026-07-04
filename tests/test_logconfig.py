"""Tests for the shared logging setup (app/logconfig.py)."""
from __future__ import annotations

import logging

from app.logconfig import _MARKER, setup_logging


def _our_handlers() -> list[logging.Handler]:
    root = logging.getLogger()
    return [h for h in root.handlers if getattr(h, "_carhelper", None) == _MARKER]


def test_setup_is_idempotent() -> None:
    setup_logging("INFO")
    first = len(_our_handlers())
    setup_logging("INFO")
    second = len(_our_handlers())
    # Calling twice must not stack duplicate handlers.
    assert first == 1
    assert second == 1


def test_respects_explicit_level_arg() -> None:
    setup_logging("DEBUG")
    assert logging.getLogger().level == logging.DEBUG
    setup_logging("warning")  # case-insensitive
    assert logging.getLogger().level == logging.WARNING


def test_respects_log_level_env(monkeypatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "error")
    setup_logging()  # no explicit arg → read from env
    assert logging.getLogger().level == logging.ERROR


def test_unknown_level_falls_back_to_info() -> None:
    setup_logging("NOPE")
    assert logging.getLogger().level == logging.INFO


def test_error_is_emitted(caplog) -> None:
    setup_logging("INFO")
    log = logging.getLogger("app.test_logconfig")
    with caplog.at_level(logging.ERROR):
        log.error("boom happened")
    assert any(r.levelno == logging.ERROR and "boom happened" in r.getMessage()
               for r in caplog.records)
