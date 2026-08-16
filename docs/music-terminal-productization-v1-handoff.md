# MUSIC_TERMINAL_PRODUCTIZATION_V1 — Handoff Report

Branch: `feat/music-terminal-productization-v1`
Commit: `52ad3af` · PR: **#31** (draft) · Parent: PR #30 merged at `17b8df5`
Full machine report: `reports/music_terminal_productization_v1.json` (local, gitignored by convention)

---

## Executive verdict

**PARTIAL — strong infrastructure, real product surfaces, but not yet a daily-use product.**

What works today, end to end: search resolves a real artist (e.g. Billie Eilish) → ART page shows canonical identity (type, area, ISNI, IPI, sort name, life span), 12 cross-provider external IDs, and real ListenBrainz attention (2.4M listens). TODAY renders busy markets (Las Vegas 2362 / New York 2017 / Chicago 1061 events), attention movers, provider health. FEST/TOUR pages read from the materialized graph. Watchlists, saved monitors, and 40 deterministic NEW_EVENT alerts exist.

What's missing for a PASS: alert/ticketing sections are honest-but-empty (only NEW_EVENT alerts exist — a real Ticketmaster refresh that creates status/presale/price change alerts was never run against the productization branch), box-office columns on ART read 0, and performance p50/p95 was not measured.

---

## What was built (by phase)

| Phase | Result |
|---|---|
| 1. Artist master bootstrap | 113,040 canonical `core.artists` from distinct event-performer MBIDs; 430,623 external IDs |
| 2a. ListenBrainz bulk | 113,040 artists, 114 requests, 0 errors, 224,880 rows persisted (226,984 total obs) |
| 2b. ListenBrainz priority | 100 high-value artists × week/month/all_time, 300 ranges, 0 errors, 0 rate-limited |
| 3. AREA ingest | 120,050 areas (76,479 City, 32,561 Municipality, 255 Country, …), snapshot 20260815-001001, sha256 recorded |
| 4. Artist dump | 2,205,000 reference artists streamed from `artist.tar.xz` (lazy tar iteration — fixed a `getmembers()` O(n²) xz hang), no OOM |
| 4b. Enrichment | Backfilled ISNI/IPI (dump keys are `isnis`/`ipis`); 149,738 ISNI rows, 88,715 IPI rows in reference |
| 5. Industry identifiers | 317,477 URL rows + 27,297 ISNI + 10,978 IPI extracted onto 91,731 canonical artists; 430,623 external IDs total |
| 6. Spotify quarantine | 8 deprecated popularity columns registered (`core.deprecated_columns`), no fabricated writes |
| 7. TM resolution | 5,371 attractions → 3,747 MATCHED_ARTIST (70%), 576 AMBIGUOUS, 930 NO_MATCH, 118 REJECTED_NON_ARTIST; special classes: 58 collabs, 41 dance parties, 39 DJ events, 19 tribute acts, 13 festivals |
| 8. Identity QA | 13-sample deterministic set, precision 1.0, 0 false positives |
| 9. Festival read models | 1,919 series, 7,939 editions, 25,627 performer links, 1,801 places |
| 10. Tour read models | 3,179 series, 39,822 events, 9,284 performers, 8,206 venues, 2,854 markets |
| 11. Workflows | 3 default watchlists (180 items), 3 saved monitors, 40 NEW_EVENT alerts, TODAY view, DATA coverage |

## Data estate now

- Identity: 113,040 canonical artists (all with MBID), 26,253 with ISNI, 40,719 Wikidata, 23,781 YouTube, 101 Spotify (independently acquired)
- Reference: 2,205,000 artists, 120,050 areas, 124,404 events, 82,547 places, 6,228 series, 347,316 performers, 202,156 relationships
- Attention: 226,984 ListenBrainz rows (113,040 artists), 30 Wikimedia rows
- Live: 16,831 Ticketmaster snapshots / 14,023 events

## Key files

- `python/festival_bloomberg/oa/music_terminal_productization.py` — orchestration + report (incremental per-phase writes)
- `python/festival_bloomberg/identity/artist_master.py` — canonical bootstrap
- `python/festival_bloomberg/identity/ticketmaster_resolution.py` — deterministic resolution + QA
- `python/festival_bloomberg/musicbrainz/dumps.py` — streaming ingest/enrichment
- `python/festival_bloomberg/product/workflow.py` — watchlists, monitors, alerts, TODAY
- `schema/migrations/029_music_terminal_productization_v1.sql`
- `python/festival_bloomberg/intelligence/readmodels.py`, `terminal/server.py`, `apps/terminal/static/app.js` — search/ART/TODAY endpoints + SPA views

## Known gaps / notes for next AI

1. **Ticketmaster refresh never ran on this branch** — the 40 alerts are all NEW_EVENT from the earlier national collection. Run a quota-aware refresh to produce PRESALE/ONSALE/STATUS/PRICE alerts (phase 33 of the goal).
2. **Artist dump snapshot is hardcoded** `20260815-001001` in the OA (matches what was actually ingested); enrich/ingest are resumable via marker files.
3. **`getmembers()` on xz tar archives hangs** — always iterate members lazily (`for m in tf`), never `tf.getmembers()`.
4. **`CURRENT_TIMESTAMP` misparses** in DuckDB `ON CONFLICT DO UPDATE SET` — use `now()`.
5. **ListenBrainz attention keys are `mbid::<mbid>`**; read models now resolve them to canonical names.
6. **Tests**: Python 504 passed (1 pre-existing failure `test_forward_history` deselected — fails on clean main too); Node 76 passed; typecheck clean. Migration-count tests were updated for 029.
7. **`industry_identifiers` loop is slow** (~113k artists × writes) — ran in ~10 min chunks; the report phase now computes counts from the DB instead.
8. **Spotify** in 2026 Dev Mode no longer supplies popularity/followers — treated as identity/catalog only; attention lives in `metrics.artist_attention_observations`.
9. Performance p50/p95 and full manual UI QA were not completed — see the milestone goal sections 34/35.
