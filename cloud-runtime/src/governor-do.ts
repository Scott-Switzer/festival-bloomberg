/**
 * Acquisition Governor — Cloudflare Durable Object.
 *
 * Strongly consistent budget/rate/concurrency state.
 * Controls state ONLY — no canonical evidence.
 *
 * Uses correct DurableObject base class with RPC methods.
 * Atomic reservation ledger prevents concurrent budget overruns.
 *
 * Hard invariant:
 *   spent + SUM(reservations) + expected_next_cost <= budget
 *
 * Provider ≠ marketplace:
 *   Budgets/rate limits/circuit breakers operate on acquisition_provider.
 *   Marketplace remains evidence provenance.
 *
 * Key invariant:
 *   reserveTask creates a reservation with explicit amount
 *   commitTask reads the ORIGINAL reservation, subtracts it, adds actual cost
 *   releaseTask subtracts the ORIGINAL reservation, does NOT mark success
 *   expired leases release corresponding reservations
 */

import { DurableObject } from "cloudflare:workers";
import {
  GovernorState,
  TaskReservation,
  ObservationState,
  createInitialGovernorState,
} from "./governor";

interface GovernorEnv {
  DAILY_BUDGET_USD?: string;
  MONTHLY_BUDGET_USD?: string;
}

export class AcquisitionGovernor extends DurableObject<GovernorEnv> {
  private gov!: GovernorState;

  constructor(state: DurableObjectState, env: GovernorEnv) {
    super(state, env);
    this.ctx.blockConcurrencyWhile(async () => {
      const stored = await this.ctx.storage.get<GovernorState>("governor");
      if (stored) {
        const today = new Date().toISOString().slice(0, 10);
        if (stored.current_day !== today) {
          stored.daily_spend_usd = 0;
          stored.current_day = today;
          stored.reserved_spend_usd = 0;
          stored.reservations = {};
          for (const key of Object.keys(stored.provider_rate_limits)) {
            stored.provider_rate_limits[key].current_day_count = 0;
            stored.provider_rate_limits[key].current_day_cost_usd = 0;
          }
        }
        const currentMonth = new Date().toISOString().slice(0, 7);
        if (stored.current_month !== currentMonth) {
          stored.monthly_spend_usd = 0;
          stored.current_month = currentMonth;
        }
        // Ensure new fields exist on older state
        if (!stored.reservations) stored.reservations = {};
        if (!stored.observation_state) stored.observation_state = {};
        // Apply budget overrides from env (keep existing spend/recent keys)
        if (env.DAILY_BUDGET_USD) stored.authorized_daily_budget_usd = parseFloat(env.DAILY_BUDGET_USD);
        if (env.MONTHLY_BUDGET_USD) stored.authorized_monthly_budget_usd = parseFloat(env.MONTHLY_BUDGET_USD);
        this.gov = stored;
        // Expire stale leases on startup (after this.gov is set)
        this.expireStaleLeases();
      } else {
        this.gov = createInitialGovernorState(
          env.DAILY_BUDGET_USD ? parseFloat(env.DAILY_BUDGET_USD) : 0.25,
          env.MONTHLY_BUDGET_USD ? parseFloat(env.MONTHLY_BUDGET_USD) : 7.50,
        );
      }
    });
  }

  /** Get current governor state */
  async getState(): Promise<GovernorState> {
    return this.gov;
  }

