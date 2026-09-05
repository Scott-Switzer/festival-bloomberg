# Factor Tape History V2 — recon and decision

Read-only audit performed 2026-09-05 UTC (September 4 Pacific).

## Verified starting state

- Remote main: `dedca109834a25967f9c2e711e9d43443bbd5637` (API + fetch).
- Original checkout: `feat/artist-security-25000-database-v1`, `f4f6add62bd5d0860e53df3b9a6c4652f6679c10`, 0 ahead / 61 behind. Three tracked modified files and ten untracked paths preserved. No open PRs.
- Main CI run 33829365432: success. Browser UAT run 33829364627: failure with no jobs; workflow uses unsupported job-level secrets condition. Staging run 33829365468: deployment followed by health timeout; hosted workflow acceptance not reached.
- Acquisition deployment: `598320bb-dd7f-471d-bbdc-4572630738ac`, 100%, deployed September 3 19:20 UTC, queried with installed Wrangler.
- Configured acquisition: one-minute scheduler; YouTube, structured API, browser, Monid, processing and DLQ queues; governor/container/batch DOs; raw/lake/private/backups R2; $0.25/day and $7.50/month provider caps. Configuration is not proof of actual billed cost or queue health.
- Local canonical warehouse: 48 migrations, 200 tables, 356 core artists, 657 historical engagements, 562 forward events, 1,716 outcome claims. Security universe, artist factors and ticket observation tables inspected are empty. Local counts are not cloud coverage.
- Live R2 inventory: raw 1,073 objects / 256,185,929,952 bytes; Silver 315 objects / 321,403,886 bytes; YouTube staging 29,262 objects / 23,107,317 bytes. Listing is a live observation, not an atomic source snapshot.
- Gold downloaded and SHA-256 verified: `artist_factor_tape_v1_20260903T182518Z`, 66,684 rows, 725 artists, August 27 22:04 through September 3 18:02 UTC. No missing knowledge_time/evidence_ref. All 66,684 measurement_window/geographic_scope values are null; preserve NOT_COMPARABLE.
- Serving CURRENT: `terminal_v1_20260903T190113Z`, 143,405,056 bytes, SHA `8c22ddb35f6a95bfa57856c32f00dd7e9dc854efaeb09c92a0d448cb0d0ed614`. Metadata reports 25,000 artists, 47,602 external IDs, 27,322 markets links, 134,447 event history, 14,758 festival appearances, 6,690 future events, 50,886 attention observations, 90,866 peers, 111 sentiment observations. These counts were pointer claims at recon, not independently re-counted serving tables.
- Wikidata CURRENT advanced after serving to `20260903T210706Z-1647`; serving is not automatically synchronized with every Silver update.
- Free local disk about 8.1 GiB. No bulk downloads or canonical migrations run.

## Architecture and unfinished work

Reviewed main's Python acquisition registry, identity/security, research baselines, buyer/economics, factor tape and serving fold; TS acquisition rail and scheduler/batch dispatch; root and cloud test workflows; SQL migrations through 050; serving/container startup; R2 helper and job manifests; corpus/ListenBrainz map/reduce interfaces; current and historical handoffs, branch history, provider registry, and legacy checkout (no Python tree there).

The 25K estate is queryable in a separate cloud serving product, not in the named local canonical DB. Main includes merged product, factor and sentiment materializers absent from the old checkout. No open work needs merging. Local dirty edits overlap batch dispatch and are deliberately excluded.

Key defects: factor refresh replaces history with a bounded source slice; bounded lexicographic listing can miss newest keys; artist limit rejects repeats rather than new artists; read failures are silently skipped; all input rows/futures accumulate in Python; no persisted source ledger or interrupted-refresh checkpoint. Publication verification exists, but CURRENT has no concurrent-writer precondition in this path. Existing comparable-delta logic is duplicated in security and terminal. Batch jobs share a large mixed-purpose module. Manifest and source-code provenance can report `unknown` in images.

