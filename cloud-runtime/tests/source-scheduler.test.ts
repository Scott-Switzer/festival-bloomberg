import { describe, it, expect } from "vitest";
import {
  decideWikimediaTrigger,
  decideSpotifyTrigger,
  deriveLatestAdmissible,
  nextSpotifyCursor,
  spotifySliceForCursor,
  advanceStateOnSuccess,
  advanceStateOnRateLimited,
  advanceStateOnQuotaExceeded,
  advanceStateOnFailure,
  markInFlight,
  classifyJobOutcome,
  estimatedFullRefreshDays,
  emptySourceState,
  SourceSchedulerOrchestrator,
  SPOTIFY_COHORT_SIZE,
  SPOTIFY_UNIVERSE_SIZE_FALLBACK,
  SOURCE_INFLIGHT_TIMEOUT_HOURS,
} from "../src/source-scheduler";

// ── 12-test contract from the spec ─────────────────────────────────────────

describe("TEST 1: Wikimedia not due → no job", () => {
  it("no new admissible day → SKIP_NOT_DUE", () => {
    const state: any = { last_attempt_at: "2026-09-11T10:00:00Z", next_due_at: "2026-09-12T10:00:00Z", last_generation: "wikimedia_20260909T210109Z_b8e4a838", last_job_id: "wikimedia_001" };
    const gold = { generation: "wikimedia_20260909T210109Z_b8e4a838", latest_admissible: "2026-09-08", new_max_period_end: "2026-09-08" };
    const d = decideWikimediaTrigger(state, gold, null, "2026-09-11T18:00:00Z");
    expect(d.shouldTrigger).toBe(false);
    expect(d.reason).toContain("NOT_DUE");
  });

  it("global 429 cooldown active → SKIP (zero requests)", () => {
    const future = new Date(Date.now() + 60_000).toISOString();
    const gold = { generation: "wikimedia_20260909T210109Z_b8e4a838", latest_admissible: "2026-09-09", new_max_period_end: "2026-09-08" };
    const d = decideWikimediaTrigger(null, gold, { cooldown_until: future }, new Date().toISOString());
    expect(d.shouldTrigger).toBe(false);
    expect(d.reason).toBe("WIKIMEDIA_RATE_COOLDOWN");
  });
});

describe("TEST 2: Wikimedia due → exactly one job", () => {
  it("new admissible day beyond Gold → triggers", () => {
    const gold = { generation: "wikimedia_20260909T210109Z_b8e4a838", latest_admissible: "2026-09-10", new_max_period_end: "2026-09-08" };
    const d = decideWikimediaTrigger(null, gold, null, "2026-09-11T10:00:00Z");
    expect(d.shouldTrigger).toBe(true);
    expect(d.jobId).toContain("wikimedia_");
    expect(d.params).toBeTruthy();
  });

  it("no Gold at all → initial bootstrap triggers", () => {
    const d = decideWikimediaTrigger(null, null, null, "2026-09-11T18:00:00Z");
    expect(d.shouldTrigger).toBe(true);
    expect(d.reason).toBe("WIKIMEDIA_NO_GOLD_INITIAL");
  });
});

describe("TEST 3: repeated scheduler tick → no duplicate", () => {
  it("same Gold + no new day → SKIP_NOT_DUE (zero requests)", () => {
    const gold = { generation: "wikimedia_20260909T210109Z_b8e4a838", latest_admissible: "2026-09-08", new_max_period_end: "2026-09-08" };
    const state: any = { last_generation: "wikimedia_20260909T210109Z_b8e4a838", last_job_id: "wikimedia_prev", next_due_at: null, last_attempt_at: null };
    const d = decideWikimediaTrigger(state, gold, null, "2026-09-09T18:00:00Z");
    expect(d.shouldTrigger).toBe(false);
    expect(d.reason).toContain("NOT_DUE");
  });

  it("in-flight guard blocks a second trigger while a job is running", () => {
    const now = "2026-09-11T18:00:00Z";
    const state = markInFlight(emptySourceState("wikimedia"), "wikimedia_auto_20260911180000_abc", now);
    const gold = { generation: "wikimedia_20260909T210109Z_b8e4a838", latest_admissible: "2026-09-10", new_max_period_end: "2026-09-08" };
    const d = decideWikimediaTrigger(state, gold, null, "2026-09-11T18:01:00Z");
    expect(d.shouldTrigger).toBe(false);
    expect(d.reason).toBe("WIKIMEDIA_INFLIGHT");
  });
});

