# Show Economics Workbench V1 — Spec

A deterministic show/offer economics research tool. The buyer supplies
assumptions; Festival Bloomberg does the math and shows the evidence ledger.

**This is NOT**: demand prediction, guarantee estimation, booking
recommendation, or a lineup optimizer.

## Core sentence

> "If these assumptions hold, this is the economics."

Never: "This artist is worth $X."

## Typed inputs

Every input is typed:

- `OBSERVED_PUBLIC` — public provider evidence (e.g. a published capacity claim)
- `OBSERVED_PRIVATE` — design-partner evidence (isolated, rights-gated)
- `USER_ASSUMPTION` — an explicit scenario input the buyer chose
- `DERIVED` — computed from other typed inputs
- `UNKNOWN` — no value; never coerced to 0

## Scenario inputs

- **ticket scaling** — per price tier: tier, price, quantity
- **capacity** — usable capacity, ticket capacity (distinct)
- **deal** — guarantee, backend %, deal basis (gross/net), flat vs guarantee,
  artist expenses
- **costs** — marketing, production, venue, labor, other (each separately typed)
- **fees/tax** — ticketing deductions, applicable tax treatment (explicit)

Deal formulas are explicitly defined per scenario — no assumed "industry
standard" settlement definitions.

## Outputs

- gross potential (sum of tier price × quantity)
- weighted average ticket price (ATP)
- sell-through scenarios (50% / 60% / 70% / 80% / 90% / 100%)
- paid-ticket scenarios
- artist settlement under the specified formula
- promoter contribution
- break-even paid tickets
- break-even sell-through
- margin of safety
- guarantee sensitivity
- price sensitivity
- capacity sensitivity
- comparable-event outcomes shown *beside* the scenario (not mixed in)
- evidence / assumption ledger
- explicit `UNKNOWN` fields

## Arithmetic rules

- money uses exact decimal arithmetic (`Decimal`), never binary float
- explicit currency throughout
- no implicit guarantee cost; an unknown guarantee stays `UNKNOWN`

## Doctrinal guards

- No recommended offer.
- No estimated guarantee.
- No predicted attendance.
- `OBSERVED_PRIVATE` fields flow through the same calculation contract as
  public fields, but never leave the isolated partner environment.

## Dependencies

- `SHOW_ECONOMICS_WORKBENCH_V1` is the next deterministic product milestone and
  does **not** depend on ML success or Comparable V2.
- It integrates eventual design-partner `OBSERVED_PRIVATE` evidence via the
  existing `economics/` stack (no new importer).

## Status

SPEC ONLY — not implemented. Implementation is gated until PR #35 (dense panel)
is complete.
