# Show Economics Private-Data Readiness V1

This is the mapping contract between a future design-partner settlement file
and the existing show-economics workbench. It does not authorize an import and
does not add a second ingestion framework. The existing design-partner mapper
remains the ingestion boundary; partner data remains private.

Status meanings:

- `SUPPORTED_NOW`: the current workbench can retain the field with explicit
  provenance and either calculate with it or use it as audit context.
- `SCHEMA_READY_NOT_CALCULATED`: it can be retained as context or a future
  validation outcome, but the V1 engine does not use it in arithmetic.
- `NOT_SUPPORTED`: the present contract cannot represent the required mechanics
  faithfully.

| Partner field | Status | Current mapping / boundary |
|---|---|---|
| usable capacity | `SUPPORTED_NOW` | `usable_capacity` |
| sellable capacity | `SUPPORTED_NOW` | `sellable_capacity` |
| holds, kills, comps | `SCHEMA_READY_NOT_CALCULATED` | identity context only; no invented inventory arithmetic |
| ticket tiers, face prices, sellable inventory | `SUPPORTED_NOW` | `ticket_scale` price and quantity |
| paid tickets | `SCHEMA_READY_NOT_CALCULATED` | engine derives scenario paid tickets; observed paid tickets are validation context, not an override |
| refunds | `NOT_SUPPORTED` | no refund or chargeback mechanics |
| deal type | `SUPPORTED_NOW` | three closed deal structures |
| guarantee | `SUPPORTED_NOW` | `deal.guarantee` |
| backend percentage | `SUPPORTED_NOW` | `deal.backend_percentage` |
| backend basis | `SUPPORTED_NOW` | explicit gross, adjusted gross, or net-after-approved-expenses basis |
| approved expenses | `SUPPORTED_NOW` | explicit names from the supported fixed-cost fields |
| final artist settlement | `SCHEMA_READY_NOT_CALCULATED` | future observed validation outcome; never overwrites scenario settlement |
| gross box office | `SCHEMA_READY_NOT_CALCULATED` | future observed validation outcome; never overwrites scenario gross |
| taxes | `SUPPORTED_NOW` | explicit gross tax rate only |
| ticketing deductions | `SUPPORTED_NOW` | explicit deduction per scenario paid ticket only |
| venue cost | `SUPPORTED_NOW` | `costs.venue` |
| production cost | `SUPPORTED_NOW` | `costs.production` |
| labor cost | `SUPPORTED_NOW` | `costs.labor` |
| marketing cost | `SUPPORTED_NOW` | `costs.marketing` |
| insurance | `SUPPORTED_NOW` | `costs.insurance` |
| other cost | `SUPPORTED_NOW` | `costs.other` |
| ancillary revenue | `SUPPORTED_NOW` | `ancillary_revenue` |
| sponsorship allocation | `SUPPORTED_NOW` | `sponsorship_allocation` |
| offer created at | `SUPPORTED_NOW` | scenario identity/audit context |
| assumption as of | `SUPPORTED_NOW` | per-input `as_of` |
| settlement finalized at | `SCHEMA_READY_NOT_CALCULATED` | future validation context |
| revision ID | `SUPPORTED_NOW` | append-only `revision_key` and `revision_no` |
| evidence document reference | `SUPPORTED_NOW` | per-input `evidence_ref`; references only, no evidence mutation |
| tier-specific sell-through/order | `NOT_SUPPORTED` | one show-level sell-through rate |
| jurisdiction-specific tax rules | `NOT_SUPPORTED` | explicit scenario rate only |
| payout timing and multi-currency FX | `NOT_SUPPORTED` | one explicit ISO currency; no timing or FX model |

## Existing importer alignment

The current design-partner contract already recognizes event identity and
timing, venue/event/ticket capacities, deal type, guarantee, backend percentage,
artist expenses, ticket/attendance observations, gross/net/ATP, supported cost
categories, ancillary categories, settlement outcomes, currency, source, and
notes. A first partner mapping would extend that existing closed contract for
backend basis, approved expense names, tier arrays, explicit tax/deduction
terms, assumption/revision timestamps, and evidence references. Those are
schema-mapping additions—not a new importer and not new engine mechanics.

The first partner file can therefore validate deterministic replay, field
coverage, scenario-to-settlement errors for supported outputs, and whether
missing inputs prevent evaluation. It cannot validate a legitimate pre-offer
underwriting model until historical decision-time versions of the inputs are
available and separated from settlement-time outcomes.
