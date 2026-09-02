import { describe, expect, it, vi } from "vitest";
import { handleDlqBatch } from "../src/dlq-consumer";

function bucket() {
  const writes = new Map<string, string>();
  return {
    writes,
    put: async (key: string, value: string) => { writes.set(key, value); },
  } as unknown as R2Bucket & { writes: Map<string, string> };
}

describe("DLQ consumer", () => {
  it("archives the complete payload before acknowledging", async () => {
    const backup = bucket();
    const ack = vi.fn();
    const retry = vi.fn();
    const timestamp = new Date("2026-08-30T12:00:00Z");
    await handleDlqBatch({
      queue: "fi-dlq",
      messages: [{ id: "message-1", timestamp, attempts: 4, body: { task_key: "task-1", error: "429" }, ack, retry }],
    } as unknown as MessageBatch<unknown>, { BACKUP_BUCKET: backup, SOFTWARE_VERSION: "test" });

    expect(ack).toHaveBeenCalledOnce();
    expect(retry).not.toHaveBeenCalled();
    const archive = [...backup.writes.entries()].find(([key]) => key.includes("evidence/queue-dlq/"));
    expect(archive).toBeDefined();
    expect(JSON.parse(archive![1])).toMatchObject({
      schema_version: "queue_dlq_message_v1",
      queue: "fi-dlq",
      message_id: "message-1",
      attempts: 4,
      body: { task_key: "task-1", error: "429" },
    });
  });

  it("keeps a message retryable when private evidence archival fails", async () => {
    const ack = vi.fn();
    const retry = vi.fn();
    const backup = { put: vi.fn(async () => { throw new Error("R2 unavailable"); }) } as unknown as R2Bucket;
    await handleDlqBatch({
      queue: "fi-dlq",
      messages: [{ id: "message-2", timestamp: new Date("2026-08-30T12:00:00Z"), attempts: 1, body: { task_key: "task-2" }, ack, retry }],
    } as unknown as MessageBatch<unknown>, { BACKUP_BUCKET: backup, SOFTWARE_VERSION: "test" });

    expect(ack).not.toHaveBeenCalled();
    expect(retry).toHaveBeenCalledOnce();
  });
});
