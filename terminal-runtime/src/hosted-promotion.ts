/**
 * Hosted Promotion — automatic propagation of a new Serving generation to the
 * hosted terminal ("hosted Pages" in the milestone terminology).
 *
 * Architecture finding (PHASE 0): there is NO Cloudflare Pages project. The
 * hosted terminal is a Workers+Containers app whose container reads the lake
 * `serving/artist_security_terminal_v1/CURRENT.json` (and streams the immutable
 * serving DB) DYNAMICALLY from R2 at container STARTUP. The only gap is that a
 * long-lived (hot) container keeps the old generation until it is restarted.
 *
 * So the smallest correct fix (and the one the task's "dynamic R2" alternative
 * describes) is: a cron that watches the lake Serving CURRENT, and — when it
 * advances beyond the hosted generation we last verified — restarts the
 * container EXACTLY ONCE, then verifies the hosted /health actually reports the
 * new generation before claiming HOSTED_FRESH.
 *
 * This is NOT a Pages deploy: there is no build, no hook, no artifact. Data
 * publication is a R2 pointer move + a container restart. `pages_deployment_id`
 * is therefore null by construction; the promotion is a container restart.
 *
 * Invariants (proven by tests in tests/hosted-promotion.test.ts):
 *   1. same Serving generation → no duplicate restart (deduped).
 *   2. new Serving generation → one promotion (one restart).
 *   3. restart failure → Serving CURRENT remains valid; retry possible.
 *   4. late/older completion → not claimed HOSTED_FRESH unless hosted /health
 *      actually reports the target generation.
 *   5. PASS (HOSTED_FRESH) is based on hosted /health generation, not merely
 *      "restart requested".
 */

export type PromotionStatus = "IDLE" | "IN_FLIGHT" | "HOSTED_FRESH" | "FAILED";

export interface HostedPromotionState {
  // The lake Serving generation we are promoting.
  serving_generation: string | null;
  // The generation we requested a restart for (dedupe key).
  pages_trigger_generation: string | null;
  pages_triggered_at: string | null;
  // Container restarts have no Pages deployment id; null by construction.
  pages_deployment_id: string | null;
  pages_status: PromotionStatus;
  // Last VERIFIED hosted generation (from hosted /health).
  hosted_generation: string | null;
  hosted_verified_at: string | null;
  failure_count: number;
  retry_after: string | null;
  updated_at: string | null;
}

export interface PromotionDecision {
  action: "NONE" | "TRIGGER_RESTART" | "VERIFY" | "BACKOFF";
  reason: string;
  deduped: boolean;
}

export function emptyPromotionState(): HostedPromotionState {
  return {
    serving_generation: null,
    pages_trigger_generation: null,
    pages_triggered_at: null,
    pages_deployment_id: null,
    pages_status: "IDLE",
    hosted_generation: null,
    hosted_verified_at: null,
    failure_count: 0,
    retry_after: null,
    updated_at: null,
  };
}

// Restart backoff after a failed verification attempt (seconds). Avoids a hot
// restart loop if the container repeatedly fails to serve the new generation.
export const PROMOTION_VERIFY_BACKOFF_SECONDS = 60;
// Max consecutive verification failures before we stop auto-retrying (a human
// should look). After this, pages_status = FAILED and we stop triggering.
export const PROMOTION_MAX_FAILURES = 5;

function parseMs(v: string | null): number | null {
  if (!v) return null;
  const t = new Date(v).getTime();
  return isNaN(t) ? null : t;
}

/**
 * Pure decision: given the lake Serving generation and the persisted promotion
 * state, what should the cron do?
 *
 *   - hosted already == serving → NONE (caught up).
 *   - a promotion for this exact serving gen is already in flight → NONE (dedupe).
 *   - max failures reached for this serving gen → NONE (stopped, needs human).
 *   - in backoff → BACKOFF.
 *   - we already requested a restart for this serving gen (in flight) → VERIFY.
 *   - new serving gen beyond hosted → TRIGGER_RESTART.
 */
