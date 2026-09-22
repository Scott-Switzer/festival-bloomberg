# Market Timing + Identity V1 — implementation report

Milestone: `MARKET_TIMING_AND_IDENTITY_V1`
Branch: `feat/market-timing-identity-v1` (from `679d284`)
Date: 2026-09-22. No secrets in this document.

Buyer goal: when did this artist last play this market, on what evidence,
and which same-named artist am I selecting.

## 1. Root causes (traced, not guessed)

**last_play/first_play NULL everywhere — SERVING_NOT_MATERIALIZED.**
`scripts/build_talent_buyer_terminal_v1.py::_materialize_markets` inserts
`None, None, None, None, None` for
(venue_count, first_play_date, last_play_date, future_events,
ticket_evidence_count) with the literal explanation "dates, venues, tickets
and future activity remain UNKNOWN here". The estate report that feeds it
keeps only `{market, shows}` counts (`event_performances: 1450` is an int);
per-event dates never survive into the estate JSON. All 27,322 serving
`artist_markets` rows have NULL first/last play. Read models, APIs, and UI
already projected the columns — the data was simply never filled.

**observed_shows NULL for specific pairs (Alice Cooper × Chicago) —
MARKET_MAPPING_MISSING.** No `chicago-il` link exists in Alice's 57 estate
markets (top: phoenix-az 16, columbus-oh 12). The estate's MB-place
attribution found no Chicago evidence mappable through
`CITY_MARKET_MAP`; NULL here is the honest UNKNOWN, not a bug to patch
over. No link is invented by this milestone.

**Duplicate Alice Coopers — INSUFFICIENT_EVIDENCE for merge.**
`mbid::ee58c59f` (HOT_1000, 1450 events, 57 markets, 11 festivals, 3 YouTube
channels) vs `mbid::4d7928cd` (COVERAGE_25000, 8 events, 4 markets, shares 1
YouTube channel). Both have NULL type/area/disambiguation/aliases upstream.
Distinct MBIDs, 180× event-scale gap, shared channel → person-vs-group or
alias-collision pattern, but no merge-grade evidence. NO auto-merge.

## 2. Market-timing contract (implemented)

Per ARTIST × MARKET link, from admissible dated event evidence only:
`observed_shows` = estate count, untouched. `first_play_date` =
MIN(event_date ≤ as_of), `last_play_date` = MAX(event_date ≤ as_of),
`future_events` = count(event_date > as_of) from serving future rows.
Future never becomes last_play. No link invented (row count gate
unchanged). Missing → NULL (UNKNOWN, never 0/"never played").

## 3. Changes

**Serving build (shared by snapshot + R2-parquet paths):**
`scripts/build_talent_buyer_terminal_v1.py`: `create_area_market_map`
(SQL mirror of the canonical Python city/state maps),
`fill_market_timing_from_evidence` (UPDATE existing links only, as_of
gated, explanation suffix), `fill_market_futures` (from serving
future_events city→slug), `_mb_timing_evidence` (MB strategies 1+2, pure
read), `_boxoffice_timing_evidence` (engagements city/market→slug),
`_materialize_market_timing` wired post-future with validation stats.
`python/festival_bloomberg/cloud/batch_jobs.py`: R2-parquet evidence SQL
(events→edges→place_edges→venues.area_name) + futures fill in the nightly
`terminal_serving_build_v1` path; counts reported to rows_honest.

**API:** `/api/market/<unknown>` → HTTP 404 `{"error":"not found"}`
(no more synthesized "Nope Not A, MARKET"). `/api/search` items gain
`artist_type`, `area`, `historical_event_count`, `market_count`
(stored facts only; NULLs stay NULL).

**Terminal UI:** searches navigate to `#/search?q=…` (bookmarkable,
reload/back/forward-safe, still routed through the PR #79 stale guards);
results render a disambiguation line (e.g. "1,450 observed events ·
57 markets"); compare picker shows the same; artist markets table gains
First played; unknown-market page says "Market not found."

## 4. Coverage before/after

| metric | before | after (code) | after (data, post-nightly) |
|---|---|---|---|
| artist-market rows | 27,322 | 27,322 (unchanged) | same |
| rows observed_shows known | 27,322 (100%) | unchanged | unchanged |
| rows first_play known | 0 (0%) | fill on nightly build | verified §7 |
| rows last_play known | 0 (0%) | fill on nightly build | verified §7 |
| duplicate names with disambiguation | 0 | all (counts+tier) | live §7 |
| invalid-market behavior | fabricated name, 200 | 404 + "Market not found." | live §7 |

Alice Cooper fixture: HOT_1000 identity (57 markets, 1450 events) opens
from disambiguated search; per-market first/last populate from MB-dated
evidence where place areas map; Chicago remains UNKNOWN (no estate link —
documented gap, not patched).

## 5. Identity audit

25,000 artist records / 24,933 unique normalized names / **66 colliding
names** (65 two-way, 1 three-way). Dominant pattern matches Alice Cooper:
one HOT_1000/CORE_5000 high-coverage identity plus a near-empty
COVERAGE_25000 doppelganger (ghost, marilyn manson, fergie, chris brown,
goose, plan b…). Top-50 reviewed: same shape throughout (full table in
CI artifacts of the audit run). **Zero merges performed** — no
type/area/disambiguation evidence upstream; ambiguity is now visible in
search instead of hidden. Cheap deterministic resolution (shared YouTube
channel analysis, alias-graph review) is queued, not attempted.

## 6. Tests

`test_market_timing_v1.py` (mapping incl. unicode/state fallback, first/last
+ dedup, future exclusion, future-only UNKNOWN, no invented links, futures
separation); `test_market_identity_api_v1.py` (market 200+timing, market
404, duplicate-name metadata); `test_terminal_search_routing.py` (hash
routing, disambiguation render, first-play column, not-found copy).
All green locally; full gates via PR CI.

## 7. Production verification

Runtime (mvp_server + SPA) deploys through `terminal-production.yml`;
CI authorized UAT re-proves home/search/artist/market/compare/underwrite
with zero console errors. Serving data rides the nightly
`terminal_serving_build_v1` + hosted promotion (automatic, no runtime
redeploy needed for data). Post-land verification: R2 CURRENT generation
advance → download generation artifact (read-only) → SQL-assert Alice
first/last populated + row-count gate intact → production browser spot
check (search disambiguation, market 404, back/forward, no stale
regressions). Statuses recorded in the PR.

## 8. Remaining limitations

* Pairs with no estate link (Alice×Chicago) stay UNKNOWN until the estate
  attribution covers boxoffice-family markets — next data milestone.
* Boxoffice-family dates feed the snapshot path only (cloud inputs are
  MB-only); venue_count/ticket counts remain NULL honestly.
* Identity collisions unresolved by design; merge-grade evidence rules
  still needed.
* First-play for capped histories derives at build (uncapped src), not
  from the 250-row serving cap — correct by construction.
