/**
 * Acquisition Planner — selects due event×marketplace pairs and builds FAST tasks.
 *
 * Used by BOTH:
 *   - the Worker `scheduled()` Cron Trigger handler (production scheduler)
 *   - the `/dispatch` admin endpoint (manual / pilot)
 *
 * The planner ONLY plans and dispatches. It never reserves budget,
 * never calls Monid, never mutates evidence, never does long work.
 * The FAST queue consumer is the sole execution point.
 */

import { generateTaskKey, AcquisitionTask, AcquisitionProvider, Marketplace, AcquisitionRail } from "./task-contract";

/** Minimum env needed by the planner */
export interface PlannerEnv {
  BACKUP_BUCKET: R2Bucket;
  FAST_QUEUE: Queue;
  SOFTWARE_VERSION: string;
}

/** A single candidate acquisition unit */
export interface AcquisitionUnit {
  event_key: string;
  artist_name?: string;
  venue_name?: string;
  city?: string;
  event_date?: string;
  marketplace: string;
  marketplace_event_url: string;
  mapping_status: string;
}

export interface PlanResult {
  status: "PLANNED";
  candidate_pairs: number;
  due_pairs: number;
  queued: number;
  window: string;
  tasks: Array<{ task_key: string; event_key: string; url: string; days_to_show: number }>;
  budget_blocked: number;
}

/** Lifecycle cadence: min_hours_between determines due/not-due */
const CADENCE_HOURS: Array<{ min_days: number; max_days: number; hours_between: number }> = [
  { min_days: 120, max_days: 9999, hours_between: 24 * 7 },   // weekly
  { min_days: 60, max_days: 120, hours_between: 12 * 7 },     // 2x/week
  { min_days: 14, max_days: 60, hours_between: 24 },          // daily
  { min_days: 7, max_days: 14, hours_between: 12 },           // 2x/day
  { min_days: 3, max_days: 7, hours_between: 8 },             // 3x/day
  { min_days: 1, max_days: 3, hours_between: 6 },             // 4x/day
  { min_days: 0, max_days: 1, hours_between: 5 },             // 4-6x/day show day
];

function hoursBetweenFor(daysToShow: number): number {
  for (const r of CADENCE_HOURS) {
    if (daysToShow >= r.min_days && daysToShow < r.max_days) return r.hours_between;
  }
  return 24 * 7;
}

const EXACT_STATUSES = ["EXACT_PROVIDER_ID", "EXACT_PAGE_MATCH", "HIGH_CONFIDENCE"];

/**
 * Normalize a raw universe event into an AcquisitionUnit.
 * Handles both legacy universe shape (canonical_url) and
 * modern shape (marketplace_event_url).
 */
function toAcquisitionUnit(e: any): AcquisitionUnit | null {
  const url = e.marketplace_event_url || e.canonical_url;
  if (!url) return null;
  return {
    event_key: e.event_key || e.id,
    artist_name: e.artist_name,
    venue_name: e.venue_name,
    city: e.city,
    event_date: e.event_date || e.date,
    marketplace: e.marketplace || "ticketmaster.com",
    marketplace_event_url: url,
    mapping_status: e.mapping_status || "EXACT_PAGE_MATCH",
  };
}

/**
 * Load and normalize the current acquisition universe from R2.
 * Tries the stable control pointer first, falls back to legacy frozen universe.
 */
export async function loadUniverse(env: PlannerEnv): Promise<{ events: AcquisitionUnit[] }> {
  const rawEvents: any[] = [];

  // Try stable control pointer first
  const pointerObj = await env.BACKUP_BUCKET.get("control/watch_universe/current.json");
  if (pointerObj) {
    try {
      const pointer = await pointerObj.json() as { source?: string };
      if (pointer.source) {
        const actual = await env.BACKUP_BUCKET.get(pointer.source);
        if (actual) {
          const data = await actual.json() as { events?: any[] };
          rawEvents.push(...(data.events || []));
        }
      } else {
        // pointer itself contains events inline
        const data = await pointerObj.json() as { events?: any[] };
        rawEvents.push(...(data.events || []));
      }
    } catch {
      // fall through to legacy
    }
  }

  // Fallback: legacy frozen universe
  if (rawEvents.length === 0) {
    const legacy = await env.BACKUP_BUCKET.get(
      "canonical/2026-08-26T01-00-58Z/watch_universe_v1.json"
    );
    if (legacy) {
      try {
        const data = await legacy.json() as { events?: any[] };
        rawEvents.push(...(data.events || []));
      } catch {
        // no events
      }
    }
  }

  const events: AcquisitionUnit[] = rawEvents
    .map(toAcquisitionUnit)
    .filter((e): e is AcquisitionUnit => e !== null);

  return { events };
}

