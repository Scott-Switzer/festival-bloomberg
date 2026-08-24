"""High-assurance tests for the deterministic show economics contract."""

from dataclasses import replace
from decimal import Decimal

import pytest

from festival_bloomberg.economics.show_economics import (
    BackendBasis,
    DealDefinition,
    DealType,
    ENGINE_VERSION,
    FixedCosts,
    OutputStatus,
    Provenance,
    SensitivityField,
    ShowEconomicsScenario,
    TicketTier,
    TypedInput,
    boundary_grid,
    evaluate,
    scenario_from_dict,
    scenario_to_dict,
    sensitivity,
)
from festival_bloomberg.economics.show_economics_repository import (
    list_show_economics_scenarios,
    load_show_economics_scenario,
    save_show_economics_scenario,
)
from festival_bloomberg.terminal.storage import create_workspace_db


def d(value: str, provenance: Provenance = Provenance.USER_ASSUMPTION) -> TypedInput:
    return TypedInput(Decimal(value), provenance)


def i(value: int, provenance: Provenance = Provenance.USER_ASSUMPTION) -> TypedInput:
    return TypedInput(value, provenance)


def base_scenario() -> ShowEconomicsScenario:
    return ShowEconomicsScenario(
        currency=TypedInput("USD", Provenance.USER_ASSUMPTION),
        usable_capacity=i(100, Provenance.OBSERVED_PUBLIC),
        sellable_capacity=i(100),
        ticket_scale=(
            TicketTier("GA", d("10.00"), i(50)),
            TicketTier("Reserved", d("20.00"), i(50)),
        ),
        sell_through=d("0.80"),
        ticketing_deduction_per_paid_ticket=d("1.00"),
        tax_rate_on_gross=d("0.10"),
        deal=DealDefinition(
            deal_type=TypedInput(DealType.FLAT_GUARANTEE, Provenance.USER_ASSUMPTION),
            guarantee=TypedInput(
                Decimal("300.00"), Provenance.OBSERVED_PRIVATE, "partner:settlement:guarantee"
            ),
            backend_percentage=TypedInput.unknown(),
            backend_basis=TypedInput.unknown(),
            artist_expenses=d("20.00"),
            approved_expense_names=TypedInput.unknown(),
        ),
        costs=FixedCosts(
            marketing=d("100.00"),
            production=d("100.00", Provenance.OBSERVED_PRIVATE),
            venue=d("100.00"),
            labor=d("50.00"),
            insurance=d("20.00"),
            other=d("10.00"),
        ),
        ancillary_revenue=d("50.00"),
        sponsorship_allocation=d("50.00"),
    )


def test_flat_guarantee_golden_case_and_inverse_boundaries():
    result = evaluate(base_scenario())

    expected = {
        "gross_potential": Decimal("1500.00"),
        "weighted_average_ticket_price": Decimal("15.00"),
        "paid_tickets": 80,
        "gross_ticket_revenue": Decimal("1200.00"),
        "taxes": Decimal("120.00"),
        "ticketing_deductions": Decimal("80.00"),
        "adjusted_gross": Decimal("1000.00"),
        "artist_settlement": Decimal("320.00"),
        "total_fixed_costs": Decimal("380.00"),
        "total_variable_costs": Decimal("520.00"),
        "total_event_costs": Decimal("900.00"),
        "promoter_revenue": Decimal("1300.00"),
        "promoter_contribution": Decimal("400.00"),
        "promoter_margin": Decimal("0.307692"),
        "break_even_paid_tickets": 48,
        "break_even_sell_through": Decimal("0.480000"),
        "break_even_average_ticket_price": Decimal("9.45"),
        "break_even_sellable_capacity": 60,
        "margin_of_safety_tickets": 32,
        "additional_cost_capacity": Decimal("400.00"),
        "maximum_artist_settlement_at_break_even": Decimal("720.00"),
        "maximum_flat_guarantee_at_break_even": Decimal("700.00"),
    }
    assert {name: result[name].value for name in expected} == expected
    assert result.engine_version == ENGINE_VERSION
    assert result.currency == "USD"
    assert result["promoter_contribution"].provenance == Provenance.DERIVED
    assert "deal.guarantee" in result["promoter_contribution"].lineage
    assert result["promoter_contribution"].currency == "USD"


