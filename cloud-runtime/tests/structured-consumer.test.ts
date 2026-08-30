import { describe, expect, it, vi } from "vitest";
import { handleStructuredBatch } from "../src/structured-consumer";

describe("structured consumer", () => {
  it("acks a task without a provider id as terminal instead of retrying", async () => {
    const ack = vi.fn();
    const retry = vi.fn();
    const env = {
      RAW_BUCKET: {} as R2Bucket,
      LAKE_BUCKET: {} as R2Bucket,
      BACKUP_BUCKET: { put: vi.fn() } as unknown as R2Bucket,
      TICKETMASTER_API_KEY: "",
      SOFTWARE_VERSION: "test",
    };
    await handleStructuredBatch({
      queue: "fi-structured-api",
      messages: [{ body: { event_key: "bad", target_url: "https://www.ticketmaster.com/venue/123" }, ack, retry }],
    } as unknown as MessageBatch<any>, env);

    expect(ack).toHaveBeenCalledOnce();
    expect(retry).not.toHaveBeenCalled();
  });
});