export function decidePromotion(
  servingGeneration: string | null,
  state: HostedPromotionState | null,
  nowIso: string,
): PromotionDecision {
  const st = state || emptyPromotionState();

  // No lake generation yet → nothing to promote.
  if (!servingGeneration) {
    return { action: "NONE", reason: "NO_SERVING_GENERATION", deduped: false };
  }

  // Caught up: hosted verified == serving.
  if (st.hosted_generation === servingGeneration) {
    return { action: "NONE", reason: "HOSTED_CAUGHT_UP", deduped: true };
  }

  // A promotion for this exact serving generation is already in flight → dedupe.
  // (Do NOT re-restart; go to VERIFY instead so we observe the outcome.)
  if (st.pages_trigger_generation === servingGeneration && st.pages_status === "IN_FLIGHT") {
    // Backoff check for the verify path.
    const rb = parseMs(st.retry_after);
    if (rb !== null && nowIso && new Date(nowIso).getTime() < rb) {
      return { action: "BACKOFF", reason: "PROMOTION_VERIFY_BACKOFF", deduped: true };
    }
    if (st.failure_count >= PROMOTION_MAX_FAILURES) {
      return { action: "NONE", reason: "PROMOTION_MAX_FAILURES_STOPPED", deduped: false };
    }
    return { action: "VERIFY", reason: "PROMOTION_IN_FLIGHT_VERIFY", deduped: true };
  }

  // We already promoted this generation and verified it was fresh, but lake has
  // since advanced — that's a NEW serving gen (handled below). If we previously
  // FAILED (max failures) for a DIFFERENT (older) serving gen, allow the new one.
  // If we FAILED for THIS serving gen, stop (needs human).
  if (st.pages_trigger_generation === servingGeneration && st.pages_status === "FAILED") {
    return { action: "NONE", reason: "PROMOTION_STOPPED_MAX_FAILURES", deduped: false };
  }

  // New serving generation beyond what hosted serves → trigger exactly one restart.
  return { action: "TRIGGER_RESTART", reason: `PROMOTION_NEW_SERVING serving=${servingGeneration} hosted=${st.hosted_generation ?? "none"}`, deduped: false };
}

// Record that we requested a restart for the given serving generation.
export function stateAfterTrigger(servingGeneration: string, nowIso: string): HostedPromotionState {
  return {
    serving_generation: servingGeneration,
    pages_trigger_generation: servingGeneration,
    pages_triggered_at: nowIso,
    pages_deployment_id: null,
    pages_status: "IN_FLIGHT",
    hosted_generation: null,
    hosted_verified_at: null,
    failure_count: 0,
    retry_after: null,
    updated_at: nowIso,
  };
}

// Record a VERIFIED hosted generation (hosted /health reported the target gen).
export function stateAfterVerified(servingGeneration: string, nowIso: string): HostedPromotionState {
  return {
    serving_generation: servingGeneration,
    pages_trigger_generation: servingGeneration,
    pages_triggered_at: null,
    pages_deployment_id: null,
    pages_status: "HOSTED_FRESH",
    hosted_generation: servingGeneration,
    hosted_verified_at: nowIso,
    failure_count: 0,
    retry_after: null,
    updated_at: nowIso,
  };
}

// Record a failed verification attempt (hosted /health did NOT report the
// target generation). Increments failure_count; sets a retry backoff.
export function stateAfterVerifyFailure(prev: HostedPromotionState, nowIso: string): HostedPromotionState {
  const failures = (prev.failure_count || 0) + 1;
  const next = {
    ...prev,
    failure_count: failures,
    retry_after: new Date(new Date(nowIso).getTime() + PROMOTION_VERIFY_BACKOFF_SECONDS * 1000).toISOString(),
    updated_at: nowIso,
    pages_status: (failures >= PROMOTION_MAX_FAILURES ? "FAILED" : "IN_FLIGHT") as PromotionStatus,
  };
  return next;
}

// ── Env contract (decoupled from the full TerminalEnv for testability) ────
export interface HostedPromotionEnv {
  LAKE_BUCKET: {
    get(key: string): Promise<{ json(): Promise<unknown> } | null>;
  };
  BACKUP_BUCKET: {
    get(key: string): Promise<{ json(): Promise<unknown> } | null>;
    put(key: string, value: string, opts?: { httpMetadata?: { contentType?: string } }): Promise<unknown>;
  };
  TERMINAL_CONTAINER: {
    idFromName(name: string): { name: string };
    get(id: { name: string }): {
      restartContainer(reason?: string): Promise<Record<string, unknown>>;
      fetch(request: Request): Promise<Response>;
    };
  };
  // Public origin of this deployment (used to build the verification request
  // whose /health reports the container's served generation).
  TERMINAL_PUBLIC_ORIGIN?: string;
  // Production access prefix (deployment secret) — forwarded as an internal
  // routing header on the verification request so a cold production container
  // bootstraps from the correct origin.
  TERMINAL_ACCESS_PATH?: string;
}

const CURRENT_KEY = "serving/artist_security_terminal_v1/CURRENT.json";
export const PROMOTION_STATE_KEY = "control/terminal/hosted_promotion/state.json";

