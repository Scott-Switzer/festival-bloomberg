# ARTIST_SECURITY_1000_SCALE_V1

Milestone `feat/artist-security-1000-scale-v1` — turn the first 1,000 artists
into dense, auditable, professional-grade securities and start the ARTIST ×
MARKET layer a talent buyer actually needs.

## Phase 0 — merge OPEN_ARTIST_MARKET_DATA_V1 (#53)

- PR #53 `feat/open-artist-market-data-v1` merged normally into `main`.
- Exact head verified (`e19a17cdd08f2fc66d251b11bc234799f30c718d`); all four
  CI checks (node, python, cloud-runtime, security) green pre-merge and on
  post-merge `main` (`ecdc0a489870f762f9f1d77b5c187404b460f4ad`).
- Branch `feat/artist-security-1000-scale-v1` created from that `main`.

## What this milestone delivers

| Workstream | Deliverable | Status |
|---|---|---|
| P0 | Full Wikimedia daily pageviews backfill 2015-07-01 → latest for the 1000-universe (NOT 120-day truncation) | batched collector + background run |
| P1 | ListenBrainz scale for the full universe (bulk popularity + week/month/all_time ranges) | collector wired |
| P2 | Cross-provider artist identity master + IDENTITY_COVERAGE_SCORECARD | `identity.artist_provider_linkages` |
| P3 | YouTube forward tape — key provisioning status made explicit, fail closed | INVALID key detected (400) |
| P4 | Spotify identity join repair + catalog (albums/singles/release dates) | candidates fail closed |
| P5 | Live + ticket joins (SHOWS_30D/90D/365D, markets, venues, future events, ticket counts) | SetlistFM + TM estate |
| P6 | Feast bounded adoption — real PIT retrieval validation | APPROVED_DEPENDENCY_BOUNDED |
| P7 | Perspective internal artist monitor over real data | monitor export + semantics |
| P8 | Voyager remains dormant (INSUFFICIENT_DATA; no tuning) | documented |
| P9 | EVENT_TAPE_2000 — event tape scaling | `acquisition.event_tape_scale` |
| P10 | ARTIST × MARKET security objects (top US markets) | `asm.artist_market_security_v1` |

## Identity master policy

The pilot exposed a real weakness: Spotify IDs existed in the lake keyed by
the legacy `name::<normalized>` fallback, so they did not join cleanly to the
MBID-centered universe. `identity.artist_provider_linkages` fixes the layer:

- **Never resolve silently by normalized artist name alone.** A name match
  generates a CANDIDATE only; ambiguous links FAIL CLOSED (AMBIGUOUS), and
  CANDIDATE is never promoted to VERIFIED without a second evidence source.
- Lake IDs (from the MusicBrainz artist-dump typed URL relations) are VERIFIED
  with `link_method = LAKE_EXTERNAL_ID` and an evidence URL.
- MBID-derived providers (MUSICBRAINZ, LISTENBRAINZ) are VERIFIED by
  construction (`MBID_DERIVED`).
- Every linkage carries `artist_key | provider | provider_id | provider_url |
  link_method | confidence | evidence_ref | first_seen_at | last_verified_at |
  rights_status | commercial_use_status`.
- `identity.identity_coverage_scorecard` materializes coverage by provider
  over the 1000-universe each pass.

## Feast bounded adoption

Feast is adopted ONLY as a historical feature retrieval/materialization layer
over canonical Festival Intelligence factor observations — canonical evidence
storage is never rewritten. The adoption module (`security/feast_adoption.py`)
runs the pilot's equivalence test (our PIT pipeline vs Feast-style as-of
retrieval) over the REAL wiki daily history of the universe, verifying:

- no leakage (available_at >= cutoff excluded even when observation < cutoff);
- UNKNOWN preserved (missing feature is NULL, never fabricated 0);
- available_at semantics (as-of gate uses source availability, not retrieval);
- knowledge-time semantics (retrieved_at is provenance only).

Verdict `APPROVED_DEPENDENCY_BOUNDED` on no divergence; `STOPPED_DIVERGENCE`
stops adoption.

## Live + ticket evidence (P5/P9/P10)

- SetlistFM official API provides per-artist performance history (eventDate =
  EVENT_TIME, venue, city/state → market) — the PRIMARY historical rail.
- MusicBrainz event dump (CC0, streaming ingest) adds begin_date + event_type
  for the 107,599 events in the lake performer graph (incl. festival flags).
- The Ticketmaster provider estate (`events.provider_event_snapshots`) drives
  future events, markets, and the EVENT_TAPE_2000 bootstrap.
