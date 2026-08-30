import { writeQueueBatchMetric } from "./queue-metrics";

interface DlqEnv {
  BACKUP_BUCKET: R2Bucket;
  SOFTWARE_VERSION: string;
}

interface DlqArchiveRecord {
  schema_version: "queue_dlq_message_v1";
  queue: string;
  message_id: string;
  attempts: number;
  enqueued_at: string;
  archived_at: string;
  software_version: string;
  body: unknown;
}

function archiveKey(message: Message<unknown>): string {
  const day = message.timestamp.toISOString().slice(0, 10);
  return `evidence/queue-dlq/queue=fi-dlq/date=${day}/${message.id}.json`;
}

/**
 * Preserve every dead-letter payload before acknowledging it. The message id
 * makes the R2 write idempotent when Cloudflare redelivers after an uncertain
 * acknowledgement; a failed write deliberately leaves the message retryable.
 */
export async function handleDlqBatch(batch: MessageBatch<unknown>, env: DlqEnv): Promise<void> {
  let acked = 0;
  let retried = 0;
  for (const message of batch.messages) {
    const key = archiveKey(message);
    try {
      const archivedAt = new Date().toISOString();
      const record: DlqArchiveRecord = {
        schema_version: "queue_dlq_message_v1",
        queue: batch.queue,
        message_id: message.id,
        attempts: message.attempts,
        enqueued_at: message.timestamp.toISOString(),
        archived_at: archivedAt,
        software_version: env.SOFTWARE_VERSION,
        body: message.body,
      };
      await env.BACKUP_BUCKET.put(key, JSON.stringify(record), {
        httpMetadata: { contentType: "application/json" },
        customMetadata: { source: "CLOUDFLARE_QUEUE_DLQ", message_id: message.id, queue: batch.queue },
      });
      message.ack();
      acked++;
      console.log(JSON.stringify({ event: "DLQ_MESSAGE_ARCHIVED", queue: batch.queue, message_id: message.id, archive_key: key }));
    } catch (error) {
      console.error(JSON.stringify({ event: "DLQ_ARCHIVE_ERROR", queue: batch.queue, message_id: message.id, error: error instanceof Error ? error.message : String(error) }));
      message.retry();
      retried++;
    }
  }
  await writeQueueBatchMetric(env, { queue: batch.queue, received: batch.messages.length, acked, retried, explicit_dlq: 0 }).catch((metricError) => console.error(JSON.stringify({ event: "QUEUE_METRIC_WRITE_ERROR", queue: batch.queue, error: metricError instanceof Error ? metricError.message : String(metricError) })));
}
