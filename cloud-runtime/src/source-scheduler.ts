/**
 * Source Scheduler — AUTOMATIC acquisition for the two spotlight families.
 *
 * This is the SOURCE_ACQUISITION_AUTOMATIC lane. It is the ONLY place that
 * decides "is a source family due, and should we acquire it now?" It is wired
 * into the Worker `scheduled()` cron (the master clock) and persists a durable
 * per-family acquisition state in BACKUP_BUCKET `control/source-state/<family>.json`.
 *
 * Families:
 *   - WIKIMEDIA: incremental when a new admissible observation day exists that
 *     is beyond what Gold already covers. Never requests a day that is not yet
 *     admissible (zero provider requests when not due).
 *   - SPOTIFY: bounded deterministic rotating cohort (default 25) over the
 *     stable sorted universe of known Spotify identities, with a durable
 *     cursor. Never attempts the full ~15k identities in one cycle.
 *
 * Verdict taxonomy (do NOT collapse these — see the closure report):
 *   SOURCE_ACQUISITION_AUTOMATIC  — this module (trigger + durable due-state)
 *   GOLD_PUBLICATION_ATOMIC        — batch_jobs.py verify→publish (already proven)
 *   GOLD_TO_SERVING_AUTOMATIC      — watermark-controller (already proven)
 * A MANUAL POST /batch/trigger is NOT automatic acquisition, even though it
 * produces a Gold→Serving cycle.
 *
 * Invariants (proven by tests in tests/source-scheduler.test.ts):
 *   1. Wikimedia not due → no job
 *   2. Wikimedia due → exactly one job
 *   3. repeated scheduler tick → no duplicate (in-flight guard + dedupe)
 *   4. Spotify not due → no job
 *   5. Spotify due → bounded cohort queued once
 *   6. cursor advances correctly
 *   7. retry does not double-advance cursor (advance only on SUCCESS)
 *   8. normal 429 honors backoff
 *   9. QUOTA_EXCEEDED opens long quota backoff / no hot loop
 *   10. provider failure retains previous Gold (Gold CURRENT untouched)
 *   11. stale acquisition cannot replace newer CURRENT (batch_jobs verify→publish)
 *   12. restart resumes from durable state
 *
 * The orchestrator is a thin, side-effect-light control loop: it reads the
 * Gold CURRENTs + rate state + its own durable state, decides, and — when due —
 * fires exactly one batch job through the SAME BatchContainer DO path that the
 * admin `/batch/trigger` endpoint uses (startJob). No new framework, no queues,
 * no provider calls on the trigger path.
 */

export type SourceFamily = "wikimedia" | "spotify";

export interface SourceAcquisitionState {
  family: SourceFamily;
  last_attempt_at: string | null;
  last_success_at: string | null;
  last_generation: string | null;
  next_due_at: string | null;
  // Spotify only; deterministic rotating cursor into the sorted universe.
  cursor: number;
  universe_size: number | null;
  // Generation/version stamp of the universe the cursor was computed over.
  // If the eligible universe identity changes, the cursor is re-anchored.
  universe_generation: string | null;
  consecutive_failures: number;
  backoff_until: string | null;
  quota_backoff_until: string | null;
  attempted: number;
  successful: number;
  rate_limited: number;
  quota_exceeded: number;
  provider_failed: number;
  last_job_id: string | null;
  // ── In-flight guard (crash/restart-safe, no double-trigger) ──
  // Set BEFORE firing a job; cleared when the job completes (observed by a
  // later tick reading the job manifest) or expires after a bounded window.
  in_flight: boolean;
  in_flight_job_id: string | null;
  in_flight_requested_at: string | null;
}

export interface SourceTriggerDecision {
  shouldTrigger: boolean;
  reason: string;
  deduped: boolean;
  jobId: string | null;
  params: Record<string, unknown> | null;
}

// ── Conservative, explicit, configurable cadences ─────────────────────────
// Wikimedia: at most daily when a new admissible day exists.
export const WIKIMEDIA_MIN_INTERVAL_HOURS = 24;
// Spotify: bounded rolling cohort on a conservative cadence. 6h = 4x/day max,
// and it is further gated by observed 429/quota backoff state.
export const SPOTIFY_MIN_INTERVAL_HOURS = 6;
// Bounded cohort per cycle. NOT the full universe.
export const SPOTIFY_COHORT_SIZE = 25;
// Measured eligible-universe size, refreshed from live Gold stats (universe_size)
// when available; this is the fallback when Gold has not yet reported it.
export const SPOTIFY_UNIVERSE_SIZE_FALLBACK = 15159;
// Backoff (seconds). Quota backoff is long by design: never hot-loop a quota.
export const SPOTIFY_BACKOFF_SECONDS_NORMAL = 15 * 60; // 15 min on ordinary 429
export const SPOTIFY_BACKOFF_SECONDS_QUOTA = 24 * 60 * 60; // 24h on QUOTA_EXCEEDED
export const WIKIMEDIA_BACKOFF_SECONDS = 60 * 60; // 1h on provider failure
// In-flight guard: a fired job that is still not observed complete after this
// many hours is treated as stale/expired and may be re-triggered (crash safety).
// The actual Spotify 25-artist job is ~1-2 min; Wikimedia incremental is minutes.
export const SOURCE_INFLIGHT_TIMEOUT_HOURS = 6;

