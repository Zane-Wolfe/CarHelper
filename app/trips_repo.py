"""Read-only access to saved trips for the local web viewer.

Stdlib-only for listing + per-trip detail (reads ``index.jsonl`` and
``summary.json``), so the viewer works on a minimal device install with no
pandas. The raw time-series drill-down (``load_series``) lazy-imports pandas and
degrades gracefully when it — or the parquet file — is absent.

Everything here is read-only: it only ever *reads* files under ``data/``. It
never touches the car (no OBD access) and never writes. Path inputs from HTTP
are funneled through :func:`_safe_trip_dir`, which rejects anything that would
escape ``data/trips/`` — the same traversal guard ``writer.delete_trip`` uses.
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

# Default sensor columns charted in the per-trip detail view. All are numeric
# python-OBD PIDs present in samples.parquet. NOTE: SPEED is stored in kph (see
# config.py) — the client converts to mph for display.
DEFAULT_SERIES_FIELDS: list[str] = [
    "SPEED", "RPM", "COOLANT_TEMP", "CONTROL_MODULE_VOLTAGE",
    "LONG_FUEL_TRIM_1", "LONG_FUEL_TRIM_2", "ENGINE_LOAD", "THROTTLE_POS",
]


def _index_path(data_dir: str) -> Path:
    return Path(data_dir) / "index.jsonl"


def read_index(data_dir: str) -> list[dict[str, Any]]:
    """Parse ``index.jsonl`` into a list of trip rows (oldest first).

    Malformed lines are skipped rather than raising — a single bad line must not
    make the whole history unreadable.
    """
    path = _index_path(data_dir)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def list_trips(data_dir: str, limit: int = 20) -> list[dict[str, Any]]:
    """Return the most recent ``limit`` trips, newest first."""
    rows = read_index(data_dir)
    if limit and limit > 0:
        rows = rows[-limit:]
    return list(reversed(rows))


def _safe_trip_dir(data_dir: str, ref: str) -> Path:
    """Resolve a trip directory from an untrusted ``ref`` (the folder basename).

    Rejects references that contain a path separator or otherwise resolve
    outside ``data/trips/`` — only a direct child of ``trips/`` is allowed.
    """
    if not ref or "/" in ref or "\\" in ref or ref in (".", ".."):
        raise ValueError("Invalid trip reference")
    base = (Path(data_dir) / "trips").resolve()
    target = (base / ref).resolve()
    if target.parent != base:
        raise ValueError("Invalid trip reference")
    return target


def _finding_counts(findings: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"info": 0, "watch": 0, "action": 0}
    for f in findings:
        sev = f.get("severity")
        if sev in counts:
            counts[sev] += 1
    return counts


def load_trip(data_dir: str, ref: str) -> dict[str, Any]:
    """Load one trip's full detail for the viewer.

    ``ref`` is the trip directory basename (e.g. ``2026-06-29T2347_t76848``).
    Raises :class:`ValueError` for an unsafe ref and :class:`FileNotFoundError`
    when no such trip exists.
    """
    trip_dir = _safe_trip_dir(data_dir, ref)
    summary_path = trip_dir / "summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(ref)

    summary = json.loads(summary_path.read_text())
    findings = summary.get("findings") or []
    started_at = summary.get("started_at")
    date = (
        datetime.fromtimestamp(started_at).isoformat(timespec="seconds")
        if isinstance(started_at, (int, float))
        else None
    )
    md_path = trip_dir / "summary.md"
    return {
        "ref": ref,
        "date": date,
        "trip_id": summary.get("trip_id"),
        "started_at": started_at,
        "ended_at": summary.get("ended_at"),
        "vehicle": summary.get("vehicle"),
        "metrics": summary.get("metrics") or {},
        "findings": findings,
        "finding_counts": _finding_counts(findings),
        "has_samples": (trip_dir / "samples.parquet").exists(),
        "markdown": md_path.read_text() if md_path.exists() else None,
    }


def load_series(
    data_dir: str,
    ref: str,
    fields: list[str] | None = None,
    max_points: int = 300,
) -> dict[str, Any]:
    """Return downsampled per-sample time series for charting.

    Reads ``samples.parquet`` (needs pandas/pyarrow). Returns
    ``{"available": False, ...}`` — never raises — when pandas or the parquet
    file is missing, so a minimal install still serves the detail view (just
    without charts). Downsamples to at most ``max_points`` points, keeping the
    last sample. NaNs become ``null`` so the payload is valid JSON.
    """
    trip_dir = _safe_trip_dir(data_dir, ref)
    path = trip_dir / "samples.parquet"
    if not path.exists():
        return {"available": False, "reason": "no-samples", "count": 0, "series": {}}
    try:
        import pandas as pd
    except ImportError:
        return {"available": False, "reason": "pandas-missing", "count": 0, "series": {}}

    df = pd.read_parquet(path)
    total = len(df)
    if total == 0:
        return {"available": False, "reason": "empty", "count": 0, "series": {}}

    want = fields or DEFAULT_SERIES_FIELDS
    cols = [c for c in want if c in df.columns]

    if max_points and total > max_points:
        stride = math.ceil(total / max_points)
        rows = list(range(0, total, stride))
        if rows[-1] != total - 1:
            rows.append(total - 1)
        df = df.iloc[rows]

    series: dict[str, list[float | None]] = {}
    for name in cols:
        try:
            series[name] = [
                None if pd.isna(v) else round(float(v), 3) for v in df[name]
            ]
        except (TypeError, ValueError):
            # Skip a column that isn't cleanly numeric rather than fail the request.
            continue

    return {
        "available": True,
        "count": total,
        "returned": len(df),
        "fields": list(series.keys()),
        "series": series,
    }
