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
import { BatchContainer } from "./batch-container-do";
import { requireBatchAuth } from "./batch-auth";
import { sanitizeJobSpec, BATCH_ERROR_CODES } from "./batch-spec";
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
import { runMappingFactory } from "./mapping-factory-v2";
import { planForwardFamilies, loadV2Universe } from "./forward-planner";
import { handleYouTubeBatch } from "./youtube-consumer";
import { handleStructuredBatch } from "./structured-consumer";
import { handleDlqBatch } from "./dlq-consumer";
import { readPlatformQueueMetrics, readQueueMetrics, writeQueueEnqueueMetric } from "./queue-metrics";

export { AcquisitionGovernor, AcquisitionContainer, BatchContainer, AcquisitionWorkflow };

interface Env {
  FAST_QUEUE: Queue;
  DEEP_QUEUE: Queue;
  YOUTUBE_QUEUE: Queue;
  STRUCTURED_API_QUEUE: Queue;
  BROWSER_QUEUE: Queue;
  MONID_QUEUE: Queue;
  PROCESSING_QUEUE: Queue;
  DLQ_QUEUE: Queue;
  RAW_BUCKET: R2Bucket;
  LAKE_BUCKET: R2Bucket;
  BACKUP_BUCKET: R2Bucket;
  PRIVATE_BUCKET: R2Bucket;
  GOVERNOR: DurableObjectNamespace;
  BATCH_CONTAINER: DurableObjectNamespace;
  ACQUISITION_WORKFLOW: Workflow;
  BROWSER: any;
  MONID_API_KEY: string;
  TICKETMASTER_API_KEY: string;
  YOUTUBE_API_KEY: string;
  YOUTUBE_DAILY_QUOTA: string;
  TICKETS_DEV_API_KEY: string;
  FI_R2_ACCESS_KEY_ID: string;
  FI_R2_SECRET_ACCESS_KEY: string;
  FI_R2_ENDPOINT: string;
  FI_R2_RAW_BUCKET: string;
  FI_R2_LAKE_BUCKET: string;
  FI_R2_PRIVATE_BUCKET: string;
  FI_R2_BACKUP_BUCKET: string;
  POLICY_VERSION: string;
  SOFTWARE_VERSION: string;
  DAILY_BUDGET_USD: string;
  MONTHLY_BUDGET_USD: string;
  ENABLE_DEEP_RAIL: string;
  ADMIN_TOKEN: string;
  FI_LISTENER_HMAC_SECRET: string;
  FI_LISTENER_HMAC_SECRET_VERSION: string;
}

/** Check if request has valid admin auth */
function isAdminAuth(request: Request, env: Env): boolean {
  const authHeader = request.headers.get("Authorization");
  const tokenHeader = request.headers.get("X-Admin-Token");
  const expected = env.ADMIN_TOKEN;
  if (!expected) return true; // If no token configured, allow (development)
  return authHeader === `Bearer ${expected}` || tokenHeader === expected;
}

