"""Synthetic OBD source for desk testing (SIMULATE=1). No car or dongle needed.

Mirrors the OBDSource duck-type (connect/poll/read_dtcs/read_report/read_vin/
close). Drives a repeating accelerate→cruise→hard-brake cycle, injects a slowly
worsening fault, and after ~90 s reports the check-engine light on with a stored
DTC (P0171) so the live pipeline, rules, and the diagnostics page all light up.
"""
from __future__ import annotations

import math
import random
import time

from . import config


class SimSource:
    name = "sim"

    def __init__(self) -> None:
        self._t0 = time.time()

    async def connect(self) -> None:
        self._t0 = time.time()

    @property
    def supported_pids(self) -> list[str]:
        # Pretend the simulated vehicle supports the whole candidate set.
        return list(config.CANDIDATE_PIDS)

    def _speed_kph(self, e: float) -> float:
        cycle = e % 60.0
        if cycle < 10:
            return (cycle / 10.0) * 55.0
        if cycle < 40:
            return 55.0 + 4.0 * math.sin(cycle) + random.uniform(-1, 1)
        if cycle < 41:
            return 12.0
        return max(0.0, 55.0 - (cycle - 41) * 2.0)

    def _core(self, e: float) -> dict:
        speed = round(max(0.0, self._speed_kph(e)), 1)
        rpm = round(820 + speed * 38 + random.uniform(-40, 40))
        load = round(min(95.0, 15 + speed * 0.9 + random.uniform(-3, 3)), 1)
        return {
            "RPM": float(rpm), "SPEED": speed, "ENGINE_LOAD": load,
            "COOLANT_TEMP": round(min(92.0, 40 + e * 1.5) + (16 if 60 < e < 75 else 0), 1),
            "THROTTLE_POS": round(min(90.0, 10 + load * 0.8), 1),
            "LONG_FUEL_TRIM_1": round(3.0 + e * 0.05 + random.uniform(-0.5, 0.5), 1),
            "SHORT_FUEL_TRIM_1": round(random.uniform(-3, 3), 1),
            "LONG_FUEL_TRIM_2": round(2.0 + e * 0.04 + random.uniform(-0.5, 0.5), 1),
            "SHORT_FUEL_TRIM_2": round(random.uniform(-3, 3), 1),
            "INTAKE_PRESSURE": round(28 + load * 0.6 + random.uniform(-2, 2), 1),
            "MAF": round(max(1.5, load * 0.6 + speed * 0.2), 2),
            "TIMING_ADVANCE": round(8 + load * 0.2 + random.uniform(-2, 2), 1),
            "CONTROL_MODULE_VOLTAGE": round(14.1 + random.uniform(-0.2, 0.2), 2),
            "INTAKE_TEMP": round(28 + random.uniform(-1, 1), 1),
        }

    def _extended(self, e: float) -> dict:
        return {
            "RUN_TIME": round(e), "RUN_TIME_MIL": round(max(0, e - 90)) if e > 90 else 0,
            "DISTANCE_W_MIL": round(max(0, (e - 90) / 60)) if e > 90 else 0,
            "DISTANCE_SINCE_DTC_CLEAR": round(e / 30), "WARMUPS_SINCE_DTC_CLEAR": 3,
            "TIME_SINCE_DTC_CLEARED": round(e), "FUEL_LEVEL": round(68 - e * 0.01, 1),
            "BAROMETRIC_PRESSURE": 99.0, "AMBIANT_AIR_TEMP": 24.0,
            "ABSOLUTE_LOAD": round(20 + random.uniform(-2, 2), 1),
            "RELATIVE_THROTTLE_POS": round(8 + random.uniform(-1, 1), 1),
            "COMMANDED_EQUIV_RATIO": round(0.99 + random.uniform(-0.02, 0.02), 3),
            "COMMANDED_EGR": round(random.uniform(0, 6), 1), "EGR_ERROR": round(random.uniform(-3, 3), 1),
            "EVAPORATIVE_PURGE": round(random.uniform(0, 20), 1),
            "CATALYST_TEMP_B1S1": round(430 + random.uniform(-30, 30), 1),
            "CATALYST_TEMP_B2S1": round(425 + random.uniform(-30, 30), 1),
            "O2_B1S1": round(0.45 + 0.4 * math.sin(e * 3), 3),
            "O2_B1S2": round(0.6 + random.uniform(-0.05, 0.05), 3),
            "O2_B2S1": round(0.45 + 0.4 * math.sin(e * 3 + 1), 3),
            "O2_B2S2": round(0.6 + random.uniform(-0.05, 0.05), 3),
            "FUEL_PRESSURE": round(380 + random.uniform(-10, 10)),
            "OIL_TEMP": round(min(98, 40 + e), 1), "FUEL_RATE": round(random.uniform(1, 8), 1),
            "ETHANOL_PERCENT": 10.0, "SHORT_O2_TRIM_B1": round(random.uniform(-2, 2), 1),
            "LONG_O2_TRIM_B1": round(random.uniform(-2, 2), 1),
        }

    async def poll(self, full: bool = False) -> dict:
        e = time.time() - self._t0
        sample = {"ts": time.time(), **self._core(e)}
        if full:
            sample.update(self._extended(e))
        return sample

    def _stored(self):
        return ([{"code": "P0171", "description": "System Too Lean (Bank 1)"}]
                if (time.time() - self._t0) > 90 else [])

    async def read_dtcs(self) -> list[dict]:
        return self._stored()

    async def read_report(self) -> dict:
        e = time.time() - self._t0
        mon = ["MISFIRE_MONITORING", "FUEL_SYSTEM_MONITORING", "COMPONENT_MONITORING",
               "CATALYST_MONITORING", "OXYGEN_SENSOR_MONITORING",
               "EVAPORATIVE_SYSTEM_MONITORING", "EGR_VVT_SYSTEM_MONITORING"]
        return {
            "ts": time.time(), "mil": e > 90, "dtc_count": 1 if e > 90 else 0,
            "ignition_type": "spark",
            "monitors": [{"name": n, "complete": i < 5} for i, n in enumerate(mon)],
            "stored": self._stored(),
            "pending": ([{"code": "P0300", "description": "Random/Multiple Cylinder Misfire Detected"}]
                        if 70 < e < 95 else []),
            "supported_count": len(config.CANDIDATE_PIDS),
        }

    async def read_vin(self) -> str | None:
        return "SIMULATED00000000"

    async def close(self) -> None:
        return None
