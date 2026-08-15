# LIVE_DATA_ACTIVATION_AND_INTELLIGENCE_SCALE_V1

Turn already-validated providers into persisted, source-backed data — measured
by NEW QUERYABLE FACTS, not by classes written or APIs pinged.

## What was built

### 1. NVIDIA router defect fixed (`intelligence/llm.py`)

The router previously seeded `tasks = DEFAULT_TASKS`, so the "explicit
override" branch always matched and the catalog-aware hint matching was never
reached — it could emit a model id absent from the live catalog. Now:

1. explicit overrides (only honored when catalog-valid)
2. catalog candidates (hint-matched against the LIVE catalog)
3. fallback defaults (only if present in the live catalog)
4. `UNAVAILABLE` — fail closed, never invent a model id

`chat()` / `embed()` load the catalog once and refuse to issue an off-catalog
id. Live benchmark: `DEEP_REASON` → `deepseek-ai/deepseek-v4-flash-0731`
(chat 200), `EMBED` → `nvidia/nv-embedqa-e5-v5` (1024-dim, 200), `RERANK` →
`UNAVAILABLE` (no rerank model in the account — honest). The embed hint order
was fixed so `nvidia/nv-embedqa-e5-v5` wins over the non-deployable
`nvidia/llama-3.2-nv-embedqa-1b-v1`.

### 2. Spotify identity resolution (bounded, deterministic)

`identity/spotify.py` + `oa/live_data_activation.py` resolve the
festival-seed / box-office / forward artist names against the Web API and
persist append-only rows to `identity.spotify_artist_resolutions`
(`EXACT` / `HIGH_CONFIDENCE` / `AMBIGUOUS` / `NO_MATCH`). Only EXACT
normalized-name matches are promoted to `core.entity_external_ids`
(`id_type='spotify'`, keyed `name::<normalized>`). Nothing is force-merged on
string similarity alone. **Live: 120 names searched, 103 resolved, 101
artists now carry a Spotify external id.**

### 3. Ticketmaster US music acquisition (bounded)

`_search_events` now accepts `classificationName` + `startDateTime`. The OA
runs 5 US market partitions (Chicago/LA/NY/Austin/Nashville) filtered to Music
and appends every event snapshot (status, public onsale, independent presales,
price range, promoter, classification, venue coordinates) to
`events.provider_event_snapshots`. `offsale != sold_out`; a price range is an
observation. **Live: 201 distinct events across 5 markets, 200 with public
onsale, 49 with presale, 84 with price range, 83 with promoter.**

### 4. NWS weather attached to real events

`events.weather_forecast_snapshots` key forecast snapshots to future US events
with coordinates. Forecast generation time is kept separate from the validity
window. **Live: 12 events enriched with forecasts.**

### 5. Activity tape from provider snapshots

`derive_provider_event_tape_entries` emits `EVENT_DISCOVERED`,
`ONSALE_DISCOVERED`, `PRESALE_DISCOVERED`, `PRICE_RANGE_DISCOVERED`,
`PROMOTER_IDENTIFIED`, and cancellation/postponement/reschedule transitions.
An unchanged poll emits nothing; re-derivation is idempotent.

### 6. Terminal

- `/api/status` — recently changed events (cancellations, onsales, prices,
  promoters).
- `/api/events/live` — latest Ticketmaster snapshots.
- STATUS view in the SPA; artist page now links to Spotify when resolved.

## Migration

- **024** `live_data_activation_v1` — `identity.spotify_artist_resolutions`,
  `events.provider_event_snapshots`, `events.weather_forecast_snapshots`.

## Verdict

`LIVE_DATA_ACTIVATION_AND_INTELLIGENCE_SCALE_V1 = PARTIAL` (toward PASS)

- NVIDIA routing + live chat/embed: **PASS** (rerank honestly UNAVAILABLE).
- Spotify identity resolution: **PASS** (bounded, 101 external ids).
- Ticketmaster US music: **PASS** (bounded, 201 events, 5 markets).
- NWS weather: **PASS** (bounded, 12 events).
- ListenBrainz / GDELT / Census: **NOT_IMPLEMENTED** — public sources remain
  scaffolds; the remaining binding edge is wiring them into the same
  append-only read models.

## Recommended next milestone

Wire ListenBrainz (CC0 attention sample) + GDELT (metadata-only news) into the
existing public-provider scaffolds and extend the bounded Ticketmaster sweep
to full DMA partitions, populating the same snapshots/tape/read models.