  /**
   * Atomically reserve budget for a task.
   * Creates a reservation with explicit amount.
   * Checks: spent + SUM(reservations) + expected_cost <= budget
   */
  async reserveTask(params: {
    task_key: string;
    acquisition_provider: string;
    expected_max_cost_usd: number;
    container_id: string;
    max_lease_seconds?: number;
  }): Promise<{ allowed: boolean; reason?: string }> {
    const { task_key, acquisition_provider, expected_max_cost_usd, container_id, max_lease_seconds = 300 } = params;

    // Idempotency check — already completed
    if (this.gov.recent_task_keys[task_key]) {
      return { allowed: false, reason: "DUPLICATE_TASK" };
    }

    // Already reserved
    if (this.gov.reservations[task_key]) {
      return { allowed: false, reason: "ALREADY_RESERVED" };
    }

    // Active lease check
    if (this.gov.active_leases[task_key]) {
      return { allowed: false, reason: "TASK_LEASED" };
    }

    // Expire stale leases
    this.expireStaleLeases();

    // Atomic budget check: spent + SUM(all reservations) + expected <= budget
    const totalReserved = this.totalReserved();
    const totalCommitted = this.gov.daily_spend_usd + totalReserved + expected_max_cost_usd;
    if (totalCommitted > this.gov.authorized_daily_budget_usd) {
      return { allowed: false, reason: "DAILY_BUDGET_EXCEEDED" };
    }

    const monthlyCommitted = this.gov.monthly_spend_usd + totalReserved + expected_max_cost_usd;
    if (monthlyCommitted > this.gov.authorized_monthly_budget_usd) {
      return { allowed: false, reason: "MONTHLY_BUDGET_EXCEEDED" };
    }

    // Circuit breaker check (on acquisition_provider, not marketplace)
    const breaker = this.gov.circuit_breakers[acquisition_provider];
    if (breaker?.state === "OPEN") {
      return { allowed: false, reason: "CIRCUIT_BREAKER_OPEN" };
    }

    // Provider cooldown check
    const cooldown = this.gov.provider_cooldowns[acquisition_provider];
    if (cooldown && new Date(cooldown.until) > new Date()) {
      return { allowed: false, reason: `PROVIDER_COOLDOWN: ${cooldown.reason}` };
    }

    // Rate limit check
    const rateLimit = this.gov.provider_rate_limits[acquisition_provider];
    if (rateLimit) {
      if (rateLimit.current_minute_count >= rateLimit.requests_per_minute) {
        return { allowed: false, reason: "RATE_LIMIT_MINUTE" };
      }
      if (rateLimit.current_day_count >= rateLimit.requests_per_day) {
        return { allowed: false, reason: "RATE_LIMIT_DAY" };
      }
    }

    // Concurrency check
    this.gov.active_containers = Object.keys(this.gov.active_leases).length;
    if (this.gov.active_containers >= this.gov.max_concurrent_containers) {
      return { allowed: false, reason: "MAX_CONCURRENT_REACHED" };
    }

    // ATOMIC RESERVATION — create explicit reservation with amount
    const now = new Date();
    this.gov.reservations[task_key] = {
      task_key,
      acquisition_provider,
      expected_cost_usd: expected_max_cost_usd,
      reserved_at: now.toISOString(),
      expires_at: new Date(now.getTime() + max_lease_seconds * 1000).toISOString(),
    };

    // Create lease
    this.gov.active_leases[task_key] = {
      task_key,
      leased_to: container_id,
      leased_at: now.toISOString(),
      expires_at: new Date(now.getTime() + max_lease_seconds * 1000).toISOString(),
      max_lease_seconds,
    };

    await this.save();
    return { allowed: true };
  }

  /**
   * Commit a completed task — release ORIGINAL reservation, record actual spend.
   * This is the ONLY place where spend is recorded.
   */
  async commitTask(params: {
    task_key: string;
    actual_cost_usd: number;
    cost_basis: string;
  }): Promise<void> {
    const { task_key, actual_cost_usd, cost_basis } = params;

    // Read ORIGINAL reservation before deleting it
    const reservation = this.gov.reservations[task_key];

    if (reservation) {
      // Subtract the ORIGINAL reserved amount (not the actual cost)
      this.gov.reserved_spend_usd = Math.max(
        0,
        this.gov.reserved_spend_usd - reservation.expected_cost_usd
      );
      delete this.gov.reservations[task_key];
    }

    // Record actual/accounted spend
    this.gov.daily_spend_usd += actual_cost_usd;
    this.gov.monthly_spend_usd += actual_cost_usd;

    // Mark task complete for idempotency
    this.gov.recent_task_keys[task_key] = new Date().toISOString();

    // Release lease
    delete this.gov.active_leases[task_key];
    this.gov.active_containers = Math.max(0, this.gov.active_containers - 1);

    // Prune old idempotency keys (keep last 10000)
    const keys = Object.entries(this.gov.recent_task_keys);
    if (keys.length > 10000) {
      const sorted = keys.sort((a, b) => a[1].localeCompare(b[1]));
      this.gov.recent_task_keys = Object.fromEntries(sorted.slice(-10000));
    }

    await this.save();
  }

