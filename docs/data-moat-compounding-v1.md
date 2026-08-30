# Data Moat Compounding V1

## Technical summary

**Decision: proceed, but change the order of work.** The repository already has
the raw estate and most of the product vocabulary. The critical path is now
correctness, identity, rights, geography, and serving integration—not another
round of indiscriminate acquisition.

Sixteen audit workstreams reviewed architecture, Wikidata, ListenBrainz
MAP/REDUCE, ListenBrainz statistical methodology, identity, geography, venues,
ticket markets, YouTube, private outcomes, buyer questions, rights,
performance, tests, open-source leverage, and competitors.
The full machine-readable synthesis is
`reports/data_moat_swarm_review_v1.json`.

All process states and counts in this document are observations as of
2026-08-30T21:29:44Z, not live guarantees.

The current verified boundary is narrower than the checked-out code:

- canonical and serving databases are at migration 36; source migrations reach
  47;
- the R2 catalog contains about 255.87 GB across 8 Raw, 16 Silver, and 4 Gold
  datasets, but no cataloged Bronze or Serving datasets;
- major 25K/security and newer Wikidata, ListenBrainz, ticket, and identity
  products are still lake/control artifacts rather than verified serving
  products; the older `core.entity_external_ids` layer is present;
- the live Wikidata scan restarted from byte zero after a host reboot at 14:06
  local time because the parallel path has no checkpoint;
- ListenBrainz is not running, and its current checkpoint is intentionally
  invalid for the configured source.

## Key findings and decisions

The rank is judgmental. It uses all seven requested dimensions and does not
collapse them into a composite score. Cost scores below are burdens: 5 is
hardest or most expensive.

| Rank | Finding | Decision | Moat | Product | Unique | Impl. cost | Ongoing | Rights confidence | Time to value |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | Canonical/serving migration parity | Fix first | 5 | 5 | 4 | 3 | 3 | 4 | 2 |
| 2 | Identity Graph V2 | Build after parity | 5 | 5 | 5 | 3 | 3 | 4 | 3 |
| 3 | ListenBrainz reducer/statistical correctness | Block 5% run until fixed | 4 | 4 | 5 | 3 | 3 | 3 | 2 |
| 4 | Rights and privacy propagation | Fail closed | 5 | 5 | 5 | 4 | 4 | 2 | 2 |
| 5 | Wikidata normalized Silver | Correct and rebuild | 5 | 5 | 4 | 3 | 2 | 5 | 3 |
| 6 | Canonical market geography | Build | 5 | 5 | 5 | 3 | 3 | 5 | 3 |
| 7 | Buyer actionability trust layer | Ship bounded MVP | 4 | 5 | 4 | 2 | 2 | 4 | 5 |
| 8 | Ticketmaster 5K official rail | Scale after gates | 4 | 5 | 4 | 3 | 3 | 2 | 4 |
| 9 | Typed venue securities | Build 10K typed rail | 4 | 5 | 4 | 3 | 3 | 4 | 4 |
| 10 | YouTube proof states | Reclassify, then verify | 3 | 4 | 3 | 2 | 2 | 2 | 4 |
| 11 | Private settled-outcome vault | Secure pilot only | 5 | 5 | 5 | 5 | 5 | 3 | 1 |
| 12 | Release data-quality gates | Implement with products | 4 | 5 | 3 | 3 | 2 | 5 | 4 |
| 13 | Measured compute and R2 behavior | Optimize after correctness | 3 | 4 | 2 | 3 | 3 | 5 | 3 |
| 14 | OSS and competitive focus | Retain foundation | 5 | 4 | 5 | 2 | 2 | 4 | 4 |

### 1. Make the checked-out product real in canonical and serving storage

The verified snapshot stops at migration 36 while the repo reaches 47. That
explains several apparent contradictions: tests and migration files describe
25K identity, ticket, and artist-market products that are absent from the live
database. Missing tables must not be reported as zero coverage.

Apply migrations to a copy, materialize the intended tables, validate content
and endpoint parity, and publish an immutable snapshot. Do not mutate the
current verified serving file in place.

### 2. Build Identity Graph V2 before multiplying derived products

The current external-ID table contains malformed identifiers, empty values, and
provider IDs linked to multiple artists. Venue resolution can silently select
the first row. Identity Graph V2 must store append-only assertions and explicit
states:

