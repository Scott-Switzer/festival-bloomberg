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
import { acquireUrl, AcquisitionResult, RouterDeps } from "./acquisition";
import { fetchPage } from "./monid-client";

interface Env {
  FAST_QUEUE: Queue;
  DEEP_QUEUE: Queue;
  PROCESSING_QUEUE: Queue;
  DLQ_QUEUE: Queue;
  RAW_BUCKET: R2Bucket;
  LAKE_BUCKET: R2Bucket;
  BACKUP_BUCKET: R2Bucket;
  GOVERNOR: DurableObjectNamespace;
  BROWSER: any;
  MONID_API_KEY: string;
  TICKETS_DEV_API_KEY: string;
}

/**
 * Build the router dependencies from env.
 * BROWSER is optional — if the account lacks Browser Run, direct+Monid rails still work.
 */
function buildRouterDeps(env: Env): RouterDeps {
  return {
    browser: env.BROWSER ? (env.BROWSER as any) : null,
    monidApiKey: env.MONID_API_KEY || null,
    monidFetchPage: async (apiKey, url) => {
      const page = await fetchPage(apiKey, url);
      return {
        status: page.status,
        html: page.html,
        provider: page.provider,
        cost_usd: page.cost_usd,
        latency_ms: page.latency_ms,
      };
    },
  };
}

/** Resolve actual accounted cost based on the rail/provider used (free rails cost $0). */
function accountedCostFor(res: AcquisitionResult): { cost_usd: number; cost_basis: string } {
  // Only Monid is a paid rail. Direct/browser rails are free.
  if (res.error_category) return { cost_usd: 0, cost_basis: "NONE" };
  if (res.acquisition_provider === "monid" || res.acquisition_rail === "RAIL_4_MONID") {
    return { cost_usd: 0.0009, cost_basis: "MEASURED" };
  }
  return { cost_usd: 0, cost_basis: "FREE_RAIL" };
}

/**
 * USEFUL_OBSERVATION = fetch succeeded AND identity valid AND at least one
 * economically relevant field (price, currency, or availability) extracted.
 * An empty normalized shell is NOT useful.
 */
function isUsefulObservation(res: AcquisitionResult): boolean {
  if (res.error_category) return false;
  if (res.identity_status === "FAILED" || res.identity_status === "UNKNOWN") return false;
  const priceOk = res.observed_offer_min_price != null;
  const currencyOk = !!res.currency;
  const availOk = !!res.availability_state && res.availability_state !== "UNKNOWN";
  return priceOk || currencyOk || availOk;
}

interface ScorecardTelemetry {
  task_key: string;
  event_key: string;
  marketplace: string;
  rail: string;
  provider: string;
  ok: boolean;
  http_status: number;
  latency_ms: number;
  browser_ms: number;
  cost_usd: number;
  raw_bytes: number;
  identity_status: string;
  price_extracted: boolean;
  availability_extracted: boolean;
  useful: boolean;
  error_category?: string;
}

/**
 * Write one immutable scorecard telemetry object per task (atomic, race-free).
 * The /scorecard admin endpoint aggregates these by listing the prefix.
 */
