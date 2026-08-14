# Design Partner Retrospective — "Backtest My Shows"

The bridge from infrastructure to proprietary outcome data. A promoter,
venue group, or festival operator sends us historical show data; we return an
immediate data audit + a blind-retrospective package, without training a
single model.

## What we want

Historical, already-settled events: identity (artist, venue, market, date),
decision timing (offer / booking / announcement / on-sale), event
configuration (capacities), deal terms, ticketing (sold / paid / comps /
refunds / scanned), costs, ancillary revenue, and settlement.

See `docs/design-partner-data-contract.md` and the templates in
`data/import_templates/`.

## Accepted formats

CSV, TSV, XLSX. Any sane tabular layout — column names are mapped
conservatively; ambiguous columns are flagged for review rather than guessed.

## What we do NOT need

Ticket-buyer PII (names, emails, phones, addresses, card data) is quarantined
and never ingested. We do not need individual transactions, only per-event
aggregates.

## Privacy posture

- Default sharing policy: `PRIVATE_ONLY`.
- Customer data is `OBSERVED_PRIVATE` and is never mixed with public data.
- Pooling into anonymized/aggregate benchmarks is explicit opt-in only.

## How the blind retrospective works

1. Outcomes (attendance, tickets sold, gross, settlement, promoter
   contribution) are written to a **separate outcome vault**.
2. The feature side can only see pre-cutoff evidence that is not a hidden
   outcome (public observations + explicitly-allowed private inputs such as
   capacity, deal terms, and costs).
3. Two manifests are exported: a feature-side manifest (no outcomes) and an
   outcome-side manifest (targets + realized values). Future model code reads
   only the feature side; outcomes are revealed only for scoring.

Leakage is impossible by construction: the feature-side access path excludes
hidden outcome types, and this is asserted by regression tests.

## What the customer receives

A promoter data audit (JSON + HTML) reporting dataset coverage, quality
issues, financial reconciliation, PIT reconstructability, and how many events
are eligible for a rigorous blind backtest — with recommendations for
improving their data. No predictions.

## CLI

```
festival-bloomberg backtest import --customer demo_promoter --events shows.xlsx
festival-bloomberg backtest audit --dataset ds_demo_promoter
festival-bloomberg backtest create-study --dataset ds_demo_promoter --target paid_attendance --cutoff announcement
festival-bloomberg backtest freeze --study study_ds_demo_promoter_paid_attendance_announcement
festival-bloomberg backtest readiness --study study_ds_demo_promoter_paid_attendance_announcement
```

No model command exists yet, on purpose.
