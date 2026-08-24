"""Workspace-only persistence for reproducible show economics scenarios."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .show_economics import (
    ShowEconomicsScenario,
    evaluate,
    evaluation_to_dict,
    scenario_from_dict,
    scenario_to_dict,
)


def _scenario_key(project_key: str | None, name: str) -> str:
    material = f"show-economics::{project_key or 'standalone'}::{name}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _decode(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def save_show_economics_scenario(
    connection,
    *,
    name: str,
    scenario: ShowEconomicsScenario,
    project_key: str | None = None,
    scenario_key: str | None = None,
) -> dict[str, Any]:
    """Evaluate and save one scenario in the mutable planning workspace."""
    if not name.strip():
        raise ValueError("scenario name must not be blank")
    result = evaluate(scenario)
    key = scenario_key or _scenario_key(project_key, name)
    inputs = scenario_to_dict(scenario)
    outputs = evaluation_to_dict(result)
    connection.execute(
        """
        INSERT INTO planning.show_economics_scenarios
            (scenario_key, project_key, name, currency, engine_version,
             inputs, derived_outputs, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, now(), now())
        ON CONFLICT (scenario_key) DO UPDATE SET
            project_key = excluded.project_key,
            name = excluded.name,
            currency = excluded.currency,
            engine_version = excluded.engine_version,
            inputs = excluded.inputs,
            derived_outputs = excluded.derived_outputs,
            updated_at = now()
        """,
        [
            key,
            project_key,
            name,
            result.currency,
            result.engine_version,
            json.dumps(inputs, separators=(",", ":")),
            json.dumps(outputs, separators=(",", ":")),
        ],
    )
    return load_show_economics_scenario(connection, key)


def load_show_economics_scenario(connection, scenario_key: str) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT scenario_key, project_key, name, currency, engine_version,
               inputs, derived_outputs, created_at, updated_at
        FROM planning.show_economics_scenarios
        WHERE scenario_key = ?
        """,
        [scenario_key],
    ).fetchone()
    if row is None:
        raise KeyError(f"unknown show economics scenario {scenario_key!r}")
    columns = (
        "scenario_key", "project_key", "name", "currency", "engine_version",
        "inputs", "derived_outputs", "created_at", "updated_at",
    )
    record = dict(zip(columns, row))
    record["inputs"] = _decode(record["inputs"])
    record["derived_outputs"] = _decode(record["derived_outputs"])
    record["scenario"] = scenario_from_dict(record["inputs"])
    return record


def list_show_economics_scenarios(
    connection, *, project_key: str | None = None,
) -> list[dict[str, Any]]:
    if project_key is None:
        rows = connection.execute(
            """
            SELECT scenario_key, project_key, name, currency, engine_version,
                   created_at, updated_at
            FROM planning.show_economics_scenarios
            ORDER BY updated_at DESC, scenario_key
            """
        ).fetchall()
    else:
        rows = connection.execute(
            """
            SELECT scenario_key, project_key, name, currency, engine_version,
                   created_at, updated_at
            FROM planning.show_economics_scenarios
            WHERE project_key = ?
            ORDER BY updated_at DESC, scenario_key
            """,
            [project_key],
        ).fetchall()
    columns = (
        "scenario_key", "project_key", "name", "currency", "engine_version",
        "created_at", "updated_at",
    )
    return [dict(zip(columns, row)) for row in rows]