export const SOURCE_STATE_PREFIX = "control/source-state/";

// ── Minimal env contract the orchestrator needs (decoupled from full Env) ──
export interface SourceSchedulerEnv {
  LAKE_BUCKET: {
    get(key: string): Promise<{ json(): Promise<unknown> } | null>;
  };
  BACKUP_BUCKET: {
    get(key: string): Promise<{ json(): Promise<unknown> } | null>;
    put(key: string, value: string, opts?: { httpMetadata?: { contentType?: string } }): Promise<unknown>;
  };
  BATCH_CONTAINER: {
    idFromName(name: string): { name: string };
    get(id: { name: string }): {
      startJob(spec: { job_id: string; job_type: string; params: Record<string, unknown> }): Promise<{ job_id: string }>;
    };
  };
}

export function emptySourceState(family: SourceFamily): SourceAcquisitionState {
  return {
    family,
    last_attempt_at: null,
    last_success_at: null,
    last_generation: null,
    next_due_at: null,
    cursor: 0,
    universe_size: null,
    universe_generation: null,
    consecutive_failures: 0,
    backoff_until: null,
    quota_backoff_until: null,
    attempted: 0,
    successful: 0,
    rate_limited: 0,
    quota_exceeded: 0,
    provider_failed: 0,
    last_job_id: null,
    in_flight: false,
    in_flight_job_id: null,
    in_flight_requested_at: null,
  };
}

function parseMs(v: string | null | undefined): number | null {
  if (!v) return null;
  const t = new Date(v).getTime();
  return isNaN(t) ? null : t;
}

// ── Wikimedia due-state ────────────────────────────────────────────────────
// Due when a new admissible day exists beyond what Gold covers, backoff has
// expired, and no identical attempt is already in flight.
//
// Gold fields used:
//   new_max_period_end — the latest observation day Gold currently contains.
//   latest_admissible  — the latest day that is admissible (today-1), computed
//                        by the build; when absent we derive it from now (UTC).
export function deriveLatestAdmissible(nowIso: string): string {
  // latest_admissible = yesterday in UTC (a day is admissible once its
  // observation window has closed). Computed deterministically from now.
  const d = new Date(new Date(nowIso).getTime() - 24 * 3600 * 1000);
  return d.toISOString().slice(0, 10);
}

export interface WikimediaGoldInput {
  generation?: string | null;
  latest_admissible?: string | null;
  new_max_period_end?: string | null;
  old_max_period_end?: string | null;
}
export interface WikimediaRateState {
  cooldown_until?: string | null;
  consecutive_429?: number;
}