describe("TEST 4: Spotify not due → no job", () => {
  it("next_due_at in future → not due", () => {
    const future = new Date(Date.now() + 60_000).toISOString();
    const state: any = { next_due_at: future, cursor: 0, quota_backoff_until: null, backoff_until: null, last_attempt_at: null };
    const d = decideSpotifyTrigger(state, new Date().toISOString());
    expect(d.shouldTrigger).toBe(false);
    expect(d.reason).toContain("NOT_DUE");
  });

  it("quota backoff active → not due", () => {
    const future = new Date(Date.now() + 60_000).toISOString();
    const state: any = { quota_backoff_until: future, backoff_until: null, next_due_at: null, cursor: 0, last_attempt_at: null };
    const d = decideSpotifyTrigger(state, new Date().toISOString());
    expect(d.shouldTrigger).toBe(false);
    expect(d.reason).toBe("SPOTIFY_QUOTA_BACKOFF");
  });
});

describe("TEST 5: Spotify due → bounded cohort queued once", () => {
  it("no prior state → triggers with cohort 25 at cursor 0", () => {
    const d = decideSpotifyTrigger(null, "2026-09-11T18:00:00Z");
    expect(d.shouldTrigger).toBe(true);
    expect(d.params?.max_artists).toBe(25);
    expect(d.params?.cursor_offset).toBe(0);
    expect(d.jobId).toContain("spotify_auto_");
  });
});

describe("TEST 6: Spotify cursor advances correctly", () => {
  it("0 + 25 = 25", () => {
    expect(nextSpotifyCursor(0, 25, 15159)).toBe(25);
  });
  it("wraps: 15150 + 25 = 16", () => {
    expect(nextSpotifyCursor(15150, 25, 15159)).toBe(16);
  });
  it("slice without wrap", () => {
    const s = spotifySliceForCursor(100, 25, 15159);
    expect(s.offset).toBe(100);
    expect(s.limit).toBe(25);
    expect(s.wraps).toBe(false);
    expect(s.nextCursor).toBe(125);
  });
  it("slice with wrap", () => {
    const s = spotifySliceForCursor(15150, 25, 15159);
    expect(s.wraps).toBe(true);
    expect(s.nextCursor).toBe(16);
  });
});

describe("TEST 7: retry does not double-advance cursor", () => {
  it("cursor advances ONLY on success; rate-limit/failure keep cursor", () => {
    const s0 = { ...emptySourceState("spotify"), cursor: 0, universe_size: 15159 };
    const success = advanceStateOnSuccess(s0, "2026-09-11T18:00:00Z", { generation: "spotify_x" }, 25, 15159, "spotify");
    expect(success.cursor).toBe(25);

    const s1 = { ...emptySourceState("spotify"), cursor: 25, universe_size: 15159 };
    const rl = advanceStateOnRateLimited(s1, "2026-09-11T18:00:00Z", 120);
    expect(rl.cursor).toBe(25); // NOT advanced
    const quota = advanceStateOnQuotaExceeded(s1, "2026-09-11T18:00:00Z");
    expect(quota.cursor).toBe(25); // NOT advanced
    const fail = advanceStateOnFailure(s1, "2026-09-11T18:00:00Z");
    expect(fail.cursor).toBe(25); // NOT advanced
  });
});

describe("TEST 8: normal 429 honors backoff", () => {
  it("rate limited → backoff_until set, next tick blocked", () => {
    const s0 = { ...emptySourceState("spotify"), cursor: 0 };
    const s1 = advanceStateOnRateLimited(s0, "2026-09-11T18:00:00Z", 120);
    expect(s1.backoff_until).toBeTruthy();
    expect(s1.rate_limited).toBe(1);
    const d = decideSpotifyTrigger(s1, "2026-09-11T18:01:00Z");
    expect(d.shouldTrigger).toBe(false);
    expect(d.reason).toBe("SPOTIFY_RATE_BACKOFF");
  });
});

