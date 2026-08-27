# ARTIST_SECURITY_25000_DATABASE_V1

## Initial build status

**PARTIAL — database shell and deterministic tiers are materialized.** This
milestone intentionally does not fabricate historical volume. The current
local source estate contains 114,167 canonical artists and 9,782 factor
observations; the 25K tier membership is complete, while bulk historical
promotion remains to be populated from official dumps.

Initial report: `ARTIST_SECURITY_25000_DATABASE_REPORT.json`.

## Tier model

- `IDENTITY_100K_PLUS`: canonical identity/reference layer
- `COVERAGE_25000`: deterministic 25,000-artist analytical universe
- `CORE_5000`: cumulative membership of the first 5,000 coverage artists
- `HOT_1000`: cumulative membership of the first 1,000 coverage artists

The exclusive assignment is stored once in
`security.artist_security_universe_25000`; cumulative membership is stored in
`security.artist_security_tiers`. `artist_key` remains the canonical key.

Selection uses observable evidence only: event-performance presence,
Ticketmaster identity evidence, attention observations, and external identity
depth. No opaque score is used.

## Real counts from the local build

| Measure | Count |
|---|---:|
| Canonical artists | 114,167 |
| Coverage securities | 25,000 |
| Core securities | 5,000 |
| Hot securities | 1,000 |
| Canonical event snapshots | 96,556 |
| Current ticket pairs | 571 |
| Existing factor observations | 9,782 |

## Storage architecture

Historical datasets are exported as compressed Parquet with immutable manifest
records in `security.bulk_dataset_manifests`. Forward evidence remains in
content-addressed raw storage and small tick records. DuckDB remains the
materialized analytical view.

The build exports current factor, attention, and performance observations into
separate Parquet datasets. Empty or unavailable source families are reported,
not filled.

## Remaining work

- Ingest current MusicBrainz official dump/reference entities and join the 25K
  tiers.
- Ingest ListenBrainz full/incremental bulk data rather than per-artist API
  loops.
- Materialize 730-day Wikimedia history for coverage and full history for core
  into partitioned Parquet.
- Expand event/venue/Artist × Market materializations from actual evidence.
- Run tier-aware YouTube daily/higher-frequency collection using batched
  `channels.list` calls.
- Re-run product/query benchmarks and Voyager only after factor density grows.

No ML, demand score, recommendation score, or equal-depth 100K collection is
part of this milestone.