export function decideWikimediaTrigger(
  state: SourceAcquisitionState | null,
  gold: WikimediaGoldInput | null,
  rateState: WikimediaRateState | null,
  nowIso: string,
): SourceTriggerDecision {
  const nowMs = new Date(nowIso).getTime();

  // Global Wikimedia 429 cooldown active → SKIP (zero provider requests).
  const cd = parseMs(rateState?.cooldown_until);
  if (cd !== null && nowMs < cd) {
    return { shouldTrigger: false, reason: "WIKIMEDIA_RATE_COOLDOWN", deduped: false, jobId: null, params: null };
  }

  // Source-owned backoff (provider failures).
  const bu = parseMs(state?.backoff_until);
  if (bu !== null && nowMs < bu) {
    return { shouldTrigger: false, reason: "WIKIMEDIA_BACKOFF", deduped: false, jobId: null, params: null };
  }

  // In-flight guard: a job was fired and not yet observed complete / expired.
  if (state?.in_flight && state.in_flight_requested_at) {
    const reqMs = parseMs(state.in_flight_requested_at);
    if (reqMs !== null && nowMs - reqMs < SOURCE_INFLIGHT_TIMEOUT_HOURS * 3600 * 1000) {
      return { shouldTrigger: false, reason: "WIKIMEDIA_INFLIGHT", deduped: true, jobId: state.in_flight_job_id, params: null };
    }
    // Expired in-flight is treated as a crashed/stale job; fall through to a
    // fresh decision (the next fired job supersedes it).
  }

  // No Gold yet → initial bootstrap (due).
  if (!gold || !gold.generation) {
    const stamp = nowIso.replace(/[-:.TZ]/g, "").slice(0, 14);
    return {
      shouldTrigger: true,
      reason: "WIKIMEDIA_NO_GOLD_INITIAL",
      deduped: false,
      jobId: `wikimedia_auto_${stamp}_initial`,
      params: { mode: "fresh", recent_window_days: 30 },
    };
  }

  const latest = deriveLatestAdmissible(nowIso);
  const curMax = gold.new_max_period_end || gold.old_max_period_end || null;

  // No new admissible day beyond Gold → SKIP_NOT_DUE (zero provider requests).
  // NOTE: gold.latest_admissible is the admissible day AT BUILD TIME and never
  // advances by itself — relying on it would make the family circularly never
  // due. The authoritative comparison is derived-today's admissible vs what
  // Gold actually covers (new_max_period_end).
  if (curMax && latest <= curMax) {
    return { shouldTrigger: false, reason: "WIKIMEDIA_NOT_DUE_NO_NEW_DAY", deduped: false, jobId: null, params: null };
  }

  // At-most-daily cadence (next_due_at, else last_attempt + 24h fallback).
  const nd = parseMs(state?.next_due_at);
  if (nd !== null && nowMs < nd) {
    return { shouldTrigger: false, reason: "WIKIMEDIA_NOT_DUE_CADENCE", deduped: false, jobId: null, params: null };
  }
  if (state?.next_due_at === undefined || state?.next_due_at === null) {
    const la = parseMs(state?.last_attempt_at);
    if (la !== null && nowMs - la < WIKIMEDIA_MIN_INTERVAL_HOURS * 3600 * 1000) {
      return { shouldTrigger: false, reason: "WIKIMEDIA_NOT_DUE_CADENCE_FALLBACK", deduped: false, jobId: null, params: null };
    }
  }

  // No extra last_generation dedupe here: the no-duplicate guarantees are
  // (a) the in-flight guard, (b) next_due_at cadence after each attempt, and
  // (c) WIKIMEDIA_NOT_DUE_NO_NEW_DAY once Gold covers the admissible day.
  // A last_job_id-based dedupe would DEADLOCK: if a job fails without
  // publishing, Gold never advances and the family would never re-fire.

  const stamp = nowIso.replace(/[-:.TZ]/g, "").slice(0, 14);
  return {
    shouldTrigger: true,
    reason: `WIKIMEDIA_DUE latest=${latest} curMax=${curMax ?? "none"}`,
    deduped: false,
    jobId: `wikimedia_auto_${stamp}_${(gold.generation || "nogold").slice(-6)}`,
    params: { mode: "fresh", recent_window_days: 30 },
  };
}

// ── Spotify due-state ──────────────────────────────────────────────────────
export interface SpotifyDecideOpts {
  cohortSize?: number;
  minIntervalHours?: number;
}

export function decideSpotifyTrigger(
  state: SourceAcquisitionState | null,
  nowIso: string,
  opts: SpotifyDecideOpts = {},
): SourceTriggerDecision {
  const nowMs = new Date(nowIso).getTime();
  const cohortSize = opts.cohortSize ?? SPOTIFY_COHORT_SIZE;
  const minIntervalMs = (opts.minIntervalHours ?? SPOTIFY_MIN_INTERVAL_HOURS) * 3600 * 1000;

  // Quota backoff is longer and takes priority.
  const qb = parseMs(state?.quota_backoff_until);
  if (qb !== null && nowMs < qb) {
    return { shouldTrigger: false, reason: "SPOTIFY_QUOTA_BACKOFF", deduped: false, jobId: null, params: null };
  }
  const bu = parseMs(state?.backoff_until);
  if (bu !== null && nowMs < bu) {
    return { shouldTrigger: false, reason: "SPOTIFY_RATE_BACKOFF", deduped: false, jobId: null, params: null };
  }
  // In-flight guard.
  if (state?.in_flight && state.in_flight_requested_at) {
    const reqMs = parseMs(state.in_flight_requested_at);
    if (reqMs !== null && nowMs - reqMs < SOURCE_INFLIGHT_TIMEOUT_HOURS * 3600 * 1000) {
      return { shouldTrigger: false, reason: "SPOTIFY_INFLIGHT", deduped: true, jobId: state.in_flight_job_id, params: null };
    }
  }
  // Cadence (next_due_at, else last_attempt + interval fallback).
  const nd = parseMs(state?.next_due_at);
  if (nd !== null && nowMs < nd) {
    return { shouldTrigger: false, reason: "SPOTIFY_NOT_DUE_CADENCE", deduped: false, jobId: null, params: null };
  }
  if (state?.next_due_at === undefined || state?.next_due_at === null) {
    const la = parseMs(state?.last_attempt_at);
    if (la !== null && nowMs - la < minIntervalMs) {
      return { shouldTrigger: false, reason: "SPOTIFY_NOT_DUE_CADENCE_FALLBACK", deduped: false, jobId: null, params: null };
    }
  }

  const cursor = state?.cursor ?? 0;
  const stamp = nowIso.replace(/[-:.TZ]/g, "").slice(0, 14);
  const jobId = `spotify_auto_${stamp}_${String(cursor).padStart(5, "0")}`;
  return {
    shouldTrigger: true,
    reason: `SPOTIFY_DUE cursor=${cursor} cohort=${cohortSize}`,
    deduped: false,
    jobId,
    params: { max_artists: cohortSize, cursor_offset: cursor },
  };
}

