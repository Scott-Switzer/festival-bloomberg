/**
 * Festival Intelligence Acquisition Runtime — Worker Entry Point.
 *
 * Exports:
 * - default: fetch + queue handlers
 * - AcquisitionGovernor: Durable Object
 * - AcquisitionContainer: Container-enabled Durable Object
 * - AcquisitionWorkflow: Workflow entrypoint
 *
 * Security:
 * - /health: public, no auth required
 * - All admin endpoints: require ADMIN_TOKEN header
 * - /test-fetch, /test-monid: staging-only, should be removed after acceptance
 */

import { AcquisitionGovernor } from "./governor-do";
import { AcquisitionContainer } from "./container-do";
import { AcquisitionWorkflow } from "./workflow";
import { handleFastBatch, handleDeepBatch, handleProcessingBatch } from "./queue-consumer";
import { planTasks, loadUniverse } from "./planner";

export { AcquisitionGovernor, AcquisitionContainer, AcquisitionWorkflow };

interface Env {
  FAST_QUEUE: Queue;
  DEEP_QUEUE: Queue;
  PROCESSING_QUEUE: Queue;
  DLQ_QUEUE: Queue;
  RAW_BUCKET: R2Bucket;
  LAKE_BUCKET: R2Bucket;
  BACKUP_BUCKET: R2Bucket;
  GOVERNOR: DurableObjectNamespace;
  ACQUISITION_WORKFLOW: Workflow;
  MONID_API_KEY: string;
  TICKETMASTER_API_KEY: string;
  TICKETS_DEV_API_KEY: string;
  FI_R2_ACCESS_KEY_ID: string;
  FI_R2_SECRET_ACCESS_KEY: string;
  FI_R2_RAW_BUCKET: string;
  POLICY_VERSION: string;
  SOFTWARE_VERSION: string;
  DAILY_BUDGET_USD: string;
  MONTHLY_BUDGET_USD: string;
  ENABLE_DEEP_RAIL: string;
  ADMIN_TOKEN: string;
}

/** Check if request has valid admin auth */
function isAdminAuth(request: Request, env: Env): boolean {
  const authHeader = request.headers.get("Authorization");
  const tokenHeader = request.headers.get("X-Admin-Token");
  const expected = env.ADMIN_TOKEN;
  if (!expected) return true; // If no token configured, allow (development)
  return authHeader === `Bearer ${expected}` || tokenHeader === expected;
}

