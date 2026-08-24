# Show Economics Workbench V1 — Financial Contract

`festival_bloomberg.economics.show_economics` is a pure deterministic engine.
It evaluates the consequences of explicit evidence and buyer assumptions. It
does not estimate whether those assumptions will occur.

## Inputs and provenance

Every input is a `TypedInput` with one provenance value:

- `OBSERVED_PUBLIC`
- `OBSERVED_PRIVATE`
- `USER_ASSUMPTION`
- `DERIVED`
- `UNKNOWN`

`UNKNOWN` carries no value. Known values cannot carry `UNKNOWN` provenance.
Optional `evidence_ref`, `as_of`, and `entered_by` metadata identify the source
and audit context without changing the equation or provenance class.
Outputs are `DERIVED` when calculable and retain the leaf input names in their
lineage. Uncalculable outputs carry a distinct `UNKNOWN`, `NOT_ACHIEVABLE`, or
`NOT_COMPARABLE` status and no numeric value.

The V1 scenario contract contains:

- explicit currency;
- usable and sellable capacity;
- ticket tiers with price and quantity;
- sell-through;
- per-paid-ticket ticketing deduction and gross-tax rate;
- one explicit deal definition;
- marketing, production, venue, labor, insurance, and other fixed costs;
- ancillary revenue and sponsorship allocation.

Known capacities and quantities are whole, nonnegative ticket counts. Sellable
capacity cannot exceed usable capacity, and known tier quantities must sum to
sellable capacity. Rates are decimals from zero through one. Binary floating
point is rejected.

## Deal structures and bases

V1 supports only:

- `FLAT_GUARANTEE`;
- `GUARANTEE_VS_PERCENTAGE`;
- `PERCENTAGE_OF_DEFINED_BASE`.

Percentage deals require one explicit basis:

- `GROSS_BOX_OFFICE`;
- `ADJUSTED_GROSS`, defined as gross box office less explicit tax and ticketing
  deductions;
- `NET_AFTER_APPROVED_EXPENSES`, defined as adjusted gross less the scenario's
  named subset of fixed-cost fields.

An unknown percentage or basis is not replaced by an industry convention.
Undefined approved-expense names fail validation.

## Equations

For ticket tiers `i`, realized sell-through `s`, and sellable capacity `C`:

```text
gross potential = sum(price_i * quantity_i)
weighted ATP = gross potential / sum(quantity_i)
paid tickets = floor(C * s)
gross ticket revenue = weighted ATP * paid tickets
tax = gross ticket revenue * explicit tax rate
ticketing deductions = paid tickets * explicit per-ticket deduction
adjusted gross = gross ticket revenue - tax - ticketing deductions
```

Artist settlement is:

```text
flat = guarantee + artist expenses
percentage = percentage * explicit basis + artist expenses
guarantee-vs-percentage = max(guarantee, percentage * explicit basis)
                          + artist expenses
```

Promoter economics are:

```text
fixed costs = sum(marketing, production, venue, labor, insurance, other)
variable costs = tax + ticketing deductions + artist settlement
event costs = fixed costs + variable costs
promoter revenue = gross ticket revenue + ancillary revenue
                   + sponsorship allocation
promoter contribution = promoter revenue - event costs
promoter margin = promoter contribution / promoter revenue
```

The engine reports the first whole paid-ticket count with nonnegative
contribution, the corresponding sell-through, the first cent-level average
ticket price that breaks even at the scenario's paid tickets, and required
sellable capacity at the scenario's sell-through. It also reports contribution
headroom for additional cost, the artist-settlement ceiling at zero
contribution, and the corresponding flat-guarantee ceiling after artist
expenses. A boundary beyond explicit usable/sellable capacity is
`NOT_ACHIEVABLE`, not a recommendation to increase capacity.

`boundary_grid` evaluates explicit price × capacity × sell-through combinations
against a caller-provided contribution and optional margin hurdle. `sensitivity`
holds all other fields fixed while varying sell-through, average ticket price,
artist guarantee, sellable capacity, marketing, or production. Neither function
assigns likelihoods.

## Decimal, rounding, and currency

All monetary inputs and calculations use `Decimal`. Named monetary components
and outputs round to currency cents with `ROUND_HALF_UP`. Realized ticket counts
round down to whole tickets. Rates and margins are reported to six decimal
places. Weighted ATP division uses 40-digit internal precision before the
displayed cent rounding. Break-even ticket searches return the first whole
ticket with nonnegative contribution; price searches return the first cent with
nonnegative contribution.

A scenario has exactly one explicit uppercase three-letter currency code. The
engine has no FX source and never assumes 1:1 conversion. If currency is
`UNKNOWN`, currency-dependent economics and boundaries remain `UNKNOWN`.

## UNKNOWN behavior

UNKNOWN is dependency-specific. An unknown capacity leaves gross potential
known when the tier scale is complete, but paid-ticket revenue and contribution
remain unknown. An unknown artist guarantee in a flat deal leaves ticket revenue
known but settlement and contribution unknown. An unknown fee leaves no implied
standard fee. Zero is valid only when explicitly supplied or mathematically
independent of an unknown input, such as ticket revenue at zero paid tickets.

## Workspace persistence

`planning.show_economics_scenarios` exists only in the mutable WORKSPACE schema.
It stores scenario identity, optional planning project identity, currency,
engine version, lossless inputs with provenance/evidence references, derived
outputs, identity context, parent-scenario lineage, revision number, and
timestamps. Every save also appends a full replayable row to
`planning.show_economics_scenario_revisions`, including leaf fields changed
from the prior revision. Decimal values serialize as strings. Loading the input
payload reconstructs the domain object for deterministic replay. No analyst
assumption is written to canonical evidence or a serving snapshot.

## Limitations and doctrinal guards

V1 provides:

```text
NO demand prediction
NO expected attendance
NO recommended guarantee
NO GO/HOLD/PASS
NO probability of loss
NO VaR/CVaR
NO implicit fees, taxes, deal bases, or FX
```

It does not model tier-specific sell-through order, dynamic pricing, refunds,
chargebacks, settlement timing, tax jurisdiction logic, multiple currencies,
or probabilistic uncertainty. Comparable outcomes may be shown beside a
scenario by another read model, but they are not inputs to these equations.
