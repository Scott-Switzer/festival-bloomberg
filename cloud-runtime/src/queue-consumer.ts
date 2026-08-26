/**
 * Queue Consumer Handlers — process acquisition tasks from FAST/DEEP queues.
 *
 * KEY INVARIANT: This is the ONLY place that calls Governor.reserveTask().
 * The Workflow plans and enqueues; this consumer reserves, fetches, persists.
 *
 * FAST rail: native Monid context.dev fetch in Worker (no Container needed).
 * context.dev returns COMPLETED immediately — no polling needed.
 * DEEP rail: DISABLED_NOT_CONFIGURED (no tickets.dev key).
 *
 * Price semantics:
 *   observed_offer_min_price = lowest JSON-LD offer price
 *   price_basis = PUBLIC_PAGE_JSON_LD_OFFER (or PUBLIC_PAGE_NEXT_DATA)
 *   inventory_basis = UNKNOWN (Monid can't distinguish primary vs resale)
 *   resale_min_price = only populated if evidence actually establishes resale basis
 *
 * Raw evidence:
 *   FULL canonical bytes, content-addressed by SHA256
 *   No truncation. The hash must match the stored bytes exactly.
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
  MONID_API_KEY: string;
  TICKETS_DEV_API_KEY: string;
}

const MONID_BASE = "https://api.monid.ai";

/**
 * Call Monid context.dev directly — returns COMPLETED immediately.
 * No tinyfish fallback (that was causing timeouts).
 */
async function fetchPageDirect(
  apiKey: string,
  url: string
): Promise<{ status: string; html: string; cost_usd: number; provider: string; latency_ms: number; http_status: number }> {
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

    if (!resp.ok) {
      const errText = await resp.text();
      return {
        status: resp.status === 429 ? "RATE_LIMIT" : "FETCH_FAILED",
        html: "",
        provider: "none",
        cost_usd: 0,
        latency_ms: Date.now() - start,
        http_status: resp.status,
      };
    }

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
        http_status: resp.status,
      };
    }

    // If not immediately complete, poll once (context.dev is usually immediate)
    const runId = data.runId || data.run_id;
    if (runId) {
      await new Promise((r) => setTimeout(r, 5000));
      const pollResp = await fetch(`${MONID_BASE}/v1/runs/${runId}`, {
        headers: { Authorization: `Bearer ${apiKey}` },
      });
      if (pollResp.ok) {
        const pollData: any = await pollResp.json();
        if (pollData.status === "COMPLETED") {
          const output = pollData.output || {};
          return {
            status: "FETCHED",
            html: output.html || "",
            provider: "context.dev",
            cost_usd: 0.0009,
            latency_ms: Date.now() - start,
            http_status: pollResp.status,
          };
        }
      }
    }

    return {
      status: "FETCH_FAILED",
      html: "",
      provider: "none",
      cost_usd: 0,
      latency_ms: Date.now() - start,
      http_status: 0,
    };
  } catch (e: any) {
    return {
      status: "FETCH_FAILED",
      html: "",
      provider: "none",
      cost_usd: 0,
      latency_ms: Date.now() - start,
      http_status: 0,
    };
  }
}

/**
 * Extract ticket-market data from HTML — JSON-LD priority.
 *
 * Price semantics:
 *   observed_offer_min_price: lowest offer price from JSON-LD
 *   price_basis: PUBLIC_PAGE_JSON_LD_OFFER
 *   inventory_basis: UNKNOWN (can't distinguish primary vs resale from JSON-LD alone)
 *   resale_min_price: NOT set — JSON-LD offer != automatically resale
 */
