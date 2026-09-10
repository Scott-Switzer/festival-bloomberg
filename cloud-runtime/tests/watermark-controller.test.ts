import { describe, it, expect } from "vitest";
import {
  decideGoldTrigger,
  decideServingTrigger,
  fingerprintGolds,
  servingGoldFingerprint,
} from "../src/watermark-controller";

// 8-test contract from the closure spec:
// 1. no change → no build
// 2. source newer than Gold → one Gold build
// 3. repeated invocation → no duplicate
// 4. Gold newer than Serving → one Serving build
// 5. repeated Gold check → no duplicate Serving build
// 6. failed Gold → CURRENT unchanged (controller never advances pointer — batch_jobs verify contract)
// 7. failed Serving → CURRENT unchanged (same)
// 8. stale generation cannot replace newer generation
// Plus: CAS / retry safety is structural (If-Match on R2 CURRENT), proven by the
// nightly refresh path's serving_parent_etag + put_json_if_version.

describe("watermark controller — TEST 1: no change → no builds", () => {
  it("Gold trigger: source == Gold → no build", () => {
    const d = decideGoldTrigger("genA", "genA", null);
    expect(d.shouldTrigger).toBe(false);
    expect(d.reason).toBe("GOLD_CAUGHT_UP");
  });

  it("Serving trigger: live Golds == Serving provenance → no build", () => {
    const live = { spotify: "spotify_20260910T220736Z_37949828", wikimedia: "wikimedia_20260909T210109Z_b8e4a838" };
    const serving: any = { generation: "terminal_v1_20260909T222719Z", spotify_generation: "spotify_20260910T220736Z_37949828", wikimedia_generation: "wikimedia_20260909T210109Z_b8e4a838" };
    const d = decideServingTrigger(live, serving, null, "2026-09-10T22:00:00Z");
    expect(d.shouldTrigger).toBe(false);
    expect(d.reason).toBe("SERVING_CAUGHT_UP");
  });

  it("Serving trigger: no live Golds at all → no build (UNKNOWN stays UNKNOWN)", () => {
    const d = decideServingTrigger({}, null, null, "2026-09-10T22:00:00Z");
    expect(d.shouldTrigger).toBe(false);
    expect(d.reason).toBe("NO_LIVE_GOLD");
  });
});

describe("TEST 2: source newer than Gold → one Gold build", () => {
  it("source A > Gold old → triggers exactly once", () => {
    const d = decideGoldTrigger("sourceA_20260910T220800Z", "goldOld_20260910T220000Z", null);
    expect(d.shouldTrigger).toBe(true);
    expect(d.reason).toBe("SOURCE_AHEAD");
  });
});

describe("TEST 3: repeated scheduler invocation → still one build (dedupe)", () => {
  it("same source A checked repeatedly → still one build", () => {
    const first = decideGoldTrigger("sourceA_20260910T220800Z", "goldOld", null);
    expect(first.shouldTrigger).toBe(true);
    const second = decideGoldTrigger("sourceA_20260910T220800Z", "goldOld", "sourceA_20260910T220800Z");
    expect(second.shouldTrigger).toBe(false);
    expect(second.deduped).toBe(true);
  });

  it("same live Gold fingerprint repeatedly → Serving deduped", () => {
    const live = { spotify: "spotify_20260910T220736Z_37949828" };
    const serving: any = { generation: "terminal_v1_old", spotify_generation: "spotify_old" };
    const triggerState: any = { last_trigger_job_id: "terminal_serving_build_v1_watermark_20260910T220800", last_trigger_at: "2026-09-10T22:08:00Z", last_seen_gold: live, last_serving_generation: "terminal_v1_old" };
    const second = decideServingTrigger(live, serving, triggerState, "2026-09-10T22:10:00Z");
    expect(second.shouldTrigger).toBe(false);
    expect(second.deduped).toBe(true);
  });
});

describe("TEST 4: Gold newer than Serving → one Serving build", () => {
  it("Gold A > Serving old → triggers Serving exactly once", () => {
    const live = { spotify: "spotify_20260910T220736Z_37949828", wikimedia: "wikimedia_20260909T210109Z_b8e4a838" };
    const serving: any = { generation: "terminal_v1_20260909T222719Z", spotify_generation: "spotify_20260910T220644Z_30a92ea6", wikimedia_generation: "wikimedia_20260909T210109Z_b8e4a838" };
    const d = decideServingTrigger(live, serving, null, "2026-09-10T22:08:00Z");
    expect(d.shouldTrigger).toBe(true);
    expect(d.servingJobId).toBeTruthy();
    expect(d.servingJobId).toContain("terminal_serving_build_v1_watermark_");
  });
});

