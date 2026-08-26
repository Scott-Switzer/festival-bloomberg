/**
 * Acquisition Workflow — durable scheduled acquisition runner.
 * Uses Cloudflare WorkflowEntrypoint with durable steps.
 *
 * KEY INVARIANT: Workflow does NOT reserve budget.
 * It plans tasks and enqueues them.
 * The Queue consumer is the ONLY entity that calls reserveTask().
 */

import { WorkflowEntrypoint, WorkflowStep, WorkflowEvent } from "cloudflare:workers";
import {
  AcquisitionTask,
  AcquisitionRun,
  generateTaskKey,
  AcquisitionRail,
  AcquisitionProvider,
  Marketplace,
} from "./task-contract";
import {
  shouldObserveNow,
  DEFAULT_CADENCE_POLICY,
} from "./scheduler";

interface WorkflowEnv {
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
  POLICY_VERSION: string;
  SOFTWARE_VERSION: string;
  DAILY_BUDGET_USD: string;
  MONTHLY_BUDGET_USD: string;
  ENABLE_DEEP_RAIL: string;
}

/** Universe event — what we load from R2 */
interface UniverseEvent {
  event_key: string;
  artist_name?: string;
  venue_name?: string;
  city?: string;
  event_date?: string;
  marketplace: string;
  marketplace_event_url: string;
  mapping_status: string;
  mapping_method?: string;
}

/** Stable universe pointer structure */
interface UniversePointer {
  source: string;
  updated_at: string;
  event_count: number;
}

