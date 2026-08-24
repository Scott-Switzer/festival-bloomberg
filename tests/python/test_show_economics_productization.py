"""Acceptance contract for SHOW_ECONOMICS_WORKBENCH_PRODUCTIZATION_V1."""

from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from time import perf_counter

import duckdb
import pytest

from festival_bloomberg.economics.show_economics import (
    BackendBasis,
    DealDefinition,
    DealType,
    FixedCosts,
    Provenance,
    ShowEconomicsScenario,
    TicketTier,
    TypedInput,
    scenario_to_dict,
)
from festival_bloomberg.economics.show_economics_product import (
    calculate_workbench,
    capacity_prefill,
    compare_saved_scenarios,
)
from festival_bloomberg.economics.show_economics_repository import (
    duplicate_show_economics_scenario,
    list_show_economics_revisions,
    load_show_economics_scenario,
    save_show_economics_scenario,
)
from festival_bloomberg.migrations import apply_pending_migrations
from festival_bloomberg.planning.repository import create_project
from festival_bloomberg.terminal.server import TerminalApp
from festival_bloomberg.terminal.storage import create_workspace_db


def typed(value, provenance=Provenance.USER_ASSUMPTION, evidence_ref=None):
    return TypedInput(value, provenance, evidence_ref, "2026-08-23T12:00:00Z", "buyer@example")


def fixture_scenario() -> ShowEconomicsScenario:
    return ShowEconomicsScenario(
        currency=typed("USD"),
        usable_capacity=typed(1000),
        sellable_capacity=typed(900),
        ticket_scale=(
            TicketTier("GA", typed(Decimal("50")), typed(600)),
            TicketTier("Premium", typed(Decimal("100")), typed(300)),
        ),
        sell_through=typed(Decimal("0.80")),
        ticketing_deduction_per_paid_ticket=typed(Decimal("3")),
        tax_rate_on_gross=typed(Decimal("0.08")),
        deal=DealDefinition(
            deal_type=typed(DealType.FLAT_GUARANTEE),
            guarantee=typed(Decimal("18000")),
            backend_percentage=TypedInput.unknown(),
            backend_basis=TypedInput.unknown(),
            artist_expenses=typed(Decimal("1000")),
            approved_expense_names=TypedInput.unknown(),
        ),
        costs=FixedCosts(
            marketing=typed(Decimal("2500")),
            production=typed(Decimal("5000")),
            venue=typed(Decimal("3500")),
            labor=typed(Decimal("2000")),
            insurance=typed(Decimal("500")),
            other=typed(Decimal("1000")),
        ),
        ancillary_revenue=typed(Decimal("2500")),
        sponsorship_allocation=typed(Decimal("1000")),
    )


def test_case_a_flat_guarantee_product_fixture_has_equations_not_forecast():
    response = calculate_workbench(
        scenario_to_dict(fixture_scenario()),
        sensitivity_requests={"sell_through": ["0.60", "0.80", "1.00"]},
        boundary_request={
            "average_ticket_prices": ["50", "75"],
            "sellable_capacities": [700, 900],
            "sell_throughs": ["0.70", "0.90"],
            "minimum_contribution": "0",
        },
    )
    outputs = response["evaluation"]["outputs"]
    assert outputs["paid_tickets"]["value"] == 720
    assert outputs["gross_potential"]["value"] == "60000.00"
    assert outputs["gross_ticket_revenue"]["value"] == "48000.00"
    assert outputs["artist_settlement"]["value"] == "19000.00"
    assert outputs["promoter_contribution"]["value"] == "12000.00"
    assert outputs["break_even_paid_tickets"]["value"] == 515
    assert outputs["break_even_sell_through"]["value"] == "0.572222"
    assert len(response["sensitivities"]["sell_through"]) == 3
    assert len(response["boundaries"]) == 8
    assert "not probabilities" in response["labels"]["sensitivities"]
    assert "not predictions" in response["labels"]["boundaries"]