/**
 * Determine which event×marketplace pairs are due right now, based on
 * lifecycle cadence. If Governor observation-state is provided via callback,
 * it is used for last-observed time. Otherwise defaults to "never observed".
 */
export async function planTasks(
  env: PlannerEnv,
  opts: {
    max_tasks?: number;
    getLastObservedHoursAgo?: (event_key: string, marketplace: string) => Promise<number | null>;
  } = {}
): Promise<PlanResult> {
  const { max_tasks = 25 } = opts;
  const { events } = await loadUniverse(env);

  const now = new Date();
  const window = now.toISOString().slice(0, 13);
  const tasks: Array<{ task_key: string; event_key: string; url: string; days_to_show: number; event_date?: string }> = [];
  let candidatePairs = 0;
  let duePairs = 0;
  let budgetBlocked = 0;

  for (const event of events) {
    if (tasks.length >= max_tasks) break;
    const eventKey = event.event_key;
    const eventDate = new Date(event.event_date || "2099-01-01");
    const daysToShow = (eventDate.getTime() - now.getTime()) / (1000 * 60 * 60 * 24);
    if (daysToShow < 0) continue; // post-show

    // Only exact/high-confidence mappings
    if (!EXACT_STATUSES.includes(event.mapping_status)) continue;
    const url = event.marketplace_event_url;
    if (!url) continue;
    candidatePairs++;

    // Cadence / due check using last-observed (from Governor observation-state)
    let lastObservedHoursAgo = 999; // never observed → due
    if (opts.getLastObservedHoursAgo) {
      try {
        const v = await opts.getLastObservedHoursAgo(eventKey, event.marketplace);
        if (v != null) lastObservedHoursAgo = v;
      } catch {
        // fall through to never observed
      }
    }
    const required = hoursBetweenFor(daysToShow);
    if (lastObservedHoursAgo < required) continue; // not yet due
    duePairs++;

    const taskKey = generateTaskKey(eventKey, event.marketplace, "FAST", window, "v1");

    tasks.push({
      task_key: taskKey,
      event_key: eventKey,
      url,
      days_to_show: Math.round(daysToShow),
      event_date: eventDate.toISOString().slice(0, 10),
    });
  }

  // Enqueue tasks to FAST queue (planner does NOT reserve budget)
  let queued = 0;
  for (const t of tasks) {
    const task: AcquisitionTask = {
      task_key: t.task_key,
      event_key: t.event_key,
      acquisition_provider: "monid" as AcquisitionProvider,
      marketplace: t.url.startsWith("seatgeek") ? "seatgeek.com"
        : t.url.startsWith("stubhub") ? "stubhub.com"
        : t.url.startsWith("vivid") ? "vividseats.com"
        : t.url.startsWith("tickpick") ? "tickpick.com"
        : t.url.startsWith("gametime") ? "gametime.com"
        : t.url.includes("axs.com") ? "axs.com"
        : t.url.includes("ticketweb") ? "ticketweb.com"
        : "ticketmaster.com" as Marketplace,
      rail: "FAST" as AcquisitionRail,
      target_url: t.url,
      scheduled_window: window,
      priority: t.days_to_show <= 7 ? 1 : t.days_to_show <= 30 ? 2 : 3,
      expected_max_cost_usd: 0.0009,
      created_at: now.toISOString(),
      software_version: env.SOFTWARE_VERSION,
      mapping_version: "v1",
      event_metadata: {
        event_date: t.event_date || undefined,
        time_to_show_days: t.days_to_show,
      },
      trigger: "SCHEDULED",
      run_id: `sched_${now.toISOString().slice(0, 13)}`,
    };
    await env.FAST_QUEUE.send(task);
    queued++;
  }

  return {
    status: "PLANNED",
    candidate_pairs: candidatePairs,
    due_pairs: duePairs,
    queued,
    window,
    tasks,
    budget_blocked: budgetBlocked,
  };
}