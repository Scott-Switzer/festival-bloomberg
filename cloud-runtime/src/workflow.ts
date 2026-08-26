/**
 * Acquisition Workflow — durable scheduled acquisition runner.
 * Uses Cloudflare WorkflowEntrypoint with durable steps.
 *
 * KEY INVARIANT: Workflow does NOT reserve budget.
 * It plans tasks and enqueues them.
 * The Queue consumer is the ONLY entity that calls reserveTask().
 *
 * Pilot mode: all EXACT-mapped future events are due.
 * Observation state is checked by the Governor on reserveTask(),
 * NOT by the Workflow. This avoids 100+ RPC calls in the workflow.
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

interface UniverseEvent {
  event_key: string;
  artist_name?: string;
  venue_name?: string;
  city?: string;
  event_date?: string;
  marketplace: string;
  marketplace_event_url: string;
  mapping_status: string;
}

export class AcquisitionWorkflow extends WorkflowEntrypoint<WorkflowEnv, Record<string, never>> {
  async run(event: WorkflowEvent<Record<string, never>>, step: WorkflowStep): Promise<AcquisitionRun> {
    const runId = `wf_${Date.now()}`;
    const startTime = new Date().toISOString();

    // Step 1: Load universe
    const universe = await step.do("load-universe", async () => {
      const pointerObj = await this.env.BACKUP_BUCKET.get("control/watch_universe/current.json");
      if (pointerObj) {
        const pointer = await pointerObj.json() as { source?: string };
        if (pointer.source) {
          const actual = await this.env.BACKUP_BUCKET.get(pointer.source);
          if (actual) return (await actual.json()) as { events: UniverseEvent[] };
        }
      }

      const legacy = await this.env.BACKUP_BUCKET.get(
        "canonical/2026-08-26T01-00-58Z/watch_universe_v1.json"
      );
      if (legacy) {
        const data = await legacy.json() as { events: any[] };
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

    // Step 2: Plan tasks — no Governor RPC in workflow.
    // Governor dedup is handled at reserveTask() in the queue consumer.
    const { tasksToQueue, budgetBlocked } = await step.do("plan-tasks", async () => {
      const tasks: AcquisitionTask[] = [];
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

        const targetUrl = event.marketplace_event_url;
        if (!targetUrl) continue;

        const taskKey = generateTaskKey(eventKey, event.marketplace, "FAST", window, "v1");

        tasks.push({
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
        });
      }

      // Cap at 25 for pilot
      if (tasks.length > 25) tasks.length = 25;

      return { tasksToQueue: tasks, budgetBlocked: budgetBlockedCount };
    });

    // Step 3: Queue tasks — consumer will reserve budget
    const queued = await step.do("queue-tasks", async () => {
      let count = 0;
      for (const task of tasksToQueue) {
        await this.env.FAST_QUEUE.send(task);
        count++;
      }
      return count;
    });

    // Step 4: Persist run record
    const run = await step.do("persist-run", async () => {
      const runRecord: AcquisitionRun = {
        run_id: runId,
        started_at: startTime,
        completed_at: new Date().toISOString(),
        status: "COMPLETED",
        events_planned: events.length,
        tasks_planned: tasksToQueue.length + budgetBlocked,
        tasks_queued: queued,
        tasks_suppressed: 0,
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
      return runRecord;
    });

    return run;
  }
}
