"""Tests for the device-side sync client (uses a fake transport — no network)."""
from __future__ import annotations

import json

from app import schema
from sync import Response, SyncClient, SyncState


class FakeTransport:
    def __init__(self, status: int = 201, raises: bool = False) -> None:
        self.status = status
        self.raises = raises
        self.calls: list[tuple[str, str | None, str]] = []

    def post_json(self, url: str, token: str | None, payload: dict) -> Response:
        self.calls.append((url, token, payload["trip_id"]))
        if self.raises:
            raise ConnectionError("connection refused")
        return Response(self.status, "{}")


def _make(tmp_path, trips: list[tuple[str, str]]) -> None:
    for trip_id, rel in trips:
        (tmp_path / rel).mkdir(parents=True, exist_ok=True)
        summary = schema.build_summary(
            trip_id, 1000.0, 2000.0, {"dtc_count": 0, "dtcs": []}, [], vehicle=None
        )
        (tmp_path / rel / "summary.json").write_text(json.dumps(summary))
        with (tmp_path / "index.jsonl").open("a") as fh:
            fh.write(json.dumps({"trip_id": trip_id, "dir": rel}) + "\n")


def test_pushes_pending_then_marks_synced(tmp_path):
    _make(tmp_path, [("t1", "trips/a"), ("t2", "trips/b")])
    transport = FakeTransport(201)
    state = SyncState(tmp_path / "sync_state.json")

    report = SyncClient("http://localhost:8080", "tok", transport).push_pending(tmp_path, state)

    assert sorted(report.pushed) == ["t1", "t2"]
    assert len(transport.calls) == 2
    assert transport.calls[0][0] == "http://localhost:8080/v1/trips"
    assert transport.calls[0][1] == "tok"
    assert state.load() == {"t1", "t2"}


def test_rerun_pushes_nothing_new(tmp_path):
    _make(tmp_path, [("t1", "trips/a")])
    state = SyncState(tmp_path / "sync_state.json")
    SyncClient("http://x", "t", FakeTransport(201)).push_pending(tmp_path, state)

    transport2 = FakeTransport(201)
    report = SyncClient("http://x", "t", transport2).push_pending(tmp_path, state)
    assert report.pushed == []
    assert transport2.calls == []


def test_idempotent_200_counts_as_pushed(tmp_path):
    _make(tmp_path, [("t1", "trips/a")])
    state = SyncState(tmp_path / "sync_state.json")
    report = SyncClient("http://x", "t", FakeTransport(200)).push_pending(tmp_path, state)
    assert report.pushed == ["t1"]


def test_unauthorized_aborts_the_run(tmp_path):
    _make(tmp_path, [("t1", "trips/a"), ("t2", "trips/b")])
    transport = FakeTransport(401)
    state = SyncState(tmp_path / "s.json")
    report = SyncClient("http://x", "bad", transport).push_pending(tmp_path, state)

    assert report.pushed == []
    assert len(transport.calls) == 1  # stopped after the first failure
    assert report.failed[0][1] == "unauthorized"


def test_server_error_is_retryable(tmp_path):
    _make(tmp_path, [("t1", "trips/a")])
    state = SyncState(tmp_path / "s.json")
    report = SyncClient("http://x", "t", FakeTransport(503)).push_pending(tmp_path, state)
    assert report.failed and "503" in report.failed[0][1]
    assert state.load() == set()  # not marked, will retry next run


def test_bad_payload_is_skipped_not_retried(tmp_path):
    _make(tmp_path, [("t1", "trips/a")])
    state = SyncState(tmp_path / "s.json")
    report = SyncClient("http://x", "t", FakeTransport(422)).push_pending(tmp_path, state)
    assert report.skipped == ["t1"]
    assert state.load() == set()


def test_missing_summary_is_skipped(tmp_path):
    (tmp_path / "index.jsonl").write_text(json.dumps({"trip_id": "t1", "dir": "trips/gone"}) + "\n")
    transport = FakeTransport(201)
    state = SyncState(tmp_path / "s.json")
    report = SyncClient("http://x", "t", transport).push_pending(tmp_path, state)
    assert report.skipped == ["t1"]
    assert transport.calls == []


def test_transport_error_is_retryable(tmp_path):
    _make(tmp_path, [("t1", "trips/a")])
    state = SyncState(tmp_path / "s.json")
    report = SyncClient("http://x", "t", FakeTransport(raises=True)).push_pending(tmp_path, state)
    assert report.failed and "transport error" in report.failed[0][1]
    assert state.load() == set()