def test_input_ledger_and_serialization_preserve_private_provenance_and_decimal():
    scenario = base_scenario()
    ledger = scenario.input_ledger()
    assert ledger["deal.guarantee"].provenance == Provenance.OBSERVED_PRIVATE
    assert ledger["deal.guarantee"].evidence_ref == "partner:settlement:guarantee"
    assert ledger["costs.production"].provenance == Provenance.OBSERVED_PRIVATE

    payload = scenario_to_dict(scenario)
    restored = scenario_from_dict(payload)
    assert restored == scenario
    assert scenario_to_dict(restored) == payload
    assert evaluate(restored)["promoter_contribution"].value == Decimal("400.00")


@pytest.mark.parametrize(
    ("field", "expected_known"),
    [
        ("sellable_capacity", "gross_potential"),
        ("guarantee", "gross_ticket_revenue"),
        ("ticket_fee", "artist_settlement"),
    ],
)
def test_unknown_inputs_propagate_without_becoming_zero(field: str, expected_known: str):
    scenario = base_scenario()
    if field == "sellable_capacity":
        scenario = replace(scenario, sellable_capacity=TypedInput.unknown())
    elif field == "guarantee":
        scenario = replace(
            scenario, deal=replace(scenario.deal, guarantee=TypedInput.unknown())
        )
    else:
        scenario = replace(
            scenario, ticketing_deduction_per_paid_ticket=TypedInput.unknown()
        )
    result = evaluate(scenario)
    assert result[expected_known].status == OutputStatus.KNOWN
    assert result["promoter_contribution"].status == OutputStatus.UNKNOWN
    assert result["promoter_contribution"].value is None


def test_unknown_currency_fails_closed_for_money_and_boundaries():
    result = evaluate(replace(base_scenario(), currency=TypedInput.unknown()))
    assert result["paid_tickets"].value == 80
    assert result["gross_ticket_revenue"].status == OutputStatus.UNKNOWN
    assert result["break_even_paid_tickets"].status == OutputStatus.UNKNOWN
    assert result["promoter_contribution"].value is None


@pytest.mark.parametrize(
    ("paid_tickets", "expected_settlement"),
    [(40, Decimal("320.00")), (80, Decimal("320.00")), (100, Decimal("395.00"))],
)
def test_guarantee_vs_percentage_below_at_and_above_crossover(
    paid_tickets: int, expected_settlement: Decimal,
):
    scenario = base_scenario()
    scenario = replace(
        scenario,
        sell_through=d(str(Decimal(paid_tickets) / Decimal(100))),
        deal=DealDefinition(
            deal_type=TypedInput.assumption(DealType.GUARANTEE_VS_PERCENTAGE),
            guarantee=d("300.00"),
            backend_percentage=d("0.25"),
            backend_basis=TypedInput.assumption(BackendBasis.GROSS_BOX_OFFICE),
            artist_expenses=d("20.00"),
            approved_expense_names=TypedInput.unknown(),
        ),
    )
    assert evaluate(scenario)["artist_settlement"].value == expected_settlement


def test_percentage_of_adjusted_gross_and_approved_expense_bases_are_explicit():
    scenario = base_scenario()
    percentage = replace(
        scenario,
        deal=DealDefinition(
            deal_type=TypedInput.assumption(DealType.PERCENTAGE_OF_DEFINED_BASE),
            guarantee=TypedInput.unknown(),
            backend_percentage=d("0.25"),
            backend_basis=TypedInput.assumption(BackendBasis.ADJUSTED_GROSS),
            artist_expenses=d("20.00"),
            approved_expense_names=TypedInput.unknown(),
        ),
    )
    assert evaluate(percentage)["artist_settlement"].value == Decimal("270.00")

    net = replace(
        percentage,
        deal=replace(
            percentage.deal,
            backend_basis=TypedInput.assumption(BackendBasis.NET_AFTER_APPROVED_EXPENSES),
            approved_expense_names=TypedInput.assumption(("venue", "production")),
        ),
    )
    assert evaluate(net)["artist_settlement"].value == Decimal("220.00")