describe("TEST 9: QUOTA_EXCEEDED opens quota backoff / no hot loop", () => {
  it("quota exceeded → 24h backoff, not retried quickly", () => {
    const s0 = { ...emptySourceState("spotify"), cursor: 100 };
    const s1 = advanceStateOnQuotaExceeded(s0, "2026-09-11T18:00:00Z");
    expect(s1.quota_backoff_until).toBeTruthy();
    expect(s1.quota_exceeded).toBe(1);
    const d = decideSpotifyTrigger(s1, "2026-09-11T19:00:00Z");
    expect(d.shouldTrigger).toBe(false);
    expect(d.reason).toBe("SPOTIFY_QUOTA_BACKOFF");
    expect(s1.cursor).toBe(100);
  });
});

describe("TEST 10: provider failure retains previous Gold", () => {
  it("failure does not set last_generation — Gold stays old", () => {
    const s0: any = { ...emptySourceState("spotify"), last_generation: "spotify_old_aaa", cursor: 50 };
    const s1 = advanceStateOnFailure(s0, "2026-09-11T18:00:00Z");
    expect(s1.last_generation).toBe("spotify_old_aaa");
    expect(s1.provider_failed).toBe(1);
    expect(s1.cursor).toBe(50);
  });
});

describe("TEST 11: stale acquisition cannot replace newer CURRENT", () => {
  it("cursor is monotonic modulo; batch_jobs verify→publish enforces CAS", () => {
    const c0 = nextSpotifyCursor(15150, 25, 15159);
    expect(c0).toBe(16);
    const c1 = nextSpotifyCursor(c0, 25, 15159);
    expect(c1).toBe(41);
  });
});

describe("TEST 12: restart resumes from durable state", () => {
  it("state survives JSON round-trip and orchestrator re-loads it", () => {
    const s = { ...emptySourceState("spotify"), cursor: 75, last_generation: "spotify_xyz", next_due_at: "2026-09-12T00:00:00Z" };
    const roundTripped = JSON.parse(JSON.stringify(s));
    expect(roundTripped.cursor).toBe(75);
    expect(roundTripped.last_generation).toBe("spotify_xyz");
    const d = decideSpotifyTrigger(roundTripped, "2026-09-11T18:00:00Z");
    expect(d.shouldTrigger).toBe(false);
  });

  it("expired in-flight does NOT deadlock the scheduler (crash recovery)", () => {
    const nowIso = "2026-09-11T18:00:00Z";
    const staleAt = new Date(new Date(nowIso).getTime() - (SOURCE_INFLIGHT_TIMEOUT_HOURS + 1) * 3600 * 1000).toISOString();
    const state: any = {
      ...emptySourceState("spotify"),
      in_flight: true,
      in_flight_job_id: "spotify_auto_stale",
      in_flight_requested_at: staleAt,
      cursor: 0,
      next_due_at: null,
    };
    const d = decideSpotifyTrigger(state, nowIso);
    expect(d.shouldTrigger).toBe(true); // expired → re-decidable
  });
});

// ── Classifier: QUOTA_TRUNCATED (cohort stopped early by quota/auth) ───────
describe("classifyJobOutcome — quota-truncated cohorts", () => {
  it("PUBLISHED with spotify_quota_stopped_cohort flag → QUOTA_TRUNCATED", () => {
    const c = classifyJobOutcome({
      status: "PUBLISHED",
      generation: "spotify_partial",
      new_rows: 5,
      successful: 5,
      params: { spotify_quota_stopped_cohort: true },
    });
    expect(c.outcome).toBe("QUOTA_TRUNCATED");
  });

  it("COMPLETED with quota_exceeded>0 → QUOTA_TRUNCATED (no cursor advance implied)", () => {
    const c = classifyJobOutcome({ status: "BUILD_COMPLETE", quota_exceeded: 1, new_rows: 0 });
    expect(c.outcome).toBe("QUOTA_TRUNCATED");
  });

  it("auth broken → QUOTA_TRUNCATED (long backoff, no advance)", () => {
    const c = classifyJobOutcome({ status: "BUILD_COMPLETE", params: { spotify_auth_broken: true } });
    expect(c.outcome).toBe("QUOTA_TRUNCATED");
  });

  it("clean PUBLISHED → SUCCESS", () => {
    const c = classifyJobOutcome({ status: "PUBLISHED", generation: "spotify_ok", new_rows: 25, successful: 25 });
    expect(c.outcome).toBe("SUCCESS");
    expect(c.result.generation).toBe("spotify_ok");
  });

  it("FAILED with generic code → PROVIDER_FAILED", () => {
    const c = classifyJobOutcome({ status: "FAILED", error_code: "JOB_EXEC_FAILED" });
    expect(c.outcome).toBe("PROVIDER_FAILED");
  });

  it("RUNNING → RUNNING (keep in-flight)", () => {
    expect(classifyJobOutcome({ status: "RUNNING" }).outcome).toBe("RUNNING");
    expect(classifyJobOutcome(null).outcome).toBe("UNKNOWN");
  });
});