  /**
   * Release a failed task — subtract ORIGINAL reservation, do NOT mark success.
   */
  async releaseTask(params: { task_key: string }): Promise<void> {
    const { task_key } = params;

    // Read ORIGINAL reservation before deleting it
    const reservation = this.gov.reservations[task_key];

    if (reservation) {
      // Subtract the ORIGINAL reserved amount
      this.gov.reserved_spend_usd = Math.max(
        0,
        this.gov.reserved_spend_usd - reservation.expected_cost_usd
      );
      delete this.gov.reservations[task_key];
    }

    // Release lease
    delete this.gov.active_leases[task_key];
    this.gov.active_containers = Math.max(0, this.gov.active_containers - 1);

    // DO NOT mark in recent_task_keys — failed tasks should be retryable

    await this.save();
  }

  /**
   * Record observation state — persists last successful observation.
   */
  async recordObservation(params: {
    event_key: string;
    marketplace: string;
    rail: string;
    success: boolean;
    logical_window: string;
  }): Promise<void> {
    const { event_key, marketplace, rail, success, logical_window } = params;
    const obsKey = `${event_key}|${marketplace}|${rail}`;

    if (!this.gov.observation_state[obsKey]) {
      this.gov.observation_state[obsKey] = {
        event_key,
        marketplace,
        rail,
        last_successful_observation_at: "",
        last_successful_logical_window: "",
        last_failure_at: "",
        consecutive_failures: 0,
      };
    }

    const obs = this.gov.observation_state[obsKey];
    if (success) {
      obs.last_successful_observation_at = new Date().toISOString();
      obs.last_successful_logical_window = logical_window;
      obs.consecutive_failures = 0;
    } else {
      obs.last_failure_at = new Date().toISOString();
      obs.consecutive_failures++;
    }

    await this.save();
  }

  /** Record a provider failure for circuit breaker */
  async recordFailure(params: {
    acquisition_provider: string;
    reason: string;
    cooldown_seconds?: number;
  }): Promise<void> {
    const { acquisition_provider, reason, cooldown_seconds = 300 } = params;

    if (!this.gov.circuit_breakers[acquisition_provider]) {
      this.gov.circuit_breakers[acquisition_provider] = {
        provider: acquisition_provider,
        state: "CLOSED",
        failure_count: 0,
        last_failure_at: "",
        cooldown_seconds,
        half_open_success_threshold: 3,
        half_open_success_count: 0,
      };
    }
    const cb = this.gov.circuit_breakers[acquisition_provider];
    cb.failure_count++;
    cb.last_failure_at = new Date().toISOString();

    if (cb.failure_count >= 5) {
      cb.state = "OPEN";
      this.gov.provider_cooldowns[acquisition_provider] = {
        until: new Date(Date.now() + cooldown_seconds * 1000).toISOString(),
        reason,
      };
    }

    await this.save();
  }

  /** Record a provider success — close circuit breaker */
  async recordSuccess(params: { acquisition_provider: string }): Promise<void> {
    const { acquisition_provider } = params;
    if (this.gov.circuit_breakers[acquisition_provider]) {
      this.gov.circuit_breakers[acquisition_provider].state = "CLOSED";
      this.gov.circuit_breakers[acquisition_provider].failure_count = 0;
      this.gov.circuit_breakers[acquisition_provider].half_open_success_count = 0;
    }
    await this.save();
  }

