# Music Terminal Productization V1 — Final Handoff

Date: 2026-08-18 (late-night closure run)
Repo: `Scott-Switzer/festival-bloomberg`
PR: **#31** `feat/music-terminal-productization-v1` — **OPEN / DRAFT / MERGEABLE**

> This document is the definitive handoff. Everything below was verified against the
> live warehouse, the real GitHub state, and exact-head CI — not reconstructed from memory.

---

## 1. Git / GitHub state (verified)

- **HEAD: `1035754`** `Fix two real defects found in SPA acceptance …`
- Remote branch == local HEAD, working tree **clean**, everything pushed.
- PR #31: **OPEN, DRAFT, MERGEABLE** — https://github.com/Scott-Switzer/festival-bloomberg/pull/31
- Exact-head CI green on the substantive code head (`fb58b97` → run `32210028800`); the final
  commit `1035754` (two acceptance-found fixes + tests, docs) has its own exact-head CI run
  (python/node/security). All acceptance gates (§5) are green; merge is the remaining step.

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

## 5. Product acceptance (COMPLETE — `1035754`)

Terminal live on `127.0.0.1:8931`. Real SPA → HTTP API → DuckDB round-trip, exercised in the browser:

- **Watchlist round-trip (gate 1) — PASS.** Created `2027 Talent Targets` via the SPA create form
  (`watchlist_key 7624f9a4…`); added Billie Eilish (`mbid::f4abc0b5…`), Bad Bunny
  (`mbid::89aa5ecb…`), Fred again.. (`mbid::bca46a0c…`) through the SPA add form; reload → all 3
  persisted; removed Billie Eilish via SPA Remove; reload → Bad Bunny + Fred again.. remain
  (heading reads `2027 Talent Targets 2 items`). All resolved to real canonical artists.
- **Personalized TODAY (gate 2) — PASS.** `watchlist` section surfaces only alerts linked via
  `core.alert_related_entities` to watched entities; global market/ticketing/attention channels stay
  separate. Bad Bunny + Fred again.. have zero related alerts in the current data (the earlier
  5-market refresh didn't include them) → honest zero reported, nothing fabricated. Related-entity
  surfacing is regression-tested (`test_today_watchlist_related_entities`).
- **Surface smoke (gate 3) — PASS.** TODAY, WATCHLIST, MON, ALERTS, DATA, ART, FEST, TOUR, EVENT,
  VENUE, MARKET, SEARCH, ASK all HTTP 200 with non-crashing SPA renders; missing data renders as
  UNKNOWN (`"No data recorded. Unknown is not shown as zero."`), ASK answers deterministically
  (no LLM invention: `mode=deterministic` in the test suite).
- **p50/p95 (gate 4) — PASS, all targets met** (7 warm calls each):

  | surface | p50 | p95 | target |
  |---|---|---|---|
  | SEARCH | 60 ms | 61 ms | <500 ms |
  | WATCHLIST | 1.7 ms | 1.9 ms | <300 ms |
  | ART | 10.8 ms | 10.9 ms | <750 ms |
  | TODAY | 8.9 ms | 10.7 ms | <750 ms |
  | FEST | 1.8 ms | 2.1 ms | <1 s |
  | TOUR | 18.1 ms | 18.7 ms | <1 s |
  | MARKET | 1.4 ms | 1.6 ms | <1 s |
  | DATA | 15.9 ms | 16.2 ms | <1 s |
  | EVENT / VENUE / MON / ALERTS | 0.7–4.7 ms | ≤4.7 ms | — |
  | ASK | 4.9 ms | 5.3 ms | deterministic |

- **Two real defects found and fixed by acceptance (`1035754`):**
  1. `ThreadingHTTPServer` shared ONE DuckDB connection across threads → concurrent SPA fetches
     returned shuffled/garbled watchlist payloads (the acceptance run caught list rendering as
     randomly "Empty list"). Fixed: `TerminalApp.dispatch` serializes on a lock.
  2. Alerts/TODAY link events as `tm::<provider_event_id>` but the event route only matched
     `watch_<hash>` ids → every alert link 404'd. Fixed: `get_event` resolves `tm::`/bare ids and
     falls back to `events.provider_event_snapshots` (returns `kind=SNAPSHOT` with observations;
     live-verified: `tm::vv1FvZv0o3_fZ72eee` → 200, JIMMY EAT WORLD, 3 observations).
- **Full gate after fixes:** Python **537 passed / 1 skipped**, Node **76 passed**, typecheck clean,
  security (gitleaks) green in CI. Working tree clean, pushed.

---

## 6. Semantics preserved

UNKNOWN ≠ 0 · event_time ≠ knowledge_time · attention ≠ demand · external ID ≠ canonical ID ·
ambiguity ≠ match · initial load ≠ NEW_EVENT · fuzzy search never defines identity ·
LLM never commits identity · zero historical observations ≠ zero gross/tickets.

## 7. Rights / licenses

No new dependencies. No source dumps committed. No secrets touched. All data from existing
licensed/accepted providers (Ticketmaster API, MusicBrainz dumps, ListenBrainz, Wikimedia).

---

## 8. Remaining gates before merge

1. Final PR-body refresh + mark ready + merge + verify post-merge main CI (mechanical only — all
   acceptance gates are now green).
2. (Post-PR31, per plan) Splink shadow identity challenger, Memray profiling, Dagster/OpenLineage
   wrapper, H3 cells, dlt A/B — these belong on a NEW branch, not PR #31.

## 9. Recommended next milestone

**`OSS_PLATFORM_ADOPTION_V1`** — merge PR #31, then wrap existing providers in Dagster assets
(warehouse stays authoritative; `provider_acquisition_runs` links Dagster run IDs), add OpenLineage
facets for evidence/PIT semantics, pilot Crawlee on one rights-approved source, and run the Splink
shadow identity challenger against the 59-case MBID fixture. Do NOT rebuild acquisition semantics.