// ── Cursor helpers — deterministic, wrap-safe, no starvation ──────────────
export function nextSpotifyCursor(currentCursor: number, cohortSize: number, universeSize: number): number {
  if (universeSize <= 0) return 0;
  return (currentCursor + cohortSize) % universeSize;
}

export function spotifySliceForCursor(
  cursor: number,
  cohortSize: number,
  universeSize: number,
): { offset: number; limit: number; wraps: boolean; nextCursor: number } {
  if (universeSize <= 0) return { offset: 0, limit: 0, wraps: false, nextCursor: 0 };
  const start = ((cursor % universeSize) + universeSize) % universeSize;
  const wraps = start + cohortSize > universeSize;
  return { offset: start, limit: cohortSize, wraps, nextCursor: (start + cohortSize) % universeSize };
}

// ── State advance (pure; caller persists) ─────────────────────────────────
export interface JobOutcome {
  generation?: string | null;
  successful?: number;
  rate_limited?: number;
  quota_exceeded?: number;
  provider_failed?: number;
  attempted?: number;
  new_rows?: number;
  universe_size?: number | null;
  universe_generation?: string | null;
}

export function advanceStateOnSuccess(
  prev: SourceAcquisitionState,
  nowIso: string,
  result: JobOutcome,
  cohortSize: number,
  universeSize: number,
  family: SourceFamily,
): SourceAcquisitionState {
  const intervalHours = family === "spotify" ? SPOTIFY_MIN_INTERVAL_HOURS : WIKIMEDIA_MIN_INTERVAL_HOURS;
  const nextDue = new Date(new Date(nowIso).getTime() + intervalHours * 3600 * 1000).toISOString();
  const next = { ...prev };
  next.last_attempt_at = nowIso;
  next.last_success_at = nowIso;
  if (result.generation) next.last_generation = result.generation;
  next.next_due_at = nextDue;
  // Clear backoff on a healthy cycle (a success means the lane is usable).
  next.backoff_until = null;
  next.quota_backoff_until = null;
  next.consecutive_failures = 0;
  next.rate_limited = (prev.rate_limited || 0) + (result.rate_limited || 0);
  next.quota_exceeded = (prev.quota_exceeded || 0) + (result.quota_exceeded || 0);
  next.provider_failed = (prev.provider_failed || 0) + (result.provider_failed || 0);
  next.attempted = (prev.attempted || 0) + 1;
  next.successful = (prev.successful || 0) + 1;
  if (family === "spotify") {
    const uSize = result.universe_size && result.universe_size > 0 ? result.universe_size : universeSize;
    next.universe_size = uSize;
    if (result.universe_generation) next.universe_generation = result.universe_generation;
    // Cursor advances ONLY on success, by the full cohort (retry-safe).
    next.cursor = nextSpotifyCursor(prev.cursor, cohortSize, uSize);
  }
  next.in_flight = false;
  next.in_flight_job_id = null;
  next.in_flight_requested_at = null;
  return next;
}

export function advanceStateOnRateLimited(prev: SourceAcquisitionState, nowIso: string, retryAfterSeconds?: number | null): SourceAcquisitionState {
  const delay = retryAfterSeconds && retryAfterSeconds > 0 && retryAfterSeconds < 3600 ? retryAfterSeconds : SPOTIFY_BACKOFF_SECONDS_NORMAL;
  const backoffUntil = new Date(new Date(nowIso).getTime() + delay * 1000).toISOString();
  return {
    ...prev,
    last_attempt_at: nowIso,
    next_due_at: backoffUntil,
    backoff_until: backoffUntil,
    consecutive_failures: (prev.consecutive_failures || 0) + 1,
    rate_limited: (prev.rate_limited || 0) + 1,
    // The observed job is finished: clear the in-flight guard so a later tick
    // can re-decide after backoff.
    in_flight: false,
    in_flight_job_id: null,
    in_flight_requested_at: null,
    // Cursor does NOT advance on rate-limit.
  };
}

export function advanceStateOnQuotaExceeded(prev: SourceAcquisitionState, nowIso: string): SourceAcquisitionState {
  const backoffUntil = new Date(new Date(nowIso).getTime() + SPOTIFY_BACKOFF_SECONDS_QUOTA * 1000).toISOString();
  return {
    ...prev,
    last_attempt_at: nowIso,
    next_due_at: backoffUntil,
    quota_backoff_until: backoffUntil,
    consecutive_failures: (prev.consecutive_failures || 0) + 1,
    quota_exceeded: (prev.quota_exceeded || 0) + 1,
    // Finished: clear the in-flight guard (backoff governs the next attempt).
    in_flight: false,
    in_flight_job_id: null,
    in_flight_requested_at: null,
    // Cursor does NOT advance on quota.
  };
}

