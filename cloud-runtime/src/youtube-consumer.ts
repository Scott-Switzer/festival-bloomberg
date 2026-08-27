import { collectYouTubeBatch, YouTubeChannelIdentity } from "./youtube-cloud";

interface YouTubeEnv {
  RAW_BUCKET: R2Bucket;
  LAKE_BUCKET: R2Bucket;
  BACKUP_BUCKET: R2Bucket;
  YOUTUBE_API_KEY: string;
  SOFTWARE_VERSION: string;
}

export async function handleYouTubeBatch(batch: MessageBatch<any>, env: YouTubeEnv): Promise<void> {
  const identities: YouTubeChannelIdentity[] = [];
  const messages = [...batch.messages];
  for (const msg of messages) {
    const body = msg.body as { tasks?: Array<{ artist_key?: string; event_key?: string; target_url?: string; youtube_channel_id?: string }>; artist_key?: string; event_key?: string; target_url?: string; youtube_channel_id?: string };
    const tasks = body.tasks || [body];
    for (const task of tasks) {
      const channel = task.youtube_channel_id || task.target_url;
      const artist = task.artist_key || task.event_key;
      if (channel && artist) identities.push({ artist_key: artist, youtube_channel_id: channel });
    }
  }
  try {
    const result = await collectYouTubeBatch(env, identities, { maxChannels: 50 });
    console.log(JSON.stringify({ event: "YOUTUBE_BATCH_COMPLETED", ...result }));
    for (const msg of messages) msg.ack();
  } catch (error) {
    console.error(JSON.stringify({ event: "YOUTUBE_BATCH_ERROR", error: error instanceof Error ? error.message : String(error) }));
    for (const msg of messages) msg.retry();
  }
}

export async function handleStructuredBatch(batch: MessageBatch<any>, env: any): Promise<void> {
  // Structured ticket tasks remain on the existing acquisition path until the
  // official Ticketmaster adapter is enabled; they are not sent through Monid
  // by the YouTube queue.
  for (const msg of batch.messages) {
    console.log(JSON.stringify({ event: "STRUCTURED_TASK_DEFERRED", task_key: msg.body?.task_key || null, reason: "TICKETMASTER_ADAPTER_PENDING" }));
    msg.ack();
  }
}
