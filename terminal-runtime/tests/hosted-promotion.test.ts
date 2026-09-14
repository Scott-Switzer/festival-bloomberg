import { describe, it, expect } from "vitest";
import {
  decidePromotion,
  emptyPromotionState,
  stateAfterTrigger,
  stateAfterVerified,
  stateAfterVerifyFailure,
  HostedPromotionOrchestrator,
  PROMOTION_MAX_FAILURES,
  PROMOTION_STATE_KEY,
} from "../src/hosted-promotion";

const NOW = "2026-09-13T19:00:00.000Z";

describe("decidePromotion invariants", () => {
  it("1. same Serving generation (hosted verified) → no duplicate promotion", () => {
    const st = stateAfterVerified("terminal_v1_20260913T185647Z", NOW);
    const d = decidePromotion("terminal_v1_20260913T185647Z", st, NOW);
    expect(d.action).toBe("NONE");
    expect(d.deduped).toBe(true);
  });

  it("2. new Serving generation → one promotion (TRIGGER_RESTART)", () => {
    const st = stateAfterVerified("terminal_v1_OLD", NOW);
    const d = decidePromotion("terminal_v1_NEW", st, NOW);
    expect(d.action).toBe("TRIGGER_RESTART");
    expect(d.deduped).toBe(false);
  });

  it("in-flight promotion for the same generation → VERIFY, not re-trigger", () => {
    const st = stateAfterTrigger("terminal_v1_NEW", NOW);
    const d = decidePromotion("terminal_v1_NEW", st, NOW);
    expect(d.action).toBe("VERIFY");
    expect(d.deduped).toBe(true);
  });

  it("verify backoff is honored while in-flight", () => {
    const st = stateAfterVerifyFailure(stateAfterTrigger("terminal_v1_NEW", NOW), NOW);
    const justAfter = new Date(new Date(NOW).getTime() + 1_000).toISOString();
    const d = decidePromotion("terminal_v1_NEW", st, justAfter);
    expect(d.action).toBe("BACKOFF");
  });

  it("max failures → stop auto-retrying (needs human)", () => {
    let st = stateAfterTrigger("terminal_v1_NEW", NOW);
    for (let i = 0; i < PROMOTION_MAX_FAILURES; i++) st = stateAfterVerifyFailure(st, NOW);
    expect(st.pages_status).toBe("FAILED");
    const d = decidePromotion("terminal_v1_NEW", st, new Date(Date.now() + 120_000).toISOString());
    expect(d.action).toBe("NONE");
    expect(d.reason).toBe("PROMOTION_STOPPED_MAX_FAILURES");
  });

  it("a NEWER serving generation is still promotable after a stopped older one", () => {
    let st = stateAfterTrigger("terminal_v1_OLD", NOW);
    for (let i = 0; i < PROMOTION_MAX_FAILURES; i++) st = stateAfterVerifyFailure(st, NOW);
    const d = decidePromotion("terminal_v1_NEWER", st, NOW);
    expect(d.action).toBe("TRIGGER_RESTART");
  });

  it("no serving generation → NONE", () => {
    const d = decidePromotion(null, emptyPromotionState(), NOW);
    expect(d.action).toBe("NONE");
  });
});

describe("state transitions", () => {
  it("trigger → in-flight with dedupe key set", () => {
    const st = stateAfterTrigger("terminal_v1_NEW", NOW);
    expect(st.pages_status).toBe("IN_FLIGHT");
    expect(st.pages_trigger_generation).toBe("terminal_v1_NEW");
    expect(st.serving_generation).toBe("terminal_v1_NEW");
  });

  it("verified → HOSTED_FRESH with hosted_generation == serving_generation", () => {
    const st = stateAfterVerified("terminal_v1_NEW", NOW);
    expect(st.pages_status).toBe("HOSTED_FRESH");
    expect(st.hosted_generation).toBe("terminal_v1_NEW");
    expect(st.hosted_verified_at).toBe(NOW);
  });

  it("verify failure keeps in-flight until max failures, then FAILED", () => {
    let st = stateAfterTrigger("terminal_v1_NEW", NOW);
    st = stateAfterVerifyFailure(st, NOW);
    expect(st.pages_status).toBe("IN_FLIGHT");
    expect(st.failure_count).toBe(1);
    for (let i = 1; i < PROMOTION_MAX_FAILURES; i++) st = stateAfterVerifyFailure(st, NOW);
    expect(st.pages_status).toBe("FAILED");
    expect(st.failure_count).toBe(PROMOTION_MAX_FAILURES);
  });
});

