"""Product-facing orchestration for the deterministic show-economics engine.

This module does not add a second calculator.  It only serializes engine
results, builds explicit equation/sensitivity tables, compares saved scenarios,
and offers fail-closed public capacity claims to the planning workbench.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable

from .capacity import CapacityClaim, assess_venue_claims
from .show_economics import (
    SensitivityField,
    ShowEconomicsScenario,
    boundary_grid,
    evaluate,
    evaluation_to_dict,
    output_to_dict,
    scenario_from_dict,
    sensitivity,
)

SUPPORTED_NOW = "SUPPORTED_NOW"
SCHEMA_READY_NOT_CALCULATED = "SCHEMA_READY_NOT_CALCULATED"
NOT_SUPPORTED = "NOT_SUPPORTED"

PRIVATE_DATA_READINESS: tuple[dict[str, str], ...] = (
    {"field": "usable_capacity", "status": SUPPORTED_NOW, "mapping": "event_usable_capacity"},
    {"field": "sellable_capacity", "status": SUPPORTED_NOW, "mapping": "ticket_capacity"},
    {"field": "ticket_tiers_and_face_prices", "status": SUPPORTED_NOW, "mapping": "ticket_scale"},
    {"field": "holds_kills_comps", "status": SCHEMA_READY_NOT_CALCULATED, "mapping": "identity_context"},
    {"field": "paid_tickets", "status": SCHEMA_READY_NOT_CALCULATED, "mapping": "derived_or_validation_context"},
    {"field": "deal_type", "status": SUPPORTED_NOW, "mapping": "deal_type"},
    {"field": "guarantee", "status": SUPPORTED_NOW, "mapping": "artist_guarantee"},
    {"field": "backend_percentage", "status": SUPPORTED_NOW, "mapping": "artist_backend_pct"},
    {"field": "backend_basis", "status": SUPPORTED_NOW, "mapping": "backend_basis"},
    {"field": "approved_expenses", "status": SUPPORTED_NOW, "mapping": "approved_expense_names"},
    {"field": "taxes", "status": SUPPORTED_NOW, "mapping": "tax_rate_on_gross"},
    {"field": "ticketing_deductions", "status": SUPPORTED_NOW, "mapping": "deduction_per_paid_ticket"},
    {"field": "venue_production_labor_marketing_insurance_other", "status": SUPPORTED_NOW, "mapping": "fixed_costs"},
    {"field": "ancillary_revenue", "status": SUPPORTED_NOW, "mapping": "ancillary_revenue"},
    {"field": "sponsorship_allocation", "status": SUPPORTED_NOW, "mapping": "sponsorship_allocation"},
    {"field": "offer_created_at", "status": SUPPORTED_NOW, "mapping": "identity_context"},
    {"field": "assumption_as_of", "status": SUPPORTED_NOW, "mapping": "typed_input.as_of"},
    {"field": "revision_id", "status": SUPPORTED_NOW, "mapping": "revision_key"},
    {"field": "evidence_reference", "status": SUPPORTED_NOW, "mapping": "typed_input.evidence_ref"},
    {"field": "gross_box_office", "status": SCHEMA_READY_NOT_CALCULATED, "mapping": "validation_outcome"},
    {"field": "final_artist_settlement", "status": SCHEMA_READY_NOT_CALCULATED, "mapping": "validation_outcome"},
    {"field": "settlement_finalized_at", "status": SCHEMA_READY_NOT_CALCULATED, "mapping": "validation_context"},
    {"field": "refunds_chargebacks", "status": NOT_SUPPORTED, "mapping": "none"},
    {"field": "tier_specific_sell_through", "status": NOT_SUPPORTED, "mapping": "none"},
    {"field": "tax_jurisdiction_rules", "status": NOT_SUPPORTED, "mapping": "none"},
    {"field": "payout_timing_and_fx", "status": NOT_SUPPORTED, "mapping": "none"},
)


def _decimal_values(values: Iterable[Any]) -> list[Decimal]:
    return [Decimal(str(value)) for value in values]


def calculate_workbench(
    inputs: dict[str, Any],
    *,
    sensitivity_requests: dict[str, list[Any]] | None = None,
    boundary_request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate one input contract and optional explicit what-if equations."""
    scenario = scenario_from_dict(inputs)
    result = evaluate(scenario)
    sensitivities: dict[str, list[dict[str, Any]]] = {}
    for field_name, values in (sensitivity_requests or {}).items():
        field = SensitivityField(field_name)
        typed_values: list[Decimal | int]
        if field == SensitivityField.SELLABLE_CAPACITY:
            typed_values = [int(value) for value in values]
        else:
            typed_values = _decimal_values(values)
        sensitivities[field.value] = [
            {
                "input": str(point.input_value.value),
                "provenance": point.input_value.provenance.value,
                "promoter_contribution": output_to_dict(point.promoter_contribution),
                "promoter_margin": output_to_dict(point.promoter_margin),
            }
            for point in sensitivity(scenario, field, typed_values)
        ]
    boundaries: list[dict[str, Any]] = []
    if boundary_request:
        boundaries = [
            {
                "average_ticket_price": str(point.average_ticket_price),
                "sellable_capacity": point.sellable_capacity,
                "sell_through": str(point.sell_through),
                "paid_tickets": point.paid_tickets,
                "promoter_contribution": output_to_dict(point.promoter_contribution),
                "promoter_margin": output_to_dict(point.promoter_margin),
                "meets_hurdle": point.meets_hurdle,
            }
            for point in boundary_grid(
                scenario,
                average_ticket_prices=_decimal_values(boundary_request.get("average_ticket_prices", [])),
                sellable_capacities=[int(v) for v in boundary_request.get("sellable_capacities", [])],
                sell_throughs=_decimal_values(boundary_request.get("sell_throughs", [])),
                minimum_contribution=Decimal(str(boundary_request.get("minimum_contribution", "0"))),
                minimum_margin=(Decimal(str(boundary_request["minimum_margin"]))
                                if boundary_request.get("minimum_margin") is not None else None),
            )
        ]
    return {
        "evaluation": evaluation_to_dict(result),
        "sensitivities": sensitivities,
        "boundaries": boundaries,
        "labels": {
            "sensitivities": "WHAT-IF EQUATIONS — not probabilities or forecasts",
            "boundaries": "SCENARIO BOUNDARIES — not predictions",
        },
    }


