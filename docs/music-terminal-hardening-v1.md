# MUSIC_TERMINAL HARDENING V1 — status (head `25dc467`)

PR #31 hardening pass. The red CI gate is fixed, matched artists are promoted
into the canonical master, and a behavioral regression suite now covers the new
systems.

## Fixed this pass

1. **PIT CI gate (P0)** — `_two_snapshot_pit()` called `get_active_events()`
   with the wall clock, ignoring its `oa_started` argument. Once a seeded
   event's time was >48h old, it dropped out of the tracking window and the
   gate silently flipped to FAIL. Now evaluated `as_of=oa_started`. This is a
   real determinism fix, not a test quarantine. Full Python suite green.

2. **MATCHED_ARTIST linkage (P2)** — `promote_resolved_artists()` promotes
   reference-layer `MB_EXACT_NAME` matches (which had `artist_key=NULL`) into
   `core.artists` (`mbid::<mbid>`), persists the Ticketmaster attraction ID as
   an external identifier, and back-fills the resolution row. Live run:
   +1,127 canonical artists, 1,127 TM ID mappings, 0 MATCHED_ARTIST with a
   NULL key. Wired into the OA after `ticketmaster_resolution`.

3. **Watchlist remove bug** — `remove_watchlist_item()` used `SELECT changes()`
   (a SQLite-ism DuckDB lacks), so item removal raised at runtime. Now checks
   existence and returns 0/1.

4. **Default list naming (P9)** — "Major US Festivals" renamed "Major
   Festivals" (no country filter exists on the spine; `raw.musicbrainz_place.area`
   is a city/market, not a country). "Active Tours" description corrected to
   "events dated 2024 or later".

5. **Behavioral regression suite (P3)** — `tests/python/test_product_hardening.py`
   (15 tests): temporal ListenBrainz keys (same-day idempotent, later-day new,
   provider-updated new), DJ Khaled non-rejection, presale signature diff,
   watchlist create/add/remove, identity-conflict visibility, alias→real-MBID,
   TM attraction-ID dedupe, run-aware NEW_EVENT (none on initial load, one on
   later run), price/status change-then-revert, presale discovered + no dup.

## Gate status

- Python: **520 passed, 1 skipped** (local, full suite)
- Node: **76 passed**
- Typecheck: **clean**
- CI exact-head (`25dc467`): node SUCCESS, security SUCCESS, python IN_PROGRESS
  (expected green now that the PIT failure is fixed).

## Still open (unchanged from the audit — next milestone)

- MBID-ground-truth identity QA (current QA verifies name, not MBID).
- `core.alert_related_entities` + watchlist-personalized TODAY.
- TODAY API/frontend contract alignment.
- Editable watchlist UI in the SPA (API now works).
- Explicit acquisition `run_id` (alerts still infer runs from `retrieved_at`).
- Phase ledger / resumability so reports never re-stream the 2.2M archive.
- Bulk SQL/Arrow/Parquet ingest + search-term index (row-by-row is still slow).
- Box-office ART linkage (avoid false zero).
- Real Ticketmaster refresh + product acceptance walkthrough.

## Recommended next milestone

Correctness + performance + daily-workflow hardening, in that order. Do not
start another data-expansion milestone. The remaining P4-P19 items above are
the checklist.
