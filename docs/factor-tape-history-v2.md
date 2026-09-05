# Factor Tape History V2

## Delivery boundary

`FACTOR_TAPE_HISTORY_V2 = IMPLEMENTED / TESTED / PILOTED`.
`PRODUCTION_PROVEN = NO`. This branch does not deploy, merge, switch production
Gold/serving pointers, or claim full 25K observation coverage.

The previous refresh replaced Gold with a bounded slice and silently skipped
failed reads. The replacement accumulates a verified parent plus new source
observations. A 512-tick refresh can now add evidence without deleting the
older observation history that a buyer or future researcher needs.

See [read-only recon and 14-category decision ranking](factor-tape-history-v2-recon.md)
and [machine-readable R2 acceptance](../reports/factor_history_v2_acceptance.json).


## Measured acceptance (September 5 UTC)

Pilot code: `2b9456c964be74222c2dfa49bdfa2c2c6408aff8`.
Implementation SHA-256: `d6564784cdc4fe6bc04619938c22085ff25b15e6e215977888ac9c374e91b555`.
R2 namespace: `r2://festival-intelligence-lake/validation/factor_history_v2/pilot_20260905_2b9456c/`.

| Measure | Result |
|---|---:|
| Verified parent observations | 66,684 |
| First refresh observations | 68,220 |
| Second refresh observations | 69,756 |
| Added observations | 3,072 |
| Missing parent rows (all-column EXCEPT) | 0 |
| Artists | 725 |
| Newly consumed ticks | 1,024 |
| September 4 inventory / remaining ticks | 4,819 / 3,795 |
| Completed chunks reused after interruption | 1 |
| Tick reads on exact replay | 0 |
| First resumed build / second build seconds | 25.74 / 38.239 |
| Process peak RSS bytes | 324,534,272 |
| Sampled scratch high-water bytes | 15,738,335 |
| R2 stale-writer response | HTTP 412 |
| Production CURRENT changed | No |
| Provider requests | 0 |
| Billed cost | NOT_AVAILABLE |

Coverage spans `2026-08-27T22:04:51.121Z` to `2026-09-04T02:02:26.309Z`. The final Parquet
hash is `b34b13e08b1d946699caa118ac0911b948ba8dd554efc24851c1c25ed50b3328`. It contains real public-provider observations,
with original rights and knowledge times, in an isolated validation generation.

The full serving artifact was separately downloaded and hash-verified. Actual
counts match recon metadata: 25,000 artists; 47,602 external IDs; 27,322 market
links; 134,447 history rows; 14,758 festival appearances; 6,690 future events;
66,684 original factors; 111 sentiment rows. Folding the candidate produces
69,756 factor rows through the unchanged production fold. The real artist
readmodel reports OBSERVED with three NOT_COMPARABLE change records and no
percentage deltas. This is serving/readmodel proof, not a hosted browser test.

Local validation: complete Python suite 1,147 passed / 22 skipped; 27 targeted
regressions passed after final resource/fingerprint hardening; Node 76 passed;
TypeScript and git diff --check passed. A redundant concurrent test run was
interrupted after it exhausted scratch space; its disk-gate failures were
resolved by removing only this task's test scratch and rerunning sequentially.
Fixture tests now mock free-space capacity and test the real preflight failure
separately. No research datasets or unrelated caches were removed.

## Implementation

The existing `artist_factor_tape_build_v1` dispatch and Parquet column contract
remain compatible with `_fold_gold_artist_intelligence` and the terminal.
`cloud/factor_history.py` owns this refresh path; no migration or dependency
was added. boto3 handles object I/O; Arrow serializes bounded chunks; DuckDB
merges/deduplicates the history with disk-backed tables.

1. Read CURRENT and ETag in one response; download/hash-check its Parquet and,
   when present, its source ledger. Never infer coverage from a timestamp.
2. List the source inventory with exact page/total bounds. Exceeding the
   inventory cap aborts before publication. Compare retained source ETags to
   reject mutations. Select oldest unprocessed keys in stable order.
3. Persist an immutable job plan containing the parent, selected object
   versions, inventory/pending counts, params, code reference and implementation
   fingerprint. New refreshes require new job IDs; retries reuse their ID.
4. Read at most one chunk concurrently using If-Match on source ETags. Validate
   the official tick contract, timestamp zones, channel-ID shape, lineage,
   fixture flags, numeric values and field lengths. Keep UNKNOWN absent and
   observed zero present. This is not a new independent identity verification.
5. Write/hash-verify each normalized chunk before persisting its checkpoint.
   Retry skips verified chunks even after local scratch disappears. ETag and
   SHA-256 lineage records remain in the cumulative input ledger.
6. Union the unchanged parent with new rows. Exact duplicates collapse;
   different payloads sharing an observation key fail closed. Different-value
   source claims with distinct existing observation keys coexist. Artist caps
   now reject an oversized generation instead of rejecting repeated artists.
