# Design-Partner Onboarding — readiness package

Free public data will not produce promoter-level economics. This is the
commercial track: a design partner hands over **event history, offers,
bookings, capacity, guarantees, ticket sales, gross, costs, settlements** —
without customer PII.

## What unlocks at each corpus size (expected, research-informed)

| Events | Warm-start coverage | Artist repeat history | Venue history | Market history | Gross labels | Guarantee labels | Economics-readiness |
|---|---:|---:|---:|---:|---:|---:|---|
| 50 | partial | thin | thin | thin | 50 | ~0 | basic MAE only |
| 100 | moderate | sparse | sparse | sparse | 100 | ~10 | comp ranges real |
| 250 | moderate | some repeats | some | some | 250 | ~40 | venue/market comps usable |
| 500 | good | repeat clusters | good | good | 500 | ~100 | economics research real |
| 1,000 | good | dense | dense | dense | 1,000 | ~250 | underwriting research |
| 5,000 | strong | strong | strong | strong | 5,000 | ~1,500 | credible economics |

Guarantee coverage assumes ~20–30% of events carry a guarantee field.

## Required inputs (PII excluded by design)

- event history (date, market, venue, promoter)
- offers / bookings (offer date, terms)
- capacity (as sold / as used)
- guarantees
- ticket sales, gross, costs
- settlement outcomes

## Deliverables in this milestone

- `docs/design-partner-data-contract.md` (existing) — canonical schema.
- Sanitized example CSV (strip PII; ids only).
- Validation CLI: schema check + PII quarantine report (emails, phones,
  government ids flagged, never ingested).
- Import preview: dry-run row counts + quality report.
- Data quality + coverage reports per partner.
- Rights/privacy summary for the partner.

## DESIGN_PARTNER_READINESS scorecard (definition)

For each partner corpus compute: rows eligible per target
(REPORTED_ATTENDANCE / PAID_TICKETS / TICKET_GROSS), warm-start artist count,
venue/market history depth, gross label count, guarantee label count,
coverage heatmap by year × market, and a readiness tier
(EXPLORATORY / RESEARCH / ECONOMICS_USABLE / UNDERWRITING_CANDIDATE).

## Guardrails

- PII quarantine is a hard gate before any analytics.
- Guarantee UNKNOWN stays UNKNOWN (never $0).
- Research-only public data is never mixed into commercial surfaces.
