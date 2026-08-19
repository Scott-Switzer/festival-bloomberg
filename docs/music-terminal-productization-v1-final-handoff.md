# Music Terminal Productization V1 — Final Handoff

Date: 2026-08-18 (late-night closure run)
Repo: `Scott-Switzer/festival-bloomberg`
PR: **#31** `feat/music-terminal-productization-v1` — **OPEN / DRAFT / MERGEABLE**

> This document is the definitive handoff. Everything below was verified against the
> live warehouse, the real GitHub state, and exact-head CI — not reconstructed from memory.

---

## 1. Git / GitHub state (verified)

- **HEAD: `fb58b97`** `feat: artist search index (terms + DuckDB FTS), phase ledger, collision audit wiring`
- Remote branch == local HEAD (`fb58b9736a965d3d3de8f4858153d6885803609c`), working tree **clean**, everything pushed.
- PR #31: **OPEN, DRAFT, MERGEABLE** — https://github.com/Scott-Switzer/festival-bloomberg/pull/31
- **Exact-head CI run `32209602970` = ALL GREEN**: python ✅ (2m40s), node ✅ (24s), security ✅ (6s).
- Merge was deliberately **NOT performed**: several closure gates are genuinely unfinished
  (see §9). PR body was updated at `c277d0a` and needs one final refresh with the numbers in this doc.

Commit chain (oldest → newest):
`711b109` P0 audit fixes · `3e3edec` PIT determinism fix (real bug: `_two_snapshot_pit` ignored `oa_started`) ·
`25dc467` promote 1,127 reference-matched artists + behavioral suite · `c277d0a` hardening status ·
`466c959` deterministic canonical artist names (removed `ARBITRARY()`) · `273458d` alert related-entity graph +
explicit acquisition runs + personalized TODAY · `f4f7518` MBID-ground-truth identity QA + collision ledger ·
`fb58b97` artist search index (DuckDB FTS) + phase ledger + collision audit wiring

---

## 2. What was built in this closure run (commits `c277d0a`…`fb58b97`)

### 2.1 Deterministic canonical artist names (`466c959`)
`collect_performer_mbids()` no longer uses `ARBITRARY(artist_name)`. Preference order:
reference `musicbrainz_artists.name` → most-frequent event credit → lexicographic tie-break → MBID fallback.
All alternate credits preserved as aliases. Row-order determinism tests added. Live backfill verified:
0 updates needed (live data has one credit per MBID; fix protects future ingests).

### 2.2 MBID-ground-truth identity QA (`f4f7518`)
- QA sample rebuilt from **`core.event_performers` evidence** — every expected MBID is the MBID
  MusicBrainz event relations actually use (several hand-typed MBIDs were wrong, e.g. Queen, Miley Cyrus,
  KISS — the QA caught its own fixtures).
- 59 cases, all event-verified: **52 TP, 0 FP, 6 TN, 0 FN, 1 ambiguous** (The Killers — correctly conservative).
- **Precision 1.0, false-positive rate 0.0.** Recall 0.98 (1 correctly-unmatched).
- New tribute-act reject gate (strong signals: tribute/tribute to/karaoke/"rumours of"; `orchestra` kept as weak
  feature because Trans-Siberian Orchestra et al. are real artists). "Dead & Company" correctly NOT classified
  as a tribute (Grateful Dead members + Oteil Burbridge — real band).

### 2.3 External-ID collision ledger (`f4f7518` + `fb58b97`)
- YouTube external-ID extractor fixed: was taking the last URL path segment
  (`youtube.com/featured` → `"featured"`); now only accepts real channel IDs/handles.
- Collision audit persisted to `core.identity_conflicts`: **177 conflicts now visible**
  (previously silently ignored). Includes genuine Wikidata collisions and legacy junk-YouTube rows.
- Regression tests for the extractor and the audit.

### 2.4 Alert related-entity graph + acquisition runs + personalized TODAY (`273458d`)
- Migration **030**: `core.alert_related_entities` (alert_key, entity_type, entity_key, relationship) and
  `audit.provider_acquisition_runs` (run_id, provider, operation, started_at, completed_at, status, counts, software_version).
- Every Ticketmaster observation now carries `acquisition_run_id`; alert logic compares
  LATEST COMPLETE run vs PREVIOUS COMPLETE run (not inferred timestamp batches).
- `generate_new_event_alerts` is run-aware: NEW_EVENT = present in latest run, absent from ALL previous runs.
  Initial load produces no fake NEW_EVENT.
- `build_today` watchlist section now answers "what changed for things I follow" via related entities;
  global live-market context is a separate section.
- TODAY API contract normalized: flat typed fields (`alert_key, alert_type, observed_at, provider,
  event_key/name, artist_key/name, venue_key/name, market_key/name, presale_start, onsale_start,
  old/new_status, old/new price min/max`) instead of opaque `detail` JSON the SPA had to guess at.

### 2.5 Editable watchlist SPA + box-office linkage (`273458d`)
- SPA: `api()` helper now supports POST; WATCHLIST page supports create + add/remove item;
  ART page has "Add to watchlist" control. Watchlists persist through reload (backend was already durable).
- Box-office ART bug fixed: `history`/`outcomes` queried `WHERE lower(artist) = artist_key`
  (`mbid::…` can never equal a name) → always empty → false zero. Now resolves canonical name + aliases
  (`reference.musicbrainz_artists.aliases` JSON, NULL-safe). Verified live: Guns N' Roses → 12 real engagements.

