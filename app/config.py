"""Configuration, watched PIDs, the read-only command allowlist, and rule thresholds.

Units note: values are stored in the units python-OBD returns them in —
SPEED in kph, COOLANT_TEMP/INTAKE_TEMP in °C, fuel trims in %, voltage in V,
RPM in rpm. Human-facing summaries convert speed/distance to mph/miles.
"""
from __future__ import annotations

import os


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


# --- Runtime ---
SIMULATE: bool = _env("SIMULATE", "0") == "1"
SAMPLE_HZ: float = float(_env("SAMPLE_HZ", "2") or "2")
HOST: str = _env("HOST", "0.0.0.0")
PORT: int = int(_env("PORT", "8000") or "8000")
DATA_DIR: str = _env("DATA_DIR", "./data")

# --- Bluetooth / adapter ---
BT_MAC: str | None = _env("OBD_BT_MAC")
BT_NAME: str = _env("OBD_BT_NAME", "vLinker MC-Android")
BT_PIN: str = _env("OBD_BT_PIN", "1234")
RFCOMM_DEV: str = _env("RFCOMM_DEV", "/dev/rfcomm0")

# --- Live PIDs (python-OBD command names, all Mode 01 / read-only) ---
# We don't hardcode "every car's sensors" — instead we offer a broad candidate
# list and, at connect time, keep only the ones THIS vehicle reports as
# supported (see obd_session.OBDSource.connect). CORE is polled every cycle for
# responsive gauges + rules; EXTENDED is polled periodically (slower-changing).
CORE_PIDS: list[str] = [
    "RPM", "SPEED", "ENGINE_LOAD", "COOLANT_TEMP", "THROTTLE_POS",
    "SHORT_FUEL_TRIM_1", "LONG_FUEL_TRIM_1",
    "SHORT_FUEL_TRIM_2", "LONG_FUEL_TRIM_2",
    "INTAKE_PRESSURE", "MAF", "TIMING_ADVANCE",
    "CONTROL_MODULE_VOLTAGE", "INTAKE_TEMP",
]
EXTENDED_PIDS: list[str] = [
    "RUN_TIME", "RUN_TIME_MIL", "DISTANCE_W_MIL", "DISTANCE_SINCE_DTC_CLEAR",
    "WARMUPS_SINCE_DTC_CLEAR", "TIME_SINCE_DTC_CLEARED", "FUEL_LEVEL",
    "BAROMETRIC_PRESSURE", "AMBIANT_AIR_TEMP", "ABSOLUTE_LOAD",
    "RELATIVE_THROTTLE_POS", "COMMANDED_EQUIV_RATIO", "COMMANDED_EGR",
    "EGR_ERROR", "EVAPORATIVE_PURGE", "CATALYST_TEMP_B1S1", "CATALYST_TEMP_B2S1",
    "O2_B1S1", "O2_B1S2", "O2_B2S1", "O2_B2S2", "FUEL_PRESSURE", "OIL_TEMP",
    "FUEL_RATE", "ETHANOL_PERCENT", "SHORT_O2_TRIM_B1", "LONG_O2_TRIM_B1",
]
CANDIDATE_PIDS: list[str] = CORE_PIDS + EXTENDED_PIDS
# Poll the EXTENDED set once every N core cycles.
EXTENDED_EVERY: int = 10

# --- READ-ONLY ALLOWLIST (NON-NEGOTIABLE) ---
# Every OBD command sent to the car must be in this set. Query/read services
# only: Mode 01 (live data), Mode 03 (stored DTCs), Mode 07 (pending DTCs),
# Mode 09 (vehicle info), plus the PID-support/compliance probes. It deliberately
# EXCLUDES CLEAR_DTC (Mode 04) and every control/write service.
# app/obd_session.py::safe_query enforces membership.
READ_ONLY_COMMANDS: frozenset[str] = frozenset(
    set(CANDIDATE_PIDS)
    | {"GET_DTC", "GET_CURRENT_DTC", "STATUS", "FUEL_STATUS", "VIN",
       "PIDS_A", "PIDS_B", "PIDS_C", "OBD_COMPLIANCE",
       "ELM_VOLTAGE"}  # AT RV — adapter reads OBD-II pin 16 voltage; safe read-only fallback
)

# --- Rule thresholds (consumed by app/rules.py) ---
# Severity ladder: info < watch < action.
THRESHOLDS: dict = {
    # Coolant temperature (°C): normal up to ~104; overheating beyond.
    "coolant_c": {"watch": 105, "action": 113},
    # Absolute fuel trim (%): persistent large trims hint at air/fuel faults.
    "fuel_trim_pct": {"watch": 8, "action": 12},
    # Flag a sustained trim condition when long-term trim is beyond the
    # watch/action level for at least this % of the trip — catches cases the
    # trip-average smooths under threshold.
    "fuel_trim_sustained_pct": 50,
    # Module/battery voltage (V) while running (expect ~13.5–14.8).
    "voltage_low": {"watch": 12.4, "action": 11.8},
    "voltage_high": {"watch": 15.0, "action": 15.6},
    # Engine speed (rpm): sustained dwell above this is "high RPM" time.
    "rpm_high": 4000,
    # Harsh events from speed deltas, expressed in g (1 g = 35.3 kph/s).
    "harsh_accel_g": 0.35,
    "harsh_brake_g": 0.40,
}

# Max gap (s) between two samples that still counts as continuous driving for
# per-interval integration (distance, idle/motion time, high-RPM dwell). Larger
# gaps are treated as a pause/dropout and skipped. Sized for slow J1850 buses
# that only return ~1 sample / few seconds — too tight a cap silently drops real
# driving time and biases distance/idle%/high-RPM% low.
MAX_SAMPLE_GAP_S: float = 8.0

# Conversions
KPH_TO_MPH = 0.621371
KM_TO_MI = 0.621371
G_PER_KPH_PER_S = 1.0 / 35.3  # 1 g ≈ 35.3 (kph/s)
GASOLINE_G_PER_L = 737.0
STOICH_AFR = 14.7
