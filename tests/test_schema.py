"""Tests for the trip-summary contract (schema/summary.schema.json)."""
from __future__ import annotations

import copy

import pytest

from app import schema

# A representative, schema-valid summary (mirrors a real trip's shape).
_METRICS = {
    "sample_count": 118,
    "duration_s": 2116,
    "distance_mi": 1.38,
    "idle_pct": 31.9,
    "high_rpm_pct": 0.0,
    "max_speed_mph": 55.9,
    "coolant_max_c": 96.0,
    "ltft_b1_mean_pct": -5.3,
    "stft_b1_mean_pct": -0.8,
    "ltft_b2_mean_pct": -4.7,
    "stft_b2_mean_pct": -0.6,
    "ltft_over_watch_pct": 38.6,
    "ltft_over_action_pct": 0.0,
    "voltage_min_v": 13.1,
    "voltage_max_v": 13.7,
    "harsh_accel_events": 0,
    "harsh_brake_events": 0,
    "est_mpg": 13.8,
    "dtc_count": 0,
    "dtcs": [],
}

_FINDING = {
    "code": "fuel_trim",
    "category": "health",
    "severity": "watch",
    "title": "Fuel trim outside normal range",
    "detail": "Mean fuel-trim magnitude 9.1%.",
    "evidence": {"ltft_b1_mean_pct": -9.1},
}


def _valid_summary() -> dict:
    return schema.build_summary(
        trip_id="t76848",
        started_at=1782776848.82,
        ended_at=1782778965.09,
        metrics=copy.deepcopy(_METRICS),
        findings=[copy.deepcopy(_FINDING)],
        vehicle="2004 Ford Expedition",
    )


def test_build_summary_stamps_version():
    summary = _valid_summary()
    assert summary["schema_version"] == schema.SCHEMA_VERSION
    # The JSON Schema's const must match the module constant, or they can drift.
    assert schema.load_schema()["properties"]["schema_version"]["const"] == (
        schema.SCHEMA_VERSION
    )


def test_valid_summary_passes():
    schema.validate_summary(_valid_summary())  # must not raise


def test_short_trip_with_null_metrics_is_valid():
    # J1850 / very short trips leave many metrics null — the contract allows it.
    summary = schema.build_summary(
        trip_id="t23056",
        started_at=1781523056.49,
        ended_at=None,
        metrics={k: None for k in _METRICS if k != "dtcs"} | {"dtcs": [], "dtc_count": 0},
        findings=[],
    )
    schema.validate_summary(summary)


def test_missing_required_field_fails():
    summary = _valid_summary()
    del summary["trip_id"]
    with pytest.raises(schema.SummaryValidationError):
        schema.validate_summary(summary)


def test_wrong_schema_version_fails():
    summary = _valid_summary()
    summary["schema_version"] = 999
    with pytest.raises(schema.SummaryValidationError):
        schema.validate_summary(summary)


def test_bad_severity_enum_fails():
    summary = _valid_summary()
    summary["findings"][0]["severity"] = "critical"  # not in enum
    with pytest.raises(schema.SummaryValidationError):
        schema.validate_summary(summary)


def test_unknown_metric_key_fails():
    # additionalProperties:false on metrics enforces the version-bump discipline.
    summary = _valid_summary()
    summary["metrics"]["boost_psi"] = 12.0
    with pytest.raises(schema.SummaryValidationError):
        schema.validate_summary(summary)


def test_finding_requires_a_code():
    summary = _valid_summary()
    del summary["findings"][0]["code"]
    with pytest.raises(schema.SummaryValidationError):
        schema.validate_summary(summary)
