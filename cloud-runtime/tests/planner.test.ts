import { describe, it, expect } from "vitest";
import { planTasks, PlannerEnv } from "../src/planner";

/** Minimal mock R2 bucket that serves a universe via the stable control pointer */
function mockBucket(events: any[]): R2Bucket {
  const pointerKey = "control/watch_universe/current.json";
  const universeKey = "canonical/2026-08-26T01-00-58Z/watch_universe_v1.json";
  const universe = JSON.stringify({ events });

  const get = async (key: string): Promise<R2Object | null> => {
    if (key === pointerKey) {
      return { key, json: async () => ({ source: universeKey }) } as unknown as R2Object;
    }
    if (key === universeKey) {
      return { key, json: async () => JSON.parse(universe) } as unknown as R2Object;
    }
    return null;
  };

  return {
    get,
    put: async () => ({} as R2Object),
    delete: async () => {},
    list: async () => ({ objects: [], truncated: false }),
  } as unknown as R2Bucket;
}

/** Minimal Queue mock that captures sent messages */
function mockQueue(): Queue {
  return {
    send: async () => {},
    batch: async () => {},
  } as unknown as Queue;
}

function makeEnv(events: any[]): PlannerEnv {
  return {
    BACKUP_BUCKET: mockBucket(events),
    FAST_QUEUE: mockQueue(),
    SOFTWARE_VERSION: "test",
  };
}

/** The canonical_url is what loadUniverse uses to build marketplace_event_url for legacy events */
function legEvent(key: string, date: string, status = "EXACT_PAGE_MATCH") {
  return {
    event_key: key,
    event_date: date,
    canonical_url: `https://www.ticketmaster.com/${key}`,
    mapping_status: status,
  };
}

describe("Planner", () => {
  it("plans future exact-mapped events as due tasks when never observed", async () => {
    const env = makeEnv([legEvent("evt_1", "2099-01-01")]);
    const result = await planTasks(env, { max_tasks: 25 });
    expect(result.candidate_pairs).toBe(1);
    expect(result.due_pairs).toBe(1);
    expect(result.queued).toBe(1);
    expect(result.tasks[0].event_key).toBe("evt_1");
  });

  it("skips past-dated (post-show) events", async () => {
    const env = makeEnv([legEvent("evt_old", "2020-01-01")]);
    const result = await planTasks(env, { max_tasks: 25 });
    expect(result.queued).toBe(0);
    expect(result.candidate_pairs).toBe(0);
  });

  it("skips events without an exact mapping status", async () => {
    const env = makeEnv([legEvent("evt_amb", "2099-01-01", "AMBIGUOUS")]);
    const result = await planTasks(env, { max_tasks: 25 });
    expect(result.queued).toBe(0);
    expect(result.candidate_pairs).toBe(0);
  });

  it("respects cadence based on last observed hours", async () => {
    // ~45 days out → daily cadence (24h between observations)
    const future = new Date(Date.now() + 45 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
    const env = makeEnv([legEvent("evt_1", future)]);
    // Recently observed (1 hour ago) → not due for daily cadence
    const recent = await planTasks(env, {
      max_tasks: 25,
      getLastObservedHoursAgo: async () => 1,
    });
    expect(recent.queued).toBe(0);
    // Not observed for 50 hours → due
    const stale = await planTasks(env, {
      max_tasks: 25,
      getLastObservedHoursAgo: async () => 50,
    });
    expect(stale.queued).toBe(1);
  });

  it("caps at max_tasks", async () => {
    const events = Array.from({ length: 50 }, (_, i) => legEvent(`evt_${i}`, "2099-01-01"));
    const env = makeEnv(events);
    const result = await planTasks(env, { max_tasks: 5 });
    expect(result.queued).toBe(5);
  });

  it("counts all candidates across the full universe even when selection is capped (P2)", async () => {
    // 100 candidates, max_tasks=25 → candidate_pairs must be 100, not "candidates
    // scanned before the selection cap".  The old bug broke the scan loop early
    // and under-reported candidate_pairs.
    const events = Array.from({ length: 100 }, (_, i) => legEvent(`evt_${i}`, "2099-01-01"));
    const env = makeEnv(events);
    const result = await planTasks(env, { max_tasks: 25 });
    expect(result.candidate_pairs).toBe(100);
    expect(result.queued).toBe(25);
    // The unselected dues are reported as deferred, not silently dropped.
    expect(result.deferred_due).toBe(75);
    // Digest is deterministic
    expect(result.selected_task_digest.length).toBeGreaterThan(0);
  });

  it("a pair observed 15 minutes ago is NOT re-selected for a weekly/daily cadence event", async () => {
    // 45 days out → daily cadence (24h required).
    const future = new Date(Date.now() + 45 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
    const env = makeEnv([legEvent("evt_1", future)]);
    // Observed 0.25h ago (15 min) → NOT due again within 24h
    const reRun = await planTasks(env, {
      max_tasks: 25,
      getLastObservedHoursAgo: async () => 0.25,
    });
    expect(reRun.queued).toBe(0);
    expect(reRun.due_pairs).toBe(0);
  });

  it("different */15 invocations in the same hour share the logical window but still dispatch", async () => {
    const future = new Date(Date.now() + 45 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
    const env = makeEnv([legEvent("evt_1", future)]);
    // Never observed → due. Two planner runs in the same hour produce the SAME
    // logical window and SAME task_key (idempotency), as required: the second
    // would be suppressed by Governor.commitTask dedup on the same window.
    const run1 = await planTasks(env, { max_tasks: 25, getLastObservedHoursAgo: async () => null });
    const run2 = await planTasks(env, { max_tasks: 25, getLastObservedHoursAgo: async () => null });
    expect(run1.window).toBe(run2.window);
    expect(run1.tasks[0].task_key).toBe(run2.tasks[0].task_key);
    expect(run1.queued).toBe(1);
  });
});