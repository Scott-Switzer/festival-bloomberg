# MUSIC_REFERENCE_GRAPH_AND_PRO_WORKFLOW_V1

**Verdict: PARTIAL (the series spine is now a connected event/festival/tour/
performer/venue graph from real MusicBrainz CC0 data; MBID resolution of the
live artist universe, ListenBrainz live coverage, and the monitor/workflow
layer remain).**

## What changed

### 1. Migration 028 — series shells become a graph

Migration 027 left 6,228 canonical event series mostly unconnected. Migration
028 adds the materialized link tables that turn them into a queryable graph:

- `raw.musicbrainz_event` — full event observations (payload preserved).
- `raw.musicbrainz_place` — full place observations (payload preserved).
- `core.series_events` — series → event membership (festival/tour).
- `core.event_performers` — event → artist with **role semantics preserved**
  (`main performer` / `support act` / `guest performer` / `host` /
  `conductor` … — never collapsed to "artist appeared").

Typed edges (`EVENT_AT_PLACE`, `EVENT_PART_OF_EVENT`, `EVENT_HAS_URL`,
`PLACE_IN_AREA`) continue to flow through `core.entity_relationships` with
`evidence_class = CROWD_CURATED_REFERENCE` — MusicBrainz never silently
upgrades research seeds to primary-source evidence.

### 2. MusicBrainz event dump ingest

Authoritative bounded run against snapshot `20260815-001001`
(`event.tar.xz`, 47.9 MB → 532 MB NDJSON, 124,404 events):

| Result | Count |
| --- | --- |
| events persisted (raw) | 124,404 |
| ├ Concert | 81,968 |
| ├ Festival | 31,376 |
| ├ Convention/Expo | 1,223 |
| └ other typed events | 1,847 + 6,990 untyped |
| **series → event links** | **59,406** |
| **event → performer relations** | **347,316** |
| ├ main performer | 277,173 |
| ├ support act | 46,933 |
| ├ guest performer | 10,150 |
| └ host / conductor / VJ / … | 13,060 |

Series linkage by type: **TOUR 2,627 · FESTIVAL 1,779 · EVENT_SERIES 1,017 ·
RUN 243 · RESIDENCY 37**.

### 3. MusicBrainz place dump ingest

`place.tar.xz` (148.1 MB, 82,547 places) → `raw.musicbrainz_place` +
canonical `core.venues` keyed by MBID (82,547 venues). Venue rows carry
name/type/address/coordinates; **capacity and country are NOT fabricated**
(area name alone is not country evidence). Invalid coordinates in the
crowdsourced dump (e.g. longitude `-73991593.99`) are stored as NULL, never
clamped into something plausible.

### 4. ListenBrainz bulk popularity

Added the documented batch endpoint (`POST /1/popularity/artist`, up to 1,000
MBIDs/request) as `fetch_artist_popularity` / `collect_artist_popularity`,
persisting `LISTENBRAINZ_TOTAL_LISTEN_COUNT` / `LISTENBRAINZ_TOTAL_USER_COUNT`
as `ATTENTION_CONSUMPTION_SAMPLE`. Missing → NULL, never zero. (No live data
yet: the warehouse still has 0 resolved MBIDs — that is the next milestone's
identity-resolution work.)

## Negative results (not hidden)

- **MBID resolution for the ~14k Ticketmaster attractions not started** — the
  artist dump (~1.7 GB) is still deferred; ListenBrainz therefore has nothing
  to query yet.
- **Monitoring layer (watchlists / saved views / alerts / TODAY) not built.**
- **Place → area ingest is bounded to the area fields embedded in the place
  dump**; the standalone area dump is not ingested yet.

## Tests / CI

- Python **510 passed, 1 skipped** (8 new music-reference-graph regressions)
- Node 76/76, typecheck clean, gitleaks clean.

## Next binding edge

Ingest the MusicBrainz `artist` dump, build the local artist resolution index,
and resolve the ~14k Ticketmaster attractions + box-office artists to MBIDs;
that turns ListenBrainz live and makes the entity master a real cross-provider
linkage graph.