ListenBrainz serving still references a pilot affinity asset; full-corpus/causal demand claims are unjustified. Existing map/reduce caps and private listener storage should be retained, not superseded here. Research champion remains COMPS_SIGNAL_ONLY; no new outcome/PIT evidence found to justify model promotion. Design-partner ingestion exists but secure production tenancy and real partner outcomes are separate gates.

## Candidate ranking

Judgment scores, not measured probabilities: strategic/data-moat/user value each 1–5; success probability 0–1; cost in relative engineering units. Score = S × P × M × U / C. Scores are conditioned on available inputs and a bounded milestone, not entire category delivery.

| Rank | Candidate | S | P | M | U | C | Score | Decision |
|---|---|---:|---:|---:|---:|---:|---:|---|
| 1 | Longitudinal factor retention and replay | 5 | .95 | 5 | 4 | 2 | 47.5 | Selected: protect existing exact evidence |
| 2 | Ticket-market/history improvements | 5 | .75 | 5 | 5 | 4 | 23.4 | Next; current cohort/freshness and rights gates |
| 3 | Finish/harden 25K estate | 4 | .90 | 4 | 4 | 3 | 19.2 | Mostly exists; retention is narrow critical path |
| 4 | Competitive-event calendar | 4 | .85 | 4 | 4 | 3 | 18.1 | Existing feature; refresh/join coverage work |
| 5 | Design-partner/outcome ingestion | 5 | .40 | 5 | 5 | 3 | 16.7 | Highest long-term upside, partner/security dependence |
| 6 | Reliability/performance/cloud | 5 | .85 | 2 | 5 | 3 | 14.2 | Hosted acceptance broken; distinct follow-up |
| 7 | Artist-market intelligence | 5 | .60 | 5 | 5 | 6 | 12.5 | Requires defensible geography |
| 8 | Venue intelligence | 4 | .75 | 4 | 4 | 4 | 12.0 | Configuration evidence sparse |
| 9 | Buyer-workbench productization | 4 | .90 | 2 | 5 | 3 | 12.0 | Already broad; needs operational proof |
| 10 | Wikidata enrichment | 3 | .90 | 3 | 3 | 3 | 8.1 | Silver exists; serving lag remains |
| 11 | Open-corpus extraction productionization | 4 | .75 | 4 | 3 | 5 | 7.2 | Existing extraction; integration beats dump scale |
| 12 | Historical web/PIT reconstruction | 5 | .35 | 5 | 4 | 5 | 7.0 | Valuable but uncertain timestamp yield |
| 13 | ListenBrainz affinity | 3 | .65 | 3 | 3 | 5 | 3.5 | Sample/estimator/privacy gates |
| 14 | Legitimate modeling readiness | 3 | .25 | 2 | 3 | 3 | 1.5 | No new dense outcomes supporting promotion |

Largest bottlenecks: retention correctness; repeated-observation breadth (725/25K); decision-time/outcome density; defensible geography; hosted-product acceptance.

## Chosen contract

Keep the existing job type and Parquet serving schema. Merge verified parent observations with bounded new inputs using DuckDB. Freeze input plans, retain source object identities/hashes, checkpoint normalized chunks in R2, deduplicate exact rows, fail on conflicting observation keys, bound memory/disk/inventory, and conditionally publish after verification. Bounded batches report remaining backlog, never full coverage. Repeated inventory scans detect backdated arrivals. No new model, provider, public deployment, or privacy boundary.

Use existing boto3, Arrow and DuckDB. Official [S3 pagination](https://docs.aws.amazon.com/boto3/latest/reference/services/s3/paginator/ListObjectsV2.html), [DuckDB memory guidance](https://duckdb.org/docs/lts/operations_manual/limits), and [R2 conditional-operation compatibility](https://developers.cloudflare.com/r2/api/s3/api/) checked; no new dependency needed. DuckDB's memory_limit is not a whole-process RSS cap, so measure RSS separately.
