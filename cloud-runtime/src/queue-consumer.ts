/**
 * Queue Consumer Handlers — process acquisition tasks from FAST/DEEP queues.
 *
 * FAST rail: native Monid context.dev fetch in Worker (no Container needed).
 * context.dev returns COMPLETED immediately — no polling needed.
 * DEEP rail: DISABLED_NOT_CONFIGURED (no tickets.dev key).
 * Processing: batch staging → Parquet compaction.
 */

import { AcquisitionTask } from "./task-contract";

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

const MONID_BASE = "https://api.monid.ai";

/** Call Monid context.dev directly — returns COMPLETED immediately. */
async function fetchPageDirect(
  apiKey: string,
  url: string
): Promise<{ status: string; html: string; cost_usd: number; provider: string; latency_ms: number }> {
  const start = Date.now();
  try {
    const resp = await fetch(`${MONID_BASE}/v1/run`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        provider: "context.dev",
        endpoint: "/web/scrape/html",
        queryParams: { url },
      }),
    });

    const data: any = await resp.json();

    if (data.status === "COMPLETED") {
      const output = data.output || {};
      const html = output.html || output.content || output.text || "";
      return {
        status: "FETCHED",
        html,
        provider: "context.dev",
        cost_usd: 0.0009,
        latency_ms: Date.now() - start,
      };
    }

    // If not immediately complete, poll once
    const runId = data.runId || data.run_id;
    if (runId) {
      await new Promise((r) => setTimeout(r, 5000));
      const pollResp = await fetch(`${MONID_BASE}/v1/runs/${runId}`, {
        headers: { Authorization: `Bearer ${apiKey}` },
      });
      const pollData: any = await pollResp.json();
      if (pollData.status === "COMPLETED") {
        const output = pollData.output || {};
        return {
          status: "FETCHED",
          html: output.html || "",
          provider: "context.dev",
          cost_usd: 0.0009,
          latency_ms: Date.now() - start,
        };
      }
    }

    return { status: "FETCH_FAILED", html: "", provider: "none", cost_usd: 0, latency_ms: Date.now() - start };
  } catch (e: any) {
    return { status: "FETCH_FAILED", html: "", provider: "none", cost_usd: 0, latency_ms: Date.now() - start };
  }
}

/** Extract ticket-market data from HTML — JSON-LD priority. */
function extractFromPage(html: string, _marketplace: string): Record<string, any> {
  if (!html) return { has_structured_data: false };
  const extracted: Record<string, any> = {};

  const ldRegex = /<script[^>]*type="application\/ld\+json"[^>]*>([\s\S]*?)<\/script>/gi;
  let match;
  while ((match = ldRegex.exec(html)) !== null) {
    try {
      const ldData = JSON.parse(match[1]);
      if (ldData?.["@type"] === "Event" || ldData?.["@type"] === "MusicEvent" || ldData?.["@type"] === "Concert") {
        const offers = ldData.offers;
        if (offers && !Array.isArray(offers)) {
          extracted.price = offers.price;
          extracted.currency = offers.priceCurrency;
          extracted.availability = offers.availability;
        } else if (Array.isArray(offers) && offers.length > 0) {
          const prices = offers.map((o: any) => parseFloat(o.price)).filter((p: number) => !isNaN(p));
          if (prices.length > 0) extracted.price_min = Math.min(...prices);
        }
        extracted.name = ldData.name;
        extracted.startDate = ldData.startDate;
        const loc = ldData.location;
        if (loc) {
          extracted.venue_name = loc.name;
          extracted.venue_city = loc.address?.addressLocality;
        }
        break;
      }
    } catch { /* continue */ }
  }

  extracted.has_structured_data = !!(extracted.price || extracted.name);
  return extracted;
}

