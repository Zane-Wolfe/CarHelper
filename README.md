# CarHelper

Offline, **read-only** OBD-II telemetry capture + real-time rule evaluation for your car.
It collects data and flags issues while you drive (no internet, no AI at runtime), then
writes small per-trip artifacts you can read later with **Claude Code** at home.

> **Safety:** CarHelper only ever *reads* from the car. It never sends clear-codes, resets,
> actuator tests, or any command that changes vehicle state. All OBD access is funneled
> through a read-only allowlist (`app/obd_session.py::safe_query`).

## Quick start

```bash
cp .env.example .env        # then edit OBD_BT_MAC if you know it
docker compose up           # one command -> http://localhost:8000
```

Open the web UI, press the **physical Connect button** on the vLinker MC+, then click
**Connect** in the UI. Start a trip, drive, then Stop — artifacts land in `./data`.

### Try it without a car

```bash
SIMULATE=1 docker compose up
```

A synthetic OBD source drives the full pipeline (live UI, rules, artifact writing) so you
can verify everything at your desk. The simulator injects a slowly worsening fault so you
can see findings appear.

## Analyzing trips later (Claude Code)

At home, open Claude Code in this directory and ask in plain language, e.g.:

> Any signs something's wrong with the car over the last week?

Claude Code reads `CLAUDE.md`, `data/index.jsonl`, and the per-trip `summary.md` files, and
can run the aggregator:

```bash
python tools/query.py --since 7d
```

## Layout

```
app/        web server, OBD capture, rules, artifact writer, simulator
tools/      query.py  (stdlib-only trip aggregator for Claude Code)
data/       trips/<id>/{samples.parquet, summary.json, summary.md} + index.jsonl
CLAUDE.md   grounding for Claude Code (vehicle, thresholds, how to read the data)
```