def test_zero_and_full_sell_through_boundaries_are_deterministic():
    zero = evaluate(replace(base_scenario(), sell_through=d("0")))
    assert zero["paid_tickets"].value == 0
    assert zero["gross_ticket_revenue"].value == Decimal("0.00")
    assert zero["promoter_contribution"].value == Decimal("-600.00")
    assert zero["break_even_sellable_capacity"].status == OutputStatus.NOT_ACHIEVABLE

    full = evaluate(replace(base_scenario(), sell_through=d("1")))
    assert full["paid_tickets"].value == 100
    assert full["gross_ticket_revenue"].value == Decimal("1500.00")
    assert full["promoter_contribution"].value == Decimal("650.00")


def test_zero_ticket_scale_is_not_fabricated_but_zero_sales_revenue_is_known():
    scenario = replace(
        base_scenario(),
        usable_capacity=i(0),
        sellable_capacity=i(0),
        ticket_scale=(TicketTier("closed", d("0.10"), i(0)),),
        sell_through=d("0"),
    )
    result = evaluate(scenario)
    assert result["weighted_average_ticket_price"].status == OutputStatus.UNKNOWN
    assert result["gross_ticket_revenue"].value == Decimal("0.00")


def test_decimal_arithmetic_handles_binary_float_trouble_exactly():
    scenario = replace(
        base_scenario(),
        usable_capacity=i(3),
        sellable_capacity=i(3),
        ticket_scale=(TicketTier("micro", d("0.10"), i(3)),),
        sell_through=d("1"),
    )
    result = evaluate(scenario)
    assert result["gross_potential"].value == Decimal("0.30")
    assert result["gross_ticket_revenue"].value == Decimal("0.30")
    with pytest.raises(TypeError, match="binary floating-point"):
        TypedInput(0.1, Provenance.USER_ASSUMPTION)


def test_boundary_grid_identifies_exact_contribution_and_margin_hurdles():
    points = boundary_grid(
        base_scenario(),
        average_ticket_prices=[Decimal("15")],
        sellable_capacities=[47, 48, 80],
        sell_throughs=[Decimal("1")],
        minimum_contribution=Decimal("0"),
        minimum_margin=Decimal("0"),
    )
    assert [point.promoter_contribution.value for point in points] == [
        Decimal("-12.50"), Decimal("0.00"), Decimal("400.00")
    ]
    assert [point.meets_hurdle for point in points] == [False, True, True]
    assert [point.paid_tickets for point in points] == [47, 48, 80]


@pytest.mark.parametrize(
    ("field", "values"),
    [
        (SensitivityField.ARTIST_GUARANTEE, [Decimal("250"), Decimal("300"), Decimal("350")]),
        (SensitivityField.MARKETING_COST, [Decimal("50"), Decimal("100"), Decimal("150")]),
        (SensitivityField.PRODUCTION_COST, [Decimal("50"), Decimal("100"), Decimal("150")]),
    ],
)
def test_cost_sensitivities_cannot_improve_contribution(field, values):
    contributions = [
        point.promoter_contribution.value for point in sensitivity(base_scenario(), field, values)
    ]
    assert contributions == sorted(contributions, reverse=True)


@pytest.mark.parametrize(
    ("field", "values"),
    [
        (SensitivityField.SELL_THROUGH, [Decimal("0.5"), Decimal("0.8"), Decimal("1")]),
        (SensitivityField.AVERAGE_TICKET_PRICE, [Decimal("10"), Decimal("15"), Decimal("20")]),
        (SensitivityField.SELLABLE_CAPACITY, [50, 80, 100]),
    ],
)
def test_volume_and_price_sensitivities_are_monotone(field, values):
    contributions = [
        point.promoter_contribution.value for point in sensitivity(base_scenario(), field, values)
    ]
    assert contributions == sorted(contributions)


