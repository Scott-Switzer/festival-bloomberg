/**
 * Queue Consumer Handlers — process acquisition tasks from FAST/DEEP queues.
 *
 * Uses RPC methods for Governor interaction (no fake HTTP URLs).
 * Uses correct Container DO for execution.
 * Cost semantics match the existing router contract exactly.
 */

import { AcquisitionTask, TaskResult } from "./task-contract";

interface Env {
  FAST_QUEUE: Queue;
  DEEP_QUEUE: Queue;
  PROCESSING_QUEUE: Queue;
  DLQ_QUEUE: Queue;
  RAW_BUCKET: R2Bucket;
  LAKE_BUCKET: R2Bucket;
  BACKUP_BUCKET: R2Bucket;
  GOVERNOR: DurableObjectNamespace;
  ACQUISITION_CONTAINER: DurableObjectNamespace;
  MONID_API_KEY: string;
  TICKETS_DEV_API_KEY: string;
  FI_R2_ACCESS_KEY_ID: string;
  FI_R2_SECRET_ACCESS_KEY: string;
  FI_R2_RAW_BUCKET: string;
}

/**
 * FAST queue consumer — cheap/high-volume Monid event-state tasks.
 */
export async function handleFastBatch(
  batch: MessageBatch<AcquisitionTask>,
  env: Env
): Promise<void> {
  for (const msg of batch.messages) {
    const task = msg.body;

    try {
      // Get Governor DO via RPC
      const governorId = env.GOVERNOR.idFromName("acquisition-governor");
      const governor = env.GOVERNOR.get(governorId) as any;

      // Pre-reserve budget (atomic)
      const reserveResult = await governor.reserveTask({
        task_key: task.task_key,
        provider: task.source,
        expected_max_cost_usd: task.expected_max_cost_usd,
        container_id: `fast_${Date.now()}`,
      });

      if (!reserveResult.allowed) {
        if (reserveResult.reason === "DUPLICATE_TASK") {
          msg.ack();
          continue;
        }
        // Budget/rate blocked — release and ack
        msg.ack();
        continue;
      }

      // Execute via Container DO RPC
      const containerId = env.ACQUISITION_CONTAINER.idFromName(
        `collector-${parseInt(task.task_key.slice(5, 8), 36) % 3}`
      );
      const container = env.ACQUISITION_CONTAINER.get(containerId) as any;

      const result: TaskResult = await container.runTask({
        task,
        env_vars: {
          FI_R2_ENDPOINT: `https://${env.FI_R2_ACCESS_KEY_ID ? "" : ""}r2.cloudflarestorage.com`,
          FI_R2_ACCESS_KEY_ID: env.FI_R2_ACCESS_KEY_ID || "",
          FI_R2_SECRET_ACCESS_KEY: env.FI_R2_SECRET_ACCESS_KEY || "",
          FI_R2_RAW_BUCKET: env.FI_R2_RAW_BUCKET || "festival-intelligence-raw",
        },
        timeout_seconds: 120,
      });

      if (result.status === "COMPLETED") {
        // Commit spend and mark idempotent
        await governor.commitTask({
          task_key: task.task_key,
          actual_cost_usd: result.actual_cost_usd,
          cost_basis: result.cost_basis || "MEASURED",
        });

        // Queue for processing if raw evidence was written
        if (result.raw_object_key) {
          await env.PROCESSING_QUEUE.send({
            type: "NORMALIZE_RAW",
            raw_key: result.raw_object_key,
            task_key: task.task_key,
            event_key: task.event_key,
            marketplace: task.marketplace,
          });
        }

        msg.ack();
      } else {
        // Failed — release reservation (NOT commit)
        await governor.releaseTask({ task_key: task.task_key });

        if (msg.attempts >= 2) {
          await env.DLQ_QUEUE.send(task);
          await governor.recordFailure({
            provider: task.source,
            reason: result.error_category || "UNKNOWN",
          });
        }
        msg.retry();
      }
    } catch (e: unknown) {
      const errMsg = e instanceof Error ? String(e instanceof Error ? e.message : e) : String(e);
      console.error(JSON.stringify({
        event: "FAST_TASK_ERROR",
        task_key: task.task_key,
        error: errMsg,
      }));

      try {
        const governorId = env.GOVERNOR.idFromName("acquisition-governor");
        const governor = env.GOVERNOR.get(governorId) as any;
        await governor.releaseTask({ task_key: task.task_key });
      } catch (_) {}

      if (msg.attempts >= 2) {
        await env.DLQ_QUEUE.send(task);
      }
      msg.retry();
    }
  }
}

/**
 * DEEP queue consumer — expensive/selective tickets.dev captures.
 */
/**
 * DEEP queue consumer — DISABLED_NOT_CONFIGURED.
 * No tickets.dev key. Product decision: not purchasing one.
 * This is NOT an error.
 */
export async function handleDeepBatch(
  batch: MessageBatch<AcquisitionTask>,
  _env: Env
): Promise<void> {
  for (const msg of batch.messages) {
    const task = msg.body;
    console.log(JSON.stringify({
      event: "DEEP_UNAVAILABLE",
      task_key: task.task_key,
      reason: "NOT_CONFIGURED",
    }));
    msg.ack();
  }
}

/**
 * Processing queue consumer — raw evidence → normalized observations.
 * The actual normalization happens in Python containers.
 */
export async function handleProcessingBatch(
  batch: MessageBatch<any>,
  env: Env
): Promise<void> {
  for (const msg of batch.messages) {
    const payload = msg.body;

    if (payload.type === "NORMALIZE_RAW") {
      try {
        // Download raw from R2 and trigger normalization
        const rawObj = await env.RAW_BUCKET.get(payload.raw_key);
        if (!rawObj) {
          console.error(JSON.stringify({ event: "RAW_NOT_FOUND", key: payload.raw_key }));
          msg.ack();
          continue;
        }

        // For V1, just log that we received the raw evidence
        // Full normalization will happen in compaction Container
        console.log(JSON.stringify({
          event: "RAW_EVIDENCE_RECEIVED",
          raw_key: payload.raw_key,
          task_key: payload.task_key,
          size: rawObj.size,
        }));

        msg.ack();
      } catch (e: unknown) {
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