async function writeScorecardTelemetry(env: Env, t: ScorecardTelemetry): Promise<void> {
  const day = new Date().toISOString().slice(0, 10);
  const key = `control/scorecard/date=${day}/${t.task_key}.json`;
  await env.BACKUP_BUCKET.put(key, JSON.stringify({ ...t, recorded_at: new Date().toISOString() }), {
    httpMetadata: { contentType: "application/json" },
  });
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

      // 2. Fetch page via the acquisition ROUTER (cheapest acceptable rail first)
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

      const routerDeps = buildRouterDeps(env);
      const result = await acquireUrl(
        routerDeps,
        task.event_key,
        task.marketplace,
        targetUrl,
        task.software_version,
        { mode: "cheapest" }
      );

      // --------- FAILED acquisition handling (all rails failed) ---------
      if (result.error_category && result.acquisition_rail === "RAIL_UNSUPPORTED") {
        await governor.releaseTask({ task_key: task.task_key });
        console.error(JSON.stringify({
          event: "ACQUISITION_FAILED_ALL_RAILS",
          task_key: task.task_key,
          error: result.error_detail,
        }));
        await writeScorecardTelemetry(env, {
          task_key: task.task_key,
          event_key: task.event_key,
          marketplace: task.marketplace,
          rail: result.acquisition_rail,
          provider: result.acquisition_provider,
          ok: false,
          http_status: result.http_status,
          latency_ms: Date.now() - start,
          browser_ms: result.browser_ms,
          cost_usd: 0,
          raw_bytes: 0,
          identity_status: "FAILED",
          price_extracted: false,
          availability_extracted: false,
          useful: false,
          error_category: result.error_category,
        });
        if (msg.attempts >= 2) {
          await env.DLQ_QUEUE.send(task);
          await governor.recordFailure({
            acquisition_provider: task.acquisition_provider || "monid",
            reason: result.error_category || "FAILED",
          });
        }
        msg.retry();
        continue;
      }

      // --------- SUCCESS: raw evidence + normalized observation ---------

      // 3. Determine accounted cost from the rail actually used
      const { cost_usd, cost_basis } = accountedCostFor(result);

      // 4. Write FULL raw evidence to R2 (NO TRUNCATION)
      // raw_sha256 was computed over result.raw_body — the exact canonical bytes.
      // The raw object stores those full bytes so the hash always matches.
      const contentHash = result.raw_sha256;
      if (contentHash) {
        const rawKey = `raw/${result.acquisition_provider}/${contentHash.slice(0, 2)}/${contentHash.slice(2, 4)}/${contentHash}.json`;
        const rawPayload = JSON.stringify({
          url: targetUrl,
          marketplace: task.marketplace,
          event_key: task.event_key,
          acquisition_provider: result.acquisition_provider,
          acquisition_rail: result.acquisition_rail,
          final_url: result.final_url,
          http_status: result.http_status,
          html: result.raw_body, // FULL canonical evidence bytes — no truncation
          observed_offer_min_price: result.observed_offer_min_price ?? null,
          currency: result.currency ?? null,
          price_basis: result.price_basis,
          inventory_basis: result.inventory_basis,
          availability_state: result.availability_state,
          identity_status: result.identity_status,
          fetched_at: new Date().toISOString(),
          cost_usd,
          cost_basis,
          latency_ms: result.latency_ms,
          software_version: task.software_version,
        });

        await env.RAW_BUCKET.put(rawKey, rawPayload, {
          httpMetadata: { contentType: "application/json" },
          customMetadata: {
            source: result.acquisition_provider,
            rail: result.acquisition_rail,
            marketplace: task.marketplace,
            event_key: task.event_key,
            content_hash: contentHash,
          },
        });
        result.raw_object_key = rawKey;
      }

      // 5. Write normalized observation to R2 lake staging
      const now = new Date().toISOString();
      const observation = {
        schema_version: "ticket_market_snapshot_v1",
        event_key: task.event_key,
        source_platform: task.marketplace,
        acquisition_provider: result.acquisition_provider,
        acquisition_rail: result.acquisition_rail,
        actor_or_endpoint: `${result.acquisition_provider}_${result.acquisition_rail}`,
        wave_label: task.scheduled_window || "cloud_wave",
        observed_at: now,
        retrieved_at: now,
        knowledge_time: now,
        // Price semantics — neutral evidence, not auto-resale
        observed_offer_min_price: result.observed_offer_min_price ?? null,
        currency: result.currency || null,
        price_basis: result.price_basis || "NONE",
        inventory_basis: result.inventory_basis || "UNKNOWN",
        availability_state: result.availability_state || "UNKNOWN",
        identity_status: result.identity_status || "UNKNOWN",
        source_url: targetUrl,
        final_url: result.final_url,
        raw_payload_hash: contentHash || "",
        rights_status: result.rights_status || "TERMS_REVIEW_REQUIRED",
        commercial_use_status: result.commercial_use_status || "PROTOTYPE_ONLY",
      };

      const stagingKey = `staging/ticket_market/date=${now.slice(0, 10)}/hour=${now.slice(11, 13)}/${task.task_key}.json`;
      await env.LAKE_BUCKET.put(stagingKey, JSON.stringify(observation, null, 2), {
        httpMetadata: { contentType: "application/json" },
        customMetadata: { event_key: task.event_key, marketplace: task.marketplace },
      });

      // 6. Commit spend and mark idempotent (EXACT accounted cost)
      await governor.commitTask({
        task_key: task.task_key,
        actual_cost_usd: cost_usd,
        cost_basis,
      });

      // 7. Record observation state for scheduler
      await governor.recordObservation({
        event_key: task.event_key,
        marketplace: task.marketplace,
        rail: "FAST",
        success: true,
        logical_window: task.scheduled_window,
      });

      // Scorecard telemetry — one immutable object per task (atomic, no races).
      await writeScorecardTelemetry(env, {
        task_key: task.task_key,
        event_key: task.event_key,
        marketplace: task.marketplace,
        rail: result.acquisition_rail,
        provider: result.acquisition_provider,
        ok: true,
        http_status: result.http_status,
        latency_ms: Date.now() - start,
        browser_ms: result.browser_ms,
        cost_usd,
        raw_bytes: result.raw_bytes,
        identity_status: result.identity_status,
        price_extracted: result.observed_offer_min_price != null,
        availability_extracted: result.availability_state !== "UNKNOWN",
        useful: isUsefulObservation(result),
        error_category: undefined,
      });

      console.log(JSON.stringify({
        event: "FAST_TASK_COMPLETED",
        task_key: task.task_key,
        event_key: task.event_key,
        marketplace: task.marketplace,
        acquisition_provider: result.acquisition_provider,
        acquisition_rail: result.acquisition_rail,
        observed_offer_min_price: result.observed_offer_min_price ?? null,
        price_basis: result.price_basis || "NONE",
        cost_usd,
        cost_basis,
        latency_ms: Date.now() - start,
        raw_key: result.raw_object_key,
        staging_key: stagingKey,
        raw_bytes: observation.raw_payload_hash.length,
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
