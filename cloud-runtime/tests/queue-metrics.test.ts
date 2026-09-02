import { describe, expect, it } from "vitest";
import { readPlatformQueueMetrics, readQueueMetrics, writeQueueBatchMetric, writeQueueEnqueueMetric } from "../src/queue-metrics";

describe("queue metrics", () => {
  it("reconciles enqueue and terminal batch telemetry without fabricating platform DLQ depth", async () => {
    const objects = new Map<string, string>();
    const env = {
      BACKUP_BUCKET: {
        put: async (key: string, value: string) => { objects.set(key, value); },
        list: async () => ({ objects: [...objects.keys()].map((key) => ({ key })), truncated: false }),
        get: async (key: string) => {
          const value = objects.get(key);
          return value ? { json: async () => JSON.parse(value) } : null;
        },
      },
    } as unknown as { BACKUP_BUCKET: R2Bucket };

    const now = "2026-08-30T12:04:00.000Z";
    await writeQueueEnqueueMetric(env, "fi-youtube", 3, "run-1", now);
    await writeQueueBatchMetric(env, {
      queue: "fi-youtube", received: 3, acked: 2, retried: 1, explicit_dlq: 0, recorded_at: now,
    });

    const snapshot = await readQueueMetrics(env, new Date(now), 1);
    expect(snapshot.complete).toBe(true);
    expect(snapshot.minutes_covered).toBe(1);
    expect(snapshot.totals["fi-youtube"]).toEqual({
      enqueued: 3, received: 3, acked: 2, retried: 1, explicit_dlq: 0, telemetry_batches: 2,
    });
  });

  it("reads authoritative realtime depth for every bound queue and isolates failures", async () => {
    const queues = {
      "fi-youtube": { metrics: async () => ({ backlogCount: 4, backlogBytes: 120, oldestMessageTimestamp: new Date("2026-08-30T12:00:00Z") }) },
      "fi-dlq": { metrics: async () => { throw new Error("metrics unavailable"); } },
    } as unknown as Record<string, Queue>;

    const result = await readPlatformQueueMetrics(queues);
    expect(result["fi-youtube"]).toEqual({
      backlog_count: 4,
      backlog_bytes: 120,
      oldest_message_timestamp: "2026-08-30T12:00:00.000Z",
      available: true,
    });
    expect(result["fi-dlq"]).toMatchObject({
      backlog_count: null,
      backlog_bytes: null,
      oldest_message_timestamp: null,
      available: false,
      error: "metrics unavailable",
    });
  });
});