  /** Update provider rate limit after a call */
  async recordRateLimit(params: {
    acquisition_provider: string;
    requests_per_minute?: number;
    requests_per_day?: number;
  }): Promise<void> {
    const { acquisition_provider, requests_per_minute = 60, requests_per_day = 1000 } = params;

    if (!this.gov.provider_rate_limits[acquisition_provider]) {
      this.gov.provider_rate_limits[acquisition_provider] = {
        provider: acquisition_provider,
        requests_per_minute,
        requests_per_day,
        cost_per_day_usd: 5.0,
        current_minute_count: 0,
        current_day_count: 0,
        current_day_cost_usd: 0,
        minute_window_start: new Date().toISOString(),
        day_window_start: new Date().toISOString(),
      };
    }
    const rl = this.gov.provider_rate_limits[acquisition_provider];
    rl.current_day_count++;
    rl.current_minute_count++;
    await this.save();
  }

  /** Reset governor to clean state (for recovery from stale leases) */
  async resetState(): Promise<void> {
    this.gov = createInitialGovernorState(
      (this.env as any)?.DAILY_BUDGET_USD ? parseFloat((this.env as any).DAILY_BUDGET_USD) : 0.25,
      (this.env as any)?.MONTHLY_BUDGET_USD ? parseFloat((this.env as any).MONTHLY_BUDGET_USD) : 7.50,
    );
    await this.save();
  }

  /** Get current reservation summary for observability */
  async getReservationSummary(): Promise<{
    daily_spend: number;
    reserved: number;
    reserved_count: number;
    monthly_spend: number;
    daily_budget: number;
    monthly_budget: number;
    active_containers: number;
    active_leases: number;
    observation_states: number;
  }> {
    return {
      daily_spend: this.gov.daily_spend_usd,
      reserved: this.gov.reserved_spend_usd,
      reserved_count: Object.keys(this.gov.reservations).length,
      monthly_spend: this.gov.monthly_spend_usd,
      daily_budget: this.gov.authorized_daily_budget_usd,
      monthly_budget: this.gov.authorized_monthly_budget_usd,
      active_containers: this.gov.active_containers,
      active_leases: Object.keys(this.gov.active_leases).length,
      observation_states: Object.keys(this.gov.observation_state).length,
    };
  }

  /**
   * Get observation state for a specific event×marketplace×rail.
   * Used by the scheduler to determine due/not-due.
   */
  async getObservationState(params: {
    event_key: string;
    marketplace: string;
    rail: string;
  }): Promise<ObservationState | null> {
    const obsKey = `${params.event_key}|${params.marketplace}|${params.rail}`;
    return this.gov.observation_state[obsKey] || null;
  }

  /**
   * Get last scheduled window.
   */
  async getLastScheduledWindow(): Promise<string> {
    return this.gov.last_scheduled_window;
  }

  /**
   * Update last scheduled window after a workflow run.
   */
  async setLastScheduledWindow(params: { window: string }): Promise<void> {
    this.gov.last_scheduled_window = params.window;
    await this.save();
  }

  /** Expire stale leases and release their reservations */
  private expireStaleLeases(): void {
    const now = Date.now();
    for (const [taskKey, lease] of Object.entries(this.gov.active_leases)) {
      if (new Date(lease.expires_at).getTime() < now) {
        // Release the corresponding reservation
        const reservation = this.gov.reservations[taskKey];
        if (reservation) {
          this.gov.reserved_spend_usd = Math.max(
            0,
            this.gov.reserved_spend_usd - reservation.expected_cost_usd
          );
          delete this.gov.reservations[taskKey];
        }
        delete this.gov.active_leases[taskKey];
      }
    }
    this.gov.active_containers = Object.keys(this.gov.active_leases).length;
  }

  /** Calculate total reserved spend across all active reservations */
  private totalReserved(): number {
    return Object.values(this.gov.reservations).reduce(
      (sum, r) => sum + r.expected_cost_usd,
      0
    );
  }

  private async save(): Promise<void> {
    await this.ctx.storage.put("governor", this.gov);
  }
}