`VERIFIED_EXACT`, `SUPPORTED_MULTI_SOURCE`, `CANDIDATE`, `AMBIGUOUS`,
`CONFLICT`, and `MISSING`.

Provider-specific validators decide admissibility. Name matches may propose
candidates but cannot promote them. Conflicts coexist and remain visible on
artist, venue, and market pages.

### 3. Fix ListenBrainz before the 5% production test

The current reducer does not prove the product it claims:

- batch partials are concatenated without global listener/artist aggregation
  before ranking;
- duplicate fragments can consume TOP-25 slots;
- pair support is filtered inside partitions before global union;
- artist/day partials are copied without global aggregation;
- Jaccard, cosine, and lift use node and pair counts from different universes;
- support 3 has no null model, multiplicity control, or replicate stability.

The 5% run remains blocked until global aggregation, estimator consistency,
multi-credit handling, checkpoint geometry, and restart behavior pass explicit
fixtures. The interruption test must compare exact output hashes with a clean
reference and record RSS, local disk, network, object count, and output bytes.
Only artist-level aggregates may advance; listener-level rows remain
quarantined pending privacy review.

### 4. Treat the live Wikidata result as provisional

The scan is operationally healthy, but the current parser and finalizer do not
meet the phase acceptance contract. Current code emits full URI QIDs, drops
location relations after stripping URI delimiters, stores P31 type facts as
external IDs, uses overly broad venue classes, deletes spills before canonical
publication is verified, and builds full outputs in memory.

At report time, the existing launchd-managed job was the only heavy process.
Preserve its run evidence without treating this document as an instruction to
launch or continue compute. Canonical Silver still requires a corrected, tested build that
produces typed `music_entities`, `external_ids`, `locations`, `genres`, and
`relationships` under a run-scoped generation manifest. Retain spills until
hashes, schemas, counts, and catalog entries are verified; garbage-collect them
only with explicit operator approval and a recoverable procedure.

### 5. Build one geography layer

The current static artist-market mapping covers only 77,290 of 326,608
performer-place rows (23.66%). Historical markets contain venue names,
state-only values, and missing countries; venue geography is mostly dropped;
competitive-calendar location can select non-temporal minima.

Create stable place and market IDs with source observations, area ancestry,
country/region/city/metro, centroid or boundary version, timezone, resolution
method, and conflict state. All tape, ticket, venue, outcome, calendar, and API
joins should use canonical IDs while retaining raw provider strings.

### 6. Scale only products with honest semantics

- **Ticket:** rebuild current status from the official Ticketmaster rail. The
  prior 571-pair cohort is stale; only 530 were future and onsale in the
  August 25 snapshot reviewed on August 30. The 5K gate requires native IDs, <=24-hour freshness, two successful
  passes, explicit price basis, and no OFFSALE/zero-listing to SOLD_OUT
  conversion.
- **Venue:** retain all 82,547 MusicBrainz places as source records, but count a
  venue security only after typed eligibility. The initial defensible candidate
  rail is 15,253 live-event-used places. Capacity remains a claim: 52 current
  claims across 28 venues cannot support a 1K claim today.
- **YouTube:** the 25K artifact contains zero API-verified channels. Reclassify
  the estate into proof states first. There are 11,719 unique UC-shaped ID
  values across 10,607 artists, but malformed rows and 25 collision values must
  be quarantined before batched verification.
- **Audience:** serve only corrected, descriptive consumption affinity. It is
  not local demand, ticket intent, or an underwriting probability.

## Buyer product boundary

The proposed, testable near-term product hypothesis is an evidence workbench
for two proposed shows. It
should let a buyer resolve identity, inspect configuration-compatible capacity
claims, review recent plays and market evidence, see PIT-labeled competitive
events, inspect comparable gross/headcount ranges, and run deterministic
economics from explicit inputs. It should end with a missing-evidence request
list.

It must not output `BOOK`, `GO`, a demand score, expected attendance, a
guarantee recommendation, availability, or an audience-overlap claim. Unknown
private facts remain explicit next actions.

## Private outcome moat

`BACKTEST_MY_SHOWS / SETTLED_OUTCOME_VAULT` is the hypothesized highest-upside
long-term moat, not a production-ready feature. Existing import and retrospective code is
a useful research foundation, but logical tenant columns and a hidden flag are
not a security boundary.

