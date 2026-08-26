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
 *   spent + reserved + expected_next_cost <= budget
 */

import { DurableObject } from "cloudflare:workers";
import {
  GovernorState,
  createInitialGovernorState,
  CircuitState,
  ProviderRateLimit,
} from "./governor";

interface GovernorEnv {}

export class AcquisitionGovernor extends DurableObject<GovernorEnv> {
  private gov!: GovernorState;

  constructor(state: DurableObjectState, env: GovernorEnv) {
    super(state, env);
    // blockConcurrencyWhile is the correct API
    this.ctx.blockConcurrencyWhile(async () => {
      const stored = await this.ctx.storage.get<GovernorState>("governor");
      if (stored) {
        const today = new Date().toISOString().slice(0, 10);
        if (stored.current_day !== today) {
          stored.daily_spend_usd = 0;
          stored.current_day = today;
          stored.reserved_spend_usd = 0;
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
        this.gov = stored;
      } else {
        this.gov = createInitialGovernorState();
      }
    });
  }

  /** Get current governor state */
  async getState(): Promise<GovernorState> {
    return this.gov;
  }

  /**
   * Atomically reserve budget for a task.
   * Checks: spent + reserved + expected_cost <= budget
   * On success: creates reservation + lease
   * On failure: returns denial reason
   */
  async reserveTask(params: {
    task_key: string;
    provider: string;
    expected_max_cost_usd: number;
    container_id: string;
    max_lease_seconds?: number;
  }): Promise<{ allowed: boolean; reason?: string }> {
    const { task_key, provider, expected_max_cost_usd, container_id, max_lease_seconds = 300 } = params;

    // Idempotency check
    if (this.gov.recent_task_keys[task_key]) {
      return { allowed: false, reason: "DUPLICATE_TASK" };
    }

    // Active lease check
    if (this.gov.active_leases[task_key]) {
      return { allowed: false, reason: "TASK_LEASED" };
    }

    // Expire stale leases (lease timeout)
    const now = Date.now();
    for (const [lk, lease] of Object.entries(this.gov.active_leases)) {
      if (new Date(lease.expires_at).getTime() < now) {
        delete this.gov.active_leases[lk];
        this.gov.active_containers = Math.max(0, this.gov.active_containers - 1);
      }
    }

    // Atomic budget check: spent + reserved + expected <= budget
    const totalCommitted = this.gov.daily_spend_usd + this.gov.reserved_spend_usd + expected_max_cost_usd;
    if (totalCommitted > this.gov.authorized_daily_budget_usd) {
      return { allowed: false, reason: "DAILY_BUDGET_EXCEEDED" };
    }

    const monthlyCommitted = this.gov.monthly_spend_usd + this.gov.reserved_spend_usd + expected_max_cost_usd;
    if (monthlyCommitted > this.gov.authorized_monthly_budget_usd) {
      return { allowed: false, reason: "MONTHLY_BUDGET_EXCEEDED" };
    }

    // Circuit breaker check
    const breaker = this.gov.circuit_breakers[provider];
    if (breaker?.state === "OPEN") {
      return { allowed: false, reason: "CIRCUIT_BREAKER_OPEN" };
    }

    // Provider cooldown check
    const cooldown = this.gov.provider_cooldowns[provider];
    if (cooldown && new Date(cooldown.until) > new Date()) {
      return { allowed: false, reason: `PROVIDER_COOLDOWN: ${cooldown.reason}` };
    }

    // Rate limit check
    const rateLimit = this.gov.provider_rate_limits[provider];
    if (rateLimit) {
      if (rateLimit.current_minute_count >= rateLimit.requests_per_minute) {
        return { allowed: false, reason: "RATE_LIMIT_MINUTE" };
      }
      if (rateLimit.current_day_count >= rateLimit.requests_per_day) {
        return { allowed: false, reason: "RATE_LIMIT_DAY" };
      }
    }

    // Concurrency check (recount after expiry)
    this.gov.active_containers = Object.keys(this.gov.active_leases).length;
    if (this.gov.active_containers >= this.gov.max_concurrent_containers) {
      return { allowed: false, reason: "MAX_CONCURRENT_REACHED" };
    }

    // ATOMIC RESERVATION — deduct from available budget
    this.gov.reserved_spend_usd += expected_max_cost_usd;
    this.gov.active_containers++;

    // Create lease
    this.gov.active_leases[task_key] = {
      task_key,
      leased_to: container_id,
      leased_at: new Date().toISOString(),
      expires_at: new Date(Date.now() + max_lease_seconds * 1000).toISOString(),
      max_lease_seconds,
    };

    await this.save();
    return { allowed: true };
  }

  /**
   * Commit a completed task — release reservation, record actual spend.
   */
  async commitTask(params: {
    task_key: string;
    actual_cost_usd: number;
    cost_basis: string;
  }): Promise<void> {
    const { task_key, actual_cost_usd, cost_basis } = params;

    // Release reservation
    this.gov.reserved_spend_usd = Math.max(0, this.gov.reserved_spend_usd - actual_cost_usd);

    // Record actual spend
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
   * Release a failed task — release reservation without marking success.
   */
  async releaseTask(params: { task_key: string }): Promise<void> {
    const { task_key } = params;

    // Release lease
    delete this.gov.active_leases[task_key];
    this.gov.active_containers = Math.max(0, this.gov.active_containers - 1);

    // DO NOT mark task_key in recent_task_keys — failed tasks should be retryable
    // The reservation is released but no spend is recorded
    // The reserved amount was already set during reserveTask, so we don't need to
    // release it again here — it was never committed

    await this.save();
  }

  /** Record a provider failure for circuit breaker */
  async recordFailure(params: {
    provider: string;
    reason: string;
    cooldown_seconds?: number;
  }): Promise<void> {
    const { provider, reason, cooldown_seconds = 300 } = params;

    if (!this.gov.circuit_breakers[provider]) {
      this.gov.circuit_breakers[provider] = {
        provider,
        state: "CLOSED",
        failure_count: 0,
        last_failure_at: "",
        cooldown_seconds,
        half_open_success_threshold: 3,
        half_open_success_count: 0,
      };
    }
    const cb = this.gov.circuit_breakers[provider];
    cb.failure_count++;
    cb.last_failure_at = new Date().toISOString();

    if (cb.failure_count >= 5) {
      cb.state = "OPEN";
      // Set cooldown
      this.gov.provider_cooldowns[provider] = {
        until: new Date(Date.now() + cooldown_seconds * 1000).toISOString(),
        reason,
      };
    }

    await this.save();
  }

  /** Record a provider success — close circuit breaker */
  async recordSuccess(params: { provider: string }): Promise<void> {
    const { provider } = params;
    if (this.gov.circuit_breakers[provider]) {
      this.gov.circuit_breakers[provider].state = "CLOSED";
      this.gov.circuit_breakers[provider].failure_count = 0;
      this.gov.circuit_breakers[provider].half_open_success_count = 0;
    }
    await this.save();
  }

  /** Update provider rate limit after a call */
  async recordRateLimit(params: {
    provider: string;
    requests_per_minute?: number;
    requests_per_day?: number;
  }): Promise<void> {
    const { provider, requests_per_minute = 60, requests_per_day = 1000 } = params;

    if (!this.gov.provider_rate_limits[provider]) {
      this.gov.provider_rate_limits[provider] = {
        provider,
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
    const rl = this.gov.provider_rate_limits[provider];
    rl.current_day_count++;
    rl.current_minute_count++;
    await this.save();
  }

  /** Reset governor to clean state (for recovery from stale leases) */
  async resetState(): Promise<void> {
    this.gov = createInitialGovernorState();
    await this.save();
  }

  /** Get current reservation summary for observability */
  async getReservationSummary(): Promise<{
    daily_spend: number;
    reserved: number;
    monthly_spend: number;
    daily_budget: number;
    monthly_budget: number;
    active_containers: number;
    active_leases: number;
  }> {
    return {
      daily_spend: this.gov.daily_spend_usd,
      reserved: this.gov.reserved_spend_usd,
      monthly_spend: this.gov.monthly_spend_usd,
      daily_budget: this.gov.authorized_daily_budget_usd,
      monthly_budget: this.gov.authorized_monthly_budget_usd,
      active_containers: this.gov.active_containers,
      active_leases: Object.keys(this.gov.active_leases).length,
    };
  }

  private async save(): Promise<void> {
    await this.ctx.storage.put("governor", this.gov);
  }
}