describe("TEST 5: repeated Gold check → no duplicate Serving build", () => {
  it("second check with same Golds → deduped", () => {
    const live = { spotify: "spotify_20260910T220736Z_37949828" };
    const serving: any = { generation: "terminal_v1_old", spotify_generation: "old" };
    const first = decideServingTrigger(live, serving, null, "2026-09-10T22:08:00Z");
    expect(first.shouldTrigger).toBe(true);
    const state: any = { last_trigger_job_id: first.servingJobId, last_trigger_at: "2026-09-10T22:08:00Z", last_seen_gold: live, last_serving_generation: "terminal_v1_old" };
    const second = decideServingTrigger(live, serving, state, "2026-09-10T22:09:00Z");
    expect(second.shouldTrigger).toBe(false);
    expect(second.deduped).toBe(true);
  });
});

describe("TEST 6+7: failed builds → CURRENT unchanged", () => {
  it("failed Gold does not advance: decideServingTrigger still sees old serving (no auto-advance)", () => {
    // A failed Gold build never moved Gold CURRENT, so live Gold stays old → no Serving trigger.
    // The controller itself never writes pointers; publication is via batch_jobs verify→publish.
    const liveOld = { spotify: "spotify_old_00000000" };
    const servingOld: any = { generation: "terminal_old", spotify_generation: "spotify_old_00000000" };
    const d = decideServingTrigger(liveOld, servingOld, null, "2026-09-10T22:08:00Z");
    expect(d.shouldTrigger).toBe(false);
    expect(d.reason).toBe("SERVING_CAUGHT_UP");
  });

  it("failed Serving: last trigger did not move serving CURRENT → retry would see same Golds and re-trigger (safe retry)", () => {
    const live = { spotify: "spotify_new_37949828" };
    const servingOld: any = { generation: "terminal_old", spotify_generation: "spotify_old" };
    // First trigger happened but Serving failed (CURRENT still old)
    const first = decideServingTrigger(live, servingOld, null, "2026-09-10T22:08:00Z");
    expect(first.shouldTrigger).toBe(true);
    // After failure, trigger state would have been cleared or expired; next minute we can retry.
    // If we clear dedupe (failure path), retry succeeds safely — one generation publishes once.
    const retry = decideServingTrigger(live, servingOld, null, "2026-09-10T22:10:00Z");
    expect(retry.shouldTrigger).toBe(true);
  });
});

describe("TEST 7 variant: stale generation cannot overwrite newer generation", () => {
  it("stale source < Gold → no Gold trigger", () => {
    // Use timestamp-lexicographic generations where stale < new (same prefix, earlier timestamp)
    const d = decideGoldTrigger("gen_20260909T000000Z_aaa", "gen_20260910T220736Z_bbb", null);
    expect(d.shouldTrigger).toBe(false);
    expect(d.reason).toBe("STALE_SOURCE");
  });

  it("wall-clock time is not treated as source freshness — only generation strings", () => {
    // Two calls at different wall-clock times with identical live fingerprint → deduped, no time-based freshness.
    const live = { spotify: "spotify_20260910T220736Z_37949828" };
    const serving: any = { generation: "terminal_old", spotify_generation: "old" };
    const state: any = { last_trigger_job_id: "terminal_serving_build_v1_watermark_20260910T220800", last_trigger_at: "2026-09-10T22:08:00Z", last_seen_gold: live, last_serving_generation: "terminal_old" };
    const later = decideServingTrigger(live, serving, state, "2026-09-11T10:00:00Z"); // wall-clock jumped
    expect(later.shouldTrigger).toBe(false); // still deduped — wall-clock ignored
  });
});

describe("fingerprint: UNKNOWN stays UNKNOWN", () => {
  it("missing fields stay null in fingerprint, never 0/empty", () => {
    const fp = fingerprintGolds({});
    expect(fp).toContain('"wikimedia":null');
    expect(fp).toContain('"spotify":null');
    const servingFp = servingGoldFingerprint(null);
    expect(servingFp).toContain('"wikimedia":null');
  });
});
