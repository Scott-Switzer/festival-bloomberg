# P0 — Remeasured Production State (2026-09-09T01:09Z)

Base: `5841b32955483e3cb58c8d3c3af04775e82dcea4` (PR #74 merge, serving `terminal_v1_20260909T002722Z`).

## Lake / Gold / Serving

- **Lake attention** `metrics/artist_attention_observations/artist_attention_observations.parquet`: **227,367 rows** (226,984 listenbrainz + 383 wikimedia), max `period_end` 2026-08-20, max `retrieved_at` 2026-08-20T01:12, max `ingested_at` 2026-08-19T18:12 — **frozen ~20d**.
- **Factor Gold** `gold/artist_factor_tape/CURRENT.json` `artist_factor_tape_v1_d71da66e08bced6abf8dc6c6`: **89,388 rows**, 725 artists, window 2026-08-27→2026-09-05 — **frozen ~4d**.
- **Peers** Gold `lb_full_20260908T193559Z` 1,081,761 edges → serving 126,898 / 13,220 artists (52.9%).
- **Geography** Gold 10,640 → serving 9,975 restored via `fold_bulk_gold_into_serving.py` 20260909T002722Z — ACTIVATED on staging 34295135114.
- **News** 0 serving rows; GDELT provider proven (1×5 at 00:11:02Z) then edge 429 on 12-cohort burst at 00:32 — honest UNKNOWN, no fake rows.
- **Scheduler** `BACKUPS/control/scheduler/LAST_CRON.json` `cron_20260909010908`: YOUTUBE_CHANNEL candidate 964 deferred 964 quota_blocked 964, TICKET 387/450 deferred (windowed quota as designed).
- **NIM** catalog 81 OK (`nvidia/embed-qa-4` etc.); routing stale (`nvidia/nv-embedqa-e5-v5` 404, `llama-3.3-70b` MODEL_UNAVAILABLE).

## Correction

`NIGHTLY_SERVING_REFRESH` previously reported PASS while publication remained manual via laptop script. Corrected to **PARTIAL** until automated scheduled generation promotion proves via an enabled cron/workflow.

## Ledger contract (P1)

Reuse `evidence_rails/contract.py:ObservationRecord` (content-addressable `observation_id = obs::<hash16>`, append-only, UNKNOWN!=0, retrieved_at != observation_time, knowledge_time explicit, derived cannot overwrite observed, rights/retention enforceable). Acquisition layer uses `acquisition/contracts.py:AcquisitionRequest/Result` with explicit `AcquisitionStatus` (including `RATE_LIMITED` → `SOURCE_RATE_LIMITED`, never retried aggressively).

Physical tables already implement the logical ledger: `acquisition.external_event_observations`, `metrics.artist_attention_observations`, `gold/artist_factor_tape`, `serving/artist_factor_observations`, `serving/artist_security_terminal_v1` — the requirement is the canonical logical contract, not one giant SQL table.