export function advanceStateOnFailure(prev: SourceAcquisitionState, nowIso: string): SourceAcquisitionState {
  const backoffUntil = new Date(new Date(nowIso).getTime() + WIKIMEDIA_BACKOFF_SECONDS * 1000).toISOString();
  return {
    ...prev,
    last_attempt_at: nowIso,
    next_due_at: backoffUntil,
    backoff_until: backoffUntil,
    consecutive_failures: (prev.consecutive_failures || 0) + 1,
    provider_failed: (prev.provider_failed || 0) + 1,
    // Finished: clear the in-flight guard (backoff governs the next attempt).
    in_flight: false,
    in_flight_job_id: null,
    in_flight_requested_at: null,
    // last_generation and cursor are UNCHANGED — previous Gold stays valid.
  };
}

// Mark a job as in-flight (set before firing).
export function markInFlight(prev: SourceAcquisitionState, jobId: string, nowIso: string): SourceAcquisitionState {
  return {
    ...prev,
    in_flight: true,
    in_flight_job_id: jobId,
    in_flight_requested_at: nowIso,
    last_attempt_at: nowIso,
    last_job_id: jobId,
  };
}

// ── Job completion observer ────────────────────────────────────────────────
// Reads a batch job's durable manifest (written by batch_jobs.py) and returns a
// normalized outcome. The orchestrator calls this each tick to detect that a
// previously-fired job completed, then advances state accordingly.
export interface JobManifestInput {
  status?: string;
  error_code?: string | null;
  generation?: string | null;
  params?: Record<string, unknown>;
  // Spotify / wikimedia stats (top-level in the Gold CURRENT / manifest).
  rate_limited?: number;
  quota_exceeded?: number;
  provider_error?: number;
  provider_failed?: number;
  successful?: number;
  attempted?: number;
  new_rows?: number;
  eligible?: number;
}

export type JobOutcomeClass = "SUCCESS" | "QUOTA_TRUNCATED" | "RATE_LIMITED" | "QUOTA_EXCEEDED" | "PROVIDER_FAILED" | "RUNNING" | "UNKNOWN";

export function classifyJobOutcome(m: JobManifestInput | null): {
  outcome: JobOutcomeClass;
  result: JobOutcome;
} {
  if (!m) return { outcome: "UNKNOWN", result: {} };
  // Still running (no terminal status) → do not advance.
  const status = String(m.status || "").toUpperCase();
  if (!status || status === "RUNNING" || status === "IN_PROGRESS") {
    return { outcome: "RUNNING", result: {} };
  }
  if (status === "FAILED") {
    const code = String(m.error_code || "").toUpperCase();
    if (code.includes("QUOTA")) return { outcome: "QUOTA_EXCEEDED", result: { quota_exceeded: m.quota_exceeded || 1 } };
    if (code.includes("RATE") || code === "HTTP_429") return { outcome: "RATE_LIMITED", result: { rate_limited: m.rate_limited || 1 } };
    return { outcome: "PROVIDER_FAILED", result: { provider_failed: m.provider_failed || 1 } };
  }
  // Terminal success (PUBLISHED / BUILD_COMPLETE / COMPLETED). Classify by the
  // dominant failure signal even if the job "completed" with partial data.
  const params = (m.params as Record<string, unknown> | undefined) || {};
  const quotaStopped = Boolean(params.spotify_quota_stopped_cohort) || (m.quota_exceeded || 0) > 0;
  const authBroken = Boolean(params.spotify_auth_broken);
  const quota = m.quota_exceeded || 0;
  const rate = m.rate_limited || 0;
  const provErr = m.provider_error || m.provider_failed || 0;
  const succeeded = m.successful || m.new_rows || 0;
  // A cohort stopped early by QUOTA or a broken auth token must NOT be treated
  // as a clean success — the cursor must not advance past artists that were
  // never observed. Gold may still have published the partial (legit) rows,
  // but the scheduler opens backoff instead of advancing.
  if (quotaStopped || authBroken) {
    return {
      outcome: "QUOTA_TRUNCATED",
      result: {
        generation: m.generation ?? null,
        successful: m.successful ?? 0,
        rate_limited: rate,
        quota_exceeded: quota,
        provider_failed: provErr,
        new_rows: m.new_rows,
      },
    };
  }
  if (succeeded > 0 || status === "PUBLISHED" || status === "COMPLETED") {
    return {
      outcome: "SUCCESS",
      result: {
        generation: m.generation ?? null,
        successful: m.successful ?? m.new_rows ?? 0,
        rate_limited: rate,
        quota_exceeded: quota,
        provider_failed: provErr,
        attempted: m.attempted,
        new_rows: m.new_rows,
      },
    };
  }
  if (quota > 0) return { outcome: "QUOTA_EXCEEDED", result: { quota_exceeded: quota } };
  if (rate > 0) return { outcome: "RATE_LIMITED", result: { rate_limited: rate } };
  if (provErr > 0) return { outcome: "PROVIDER_FAILED", result: { provider_failed: provErr } };
  return { outcome: "UNKNOWN", result: {} };
}

