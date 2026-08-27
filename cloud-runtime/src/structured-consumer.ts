interface StructuredEnv {
  RAW_BUCKET: R2Bucket;
  LAKE_BUCKET: R2Bucket;
  TICKETMASTER_API_KEY: string;
  SOFTWARE_VERSION: string;
}

interface TicketTask {
  task_key?: string;
  event_key: string;
  target_url?: string;
  provider_event_id?: string;
  marketplace?: string;
  event_metadata?: Record<string, unknown>;
}

async function sha256(text: string): Promise<string> {
  const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function handleStructuredBatch(batch: MessageBatch<TicketTask>, env: StructuredEnv): Promise<void> {
  for (const msg of batch.messages) {
    const task = msg.body;
    try {
      if (!env.TICKETMASTER_API_KEY) throw new Error("TICKETMASTER_API_KEY not configured");
      const id = task.provider_event_id || task.target_url?.match(/event\/([^/?]+)/i)?.[1];
      if (!id) throw new Error("provider event id required");
      const response = await fetch(`https://app.ticketmaster.com/discovery/v2/events/${encodeURIComponent(id)}.json?apikey=${encodeURIComponent(env.TICKETMASTER_API_KEY)}`);
      const raw = await response.text();
      if (!response.ok) {
        const detail = `Ticketmaster HTTP ${response.status}`;
        if (response.status === 400 || response.status === 401 || response.status === 403 || response.status === 404) {
          console.error(JSON.stringify({ event: "STRUCTURED_TASK_TERMINAL_FAILURE", task_key: task.task_key || null, event_key: task.event_key, error: detail }));
          msg.ack();
          continue;
        }
        throw new Error(detail);
      }
      const hash = await sha256(raw);
      const now = new Date().toISOString();
      const rawKey = `raw/ticketmaster/${hash.slice(0, 2)}/${hash.slice(2, 4)}/${hash}.json`;
      if (!(await env.RAW_BUCKET.head(rawKey))) await env.RAW_BUCKET.put(rawKey, raw, { httpMetadata: { contentType: "application/json" }, customMetadata: { source: "TICKETMASTER_API", content_hash: hash, event_key: task.event_key } });
      const data: any = JSON.parse(raw);
      const price = data.priceRanges?.[0];
      const observation = {
        schema_version: "ticketmaster_structured_observation_v1",
        event_key: task.event_key,
        provider_event_id: id,
        observed_at: now,
        retrieved_at: now,
        knowledge_time: now,
        status: data.status?.code || null,
        onsale_start: data.sales?.public?.startDateTime || null,
        onsale_end: data.sales?.public?.endDateTime || null,
        standard_primary_min: price?.min ?? null,
        standard_primary_max: price?.max ?? null,
        currency: price?.currency || null,
        promoter: data.promoter?.name || null,
        event_url: data.url || null,
        raw_evidence_ref: `r2://${rawKey}`,
        source: "TICKETMASTER_API",
        rights_status: "PROVIDER_TERMS_REVIEW_REQUIRED",
        commercial_use_status: "INTERNAL_ANALYTICS_ONLY",
      };
      const key = `staging/ticketmaster/date=${now.slice(0, 10)}/hour=${now.slice(11, 13)}/${task.task_key || id}-${hash.slice(0, 12)}.json`;
      await env.LAKE_BUCKET.put(key, JSON.stringify(observation), { httpMetadata: { contentType: "application/json" } });
      console.log(JSON.stringify({ event: "STRUCTURED_TASK_COMPLETED", task_key: task.task_key || null, event_key: task.event_key, raw_key: rawKey, staging_key: key, source: "TICKETMASTER_API" }));
      msg.ack();
    } catch (error) {
      console.error(JSON.stringify({ event: "STRUCTURED_TASK_ERROR", task_key: task.task_key || null, error: error instanceof Error ? error.message : String(error) }));
      msg.retry();
    }
  }
}