7. Upload immutable content-derived generation paths and the cumulative ledger;
   verify both, persist VERIFIED, then use R2 If-Match/If-None-Match for CURRENT.
   A stale writer leaves its verified candidate unpublished and the winner intact.
   A completed job replays its result without reading ticks or republishing.

Publication is atomic with respect to other conditional writers. Existing old
code that writes CURRENT unconditionally must be retired before production
rollout; this PR cannot make a legacy writer honor a precondition.

## Resource and recovery contract

- Default 25,000 new ticks/run; hard maximum 100,000.
- Inventory cap 100,000 objects; explicit failure above it, not silent truncation.
- 256 ticks/chunk by default, at most 512; 16 concurrent reads, maximum 32.
- Tick object ≤64 KiB; normalized chunk ≤4 MiB.
- Parent ≤512 MiB both compressed and Parquet-reported uncompressed bytes;
  maximum 1,000,000 parent/output rows; output Parquet ≤512 MiB.
- DuckDB buffer budget 256 MB, two threads, temporary-spill limit 1 GB.
- Start only with ≥2 GiB free; abort at a sampled scratch footprint >1 GiB.
  These checks are not an OS-level disk quota or whole-process RAM limit.
- Scratch is removed on success/error; verified chunks and plans remain in R2.
  R2 checkpoint garbage collection/retention is a separate operator task.
- Runtime is measured through candidate construction before final verification
  and publication overhead. RSS is the process lifetime high-water mark. Scratch
  peaks are sampled at chunk/finalization boundaries, not continuous maxima.
- `r2_read_bytes`/`r2_write_bytes` are accounted data payloads, not invoice totals:
  control JSON, verification rereads, retries and protocol overhead are excluded.

The first V2 refresh of a V1 parent has no source ledger, so it rereads its
bounded source slice and deduplicates overlaps against the preserved parent.
Subsequent refreshes skip ledgered source objects. Every new inventory scan
can discover backdated arrivals; a run is only complete for its frozen plan.
Older unledgered parent rows keep their original evidence references and are
not retroactively assigned invented input-object hashes.

## Reproduce the isolated real-data pilot

Use the repository environment and existing R2 credentials. Choose a fresh run
name. All writes, including apparent Gold/CURRENT/control paths, are remapped
under `validation/factor_history_v2/<run>/`; production source reads are allowed.

```bash
PYTHONPATH=python .venv/bin/python scripts/accept_factor_history_v2.py \
  --run unique_pilot_name --source-date 2026-09-04 --max-ticks 512 \
  --verify-serving --out reports/factor_history_v2_acceptance.json
```

This intentionally interrupts after one chunk, resumes, runs a second refresh,
replays it, proves zero missing parent rows with SQL EXCEPT over every column,
verifies R2 hashes, exercises the existing serving fold/readmodel, independently
counts the actual full serving snapshot, and tests a stale R2 write (HTTP 412).
The September 4 source slice is explicit. It is not full-corpus acquisition,
an increased artist universe, a model benchmark, or hosted-browser acceptance.

## CI gate repair

Browser UAT previously used `secrets.ADMIN_TOKEN` in a job-level `if`, so GitHub
rejected the workflow before it could create jobs. The presence boolean now
lives in job env and gates steps, with an explicit NOT_AVAILABLE job summary
when absent. Required Python dependencies are installed instead of suppressing
a failed package install. This follows [GitHub's documented secret-condition
pattern](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets).
A green credential-absent job is not browser proof. The existing hosted staging
startup failure is not repaired by this change.

## Remaining gaps and rollout

1. Merge/review separately, deploy the updated batch image, retire legacy
   unconditional writers, then run new job IDs until the bounded backlog drains.
   Validate fresh Gold, rebuild serving and perform actual hosted buyer acceptance.
2. 725 artists with factors remains narrow versus a 25,000-artist universe.
   The measured pilot adds history, not identity/artist coverage.
3. YouTube's missing measurement-window/geography contract remains
   NOT_COMPARABLE. No percentage changes or demand interpretation were added.
   The pre-existing readmodel still includes an absolute delta in a
   NOT_COMPARABLE record; consumers must honor its comparability status.
4. Image builds still need an exact Git provenance injection for code_commit
   to stop returning `unknown` in containers without .git. The V2 implementation
   fingerprint prevents incompatible checkpoint reuse independently of Git.
5. No billed-cost claim, Cloudflare container execution proof, full inventory
   exhaustion, secure partner ingestion, or model-readiness promotion.

Next milestones by expected value: complete the production rollout and hosted
acceptance; build a fresh repeated official ticket-market cohort; activate a
secure, consented design-partner sales/outcome feed. Keep COMPS_SIGNAL_ONLY
until new temporally admissible outcomes justify another experiment.
