"""The trip-summary contract, on the device side.

`schema/summary.schema.json` is the single source of truth for the device↔cloud
interface (and the promise to self-hosters). This module is the *only* place the
device constructs a summary object, so the shape and its ``schema_version`` stay
in one spot. Treat any change to the schema as a breaking API change and bump
``SCHEMA_VERSION`` in lockstep with the JSON Schema's ``schema_version`` const.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

# Bump together with the `schema_version` const in schema/summary.schema.json.
SCHEMA_VERSION = 1

_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "summary.schema.json"


@lru_cache(maxsize=1)
def load_schema() -> dict[str, Any]:
    """Return the parsed JSON Schema (cached)."""
    return json.loads(_SCHEMA_PATH.read_text())


def build_summary(
    trip_id: str,
    started_at: float,
    ended_at: float | None,
    metrics: dict[str, Any],
    findings: list[dict[str, Any]],
    vehicle: str | None = None,
) -> dict[str, Any]:
    """Assemble a schema-conformant trip summary.

    This is the canonical constructor — ``writer.write_trip`` and any future
    caller must go through here so ``schema_version`` is always stamped.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "trip_id": trip_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "vehicle": vehicle,
        "metrics": metrics,
        "findings": findings,
    }


class SummaryValidationError(ValueError):
    """Raised when a summary does not conform to the schema."""


def validate_summary(summary: dict[str, Any]) -> None:
    """Validate ``summary`` against the JSON Schema.

    Raises :class:`SummaryValidationError` on failure. Import of ``jsonschema``
    is deferred so the capture/rules/storage path has no hard dependency on it
    (the device must run fully offline with a minimal footprint).
    """
    try:
        import jsonschema
    except ImportError as exc:  # pragma: no cover - depends on install profile
        raise SummaryValidationError(
            "jsonschema is not installed; cannot validate summary"
        ) from exc

    try:
        jsonschema.validate(instance=summary, schema=load_schema())
    except jsonschema.ValidationError as exc:
        raise SummaryValidationError(str(exc)) from exc
