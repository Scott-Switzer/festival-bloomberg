# CLOUD_FORWARD_DATA_PLANE_V2

Date: 2026-08-27

## Result

**PARTIAL — not yet accepted as PASS.** The Cloudflare Worker now runs a
one-minute master clock, uses split acquisition queues, loads the v2 watch
universe, and executes real YouTube API batches into R2. Ticketmaster structured
execution is also proven for successful native event IDs. The acceptance
artifact is `CLOUD_FORWARD_TAPE_ACCEPTANCE.json`.

## Production deployment

- Worker: `fi-acquisition-runtime`
- Version: `f2c6154c-5940-4b21-a176-6f1476ba76b9`
- Cron: `* * * * *`
- Active pointer: `control/watch_universe/current.json` → v2 artifact
- Universe: 571 events (166 HOT, 354 ACTIVE, 51 LONG_HORIZON)
- YouTube identity candidates: 966

Queues:

- `fi-youtube`
- `fi-structured-api`
- `fi-browser`
- `fi-monid`
- `fi-processing`
- `fi-dlq`

`fi-monid` remains constrained independently from official API work.

## Observed cloud activity

The production tail and R2 evidence showed:

- 16 scheduler audit fires observed by the health endpoint
- 15 YouTube batches
- 440 YouTube channel checks
- 15 unique raw YouTube objects at the time of the acceptance snapshot
- 440 normalized YouTube lake ticks
- YouTube raw content-addressed dedupe (unique raw objects lower than checks)
- real Ticketmaster structured API completions
- raw Ticketmaster responses and normalized lake observations with matching
  `raw_evidence_ref`

R2 aggregate growth from the original pre-v2 baseline:

| Bucket | Before | After |
|---|---:|---:|
| raw | 116 objects / 4.86 MB | 131 objects / 5.02 MB |
| lake | 211 objects / 604 MB | 745 objects / 605 MB |
| backups | 3,186 objects / 5.55 GB | 3,205 objects / 5.55 GB |

## Security

`YOUTUBE_API_KEY` and rotated `ADMIN_TOKEN` are Cloudflare secrets and local
`.env` values only. Neither value is committed, logged, or included in
acceptance JSON. The admin token was regenerated with cryptographically secure
randomness and is not recovered from the prior deployment.

## Implementation

- `youtube-cloud.ts`: batches up to 50 channel IDs per official
  `channels.list`, stores exact raw response bytes by SHA-256, and writes
  `VALUE_CHANGE` or `HEARTBEAT` ticks.
- `youtube-consumer.ts`: independent YouTube queue consumer.
- `structured-consumer.ts`: official Ticketmaster API queue consumer with raw
  and normalized R2 evidence.
- `forward-planner.ts`: family planning and quota-aware HOT/FULL cadence.
- `build_cloud_watch_universe_v2.py`: deterministic local artifact builder.
- `promote_cloud_watch_universe_v2.py`: explicit pointer promotion command.
- `/ops/health`: authenticated operational endpoint.

## Outstanding blockers

The milestone is intentionally not marked PASS because:

1. `/ops/health` still lacks reliable queue backlog/DLQ metrics.
2. YouTube check, quota, heartbeat, and value-change counters need richer
   persistent aggregation rather than current audit approximations.
3. Scheduler freshness in health was stale relative to later R2 activity.
4. A bounded Monid fallback test remains outstanding.
5. Some event-estate identities are stale or non-native Ticketmaster URLs and
   must be filtered/repaired before treating the structured queue as fully
   healthy.

No additional feature milestone should begin until these acceptance gaps are
closed or explicitly waived.