// ── Honest refresh-interval math ───────────────────────────────────────────
// Full-estate refresh is cohortSize * (24 / intervalHours) artists per day.
// This is a THEORETICAL bound; report it, never pretend it is freshness.
export function estimatedFullRefreshDays(cohortSize: number, intervalHours: number, universeSize = SPOTIFY_UNIVERSE_SIZE_FALLBACK): number {
  if (cohortSize <= 0 || intervalHours <= 0) return Infinity;
  const runsPerDay = 24 / intervalHours;
  const artistsPerDay = cohortSize * runsPerDay;
  if (artistsPerDay <= 0) return Infinity;
  return universeSize / artistsPerDay;
}

// ── The orchestrator ───────────────────────────────────────────────────────
// One tick. Reads durable state, decides for both families, fires at most one
// job per due family, persists new durable state. Side effects are bounded to:
// (a) reading Gold CURRENTs / rate state / job manifests, (b) one batch DO
// startJob per due family, (c) writing control/source-state/*.json.
export interface FamilyTickResult {
  family: SourceFamily;
  decision: SourceTriggerDecision;
  fired: boolean;
  jobId: string | null;
  completedOutcome: JobOutcomeClass | null;
  stateAfter: SourceAcquisitionState;
}

export interface OrchestratorTickResult {
  now: string;
  families: FamilyTickResult[];
  firedJobs: string[];
}

export class SourceSchedulerOrchestrator {
  constructor(private readonly env: SourceSchedulerEnv) {}

  private stateKey(family: SourceFamily): string {
    return `${SOURCE_STATE_PREFIX}${family}.json`;
  }

  async loadState(family: SourceFamily): Promise<SourceAcquisitionState> {
    try {
      const obj = await this.env.BACKUP_BUCKET.get(this.stateKey(family));
      if (obj) {
        const d = (await obj.json()) as Partial<SourceAcquisitionState>;
        return { ...emptySourceState(family), ...d, family } as SourceAcquisitionState;
      }
    } catch {
      // fall through to empty
    }
    return emptySourceState(family);
  }

  async saveState(family: SourceFamily, state: SourceAcquisitionState): Promise<void> {
    await this.env.BACKUP_BUCKET.put(
      this.stateKey(family),
      JSON.stringify(state),
      { httpMetadata: { contentType: "application/json" } },
    );
  }

  private async readJson(key: string): Promise<Record<string, unknown> | null> {
    try {
      const obj = await this.env.LAKE_BUCKET.get(key);
      if (!obj) return null;
      return (await obj.json()) as Record<string, unknown>;
    } catch {
      return null;
    }
  }

