"""Threshold rules over trip metrics → findings.

A finding is a small dict:
  {code, category, severity, title, detail, evidence}
  category ∈ {health, performance, habits}
  severity ∈ {info, watch, action}   (action = look at this soon)

Rules are deliberately conservative: they only flag threshold crossings. The
nuanced "is this actually a problem / is it trending" judgement is left to
Claude Code reading these findings across many trips at home.
"""
from __future__ import annotations

import logging
from typing import Any

from . import config

log = logging.getLogger(__name__)

SEVERITY_ORDER = {"info": 0, "watch": 1, "action": 2}


def _finding(code, category, severity, title, detail, **evidence) -> dict:
    return {
        "code": code,
        "category": category,
        "severity": severity,
        "title": title,
        "detail": detail,
        "evidence": evidence,
    }


def evaluate(m: dict[str, Any]) -> list[dict]:
    t = config.THRESHOLDS
    out: list[dict] = []

    # --- Stored DTCs (health) ---
    if m.get("dtc_count"):
        codes = ", ".join(d["code"] for d in m.get("dtcs", [])) or "unknown"
        out.append(_finding(
            "dtc_present", "health", "action",
            f"{m['dtc_count']} stored diagnostic trouble code(s)",
            f"ECU has stored DTC(s): {codes}. These indicate a fault the car logged.",
            dtcs=m.get("dtcs", []),
        ))

    # --- Coolant temperature (health) ---
    c = m.get("coolant_max_c")
    if c is not None:
        if c >= t["coolant_c"]["action"]:
            out.append(_finding("coolant_overheat", "health", "action",
                "Coolant temperature very high",
                f"Peak coolant {c}°C ≥ {t['coolant_c']['action']}°C — possible overheating.",
                coolant_max_c=c))
        elif c >= t["coolant_c"]["watch"]:
            out.append(_finding("coolant_high", "health", "watch",
                "Coolant temperature elevated",
                f"Peak coolant {c}°C ≥ {t['coolant_c']['watch']}°C — warmer than normal.",
                coolant_max_c=c))

    # --- Fuel trims, both banks (health) ---
    # Fire on EITHER the trip-average magnitude OR a sustained fraction of the
    # trip beyond threshold (the latter catches conditions the mean smooths under).
    trims = {k: m.get(k) for k in (
        "ltft_b1_mean_pct", "stft_b1_mean_pct", "ltft_b2_mean_pct", "stft_b2_mean_pct")}
    worst = max((abs(v) for v in trims.values() if v is not None), default=None)
    ft = t["fuel_trim_pct"]
    frac = t["fuel_trim_sustained_pct"]
    over_watch = m.get("ltft_over_watch_pct")
    over_action = m.get("ltft_over_action_pct")

    mean_sev = None
    if worst is not None:
        mean_sev = "action" if worst >= ft["action"] else "watch" if worst >= ft["watch"] else None
    sustained_sev = None
    if over_action is not None and over_action >= frac:
        sustained_sev = "action"
    elif over_watch is not None and over_watch >= frac:
        sustained_sev = "watch"

    sev = max((s for s in (mean_sev, sustained_sev) if s),
              key=lambda s: SEVERITY_ORDER[s], default=None)
    if sev:
        banks = (f"B1 long {trims['ltft_b1_mean_pct']} / short {trims['stft_b1_mean_pct']}, "
                 f"B2 long {trims['ltft_b2_mean_pct']} / short {trims['stft_b2_mean_pct']}")
        detail = f"Mean fuel-trim magnitude {worst:.1f}% ({banks})."
        if sustained_sev:
            detail += (f" Long-term trim was beyond ±{ft['watch']}% for {over_watch}% of the "
                       f"trip (beyond ±{ft['action']}% for {over_action}%) — a sustained "
                       f"condition the average can hide.")
        detail += (" Large trims can indicate an air/vacuum leak, fuel-delivery, or sensor "
                   "issue. If only one bank is affected, the cause is usually on that bank.")
        out.append(_finding("fuel_trim", "health", sev, "Fuel trim outside normal range",
                            detail, over_watch_pct=over_watch,
                            over_action_pct=over_action, **trims))

    # --- Charging/battery voltage (health) ---
    vmin, vmax = m.get("voltage_min_v"), m.get("voltage_max_v")
    if vmin is not None:
        lo = t["voltage_low"]
        sev = "action" if vmin <= lo["action"] else "watch" if vmin <= lo["watch"] else None
        if sev:
            out.append(_finding("voltage_low", "health", sev,
                "Low system voltage",
                f"Minimum {vmin} V ≤ {lo['watch']} V — weak battery or charging issue.",
                voltage_min_v=vmin))
    if vmax is not None:
        hi = t["voltage_high"]
        sev = "action" if vmax >= hi["action"] else "watch" if vmax >= hi["watch"] else None
        if sev:
            out.append(_finding("voltage_high", "health", sev,
                "High system voltage",
                f"Maximum {vmax} V ≥ {hi['watch']} V — possible overcharging / regulator fault.",
                voltage_max_v=vmax))

    # --- Driving habits ---
    ha, hb = m.get("harsh_accel_events", 0), m.get("harsh_brake_events", 0)
    if ha or hb:
        sev = "watch" if (ha + hb) >= 6 else "info"
        out.append(_finding("harsh_events", "habits", sev,
            "Harsh acceleration / braking events",
            f"{ha} harsh acceleration and {hb} harsh braking event(s) this trip.",
            harsh_accel=ha, harsh_brake=hb))

    hr = m.get("high_rpm_pct")
    if hr is not None and hr >= 5:
        sev = "watch" if hr >= 15 else "info"
        out.append(_finding("high_rpm", "habits", sev,
            "Sustained high RPM",
            f"{hr}% of the trip above {config.THRESHOLDS['rpm_high']} rpm.",
            high_rpm_pct=hr))

    out.sort(key=lambda f: SEVERITY_ORDER[f["severity"]], reverse=True)
    log.debug("evaluate: %d finding(s) %s", len(out), severity_counts(out))
    return out


def severity_counts(findings: list[dict]) -> dict[str, int]:
    counts = {"info": 0, "watch": 0, "action": 0}
    for f in findings:
        counts[f["severity"]] += 1
    return counts