export class HostedPromotionOrchestrator {
  constructor(private readonly env: HostedPromotionEnv) {}

  async loadState(): Promise<HostedPromotionState> {
    try {
      const obj = await this.env.BACKUP_BUCKET.get(PROMOTION_STATE_KEY);
      if (obj) {
        const d = (await obj.json()) as Partial<HostedPromotionState>;
        return { ...emptyPromotionState(), ...d } as HostedPromotionState;
      }
    } catch {
      // fall through
    }
    return emptyPromotionState();
  }

  async saveState(state: HostedPromotionState): Promise<void> {
    await this.env.BACKUP_BUCKET.put(
      PROMOTION_STATE_KEY,
      JSON.stringify(state),
      { httpMetadata: { contentType: "application/json" } },
    );
  }

  private async readServingGeneration(): Promise<string | null> {
    try {
      const obj = await this.env.LAKE_BUCKET.get(CURRENT_KEY);
      if (!obj) return null;
      const d = (await obj.json()) as { generation?: string };
      return d.generation || null;
    } catch {
      return null;
    }
  }

  // Read the generation the hosted container is ACTUALLY serving right now by
  // hitting the container DO's /health directly. The DO's startContainer uses
  // the request URL origin as BOOTSTRAP_BASE for the container, so the
  // synthetic request MUST carry the real public origin (TERMINAL_PUBLIC_ORIGIN)
  // plus the production access prefix (X-Terminal-Access-Prefix) when set —
  // otherwise a cold container would bootstrap from a bogus origin and fail.
  // For a WARM container the origin is unused; /health is proxied directly.
  private async readHostedGeneration(): Promise<string | null> {
    try {
      const origin = this.env.TERMINAL_PUBLIC_ORIGIN || "http://localhost";
      const doId = this.env.TERMINAL_CONTAINER.idFromName("terminal");
      const doStub = this.env.TERMINAL_CONTAINER.get(doId);
      const headers = new Headers();
      const accessPath = this.env.TERMINAL_ACCESS_PATH;
      if (accessPath) headers.set("X-Terminal-Access-Prefix", String(accessPath));
      // With an access prefix, the DO strips `configuredPath` from the pathname,
      // so the request path must BE `${accessPath}/health` for the logical path
      // to resolve to /health.
      const healthPath = accessPath ? `${accessPath}/health` : "/health";
      const resp = await doStub.fetch(new Request(`${origin}${healthPath}`, { headers }));
      if (!resp.ok) return null;
      const d = (await resp.json()) as { generation?: string };
      return d.generation || null;
    } catch {
      return null;
    }
  }

  async runTick(nowIso: string = new Date().toISOString()): Promise<{
    decision: PromotionDecision;
    stateAfter: HostedPromotionState;
    triggered: boolean;
    verifiedHostedGeneration: string | null;
  }> {
    const servingGeneration = await this.readServingGeneration();
    const state = await this.loadState();
    const decision = decidePromotion(servingGeneration, state, nowIso);

    let stateAfter = state;
    let triggered = false;
    let verifiedHostedGeneration: string | null = state.hosted_generation;

    if (decision.action === "TRIGGER_RESTART") {
      try {
        const doId = this.env.TERMINAL_CONTAINER.idFromName("terminal");
        const doStub = this.env.TERMINAL_CONTAINER.get(doId);
        await doStub.restartContainer("serving-generation-promotion");
        triggered = true;
        stateAfter = stateAfterTrigger(servingGeneration as string, nowIso);
        await this.saveState(stateAfter);
      } catch (e: unknown) {
        // Restart failure: Serving CURRENT remains valid (we never mutate it).
        // Record a failure so a later tick can retry.
        stateAfter = stateAfterVerifyFailure(state, nowIso);
        await this.saveState(stateAfter);
        console.error(JSON.stringify({ event: "HOSTED_PROMOTION_RESTART_ERROR", error: e instanceof Error ? e.message : String(e) }));
      }
    } else if (decision.action === "VERIFY") {
      const hostedGen = await this.readHostedGeneration();
      verifiedHostedGeneration = hostedGen;
      if (hostedGen && servingGeneration && hostedGen === servingGeneration) {
        stateAfter = stateAfterVerified(servingGeneration, nowIso);
        await this.saveState(stateAfter);
      } else {
        stateAfter = stateAfterVerifyFailure(state, nowIso);
        await this.saveState(stateAfter);
      }
    }

    return { decision, stateAfter, triggered, verifiedHostedGeneration };
  }
}