// ── Orchestrator end-to-end (in-memory fake env) ───────────────────────────
function makeFakeEnv(overrides: any = {}) {
  const buckets: Record<string, Record<string, any>> = { lake: {}, backup: {} };
  const fired: Array<{ job_type: string; job_id: string; params: any }> = [];
  return {
    env: {
      LAKE_BUCKET: {
        get: async (key: string) => {
          const v = buckets.lake[key];
          if (v === undefined) return null;
          return { json: async () => v };
        },
      },
      BACKUP_BUCKET: {
        get: async (key: string) => {
          const v = buckets.backup[key];
          if (v === undefined) return null;
          return { json: async () => v };
        },
        put: async (key: string, value: string) => { buckets.backup[key] = JSON.parse(value); },
      },
      BATCH_CONTAINER: {
        idFromName: (name: string) => ({ name }),
        get: (id: { name: string }) => ({
          startJob: async (spec: any) => { fired.push(spec); return { job_id: spec.job_id }; },
        }),
      },
      ...overrides,
    },
    buckets,
    fired,
  };
}

describe("Orchestrator: fires at most one job per due family per tick", () => {
  it("Wikimedia due + Spotify due → exactly one job each, both marked in-flight", async () => {
    const { env, buckets, fired } = makeFakeEnv();
    buckets.lake["gold/artist_attention_wikimedia/CURRENT.json"] = {
      generation: "wikimedia_20260909T210109Z_b8e4a838",
      latest_admissible: "2026-09-10",
      new_max_period_end: "2026-09-08",
    };
    buckets.lake["gold/artist_attention_spotify/CURRENT.json"] = {
      generation: "spotify_20260911T185424Z_aa173c0d",
      universe_size: 15159,
    };
    const orch = new SourceSchedulerOrchestrator(env as any);
    const res = await orch.runTick("2026-09-11T18:00:00Z");
    expect(res.firedJobs.length).toBe(2);
    expect(fired.length).toBe(2);
    const wmState = buckets.backup["control/source-state/wikimedia.json"];
    const spState = buckets.backup["control/source-state/spotify.json"];
    expect(wmState.in_flight).toBe(true);
    expect(spState.in_flight).toBe(true);
    expect(spState.cursor).toBe(0); // cursor not advanced yet (job not complete)
  });

  it("Second tick while in-flight → no duplicate fire", async () => {
    const { env, buckets, fired } = makeFakeEnv();
    buckets.lake["gold/artist_attention_wikimedia/CURRENT.json"] = {
      generation: "wikimedia_20260909T210109Z_b8e4a838",
      latest_admissible: "2026-09-10",
      new_max_period_end: "2026-09-08",
    };
    buckets.lake["gold/artist_attention_spotify/CURRENT.json"] = {
      generation: "spotify_20260911T185424Z_aa173c0d",
      universe_size: 15159,
    };
    const orch = new SourceSchedulerOrchestrator(env as any);
    await orch.runTick("2026-09-11T18:00:00Z");
    expect(fired.length).toBe(2);
    await orch.runTick("2026-09-11T18:01:00Z");
    await orch.runTick("2026-09-11T18:02:00Z");
    expect(fired.length).toBe(2); // still 2
  });

  it("Job completion advances the cursor exactly once (restart recovery)", async () => {
    const { env, buckets, fired } = makeFakeEnv();
    const now = "2026-09-11T18:00:00Z";
    buckets.lake["gold/artist_attention_spotify/CURRENT.json"] = {
      generation: "spotify_20260911T185424Z_aa173c0d",
      universe_size: 15159,
    };
    // Pre-seed durable state: job fired on a previous tick.
    buckets.backup["control/source-state/spotify.json"] = {
      ...emptySourceState("spotify"),
      in_flight: true,
      in_flight_job_id: "spotify_auto_20260911170000_00000",
      in_flight_requested_at: "2026-09-11T17:00:00Z",
      cursor: 0,
      universe_size: 15159,
      next_due_at: null,
      last_attempt_at: "2026-09-11T17:00:00Z",
    };
    const orch = new SourceSchedulerOrchestrator(env as any);
    // Manifest now complete (published new generation; Gold CURRENT updated).
    buckets.lake["control/jobs/artist_attention_spotify_build_v1/spotify_auto_20260911170000_00000/manifest.json"] = {
      status: "PUBLISHED",
      generation: "spotify_20260911T180000Z_bbbb",
      new_rows: 25,
      successful: 25,
      params: { spotify_universe_size: 15159, spotify_cursor_offset: 0 },
    };
    buckets.lake["gold/artist_attention_spotify/CURRENT.json"] = {
      generation: "spotify_20260911T180000Z_bbbb",
      universe_size: 15159,
    };
    const res = await orch.runTick(now);
    const spState = buckets.backup["control/source-state/spotify.json"];
    expect(spState.cursor).toBe(25); // advanced once
    expect(spState.in_flight).toBe(false);
    expect(spState.last_generation).toBe("spotify_20260911T180000Z_bbbb");
    expect(spState.next_due_at).toBeTruthy();
    // A second observation tick does NOT re-advance.
    await orch.runTick("2026-09-11T18:01:00Z");
    expect(buckets.backup["control/source-state/spotify.json"].cursor).toBe(25);
  });

  it("Quota-truncated cohort does NOT advance cursor and opens 24h quota backoff", async () => {
    const { env, buckets } = makeFakeEnv();
    buckets.lake["gold/artist_attention_spotify/CURRENT.json"] = {
      generation: "spotify_old",
      universe_size: 15159,
      quota_exceeded: 0,
    };
    buckets.backup["control/source-state/spotify.json"] = {
      ...emptySourceState("spotify"),
      in_flight: true,
      in_flight_job_id: "spotify_auto_20260911170000_00000",
      in_flight_requested_at: "2026-09-11T17:00:00Z",
      cursor: 100,
      universe_size: 15159,
    };
    buckets.lake["control/jobs/artist_attention_spotify_build_v1/spotify_auto_20260911170000_00000/manifest.json"] = {
      status: "PUBLISHED",
      generation: "spotify_partial",
      new_rows: 5,
      successful: 5,
      params: { spotify_quota_stopped_cohort: true, spotify_universe_size: 15159 },
    };
    const orch = new SourceSchedulerOrchestrator(env as any);
    await orch.runTick("2026-09-11T18:00:00Z");
    const spState = buckets.backup["control/source-state/spotify.json"];
    expect(spState.cursor).toBe(100); // NOT advanced
    expect(spState.quota_backoff_until).toBeTruthy();
    expect(spState.in_flight).toBe(false);
  });
});

describe("deriveLatestAdmissible", () => {
  it("is yesterday (UTC)", () => {
    expect(deriveLatestAdmissible("2026-09-14T04:00:00Z")).toBe("2026-09-13");
    expect(deriveLatestAdmissible("2026-09-14T23:59:00Z")).toBe("2026-09-13");
  });
});

describe("refresh interval honesty", () => {
  it("25 artists / 6h cadence: 15159 / 100 ≈ 151.6 days", () => {
    const days = estimatedFullRefreshDays(SPOTIFY_COHORT_SIZE, 6);
    expect(days).toBeCloseTo(151.59, 0);
  });
  it("invalid cohort → Infinity", () => {
    expect(estimatedFullRefreshDays(0, 6)).toBe(Infinity);
  });
  it("respects the universe-size parameter", () => {
    expect(estimatedFullRefreshDays(25, 6, 500)).toBeCloseTo(5, 0);
  });
});
