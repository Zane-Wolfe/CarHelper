# CarHelper — guidance for Claude Code

CarHelper logs **read-only** OBD-II telemetry from the car and evaluates health/driving
rules offline while driving. It does **no** AI at runtime. Your job, here at home, is to read
the artifacts it wrote and answer the owner's plain-language questions — most often:
**"Are there any signs something is wrong with the car?"** over some time window.

## ⚠️ Read-only to the car (applies if you ever modify this code)
This program must **never** send a command that changes vehicle state — no clearing codes, no
resets, no actuator tests, no ECU writes. All OBD access is funneled through
`app/obd_session.py::safe_query`, which enforces a read-only allowlist
(`app/config.py::READ_ONLY_COMMANDS`). Never add a feature that writes to the car, and reject
requests to do so. See memory: the read-only constraint is non-negotiable.

## Vehicle (fill this in)
Describe your vehicle here so analysis uses the right baselines. Example:
- Year / make / model:  2004 Ford Expedition
- Engine:               V8, two-bank (Bank 1 + Bank 2 confirmed via live data) — 4.6L or 5.4L
                        Triton; exact displacement TBD (VIN/Mode 09 not supported by this ECU)
- Notes / baselines:    Record your own warm-idle norms here once you have a clean trip — e.g.
                        idle rpm, coolant temp, and the fuel-trim range both banks settle into.
                        Older J1850 ECUs are slow (~1 sample / few seconds) and report fewer
                        PIDs. Note any pre-existing stored codes so they aren't re-flagged as new.

Accurate vehicle info improves judgement (normal ranges vary by engine). If blank, use the
generic thresholds below and say you're using generic baselines.

## Where the data is
- `data/index.jsonl` — **one line per trip**, rolled-up metrics + finding counts. **Start
  here** for any time-window question; only open individual trips that look notable.
- `data/trips/<ts>_<id>/summary.md` — human/LLM digest of one trip (read this for detail).
- `data/trips/<ts>_<id>/summary.json` — same data, structured.
- `data/trips/<ts>_<id>/samples.parquet` — full raw time series (drill-down only; needs
  pandas/pyarrow — read it only when a question truly needs the raw curve).

Units: speed/distance in **mph/miles** (already converted in summaries); temps °C; fuel
trims %; voltage V; RPM rpm. Raw `samples.parquet` stores SPEED in **kph**.

## How to answer "is anything wrong?"
1. Run `python tools/query.py --since 7d` (or `--since 24h|30d`, or `--from/--to`). It reads
   `index.jsonl` and prints the trips in range, finding counts, and `action`/`watch` items.
2. Weigh findings by **severity**: `action` = look at this soon; `watch` = keep an eye on it;
   `info` = context only. Separate **health** findings from **habits** (driving style).
3. Look for **trends across trips**, not just single-trip spikes — e.g. long-term fuel trim
   creeping up over many trips, or coolant peaks rising. A one-off can be noise; a sustained
   trend is a signal. Cite the specific trips/dates you're basing a conclusion on.
4. Be **conservative and honest**: the rules only flag threshold crossings. Don't invent a
   diagnosis the data doesn't support. If it looks healthy, say so. If something needs a
   mechanic, say that plainly.

## Thresholds the rules use (from `app/config.py`)
| Metric | watch | action | meaning |
|---|---|---|---|
| Coolant °C | ≥105 | ≥113 | overheating |
| \|fuel trim\| % | ≥8 | ≥12 | air/fuel/vacuum/sensor issue |
| Voltage low V | ≤12.4 | ≤11.8 | weak battery / charging |
| Voltage high V | ≥15.0 | ≥15.6 | overcharging / regulator |
| High RPM | — | >4000 rpm | % of trip spent above (habit) |
| Harsh accel/brake | ≥0.35g / ≥0.40g | — | driving-habit events |
| Stored DTC | — | any | ECU logged a fault code (always `action`) |

The **fuel-trim** rule fires on *either* the trip-average magnitude *or* a **sustained
fraction**: if long-term trim (either bank) stays beyond ±8% (watch) / ±12% (action) for
≥50% of the trip, it flags even when the average squeaks under threshold. `index.jsonl`
carries `ltft_over_watch_pct` for trending this across trips. Negative long-term trim = engine
running rich (ECU removing fuel); positive = lean (adding fuel).

## Useful commands
- `python tools/query.py --since 7d` — trips + findings in the last week
- `python tools/query.py --since 30d --metric ltft_b1_mean_pct` — trend a metric over time
- `python tools/query.py --from 2026-06-01 --to 2026-06-08` — explicit date range
