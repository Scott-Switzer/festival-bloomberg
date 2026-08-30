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
- Version: `caa3ccf6-cfbf-4d6d-ae5a-42be70a53157`
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

1. YouTube check, quota, heartbeat, and value-change counters need richer
   persistent aggregation rather than current audit approximations.
2. A bounded Monid fallback test remains outstanding.
3. Stale event-estate identities still need repair at the source, even though
   non-native Ticketmaster IDs are now filtered before queue dispatch.

No additional feature milestone should begin until these acceptance gaps are
closed or explicitly waived.

## Runtime repair (2026-08-30)

The one-minute scheduler previously fetched and parsed
`control/youtube/state/<channel>.json` once per channel on every tick.  The
promoted scheduler artifact has 966 channel identities, while the broader
25K-artist security estate has 13,925. The per-channel R2 read amplification
exhausted the Worker invocation resource budget even at the promoted size and
produced repeated `exceededCpu` outcomes before queue dispatch.

The scheduler now uses the promoted active-channel artifact as its bounded
read snapshot (including an optional `status` field) and excludes entries
marked `QUARANTINED`.  Per-channel state remains persisted by the YouTube
consumer and is folded into the next explicit artifact promotion.  Queue
consumers also persist one low-cardinality batch lifecycle record per
invocation; `/ops/health` reconciles scheduler enqueue counts with ack/retry
and explicit-DLQ counts over a bounded rolling 15-minute window, alongside
authoritative point-in-time `Queue.metrics()` values for each runtime queue.
Metrics are minute-partitioned so a busy day cannot silently truncate at R2's
1,000-item list page; if any minute is truncated or malformed,
`telemetry_complete` is false. If a platform metrics read fails, only that
queue's platform fields are marked unavailable while the endpoint remains
operational. The implementation follows Cloudflare's [Queues metrics
documentation](https://developers.cloudflare.com/queues/observability/metrics/)
and its [realtime backlog metrics changelog](https://developers.cloudflare.com/changelog/post/2026-04-28-improved-queues-metrics/).

Ticket dispatch is also windowed: at most 25 structured Ticketmaster tasks are
selected at each 15-minute boundary from a deterministic rotating slice, while
Monid web work is selected at six-hour boundaries (four 25-task windows/day,
approximately $0.09/day at the measured unit cost). Structured rows without a
provider-native event id are excluded before enqueue; any legacy malformed task
that reaches the consumer is terminally acknowledged rather than retried. The
structured queue consumer is serialized at `max_concurrency=1` to avoid
provider 429 bursts. YouTube work is quota-bounded without mutable scheduler state: up to 250 hot
channels run hourly and the remaining daily quota is allocated once per UTC
day to a rotating cold-channel slice. This keeps the one-minute trigger as a
clock without turning it into a spend or queue flood; the Governor remains the
authoritative consumer-side budget gate.

The repaired version was verified live on 2026-08-30: the scheduled invocation
completed with an `ok` outcome and 11 ms CPU time, and authenticated
`/ops/health` reported complete internal telemetry plus realtime platform
metrics for all six production queues. Four unused legacy queues were removed
after their zero-backlog state was verified; six legacy DLQ messages were
archived to private R2 evidence before purge.

The managed `fi-dlq` is now consumed by a serialized archival handler. Each
message is written, idempotently by its Cloudflare message id, to the private
`BACKUP_BUCKET` under `evidence/queue-dlq/` before acknowledgement. R2 write
failures leave the message retryable; the handler never acknowledges a payload
that was not durably archived. On activation it archived and acknowledged 27
complete envelopes with zero retries; the earlier retired-queue archive holds
six more messages. The initial realtime depth was 76 and a non-destructive peek
exposed 66, so only 33 messages are directly R2-verifiable after drain. No purge
was run, and the unexplained pre-consumer/in-flight discrepancy is not promoted
to an evidence-preservation claim.
