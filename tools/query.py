#!/usr/bin/env python3
"""Aggregate saved trips over a time window. Stdlib-only — runs on the host with
no dependencies, so Claude Code can invoke it directly.

Reads data/index.jsonl (the per-trip rolled-up index) and prints the trips in
range, their findings, and simple trends. For raw time-series drill-down, open
the per-trip samples.parquet (needs pandas/pyarrow).

Examples:
  python tools/query.py --since 7d
  python tools/query.py --since 30d --metric ltft_b1_mean_pct
  python tools/query.py --from 2026-06-01 --to 2026-06-08
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

_UNITS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_since(s: str) -> float:
    m = re.fullmatch(r"(\d+)\s*([mhdw])", s.strip().lower())
    if not m:
        raise SystemExit(f"Bad --since '{s}'. Use e.g. 90m, 24h, 7d, 4w.")
    return int(m.group(1)) * _UNITS[m.group(2)]


def load_index(data_dir: Path) -> list[dict]:
    path = data_dir / "index.jsonl"
    if not path.exists():
        raise SystemExit(f"No index at {path}. Has any trip been recorded yet?")
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def in_window(trip: dict, lo: float | None, hi: float | None) -> bool:
    ts = trip.get("started_at", 0)
    return (lo is None or ts >= lo) and (hi is None or ts <= hi)


def main() -> None:
    ap = argparse.ArgumentParser(description="Query saved CarHelper trips.")
    ap.add_argument("--data", default="data", help="data directory (default: data)")
    ap.add_argument("--since", help="relative window, e.g. 24h, 7d, 4w")
    ap.add_argument("--from", dest="from_", help="start date YYYY-MM-DD")
    ap.add_argument("--to", dest="to", help="end date YYYY-MM-DD")
    ap.add_argument("--metric", help="trend a numeric metric across trips")
    args = ap.parse_args()

    lo = hi = None
    if args.since:
        lo = datetime.now().timestamp() - parse_since(args.since)
    if args.from_:
        lo = datetime.fromisoformat(args.from_).timestamp()
    if args.to:
        hi = (datetime.fromisoformat(args.to) + timedelta(days=1)).timestamp()

    trips = [t for t in load_index(Path(args.data)) if in_window(t, lo, hi)]
    trips.sort(key=lambda t: t.get("started_at", 0))

    if not trips:
        print("No trips in the selected window.")
        return

    span = "all time"
    if args.since:
        span = f"last {args.since}"
    elif args.from_ or args.to:
        span = f"{args.from_ or '…'} → {args.to or 'now'}"
    print(f"== {len(trips)} trip(s), {span} ==\n")

    totals = {"info": 0, "watch": 0, "action": 0}
    code_counts: dict[str, int] = {}
    for t in trips:
        c = t.get("finding_counts", {})
        for k in totals:
            totals[k] += c.get(k, 0)
        date = (t.get("date") or "").replace("T", " ")
        trip_id = t.get("trip_id") or "—"
        tags = " ".join(f"{c[k]}{k[0].upper()}" for k in ("action", "watch", "info") if c.get(k)) or "clean"
        print(f"{date}  {trip_id:>7}  {str(t.get('distance_mi','—')):>6} mi   {tags}")
        for f in t.get("findings", []):
            if f["severity"] in ("action", "watch"):
                code_counts[f["code"]] = code_counts.get(f["code"], 0) + 1
                print(f"      [{f['severity']}] {f['title']}")

    print(f"\nFindings in window — action:{totals['action']} watch:{totals['watch']} info:{totals['info']}")
    if code_counts:
        print("Recurring (action/watch) by code:")
        for code, n in sorted(code_counts.items(), key=lambda x: -x[1]):
            print(f"  {code}: {n} trip(s)")

    if args.metric:
        series = [(t.get("date", ""), t.get(args.metric)) for t in trips
                  if t.get(args.metric) is not None]
        print(f"\nMetric '{args.metric}' across {len(series)} trip(s):")
        if series:
            vals = [v for _, v in series]
            print(f"  first {series[0][1]}  →  last {series[-1][1]}   "
                  f"(min {min(vals)}, max {max(vals)}, mean {round(sum(vals)/len(vals), 2)})")
            for date, v in series:
                print(f"  {date.replace('T',' ')}  {v}")
        else:
            print("  (no values for that metric)")


if __name__ == "__main__":
    main()