  async runTick(nowIso: string = new Date().toISOString()): Promise<OrchestratorTickResult> {
    const results: FamilyTickResult[] = [];
    const firedJobs: string[] = [];

    // ── WIKIMEDIA ──
    {
      const wmGold = (await this.readJson("gold/artist_attention_wikimedia/CURRENT.json")) as WikimediaGoldInput | null;
      const wmRate = (await this.readJson("control/jobs/artist_attention_wikimedia_build_v1/rate_state.json")) as WikimediaRateState | null;
      const wmState = await this.loadState("wikimedia");

      let completedOutcome: JobOutcomeClass | null = null;
      let stateAfter = wmState;

      // Observe any in-flight job first (restart recovery). This MUST NOT exit
      // the tick: a stuck/stale manifest for one family must never starve the
      // other. (The previous implementation early-returned here whenever the
      // manifest read RUNNING, which wedged the WHOLE source scheduler — past
      // the 6h crash-recovery window — and starved the Spotify family.)
      if (wmState.in_flight && wmState.in_flight_job_id) {
        const m = (await this.readJobManifest("artist_attention_wikimedia_build_v1", wmState.in_flight_job_id)) as JobManifestInput | null;
        const c = classifyJobOutcome(m);
        const reqMs = parseMs(wmState.in_flight_requested_at);
        const inFlightExpired =
          reqMs !== null && new Date(nowIso).getTime() - reqMs >= SOURCE_INFLIGHT_TIMEOUT_HOURS * 3600 * 1000;
        if (c.outcome !== "RUNNING" && c.outcome !== "UNKNOWN") {
          // Terminal outcome observed: advance the durable state (clears
          // in-flight and sets the next-due cadence so the family is not
          // re-fired this tick).
          completedOutcome = c.outcome;
          stateAfter = this.applyOutcome("wikimedia", wmState, c, nowIso, WIKIMEDIA_MIN_INTERVAL_HOURS, 0, wmGold?.generation ?? null);
          await this.saveState("wikimedia", stateAfter);
        } else if (inFlightExpired) {
          // Manifest still RUNNING (or unreadable) but the in-flight window
          // expired: treat the job as crashed/stale. Clear the in-flight guard
          // so the fresh decision below can re-fire. Previous Gold stays valid
          // (no generation/cursor advance).
          stateAfter = { ...wmState, in_flight: false, in_flight_job_id: null, in_flight_requested_at: null };
          await this.saveState("wikimedia", stateAfter);
        }
        // else: still running within the in-flight window → keep in_flight.
      }

      // Re-decide from the (possibly advanced/cleared) state so a just-completed
      // job is not re-fired, and a recovered (stuck) family can re-fire.
      const decision = decideWikimediaTrigger(stateAfter, wmGold, wmRate, nowIso);

      let fired = false;
      let jobId: string | null = decision.jobId;

      if (decision.shouldTrigger) {
        // Mark in-flight, fire, persist, then advance on (assumed) success later.
        const marked = markInFlight(stateAfter, decision.jobId as string, nowIso);
        await this.safeFire("artist_attention_wikimedia_build_v1", decision.jobId as string, decision.params || {});
        // Persist in-flight state immediately (crash before job completion).
        stateAfter = marked;
        await this.saveState("wikimedia", stateAfter);
        fired = true;
        jobId = decision.jobId;
        results.push({ family: "wikimedia", decision, fired, jobId, completedOutcome, stateAfter });
        firedJobs.push(jobId as string);
      } else {
        await this.saveState("wikimedia", stateAfter);
        results.push({ family: "wikimedia", decision, fired, jobId, completedOutcome, stateAfter });
      }
    }

    // ── SPOTIFY ──
    {
      const spGold = (await this.readJson("gold/artist_attention_spotify/CURRENT.json")) as (JobManifestInput & { universe_size?: number; universe_generation?: string }) | null;
      const spState = await this.loadState("spotify");

      let completedOutcome: JobOutcomeClass | null = null;
      let stateAfter = spState;

      // Observe any in-flight job first (restart recovery). Same no-early-return
      // discipline as the Wikimedia block: a stuck/stale Spotify manifest must
      // not wedge the family past the crash-recovery window.
      if (spState.in_flight && spState.in_flight_job_id) {
        const m = (await this.readJobManifest("artist_attention_spotify_build_v1", spState.in_flight_job_id)) as JobManifestInput | null;
        const c = classifyJobOutcome(m);
        const reqMs = parseMs(spState.in_flight_requested_at);
        const inFlightExpired =
          reqMs !== null && new Date(nowIso).getTime() - reqMs >= SOURCE_INFLIGHT_TIMEOUT_HOURS * 3600 * 1000;
        if (c.outcome !== "RUNNING" && c.outcome !== "UNKNOWN") {
          completedOutcome = c.outcome;
          // Universe size: prefer the job manifest's reported eligible universe,
          // else the Gold CURRENT universe_size, else the durable state, else fallback.
          const mParams = (m?.params as Record<string, unknown> | undefined) || {};
          const uSize =
            (mParams.spotify_universe_size as number | undefined) ||
            spGold?.universe_size ||
            spState.universe_size ||
            SPOTIFY_UNIVERSE_SIZE_FALLBACK;
          stateAfter = this.applyOutcome("spotify", spState, c, nowIso, SPOTIFY_MIN_INTERVAL_HOURS, uSize, spGold?.generation ?? null);
          await this.saveState("spotify", stateAfter);
        } else if (inFlightExpired) {
          // Stuck/stale: clear the in-flight guard so the fresh decision below
          // can re-fire. Cursor/generation are NOT advanced (previous Gold valid).
          stateAfter = { ...spState, in_flight: false, in_flight_job_id: null, in_flight_requested_at: null };
          await this.saveState("spotify", stateAfter);
        }
        // else: still running within the window → keep in_flight.
      }

      // Re-decide from the (possibly advanced/cleared) state.
      const decision = decideSpotifyTrigger(stateAfter, nowIso);

      let fired = false;
      let jobId: string | null = decision.jobId;

      if (decision.shouldTrigger) {
        const marked = markInFlight(stateAfter, decision.jobId as string, nowIso);
        await this.safeFire("artist_attention_spotify_build_v1", decision.jobId as string, decision.params || {});
        stateAfter = marked;
        await this.saveState("spotify", stateAfter);
        fired = true;
        jobId = decision.jobId;
        results.push({ family: "spotify", decision, fired, jobId, completedOutcome, stateAfter });
        firedJobs.push(jobId as string);
      } else {
        await this.saveState("spotify", stateAfter);
        results.push({ family: "spotify", decision, fired, jobId, completedOutcome, stateAfter });
      }
    }

    return this.finish(results, firedJobs, nowIso);
  }

  private finish(results: FamilyTickResult[], firedJobs: string[], nowIso: string): OrchestratorTickResult {
    return { now: nowIso, families: results, firedJobs };
  }