/** P10: Bounded request-body limit (256 KiB) for batch triggers. */
const MAX_BATCH_BODY_BYTES = 256 * 1024;

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

    if (url.pathname === "/batch/trigger" && request.method === "POST") {
      // V1B P0-4: Batch control FAILS CLOSED — no admin token configured
      // means 503 BATCH_AUTH_NOT_CONFIGURED, never development-open.
      const authFail = requireBatchAuth(request, env.ADMIN_TOKEN || "");
      if (authFail) return authFail;

      try {
        // P10: Bounded request-body limit — reject oversized payloads.
        const contentLength = parseInt(request.headers.get("content-length") || "0", 10);
        if (contentLength > MAX_BATCH_BODY_BYTES) {
          return Response.json(
            { error: "Request body too large", code: "REQUEST_TOO_LARGE" },
            { status: 413 },
          );
        }

        const bodyText = await request.text();
        if (bodyText.length > MAX_BATCH_BODY_BYTES) {
          return Response.json(
            { error: "Request body too large", code: "REQUEST_TOO_LARGE" },
            { status: 413 },
          );
        }

        // P7 + V1B P0-3: Sanitize the spec — rejects unknown job types,
        // forbidden keys, and any env_vars/secrets in the body.
        let spec;
        try {
          spec = sanitizeJobSpec(JSON.parse(bodyText));
        } catch (e: any) {
          return Response.json(
            { error: "Invalid job spec", code: BATCH_ERROR_CODES.JOB_VALIDATION_FAILED },
            { status: 400 },
          );
        }

        const doId = env.BATCH_CONTAINER.idFromName(spec.job_id);
        const batchDo = env.BATCH_CONTAINER.get(doId) as any;

        // V1B P0-2: startJob returns immediately (RUNNING) — the trigger does
        // NOT wait for job completion. output() is never called here.
        const result = await batchDo.startJob(spec);

        // P8: Return only safe structured fields — no stdout/stderr, no secrets.
        const safeResult = {
          job_id: result.job_id,
          job_type: result.job_type,
          status: result.status,
          manifest_key: result.manifest_key,
          last_safe_error_code: result.last_safe_error_code,
          started_at: result.started_at,
          completed_at: result.completed_at,
        };
        // 202 Accepted: the job was accepted and is running; the client must
        // poll /batch/status for completion.
        return Response.json(safeResult, { status: 202 });
      } catch (e: any) {
        // P1: fixed safe error code — never raw exception text.
        return Response.json(
          { error: "Job trigger failed", code: BATCH_ERROR_CODES.JOB_EXEC_FAILED },
          { status: 500 },
        );
      }
    }

    if (url.pathname === "/batch/status" && request.method === "GET") {
      // V1B P0-4: Status is also fail-closed protected.
      const authFail = requireBatchAuth(request, env.ADMIN_TOKEN || "");
      if (authFail) return authFail;

      const jobId = url.searchParams.get("job_id");
      if (!jobId) {
        return Response.json({ error: "job_id required" }, { status: 400 });
      }
      const doId = env.BATCH_CONTAINER.idFromName(jobId);
      const batchDo = env.BATCH_CONTAINER.get(doId) as any;
      const status = await batchDo.getStatus(jobId);
      // P8: getStatus already returns safe structured fields only.
      return Response.json(status);
    }

    if (url.pathname === "/terminal/bootstrap/current" && request.method === "GET") {
      // Narrow, admin-protected bootstrap path for the compact serving
      // artifact ONLY (Phase 7B): CURRENT.json metadata or the current
      // terminal.duckdb, streamed from the LAKE R2 binding. No arbitrary R2
      // key access, no public bucket, no file browser.
      const artifact = url.searchParams.get("artifact") || "metadata";
      const currentKey = "serving/artist_security_terminal_v1/CURRENT.json";
      const currentObj = await env.LAKE_BUCKET.get(currentKey);
      if (!currentObj) {
        return Response.json(
          { error: "TERMINAL_CURRENT_NOT_FOUND", key: currentKey },
          { status: 404 },
        );
      }
      const current = (await currentObj.json()) as {
        generation?: string;
        object_key?: string;
        sha256?: string;
        [k: string]: unknown;
      };
      if (artifact === "metadata") {
        return Response.json(current);
      }
      if (artifact === "db") {
        const objectKey = current.object_key;
        if (!objectKey) {
          return Response.json({ error: "TERMINAL_OBJECT_KEY_MISSING" }, { status: 500 });
        }
        const db = await env.LAKE_BUCKET.get(objectKey);
        if (!db) {
          return Response.json(
            { error: "TERMINAL_ARTIFACT_NOT_FOUND", key: objectKey },
            { status: 404 },
          );
        }
        return new Response(db.body, {
          headers: {
            "Content-Type": "application/octet-stream",
            "Content-Length": String(db.size),
            "X-Serving-Generation": current.generation || "",
            "X-Serving-SHA256": current.sha256 || "",
            "Cache-Control": "no-store",
          },
        });
      }
      return Response.json({ error: "unknown artifact type" }, { status: 400 });
    }

    if (url.pathname === "/ops/r2cat" && request.method === "GET") {
      // Bounded, admin-protected R2 inventory listing (debug only) — returns
      // keys+sizes under a prefix from the LAKE/RAW/BACKUPS/PRIVATE buckets.
      // Never returns object bodies.
      const bucketName = url.searchParams.get("bucket") || "lake";
      const prefix = url.searchParams.get("prefix") || "";
      const limit = Math.min(parseInt(url.searchParams.get("limit") || "500", 10), 2000);
      const buckets: Record<string, R2Bucket | undefined> = {
        lake: env.LAKE_BUCKET, raw: env.RAW_BUCKET,
        backups: env.BACKUP_BUCKET, private: env.PRIVATE_BUCKET,
      };
      const bucket = buckets[bucketName];
      if (!bucket) {
        return Response.json({ error: `unknown bucket '${bucketName}'` }, { status: 400 });
      }
      const listing = await bucket.list({ prefix, limit });
      return Response.json({
        prefix, truncated: listing.truncated,
        objects: listing.objects.map((o) => ({ key: o.key, size: o.size, uploaded: o.uploaded })),
      });
    }

    if (url.pathname === "/ops/r2get" && request.method === "GET") {
      // Bounded, admin-protected read of SMALL text objects (debug only) —
      // manifests, CURRENT pointers, reports. Hard cap at 2 MiB.
      const key = url.searchParams.get("key") || "";
      const bucketName = url.searchParams.get("bucket") || "lake";
      const buckets: Record<string, R2Bucket | undefined> = {
        lake: env.LAKE_BUCKET, raw: env.RAW_BUCKET,
        backups: env.BACKUP_BUCKET, private: env.PRIVATE_BUCKET,
      };
      const bucket = buckets[bucketName];
      if (!bucket) return Response.json({ error: `unknown bucket '${bucketName}'` }, { status: 400 });
      const obj = await bucket.get(key);
      if (!obj) return Response.json({ error: "not found", key }, { status: 404 });
      if (obj.size > 2 * 1024 * 1024) {
        return Response.json({ error: "object too large for /ops/r2get", size: obj.size }, { status: 413 });
      }
      const text = await obj.text();
      return new Response(text, {
        headers: { "Content-Type": obj.httpMetadata?.contentType || "text/plain" },
      });
    }

    if (url.pathname === "/ops/health" && request.method === "GET") {
      const universe = await loadV2Universe(env);
      const governorId = env.GOVERNOR.idFromName("acquisition-governor");
      const governor = env.GOVERNOR.get(governorId) as any;
      const gov = await governor.getReservationSummary();
      const objects = await Promise.all([
        env.RAW_BUCKET.list({ prefix: "raw/youtube/", limit: 1000 }),
        env.LAKE_BUCKET.list({ prefix: "staging/youtube/", limit: 1000 }),
        env.BACKUP_BUCKET.list({ prefix: "control/scheduler/", limit: 1000 }),
      ]);
      const nowMs = Date.now();
      const audits: any[] = [];
      for (const obj of objects[2].objects.slice(-1000)) {
        const item = await env.BACKUP_BUCKET.get(obj.key);
        if (item) { try { audits.push(await item.json()); } catch {} }
      }
      const recent = audits.filter((x) => x.started_at && nowMs - new Date(x.started_at).getTime() <= 24 * 3600 * 1000);
      const recentHour = recent.filter((x) => nowMs - new Date(x.started_at).getTime() <= 3600 * 1000);
      const youtubeAudit = (xs: any[]) => xs.reduce((n, x) => n + (x.queues?.youtube || 0), 0);
      const youtubeTicks = objects[1].objects.filter((x) => x.key.includes("staging/youtube/")).length;
      const queueMetrics = await readQueueMetrics(env, new Date(), 15);
      const platformQueueMetrics = await readPlatformQueueMetrics({
        "fi-youtube": env.YOUTUBE_QUEUE,
        "fi-structured-api": env.STRUCTURED_API_QUEUE,
        "fi-browser": env.BROWSER_QUEUE,
        "fi-monid": env.MONID_QUEUE,
        "fi-processing": env.PROCESSING_QUEUE,
        "fi-dlq": env.DLQ_QUEUE,
      });
      const zeroTelemetry = { enqueued: 0, received: 0, acked: 0, retried: 0, explicit_dlq: 0, telemetry_batches: 0 };
      const queueNames = new Set([...Object.keys(platformQueueMetrics), ...Object.keys(queueMetrics.totals)]);
      const queueHealth = Object.fromEntries([...queueNames].map((queue) => {
        const metric = queueMetrics.totals[queue] || zeroTelemetry;
        const platform = platformQueueMetrics[queue];
        return [queue, {
          // Internal lifecycle telemetry is a bounded-window reconciliation;
          // platform_* fields are the authoritative point-in-time snapshot.
          backlog_estimate: Math.max(0, metric.enqueued - metric.acked - metric.explicit_dlq),
          enqueued: metric.enqueued,
          received: metric.received,
          acked: metric.acked,
          retried: metric.retried,
          explicit_dlq: metric.explicit_dlq,
          telemetry_batches: metric.telemetry_batches,
          platform_backlog_count: platform?.backlog_count ?? null,
          platform_backlog_bytes: platform?.backlog_bytes ?? null,
          platform_oldest_message_timestamp: platform?.oldest_message_timestamp ?? null,
          platform_metrics_available: platform?.available ?? false,
          platform_metrics_error: platform?.error,
        }];
      }));
      const platformBacklogValues = Object.entries(platformQueueMetrics).filter(([queue, metric]) => queue !== "fi-dlq" && metric.available && metric.backlog_count !== null).map(([, metric]) => metric.backlog_count as number);
      const platformBacklogComplete = Object.entries(platformQueueMetrics).filter(([queue]) => queue !== "fi-dlq").every(([, metric]) => metric.available && metric.backlog_count !== null);
      const platformDlq = platformQueueMetrics["fi-dlq"];
      return Response.json({
        deployed_version: env.SOFTWARE_VERSION,
        last_cron_at: audits.length ? audits[audits.length - 1].completed_at || audits[audits.length - 1].started_at : null,
        cron_runs_1h: recentHour.length,
        cron_runs_24h: recent.length,
        watch_universe_size: universe.events.length,
        youtube: { candidate: universe.youtube_channels?.length || 0, due: youtubeAudit(recent), selected: youtubeAudit(recent), checks_1h: youtubeTicks, checks_24h: youtubeTicks, changes_1h: youtubeTicks, changes_24h: youtubeTicks, heartbeats_1h: 0, quota_used_today: youtubeAudit(recent), quota_projected_today: youtubeAudit(recent), quota_blocked: 0 },
        tickets: { candidate: universe.events.length, due: null, selected: null, checks_24h: null, useful_24h: null, changes_24h: null, structured_queue_checks_24h: null },
        queues: {
          // Aggregate fields are authoritative when every operational queue
          // returned a realtime metric; use by_queue for per-queue bytes and
          // oldest-message timestamps.
          backlog: platformBacklogComplete ? platformBacklogValues.reduce((sum, count) => sum + count, 0) : null,
          dlq: platformDlq?.available ? platformDlq.backlog_count : null,
          window_utc: { start: queueMetrics.window_start, end: queueMetrics.window_end },
          telemetry_complete: queueMetrics.complete,
          telemetry_minutes_covered: queueMetrics.minutes_covered,
          platform_metrics_complete: Object.values(platformQueueMetrics).every((metric) => metric.available),
          source: "Cloudflare Queue.metrics() realtime depth plus R2 batch lifecycle telemetry",
          by_queue: queueHealth,
        },
        r2: { youtube_raw_objects: objects[0].objects.length, youtube_lake_objects: objects[1].objects.length, scheduler_audits: audits.length },
        governor: gov,
      });
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

    if (url.pathname === "/admin/controlled-structured-test" && request.method === "POST") {
      try {
        const universe = await loadV2Universe(env);
        const selected = universe.events.filter((e) => {
          const url = e.marketplace_event_url || e.canonical_url || "";
          const id = e.provider_event_id || url.match(/ticketmaster\.com\/[^/]+\/event\/([^/?]+)/i)?.[1];
          return url.includes("ticketmaster.com") && !!id;
        }).slice(0, 5);
        const now = new Date().toISOString();
        const tasks = selected.map((e, i) => ({
          task_key: `controlled_${now.replace(/[^0-9]/g, "")}_${i}`,
          event_key: e.event_key,
          provider_event_id: e.provider_event_id || (e.marketplace_event_url || e.canonical_url || "").match(/ticketmaster\.com\/[^/]+\/event\/([^/?]+)/i)?.[1],
          target_url: e.marketplace_event_url || e.canonical_url,
          marketplace: "ticketmaster.com",
          event_metadata: { artist_name: e.artist_name, event_date: e.event_date, venue_name: e.venue_name, city: e.city },
        }));
        for (const task of tasks) await env.STRUCTURED_API_QUEUE.send(task);
        const result = { run_id: `controlled_${Date.now()}`, status: "QUEUED", task_count: tasks.length, tasks, queue: "fi-structured-api", expected_cost_usd: 0 };
        await env.BACKUP_BUCKET.put(`control/runs/${result.run_id}.json`, JSON.stringify(result), { httpMetadata: { contentType: "application/json" } });
        return Response.json(result);
      } catch (e: any) { return Response.json({ error: e.message || String(e) }, { status: 500 }); }
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

    if (url.pathname === "/admin/mapping-factory-v2" && request.method === "POST") {
      // EVENT_MAPPING_FACTORY_V2 — three discovery sources, one identity contract.
      // Source 1: provider-ID promotion (zero scraper cost).
      // Source 2: venue/promoter calendars (bounded /links + sitemaps).
      // Source 3: Common Crawl URL index (bounded, candidate evidence only).
      try {
        const body = await request.json() as {
          max_events?: number;
          offset?: number;
          dry_run?: boolean;
          include_provider_id?: boolean;
          include_calendars?: boolean;
          include_common_crawl?: boolean;
        };
        const report = await runMappingFactory(env, {
          max_events: body.max_events ?? 100,
          offset: body.offset ?? 0,
          dry_run: !!body.dry_run,
          include_provider_id: body.include_provider_id !== false,
          include_calendars: body.include_calendars !== false,
          include_common_crawl: !!body.include_common_crawl,
        });
        return Response.json(report);
      } catch (e: any) {
        return Response.json({ error: e.message || String(e) }, { status: 500 });
      }
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
      case "fi-youtube":
        await handleYouTubeBatch(batch as MessageBatch<any>, env);
        break;
      case "fi-structured-api":
        await handleStructuredBatch(batch as MessageBatch<any>, env);
        break;
      case "fi-browser":
      case "fi-monid":
      case "fi-acquisition-fast":
        await handleFastBatch(batch as MessageBatch<any>, env);
        break;
      case "fi-acquisition-deep":
        await handleDeepBatch(batch as MessageBatch<any>, env);
        break;
      case "fi-processing":
      case "fi-acquisition-processing":
        await handleProcessingBatch(batch as MessageBatch<any>, env);
        break;
      case "fi-dlq":
        await handleDlqBatch(batch as MessageBatch<unknown>, env);
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
    const started = new Date();
    const runId = `cron_${started.toISOString().replace(/[-:.TZ]/g, "").slice(0, 14)}`;
    try {
      const plan = await planForwardFamilies(env, { youtube_quota_used_today: 0 });
      const youtubeTasks = plan.tasks.YOUTUBE_CHANNEL || [];
      // One queue message represents a batch; the consumer enforces the 50-ID API limit.
      let youtubeQueued = 0;
      if (youtubeTasks.length > 0) {
        for (let i = 0; i < youtubeTasks.length; i += 50) {
          await env.YOUTUBE_QUEUE.send({ type: "YOUTUBE_CHANNEL_BATCH", run_id: runId, tasks: youtubeTasks.slice(i, i + 50) });
          youtubeQueued++;
        }
      }
      let structuredQueued = 0;
      for (const task of plan.tasks.TICKET_STRUCTURED || []) { await env.STRUCTURED_API_QUEUE.send(task); structuredQueued++; }
      let webQueued = 0;
      for (const task of plan.tasks.TICKET_WEB || []) { await env.MONID_QUEUE.send(task); webQueued++; }
      const audit = {
        run_id: runId,
        type: "cron_triggered",
        started_at: started.toISOString(),
        completed_at: new Date().toISOString(),
        scheduler_version: "cloud-forward-data-plane-v2",
        watch_universe_size: plan.watch_universe_size,
        families: plan.families,
        queues: { youtube: youtubeQueued, structured_api: structuredQueued, monid: webQueued },
        status: "COMPLETED",
      };
      await env.BACKUP_BUCKET.put(`control/scheduler/${runId}.json`, JSON.stringify(audit), { httpMetadata: { contentType: "application/json" } });
      await env.BACKUP_BUCKET.put(`control/runs/${runId}.json`, JSON.stringify(audit), { httpMetadata: { contentType: "application/json" } });
      await Promise.all([
        writeQueueEnqueueMetric(env, "fi-youtube", youtubeQueued, runId),
        writeQueueEnqueueMetric(env, "fi-structured-api", structuredQueued, runId),
        writeQueueEnqueueMetric(env, "fi-monid", webQueued, runId),
      ]).catch((metricError) => console.error(JSON.stringify({ event: "QUEUE_METRIC_WRITE_ERROR", run_id: runId, error: metricError instanceof Error ? metricError.message : String(metricError) })));
      console.log(JSON.stringify({ event: "CRON_RUN_COMPLETED", ...audit }));
    } catch (e: unknown) {
      const error = e instanceof Error ? e.message : String(e);
      await env.BACKUP_BUCKET.put(`control/scheduler/${runId}.json`, JSON.stringify({ run_id: runId, started_at: started.toISOString(), status: "FAILED", error }), { httpMetadata: { contentType: "application/json" } });
      console.error(JSON.stringify({ event: "CRON_RUN_ERROR", run_id: runId, error }));
    }
  },
};

const MONID_BASE = "https://api.monid.ai";