def test_case_b_guarantee_vs_percentage_crossover_is_visible():
    scenario = replace(
        fixture_scenario(),
        deal=DealDefinition(
            deal_type=typed(DealType.GUARANTEE_VS_PERCENTAGE),
            guarantee=typed(Decimal("18000")),
            backend_percentage=typed(Decimal("0.85")),
            backend_basis=typed(BackendBasis.ADJUSTED_GROSS),
            artist_expenses=typed(Decimal("0")),
            approved_expense_names=TypedInput.unknown(),
        ),
    )
    response = calculate_workbench(
        scenario_to_dict(scenario),
        sensitivity_requests={"sell_through": ["0.20", "1.00"]},
    )
    points = response["sensitivities"]["sell_through"]
    assert points[0]["promoter_contribution"]["value"] != points[1]["promoter_contribution"]["value"]
    assert response["evaluation"]["outputs"]["artist_settlement"]["status"] == "KNOWN"


def test_case_c_unknown_is_explicit_and_not_zero():
    inputs = scenario_to_dict(fixture_scenario())
    inputs["costs"]["production"] = {
        "value": None, "provenance": "UNKNOWN", "evidence_ref": None,
        "as_of": None, "entered_by": None,
    }
    response = calculate_workbench(inputs)
    contribution = response["evaluation"]["outputs"]["promoter_contribution"]
    assert contribution["status"] == "UNKNOWN"
    assert contribution["value"] is None
    assert any(item.endswith("production") for item in contribution["lineage"])


def test_cases_d_and_e_compare_revision_duplicate_and_reload(tmp_path):
    workspace_path = str(tmp_path / "workspace.duckdb")
    conn = create_workspace_db(workspace_path)
    try:
        first = save_show_economics_scenario(
            conn, project_key="p1", name="Base", scenario=fixture_scenario(),
            identity_context={"artist_name": "Artist A", "holds": 10},
        )
        changed = replace(fixture_scenario(), sell_through=typed(Decimal("0.90")))
        second_revision = save_show_economics_scenario(
            conn, project_key="p1", name="Base", scenario=changed,
            scenario_key=first["scenario_key"],
            identity_context={"artist_name": "Artist A", "holds": 10},
        )
        duplicate = duplicate_show_economics_scenario(
            conn, source_scenario_key=first["scenario_key"], name="Upside copy",
        )
        with pytest.raises(ValueError, match="name already exists"):
            duplicate_show_economics_scenario(
                conn, source_scenario_key=first["scenario_key"], name="Upside copy",
            )
        variant = replace(
            fixture_scenario(),
            usable_capacity=typed(1100),
            sellable_capacity=typed(1000),
            ticket_scale=(
                TicketTier("GA", typed(Decimal("55")), typed(700)),
                TicketTier("Premium", typed(Decimal("110")), typed(300)),
            ),
            sell_through=typed(Decimal("0.70")),
            deal=replace(fixture_scenario().deal, guarantee=typed(Decimal("20000"))),
        )
        variant_record = save_show_economics_scenario(
            conn, project_key="p1", name="Alternate economics", scenario=variant,
            identity_context={"artist_name": "Artist A"},
        )
        revisions = list_show_economics_revisions(conn, first["scenario_key"])
        replay = load_show_economics_scenario(conn, first["scenario_key"])
        comparison = compare_saved_scenarios([replay, variant_record])

        assert second_revision["revision_no"] == 2
        assert [r["revision_no"] for r in revisions] == [2, 1]
        assert "inputs.sell_through.value" in revisions[0]["changed_fields"]
        assert revisions[0]["inputs"] == replay["inputs"]
        assert replay["scenario"].sell_through.value == Decimal("0.90")
        assert replay["inputs"]["sell_through"]["provenance"] == "USER_ASSUMPTION"
        assert replay["inputs"]["sell_through"]["entered_by"] == "buyer@example"
        assert duplicate["parent_scenario_key"] == first["scenario_key"]
        assert comparison["ranking"] is None
        assert comparison["recommendation"] is None
        by_metric = {row["metric"]: row for row in comparison["rows"]}
        assert by_metric["sellable_capacity"]["values"][1]["delta_from_baseline"] == "100"
        assert by_metric["sell_through"]["values"][1]["delta_from_baseline"] == "-0.20"
        assert by_metric["artist_guarantee"]["values"][1]["delta_from_baseline"] == "2000"
        assert by_metric["weighted_average_ticket_price"]["values"][1]["value"] == "71.50"
    finally:
        conn.close()

    reopened = create_workspace_db(workspace_path)
    try:
        reloaded = load_show_economics_scenario(reopened, first["scenario_key"])
        assert reloaded["inputs"] == replay["inputs"]
        assert reloaded["derived_outputs"] == replay["derived_outputs"]
        assert reloaded["scenario"] == replay["scenario"]
    finally:
        reopened.close()