def test_break_even_rounding_policy_finds_first_nonnegative_ticket():
    points = boundary_grid(
        base_scenario(),
        average_ticket_prices=[Decimal("15")],
        sellable_capacities=[47, 48],
        sell_throughs=[Decimal("1")],
    )
    assert points[0].promoter_contribution.value < 0
    assert points[1].promoter_contribution.value == Decimal("0.00")
    assert evaluate(base_scenario())["break_even_paid_tickets"].value == 48


def test_invalid_scenarios_fail_closed():
    scenario = base_scenario()
    with pytest.raises(ValueError, match="nonnegative"):
        evaluate(replace(scenario, usable_capacity=i(-1)))
    with pytest.raises(ValueError, match="between 0 and 1"):
        evaluate(replace(scenario, sell_through=d("1.01")))
    with pytest.raises(ValueError, match="currency"):
        evaluate(replace(scenario, currency=TypedInput.assumption("US$")))
    with pytest.raises(ValueError, match="cannot exceed"):
        evaluate(replace(scenario, sellable_capacity=i(101)))
    with pytest.raises(ValueError, match="tier quantities"):
        evaluate(replace(scenario, sellable_capacity=i(99)))


def test_percentage_deals_require_defined_basis_and_expense_names():
    scenario = base_scenario()
    incomplete = replace(
        scenario,
        deal=DealDefinition(
            deal_type=TypedInput.assumption(DealType.PERCENTAGE_OF_DEFINED_BASE),
            guarantee=TypedInput.unknown(),
            backend_percentage=d("0.20"),
            backend_basis=TypedInput.unknown(),
            artist_expenses=d("0"),
            approved_expense_names=TypedInput.unknown(),
        ),
    )
    with pytest.raises(ValueError, match="explicit backend basis"):
        evaluate(incomplete)

    undefined = replace(
        incomplete,
        deal=replace(
            incomplete.deal,
            backend_basis=TypedInput.assumption(BackendBasis.NET_AFTER_APPROVED_EXPENSES),
            approved_expense_names=TypedInput.assumption(("mystery_recoupment",)),
        ),
    )
    with pytest.raises(ValueError, match="undefined approved expenses"):
        evaluate(undefined)


def test_workspace_persistence_reproduces_scenario_without_canonical_evidence(tmp_path):
    connection = create_workspace_db(str(tmp_path / "workspace.duckdb"))
    try:
        saved = save_show_economics_scenario(
            connection,
            name="Partner room scenario",
            project_key="planning-project-1",
            scenario=base_scenario(),
        )
        assert saved["currency"] == "USD"
        assert saved["engine_version"] == ENGINE_VERSION
        assert saved["inputs"]["deal"]["guarantee"]["value"] == "300.00"
        assert (
            saved["inputs"]["deal"]["guarantee"]["provenance"]
            == Provenance.OBSERVED_PRIVATE.value
        )
        assert (
            saved["derived_outputs"]["outputs"]["promoter_contribution"]["value"]
            == "400.00"
        )
        assert saved["scenario"] == base_scenario()
        assert evaluate(saved["scenario"])["promoter_contribution"].value == Decimal("400.00")

        same = save_show_economics_scenario(
            connection,
            name="Partner room scenario",
            project_key="planning-project-1",
            scenario=base_scenario(),
        )
        assert same["scenario_key"] == saved["scenario_key"]
        assert len(list_show_economics_scenarios(
            connection, project_key="planning-project-1"
        )) == 1
        assert load_show_economics_scenario(connection, saved["scenario_key"])[
            "scenario"
        ] == base_scenario()

        canonical_evidence_tables = connection.execute(
            """
            SELECT COUNT(*) FROM duckdb_tables()
            WHERE (schema_name, table_name) IN (
              ('economics', 'event_outcome_claims'),
              ('events', 'provider_event_snapshots'),
              ('metrics', 'artist_attention_observations')
            )
            """
        ).fetchone()[0]
        assert canonical_evidence_tables == 0
    finally:
        connection.close()


def test_workspace_load_missing_scenario_is_explicit(tmp_path):
    connection = create_workspace_db(str(tmp_path / "workspace.duckdb"))
    try:
        with pytest.raises(KeyError, match="unknown show economics scenario"):
            load_show_economics_scenario(connection, "missing")
    finally:
        connection.close()
