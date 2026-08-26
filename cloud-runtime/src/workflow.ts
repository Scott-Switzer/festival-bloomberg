/**
 * Acquisition Workflow — durable scheduled acquisition runner.
 * Uses Cloudflare WorkflowEntrypoint with durable steps.
 */

import { WorkflowEntrypoint, WorkflowStep, WorkflowEvent } from "cloudflare:workers";
import {
  AcquisitionTask,
  AcquisitionRun,
  generateTaskKey,
  AcquisitionRail,
} from "./task-contract";
import {
  shouldObserveNow,
  DEFAULT_CADENCE_POLICY,
} from "./scheduler";
import { createScorecard } from "./observability";

interface WorkflowEnv {
  FAST_QUEUE: Queue;
  DEEP_QUEUE: Queue;
  PROCESSING_QUEUE: Queue;
  DLQ_QUEUE: Queue;
  RAW_BUCKET: R2Bucket;
  LAKE_BUCKET: R2Bucket;
  BACKUP_BUCKET: R2Bucket;
  GOVERNOR: DurableObjectNamespace;
  ACQUISITION_CONTAINER: DurableObjectNamespace;
  ACQUISITION_WORKFLOW: Workflow;
  MONID_API_KEY: string;
  TICKETMASTER_API_KEY: string;
  TICKETS_DEV_API_KEY: string;
  POLICY_VERSION: string;
  SOFTWARE_VERSION: string;
  DAILY_BUDGET_USD: string;
  MONTHLY_BUDGET_USD: string;
}

export class AcquisitionWorkflow extends WorkflowEntrypoint<WorkflowEnv, Record<string, never>> {
  async run(event: WorkflowEvent<Record<string, never>>, step: WorkflowStep): Promise<AcquisitionRun> {
    const runId = `wf_${Date.now()}`;
    const startTime = new Date().toISOString();

    // Step 1: Load universe
    const universe = await step.do("load-universe", async () => {
      const obj = await this.env.BACKUP_BUCKET.get("control/watch_universe/current.json");
      if (!obj) {
        const fallback = await this.env.BACKUP_BUCKET.get(
          "canonical/2026-08-26T01-00-58Z/watch_universe_v1.json"
        );
        if (!fallback) throw new Error("No acquisition universe found");
        return fallback.json() as Promise<{ events: any[] }>;
      }
      const data = await obj.json() as { source?: string; events?: any[] };
      // Follow pointer to actual universe if needed
      if (data.source) {
        const actual = await this.env.BACKUP_BUCKET.get(data.source);
        if (actual) return actual.json() as Promise<{ events: any[] }>;
      }
      return { events: data.events || [] };
    });

    const events = universe.events || [];

    // Step 2: Plan tasks
    const { tasksToQueue, suppressed, budgetBlocked } = await step.do("plan-tasks", async () => {
      const governorId = this.env.GOVERNOR.idFromName("acquisition-governor");
      const governor = this.env.GOVERNOR.get(governorId) as any;

      const tasks: AcquisitionTask[] = [];
      let suppressedCount = 0;
      let budgetBlockedCount = 0;

      for (const event of events) {
        const eventKey = event.event_key || event.id;
        const eventDate = new Date(event.event_date || event.date);
        const now = new Date();
        const daysToShow = (eventDate.getTime() - now.getTime()) / (1000 * 60 * 60 * 24);

        if (daysToShow < 0) continue;

        const lastObservedHoursAgo = event.last_observed_hours_ago || 999;
        if (!shouldObserveNow(daysToShow, lastObservedHoursAgo, DEFAULT_CADENCE_POLICY)) continue;

        const marketplace = event.marketplace || "ticketmaster";
        const rail: AcquisitionRail = "FAST";
        const targetUrl = event.canonical_url || event.target_url || event.url || "";
        if (!targetUrl) continue;

        const taskKey = generateTaskKey(eventKey, marketplace, rail, now.toISOString().slice(0, 13), "v1");

        const reserveResult = await governor.reserveTask({
          task_key: taskKey,
          provider: marketplace,
          expected_max_cost_usd: 0.0009,
          container_id: `wf_${runId}`,
        });

        if (!reserveResult.allowed) {
          if (reserveResult.reason === "DUPLICATE_TASK") suppressedCount++;
          else budgetBlockedCount++;
          continue;
        }

        tasks.push({
          task_key: taskKey,
          event_key: eventKey,
          source: marketplace as any,
          marketplace,
          rail,
          target_url: targetUrl,
          scheduled_window: now.toISOString().slice(0, 13),
          priority: daysToShow <= 7 ? 1 : daysToShow <= 30 ? 2 : 3,
          expected_max_cost_usd: 0.0009,
          created_at: now.toISOString(),
          software_version: this.env.SOFTWARE_VERSION,
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

      return { tasksToQueue: tasks, suppressed: suppressedCount, budgetBlocked: budgetBlockedCount };
    });

    // Step 3: Queue tasks
    const queued = await step.do("queue-tasks", async () => {
      let count = 0;
      for (const task of tasksToQueue) {
        if (task.rail === "FAST") await this.env.FAST_QUEUE.send(task);
        else if (task.rail === "DEEP") await this.env.DEEP_QUEUE.send(task);
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
      return runRecord;
    });

    return run;
  }
}
