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
import { planTasks, loadUniverse, planBootstrapWave } from "./planner";
import {
  EventIdentity,
  MappingRecord,
  DiscoveryTarget,
  discoverCandidates,
  selectBestMapping,
  ACCEPTED_MAPPING_STATUSES,
} from "./mapping";

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
  BROWSER: any;
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

    if (url.pathname === "/admin/bootstrap-wave" && request.method === "POST") {
      // Authenticated bootstrap — queue never-observed accepted pairs immediately.
      // Reuses the SAME planner/queue/Governor execution path as scheduled
      // collection. Separates INITIAL COLLECTION from lifecycle refresh.
      try {
        const body = await request.json() as {
          max_tasks?: number;
          max_cost_usd?: number;
          marketplace?: string;
          lifecycle_bucket?: string;
          never_observed_only?: boolean;
          dry_run?: boolean;
        };

        const governorId = env.GOVERNOR.idFromName("acquisition-governor");
        const governor = env.GOVERNOR.get(governorId) as any;

        // lastObserved: true if the pair already has a successful observation.
        const lastObserved = async (eventKey: string, marketplace: string) => {
          try {
            const obs = await governor.getObservationState({
              event_key: eventKey, marketplace, rail: "FAST",
            });
            return !!(obs && obs.last_successful_observation_at);
          } catch {
            return false;
          }
        };

        // Governor budget projection for the max_cost_usd gate.
        const governorBudget = async () => {
          const s = await governor.getReservationSummary();
          return { daily_spend: s.daily_spend, reserved: s.reserved, daily_budget: s.daily_budget };
        };

        const result = await planBootstrapWave(
          { ...env, governorBudget },
          {
            max_tasks: body.max_tasks ?? 100,
            max_cost_usd: body.max_cost_usd ?? 0.10,
            marketplace: body.marketplace,
            lifecycle_bucket: body.lifecycle_bucket,
            never_observed_only: body.never_observed_only !== false,
            dry_run: !!body.dry_run,
            lastObserved,
          }
        );

        return Response.json({
          status: result.dry_run ? "PLANNED_DRY" : "BOOTSTRAP_QUEUED",
          candidate_pairs: result.candidate_pairs,
          eligible: result.due_pairs,
          tasks_queued: result.queued,
          budget_blocked: result.budget_blocked,
          window: result.window,
          selected: result.selected,
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

    if (url.pathname === "/scorecard" && request.method === "GET") {
      // Acquisition operations scorecard — aggregates per-task telemetry.
      // Never hides failures behind aggregate success rates.
      const day = url.searchParams.get("date") || new Date().toISOString().slice(0, 10);
      const prefix = `control/scorecard/date=${day}/`;
      const listing = await env.BACKUP_BUCKET.list({ prefix });
      const rows: any[] = [];
      for (const obj of listing.objects) {
        const raw = await env.BACKUP_BUCKET.get(obj.key);
        if (raw) {
          try { rows.push(await raw.json()); } catch { /* skip */ }
        }
      }

      const byMpRail = new Map<string, {
        attempts: number; ok: number; useful: number; price: number; avail: number;
        cost: number; latency: number[]; http403: number; http429: number; http5xx: number; timeout: number;
        byRail: Record<string, number>; byProvider: Record<string, number>;
        browserMs: number; estimatedBrowserCost: number; byBasis: Record<string, number>;
      }>();
      const totals = {
        attempts: 0, ok: 0, useful: 0, price: 0, avail: 0, cost: 0,
        http403: 0, http429: 0, http5xx: 0, timeout: 0, latencies: [] as number[],
        browserMs: 0, estimatedBrowserCost: 0,
      };

      for (const r of rows) {
        const key = `${r.marketplace}|${r.rail}`;
        let acc = byMpRail.get(key);
        if (!acc) {
          acc = { attempts: 0, ok: 0, useful: 0, price: 0, avail: 0, cost: 0, latency: [], http403: 0, http429: 0, http5xx: 0, timeout: 0, byRail: {}, byProvider: {}, browserMs: 0, estimatedBrowserCost: 0, byBasis: {} };
          byMpRail.set(key, acc);
        }
        acc.attempts++; totals.attempts++;
        if (r.ok) { acc.ok++; totals.ok++; }
        if (r.useful) { acc.useful++; totals.useful++; }
        if (r.price_extracted) { acc.price++; totals.price++; }
        if (r.availability_extracted) { acc.avail++; totals.avail++; }
        acc.cost += r.cost_usd || 0; totals.cost += r.cost_usd || 0;
        acc.browserMs += r.browser_ms || 0; totals.browserMs += r.browser_ms || 0;
        acc.estimatedBrowserCost += r.estimated_browser_cost_usd || 0; totals.estimatedBrowserCost += r.estimated_browser_cost_usd || 0;
        if (r.cost_basis) acc.byBasis[r.cost_basis] = (acc.byBasis[r.cost_basis] || 0) + 1;
        acc.latency.push(r.latency_ms || 0); totals.latencies.push(r.latency_ms || 0);
        if (r.http_status === 403) { acc.http403++; totals.http403++; }
        if (r.http_status === 429) { acc.http429++; totals.http429++; }
        if (r.http_status >= 500) { acc.http5xx++; totals.http5xx++; }
        if ((r.error_category || "").includes("TIMEOUT")) { acc.timeout++; totals.timeout++; }
        acc.byRail[r.rail] = (acc.byRail[r.rail] || 0) + 1;
        acc.byProvider[r.provider] = (acc.byProvider[r.provider] || 0) + 1;
      }

      const pct = (n: number, d: number) => (d ? Math.round((n / d) * 1000) / 10 : 0);
      const pLat = (arr: number[], q: number) => {
        if (!arr.length) return 0;
        const s = [...arr].sort((a, b) => a - b);
        return s[Math.min(s.length - 1, Math.floor(s.length * q))];
      };

      const perMarketplace: any[] = [];
      for (const [key, acc] of byMpRail) {
        const [marketplace, rail] = key.split("|");
        perMarketplace.push({
          marketplace, rail,
          attempts: acc.attempts,
          fetch_success_rate: pct(acc.ok, acc.attempts),
          useful_observation_rate: pct(acc.useful, acc.attempts),
          price_extracted: acc.price,
          availability_extracted: acc.avail,
          provider_cash_spend_usd: Math.round(acc.cost * 1e6) / 1e6,
          cost_basis: Object.keys(acc.byBasis),
          browser_ms: acc.browserMs,
          estimated_browser_marginal_usd: Math.round(acc.estimatedBrowserCost * 1e6) / 1e6,
          cost_per_fetch: Math.round((acc.cost / (acc.attempts || 1)) * 1e6) / 1e6,
          cost_per_useful: Math.round((acc.cost / (acc.useful || 1)) * 1e6) / 1e6,
          latency_p50_ms: pLat(acc.latency, 0.5),
          latency_p95_ms: pLat(acc.latency, 0.95),
          http403: acc.http403, http429: acc.http429, http5xx: acc.http5xx, timeouts: acc.timeout,
          by_rail: acc.byRail, by_provider: acc.byProvider,
        });
      }

      return Response.json({
        date: day,
        totals: {
          attempts: totals.attempts,
          ok: totals.ok,
          fetch_success_rate: pct(totals.ok, totals.attempts),
          useful: totals.useful,
          useful_observation_rate: pct(totals.useful, totals.attempts),
          price_extracted: totals.price,
          availability_extracted: totals.avail,
          provider_cash_spend_usd: Math.round(totals.cost * 1e6) / 1e6,
          measured_browser_ms: totals.browserMs,
          estimated_browser_marginal_usd: Math.round(totals.estimatedBrowserCost * 1e6) / 1e6,
          cost_per_fetch: Math.round((totals.cost / (totals.attempts || 1)) * 1e6) / 1e6,
          cost_per_useful: Math.round((totals.cost / (totals.useful || 1)) * 1e6) / 1e6,
          latency_p50_ms: pLat(totals.latencies, 0.5),
          latency_p95_ms: pLat(totals.latencies, 0.95),
          http403: totals.http403, http429: totals.http429, http5xx: totals.http5xx, timeouts: totals.timeout,
        },
        per_marketplace_rail: perMarketplace,
      });
    }

    if (url.pathname === "/admin/mapping-wave" && request.method === "POST") {
      // Mapping factory — resolve exact marketplace URLs for canonical events.
      // Discovery via sitemaps + Browser /links. Deterministic match requires
      // artist + date + venue + city. Artist-only matches are rejected.
      try {
        const body = await request.json() as {
          max_events?: number;
          marketplaces?: string[];
          dry_run?: boolean;
        };
        const maxEvents = body.max_events || 5;

        const { events } = await loadUniverse(env);
        const governorId = env.GOVERNOR.idFromName("acquisition-governor");
        const governor = env.GOVERNOR.get(governorId) as any;

        // Discovery targets — bounded, explicit. Sitemaps first (no browser).
        const discoveryTargets: DiscoveryTarget[] = [
          { name: "seatgeek", marketplace: "seatgeek.com", start_url: "https://seatgeek.com/concerts", sitemap_url: "https://seatgeek.com/sitemap.xml" },
          { name: "vivid", marketplace: "vividseats.com", start_url: "https://www.vividseats.com/concerts", sitemap_url: "https://www.vividseats.com/sitemap.xml" },
          { name: "tickpick", marketplace: "tickpick.com", start_url: "https://www.tickpick.com/concerts", sitemap_url: "https://www.tickpick.com/sitemap.xml" },
          { name: "gametime", marketplace: "gametime.com", start_url: "https://gametime.com/concerts", sitemap_url: "https://gametime.com/sitemap.xml" },
        ];

        const accepted: MappingRecord[] = [];
        const results: any[] = [];
        let processed = 0;

        for (const event of events) {
          if (processed >= maxEvents) break;
          const identity: EventIdentity | null = event.artist_name && event.event_date && event.venue_name && event.city
            ? { event_key: event.event_key, artist_name: event.artist_name, event_date: String(event.event_date).slice(0, 10), venue_name: event.venue_name, city: event.city }
            : null;
          if (!identity) continue;

          processed++;
          for (const target of discoveryTargets) {
            const candidates = await discoverCandidates(env.BROWSER || null, target, { maxUrls: 200 });
            const { record, status, best } = selectBestMapping(identity, candidates);
            results.push({ event_key: identity.event_key, marketplace: target.marketplace, status, best_url: best?.url || null });
            if (record) {
              accepted.push(record);
              if (!body.dry_run) {
                // Persist mapping record (event_identifiers contract)
                await env.BACKUP_BUCKET.put(`canonical/event_identifiers/${identity.event_key}.json`, JSON.stringify(record, null, 2), {
                  httpMetadata: { contentType: "application/json" },
                });
              }
            }
          }
        }

        return Response.json({
          status: body.dry_run ? "MAPPING_PLANNED" : "MAPPING_WAVE_COMPLETE",
          events_processed: processed,
          accepted_mappings: accepted.length,
          accepted,
          results,
        });
      } catch (e: any) {
        return Response.json({ error: e.message || String(e) }, { status: 500 });
      }
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