### 2.6 Search index — DuckDB FTS (`fb58b97`)
- Migration **031**: `reference.artist_search_terms` (artist_mbid, artist_key, term, normalized_term,
  term_type CANONICAL_NAME/SORT_NAME/ALIAS, normalization_version) — **4.87M terms / 2.2M artists**;
  DuckDB FTS v2 index (`fts_reference_artist_search_terms.match_bm25`); name index on `core.artists`.
- Search hierarchy: exact external ID → exact canonical → exact alias → normalized exact → FTS BM25.
  Fuzzy/FTS never defines identity.
- **Measured (warm, live warehouse): p50 76 ms, p95 117 ms** (target < 500 ms).

### 2.7 Phase execution ledger (`fb58b97`)
- Migration **032**: `audit.pipeline_phase_runs` (run_id, milestone, phase, source_snapshot,
  software_version, input_fingerprint, started_at, completed_at, status, rows_read, rows_written,
  duration_seconds, checkpoint, error_code, error_message).
- OA `_run` wrapper records every phase; same snapshot + same version + COMPLETE ⇒ SKIP.
  Reports are reconstructable from warehouse + ledger without re-streaming archives.

### 2.8 Real Ticketmaster refresh (no commit — run data lives in warehouse)
- Quota-aware refresh: 5 markets, 40 events each → **200 events persisted, 0 rate limits, 0 errors**.
- Alerts: **194 NEW_EVENT** (genuinely new events, run-aware) + **3 change alerts** (2 price, 1 status).
- Related-entity graph: **197 alerts carry EVENT/ARTIST/VENUE/MARKET/PROMOTER edges; 154 link to
  resolved canonical artists** — personalized TODAY can surface them.

### 2.9 Page performance (warm, live warehouse)
| Surface | p50 | p95 | Target | Verdict |
|---|---|---|---|---|
| Universal search | 76 ms | 117 ms | 500 ms | ✅ |
| ART read model | ~ | 17 ms | 750 ms | ✅ |
| TODAY | ~ | 10 ms | 750 ms | ✅ |
| FEST / TOUR / DATA | — | — | 1 s | not re-measured this run |

---

## 3. Test gate (final state)

- **Python: 535 passed, 1 skipped** (was 505+1 at audit time; +30 new behavioral tests: determinism,
  run-aware NEW_EVENT, related entities, personalized TODAY, search index, phase ledger, collision audit,
  tribute rejection, watchlist CRUD, ListenBrainz temporal keys, change-then-revert, presale detection).
- **Node: 76 passed** (migration-count tests updated for 032). **Typecheck: clean.** **Security: green.**

---

## 4. Live data estate (verified 2026-08-18)

- `core.artists` — **114,167 canonical artists** (incl. 1,127 promoted reference `MATCHED_ARTIST` rows;
  0 MATCHED_ARTIST rows with NULL artist_key)
- `reference.musicbrainz_artists` — **2.2M** artists; `reference.artist_search_terms` — **4.87M**
- `metrics.artist_attention_observations` — 226k+ (ListenBrainz total-listen/user-count, temporal keys)
- `core.identity_conflicts` — 177; `core.alerts` + `core.alert_related_entities` — populated from real refresh
- `audit.pipeline_phase_runs` + `audit.provider_acquisition_runs` — active
- Ticketmaster: 200 new events this run; ~14k existing universe
- MusicBrainz graph: 124,404 events / 6,228 series / 82,547 places / 347,316 performer relations (unchanged)

---

## 5. Product acceptance

- Terminal server live (`127.0.0.1:8931`, launchd pid 43179): TODAY API 200 (49 KB), search 200
  (Billie Eilish → real MBID `f4abc0b5-3f7a-4eff-8f78-ac078dbce533`).
- SPA renders TODAY with real activity tape from the refresh (EVENT_DISCOVERED / ONSALE_DISCOVERED /
  PRESALE_DISCOVERED / PROMOTER_IDENTIFIED rows, all timestamped).
- Verdict: **PARTIAL** — the terminal is genuinely usable for monitoring, but full acceptance
  (watchlist round-trip in SPA against a named list, FEST/TOUR/MKT page-by-page click-through,
  ASK cross-domain queries) was not completed this run.

---

## 6. Semantics preserved

UNKNOWN ≠ 0 · event_time ≠ knowledge_time · attention ≠ demand · external ID ≠ canonical ID ·
ambiguity ≠ match · initial load ≠ NEW_EVENT · fuzzy search never defines identity ·
LLM never commits identity · zero historical observations ≠ zero gross/tickets.

## 7. Rights / licenses

No new dependencies. No source dumps committed. No secrets touched. All data from existing
licensed/accepted providers (Ticketmaster API, MusicBrainz dumps, ListenBrainz, Wikimedia).

---

## 8. Remaining gates before merge (honest list)

1. **SPA acceptance round-trip**: create "2027 Talent Targets", add/remove artists, reload persistence.
2. **FEST / TOUR / MKT / DATA / ASK page smoke test** + p95 measurement (targets ≤1 s).
3. **FINAL PR-body refresh** with §2/§3/§4 numbers, then mark ready + merge + post-merge main CI.
4. (Post-PR31, per plan) Splink shadow identity challenger, Memray profiling, Dagster/OpenLineage
   wrapper, H3 cells, dlt A/B — these belong on a NEW branch, not PR #31.

## 9. Recommended next milestone

**`OSS_PLATFORM_ADOPTION_V1`** — merge PR #31, then wrap existing providers in Dagster assets
(warehouse stays authoritative; `provider_acquisition_runs` links Dagster run IDs), add OpenLineage
facets for evidence/PIT semantics, pilot Crawlee on one rights-approved source, and run the Splink
shadow identity challenger against the 59-case MBID fixture. Do NOT rebuild acquisition semantics.