// ── Orchestrator with fake env ─────────────────────────────────────────────
function makeFakeEnv(overrides: any = {}) {
  const lake: Record<string, any> = {};
  const backup: Record<string, any> = {};
  const restarts: string[] = [];
  let hostedGeneration: string | null = "terminal_v1_OLD";
  return {
    env: {
      LAKE_BUCKET: {
        get: async (key: string) => (key in lake ? { json: async () => lake[key] } : null),
      },
      BACKUP_BUCKET: {
        get: async (key: string) => (key in backup ? { json: async () => backup[key] } : null),
        put: async (key: string, value: string) => { backup[key] = JSON.parse(value); },
      },
      TERMINAL_CONTAINER: {
        idFromName: (name: string) => ({ name }),
        get: () => ({
          restartContainer: async (reason: string) => {
            restarts.push(reason);
            // After restart, the (fake) container serves the CURRENT lake gen.
            hostedGeneration = lake["serving/artist_security_terminal_v1/CURRENT.json"]?.generation ?? null;
            return { restarted: true, reason };
          },
          fetch: async () => new Response(JSON.stringify({ generation: hostedGeneration }), { status: 200 }),
        }),
      },
      ...overrides,
    },
    lake,
    backup,
    restarts,
    setHosted: (g: string | null) => { hostedGeneration = g; },
  };
}

describe("HostedPromotionOrchestrator", () => {
  it("advancing Serving generation → restart triggered once, then verified HOSTED_FRESH", async () => {
    const { env, lake, backup, restarts } = makeFakeEnv();
    lake["serving/artist_security_terminal_v1/CURRENT.json"] = { generation: "terminal_v1_NEW" };
    // Seed: already verified on OLD generation.
    backup[PROMOTION_STATE_KEY] = stateAfterVerified("terminal_v1_OLD", NOW);

    const orch = new HostedPromotionOrchestrator(env as any);
    const t1 = await orch.runTick(NOW);
    expect(t1.decision.action).toBe("TRIGGER_RESTART");
    expect(t1.triggered).toBe(true);
    expect(restarts.length).toBe(1);
    expect(backup[PROMOTION_STATE_KEY].pages_status).toBe("IN_FLIGHT");

    // Next tick: verify (fake container now serves NEW after restart).
    const t2 = await orch.runTick(new Date(new Date(NOW).getTime() + 120_000).toISOString());
    expect(t2.decision.action).toBe("VERIFY");
    const st = backup[PROMOTION_STATE_KEY];
    expect(st.pages_status).toBe("HOSTED_FRESH");
    expect(st.hosted_generation).toBe("terminal_v1_NEW");
  });

  it("hosted still OLD after restart → NOT claimed fresh; failure counted; retried later", async () => {
    const { env, lake, backup, setHosted, restarts } = makeFakeEnv();
    lake["serving/artist_security_terminal_v1/CURRENT.json"] = { generation: "terminal_v1_NEW" };
    backup[PROMOTION_STATE_KEY] = stateAfterVerified("terminal_v1_OLD", NOW);

    const orch = new HostedPromotionOrchestrator(env as any);
    await orch.runTick(NOW);
    expect(restarts.length).toBe(1);

    // Container did NOT pick up the new generation.
    setHosted("terminal_v1_OLD");
    const later = new Date(new Date(NOW).getTime() + 120_000).toISOString();
    const t2 = await orch.runTick(later);
    const st = backup[PROMOTION_STATE_KEY];
    expect(st.pages_status).toBe("IN_FLIGHT");
    expect(st.failure_count).toBe(1);
    expect(st.hosted_generation).not.toBe("terminal_v1_NEW"); // never claimed fresh
  });

  it("no duplicate restart when already verified for the same generation", async () => {
    const { env, lake, backup, restarts } = makeFakeEnv();
    lake["serving/artist_security_terminal_v1/CURRENT.json"] = { generation: "terminal_v1_NEW" };
    backup[PROMOTION_STATE_KEY] = stateAfterVerified("terminal_v1_NEW", NOW);

    const orch = new HostedPromotionOrchestrator(env as any);
    const t = await orch.runTick(NOW);
    expect(t.decision.action).toBe("NONE");
    expect(restarts.length).toBe(0);
  });
});
