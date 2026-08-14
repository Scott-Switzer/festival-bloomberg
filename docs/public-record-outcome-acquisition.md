# Public-Record Outcome Acquisition

How future public-agency records (permits, agreements, board packets, annual
reports) will be attached to canonical events, without fabricating any
submission or record.

## Status

**Readiness only.** No FOIA or public-records request has been submitted and
no received document is ingested yet. This document and the import template
define the target shape so that when a record is lawfully obtained it can be
ingested without a schema change.

## Fields we request from public agencies

See `data/import_templates/public_event_records.csv` (names only):

- `event_name`, `event_dates`, `venue`, `market`
- `expected_attendance` (a PLAN, never stored as actual attendance)
- `actual_attendance`, `paid_attendance`, `scanned_attendance`
- `ticket_count`
- `usable_capacity`, `permit_capacity_limit`
- `permit_fee`, `per_ticket_fee`, `revenue_to_agency`
- `contractual_guarantee` (a DEAL TERM, not realized revenue)
- `settlement_payment` (realized, when the source says so)
- `permit_application_date`, `contract_date`
- `source_document_id`, `source_url`, `source_name`, `publication_date`
- `rights_status`, `notes`

## Semantics (enforced at import, not by convention)

- `expected_attendance` is never imported as an actual-attendance claim.
- `contractual_guarantee` is `ARTIST_GUARANTEE` only if the source is a
  contract term; a `settlement_payment` is a separate realized amount.
- `permit_capacity_limit` → `PERMIT_CAPACITY_LIMIT`, never attendance.
- `revenue_to_agency` is government revenue, not promoter gross — it does not
  become `TICKET_GROSS`.

## Source-manifest format

Each received document is attached to canonical events via the existing
evidence + claim ledger (`raw_payload_hash`, `evidence_observation_id`,
`source_document_id`, `source_url`, `publication_date`). Documents that
cannot be resolved to a canonical event stay as evidence with an unresolved
entity-resolution confidence and are flagged for review.

## Rights

Public records are not automatically commercially usable. Each record's
`rights_status` is set explicitly; unknown defaults to `UNKNOWN` (fail
closed). Government-produced documents may be public domain, but the specific
terms of the producing agency must be checked before any commercial use.
