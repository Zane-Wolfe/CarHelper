"""Pure trip aggregation: turn a list of raw samples into rolled-up metrics.

No thresholds or judgement here — that lives in rules.py. Computed over the
whole sample buffer so it is identical for live (buffer-so-far) and final use.
"""
from __future__ import annotations

import logging
from statistics import mean
from typing import Any

from . import config

log = logging.getLogger(__name__)


def _ok(v) -> bool:
    # Reject None and NaN (parquet reads missing values back as NaN, not None).
    return v is not None and v == v


def _vals(samples: list[dict], key: str) -> list[float]:
    return [s[key] for s in samples if _ok(s.get(key))]


def _pairs(samples: list[dict]):
    """Yield (prev, cur, dt) for consecutive samples with a sane dt.

    dt must be positive and within MAX_SAMPLE_GAP_S — larger gaps are a
    pause/dropout, not continuous driving, and are skipped.
    """
    for a, b in zip(samples, samples[1:], strict=False):
        dt = b["ts"] - a["ts"]
        if 0 < dt < config.MAX_SAMPLE_GAP_S:
            yield a, b, dt


def compute(samples: list[dict], dtcs: list[dict] | None = None) -> dict[str, Any]:
    dtcs = dtcs or []
    if not samples:
        log.debug("compute: no samples")
        return {"sample_count": 0, "dtc_count": len(dtcs), "dtcs": dtcs}

    duration_s = max(0.0, samples[-1]["ts"] - samples[0]["ts"])

    distance_km = 0.0
    idle_s = 0.0
    moving_s = 0.0
    high_rpm_s = 0.0
    accounted_s = 0.0  # total time covered by sane (non-gap) intervals
    harsh_accel = 0
    harsh_brake = 0
    fuel_g = 0.0

    for a, b, dt in _pairs(samples):
        accounted_s += dt
        sa, sb = a.get("SPEED"), b.get("SPEED")
        if sa is not None and sb is not None:
            avg_kph = (sa + sb) / 2.0
            distance_km += avg_kph * dt / 3600.0
            if avg_kph < 3:
                idle_s += dt
            else:
                moving_s += dt
            # Harsh events from speed delta, expressed in g.
            g = ((sb - sa) / dt) * config.G_PER_KPH_PER_S
            if g >= config.THRESHOLDS["harsh_accel_g"]:
                harsh_accel += 1
            elif g <= -config.THRESHOLDS["harsh_brake_g"]:
                harsh_brake += 1
        rpm_b = b.get("RPM")
        if rpm_b is not None and rpm_b > config.THRESHOLDS["rpm_high"]:
            high_rpm_s += dt
        maf = b.get("MAF")
        if maf is not None:
            # MAF g/s -> fuel g/s via stoichiometric AFR.
            fuel_g += (maf / config.STOICH_AFR) * dt

    coolant = _vals(samples, "COOLANT_TEMP")
    ltft = _vals(samples, "LONG_FUEL_TRIM_1")
    stft = _vals(samples, "SHORT_FUEL_TRIM_1")
    ltft2 = _vals(samples, "LONG_FUEL_TRIM_2")
    stft2 = _vals(samples, "SHORT_FUEL_TRIM_2")
    volts = _vals(samples, "CONTROL_MODULE_VOLTAGE")
    speeds = _vals(samples, "SPEED")

    # Fraction of the trip the worst long-term trim (either bank) spent beyond
    # the watch/action threshold — catches a sustained condition the mean hides.
    ft = config.THRESHOLDS["fuel_trim_pct"]
    ltft_counted = over_watch = over_action = 0
    for s in samples:
        banks = [abs(s[k]) for k in ("LONG_FUEL_TRIM_1", "LONG_FUEL_TRIM_2")
                 if _ok(s.get(k))]
        if not banks:
            continue
        ltft_counted += 1
        worst_lt = max(banks)
        if worst_lt >= ft["watch"]:
            over_watch += 1
        if worst_lt >= ft["action"]:
            over_action += 1

    distance_mi = distance_km * config.KM_TO_MI
    fuel_gal = (fuel_g / config.GASOLINE_G_PER_L) * 0.264172 if fuel_g else 0.0
    est_mpg = round(distance_mi / fuel_gal, 1) if fuel_gal > 0.05 else None

    def r(x, n=1):
        return round(x, n) if x is not None else None

    log.debug("compute: %d samples over %.0fs, %.2f mi, %d DTC(s)",
              len(samples), duration_s, distance_mi, len(dtcs))
    return {
        "sample_count": len(samples),
        "duration_s": round(duration_s),
        "distance_mi": r(distance_mi, 2),
        # Percentages are over accounted (non-gap) time, not wall-clock span, so
        # dropped sample gaps don't deflate them. Falls back to duration_s only
        # if no sane intervals were found at all.
        "idle_pct": r(100 * idle_s / accounted_s) if accounted_s else None,
        "high_rpm_pct": r(100 * high_rpm_s / accounted_s) if accounted_s else None,
        "max_speed_mph": r(max(speeds) * config.KPH_TO_MPH) if speeds else None,
        "coolant_max_c": r(max(coolant)) if coolant else None,
        "ltft_b1_mean_pct": r(mean(ltft)) if ltft else None,
        "stft_b1_mean_pct": r(mean(stft)) if stft else None,
        "ltft_b2_mean_pct": r(mean(ltft2)) if ltft2 else None,
        "stft_b2_mean_pct": r(mean(stft2)) if stft2 else None,
        "ltft_over_watch_pct": r(100 * over_watch / ltft_counted) if ltft_counted else None,
        "ltft_over_action_pct": r(100 * over_action / ltft_counted) if ltft_counted else None,
        "voltage_min_v": r(min(volts), 2) if volts else None,
        "voltage_max_v": r(max(volts), 2) if volts else None,
        "harsh_accel_events": harsh_accel,
        "harsh_brake_events": harsh_brake,
        "est_mpg": est_mpg,
        "dtc_count": len(dtcs),
        "dtcs": dtcs,
    }