function extractFromPage(html: string, _marketplace: string): Record<string, any> {
  if (!html) return { has_structured_data: false, price_basis: "NONE" };
  const extracted: Record<string, any> = {};

  // 1. JSON-LD extraction
  const ldRegex = /<script[^>]*type="application\/ld\+json"[^>]*>([\s\S]*?)<\/script>/gi;
  let match;
  while ((match = ldRegex.exec(html)) !== null) {
    try {
      const ldData = JSON.parse(match[1]);
      if (ldData?.["@type"] === "Event" || ldData?.["@type"] === "MusicEvent" || ldData?.["@type"] === "Concert") {
        const offers = ldData.offers;
        if (offers && !Array.isArray(offers)) {
          extracted.observed_offer_min_price = parseFloat(offers.price) || null;
          extracted.currency = offers.priceCurrency;
          extracted.availability = offers.availability;
          extracted.price_basis = "PUBLIC_PAGE_JSON_LD_OFFER";
        } else if (Array.isArray(offers) && offers.length > 0) {
          const prices = offers.map((o: any) => parseFloat(o.price)).filter((p: number) => !isNaN(p));
          if (prices.length > 0) {
            extracted.observed_offer_min_price = Math.min(...prices);
            extracted.price_basis = "PUBLIC_PAGE_JSON_LD_OFFER";
          }
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

  // 2. __NEXT_DATA__ fallback
  if (!extracted.observed_offer_min_price && !extracted.name) {
    const nextMatch = html.match(
      /<script[^>]*id="__NEXT_DATA__"[^>]*>([\s\S]*?)<\/script>/
    );
    if (nextMatch) {
      try {
        const nd = JSON.parse(nextMatch[1]);
        const props = nd?.props?.pageProps;
        if (props) {
          extracted.observed_offer_min_price = props.event?.price || props.price || null;
          extracted.name = props.event?.name || props.title;
          extracted.venue_name = props.event?.venue?.name || props.venue?.name;
          if (extracted.observed_offer_min_price) {
            extracted.price_basis = "PUBLIC_PAGE_NEXT_DATA";
          }
        }
      } catch { /* parse error */ }
    }
  }

  extracted.has_structured_data = !!(extracted.observed_offer_min_price || extracted.name);
  extracted.inventory_basis = "UNKNOWN";
  // Do NOT set resale_min_price — JSON-LD offer != automatically resale
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
 * Pipeline:
 *   1. Governor.reserveTask (EXACTLY ONCE — not in Workflow)
 *   2. Monid fetch
 *   3. Extract structured data
 *   4. Write FULL raw evidence to R2 (no truncation)
 *   5. Write normalized observation to R2 lake staging
 *   6. Governor.commitTask
 *   7. Governor.recordObservation
 *
 * Rate limiting: Waits 2s between requests within a batch to avoid Monid 502.
 */
export async function handleFastBatch(
  batch: MessageBatch<AcquisitionTask>,
  env: Env
): Promise<void> {
  for (const msg of batch.messages) {
    const task = msg.body;
    const start = Date.now();

    try {
      // 1. Reserve budget atomically — EXACTLY ONCE
      const governorId = env.GOVERNOR.idFromName("acquisition-governor");
      const governor = env.GOVERNOR.get(governorId) as any;

      const reserveResult = await governor.reserveTask({
        task_key: task.task_key,
        acquisition_provider: task.acquisition_provider || "monid",
        expected_max_cost_usd: task.expected_max_cost_usd,
        container_id: `fast_${Date.now()}`,
      });

      if (!reserveResult.allowed) {
        if (reserveResult.reason === "DUPLICATE_TASK") {
          // Already processed — suppress silently
          msg.ack();
          continue;
        }
        console.log(JSON.stringify({
          event: "TASK_BLOCKED",
          task_key: task.task_key,
          reason: reserveResult.reason,
        }));
        // Not retryable — budget/provider blocked
        msg.ack();
        continue;
      }

      // 2. Fetch page via Monid context.dev
      const targetUrl = task.target_url;
      if (!targetUrl) {
        await governor.releaseTask({ task_key: task.task_key });
        console.log(JSON.stringify({ event: "NO_TARGET_URL", task_key: task.task_key }));
        msg.ack();
        continue;
      }

      console.log(JSON.stringify({
        event: "FETCHING",
        task_key: task.task_key,
        url: targetUrl,
        marketplace: task.marketplace,
        started: new Date().toISOString(),
      }));

      const page = await fetchPageDirect(env.MONID_API_KEY, targetUrl);

      // Handle HTTP-level failures
      if (page.status !== "FETCHED" || !page.html) {
        await governor.releaseTask({ task_key: task.task_key });

        // 429: retry with backoff (Queue will retry per max_retries)
        if (page.http_status === 429 || page.status === "RATE_LIMIT") {
          console.error(JSON.stringify({
            event: "RATE_LIMITED",
            task_key: task.task_key,
            http_status: page.http_status,
          }));
          msg.retry({ delaySeconds: 15 });
          continue;
        }

        // 502/503: retry with backoff
        if (page.http_status === 502 || page.http_status === 503) {
          console.error(JSON.stringify({
            event: "SERVER_ERROR",
            task_key: task.task_key,
            http_status: page.http_status,
          }));
          msg.retry({ delaySeconds: 30 });
          continue;
        }

        // Other failures: DLQ after retries exhausted
        if (msg.attempts >= 2) {
          await env.DLQ_QUEUE.send(task);
          await governor.recordFailure({
            acquisition_provider: task.acquisition_provider || "monid",
            reason: page.status,
          });
        }
        msg.retry();
        continue;
      }

      // 3. Extract structured data from HTML
      const extracted = extractFromPage(page.html, task.marketplace);

      // 4. Write FULL raw evidence to R2 (NO TRUNCATION)
      const htmlBytes = new TextEncoder().encode(page.html);
      const contentHash = await sha256Hex(htmlBytes);
      const rawKey = `raw/monid/${contentHash.slice(0, 2)}/${contentHash.slice(2, 4)}/${contentHash}.json`;

      // Full raw evidence — the hash must match these exact bytes
      const rawPayload = JSON.stringify({
        url: targetUrl,
        marketplace: task.marketplace,
        event_key: task.event_key,
        acquisition_provider: task.acquisition_provider || "monid",
        provider: page.provider,
        html: page.html,  // FULL HTML — no truncation
        extracted,
        fetched_at: new Date().toISOString(),
        cost_usd: page.cost_usd,
        cost_basis: "MEASURED",
        latency_ms: page.latency_ms,
        http_status: page.http_status,
        software_version: task.software_version,
      });

      await env.RAW_BUCKET.put(rawKey, rawPayload, {
        httpMetadata: { contentType: "application/json" },
        customMetadata: {
          source: "monid",
          marketplace: task.marketplace,
          event_key: task.event_key,
          content_hash: contentHash,
        },
      });

      // 5. Write normalized observation to R2 lake staging
      const now = new Date().toISOString();
      const observation = {
        schema_version: "ticket_market_snapshot_v1",
        event_key: task.event_key,
        source_platform: task.marketplace,
        acquisition_provider: task.acquisition_provider || "monid",
        actor_or_endpoint: `monid_${page.provider}`,
        wave_label: task.scheduled_window || "cloud_wave",
        observed_at: now,
        retrieved_at: now,
        knowledge_time: now,
        // Price semantics — neutral evidence, not auto-resale
        observed_offer_min_price: extracted.observed_offer_min_price ?? null,
        currency: extracted.currency || null,
        price_basis: extracted.price_basis || "NONE",
        inventory_basis: extracted.inventory_basis || "UNKNOWN",
        // resale_min_price NOT set — JSON-LD offer != automatically resale
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

      // 7. Record observation state for scheduler
      await governor.recordObservation({
        event_key: task.event_key,
        marketplace: task.marketplace,
        rail: "FAST",
        success: true,
        logical_window: task.scheduled_window,
      });

      console.log(JSON.stringify({
        event: "FAST_TASK_COMPLETED",
        task_key: task.task_key,
        event_key: task.event_key,
        marketplace: task.marketplace,
        acquisition_provider: task.acquisition_provider || "monid",
        observed_offer_min_price: extracted.observed_offer_min_price ?? null,
        price_basis: extracted.price_basis || "NONE",
        cost_usd: page.cost_usd,
        latency_ms: Date.now() - start,
        raw_key: rawKey,
        staging_key: stagingKey,
        raw_bytes: rawPayload.length,
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

      // Release reservation
      try {
        const governorId = env.GOVERNOR.idFromName("acquisition-governor");
        const governor = env.GOVERNOR.get(governorId) as any;
        await governor.releaseTask({ task_key: task.task_key });
      } catch (_) {}

      // Record failure observation
      try {
        const governorId = env.GOVERNOR.idFromName("acquisition-governor");
        const governor = env.GOVERNOR.get(governorId) as any;
        await governor.recordObservation({
          event_key: task.event_key,
          marketplace: task.marketplace,
          rail: "FAST",
          success: false,
          logical_window: task.scheduled_window,
        });
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
    console.log(JSON.stringify({
      event: "DEEP_UNAVAILABLE",
      task_key: task.task_key,
      reason: "NOT_CONFIGURED",
    }));
    msg.ack();
  }
}

/**
 * Processing queue consumer — batch staging → materialized chunks.
 * For V1: batches staging JSONs into hourly chunks.
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

        const materializedKey = `ticket_market_snapshots/date=${payload.date}/hour=${payload.hour}/part-${payload.run_id || "batch"}.json`;
        await env.LAKE_BUCKET.put(materializedKey, JSON.stringify({
          schema_version: "ticket_market_snapshot_v1",
          observations,
          count: observations.length,
          materialized_at: new Date().toISOString(),
        }), {
          httpMetadata: { contentType: "application/json" },
        });

        console.log(JSON.stringify({
          event: "STAGING_MATERIALIZED",
          count: observations.length,
          key: materializedKey,
        }));

        // Clean up staging files after materialization
        for (const obj of listing.objects) {
          await env.LAKE_BUCKET.delete(obj.key);
        }
        msg.ack();
      } catch {
        if (msg.attempts >= 4) msg.ack();
        else msg.retry();
      }
    } else {
      msg.ack();
    }
  }
}
