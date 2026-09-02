# CLOUD_FIRST_DATA_EXECUTION_V1

## Architecture

```
                    FESTIVAL BLOOMBERG
                           │
                    GitHub repository
                           │
                           ▼
                 CLOUDFLARE CONTROL PLANE
                 (Worker + Workflows + Queues)
                           │
              ┌────────────┴────────────┐
              ▼                         ▼
       Cloudflare Container       R2 control state
       standard-4                 manifests/checkpoints
       4 vCPU / 12 GiB RAM              │
       20 GB ephemeral disk              │
              │                          │
              └────────────┬────────────┘
                           ▼
                         R2
          ┌────────────────┼─────────────────┐
          ▼                ▼                 ▼
         RAW             SILVER             GOLD
       (source)       (normalized)       (products)
          │                                  │
          │                                  ▼
          │                              SERVING
          │                                  │
          └──────────────────────────────────┘
                                             │
                                             ▼
                                       Buyer Terminal
```

## Mac role after this

The Mac is a **development/control client only**:
- Code checkout
- `wrangler deploy` / control commands
- Browser for UAT
- Small test fixtures

No canonical data depends solely on local disk.

## Cloud components

### BatchContainer Durable Object (`batch-container-do.ts`)

Manages a reusable Cloudflare Container instance for heavy data jobs.

- **Instance type**: `standard-4` (4 vCPU / 12 GiB / 20 GB ephemeral disk)
- **Lifecycle**: DO calls `ctx.container.start()` on first job, stays alive
- **Execution**: DO calls `ctx.container.exec(["python", "batch_entrypoint.py"])` per job
- **Restart safety**: The entrypoint reads R2 checkpoints on startup, skips
  completed batches, and resumes without duplicating work

### Batch entrypoint (`docker/batch_entrypoint.py`)

Receives a job spec via `FI_BATCH_JOB` env var (JSON), dispatches to:
- `identity_graph_v2` → `festival_bloomberg.cloud.batch_jobs.run_identity_graph_v2`
- `listenbrainz_map` → `festival_bloomberg.cloud.batch_jobs.run_listenbrainz_map`
- `listenbrainz_reduce` → `festival_bloomberg.cloud.batch_jobs.run_listenbrainz_reduce`

### R2 buckets

| Binding | Bucket | Purpose |
|---------|--------|---------|
| `RAW_BUCKET` | `festival-intelligence-raw` | Source corpora (MusicBrainz, Wikidata, ListenBrainz) |
| `LAKE_BUCKET` | `festival-intelligence-lake` | Silver/Gold parquets, product-safe aggregates |
| `PRIVATE_BUCKET` | `festival-intelligence-private` | Restricted listener-level intermediates |
| `BACKUP_BUCKET` | `festival-intelligence-backups` | Control state, manifests, checkpoints |

### Job manifest contract (`cloud/job_manifest.py`)

Every job writes a manifest to `control/jobs/<type>/<id>/manifest.json`:
- `job_id`, `job_type`, `code_commit`, `container_image`
- `source_generation`, `source_paths`
- `status`: PLANNED → RUNNING → BUILD_COMPLETE → VERIFIED → PUBLISHED
- `completed_batches`, `failed_batches`, `total_batches`
- `bytes_read`, `rows_read`, `rows_written`
- `output_paths`, `output_hashes`
- `runtime_seconds`, `peak_rss_bytes`, `r2_read_bytes`, `r2_write_bytes`

## Deployment

### Prerequisites
- Docker running locally (for image build on `wrangler deploy`)
- R2 credentials set as secrets:
  ```bash
  npx wrangler secret put FI_R2_ENDPOINT --config cloud-runtime/wrangler.jsonc
  npx wrangler secret put FI_R2_ACCESS_KEY_ID --config cloud-runtime/wrangler.jsonc
  npx wrangler secret put FI_R2_SECRET_ACCESS_KEY --config cloud-runtime/wrangler.jsonc
  ```

### Deploy
```bash
cd cloud-runtime
npx wrangler deploy
```

### Trigger a job
```bash
# Identity Graph V2 (first cloud workload)
curl -X POST https://fi-acquisition-runtime.<subdomain>.workers.dev/batch/trigger \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "job_type": "identity_graph_v2",
    "job_id": "identity_v2_20260831",
    "source_generation": "20260831T014029Z-1369"
  }'

# ListenBrainz 5% map stage
curl -X POST https://fi-acquisition-runtime.<subdomain>.workers.dev/batch/trigger \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "job_type": "listenbrainz_map",
    "job_id": "lb_map_5pct",
    "max_batches": 76,
    "params": {"partitions": 64}
  }'

# ListenBrainz reduce
curl -X POST https://fi-acquisition-runtime.<subdomain>.workers.dev/batch/trigger \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "job_type": "listenbrainz_reduce",
    "job_id": "lb_reduce_5pct",
    "params": {"map_job_id": "lb_map_5pct", "top_k_per_listener": 25}
  }'
```

### Check status
```bash
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
  "https://fi-acquisition-runtime.<subdomain>.workers.dev/batch/status?job_id=identity_v2_20260831"
```

## Scratch contract

Container scratch is bounded to `/tmp/festival-bloomberg/` (target ≤ 8–10 GB).
Every batch:
1. READ source from R2
2. PROCESS with DuckDB (bounded memory)
3. WRITE partial to R2
4. VERIFY partial
5. UPDATE checkpoint in R2
6. DELETE local scratch

At process exit: no unique dataset may remain on ephemeral disk.

## Private data

Restricted ListenBrainz listener-level intermediates live in
`festival-intelligence-private` (the `PRIVATE_BUCKET` binding):
- `listenbrainz/map/<job_id>/partition=N/*.parquet`
- `control/jobs/listenbrainz_map/<job_id>/checkpoint.json`

Only aggregate audience evidence flows to `LAKE_BUCKET` (Gold/Serving):
- `silver/listenbrainz/artist_day/<job_id>/`
- `gold/audience_affinity/<job_id>/`

## Definition of done

A fresh machine with the repository and Cloudflare credentials can:
1. Deploy the pipeline (`wrangler deploy`)
2. Trigger a batch job (`/batch/trigger`)
3. The job reads R2 canonical data and finishes
4. Outputs + manifest are in R2
5. No unique data is stranded on local disk