Before a customer pilot, require authenticated tenant scope, physical private
storage, encrypted tenant keys, audited freeze/reveal, streaming PII
quarantine, field-level `known_at`, correction/conflict history, consent,
retention, export, deletion, revocation, backup/restore, and proof that private
rows never enter public serving snapshots.

## Rights and commercial-use boundary

One authoritative registry must drive recursive inheritance. A composite is no
more permissive than its most restrictive input. Commercial serving fails
closed for `UNKNOWN`, `RESEARCH_ONLY`, `PROTOTYPE_ONLY`, `INTERNAL_ONLY`,
`TERMS_REVIEW_REQUIRED`, or `COMMERCIAL_AGREEMENT_REQUIRED`.

MusicBrainz core dumps and Wikidata structured data are the cleanest broad
rails. Wikimedia/Wikipedia and OSM require attribution/license handling.
Ticketmaster, YouTube, Spotify, Setlist.fm, JamBase, SeatGeek, proprietary
touring sources, and underlying Common Crawl pages remain blocked or
terms-bounded until explicit approval. Listener-level ListenBrainz output stays
quarantined even though the source data license is open.

Primary public references checked by the rights and competitive audits on
2026-08-30 include [MusicBrainz licensing](https://musicbrainz.org/doc/About/Data_License),
[ListenBrainz data](https://listenbrainz.org/data/),
[Wikidata licensing](https://www.wikidata.org/wiki/Wikidata:Licensing),
[OpenStreetMap attribution](https://osmfoundation.org/wiki/Licence/Attribution_Guidelines),
[YouTube developer policies](https://developers.google.com/youtube/terms/developer-policies),
and [Ticketmaster developer terms](https://developer.ticketmaster.com/support/terms-of-use/).

## Explicit disagreements resolved

- **Wikidata:** no live-process P0 justified stopping the launchd job, but its
  outputs still fail canonical acceptance. Preserve the run; rebuild from
  corrected code before publication.
- **ListenBrainz thresholds:** support 3 is exploratory Silver evidence;
  support 10/node 50 are only conservative candidates. Neither is a validated
  Gold rule without corrected estimators, FDR, and stability.
- **Open license versus privacy:** an open ListenBrainz license does not remove
  the privacy/governance risk of listener-level derived rows.
- **Venue counts:** all MusicBrainz places remain source evidence; only typed
  live-music venues count as securities.
- **YouTube:** a MusicBrainz official link is useful evidence, not an API
  verification.
- **Private vault:** accept the research workflow; reject production tenant or
  security claims.
- **New infrastructure:** retain Arrow/PyArrow, DuckDB, and R2. Pilot Polars,
  Pandera, Iceberg, Splink-shadow, or bounded NetworkX only when an equivalence
  test demonstrates a measurable win.

## Performance and robustness gates

Only one heavy job may run. After Wikidata exits, confirm no relevant PID,
settled R2/network activity, and sufficient free disk before ListenBrainz. The
5% test records RSS P50/P95/max, peak temporary disk, network bytes, spill
bytes, R2 object count/bytes, retries, throughput, and output hashes.

Cloud health and metrics endpoints must be bounded independently of object
count; small JSON ticks need explicit compaction and retention. Search FTS must
execute rather than silently fall back to a slower sequential scan.

## Next steps

1. Fix PR #57's narrow gitleaks false positive and keep its exact-head CI green.
2. Coordinate with and preserve evidence from the launchd-managed Wikidata run,
   then correct and rebuild canonical Silver with transactional publication.
3. Establish canonical/serving migration parity.
4. Build Identity Graph V2 and canonical geography.
5. Fix ListenBrainz reducers/statistics and run the isolated 5% interruption
   test.
6. Promote only accepted artist-level audience products.
7. Reclassify and verify YouTube identities; then scale the official ticket
   rail and typed venue layer.
8. Run the two-proposal buyer actionability test.
9. Keep the private outcome vault design-only until its P0 security and privacy
   controls exist.

Further questions are deliberately narrow: what minimum free-disk gate is safe
for each reducer; what empirical stability threshold promotes an audience edge
to Gold; which sources receive explicit commercial agreements; and which three
to five design partners will support a secure, consented settled-outcome pilot.
