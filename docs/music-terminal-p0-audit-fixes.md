# MUSIC_TERMINAL_PRODUCTIZATION_V1 — P0 audit fixes

Applied on `feat/music-terminal-productization-v1` after the external audit of
PR #31. These are correctness fixes, not new features. Four files changed.

## Fixed

1. **ListenBrainz observations are now temporal** (`attention/listenbrainz.py`).
   `observation_key()` now includes the retrieval-day bucket
   (`retrieved_at[:10]`) and the provider `last_updated` when supplied, so a
   later re-fetch of a cumulative listen/user count creates a NEW observation
   instead of being silently dropped. Same-day re-runs remain idempotent.
   Verified: same-day key ==, next-week key !=.

2. **Watchlist POST is reachable** (`terminal/server.py`). The generic
   `path == "/api/watchlists"` GET branch used to shadow the POST branch.
   Routes now branch on method: POST first, then `method == "GET"`.

3. **Identity conflicts no longer silently hidden** (`product/workflow.py`).
   `_identity_conflicts()` queried a non-existent `artist_key` column and
   swallowed the error with a bare `except`. It now reads the real
   `core.identity_conflicts` columns (`entity_key`, `provider_a/b`, `value_a/b`,
   `issue`, `resolution_status`) with no exception suppression.

4. **NEW_EVENT requires a prior run** (`product/workflow.py`). The old logic
   alerted every event whose `first_seen == MIN(retrieved_at)` of the whole
   corpus, which (a) fired on first ingest and (b) would never fire on a
   genuine later refresh. Now an event is NEW only when its first-seen time is
   in the LATEST distinct acquisition batch AND at least two distinct snapshot
   batches exist. A single-snapshot corpus yields no NEW_EVENT (correct: there
   is no prior state).

5. **Change alerts compare consecutive snapshots, not first-vs-latest**
   (`product/workflow.py`). A value that changes then reverts now produces two
   alerts. `_compare_consecutive()` walks each event's snapshots ordered by
   `retrieved_at`.

6. **PRESALE_DISCOVERED implemented** (`product/workflow.py`). New presale
   entries (from `events.provider_event_snapshots.presales` JSON) are diffed
   between consecutive snapshots via `_presale_signature()` and persisted.

7. **DJ regex no longer rejects "DJ Khaled"** (`identity/ticketmaster_resolution.py`).
   The bare `\bdj\b` / `\bset\b` signal was narrowed to `dj set` / `dj night` /
   `live set` so a real artist named "DJ X" is not misclassified as an event.

## Verified

- `py_compile` clean on all four files.
- Live-warehouse smoke test: `build_today()` returns all six sections,
  `_identity_conflicts()` returns real rows (empty here), temporal key
  semantics confirmed.
- `tests/python/test_intelligence_terminal.py` — 20 passed.

## Still open (hand off, do NOT re-audit these as new findings)

- **Red CI gate** — `test_forward_history.py::TestTwoSnapshotPit::test_pit_ab_visibility_from_real_rows`
  fails on clean `main` too (pre-existing, not caused by this branch). Needs a
  real fix or a tracked quarantine with proof.
- **Identity QA still compares matched NAME, not MBID** — `run_identity_qa()`
  now checks the resolved canonical name equals the expected name (no longer
  just `MATCHED_ARTIST` status), but the audit's stricter "expected MBID vs
  returned MBID" test is still not implemented.
- **Watchlists are not editable in the SPA** — the API now supports
  create/add/remove, but the UI has no controls for it yet.
- **TODAY is not watchlist-personalized** — alerts store `entity_key` but no
  `related_entities` mapping, so a watched ARTIST does not surface a new
  EVENT touch. Needs an alert→related-entity join table.
- **TODAY frontend/backend field-name contract** — backend ticketing rows use
  `alert_type`/`entity_name`/`detail`, frontend reads some `event_name`/
  `onsale_start`/`status` top-level fields. Cells can render blank until the
  contract is aligned.
- **Bulk SQL/Parquet ingest** — the 2.2M-artist and identifier workloads still
  use row-by-row SQL. The audit's Arrow/Parquet + `INSERT ... SELECT`
  recommendation would cut runtime 5-20x.
- **Dedicated behavioral tests** — none of the new systems above have focused
  regression tests yet.

## Recommended next milestone

**CORRECTNESS + PERFORMANCE + DAILY-WORKFLOW HARDENING** (in that order), not
another data-expansion milestone. Priority list and a product acceptance test
are in the audit message that prompted these fixes.
