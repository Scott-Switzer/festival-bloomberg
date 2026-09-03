"""Artist Intelligence factor tape contracts.

A factor row is a temporal observation, not a mutable current-value field.
Every new collection generation produces a new key. Missing values stay NULL;
callers must not convert an unavailable factor into zero.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import UTC, date, datetime
from typing import Any

DEFAULT_GENERATION = "artist_factor_tape_v1"
TAPE_VERSION = "artist_factor_tape_v1"

TAPE_FIELDS = (
    "artist_key",
    "factor_family",
    "factor_name",
    "platform",
    "value",
    "unit",
    "observation_time",
    "available_at",
    "knowledge_time",
    "retrieved_at",
    "source",
    "evidence_ref",
    "source_scope",
    "rights_status",
    "commercial_use_status",
    "quality_status",
    "generation",
)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time(), tzinfo=UTC).isoformat()
    return str(value)


def observation_key(
    *,
    artist_key: str,
    factor_family: str,
    factor_name: str,
    platform: str,
    observation_time: Any,
    source: str,
    generation: str,
    evidence_ref: str | None = None,
) -> str:
    """Return a stable key that includes the immutable collection generation."""
    material = "|".join(
        str(part or "")
        for part in (
            artist_key,
            factor_family,
            factor_name,
            platform,
            _iso(observation_time),
            source,
            evidence_ref,
            generation,
            TAPE_VERSION,
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:40]


COMPARABILITY_FIELDS = (
    "measurement_basis",
    "measurement_window",
    "population_scope",
    "geographic_scope",
    "methodology_version",
    "coverage_generation",
)


def comparability_of(previous: dict[str, Any], latest: dict[str, Any]) -> tuple[bool, str | None]:
    """Decide whether two observations are like-for-like enough for a delta.

    A percentage change is a financial-grade claim: it is allowed only when the
    factor identity (name/platform/unit) AND the measurement context (basis,
    window, population, geography, methodology, coverage generation) match.
    Otherwise the comparison is ``NOT_COMPARABLE`` and no delta is produced.
    """
    def field(row: dict[str, Any], name: str) -> Any:
        if row.get(name) is not None:
            return row.get(name)
        evidence = row.get("evidence_json")
        if isinstance(evidence, dict):
            return evidence.get(name)
        return None

    for name in COMPARABILITY_FIELDS:
        old_v = field(previous, name)
        new_v = field(latest, name)
        if old_v is None or new_v is None:
            return False, f"{name} missing (old={old_v!r}, new={new_v!r})"
        if str(old_v) != str(new_v):
            return False, f"{name} differs (old={old_v!r}, new={new_v!r})"
    return True, None


def build_factor_observation(
    *,
    artist_key: str,
    factor_family: str,
    factor_name: str,
    platform: str,
    value: float | int | None,
    unit: str,
    observation_time: Any,
    retrieved_at: Any,
    source: str,
    evidence_ref: str | None = None,
    available_at: Any = None,
    knowledge_time: Any = None,
    source_scope: str = "ARTIST_FACTOR_TAPE",
    rights_status: str = "TERMS_REVIEW_REQUIRED",
    commercial_use_status: str = "PROTOTYPE_ONLY",
    quality_status: str | None = None,
    generation: str = DEFAULT_GENERATION,
    measurement_basis: str | None = None,
    measurement_window: Any = None,
    population_scope: str | None = None,
    geographic_scope: str | None = None,
    methodology_version: str | None = None,
    coverage_generation: str | None = None,
) -> dict[str, Any]:
    """Build the canonical row used by migration 049.

    ``value=None`` is a valid UNKNOWN observation. It is retained with lineage
    when a source explicitly observed that the value was unavailable; callers
    should omit a row only when no observation occurred at all.
    """
    observation_iso = _iso(observation_time)
    retrieved_iso = _iso(retrieved_at) or datetime.now(UTC).isoformat()
    available_iso = _iso(available_at)
    knowledge_iso = _iso(knowledge_time) or available_iso or retrieved_iso
    if observation_iso is None:
        raise ValueError("observation_time is required")
    key = observation_key(
        artist_key=artist_key,
        factor_family=factor_family,
        factor_name=factor_name,
        platform=platform,
        observation_time=observation_iso,
        source=source,
        generation=generation,
        evidence_ref=evidence_ref,
    )
    try:
        as_of = observation_iso[:10]
    except Exception as exc:
        raise ValueError("observation_time must contain a date") from exc
    comparability_meta = {
        name: value
        for name, value in (
            ("measurement_basis", measurement_basis),
            ("measurement_window", measurement_window),
            ("population_scope", population_scope),
            ("geographic_scope", geographic_scope),
            ("methodology_version", methodology_version),
            ("coverage_generation", coverage_generation),
        )
        if value is not None
    }
    return {
        "factor_observation_key": key,
        "artist_key": artist_key,
        "factor_family": factor_family,
        "factor_name": factor_name,
        "value": float(value) if value is not None else None,
        "value_unit": unit,
        "as_of": as_of,
        "available_at": available_iso,
        "retrieved_at": retrieved_iso,
        "period_start": None,
        "period_end": None,
        "source_system": source,
        "source_version": TAPE_VERSION,
        "source_url": evidence_ref,
        "rights_status": rights_status,
        "commercial_use_status": commercial_use_status,
        "confidence": None,
        "evidence_json": comparability_meta or None,
        "platform": platform,
        "unit": unit,
        "observation_time": observation_iso,
        "knowledge_time": knowledge_iso,
        "source": source,
        "evidence_ref": evidence_ref,
        "source_scope": source_scope,
        "quality_status": quality_status or ("UNKNOWN" if value is None else "OBSERVED"),
        "generation": generation,
        "measurement_basis": measurement_basis,
        "measurement_window": _iso(measurement_window) if measurement_window else None,
        "population_scope": population_scope,
        "geographic_scope": geographic_scope,
        "methodology_version": methodology_version,
        "coverage_generation": coverage_generation,
    }


def insert_factor_observation(conn: Any, row: dict[str, Any]) -> int:
    """Insert one tape row without updating an earlier generation."""
    columns = (
        "factor_observation_key",
        "artist_key",
        "factor_family",
        "factor_name",
        "value",
        "value_unit",
        "as_of",
        "available_at",
        "retrieved_at",
        "period_start",
        "period_end",
        "source_system",
        "source_version",
        "source_url",
        "rights_status",
        "commercial_use_status",
        "confidence",
        "evidence_json",
        "platform",
        "unit",
        "observation_time",
        "knowledge_time",
        "source",
        "evidence_ref",
        "source_scope",
        "quality_status",
        "generation",
        "measurement_basis",
        "measurement_window",
        "population_scope",
        "geographic_scope",
        "methodology_version",
        "coverage_generation",
    )
    values = [row.get(column) for column in columns]
    placeholders = ", ".join("?" for _ in columns)
    before = conn.execute(
        "SELECT 1 FROM metrics.artist_factor_observations WHERE factor_observation_key = ?",
        [row["factor_observation_key"]],
    ).fetchone()
    if before:
        return 0
    conn.execute(
        f"INSERT INTO metrics.artist_factor_observations ({', '.join(columns)}) VALUES ({placeholders})",
        values,
    )
    return 1


def comparable_delta(
    observations: Iterable[dict[str, Any]],
) -> dict[str, Any] | None:
    """Compare the two latest observations only when fully like-for-like.

    The comparison requires identical unit AND identical measurement context
    (basis, window, population, geography, methodology, coverage generation).
    Otherwise ``None`` is returned: a mathematically computed percentage from
    unlike observations would be economically meaningless.
    """
    rows = sorted(
        (row for row in observations if row.get("value") is not None),
        key=lambda row: str(row.get("observation_time") or row.get("as_of") or ""),
    )
    if len(rows) < 2:
        return None
    previous, latest = rows[-2], rows[-1]
    if previous.get("unit") != latest.get("unit") and previous.get("value_unit") != latest.get(
        "value_unit"
    ):
        return None
    comparable, reason = comparability_of(previous, latest)
    if not comparable:
        return None
    old = float(previous["value"])
    new = float(latest["value"])
    result: dict[str, Any] = {
        "old_value": old,
        "new_value": new,
        "delta": new - old,
        "unit": latest.get("unit") or latest.get("value_unit"),
        "observation_time": latest.get("observation_time") or latest.get("as_of"),
        "source": latest.get("source") or latest.get("source_system"),
        "generation": latest.get("generation") or latest.get("source_version"),
        "comparability": "COMPARABLE",
    }
    if old != 0:
        result["delta_pct"] = (new - old) / abs(old) * 100.0
    return result


def what_changed(observations: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Produce deltas only for factor series with comparable observations."""
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in observations:
        key = (
            str(row.get("factor_name") or ""),
            str(row.get("platform") or row.get("source_system") or ""),
            str(row.get("unit") or row.get("value_unit") or ""),
        )
        groups.setdefault(key, []).append(row)
    changes: list[dict[str, Any]] = []
    for (factor_name, platform, _unit), rows in groups.items():
        delta = comparable_delta(rows)
        if delta is None:
            continue
        delta.update({"factor_name": factor_name, "platform": platform})
        changes.append(delta)
    return sorted(changes, key=lambda row: (row["factor_name"], row["platform"]))
