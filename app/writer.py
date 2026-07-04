"""Write per-trip artifacts: samples.parquet, summary.json, summary.md, and
append a rolled-up line to data/index.jsonl.

These files are the interface to Claude Code: small, consistent, and digestible.
index.jsonl is the time-window entry point; summary.md is the per-trip digest;
samples.parquet is raw drill-down only.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

from . import config, rules, schema

log = logging.getLogger(__name__)


def _fmt_duration(seconds: float | int | None) -> str:
    if not seconds:
        return "0 min"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m = rem // 60
    return f"{h}h {m:02d}m" if h else f"{m} min"


def _trip_dir(trip_id: str, started_at: float) -> Path:
    stamp = datetime.fromtimestamp(started_at).strftime("%Y-%m-%dT%H%M")
    d = Path(config.DATA_DIR) / "trips" / f"{stamp}_{trip_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_parquet(path: Path, samples: list[dict]) -> None:
    import pandas as pd

    pd.DataFrame(samples).to_parquet(path, engine="pyarrow", index=False)


def _summary_md(trip_id, started_at, metrics, findings, vehicle) -> str:
    date = datetime.fromtimestamp(started_at).strftime("%Y-%m-%d %H:%M")
    dist = metrics.get("distance_mi")
    lines = [
        f"# Trip {trip_id} — {date}, "
        f"{dist if dist is not None else '?'} mi, {_fmt_duration(metrics.get('duration_s'))}",
    ]
    if vehicle:
        lines.append(f"_Vehicle: {vehicle}_")

    lines.append("\n## Findings")
    if findings:
        for f in findings:
            lines.append(f"- [{f['severity']}] {f['title']} — {f['detail']}")
    else:
        lines.append("- None flagged.")

    lines.append("\n## Health metrics")
    dtcs = ", ".join(d["code"] for d in metrics.get("dtcs", [])) or "none"
    lines.append(
        f"coolant max {metrics.get('coolant_max_c')}°C · "
        f"LTFT B1 {metrics.get('ltft_b1_mean_pct')}% / B2 {metrics.get('ltft_b2_mean_pct')}% "
        f"(beyond ±8% for {metrics.get('ltft_over_watch_pct')}% of trip) · "
        f"battery {metrics.get('voltage_min_v')}–{metrics.get('voltage_max_v')} V · "
        f"DTCs: {dtcs}"
    )

    lines.append("\n## Driving")
    lines.append(
        f"distance {dist} mi · idle {metrics.get('idle_pct')}% · "
        f"time >{config.THRESHOLDS['rpm_high']}rpm {metrics.get('high_rpm_pct')}% · "
        f"max speed {metrics.get('max_speed_mph')} mph · "
        f"harsh accel/brake {metrics.get('harsh_accel_events')}/"
        f"{metrics.get('harsh_brake_events')} · "
        f"est MPG {metrics.get('est_mpg')}"
    )
    return "\n".join(lines) + "\n"


def _index_line(trip_id, dir_rel, started_at, metrics, findings) -> dict:
    return {
        "trip_id": trip_id,
        "dir": dir_rel,
        "date": datetime.fromtimestamp(started_at).isoformat(timespec="seconds"),
        "started_at": started_at,
        "duration_s": metrics.get("duration_s"),
        "distance_mi": metrics.get("distance_mi"),
        "coolant_max_c": metrics.get("coolant_max_c"),
        "ltft_b1_mean_pct": metrics.get("ltft_b1_mean_pct"),
        "stft_b1_mean_pct": metrics.get("stft_b1_mean_pct"),
        "ltft_b2_mean_pct": metrics.get("ltft_b2_mean_pct"),
        "ltft_over_watch_pct": metrics.get("ltft_over_watch_pct"),
        "voltage_min_v": metrics.get("voltage_min_v"),
        "voltage_max_v": metrics.get("voltage_max_v"),
        "max_speed_mph": metrics.get("max_speed_mph"),
        "high_rpm_pct": metrics.get("high_rpm_pct"),
        "harsh_accel_events": metrics.get("harsh_accel_events"),
        "harsh_brake_events": metrics.get("harsh_brake_events"),
        "est_mpg": metrics.get("est_mpg"),
        "dtc_count": metrics.get("dtc_count"),
        "finding_counts": rules.severity_counts(findings),
        "findings": [
            {"code": f["code"], "severity": f["severity"],
             "category": f["category"], "title": f["title"]}
            for f in findings
        ],
    }


def write_trip(trip_id, started_at, ended_at, samples, metrics, findings,
               vehicle: str | None = None) -> str:
    """Persist a finished trip. Returns the trip directory path."""
    d = _trip_dir(trip_id, started_at)
    log.info("writing trip %s to %s (%d samples, %d findings)",
             trip_id, d, len(samples), len(findings))

    if samples:
        try:
            _write_parquet(d / "samples.parquet", samples)
            log.debug("trip %s: wrote samples.parquet", trip_id)
        except Exception:
            # Re-raise (behavior unchanged) but make the failure findable.
            log.error("trip %s: failed to write samples.parquet", trip_id, exc_info=True)
            raise

    summary = schema.build_summary(
        trip_id, started_at, ended_at, metrics, findings, vehicle=vehicle
    )
    # Validate best-effort: a schema mismatch must never lose the trip data or
    # crash the device while driving, so we log and still write the artifact.
    try:
        schema.validate_summary(summary)
    except schema.SummaryValidationError as exc:
        log.warning("Trip %s summary failed schema validation: %s", trip_id, exc)
    (d / "summary.json").write_text(json.dumps(summary, indent=2))
    (d / "summary.md").write_text(_summary_md(trip_id, started_at, metrics, findings, vehicle))
    log.debug("trip %s: wrote summary.json and summary.md", trip_id)

    dir_rel = os.path.relpath(d, config.DATA_DIR)
    index_path = Path(config.DATA_DIR) / "index.jsonl"
    with index_path.open("a") as fh:
        fh.write(json.dumps(_index_line(trip_id, dir_rel, started_at, metrics, findings)) + "\n")
    log.debug("trip %s: appended index.jsonl line", trip_id)

    return str(d)


def delete_trip(dir_rel: str) -> bool:
    """Delete a saved trip: remove its folder and its line from index.jsonl.

    Guarded against path traversal — only direct subdirectories of data/trips
    can be deleted.
    """
    base = (Path(config.DATA_DIR) / "trips").resolve()
    target = (Path(config.DATA_DIR) / dir_rel).resolve()
    if target.parent != base:
        raise ValueError("Invalid trip path")

    if target.exists() and target.is_dir():
        shutil.rmtree(target)

    index_path = Path(config.DATA_DIR) / "index.jsonl"
    if index_path.exists():
        kept = []
        for line in index_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                if json.loads(line).get("dir") == dir_rel:
                    continue
            except json.JSONDecodeError:
                pass
            kept.append(line)
        index_path.write_text("\n".join(kept) + ("\n" if kept else ""))
    return True