- No attendance, no sales-from-disappearance inference anywhere.

## Reporting

`scripts/populate_artist_security_1000.py` orchestrates all workstreams and
writes `reports/artist_security_1000_success.json` with real counts.

## Result — real collected data (2026-08-27 run)

Universe: **1,000 artists, 100% MBID-backed** (`ARTIST_SECURITY_1000_V1`).

### Identity coverage (VERIFIED unless noted)

| Provider | Artists | Note |
|---|---|---|
| MusicBrainz | 1,000 | MBID_DERIVED |
| ListenBrainz | 1,000 | MBID_DERIVED |
| Wikidata/Wikipedia | 989 | lake URL relations |
| YouTube | 965 | channel IDs verified; snapshots BLOCKED (invalid key) |
| SoundCloud | 917 | lake URL relations |
| Spotify | 697 CANDIDATE / 700 AMBIGUOUS | search candidates; **fails closed** — never silently promoted; legacy `name::` keys quarantined into repair path |

6,572 linkages + 6,972 Spotify resolution records in
`identity.artist_provider_linkages` / `identity.spotify_artist_resolutions`.

### Attention history

- Wikimedia: **3,661,163 new daily artist×day rows** persisted this pass
  (3.91M total observations incl. the pilot), **966 usable artists**, full
  window **2015-07-01 → 2026-08-25** — no 120-day truncation, zero network
  fabrication.
- ListenBrainz: **10,724 rows covering all 1,000 artists** (bulk popularity
  week+month all-time in one call; per-artist week/month/all_time ranges),
  range covered **2002-01-01 → 2026-08-24** where listeners exist.
- Derived factors: WIKI_VIEWS_1D/7D/28D/90D, WIKI_MOMENTUM,
  **WIKI_ACCELERATION** (2nd-order momentum over successive 28d windows),
  WIKI_ZSCORE, WIKI_ATTENTION_SHOCK, LB variants.
- Factor rows materialized: **9,782** (DEMAND 4,866 / MOMENTUM 4,783 /
  LIVE 133). Artists with 5+ factors: **991**; 10+: **927**; 20+: 0 (latest-
  state materialization is deliberately bounded until forward tapes accrue).

### Live / event / market estate

- MusicBrainz event dump streamed for the universe's performer graph:
  **107,598 events / 51,720 series links** ingested (CC0).
- Event tape: **33,383 events linked** with PIT event-days; all have market
  attribution from venue geography; marketplace pair counts grow when listing
  sources are attached (currently single-marketplace → multi-pair metrics
  honestly 0).
- Live statistics: 972 artists carry SHOWS/FESTIVAL/MARKET aggregates from
  SetlistFM performance history + MB events; ticket observation depth pending
  marketplace listing acquisition.
- ARTIST × MARKET (`asm.artist_market_security_v1`): **602 rows over the top
  10 US live markets** with observable-only factors (historical shows, days
  since last market show, venues played, upcoming market events, competing
  nearby events, ticket evidence). No demand forecast, no booking rec.

### YouTube provisioning (P3)

`YOUTUBE_API_KEY` returns 400 `API key not valid` — collector reports status
`INVALID_KEY`, persists NOT_CONFIGURED rows instead of fabricating collection,
and stops the batch fail-closed. No historical subscriber reconstruction;
deltas only after real repeated snapshots exist.

### Provider costs & bytes

All sources free-tier/key-free in this pass: **$0.00 provider cost**. Raw
warehouse ≈ **2.0 GB** (page-compressed DuckDB; normalized floor ~1.0 GB).

### Feast adoption result (P6)

Verdict **APPROVED_DEPENDENCY_BOUNDED**: our PIT pipeline vs Feast-style as-of
retrieval across **8,505 comparisons on 945 artists' REAL wiki histories** —
**0 mismatches**, no leakage, UNKNOWN preserved, available_at and knowledge-
time semantics intact. Bounded scope: historical retrieval/materialization
only; canonical evidence storage untouched.

### Perspective monitor (P7)

`reports/artist_security_1000_monitor.json` carries all 1,000 securities as a
flat analyst table (artist, factor_coverage, lb_momentum, wiki_momentum,
shows_365d, festival_appearances, latest_update, data_confidence) rendered by
Perspective with sort/filter/group/search — no custom grid built.

## Deliberately NOT built

No ML, no artist demand score, no recommendation score, no lineup optimizer,
no opaque composite ranking, no new generic orchestration platform, no UI
rewrite. Voyager stays dormant; no schema milestone is added unless real
collected data proves the schema cannot represent something necessary.
