---
name: device-app
description: >-
  How the in-car device's FastAPI app + local web UI are wired (the PUBLIC
  carhelper repo): live capture, the read-only trip viewer, and the trips_repo
  read layer. READ THIS before adding an HTTP endpoint, touching app/main.py,
  the static UI (app/static/index.html), or reading/serving saved trips.
  Triggers: "device app", "FastAPI", "trip viewer", "web UI", "add an endpoint",
  "trips_repo", "index.jsonl", "summary.json", "charts", "app/main.py".
---

# CarHelper device app (public repo)

The in-car half: a single FastAPI app (`app/main.py`) that captures OBD telemetry
live **and** serves a local, offline web UI (`app/static/index.html`, one
self-contained file — vanilla JS, no build step). It runs on the box; there is no
cloud dependency here and there must never be one (local-first).

## ⚠️ Two non-negotiables before you touch this

1. **Read-only to the car.** Every OBD command goes through
   `app/obd_session.py::safe_query` against `app/config.py::READ_ONLY_COMMANDS`.
   Never add an endpoint or code path that writes to the vehicle. New HTTP routes
   that read *saved files* (like the trip viewer) are fine; routes that talk to
   the car must only issue allowlisted Mode 01/03/07/09 reads.
2. **No cloud imports in capture/rules/storage.** The device works fully offline.
   The `sync/` client is opt-in and separate; don't reach into it from the app.

## The app (`app/main.py`)

- A single `CaptureService` owns the OBD source, the poll loop, the in-progress
  trip buffer, and live WebSocket clients. Live state streams over `/ws/live`.
- Capture endpoints (`/api/connect`, `/api/scan`, `/api/trip/start|stop|delete`,
  `/api/codes`, …) mutate/read the connected adapter. `/api/codes` is read-only
  diagnostics (MIL, monitors, stored/pending DTCs).
- **Static UI is mounted LAST** at `/` (`StaticFiles(..., html=True)`), so every
  real route must be declared **above** the mount or it gets shadowed.
- On trip stop: `features.compute` → `rules.evaluate` → `writer.write_trip`, which
  writes `summary.json` (the versioned contract — see the `trip-summary-contract`
  skill), `summary.md`, `samples.parquet`, and appends a rolled-up `index.jsonl`
  line.

## The read layer (`app/trips_repo.py`) — use this for anything that reads trips

Route handlers must **not** read `data/` directly; go through `trips_repo` so the
path-traversal guard and graceful degradation live in one tested place.

- `list_trips(data_dir, limit)` → newest-first index rows (backs `GET /api/trips`).
- `load_trip(data_dir, ref)` → one trip's full detail (`GET /api/trips/{ref}`).
  `ref` is the trip **directory basename** (e.g. `2026-06-29T2347_t76848`), never
  a path. Raises `ValueError` (unsafe ref → 400) / `FileNotFoundError` (→ 404).
- `load_series(data_dir, ref, fields, max_points)` → downsampled per-sample series
  for charts (`GET /api/trips/{ref}/series`). **Lazy-imports pandas** and returns
  `{"available": False, "reason": ...}` (never raises) when pandas/pyarrow or the
  parquet file is absent — so the viewer still works on a minimal install, just
  without charts. SPEED stays in **kph** in the payload; the client converts to mph.
- `_safe_trip_dir` is the single traversal guard: rejects `/`, `\`, `..`, and any
  ref that resolves outside `data/trips/`. Reuse it for any new file-reading route.

## The web UI (`app/static/index.html`)

Three views toggled by `showView('dashboard'|'codes'|'trip')`:
- **dashboard** — live gauges/vitals/findings (WebSocket) + a "Recent trips" list.
- **codes** — read-only diagnostics page.
- **trip** — per-trip detail (findings, health/driving metrics, canvas charts),
  opened via `openTrip(ref)` when a trip row is clicked. Charts are drawn with
  `drawLineChart` on `<canvas>` (no chart library — keep it dependency-free).

Conventions: escape interpolated strings with `esc()`; read theme colors with
`cssVar('--token')`; the file is intentionally one HTML with inline CSS/JS.

## Adding an endpoint (checklist)

1. Declare the route in `app/main.py` **above** the static mount.
2. If it reads saved trips, add a function to `trips_repo.py` and route through it
   (guard untrusted refs with `_safe_trip_dir`). Keep it stdlib-only unless it
   genuinely needs pandas — and if so, lazy-import + degrade gracefully.
3. If it touches the car, it MUST use `safe_query` (allowlisted reads only).
4. Add tests in `tests/` (see `tests/test_trips_repo.py`). Parquet/pandas tests
   must `pytest.importorskip("pandas")` — **device CI installs only
   `jsonschema pytest ruff`**, no pandas, so unguarded pandas tests break CI.
5. Gate: `ruff check app sync tests` + `python -m pytest` (run in the project
   `.venv`, per the workspace's env preference — never system pip/conda).