export class AcquisitionWorkflow extends WorkflowEntrypoint<WorkflowEnv, Record<string, never>> {
  async run(event: WorkflowEvent<Record<string, never>>, step: WorkflowStep): Promise<AcquisitionRun> {
    const runId = `wf_${Date.now()}`;
    const startTime = new Date().toISOString();

    // Step 1: Load universe from stable control pointer
    const universe = await step.do("load-universe", async () => {
      // Try stable control pointer first
      const pointerObj = await this.env.BACKUP_BUCKET.get("control/watch_universe/current.json");
      if (pointerObj) {
        const pointer = await pointerObj.json() as UniversePointer;
        if (pointer.source) {
          const actual = await this.env.BACKUP_BUCKET.get(pointer.source);
          if (actual) return (await actual.json()) as { events: UniverseEvent[] };
        }
      }

      // Fallback: legacy frozen universe (has canonical_url but no marketplace mappings)
      const legacy = await this.env.BACKUP_BUCKET.get(
        "canonical/2026-08-26T01-00-58Z/watch_universe_v1.json"
      );
      if (legacy) {
        const data = await legacy.json() as { events: any[] };
        // Convert legacy events to marketplace-aware format
        // Legacy events have canonical_url (TM URLs) but no marketplace_event_url
        const events: UniverseEvent[] = (data.events || [])
          .filter((e: any) => e.canonical_url)
          .map((e: any) => ({
            event_key: e.event_key || e.id,
            artist_name: e.artist_name,
            venue_name: e.venue_name,
            city: e.city,
            event_date: e.event_date || e.date,
            marketplace: "ticketmaster.com",
            marketplace_event_url: e.canonical_url,
            mapping_status: "EXACT_PAGE_MATCH",
          }));
        return { events };
      }

      throw new Error("No acquisition universe found");
    });

    const events = universe.events || [];

    // Step 2: Plan tasks — NO reservation here, just determine what's due
    const { tasksToQueue, suppressed, budgetBlocked } = await step.do("plan-tasks", async () => {
      const governorId = this.env.GOVERNOR.idFromName("acquisition-governor");
      const governor = this.env.GOVERNOR.get(governorId) as any;

      const tasks: AcquisitionTask[] = [];
      let suppressedCount = 0;
      let budgetBlockedCount = 0;
      const now = new Date();
      const window = now.toISOString().slice(0, 13);

      for (const event of events) {
        const eventKey = event.event_key;
        const eventDate = new Date(event.event_date || "2099-01-01");
        const daysToShow = (eventDate.getTime() - now.getTime()) / (1000 * 60 * 60 * 24);

        // Post-show: skip
        if (daysToShow < 0) continue;

        // Only EXACT/HIGH_CONFIDENCE mappings
        if (!["EXACT_PROVIDER_ID", "EXACT_PAGE_MATCH", "HIGH_CONFIDENCE"].includes(event.mapping_status)) {
          continue;
        }

        // Check current observation state from Governor (not stale universe)
        const obsState = await governor.getObservationState({
          event_key: eventKey,
          marketplace: event.marketplace,
          rail: "FAST",
        });

        const lastObservedHoursAgo = obsState
          ? (now.getTime() - new Date(obsState.last_successful_observation_at).getTime()) / (1000 * 60 * 60)
          : 999;

        // Never observed → always due
        // Previously observed → check cadence
        if (lastObservedHoursAgo < 999 && !shouldObserveNow(daysToShow, lastObservedHoursAgo, DEFAULT_CADENCE_POLICY)) {
          continue;
        }

        const targetUrl = event.marketplace_event_url;
        if (!targetUrl) continue;

        const taskKey = generateTaskKey(
          eventKey,
          event.marketplace,
          "FAST" as AcquisitionRail,
          window,
          "v1"
        );

        // Quick idempotency check (Governor will also check on reserveTask)
        const isDuplicate = await governor.getReservationSummary()
          .then((s: any) => false) // We just need to check recent_task_keys
          .catch(() => false);

        const task: AcquisitionTask = {
          task_key: taskKey,
          event_key: eventKey,
          acquisition_provider: "monid" as AcquisitionProvider,
          marketplace: event.marketplace as Marketplace,
          rail: "FAST" as AcquisitionRail,
          target_url: targetUrl,
          scheduled_window: window,
          priority: daysToShow <= 7 ? 1 : daysToShow <= 30 ? 2 : 3,
          expected_max_cost_usd: 0.0009,
          created_at: now.toISOString(),
          software_version: this.env.SOFTWARE_VERSION,
          mapping_version: "v1",
          event_metadata: {
            artist_name: event.artist_name,
            venue_name: event.venue_name,
            city: event.city,
            event_date: event.event_date,
            time_to_show_days: Math.round(daysToShow),
          },
          trigger: "SCHEDULED",
          run_id: runId,
        };

        tasks.push(task);
      }

      // Cap at 25 for pilot
      const MAX_PILOT_EVENTS = 25;
      if (tasks.length > MAX_PILOT_EVENTS) {
        tasks.length = MAX_PILOT_EVENTS;
      }

      return { tasksToQueue: tasks, suppressed: suppressedCount, budgetBlocked: budgetBlockedCount };
    });

    // Step 3: Queue tasks — no reservation, consumer will reserve
    const queued = await step.do("queue-tasks", async () => {
      let count = 0;
      for (const task of tasksToQueue) {
        if (task.rail === "FAST") {
          await this.env.FAST_QUEUE.send(task);
        } else if (task.rail === "DEEP") {
          await this.env.DEEP_QUEUE.send(task);
        }
        count++;
      }
      return count;
    });

    // Step 4: Build and persist run record
    const run = await step.do("persist-run", async () => {
      const runRecord: AcquisitionRun = {
        run_id: runId,
        started_at: startTime,
        completed_at: new Date().toISOString(),
        status: "COMPLETED",
        events_planned: events.length,
        tasks_planned: tasksToQueue.length + suppressed + budgetBlocked,
        tasks_queued: queued,
        tasks_suppressed: suppressed,
        tasks_budget_blocked: budgetBlocked,
        tasks_completed: 0,
        tasks_failed: 0,
        tasks_dlq: 0,
        tasks_retried: 0,
        raw_objects_written: 0,
        raw_bytes_written: 0,
        observations_written: 0,
        snapshots_appended: 0,
        total_cost_usd: 0,
        errors: [],
      };
      const key = `control/runs/${runId}.json`;
      await this.env.BACKUP_BUCKET.put(key, JSON.stringify(runRecord, null, 2));

      // Update last scheduled window in Governor
      const governorId = this.env.GOVERNOR.idFromName("acquisition-governor");
      const governor = this.env.GOVERNOR.get(governorId) as any;
      await governor.setLastScheduledWindow({ window: runId });

      return runRecord;
    });

    return run;
  }
}
