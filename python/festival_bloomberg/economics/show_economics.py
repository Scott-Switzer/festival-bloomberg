"""Deterministic show economics with explicit provenance and UNKNOWNs.

This module answers equations of the form "if these inputs hold, what follows?"
It does not predict demand, attendance, guarantees, or booking outcomes.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, localcontext
from enum import Enum
import re
from typing import Any, Iterable


ENGINE_VERSION = "show_economics_v1"
MONEY_QUANTUM = Decimal("0.01")
RATE_QUANTUM = Decimal("0.000001")


class Provenance(str, Enum):
    OBSERVED_PUBLIC = "OBSERVED_PUBLIC"
    OBSERVED_PRIVATE = "OBSERVED_PRIVATE"
    USER_ASSUMPTION = "USER_ASSUMPTION"
    DERIVED = "DERIVED"
    UNKNOWN = "UNKNOWN"


class OutputStatus(str, Enum):
    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"
    NOT_ACHIEVABLE = "NOT_ACHIEVABLE"
    NOT_COMPARABLE = "NOT_COMPARABLE"


class DealType(str, Enum):
    FLAT_GUARANTEE = "FLAT_GUARANTEE"
    GUARANTEE_VS_PERCENTAGE = "GUARANTEE_VS_PERCENTAGE"
    PERCENTAGE_OF_DEFINED_BASE = "PERCENTAGE_OF_DEFINED_BASE"


class BackendBasis(str, Enum):
    GROSS_BOX_OFFICE = "GROSS_BOX_OFFICE"
    ADJUSTED_GROSS = "ADJUSTED_GROSS"
    NET_AFTER_APPROVED_EXPENSES = "NET_AFTER_APPROVED_EXPENSES"


class SensitivityField(str, Enum):
    SELL_THROUGH = "sell_through"
    AVERAGE_TICKET_PRICE = "average_ticket_price"
    ARTIST_GUARANTEE = "artist_guarantee"
    SELLABLE_CAPACITY = "sellable_capacity"
    MARKETING_COST = "marketing_cost"
    PRODUCTION_COST = "production_cost"


@dataclass(frozen=True)
class TypedInput:
    value: Decimal | int | str | Enum | tuple[str, ...] | None
    provenance: Provenance
    evidence_ref: str | None = None
    as_of: str | None = None
    entered_by: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.value, float):
            raise TypeError("binary floating-point inputs are forbidden; use Decimal")
        if self.provenance == Provenance.UNKNOWN and self.value is not None:
            raise ValueError("UNKNOWN input must not carry a value")
        if self.provenance != Provenance.UNKNOWN and self.value is None:
            raise ValueError("known input provenance requires a value")

    @classmethod
    def unknown(cls, evidence_ref: str | None = None) -> "TypedInput":
        return cls(None, Provenance.UNKNOWN, evidence_ref)

    @classmethod
    def assumption(cls, value: Decimal | int | str | Enum | tuple[str, ...]) -> "TypedInput":
        return cls(value, Provenance.USER_ASSUMPTION)


@dataclass(frozen=True)
class TicketTier:
    name: str
    price: TypedInput
    quantity: TypedInput


@dataclass(frozen=True)
class FixedCosts:
    marketing: TypedInput
    production: TypedInput
    venue: TypedInput
    labor: TypedInput
    insurance: TypedInput
    other: TypedInput


@dataclass(frozen=True)
class DealDefinition:
    deal_type: TypedInput
    guarantee: TypedInput
    backend_percentage: TypedInput
    backend_basis: TypedInput
    artist_expenses: TypedInput
    approved_expense_names: TypedInput


@dataclass(frozen=True)
class ShowEconomicsScenario:
    currency: TypedInput
    usable_capacity: TypedInput
    sellable_capacity: TypedInput
    ticket_scale: tuple[TicketTier, ...]
    sell_through: TypedInput
    ticketing_deduction_per_paid_ticket: TypedInput
    tax_rate_on_gross: TypedInput
    deal: DealDefinition
    costs: FixedCosts
    ancillary_revenue: TypedInput
    sponsorship_allocation: TypedInput

    def input_ledger(self) -> dict[str, TypedInput]:
        ledger: dict[str, TypedInput] = {
            "currency": self.currency,
            "usable_capacity": self.usable_capacity,
            "sellable_capacity": self.sellable_capacity,
            "sell_through": self.sell_through,
            "ticketing_deduction_per_paid_ticket": self.ticketing_deduction_per_paid_ticket,
            "tax_rate_on_gross": self.tax_rate_on_gross,
            "deal.deal_type": self.deal.deal_type,
            "deal.guarantee": self.deal.guarantee,
            "deal.backend_percentage": self.deal.backend_percentage,
            "deal.backend_basis": self.deal.backend_basis,
            "deal.artist_expenses": self.deal.artist_expenses,
            "deal.approved_expense_names": self.deal.approved_expense_names,
            "ancillary_revenue": self.ancillary_revenue,
            "sponsorship_allocation": self.sponsorship_allocation,
        }
        for field in fields(self.costs):
            ledger[f"costs.{field.name}"] = getattr(self.costs, field.name)
        for index, tier in enumerate(self.ticket_scale):
            ledger[f"ticket_scale.{index}.price"] = tier.price
            ledger[f"ticket_scale.{index}.quantity"] = tier.quantity
        return ledger


@dataclass(frozen=True)
class OutputValue:
    value: Decimal | int | None
    status: OutputStatus
    provenance: Provenance
    lineage: tuple[str, ...]
    currency: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class Evaluation:
    engine_version: str
    currency: str | None
    outputs: dict[str, OutputValue]

    def __getitem__(self, name: str) -> OutputValue:
        return self.outputs[name]


@dataclass(frozen=True)
class BoundaryPoint:
    average_ticket_price: Decimal
    sellable_capacity: int
    sell_through: Decimal
    paid_tickets: int
    promoter_contribution: OutputValue
    promoter_margin: OutputValue
    meets_hurdle: bool | None


@dataclass(frozen=True)
class SensitivityPoint:
    field: SensitivityField
    input_value: TypedInput
    promoter_contribution: OutputValue
    promoter_margin: OutputValue


@dataclass(frozen=True)
class _Calc:
    value: Decimal | int | None
    status: OutputStatus
    lineage: tuple[str, ...]
    reason: str | None = None


def _lineage(*calcs: _Calc) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item for calc in calcs for item in calc.lineage))


def _unknown(*names: str, reason: str | None = None) -> _Calc:
    return _Calc(None, OutputStatus.UNKNOWN, tuple(names), reason or "required input is UNKNOWN")


def _not_achievable(lineage: tuple[str, ...], reason: str) -> _Calc:
    return _Calc(None, OutputStatus.NOT_ACHIEVABLE, lineage, reason)


def _decimal_input(item: TypedInput, name: str) -> _Calc:
    if item.provenance == Provenance.UNKNOWN:
        return _unknown(name)
    if isinstance(item.value, bool) or not isinstance(item.value, (Decimal, int)):
        raise TypeError(f"{name} must be Decimal or int")
    return _Calc(Decimal(item.value), OutputStatus.KNOWN, (name,))


def _int_input(item: TypedInput, name: str) -> _Calc:
    calc = _decimal_input(item, name)
    if calc.status != OutputStatus.KNOWN:
        return calc
    assert isinstance(calc.value, Decimal)
    if calc.value != calc.value.to_integral_value():
        raise ValueError(f"{name} must be a whole number")
    return _Calc(int(calc.value), OutputStatus.KNOWN, calc.lineage)


def _enum_input(item: TypedInput, name: str, enum_type: type[Enum]) -> Enum | None:
    if item.provenance == Provenance.UNKNOWN:
        return None
    try:
        return enum_type(item.value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"invalid {name}: {item.value!r}") from exc


def _approved_expense_names(item: TypedInput) -> tuple[str, ...] | None:
    if item.provenance == Provenance.UNKNOWN:
        return None
    if not isinstance(item.value, tuple) or not all(
        isinstance(name, str) and name for name in item.value
    ):
        raise ValueError("approved_expense_names must be an explicit tuple of field names")
    return item.value


def _currency(scenario: ShowEconomicsScenario) -> str | None:
    if scenario.currency.provenance == Provenance.UNKNOWN:
        return None
    if not isinstance(scenario.currency.value, str) or not re.fullmatch(
        r"[A-Z]{3}", scenario.currency.value
    ):
        raise ValueError("currency must be an explicit uppercase ISO-style code")
    return scenario.currency.value


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _rate(value: Decimal) -> Decimal:
    return value.quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)


def _sum(calcs: Iterable[_Calc], *, money: bool = False) -> _Calc:
    items = tuple(calcs)
    unknown = next((item for item in items if item.status != OutputStatus.KNOWN), None)
    if unknown:
        return _Calc(None, unknown.status, _lineage(*items), unknown.reason)
    total = sum((Decimal(item.value) for item in items), Decimal("0"))
    return _Calc(_money(total) if money else total, OutputStatus.KNOWN, _lineage(*items))


def _output(calc: _Calc, *, currency: str | None = None) -> OutputValue:
    provenance = Provenance.DERIVED if calc.status == OutputStatus.KNOWN else Provenance.UNKNOWN
    return OutputValue(calc.value, calc.status, provenance, calc.lineage, currency, calc.reason)


def _money_output(calc: _Calc, currency: str | None) -> OutputValue:
    if calc.status == OutputStatus.KNOWN and isinstance(calc.value, Decimal):
        calc = replace(calc, value=_money(calc.value))
    return _output(calc, currency=currency)


def validate_scenario(scenario: ShowEconomicsScenario) -> None:
    """Validate only known inputs; UNKNOWN values remain valid and propagate."""
    _currency(scenario)
    usable = _int_input(scenario.usable_capacity, "usable_capacity")
    sellable = _int_input(scenario.sellable_capacity, "sellable_capacity")
    if usable.status == OutputStatus.KNOWN and usable.value < 0:
        raise ValueError("usable_capacity must be nonnegative")
    if sellable.status == OutputStatus.KNOWN and sellable.value < 0:
        raise ValueError("sellable_capacity must be nonnegative")
    if (
        usable.status == sellable.status == OutputStatus.KNOWN
        and sellable.value > usable.value
    ):
        raise ValueError("sellable_capacity cannot exceed usable_capacity")

    tier_quantity = 0
    all_quantities_known = True
    if not scenario.ticket_scale:
        raise ValueError("ticket_scale must contain at least one tier")
    for index, tier in enumerate(scenario.ticket_scale):
        if not tier.name.strip():
            raise ValueError("ticket tier name must not be blank")
        price = _decimal_input(tier.price, f"ticket_scale.{index}.price")
        quantity = _int_input(tier.quantity, f"ticket_scale.{index}.quantity")
        if price.status == OutputStatus.KNOWN and price.value < 0:
            raise ValueError("ticket price must be nonnegative")
        if quantity.status == OutputStatus.KNOWN:
            if quantity.value < 0:
                raise ValueError("ticket quantity must be nonnegative")
            tier_quantity += int(quantity.value)
        else:
            all_quantities_known = False
    if (
        all_quantities_known
        and sellable.status == OutputStatus.KNOWN
        and tier_quantity != sellable.value
    ):
        raise ValueError("ticket tier quantities must equal sellable_capacity")

    for name, item in (
        ("sell_through", scenario.sell_through),
        ("tax_rate_on_gross", scenario.tax_rate_on_gross),
        ("deal.backend_percentage", scenario.deal.backend_percentage),
    ):
        value = _decimal_input(item, name)
        if value.status == OutputStatus.KNOWN and not Decimal("0") <= value.value <= Decimal("1"):
            raise ValueError(f"{name} must be between 0 and 1")
    for name, item in scenario.input_ledger().items():
        if name in {"currency", "deal.deal_type", "deal.backend_basis",
                    "deal.approved_expense_names", "sell_through",
                    "tax_rate_on_gross", "deal.backend_percentage"} or ".quantity" in name:
            continue
        value = _decimal_input(item, name)
        if value.status == OutputStatus.KNOWN and value.value < 0:
            raise ValueError(f"{name} must be nonnegative")

    deal_type = _enum_input(scenario.deal.deal_type, "deal_type", DealType)
    basis = _enum_input(scenario.deal.backend_basis, "backend_basis", BackendBasis)
    _approved_expense_names(scenario.deal.approved_expense_names)
    if deal_type == DealType.FLAT_GUARANTEE:
        if scenario.deal.guarantee.provenance == Provenance.UNKNOWN:
            return
    elif deal_type in (DealType.GUARANTEE_VS_PERCENTAGE, DealType.PERCENTAGE_OF_DEFINED_BASE):
        if basis is None:
            raise ValueError("percentage deal requires an explicit backend basis")
        if scenario.deal.backend_percentage.provenance == Provenance.UNKNOWN:
            raise ValueError("percentage deal requires an explicit backend percentage")
        if basis == BackendBasis.NET_AFTER_APPROVED_EXPENSES:
            approved_names = _approved_expense_names(scenario.deal.approved_expense_names)
            if approved_names is None:
                raise ValueError("net-after-expenses basis requires explicit approved expenses")
            valid = {field.name for field in fields(FixedCosts)}
            unknown_names = set(approved_names) - valid
            if unknown_names:
                raise ValueError(f"undefined approved expenses: {sorted(unknown_names)}")
    elif deal_type is None:
        return


def _ticket_scale(scenario: ShowEconomicsScenario) -> tuple[_Calc, _Calc]:
    gross_parts: list[_Calc] = []
    quantity_parts: list[_Calc] = []
    for index, tier in enumerate(scenario.ticket_scale):
        price = _decimal_input(tier.price, f"ticket_scale.{index}.price")
        quantity = _int_input(tier.quantity, f"ticket_scale.{index}.quantity")
        quantity_parts.append(quantity)
        if price.status != OutputStatus.KNOWN or quantity.status != OutputStatus.KNOWN:
            gross_parts.append(_Calc(None, OutputStatus.UNKNOWN, _lineage(price, quantity)))
        else:
            gross_parts.append(_Calc(
                _money(Decimal(price.value) * Decimal(quantity.value)),
                OutputStatus.KNOWN,
                _lineage(price, quantity),
            ))
    gross = _sum(gross_parts, money=True)
    quantity = _sum(quantity_parts)
    return gross, quantity


def _fixed_cost_calcs(scenario: ShowEconomicsScenario) -> dict[str, _Calc]:
    return {
        field.name: _decimal_input(getattr(scenario.costs, field.name), f"costs.{field.name}")
        for field in fields(scenario.costs)
    }


def _operating_outputs(
    scenario: ShowEconomicsScenario,
    *,
    paid_tickets_override: int | None = None,
    average_ticket_price_override: Decimal | None = None,
) -> dict[str, _Calc]:
    gross_potential, tier_quantity = _ticket_scale(scenario)
    if tier_quantity.status != OutputStatus.KNOWN:
        weighted_atp = _Calc(None, OutputStatus.UNKNOWN, _lineage(gross_potential, tier_quantity))
    elif tier_quantity.value == 0:
        weighted_atp = _Calc(None, OutputStatus.UNKNOWN, _lineage(gross_potential, tier_quantity),
                             "weighted average ticket price is undefined for zero tickets")
    elif gross_potential.status != OutputStatus.KNOWN:
        weighted_atp = _Calc(None, OutputStatus.UNKNOWN, _lineage(gross_potential, tier_quantity))
    else:
        with localcontext() as ctx:
            ctx.prec = 40
            weighted_atp = _Calc(
                Decimal(gross_potential.value) / Decimal(tier_quantity.value),
                OutputStatus.KNOWN,
                _lineage(gross_potential, tier_quantity),
            )

    sellable = _int_input(scenario.sellable_capacity, "sellable_capacity")
    sell_through = _decimal_input(scenario.sell_through, "sell_through")
    if paid_tickets_override is not None:
        paid = _Calc(paid_tickets_override, OutputStatus.KNOWN, ("boundary.paid_tickets",))
    elif sellable.status != OutputStatus.KNOWN or sell_through.status != OutputStatus.KNOWN:
        paid = _Calc(None, OutputStatus.UNKNOWN, _lineage(sellable, sell_through))
    else:
        paid = _Calc(
            int((Decimal(sellable.value) * Decimal(sell_through.value)).to_integral_value(
                rounding=ROUND_FLOOR
            )),
            OutputStatus.KNOWN,
            _lineage(sellable, sell_through),
        )

    atp = weighted_atp
    if average_ticket_price_override is not None:
        atp = _Calc(average_ticket_price_override, OutputStatus.KNOWN,
                    ("boundary.average_ticket_price",))
    if paid.status == OutputStatus.KNOWN and paid.value == 0:
        gross_ticket_revenue = _Calc(
            Decimal("0.00"), OutputStatus.KNOWN, paid.lineage
        )
    elif atp.status != OutputStatus.KNOWN or paid.status != OutputStatus.KNOWN:
        gross_ticket_revenue = _Calc(None, OutputStatus.UNKNOWN, _lineage(atp, paid))
    else:
        gross_ticket_revenue = _Calc(
            _money(Decimal(atp.value) * Decimal(paid.value)),
            OutputStatus.KNOWN,
            _lineage(atp, paid),
        )

    tax_rate = _decimal_input(scenario.tax_rate_on_gross, "tax_rate_on_gross")
    if gross_ticket_revenue.status != OutputStatus.KNOWN or tax_rate.status != OutputStatus.KNOWN:
        taxes = _Calc(None, OutputStatus.UNKNOWN, _lineage(gross_ticket_revenue, tax_rate))
    else:
        taxes = _Calc(_money(Decimal(gross_ticket_revenue.value) * Decimal(tax_rate.value)),
                      OutputStatus.KNOWN, _lineage(gross_ticket_revenue, tax_rate))

    ticket_fee = _decimal_input(
        scenario.ticketing_deduction_per_paid_ticket,
        "ticketing_deduction_per_paid_ticket",
    )
    if ticket_fee.status != OutputStatus.KNOWN or paid.status != OutputStatus.KNOWN:
        ticketing = _Calc(None, OutputStatus.UNKNOWN, _lineage(ticket_fee, paid))
    else:
        ticketing = _Calc(_money(Decimal(ticket_fee.value) * Decimal(paid.value)),
                          OutputStatus.KNOWN, _lineage(ticket_fee, paid))

    if any(item.status != OutputStatus.KNOWN for item in (gross_ticket_revenue, taxes, ticketing)):
        adjusted_gross = _Calc(None, OutputStatus.UNKNOWN,
                               _lineage(gross_ticket_revenue, taxes, ticketing))
    else:
        adjusted_gross = _Calc(
            _money(Decimal(gross_ticket_revenue.value) - Decimal(taxes.value) - Decimal(ticketing.value)),
            OutputStatus.KNOWN,
            _lineage(gross_ticket_revenue, taxes, ticketing),
        )

    fixed_parts = _fixed_cost_calcs(scenario)
    total_fixed = _sum(fixed_parts.values(), money=True)
    deal_type = _enum_input(scenario.deal.deal_type, "deal_type", DealType)
    basis_type = _enum_input(scenario.deal.backend_basis, "backend_basis", BackendBasis)
    if deal_type is None:
        artist_settlement = _unknown("deal.deal_type")
    else:
        if basis_type == BackendBasis.GROSS_BOX_OFFICE:
            backend_base = gross_ticket_revenue
        elif basis_type == BackendBasis.ADJUSTED_GROSS:
            backend_base = adjusted_gross
        elif basis_type == BackendBasis.NET_AFTER_APPROVED_EXPENSES:
            approved_names = _approved_expense_names(scenario.deal.approved_expense_names)
            if approved_names is None:
                backend_base = _unknown("deal.approved_expense_names")
            else:
                approved = _sum(
                    (fixed_parts[name] for name in approved_names), money=True
                )
                if adjusted_gross.status != OutputStatus.KNOWN or approved.status != OutputStatus.KNOWN:
                    backend_base = _Calc(None, OutputStatus.UNKNOWN,
                                         _lineage(adjusted_gross, approved))
                else:
                    backend_base = _Calc(
                        _money(Decimal(adjusted_gross.value) - Decimal(approved.value)),
                        OutputStatus.KNOWN,
                        _lineage(adjusted_gross, approved),
                    )
        else:
            backend_base = _unknown("deal.backend_basis")

        guarantee = _decimal_input(scenario.deal.guarantee, "deal.guarantee")
        percentage = _decimal_input(scenario.deal.backend_percentage, "deal.backend_percentage")
        artist_expenses = _decimal_input(scenario.deal.artist_expenses, "deal.artist_expenses")
        if deal_type == DealType.FLAT_GUARANTEE:
            base_settlement = guarantee
        elif backend_base.status != OutputStatus.KNOWN or percentage.status != OutputStatus.KNOWN:
            base_settlement = _Calc(None, OutputStatus.UNKNOWN, _lineage(backend_base, percentage))
        else:
            percentage_value = _money(Decimal(backend_base.value) * Decimal(percentage.value))
            percentage_calc = _Calc(percentage_value, OutputStatus.KNOWN,
                                    _lineage(backend_base, percentage))
            if deal_type == DealType.PERCENTAGE_OF_DEFINED_BASE:
                base_settlement = percentage_calc
            elif guarantee.status != OutputStatus.KNOWN:
                base_settlement = guarantee
            else:
                base_settlement = _Calc(
                    max(Decimal(guarantee.value), percentage_value),
                    OutputStatus.KNOWN,
                    _lineage(guarantee, percentage_calc),
                )
        artist_settlement = _sum((base_settlement, artist_expenses), money=True)

    total_variable = _sum((taxes, ticketing, artist_settlement), money=True)
    total_event_costs = _sum((total_fixed, total_variable), money=True)
    ancillary = _decimal_input(scenario.ancillary_revenue, "ancillary_revenue")
    sponsorship = _decimal_input(scenario.sponsorship_allocation, "sponsorship_allocation")
    promoter_revenue = _sum((gross_ticket_revenue, ancillary, sponsorship), money=True)
    if promoter_revenue.status != OutputStatus.KNOWN or total_event_costs.status != OutputStatus.KNOWN:
        contribution = _Calc(None, OutputStatus.UNKNOWN,
                             _lineage(promoter_revenue, total_event_costs))
    else:
        contribution = _Calc(
            _money(Decimal(promoter_revenue.value) - Decimal(total_event_costs.value)),
            OutputStatus.KNOWN,
            _lineage(promoter_revenue, total_event_costs),
        )
    if contribution.status != OutputStatus.KNOWN or promoter_revenue.status != OutputStatus.KNOWN:
        margin = _Calc(None, OutputStatus.UNKNOWN, _lineage(contribution, promoter_revenue))
    elif promoter_revenue.value == 0:
        margin = _Calc(None, OutputStatus.UNKNOWN, _lineage(contribution, promoter_revenue),
                       "promoter margin is undefined when promoter revenue is zero")
    else:
        margin = _Calc(_rate(Decimal(contribution.value) / Decimal(promoter_revenue.value)),
                       OutputStatus.KNOWN, _lineage(contribution, promoter_revenue))

    outputs = {
        "gross_potential": gross_potential,
        "weighted_average_ticket_price": weighted_atp,
        "paid_tickets": paid,
        "gross_ticket_revenue": gross_ticket_revenue,
        "taxes": taxes,
        "ticketing_deductions": ticketing,
        "adjusted_gross": adjusted_gross,
        "artist_settlement": artist_settlement,
        "total_fixed_costs": total_fixed,
        "total_variable_costs": total_variable,
        "total_event_costs": total_event_costs,
        "promoter_revenue": promoter_revenue,
        "promoter_contribution": contribution,
        "promoter_margin": margin,
    }
    if _currency(scenario) is None:
        for name in outputs:
            if name != "paid_tickets":
                outputs[name] = _unknown("currency", reason="currency is UNKNOWN")
    return outputs


def _break_even_tickets(scenario: ShowEconomicsScenario) -> _Calc:
    sellable = _int_input(scenario.sellable_capacity, "sellable_capacity")
    if sellable.status != OutputStatus.KNOWN:
        return sellable
    low_result = _operating_outputs(scenario, paid_tickets_override=0)["promoter_contribution"]
    high_result = _operating_outputs(
        scenario, paid_tickets_override=int(sellable.value)
    )["promoter_contribution"]
    if low_result.status != OutputStatus.KNOWN or high_result.status != OutputStatus.KNOWN:
        return _Calc(None, OutputStatus.UNKNOWN, _lineage(low_result, high_result))
    if low_result.value >= 0:
        return _Calc(0, OutputStatus.KNOWN, _lineage(low_result, sellable))
    if high_result.value < 0:
        return _not_achievable(
            _lineage(high_result, sellable),
            "break-even exceeds explicit sellable capacity",
        )
    low, high = 0, int(sellable.value)
    while low < high:
        mid = (low + high) // 2
        value = _operating_outputs(scenario, paid_tickets_override=mid)["promoter_contribution"]
        assert value.status == OutputStatus.KNOWN
        if value.value >= 0:
            high = mid
        else:
            low = mid + 1
    return _Calc(low, OutputStatus.KNOWN, _lineage(low_result, high_result, sellable))


def _break_even_average_ticket_price(scenario: ShowEconomicsScenario, paid_tickets: _Calc) -> _Calc:
    if paid_tickets.status != OutputStatus.KNOWN:
        return paid_tickets
    at_zero = _operating_outputs(
        scenario, paid_tickets_override=int(paid_tickets.value),
        average_ticket_price_override=Decimal("0"),
    )["promoter_contribution"]
    if at_zero.status != OutputStatus.KNOWN:
        return at_zero
    if at_zero.value >= 0:
        return _Calc(Decimal("0.00"), OutputStatus.KNOWN, at_zero.lineage)
    if paid_tickets.value == 0:
        return _not_achievable(at_zero.lineage, "ticket price cannot recover costs at zero paid tickets")
    high_cents = 1
    cap_cents = 100_000_000
    while high_cents <= cap_cents:
        result = _operating_outputs(
            scenario,
            paid_tickets_override=int(paid_tickets.value),
            average_ticket_price_override=Decimal(high_cents) / Decimal(100),
        )["promoter_contribution"]
        if result.status != OutputStatus.KNOWN:
            return result
        if result.value >= 0:
            break
        high_cents *= 2
    else:
        return _not_achievable(at_zero.lineage, "break-even price exceeds engine search boundary")
    low_cents = 0
    while low_cents < high_cents:
        mid = (low_cents + high_cents) // 2
        result = _operating_outputs(
            scenario,
            paid_tickets_override=int(paid_tickets.value),
            average_ticket_price_override=Decimal(mid) / Decimal(100),
        )["promoter_contribution"]
        assert result.status == OutputStatus.KNOWN
        if result.value >= 0:
            high_cents = mid
        else:
            low_cents = mid + 1
    return _Calc(Decimal(low_cents) / Decimal(100), OutputStatus.KNOWN,
                 ("boundary.average_ticket_price",) + at_zero.lineage)


def evaluate(scenario: ShowEconomicsScenario) -> Evaluation:
    """Evaluate operating economics plus deterministic inverse boundaries."""
    validate_scenario(scenario)
    currency = _currency(scenario)
    operating = _operating_outputs(scenario)
    break_even_tickets = _break_even_tickets(scenario)
    sellable = _int_input(scenario.sellable_capacity, "sellable_capacity")
    if break_even_tickets.status != OutputStatus.KNOWN:
        break_even_sell_through = _Calc(None, break_even_tickets.status,
                                        break_even_tickets.lineage, break_even_tickets.reason)
    elif sellable.status != OutputStatus.KNOWN:
        break_even_sell_through = sellable
    elif sellable.value == 0:
        break_even_sell_through = (
            _Calc(Decimal("0"), OutputStatus.KNOWN, _lineage(break_even_tickets, sellable))
            if break_even_tickets.value == 0
            else _not_achievable(_lineage(break_even_tickets, sellable),
                                 "positive break-even tickets with zero capacity")
        )
    else:
        break_even_sell_through = _Calc(
            _rate(Decimal(break_even_tickets.value) / Decimal(sellable.value)),
            OutputStatus.KNOWN,
            _lineage(break_even_tickets, sellable),
        )

    paid = operating["paid_tickets"]
    if paid.status == break_even_tickets.status == OutputStatus.KNOWN:
        margin_of_safety = _Calc(int(paid.value) - int(break_even_tickets.value),
                                 OutputStatus.KNOWN, _lineage(paid, break_even_tickets))
    else:
        margin_of_safety = _Calc(None, OutputStatus.UNKNOWN, _lineage(paid, break_even_tickets))

    sell_through = _decimal_input(scenario.sell_through, "sell_through")
    usable = _int_input(scenario.usable_capacity, "usable_capacity")
    if break_even_tickets.status != OutputStatus.KNOWN:
        break_even_capacity = _Calc(None, break_even_tickets.status,
                                    break_even_tickets.lineage, break_even_tickets.reason)
    elif sell_through.status != OutputStatus.KNOWN:
        break_even_capacity = sell_through
    elif sell_through.value == 0:
        break_even_capacity = (
            _Calc(0, OutputStatus.KNOWN, _lineage(break_even_tickets, sell_through))
            if break_even_tickets.value == 0
            else _not_achievable(_lineage(break_even_tickets, sell_through),
                                 "capacity cannot produce paid tickets at zero sell-through")
        )
    else:
        capacity = int((Decimal(break_even_tickets.value) / Decimal(sell_through.value))
                       .to_integral_value(rounding=ROUND_CEILING))
        if usable.status == OutputStatus.KNOWN and capacity > usable.value:
            break_even_capacity = _not_achievable(
                _lineage(break_even_tickets, sell_through, usable),
                "break-even capacity exceeds explicit usable capacity",
            )
        else:
            break_even_capacity = _Calc(capacity, OutputStatus.KNOWN,
                                         _lineage(break_even_tickets, sell_through))

    contribution = operating["promoter_contribution"]
    if contribution.status == OutputStatus.KNOWN:
        additional_cost = _Calc(_money(max(Decimal("0"), Decimal(contribution.value))),
                                OutputStatus.KNOWN, contribution.lineage)
    else:
        additional_cost = contribution

    fixed = operating["total_fixed_costs"]
    revenue = operating["promoter_revenue"]
    taxes = operating["taxes"]
    ticketing = operating["ticketing_deductions"]
    if any(item.status != OutputStatus.KNOWN for item in (fixed, revenue, taxes, ticketing)):
        max_artist = _Calc(None, OutputStatus.UNKNOWN, _lineage(fixed, revenue, taxes, ticketing))
    else:
        amount = _money(Decimal(revenue.value) - Decimal(fixed.value)
                        - Decimal(taxes.value) - Decimal(ticketing.value))
        max_artist = (
            _Calc(amount, OutputStatus.KNOWN, _lineage(fixed, revenue, taxes, ticketing))
            if amount >= 0
            else _not_achievable(_lineage(fixed, revenue, taxes, ticketing),
                                 "non-artist costs already exceed promoter revenue")
        )
    artist_expenses = _decimal_input(scenario.deal.artist_expenses, "deal.artist_expenses")
    if max_artist.status != OutputStatus.KNOWN or artist_expenses.status != OutputStatus.KNOWN:
        max_flat_guarantee = _Calc(None, max_artist.status if max_artist.status != OutputStatus.KNOWN
                                  else OutputStatus.UNKNOWN,
                                  _lineage(max_artist, artist_expenses), max_artist.reason)
    else:
        value = _money(Decimal(max_artist.value) - Decimal(artist_expenses.value))
        max_flat_guarantee = (
            _Calc(value, OutputStatus.KNOWN, _lineage(max_artist, artist_expenses))
            if value >= 0
            else _not_achievable(_lineage(max_artist, artist_expenses),
                                 "artist expenses exceed available artist settlement")
        )

    inverse = {
        "break_even_paid_tickets": break_even_tickets,
        "break_even_sell_through": break_even_sell_through,
        "break_even_average_ticket_price": _break_even_average_ticket_price(scenario, paid),
        "break_even_sellable_capacity": break_even_capacity,
        "margin_of_safety_tickets": margin_of_safety,
        "additional_cost_capacity": additional_cost,
        "maximum_artist_settlement_at_break_even": max_artist,
        "maximum_flat_guarantee_at_break_even": max_flat_guarantee,
    }
    money_names = {
        "gross_potential", "weighted_average_ticket_price", "gross_ticket_revenue",
        "taxes", "ticketing_deductions", "adjusted_gross", "artist_settlement",
        "total_fixed_costs", "total_variable_costs", "total_event_costs",
        "promoter_revenue", "promoter_contribution", "additional_cost_capacity",
        "maximum_artist_settlement_at_break_even", "maximum_flat_guarantee_at_break_even",
        "break_even_average_ticket_price",
    }
    outputs = {
        name: (_money_output(calc, currency) if name in money_names else _output(calc))
        for name, calc in {**operating, **inverse}.items()
    }
    return Evaluation(ENGINE_VERSION, currency, outputs)


def boundary_grid(
    scenario: ShowEconomicsScenario,
    *,
    average_ticket_prices: Iterable[Decimal],
    sellable_capacities: Iterable[int],
    sell_throughs: Iterable[Decimal],
    minimum_contribution: Decimal = Decimal("0"),
    minimum_margin: Decimal | None = None,
) -> list[BoundaryPoint]:
    """Evaluate explicit price x capacity x sell-through equation points."""
    validate_scenario(scenario)
    if isinstance(minimum_contribution, float) or isinstance(minimum_margin, float):
        raise TypeError("boundary hurdles must use Decimal")
    usable = _int_input(scenario.usable_capacity, "usable_capacity")
    points: list[BoundaryPoint] = []
    for price in average_ticket_prices:
        if isinstance(price, float) or price < 0:
            raise ValueError("average ticket prices must be nonnegative Decimal values")
        for capacity in sellable_capacities:
            if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 0:
                raise ValueError("sellable capacities must be nonnegative integers")
            if usable.status == OutputStatus.KNOWN and capacity > usable.value:
                raise ValueError("boundary sellable capacity exceeds usable capacity")
            for sell_through in sell_throughs:
                if isinstance(sell_through, float) or not Decimal("0") <= sell_through <= Decimal("1"):
                    raise ValueError("boundary sell-through must be a Decimal from 0 to 1")
                paid = int((Decimal(capacity) * sell_through).to_integral_value(rounding=ROUND_FLOOR))
                outputs = _operating_outputs(
                    scenario,
                    paid_tickets_override=paid,
                    average_ticket_price_override=price,
                )
                contribution = _money_output(outputs["promoter_contribution"], _currency(scenario))
                margin = _output(outputs["promoter_margin"])
                if contribution.status != OutputStatus.KNOWN or (
                    minimum_margin is not None and margin.status != OutputStatus.KNOWN
                ):
                    meets: bool | None = None
                else:
                    meets = Decimal(contribution.value) >= minimum_contribution
                    if minimum_margin is not None:
                        meets = meets and Decimal(margin.value) >= minimum_margin
                points.append(BoundaryPoint(price, capacity, sell_through, paid,
                                            contribution, margin, meets))
    return points


def sensitivity(
    scenario: ShowEconomicsScenario,
    field: SensitivityField,
    values: Iterable[Decimal | int],
) -> list[SensitivityPoint]:
    """Hold all other inputs fixed and vary one explicit controllable input."""
    validate_scenario(scenario)
    points: list[SensitivityPoint] = []
    for value in values:
        if isinstance(value, float):
            raise TypeError("sensitivity values must use Decimal or int")
        typed = TypedInput.assumption(value)
        candidate = scenario
        paid_override: int | None = None
        price_override: Decimal | None = None
        if field == SensitivityField.SELL_THROUGH:
            rate = Decimal(value)
            if not Decimal("0") <= rate <= Decimal("1"):
                raise ValueError("sell-through sensitivity must be between 0 and 1")
            candidate = replace(scenario, sell_through=typed)
            sellable = _int_input(scenario.sellable_capacity, "sellable_capacity")
            if sellable.status == OutputStatus.KNOWN:
                paid_override = int((Decimal(sellable.value) * rate).to_integral_value(
                    rounding=ROUND_FLOOR
                ))
        elif field == SensitivityField.AVERAGE_TICKET_PRICE:
            price_override = Decimal(value)
        elif field == SensitivityField.ARTIST_GUARANTEE:
            candidate = replace(scenario, deal=replace(scenario.deal, guarantee=typed))
        elif field == SensitivityField.SELLABLE_CAPACITY:
            capacity = int(value)
            if Decimal(value) != Decimal(capacity) or capacity < 0:
                raise ValueError("capacity sensitivity values must be nonnegative integers")
            usable = _int_input(scenario.usable_capacity, "usable_capacity")
            if usable.status == OutputStatus.KNOWN and capacity > usable.value:
                raise ValueError("capacity sensitivity exceeds usable capacity")
            sell_through = _decimal_input(scenario.sell_through, "sell_through")
            if sell_through.status == OutputStatus.KNOWN:
                paid_override = int((Decimal(capacity) * Decimal(sell_through.value))
                                    .to_integral_value(rounding=ROUND_FLOOR))
        elif field in (SensitivityField.MARKETING_COST, SensitivityField.PRODUCTION_COST):
            cost_name = "marketing" if field == SensitivityField.MARKETING_COST else "production"
            candidate = replace(scenario, costs=replace(scenario.costs, **{cost_name: typed}))
        else:  # pragma: no cover - exhaustive Enum guard
            raise ValueError(f"unsupported sensitivity field {field}")
        if Decimal(value) < 0:
            raise ValueError("sensitivity values must be nonnegative")
        outputs = _operating_outputs(
            candidate,
            paid_tickets_override=paid_override,
            average_ticket_price_override=price_override,
        )
        points.append(SensitivityPoint(
            field,
            typed,
            _money_output(outputs["promoter_contribution"], _currency(scenario)),
            _output(outputs["promoter_margin"]),
        ))
    return points


def input_to_dict(item: TypedInput) -> dict[str, Any]:
    value = item.value.value if isinstance(item.value, Enum) else item.value
    return {
        "value": str(value) if isinstance(value, Decimal) else value,
        "provenance": item.provenance.value,
        "evidence_ref": item.evidence_ref,
        "as_of": item.as_of,
        "entered_by": item.entered_by,
    }


def _input_from_dict(
    data: dict[str, Any],
    value_type: type[Decimal] | type[int] | type[str] | type[tuple] | type[Enum],
) -> TypedInput:
    provenance = Provenance(data["provenance"])
    if provenance == Provenance.UNKNOWN:
        return TypedInput(
            None,
            Provenance.UNKNOWN,
            data.get("evidence_ref"),
            data.get("as_of"),
            data.get("entered_by"),
        )
    raw = data.get("value")
    if value_type is Decimal:
        value: Decimal | int | str | Enum = Decimal(str(raw))
    elif value_type is int:
        value = int(raw)
    elif value_type is str:
        value = str(raw)
    elif value_type is tuple:
        value = tuple(raw)
    else:
        value = value_type(raw)
    return TypedInput(
        value,
        provenance,
        data.get("evidence_ref"),
        data.get("as_of"),
        data.get("entered_by"),
    )


def scenario_to_dict(scenario: ShowEconomicsScenario) -> dict[str, Any]:
    return {
        "currency": input_to_dict(scenario.currency),
        "usable_capacity": input_to_dict(scenario.usable_capacity),
        "sellable_capacity": input_to_dict(scenario.sellable_capacity),
        "ticket_scale": [
            {"name": tier.name, "price": input_to_dict(tier.price),
             "quantity": input_to_dict(tier.quantity)}
            for tier in scenario.ticket_scale
        ],
        "sell_through": input_to_dict(scenario.sell_through),
        "ticketing_deduction_per_paid_ticket": input_to_dict(
            scenario.ticketing_deduction_per_paid_ticket
        ),
        "tax_rate_on_gross": input_to_dict(scenario.tax_rate_on_gross),
        "deal": {
            "deal_type": input_to_dict(scenario.deal.deal_type),
            "guarantee": input_to_dict(scenario.deal.guarantee),
            "backend_percentage": input_to_dict(scenario.deal.backend_percentage),
            "backend_basis": input_to_dict(scenario.deal.backend_basis),
            "artist_expenses": input_to_dict(scenario.deal.artist_expenses),
            "approved_expense_names": input_to_dict(scenario.deal.approved_expense_names),
        },
        "costs": {
            field.name: input_to_dict(getattr(scenario.costs, field.name))
            for field in fields(scenario.costs)
        },
        "ancillary_revenue": input_to_dict(scenario.ancillary_revenue),
        "sponsorship_allocation": input_to_dict(scenario.sponsorship_allocation),
    }


def scenario_from_dict(data: dict[str, Any]) -> ShowEconomicsScenario:
    """Reconstruct a scenario from the lossless workspace JSON contract."""
    deal = data["deal"]
    costs = data["costs"]
    return ShowEconomicsScenario(
        currency=_input_from_dict(data["currency"], str),
        usable_capacity=_input_from_dict(data["usable_capacity"], int),
        sellable_capacity=_input_from_dict(data["sellable_capacity"], int),
        ticket_scale=tuple(
            TicketTier(
                name=tier["name"],
                price=_input_from_dict(tier["price"], Decimal),
                quantity=_input_from_dict(tier["quantity"], int),
            )
            for tier in data["ticket_scale"]
        ),
        sell_through=_input_from_dict(data["sell_through"], Decimal),
        ticketing_deduction_per_paid_ticket=_input_from_dict(
            data["ticketing_deduction_per_paid_ticket"], Decimal
        ),
        tax_rate_on_gross=_input_from_dict(data["tax_rate_on_gross"], Decimal),
        deal=DealDefinition(
            deal_type=_input_from_dict(deal["deal_type"], DealType),
            guarantee=_input_from_dict(deal["guarantee"], Decimal),
            backend_percentage=_input_from_dict(deal["backend_percentage"], Decimal),
            backend_basis=_input_from_dict(deal["backend_basis"], BackendBasis),
            artist_expenses=_input_from_dict(deal["artist_expenses"], Decimal),
            approved_expense_names=_input_from_dict(deal["approved_expense_names"], tuple),
        ),
        costs=FixedCosts(**{
            name: _input_from_dict(costs[name], Decimal)
            for name in ("marketing", "production", "venue", "labor", "insurance", "other")
        }),
        ancillary_revenue=_input_from_dict(data["ancillary_revenue"], Decimal),
        sponsorship_allocation=_input_from_dict(data["sponsorship_allocation"], Decimal),
    )


def output_to_dict(item: OutputValue) -> dict[str, Any]:
    return {
        "value": str(item.value) if isinstance(item.value, Decimal) else item.value,
        "status": item.status.value,
        "provenance": item.provenance.value,
        "lineage": list(item.lineage),
        "currency": item.currency,
        "reason": item.reason,
    }


def evaluation_to_dict(result: Evaluation) -> dict[str, Any]:
    return {
        "engine_version": result.engine_version,
        "currency": result.currency,
        "outputs": {name: output_to_dict(value) for name, value in result.outputs.items()},
    }