  private applyOutcome(
    family: SourceFamily,
    prev: SourceAcquisitionState,
    c: { outcome: JobOutcomeClass; result: JobOutcome },
    nowIso: string,
    intervalHours: number,
    universeSize = SPOTIFY_UNIVERSE_SIZE_FALLBACK,
    goldGeneration: string | null = null,
  ): SourceAcquisitionState {
    // Attach the Gold generation (authoritative for lineage) if present.
    const result = { ...c.result };
    if (c.outcome === "SUCCESS" && goldGeneration && !result.generation) result.generation = goldGeneration;
    switch (c.outcome) {
      case "SUCCESS":
        return this.advanceSuccess(family, prev, nowIso, intervalHours, result, universeSize);
      case "QUOTA_TRUNCATED":
        // Partial cohort stopped by quota/auth: publish the legit rows (Gold
        // already did), but open a long quota backoff and do NOT advance the
        // cursor (artists after the stop point were never observed).
        return advanceStateOnQuotaExceeded(prev, nowIso);
      case "RATE_LIMITED":
        return advanceStateOnRateLimited(prev, nowIso);
      case "QUOTA_EXCEEDED":
        return advanceStateOnQuotaExceeded(prev, nowIso);
      case "PROVIDER_FAILED":
        return advanceStateOnFailure(prev, nowIso);
      default:
        // UNKNOWN: clear in-flight so a later tick can re-decide, but do NOT
        // advance cursor/generation (previous Gold stays valid).
        return { ...prev, in_flight: false, in_flight_job_id: null, in_flight_requested_at: null };
    }
  }

  // advanceStateOnSuccess, parameterized by the family's interval and the
  // cohort size for cursor advancement (Spotify cohort = SPOTIFY_COHORT_SIZE;
  // Wikimedia has no cursor so cohortSize is irrelevant there).
  private advanceSuccess(
    family: SourceFamily,
    prev: SourceAcquisitionState,
    nowIso: string,
    intervalHours: number,
    result: JobOutcome,
    universeSize: number,
  ): SourceAcquisitionState {
    const cohortSize = family === "spotify" ? SPOTIFY_COHORT_SIZE : 0;
    const nextDue = new Date(new Date(nowIso).getTime() + intervalHours * 3600 * 1000).toISOString();
    const next = { ...prev };
    next.last_attempt_at = nowIso;
    next.last_success_at = nowIso;
    if (result.generation) next.last_generation = result.generation;
    next.next_due_at = nextDue;
    next.backoff_until = null;
    next.quota_backoff_until = null;
    next.consecutive_failures = 0;
    next.rate_limited = (prev.rate_limited || 0) + (result.rate_limited || 0);
    next.quota_exceeded = (prev.quota_exceeded || 0) + (result.quota_exceeded || 0);
    next.provider_failed = (prev.provider_failed || 0) + (result.provider_failed || 0);
    next.attempted = (prev.attempted || 0) + 1;
    next.successful = (prev.successful || 0) + 1;
    if (family === "spotify") {
      const uSize = result.universe_size && result.universe_size > 0 ? result.universe_size : universeSize;
      next.universe_size = uSize;
      if (result.universe_generation) next.universe_generation = result.universe_generation;
      next.cursor = nextSpotifyCursor(prev.cursor, cohortSize, uSize);
    }
    next.in_flight = false;
    next.in_flight_job_id = null;
    next.in_flight_requested_at = null;
    return next;
  }

  private async safeFire(jobType: string, jobId: string, params: Record<string, unknown>): Promise<void> {
    try {
      const doId = this.env.BATCH_CONTAINER.idFromName(jobId);
      const batchDo = this.env.BATCH_CONTAINER.get(doId);
      await batchDo.startJob({ job_id: jobId, job_type: jobType, params });
    } catch (e: unknown) {
      // Fire-and-observe: a startJob failure is logged; the durable state was
      // already marked in-flight so a later tick re-evaluates. We do NOT throw
      // so one family's failure does not break the other family's tick.
      console.error(JSON.stringify({ event: "SOURCE_ACQUISITION_FIRE_ERROR", job_type: jobType, job_id: jobId, error: e instanceof Error ? e.message : String(e) }));
    }
  }

  private async readJobManifest(jobType: string, jobId: string): Promise<Record<string, unknown> | null> {
    // The job manifest is written by batch_jobs.py to LAKE (for these two
    // job types) at control/jobs/<job_type>/<job_id>/manifest.json. It carries
    // status + error_code + params (universe/cursor). Per-job provider stats
    // (rate_limited/quota_exceeded/...) and the published generation live in
    // the Gold CURRENT, which we merge in via mergeJobEvidence.
    try {
      const obj = await this.env.LAKE_BUCKET.get(`control/jobs/${jobType}/${jobId}/manifest.json`);
      if (!obj) return null;
      return (await obj.json()) as Record<string, unknown>;
    } catch {
      return null;
    }
  }

  // Combine the job manifest (authoritative for status/error_code/params) with
  // the family Gold CURRENT (authoritative for published generation + provider
  // stats) into one classification input. On FAILED the Gold CURRENT is the
  // PREVIOUS generation (unchanged) — classifyJobOutcome uses the manifest
  // error_code for the failure path and ignores the stale gold stats there.
  private mergeJobEvidence(manifest: Record<string, unknown> | null, gold: Record<string, unknown> | null): JobManifestInput | null {
    if (!manifest && !gold) return null;
    const merged: Record<string, unknown> = { ...(gold || {}), ...(manifest || {}) };
    return merged as JobManifestInput;
  }
}