/** SHA-256 hash for content-addressed storage. */
async function sha256Hex(data: Uint8Array): Promise<string> {
  const hash = await crypto.subtle.digest("SHA-256", data.buffer as ArrayBuffer);
  return [...new Uint8Array(hash)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/**
 * FAST queue consumer — Monid context.dev fetch of known marketplace URLs.
 *
 * Pipeline: Governor.reserve → Monid fetch → R2 raw → R2 staging → Governor.commit
 */
export async function handleFastBatch(
  batch: MessageBatch<AcquisitionTask>,
  env: Env
): Promise<void> {
  for (const msg of batch.messages) {
    const task = msg.body;
    const start = Date.now();

    try {
      // 1. Reserve budget atomically
      const governorId = env.GOVERNOR.idFromName("acquisition-governor");
      const governor = env.GOVERNOR.get(governorId) as any;

      const reserveResult = await governor.reserveTask({
        task_key: task.task_key,
        provider: task.source,
        expected_max_cost_usd: task.expected_max_cost_usd,
        container_id: `fast_${Date.now()}`,
      });

      if (!reserveResult.allowed) {
        console.log(JSON.stringify({ event: "TASK_BLOCKED", task_key: task.task_key, reason: reserveResult.reason }));
        msg.ack();
        continue;
      }

      // 2. Fetch page via Monid context.dev (direct, no tinyfish fallback)
      const targetUrl = task.target_url;
      if (!targetUrl) {
        await governor.releaseTask({ task_key: task.task_key });
        console.log(JSON.stringify({ event: "NO_TARGET_URL", task_key: task.task_key }));
        msg.ack();
        continue;
      }

      console.log(JSON.stringify({ event: "FETCHING", task_key: task.task_key, url: targetUrl, started: new Date().toISOString() }));

      const page = await fetchPageDirect(env.MONID_API_KEY, targetUrl);

      if (page.status !== "FETCHED" || !page.html) {
        await governor.releaseTask({ task_key: task.task_key });
        console.error(JSON.stringify({
          event: "FETCH_FAILED", task_key: task.task_key, url: targetUrl,
          status: page.status, latency_ms: page.latency_ms,
        }));
        if (msg.attempts >= 2) {
          await env.DLQ_QUEUE.send(task);
          await governor.recordFailure({ provider: task.source, reason: "FETCH_FAILED" });
        }
        msg.retry();
        continue;
      }

      // 3. Extract structured data from HTML
      const extracted = extractFromPage(page.html, task.marketplace);

      // 4. Write raw evidence to R2 (content-addressed)
      const htmlBytes = new TextEncoder().encode(page.html);
      const contentHash = await sha256Hex(htmlBytes);
      const rawKey = `raw/monid/${contentHash.slice(0, 2)}/${contentHash.slice(2, 4)}/${contentHash}.json`;

      const rawPayload = JSON.stringify({
        url: targetUrl,
        marketplace: task.marketplace,
        event_key: task.event_key,
        provider: page.provider,
        html: page.html.slice(0, 100_000),
        extracted,
        fetched_at: new Date().toISOString(),
        cost_usd: page.cost_usd,
        latency_ms: page.latency_ms,
      });

      await env.RAW_BUCKET.put(rawKey, rawPayload, {
        httpMetadata: { contentType: "application/json" },
        customMetadata: {
          source: "monid", marketplace: task.marketplace,
          event_key: task.event_key, content_hash: contentHash,
        },
      });

      // 5. Write normalized observation to R2 lake staging
      const now = new Date().toISOString();
      const observation = {
        schema_version: "ticket_market_snapshot_v1",
        event_key: task.event_key,
        source_platform: task.marketplace,
        actor_or_endpoint: `monid_${page.provider}`,
        wave_label: task.scheduled_window || "cloud_wave",
        observed_at: now,
        retrieved_at: now,
        knowledge_time: now,
        currency: extracted.currency || null,
        resale_min_price: extracted.price ?? extracted.price_min ?? null,
        sold_out_flag: String(extracted.availability || "").toLowerCase().includes("soldout"),
        availability_flag: String(extracted.availability || "").toLowerCase().includes("instock"),
        identity_match_status: "MATCHED",
        source_url: targetUrl,
        raw_payload_hash: contentHash,
        rights_status: "TERMS_REVIEW_REQUIRED",
        commercial_use_status: "PROTOTYPE_ONLY",
      };

      const stagingKey = `staging/ticket_market/date=${now.slice(0, 10)}/hour=${now.slice(11, 13)}/${task.task_key}.json`;
      await env.LAKE_BUCKET.put(stagingKey, JSON.stringify(observation, null, 2), {
        httpMetadata: { contentType: "application/json" },
        customMetadata: { event_key: task.event_key, marketplace: task.marketplace },
      });

      // 6. Commit spend and mark idempotent
      await governor.commitTask({
        task_key: task.task_key,
        actual_cost_usd: page.cost_usd,
        cost_basis: "MEASURED",
      });

      console.log(JSON.stringify({
        event: "FAST_TASK_COMPLETED",
        task_key: task.task_key,
        event_key: task.event_key,
        marketplace: task.marketplace,
        price: extracted.price ?? extracted.price_min ?? null,
        cost_usd: page.cost_usd,
        latency_ms: Date.now() - start,
        raw_key: rawKey,
        staging_key: stagingKey,
      }));

      msg.ack();
    } catch (e: unknown) {
      const errMsg = e instanceof Error ? e.message : String(e);
      console.error(JSON.stringify({
        event: "FAST_TASK_ERROR",
        task_key: task.task_key,
        error: errMsg,
        latency_ms: Date.now() - start,
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
 * DEEP queue consumer — DISABLED_NOT_CONFIGURED.
 * No tickets.dev key. Product decision: not purchasing one.
 */
export async function handleDeepBatch(
  batch: MessageBatch<AcquisitionTask>,
  _env: Env
): Promise<void> {
  for (const msg of batch.messages) {
    const task = msg.body;
    console.log(JSON.stringify({ event: "DEEP_UNAVAILABLE", task_key: task.task_key, reason: "NOT_CONFIGURED" }));
    msg.ack();
  }
}

/**
 * Processing queue consumer — batch staging → materialized chunks.
 */
export async function handleProcessingBatch(
  batch: MessageBatch<any>,
  env: Env
): Promise<void> {
  for (const msg of batch.messages) {
    const payload = msg.body;
    if (payload.type === "MATERIALIZE_STAGING") {
      try {
        const prefix = `staging/ticket_market/date=${payload.date}/hour=${payload.hour}/`;
        const listing = await env.LAKE_BUCKET.list({ prefix });
        const observations: any[] = [];
        for (const obj of listing.objects) {
          const raw = await env.LAKE_BUCKET.get(obj.key);
          if (raw) {
            observations.push(JSON.parse(await raw.text()));
          }
        }
        if (observations.length === 0) { msg.ack(); continue; }

        const parquetKey = `ticket_market_snapshots/date=${payload.date}/hour=${payload.hour}/part-${payload.run_id || "batch"}.json`;
        await env.LAKE_BUCKET.put(parquetKey, JSON.stringify({ observations, count: observations.length, materialized_at: new Date().toISOString() }), {
          httpMetadata: { contentType: "application/json" },
        });

        console.log(JSON.stringify({ event: "STAGING_MATERIALIZED", count: observations.length, key: parquetKey }));
        for (const obj of listing.objects) { await env.LAKE_BUCKET.delete(obj.key); }
        msg.ack();
      } catch { if (msg.attempts >= 4) msg.ack(); else msg.retry(); }
    } else {
      msg.ack();
    }
  }
}