def test_prefill_only_offers_unconflicted_configuration_compatible_claim(tmp_path):
    serving = duckdb.connect(str(tmp_path / "serving.duckdb"))
    apply_pending_migrations(serving)
    serving.execute(
        "INSERT INTO economics.venue_source_ids "
        "(mapping_id, canonical_venue_id, venue_name, resolution_status, knowledge_time) "
        "VALUES ('map1','v1','Test Room','RESOLVED','2026-01-01')"
    )
    serving.execute(
        "INSERT INTO economics.venue_capacity_claims "
        "(claim_id, canonical_venue_id, capacity_value, capacity_kind, provider, source, "
        "retrieved_at, knowledge_time, claim_status) VALUES "
        "('c1','v1',1000,'STANDING','wikidata','p1083','2026-01-01','2026-01-01','OBSERVED'),"
        "('c2','v1',1500,'MAX_PERSONS','wikipedia','infobox','2026-01-01','2026-01-01','OBSERVED')"
    )
    try:
        exact = capacity_prefill(serving, venue_key="Test Room", event_configuration="STANDING")
        upper = capacity_prefill(serving, venue_key="Test Room", event_configuration=None)
        assert exact["usable_capacity_suggestion"]["value"] == 1000
        assert exact["sellable_capacity_suggestion"] is None
        assert upper["usable_capacity_suggestion"] is None
        assert len(upper["claims"]) == 2
    finally:
        serving.close()


def test_workbench_api_uses_serving_for_evidence_and_workspace_for_state(tmp_path):
    serving = duckdb.connect(str(tmp_path / "serving.duckdb"))
    apply_pending_migrations(serving)
    workspace = create_workspace_db(str(tmp_path / "workspace.duckdb"))
    project = create_project(workspace, name="Test Festival")
    other_project = create_project(workspace, name="Other Festival")
    app = TerminalApp(serving, workspace)
    inputs = scenario_to_dict(fixture_scenario())
    try:
        calc = app.dispatch(
            "POST", f"/api/planning/projects/{project['project_key']}/economics/calculate",
            body=json.dumps({"inputs": inputs}).encode(),
        )
        assert calc["status"] == 200
        saved = app.dispatch(
            "POST", f"/api/planning/projects/{project['project_key']}/economics",
            body=json.dumps({
                "name": "API fixture", "inputs": inputs,
                "identity_context": {"artist_name": "API Artist"},
            }).encode(),
        )
        record = json.loads(saved["body"])
        listed = app.dispatch(
            "GET", f"/api/planning/projects/{project['project_key']}/economics"
        )
        loaded = app.dispatch("GET", f"/api/planning/economics/{record['scenario_key']}")
        revisions = app.dispatch(
            "GET", f"/api/planning/economics/{record['scenario_key']}/revisions"
        )
        cross_project = app.dispatch(
            "POST",
            f"/api/planning/projects/{other_project['project_key']}/economics",
            body=json.dumps({
                "name": "Moved scenario", "scenario_key": record["scenario_key"],
                "inputs": inputs,
            }).encode(),
        )
        assert saved["status"] == listed["status"] == loaded["status"] == revisions["status"] == 200
        assert cross_project["status"] == 400
        assert "does not belong" in json.loads(cross_project["body"])["error"]
        assert len(json.loads(listed["body"])) == 1
        assert json.loads(revisions["body"])[0]["revision_no"] == 1
        assert serving.execute(
            "SELECT COUNT(*) FROM duckdb_tables() WHERE table_name='show_economics_scenarios'"
        ).fetchone()[0] == 0
    finally:
        workspace.close()
        serving.close()


def test_calculate_save_reload_compare_are_interactive_speed(tmp_path):
    conn = create_workspace_db(str(tmp_path / "workspace.duckdb"))
    inputs = scenario_to_dict(fixture_scenario())
    started = perf_counter()
    for index in range(10):
        calculate_workbench(inputs)
        save_show_economics_scenario(
            conn, project_key="p1", name=f"S{index}", scenario=fixture_scenario()
        )
    records = [load_show_economics_scenario(conn, row[0]) for row in conn.execute(
        "SELECT scenario_key FROM planning.show_economics_scenarios ORDER BY scenario_key LIMIT 4"
    ).fetchall()]
    compare_saved_scenarios(records)
    elapsed = perf_counter() - started
    conn.close()
    assert elapsed < 2.0
