# LISTENBRAINZ_FULL_CORPUS_ACTIVATION_V1

Branch: `feat/listenbrainz-full-corpus-activation-v1`  
Parent: PR #72 merge `0594a9f` (`BULK_CORPUS_ACTIVATION_V1 = PASS` with
`LISTENBRAINZ_FULL_CORPUS = NOT_YET_COMPLETE`).

## Objective

Process the **already-stored** ~191 GiB ListenBrainz spark dump into production
Gold + serving for the 25K universe — without rewriting the bounded pilot
algorithm, without a new distributed framework, and **without running on the
local MacBook**.

## Known blockers (before → after this branch)

| Blocker | Before | After |
| --- | --- | --- |
| Tar/shard index | ABSENT | `scripts/lb_build_corpus_index.py` (header-only R2 range walk) → `control/lake/lb_tar_index.json` + lake `tar_members_v1.json` |
| Checkpoint contract | LOCAL_HOST_ONLY only | + `CLOUD_JOB_R2` via `FI_LB_CHECKPOINT_AUTHORITY` / `FI_LB_JOB_ID` |
| Local disk | ≪ map minimum | **Do not run map locally** — Cloudflare batch `standard-4` (20 GiB ephemeral) |
| Wrong cloud layout | `listenbrainz_map` expected `raw/listenbrainz/*.zst` | New job type `listenbrainz_tar_map` drives `scripts/lb_full_scan.py` against the stored tar |
| Reducer gates | not full-scale proven | Explicit `PENDING_BOUNDED_PROOF` until larger-than-pilot proof |
| Host suitability | MacBook NOT appropriate | Cloud batch container |

## Corpus

- Bucket: `festival-intelligence-raw`
- Key: `bulk/listenbrainz/dump=2593-20260712-000004/listenbrainz-spark-dump-2593-20260712-000004-full.tar`
- Bytes: `205,073,162,240`
- ETag: `b487ef886520ea2efcc149c47165b5a6-3056`

## Execution path (no new architecture)

```text
index (header walk)
→ immutable job plan
→ shard checkpoints (CLOUD_JOB_R2)
→ bounded cloud MAP listenbrainz_tar_map (top-25 policy)
→ reducer gates + proof subset
→ full MAP/REDUCE
→ new Gold generation
→ serving rebuild
→ hosted acceptance
```

Reuse:

- `scripts/lb_full_scan.py` (map / reduce-artist-day / reduce-affinity / reduce-pairs)
- `scripts/lb_pilot.py` policy (`TOP_K=25`)
- cloud `batch_jobs.run_listenbrainz_tar_map`

Do **not** use `listenbrainz_map` for this dump (different shard layout).

## Resource estimate (preflight)

| Stage | Expected |
| --- | --- |
| Index build | Header-only; ~10–15 min; negligible disk |
| Bounded proof (e.g. 76 shards) | Multi-GB R2 reads; ≤512 MB DuckDB; spill on 20 GiB container scratch |
| Full map | ~191 GiB R2 reads; multi-hour; needs ≥8 GiB free scratch |
| Affinity reduce | Hash partitions; must enforce global top-25 |

Exact MB/s / peak RSS / projected runtime: **TBD after bounded real-corpus proof**.

## Preflight gates

```bash
PYTHONUNBUFFERED=1 PYTHONPATH=python .venv/bin/python scripts/lb_full_corpus_preflight.py --report
# Cloud evaluation of MAP_RESOURCE_GATE:
PYTHONUNBUFFERED=1 PYTHONPATH=python .venv/bin/python scripts/lb_full_corpus_preflight.py --assume-cloud --report
```

Must all PASS before full run:

1. `CORPUS_INDEX = VERIFIED`
2. `CHECKPOINT_RESUME = PASS`
3. `MAP_RESOURCE_GATE = PASS` (cloud scratch)
4. `REDUCER_RESOURCE_GATE = PASS` (after proof)
5. `R2_ACCESS = PASS`
6. `OUTPUT_VERSIONING = PASS`

## Trigger bounded cloud proof (after index upload + image deploy)

```bash
curl -X POST https://fi-acquisition-runtime.scswitzer.workers.dev/batch/trigger \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "job_type": "listenbrainz_tar_map",
    "job_id": "lb_tar_proof_76",
    "max_batches": 76,
    "params": {"partitions": 256, "max_shards": 76}
  }'
```

## Product rule

Output label remains **LISTENBRAINZ CONSUMPTION AFFINITY** — never ticket demand /
local demand / fan crossover / sales affinity.
