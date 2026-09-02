/**
 * Low-cardinality queue lifecycle telemetry.
 *
 * We persist one record per consumer batch (rather than one object per
 * message) so operators can reconcile scheduler enqueues with terminal
 * acknowledgements and retries without adding a high-volume hot path. The
 * platform Queue.metrics() snapshot is read separately for authoritative
 * point-in-time backlog depth.
 */

export interface QueueMetricsEnv {
  BACKUP_BUCKET: R2Bucket;
}

export interface PlatformQueueMetric {
  backlog_count: number | null;
  backlog_bytes: number | null;
  oldest_message_timestamp: string | null;
  available: boolean;
  error?: string;
}

function timestampIso(value?: Date): string | null {
  if (!value) return null;
  const timestamp = new Date(value.getTime());
  return Number.isFinite(timestamp.getTime()) ? timestamp.toISOString() : null;
}

/** Read realtime Cloudflare Queue depth without making health depend on it. */
export async function readPlatformQueueMetrics(bindings: Record<string, Queue>): Promise<Record<string, PlatformQueueMetric>> {
  const entries = await Promise.all(Object.entries(bindings).map(async ([queue, binding]) => {
    try {
      const metrics = await binding.metrics();
      return [queue, {
        backlog_count: metrics.backlogCount,
        backlog_bytes: metrics.backlogBytes,
        oldest_message_timestamp: timestampIso(metrics.oldestMessageTimestamp),
        available: true,
      }] as const;
    } catch (error) {
      return [queue, {
        backlog_count: null,
        backlog_bytes: null,
        oldest_message_timestamp: null,
        available: false,
        error: error instanceof Error ? error.message : String(error),
      }] as const;
    }
  }));
  return Object.fromEntries(entries);
}

export interface QueueBatchMetric {
  queue: string;
  enqueued?: number;
  received: number;
  acked: number;
  retried: number;
  explicit_dlq: number;
  recorded_at?: string;
}

function metricKey(recordedAt: string, suffix: string): string {
  return `control/queue-metrics/date=${recordedAt.slice(0, 10)}/hour=${recordedAt.slice(11, 13)}/minute=${recordedAt.slice(14, 16)}/${suffix}.json`;
}

export async function writeQueueEnqueueMetric(
  env: QueueMetricsEnv,
  queue: string,
  enqueued: number,
  run_id?: string,
  recorded_at?: string,
): Promise<void> {
  const recordedAt = recorded_at || new Date().toISOString();
  const safeQueue = queue.replace(/[^A-Za-z0-9_-]/g, "_");
  const key = metricKey(recordedAt, `${safeQueue}-enqueue-${Date.now()}-${crypto.randomUUID()}`);
  await env.BACKUP_BUCKET.put(key, JSON.stringify({
    queue, enqueued, received: 0, acked: 0, retried: 0, explicit_dlq: 0, run_id, recorded_at: recordedAt,
  }), { httpMetadata: { contentType: "application/json" } });
}

export async function writeQueueBatchMetric(env: QueueMetricsEnv, metric: QueueBatchMetric): Promise<void> {
  const recordedAt = metric.recorded_at || new Date().toISOString();
  const safeQueue = metric.queue.replace(/[^A-Za-z0-9_-]/g, "_");
  const key = metricKey(recordedAt, `${safeQueue}-${Date.now()}-${crypto.randomUUID()}`);
  await env.BACKUP_BUCKET.put(key, JSON.stringify({ ...metric, recorded_at: recordedAt }), {
    httpMetadata: { contentType: "application/json" },
  });
}

export interface QueueMetricTotals {
  enqueued: number;
  received: number;
  acked: number;
  retried: number;
  explicit_dlq: number;
  telemetry_batches: number;
}

export interface QueueMetricsSnapshot {
  totals: Record<string, QueueMetricTotals>;
  window_start: string;
  window_end: string;
  minutes_covered: number;
  complete: boolean;
}

export async function readQueueMetrics(
  env: QueueMetricsEnv,
  now: Date = new Date(),
  windowMinutes = 15,
): Promise<QueueMetricsSnapshot> {
  const totals: Record<string, QueueMetricTotals> = {};
  const end = new Date(now);
  end.setSeconds(0, 0);
  const start = new Date(end.getTime() - Math.max(1, windowMinutes) * 60_000 + 60_000);
  let complete = true;
  let minutesCovered = 0;

  // Partition by minute so a busy day never causes a single list() call to
  // silently truncate at 1,000 records. A minute exceeding that bound is
  // reported incomplete instead of presenting an undercount as authoritative.
  for (let cursor = new Date(start); cursor <= end; cursor = new Date(cursor.getTime() + 60_000)) {
    const stamp = cursor.toISOString();
    const prefix = `control/queue-metrics/date=${stamp.slice(0, 10)}/hour=${stamp.slice(11, 13)}/minute=${stamp.slice(14, 16)}/`;
    const listing = await env.BACKUP_BUCKET.list({ prefix, limit: 1000 });
    if (listing.truncated) complete = false;
    minutesCovered++;
    for (const object of listing.objects) {
      const item = await env.BACKUP_BUCKET.get(object.key);
      if (!item) continue;
      try {
        const metric = await item.json() as QueueBatchMetric;
        if (!metric.queue) continue;
        const current = totals[metric.queue] || { enqueued: 0, received: 0, acked: 0, retried: 0, explicit_dlq: 0, telemetry_batches: 0 };
        current.enqueued += metric.enqueued || 0;
        current.received += metric.received || 0;
        current.acked += metric.acked || 0;
        current.retried += metric.retried || 0;
        current.explicit_dlq += metric.explicit_dlq || 0;
        current.telemetry_batches++;
        totals[metric.queue] = current;
      } catch {
        // An incomplete telemetry object must not break the operational endpoint.
        complete = false;
      }
    }
  }
  return { totals, window_start: start.toISOString(), window_end: end.toISOString(), minutes_covered: minutesCovered, complete };
}
