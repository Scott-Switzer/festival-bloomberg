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


def _changed_fields(before: Any, after: Any, prefix: str = "") -> list[str]:
    """Return stable leaf paths changed between two JSON-compatible values."""
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        changed: list[str] = []
        for key in sorted(set(before) | set(after)):
            path = f"{prefix}.{key}" if prefix else key
            if key not in before or key not in after:
                changed.append(path)
            else:
                changed.extend(_changed_fields(before[key], after[key], path))
        return changed
    if isinstance(before, list) and isinstance(after, list):
        changed = []
        for index in range(max(len(before), len(after))):
            path = f"{prefix}[{index}]"
            if index >= len(before) or index >= len(after):
                changed.append(path)
            else:
                changed.extend(_changed_fields(before[index], after[index], path))
        return changed
    return [prefix or "$root"]


def save_show_economics_scenario(
    connection,
    *,
    name: str,
    scenario: ShowEconomicsScenario,
    project_key: str | None = None,
    scenario_key: str | None = None,
    identity_context: dict[str, Any] | None = None,
    parent_scenario_key: str | None = None,
) -> dict[str, Any]:
    """Evaluate and save one scenario plus an append-only replayable revision."""
    if not name.strip():
        raise ValueError("scenario name must not be blank")
    result = evaluate(scenario)
    key = scenario_key or _scenario_key(project_key, name)
    inputs = scenario_to_dict(scenario)
    outputs = evaluation_to_dict(result)
    context = identity_context or {}
    previous = connection.execute(
        """
        SELECT inputs, identity_context, revision_no, parent_scenario_key
        FROM planning.show_economics_scenarios WHERE scenario_key = ?
        """,
        [key],
    ).fetchone()
    revision_no = (int(previous[2] or 0) + 1) if previous else 1
    before = {
        "inputs": _decode(previous[0]),
        "identity_context": _decode(previous[1]) or {},
    } if previous else {}
    after = {"inputs": inputs, "identity_context": context}
    changed_fields = _changed_fields(before, after)
    effective_parent = parent_scenario_key
    if effective_parent is None and previous:
        effective_parent = previous[3]
    inputs_json = json.dumps(inputs, separators=(",", ":"))
    outputs_json = json.dumps(outputs, separators=(",", ":"))
    context_json = json.dumps(context, separators=(",", ":"))
    revision_key = hashlib.sha256(
        f"{key}::revision::{revision_no}".encode("utf-8")
    ).hexdigest()[:32]
    connection.execute("BEGIN TRANSACTION")
    try:
        connection.execute(
            """
            INSERT INTO planning.show_economics_scenarios
                (scenario_key, project_key, name, currency, engine_version,
                 inputs, derived_outputs, identity_context, parent_scenario_key,
                 revision_no, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, now(), now())
            ON CONFLICT (scenario_key) DO UPDATE SET
                project_key = excluded.project_key,
                name = excluded.name,
                currency = excluded.currency,
                engine_version = excluded.engine_version,
                inputs = excluded.inputs,
                derived_outputs = excluded.derived_outputs,
                identity_context = excluded.identity_context,
                parent_scenario_key = excluded.parent_scenario_key,
                revision_no = excluded.revision_no,
                updated_at = now()
            """,
            [key, project_key, name, result.currency, result.engine_version,
             inputs_json, outputs_json, context_json, effective_parent, revision_no],
        )
        connection.execute(
            """
            INSERT INTO planning.show_economics_scenario_revisions
                (revision_key, scenario_key, revision_no, project_key, name,
                 currency, engine_version, inputs, derived_outputs,
                 identity_context, changed_fields, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, now())
            """,
            [revision_key, key, revision_no, project_key, name, result.currency,
             result.engine_version, inputs_json, outputs_json, context_json,
             json.dumps(changed_fields, separators=(",", ":"))],
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    return load_show_economics_scenario(connection, key)


def load_show_economics_scenario(connection, scenario_key: str) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT scenario_key, project_key, name, currency, engine_version,
               inputs, derived_outputs, identity_context, parent_scenario_key,
               revision_no, created_at, updated_at
        FROM planning.show_economics_scenarios
        WHERE scenario_key = ?
        """,
        [scenario_key],
    ).fetchone()
    if row is None:
        raise KeyError(f"unknown show economics scenario {scenario_key!r}")
    columns = (
        "scenario_key", "project_key", "name", "currency", "engine_version",
        "inputs", "derived_outputs", "identity_context", "parent_scenario_key",
        "revision_no", "created_at", "updated_at",
    )
    record = dict(zip(columns, row))
    record["inputs"] = _decode(record["inputs"])
    record["derived_outputs"] = _decode(record["derived_outputs"])
    record["identity_context"] = _decode(record["identity_context"]) or {}
    record["scenario"] = scenario_from_dict(record["inputs"])
    return record


def list_show_economics_scenarios(
    connection, *, project_key: str | None = None,
) -> list[dict[str, Any]]:
    if project_key is None:
        rows = connection.execute(
            """
            SELECT scenario_key, project_key, name, currency, engine_version,
                   identity_context, parent_scenario_key, revision_no,
                   created_at, updated_at
            FROM planning.show_economics_scenarios
            ORDER BY updated_at DESC, scenario_key
            """
        ).fetchall()
    else:
        rows = connection.execute(
            """
            SELECT scenario_key, project_key, name, currency, engine_version,
                   identity_context, parent_scenario_key, revision_no,
                   created_at, updated_at
            FROM planning.show_economics_scenarios
            WHERE project_key = ?
            ORDER BY updated_at DESC, scenario_key
            """,
            [project_key],
        ).fetchall()
    columns = (
        "scenario_key", "project_key", "name", "currency", "engine_version",
        "identity_context", "parent_scenario_key", "revision_no",
        "created_at", "updated_at",
    )
    records = [dict(zip(columns, row)) for row in rows]
    for record in records:
        record["identity_context"] = _decode(record["identity_context"]) or {}
    return records


def list_show_economics_revisions(connection, scenario_key: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT revision_key, scenario_key, revision_no, project_key, name,
               currency, engine_version, inputs, derived_outputs,
               identity_context, changed_fields, created_at
        FROM planning.show_economics_scenario_revisions
        WHERE scenario_key = ?
        ORDER BY revision_no DESC
        """,
        [scenario_key],
    ).fetchall()
    columns = (
        "revision_key", "scenario_key", "revision_no", "project_key", "name",
        "currency", "engine_version", "inputs", "derived_outputs",
        "identity_context", "changed_fields", "created_at",
    )
    records = [dict(zip(columns, row)) for row in rows]
    for record in records:
        for key in ("inputs", "derived_outputs", "identity_context", "changed_fields"):
            record[key] = _decode(record[key])
    return records


def duplicate_show_economics_scenario(
    connection,
    *,
    source_scenario_key: str,
    name: str,
) -> dict[str, Any]:
    """Duplicate inputs/provenance into a new scenario with explicit lineage."""
    source = load_show_economics_scenario(connection, source_scenario_key)
    duplicate_key = _scenario_key(source["project_key"], name)
    if connection.execute(
        "SELECT 1 FROM planning.show_economics_scenarios WHERE scenario_key = ?",
        [duplicate_key],
    ).fetchone():
        raise ValueError(f"show economics scenario name already exists: {name!r}")
    return save_show_economics_scenario(
        connection,
        name=name,
        project_key=source["project_key"],
        scenario=source["scenario"],
        identity_context=source["identity_context"],
        parent_scenario_key=source_scenario_key,
        scenario_key=duplicate_key,
    )
