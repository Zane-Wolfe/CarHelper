"""Tests for the trip writer: artifacts land, index is appended, summary validates."""
from __future__ import annotations

import json

from app import config, schema, writer

_METRICS = {
    "sample_count": 10,
    "duration_s": 120,
    "distance_mi": 0.9,
    "idle_pct": 40.0,
    "high_rpm_pct": 0.0,
    "max_speed_mph": 33.0,
    "coolant_max_c": 90.0,
    "ltft_b1_mean_pct": -3.1,
    "stft_b1_mean_pct": -0.4,
    "ltft_b2_mean_pct": -2.8,
    "stft_b2_mean_pct": -0.3,
    "ltft_over_watch_pct": 0.0,
    "ltft_over_action_pct": 0.0,
    "voltage_min_v": 13.2,
    "voltage_max_v": 13.9,
    "harsh_accel_events": 0,
    "harsh_brake_events": 0,
    "est_mpg": 15.1,
    "dtc_count": 0,
    "dtcs": [],
}


def test_write_trip_produces_valid_summary_and_index(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))

    trip_dir = writer.write_trip(
        trip_id="t99999",
        started_at=1782776848.82,
        ended_at=1782778965.09,
        samples=[],  # skip parquet so the test has no pandas dependency
        metrics=dict(_METRICS),
        findings=[],
        vehicle="2004 Ford Expedition",
    )

    summary_path = tmp_path / "trips" / f"{trip_dir.split('/')[-1]}" / "summary.json"
    # Resolve robustly regardless of path formatting.
    summary_path = next(tmp_path.glob("trips/*/summary.json"))
    summary = json.loads(summary_path.read_text())

    assert summary["schema_version"] == schema.SCHEMA_VERSION
    assert summary["trip_id"] == "t99999"
    schema.validate_summary(summary)  # the written artifact conforms

    index = (tmp_path / "index.jsonl").read_text().strip().splitlines()
    assert len(index) == 1
    assert json.loads(index[0])["trip_id"] == "t99999"


def test_write_trip_with_findings_validates(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))

    findings = [
        {
            "code": "voltage_low",
            "category": "health",
            "severity": "action",
            "title": "Low system voltage",
            "detail": "Minimum 11.6 V.",
            "evidence": {"voltage_min_v": 11.6},
        }
    ]
    writer.write_trip(
        trip_id="t88888",
        started_at=1782776848.82,
        ended_at=1782778000.0,
        samples=[],
        metrics=dict(_METRICS, voltage_min_v=11.6),
        findings=findings,
    )

    summary = json.loads(next(tmp_path.glob("trips/*/summary.json")).read_text())
    schema.validate_summary(summary)
    assert summary["findings"][0]["severity"] == "action"