/** Extract SHA-256 hex string */
async function sha256Hex(data: Uint8Array): Promise<string> {
  const hash = await crypto.subtle.digest("SHA-256", data.buffer as ArrayBuffer);
  return [...new Uint8Array(hash)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    // ── PUBLIC ENDPOINTS ──────────────────────────────────────

    if (url.pathname === "/health") {
      return Response.json({
        status: "ok",
        version: env.SOFTWARE_VERSION,
        policy_version: env.POLICY_VERSION,
      });
    }

    // ── ADMIN ENDPOINTS (require auth) ───────────────────────

    // Admin token check for all non-health endpoints
    if (!isAdminAuth(request, env)) {
      return Response.json({ error: "Unauthorized" }, { status: 401 });
    }

    if (url.pathname === "/governor") {
      const governorId = env.GOVERNOR.idFromName("acquisition-governor");
      const governor = env.GOVERNOR.get(governorId) as any;
      const summary = await governor.getReservationSummary();
      return Response.json(summary);
    }

    if (url.pathname === "/reset-governor" && request.method === "POST") {
      const governorId = env.GOVERNOR.idFromName("acquisition-governor");
      const governor = env.GOVERNOR.get(governorId) as any;
      await governor.resetState();
      return Response.json({ status: "governor_reset" });
    }

    if (url.pathname === "/trigger" && request.method === "POST") {
      const instance = await env.ACQUISITION_WORKFLOW.create();
      return Response.json({ instance_id: instance.id, status: "triggered" });
    }

    if (url.pathname === "/dispatch" && request.method === "POST") {
      // Direct dispatch: read universe, create tasks, send to FAST queue.
      // Uses the shared planner. Auth-protected.
      try {
        const body = await request.json() as { max_events?: number; force?: boolean };
        const maxTasks = body.max_events || 25;

        const result = await planTasks(env, {
          max_tasks: maxTasks,
          // Admin /dispatch can force-ignore cadence for manual pilot runs
          getLastObservedHoursAgo: body.force ? async () => null : undefined,
        });

        return Response.json({
          status: "DISPATCHED",
          candidate_pairs: result.candidate_pairs,
          due_pairs: result.due_pairs,
          tasks_dispatched: result.queued,
          window: result.window,
          tasks: result.tasks,
        });
      } catch (e: any) {
        return Response.json({ error: e.message || String(e) }, { status: 500 });
      }
    }

    if (url.pathname === "/trigger-immediate" && request.method === "POST") {
      // Trigger immediate workflow run (for pilot)
      const instance = await env.ACQUISITION_WORKFLOW.create();
      return Response.json({ instance_id: instance.id, status: "triggered_immediate" });
    }

    if (url.pathname === "/observation-state" && request.method === "GET") {
      // Query observation state for a specific event
      const eventKey = url.searchParams.get("event_key");
      const marketplace = url.searchParams.get("marketplace");
      const rail = url.searchParams.get("rail") || "FAST";
      if (!eventKey || !marketplace) {
        return Response.json({ error: "event_key and marketplace required" }, { status: 400 });
      }
      const governorId = env.GOVERNOR.idFromName("acquisition-governor");
      const governor = env.GOVERNOR.get(governorId) as any;
      const state = await governor.getObservationState({ event_key: eventKey, marketplace, rail });
      return Response.json({ event_key: eventKey, marketplace, rail, state });
    }

    // ── STAGING-ONLY ENDPOINTS (remove after acceptance) ─────

    if (url.pathname === "/test-monid" && request.method === "POST") {
      try {
        const body = await request.json() as { url: string };
        const targetUrl = body.url;
        if (!targetUrl) return Response.json({ error: "url required" }, { status: 400 });

        const apiKey = env.MONID_API_KEY;
        const resp = await fetch(`${MONID_BASE}/v1/run`, {
          method: "POST",
          headers: {
            Authorization: `Bearer ${apiKey}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            provider: "context.dev",
            endpoint: "/web/scrape/html",
            queryParams: { url: targetUrl },
          }),
        });

        const data: any = await resp.json();
        if (data.status === "COMPLETED") {
          return Response.json({
            status: "IMMEDIATE_COMPLETE",
            html_length: data.output?.html?.length || 0,
            has_json_ld: (data.output?.html || "").includes("application/ld+json"),
          });
        }

        const runId = data.runId || data.run_id;
        if (runId) {
          await new Promise((r) => setTimeout(r, 8000));
          const pollResp = await fetch(`${MONID_BASE}/v1/runs/${runId}`, {
            headers: { Authorization: `Bearer ${apiKey}` },
          });
          const pollData: any = await pollResp.json();
          return Response.json({
            status: pollData.status,
            html_length: pollData.output?.html?.length || 0,
          });
        }

        return Response.json({ status: data.status || "UNKNOWN" });
      } catch (e: any) {
        return Response.json({ error: e.message || String(e) }, { status: 500 });
      }
    }

    if (url.pathname === "/test-fetch" && request.method === "POST") {
      try {
        const body = await request.json() as { url: string; event_key?: string; marketplace?: string };
        if (!body.url) return Response.json({ error: "url required" }, { status: 400 });

        const { fetchPage } = await import("./monid-client");
        const page = await fetchPage(env.MONID_API_KEY, body.url);

        if (page.status !== "FETCHED") {
          return Response.json({ error: page.status, latency_ms: page.latency_ms });
        }

        const extracted = JSON.parse(JSON.stringify(page)); // simplified
        const now = new Date().toISOString();
        const htmlBytes = new TextEncoder().encode(page.html);
        const contentHash = await sha256Hex(htmlBytes);
        const h0 = contentHash.slice(0, 2), h1 = contentHash.slice(2, 4);
        const rawKey = `raw/monid/${h0}/${h1}/${contentHash}.json`;
        const mp = body.marketplace || "unknown";
        const ek = body.event_key || "test";

        const rawPayload = JSON.stringify({
          url: body.url, marketplace: mp, event_key: ek,
          acquisition_provider: "monid", provider: page.provider,
          html: page.html, // FULL — no truncation
          fetched_at: now, cost_usd: page.cost_usd, cost_basis: "MEASURED",
        });
        await env.RAW_BUCKET.put(rawKey, rawPayload, {
          httpMetadata: { contentType: "application/json" },
          customMetadata: { source: "monid", marketplace: mp, event_key: ek, content_hash: contentHash },
        });

        // Parse JSON-LD for observation
        const ldRegex = /<script[^>]*type="application\/ld\+json"[^>]*>([\s\S]*?)<\/script>/gi;
        let match;
        const obs: Record<string, any> = {};
        while ((match = ldRegex.exec(page.html)) !== null) {
          try {
            const ld = JSON.parse(match[1]);
            if (ld?.["@type"] === "Event" || ld?.["@type"] === "MusicEvent" || ld?.["@type"] === "Concert") {
              const offers = ld.offers;
              if (offers && !Array.isArray(offers)) {
                obs.observed_offer_min_price = parseFloat(offers.price) || null;
                obs.currency = offers.priceCurrency;
                obs.price_basis = "PUBLIC_PAGE_JSON_LD_OFFER";
                obs.inventory_basis = "UNKNOWN";
              }
              obs.name = ld.name;
              obs.venue = ld.location?.name;
              obs.city = ld.location?.address?.addressLocality;
              break;
            }
          } catch {}
        }

        const observation = {
          schema_version: "ticket_market_snapshot_v1",
          event_key: ek, source_platform: mp,
          acquisition_provider: "monid", actor_or_endpoint: `monid_${page.provider}`,
          observed_at: now, retrieved_at: now, knowledge_time: now,
          observed_offer_min_price: obs.observed_offer_min_price ?? null,
          currency: obs.currency || null,
          price_basis: obs.price_basis || "NONE",
          inventory_basis: "UNKNOWN",
          source_url: body.url, raw_payload_hash: contentHash,
          rights_status: "TERMS_REVIEW_REQUIRED",
          commercial_use_status: "PROTOTYPE_ONLY",
        };
        const stagingKey = `staging/ticket_market/date=${now.slice(0, 10)}/hour=${now.slice(11, 13)}/test-${Date.now()}.json`;
        await env.LAKE_BUCKET.put(stagingKey, JSON.stringify(observation, null, 2), {
          httpMetadata: { contentType: "application/json" },
        });

        return Response.json({
          status: "FETCHED_AND_STORED",
          raw_key: rawKey,
          staging_key: stagingKey,
          content_hash: contentHash,
          provider: page.provider,
          cost_usd: page.cost_usd,
          latency_ms: page.latency_ms,
          observation,
        });
      } catch (e: any) {
        return Response.json({ error: e.message || String(e) }, { status: 500 });
      }
    }

    return Response.json({ error: "Not found" }, { status: 404 });
  },

  async queue(batch: MessageBatch<unknown>, env: Env): Promise<void> {
    const queueName = batch.queue;

    switch (queueName) {
      case "fi-acquisition-fast":
        await handleFastBatch(batch as MessageBatch<any>, env);
        break;
      case "fi-acquisition-deep":
        await handleDeepBatch(batch as MessageBatch<any>, env);
        break;
      case "fi-acquisition-processing":
        await handleProcessingBatch(batch as MessageBatch<any>, env);
        break;
      default:
        console.error(`Unknown queue: ${queueName}`);
        for (const msg of batch.messages) {
          msg.ack();
        }
    }
  },

  /**
   * Production scheduler — Cloudflare Cron Trigger.
   *
   * Plan due event×marketplace pairs and enqueue to the FAST queue.
   * Does NOT call Monid, does NOT reserve budget, does NOT mutate evidence.
   * The FAST queue consumer is the sole execution point.
   */
  async scheduled(_controller: ScheduledController, env: Env): Promise<void> {
    try {
      // Query Governor observation-state for cadence-aware due determination
      let getLastObservedHoursAgo: (event_key: string, marketplace: string) => Promise<number | null> = async () => null;
      try {
        const governorId = env.GOVERNOR.idFromName("acquisition-governor");
        const governor = env.GOVERNOR.get(governorId) as any;
        getLastObservedHoursAgo = async (eventKey, marketplace) => {
          try {
            const obs = await governor.getObservationState({
              event_key: eventKey, marketplace, rail: "FAST",
            });
            if (obs && obs.last_successful_observation_at) {
              return (Date.now() - new Date(obs.last_successful_observation_at).getTime()) / (1000 * 60 * 60);
            }
            return null;
          } catch {
            return null;
          }
        };
      } catch {
        // Governor unavailable — treat all as due
      }

      const plan = await planTasks(env, {
        max_tasks: 25, // pilot cap
        getLastObservedHoursAgo,
      });

      console.log(JSON.stringify({
        event: "CRON_RUN_PLANNED",
        window: plan.window,
        candidate_pairs: plan.candidate_pairs,
        due_pairs: plan.due_pairs,
        queued: plan.queued,
      }));

      // Persist a run record — precise per-fire id so each */15 minute mark
      // is a distinct, verifiable cron cycle. (Tasks still dedupe on the
      // hour-level logical window via Governor.idempotent commit.)
      const runId = `sched_${new Date().toISOString().slice(0, 16).replace(/[-:T]/g, "")}`;
      // Audit info: selected task keys + lifecycle buckets prove why the
      // planner selected what it did (P2 requirement).
      const selected = plan.tasks.slice(0, 25).map((t) => ({
        task_key: t.task_key,
        event_key: t.event_key,
        days_to_show: t.days_to_show,
        lifecycle_bucket: t.lifecycle_bucket || "",
      }));
      await env.BACKUP_BUCKET.put(
        `control/runs/${runId}.json`,
        JSON.stringify({
          run_id: runId,
          type: "cron_triggered",
          started_at: new Date().toISOString(),
          logical_window: plan.window,
          candidate_pairs: plan.candidate_pairs,
          due_pairs: plan.due_pairs,
          tasks_queued: plan.queued,
          deferred_due: plan.deferred_due,
          selected_task_digest: plan.selected_task_digest,
          selected: selected.length > 0 ? selected : undefined,
          status: "COMPLETED",
          triggered_at: new Date().toISOString(),
        }, null, 2)
      );
    } catch (e: unknown) {
      console.error(JSON.stringify({
        event: "CRON_RUN_ERROR",
        error: e instanceof Error ? e.message : String(e),
      }));
    }
  },
};

const MONID_BASE = "https://api.monid.ai";