def compare_saved_scenarios(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare two-to-four scenarios without ranking or recommendation."""
    if not 2 <= len(records) <= 4:
        raise ValueError("comparison requires two to four saved scenarios")
    baseline = records[0]
    output_names = sorted({
        name
        for record in records
        for name in record["derived_outputs"]["outputs"]
    })
    rows: list[dict[str, Any]] = []
    input_dimensions = (
        ("usable_capacity", ("usable_capacity",)),
        ("sellable_capacity", ("sellable_capacity",)),
        ("sell_through", ("sell_through",)),
        ("artist_guarantee", ("deal", "guarantee")),
    )
    for name, path in input_dimensions:
        typed_inputs = []
        for record in records:
            item: Any = record["inputs"]
            for segment in path:
                item = item[segment]
            typed_inputs.append(item)
        base = typed_inputs[0]
        values = []
        for record, item in zip(records, typed_inputs):
            known = item.get("provenance") != "UNKNOWN" and item.get("value") is not None
            base_known = base.get("provenance") != "UNKNOWN" and base.get("value") is not None
            delta = None
            currencies_match = (
                name != "artist_guarantee"
                or record["currency"] == baseline["currency"]
            )
            if known and base_known and currencies_match:
                delta = str(Decimal(str(item["value"])) - Decimal(str(base["value"])))
            values.append({
                "scenario_key": record["scenario_key"],
                "value": item.get("value"),
                "status": "KNOWN" if known else "UNKNOWN",
                "currency": record["currency"] if name == "artist_guarantee" else None,
                "provenance": item.get("provenance"),
                "delta_from_baseline": delta,
            })
        rows.append({"metric": name, "values": values})
    for name in output_names:
        base_output = baseline["derived_outputs"]["outputs"].get(name, {})
        values = []
        for record in records:
            output = record["derived_outputs"]["outputs"].get(name, {})
            delta = None
            if (
                output.get("status") == "KNOWN"
                and base_output.get("status") == "KNOWN"
                and output.get("currency") == base_output.get("currency")
                and output.get("value") is not None
                and base_output.get("value") is not None
            ):
                delta = str(Decimal(str(output["value"])) - Decimal(str(base_output["value"])))
            values.append({
                "scenario_key": record["scenario_key"],
                "value": output.get("value"),
                "status": output.get("status", "UNKNOWN"),
                "currency": output.get("currency"),
                "reason": output.get("reason"),
                "delta_from_baseline": delta,
            })
        rows.append({"metric": name, "values": values})
    return {
        "baseline_scenario_key": baseline["scenario_key"],
        "scenarios": [
            {"scenario_key": r["scenario_key"], "name": r["name"],
             "revision_no": r["revision_no"], "identity_context": r["identity_context"]}
            for r in records
        ],
        "rows": rows,
        "ranking": None,
        "recommendation": None,
    }


def capacity_prefill(serving_connection, *, venue_key: str, event_configuration: str | None) -> dict[str, Any]:
    """Offer capacity claims without collapsing conflicts or inventing inventory."""
    rows = serving_connection.execute(
        """
        SELECT c.claim_id, s.venue_name, c.capacity_value, c.capacity_kind,
               c.configuration_description, c.provider AS source_provider,
               c.source_url, c.knowledge_time, c.claim_status, c.usage_label
        FROM economics.venue_capacity_claims c
        LEFT JOIN economics.venue_source_ids s
          ON s.canonical_venue_id = c.canonical_venue_id
        WHERE lower(s.venue_name) = lower(?)
        ORDER BY c.knowledge_time DESC, c.claim_id
        """,
        [venue_key],
    ).fetchall()
    columns = (
        "claim_id", "venue_name", "capacity", "capacity_type",
        "configuration_description", "source_provider", "source_url",
        "knowledge_time", "claim_status", "usage_label",
    )
    claim_dicts = [dict(zip(columns, row)) for row in rows]
    objects: list[CapacityClaim] = []
    for d in claim_dicts:
        objects.append(
            CapacityClaim(
                claim_id=d["claim_id"],
                canonical_venue_id=venue_key,
                capacity_value=d["capacity"],
                capacity_kind=d["capacity_type"],
                configuration_description=d["configuration_description"],
                effective_from=None,
                effective_to=None,
                provider=d["source_provider"],
                source="serving",
                source_url=d["source_url"],
                source_publication_time=None,
                retrieved_at=str(d["knowledge_time"] or ""),
                knowledge_time=str(d["knowledge_time"] or ""),
                source_observation_id=None,
                claim_status=d["claim_status"],
                usage_label=d["usage_label"],
            )
        )
    # One deterministic contract for the decision and the evidence.
    assessment = assess_venue_claims(objects)
    status_by_id = {c.claim_id: c.claim_status for c in objects}
    claims = [
        {**d, "claim_status": status_by_id.get(d["claim_id"], d["claim_status"])}
        for d in claim_dicts
    ]
    wanted = (event_configuration or "").upper()
    pair = next(
        (p for p in assessment["safe_pairs"] if p["configuration"] == wanted),
        None,
    )
    suggestion = None
    if pair:
        evidence = next(
            (c for c in objects if c.claim_id in pair["supporting_claim_ids"]),
            None,
        )
        suggestion = {
            "value": pair["value"],
            "provenance": "OBSERVED_PUBLIC",
            "evidence_ref": (
                (evidence.source_url or evidence.claim_id) if evidence else pair["supporting_claim_ids"][0]
            ),
            "as_of": str(evidence.knowledge_time) if evidence else None,
            "supporting_claim_ids": pair["supporting_claim_ids"],
        }
    return {
        "venue_key": venue_key,
        "event_configuration": event_configuration,
        "claims": claims,
        "usable_capacity_suggestion": suggestion,
        "sellable_capacity_suggestion": None,
        "status": "CONFIGURATION_COMPATIBLE" if suggestion else (
            "CONFLICTING_COMPATIBLE_CLAIMS"
            if any(p["configuration"] == wanted for p in assessment["review_required_pairs"])
            else "UPPER_BOUND_OR_INCOMPATIBLE_ONLY" if claim_dicts else "UNKNOWN"
        ),
        "assessment": assessment,
    }
