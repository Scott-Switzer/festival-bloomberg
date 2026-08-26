/**
 * Processing Queue Consumer — raw evidence → normalized observations.
 *
 * P0.12 fix: Do NOT attempt to decompress zstd in TypeScript Workers.
 * Python containers handle decompression via R2ObjectStore.
 *
 * For V1: raw evidence is written by the acquisition Container.
 * Normalization and Parquet compaction happen in a separate compaction step.
 */

import { AcquisitionTask, TaskResult } from "./task-contract";

interface Env {
  RAW_BUCKET: R2Bucket;
  LAKE_BUCKET: R2Bucket;
  PROCESSING_QUEUE: Queue;
  DLQ_QUEUE: Queue;
}

/**
 * Handle processing queue batch.
 *
 * P0.11/P0.12: The actual normalization and Parquet writes happen in Python.
 * This handler coordinates the pipeline, not the parsing.
 */
export async function handleProcessingBatch(
  batch: MessageBatch<any>,
  env: Env
): Promise<void> {
  for (const msg of batch.messages) {
    const payload = msg.body;

    if (payload.type === "NORMALIZE_RAW") {
      try {
        // Verify raw object exists in R2
        const rawObj = await env.RAW_BUCKET.get(payload.raw_key);
        if (!rawObj) {
          console.error(JSON.stringify({
            event: "RAW_NOT_FOUND",
            key: payload.raw_key,
          }));
          msg.ack();
          continue;
        }

        // For V1: log receipt and mark for compaction
        // Full Parquet materialization happens in a compaction Container
        console.log(JSON.stringify({
          event: "RAW_EVIDENCE_RECEIVED",
          raw_key: payload.raw_key,
          task_key: payload.task_key,
          event_key: payload.event_key,
          marketplace: payload.marketplace,
          size: rawObj.size,
          // In production: trigger compaction Container
          // that reads raw, normalizes, writes Parquet to lake
        }));

        msg.ack();
      } catch (e: unknown) {
        console.error(JSON.stringify({
          event: "PROCESSING_FAILED",
          key: payload.raw_key,
          error: String(e instanceof Error ? e.message : e),
        }));
        if (msg.attempts >= 4) {
          msg.ack();
        } else {
          msg.retry();
        }
      }
    } else {
      msg.ack();
    }
  }
}
