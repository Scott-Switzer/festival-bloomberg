# Dense Pre-Event Data Panel V1 — handoff

Branch target: `feat/dense-pre-event-data-panel-v1` (from green `main`)

Purpose: give the comparable engine (`research/comparable.py`) the independent
dimensions it needs to dethrone the hierarchical champion. The engine's verdict
is `PASS_RESEARCH_FRAMEWORK_CHAMPION_UNCHANGED`; the binding constraint is
**data density, not algorithm**. This panel is the data-acquisition program that
addresses that constraint.

Every panel feature must carry:

- `value`
- `source`
- `knowledge_time` (when the value was knowable/acquired)
- `evidence_class`
- `rights_status` / `commercial_use_status`
- `derivation_version`
- `missingness_state` (UNKNOWN, not zero)

Never take a present-day observation and pretend it existed historically.

## Priority order (what to acquire first)

### 1. Venue structure / capacity / geography — HIGHEST VOI

The champion currently proxies venue scale only via *prior outcomes*, which is
circular for cold venues. A venue-capacity band is the single most valuable
independent dimension.

- Source: existing venue master + Overture Maps Places (GeoParquet, query with
  DuckDB — do NOT download the whole country) + venue-specific research.
- Fields: `venue_key`, `lat`, `lon`, `capacity_claim`,
  `capacity_evidence_class`, `capacity_known_at`, `indoor/outdoor`,
  `h3_r5..h3_r8`, `market`, `dma/metro`.
- Rights: Overture is open data; venue claims need their own evidence class.
- Expected research coverage: high for major venues; cold venues remain sparse.
- Estimated cost: low-medium (DuckDB GeoParquet + targeted venue lookups).

### 2. Historical artist attention at cutoff

The engine currently knows nothing about how big an artist was *at the time of
a past booking*. Attention-at-cutoff is the key cold-start feature the baseline
flagged.

- Sources: Wikimedia pageviews (existing), ListenBrainz (existing), YouTube
  (existing) — all under current rights policies.
- Fields: `attention_T180`, `attention_T90`, `attention_T30` per source,
  `attention_known_at`.
- Rights: as per each source's current status.
- Expected coverage: good for recent events; weak pre-2015 (Wikimedia history
  is the deepest).

### 3. Competition around event date

- Sources: existing Ticketmaster forward watch + MusicBrainz event graph.
- Fields: same-day/same-week/same-market event counts, similar-artist event
  counts, venue competition.
- Rights: provider terms as already recorded.
- Expected coverage: good for 2024+; thin historically.

### 4. Market demographics / economics (vintage-stamped)

- Sources: Census ACS (API), FRED/ALFRED (vintages for historical values).
- Fields: population, age distribution, household income, college population,
  employment/unemployment, real-income trends, consumer conditions.
- Rights: public data; must preserve vintage (`knowledge_time`) for
  point-in-time correctness.
- Expected coverage: broad at MSA level; low at DMA level.

### 5. Tour context / lead time

- Sources: MusicBrainz tour/series graph (existing) + Ticketmaster.
- Fields: tour position, days between shows, announcement-to-event lead time.
- Rights: existing provider terms.
- Expected coverage: good where series links exist.

### 6. Prospective local POI context

- Sources: Overture Maps Places.
- Fields: hotels / bars-restaurants / nightlife / entertainment / transit POI
  density within 1km/3km/10km of venue.
- Rights: open data.
- Expected coverage: good for metro areas.
- This is the lowest priority for V1: it is a richer texture, not a first-order
  driver.

## Acquisition value function (VOI)

Rank the next acquisition with:

```
VOI = (expected coverage gain × decision importance × uniqueness × rights usability)
      ÷ (API cost + engineering cost + rate-limit cost)
```

Maintain a `coverage_heatmap(year × market × venue_class × artist_history_state)`
so the agent buys the highest-value missing cell next, not "more stuff."

## Parallel commercial-design-partner track

Free public sources will never provide enough **commercially usable settlement
outcomes**. Run this in parallel:

- Recruit a design partner; ingest real historical offers/outcomes (offer
  dates, capacities, guarantees, ticket sales, gross, costs, settlement) with
  PII excluded.
- This track has potentially higher marginal value than a dozen more public
  datasets and feeds eventual underwriting.

## Definition of done (this milestone)

- A versioned, PIT-safe feature table with `knowledge_time`/rights/missingness
  for every field.
- Coverage heatmap + VOI ranking.
- A re-run of `scripts/comparable_backtest.py` using the enriched panel, with
  the same "beat the champion consistently with uncertainty" bar.
- No ML, no vector search, no DuckDB VSS/HNSW unless exact search materially
  fails latency at the current corpus scale.
