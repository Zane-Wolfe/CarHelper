"""Tests for the read-only trip viewer repository (app/trips_repo.py)."""
from __future__ import annotations

import os

import pytest

from app import trips_repo, writer

_METRICS = {
    "sample_count": 10,
    "duration_s": 120,
    "distance_mi": 0.9,
    "coolant_max_c": 90.0,
    "voltage_min_v": 13.2,
    "voltage_max_v": 13.9,
    "dtc_count": 0,
    "dtcs": [],
}


def _make_trip(data_dir, trip_id, started_at, findings=None):
    """Write a trip via the real writer (no samples → no pandas needed)."""
    trip_dir = writer.write_trip(
        trip_id=trip_id,
        started_at=started_at,
        ended_at=started_at + 100,
        samples=[],
        metrics=dict(_METRICS),
        findings=findings or [],
        vehicle="2004 Ford Expedition",
    )
    return os.path.basename(trip_dir)


def test_list_trips_newest_first_and_limit(tmp_path, monkeypatch):
    data_dir = str(tmp_path)
    import app.config as config

    monkeypatch.setattr(config, "DATA_DIR", data_dir)

    _make_trip(data_dir, "t00001", 1_000_000.0)
    _make_trip(data_dir, "t00002", 1_000_500.0)
    _make_trip(data_dir, "t00003", 1_001_000.0)

    trips = trips_repo.list_trips(data_dir, limit=2)
    assert [t["trip_id"] for t in trips] == ["t00003", "t00002"]

    allt = trips_repo.list_trips(data_dir, limit=20)
    assert [t["trip_id"] for t in allt] == ["t00003", "t00002", "t00001"]


def test_list_trips_empty_when_no_index(tmp_path):
    assert trips_repo.list_trips(str(tmp_path)) == []


def test_load_trip_returns_detail(tmp_path, monkeypatch):
    import app.config as config

    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    findings = [
        {
            "code": "voltage_low",
            "category": "health",
            "severity": "action",
            "title": "Low system voltage",
            "detail": "Minimum 11.6 V.",
        }
    ]
    ref = _make_trip(str(tmp_path), "t42042", 1_700_000_000.0, findings)

    detail = trips_repo.load_trip(str(tmp_path), ref)
    assert detail["trip_id"] == "t42042"
    assert detail["vehicle"] == "2004 Ford Expedition"
    assert detail["metrics"]["distance_mi"] == 0.9
    assert detail["finding_counts"] == {"info": 0, "watch": 0, "action": 1}
    assert detail["has_samples"] is False
    assert detail["markdown"] and "Trip t42042" in detail["markdown"]
    assert detail["date"] is not None


def test_load_trip_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        trips_repo.load_trip(str(tmp_path), "2026-01-01T0000_tZZZZZ")


@pytest.mark.parametrize("bad", ["../secrets", "a/b", "..", "", "foo/../bar"])
def test_load_trip_rejects_traversal(tmp_path, bad):
    with pytest.raises(ValueError):
        trips_repo.load_trip(str(tmp_path), bad)


@pytest.mark.parametrize("bad", ["../secrets", "a/b", ".."])
def test_load_series_rejects_traversal(tmp_path, bad):
    with pytest.raises(ValueError):
        trips_repo.load_series(str(tmp_path), bad)


def test_load_series_no_samples_is_graceful(tmp_path, monkeypatch):
    import app.config as config

    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    ref = _make_trip(str(tmp_path), "t42043", 1_700_000_100.0)

    result = trips_repo.load_series(str(tmp_path), ref)
    assert result["available"] is False
    assert result["reason"] == "no-samples"
    assert result["series"] == {}


def test_load_series_reads_and_downsamples(tmp_path):
    """Exercises the parquet path; skips where pandas/pyarrow aren't installed."""
    pd = pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")

    trip_dir = tmp_path / "trips" / "2026-06-29T2347_t76848"
    trip_dir.mkdir(parents=True)
    n = 1000
    df = pd.DataFrame(
        {
            "SPEED": [float(i % 120) for i in range(n)],
            "RPM": [800.0 + i for i in range(n)],
            "COOLANT_TEMP": [90.0] * n,
        }
    )
    # A NaN must serialize to null, not crash / emit invalid JSON. Place it on
    # row 0 so it survives downsampling (stride keeps indices 0, stride, 2*stride…).
    df.loc[0, "SPEED"] = float("nan")
    df.to_parquet(trip_dir / "samples.parquet", engine="pyarrow", index=False)

    result = trips_repo.load_series(
        str(tmp_path), "2026-06-29T2347_t76848", fields=["SPEED", "RPM"], max_points=100
    )
    assert result["available"] is True
    assert result["count"] == n
    assert result["returned"] <= 101  # downsampled (+ forced last point)
    assert set(result["fields"]) == {"SPEED", "RPM"}
    assert None in result["series"]["SPEED"]  # the NaN survived as null
    assert "COOLANT_TEMP" not in result["series"]  # not requested
