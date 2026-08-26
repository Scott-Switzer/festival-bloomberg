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
      // Proves the autonomous pipeline without Workflow.
      try {
        const body = await request.json() as { max_events?: number; max_cost?: number };
        const maxEvents = body.max_events || 25;

        // Load universe
        const universeObj = await env.BACKUP_BUCKET.get("canonical/2026-08-26T01-00-58Z/watch_universe_v1.json");
        if (!universeObj) return Response.json({ error: "No universe" }, { status: 500 });
        const universe: any = await universeObj.json();
        const events = universe.events || [];
        const now = new Date();
        const window = now.toISOString().slice(0, 13);

        // Import generateTaskKey
        const { generateTaskKey } = await import("./task-contract");

        let dispatched = 0;
        const dispatchedTasks: any[] = [];

        for (const ev of events) {
          if (dispatched >= maxEvents) break;
          const targetUrl = ev.canonical_url || "";
          if (!targetUrl) continue;
          const eventKey = ev.event_key || ev.id;
          const eventDate = new Date(ev.event_date || "2099-01-01");
          const daysToShow = (eventDate.getTime() - now.getTime()) / (1000 * 60 * 60 * 24);
          if (daysToShow < 0) continue;

          const taskKey = generateTaskKey(eventKey, "ticketmaster.com", "FAST", window, "v1");

          const task = {
            task_key: taskKey,
            event_key: eventKey,
            acquisition_provider: "monid",
            marketplace: "ticketmaster.com",
            rail: "FAST",
            target_url: targetUrl,
            scheduled_window: window,
            priority: daysToShow <= 7 ? 1 : daysToShow <= 30 ? 2 : 3,
            expected_max_cost_usd: 0.0009,
            created_at: now.toISOString(),
            software_version: env.SOFTWARE_VERSION,
            mapping_version: "v1",
            event_metadata: {
              artist_name: ev.artist_name,
              venue_name: ev.venue_name,
              city: ev.city,
              event_date: ev.event_date,
              time_to_show_days: Math.round(daysToShow),
            },
            trigger: "SCHEDULED",
            run_id: `dispatch_${Date.now()}`,
          };

          await env.FAST_QUEUE.send(task);
          dispatched++;
          dispatchedTasks.push({ task_key: taskKey, event_key: eventKey, url: targetUrl });
        }

        return Response.json({
          status: "DISPATCHED",
          events_universe: events.length,
          tasks_dispatched: dispatched,
          window,
          tasks: dispatchedTasks,
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
};

const MONID_BASE = "https://api.monid.ai";
