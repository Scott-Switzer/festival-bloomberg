/**
 * Acquisition Governor — centralized budget/rate/concurrency state.
 *
 * Uses a Cloudflare Durable Object for strong consistency.
 * Tracks CONTROL STATE ONLY — no canonical evidence here.
 *
 * Hard invariant:
 *   spent + expected_next_cost <= authorized_budget
 *
 * must be checked BEFORE any paid network request.
 */

/** Provider-specific rate limits */
export interface ProviderRateLimit {
  provider: string;
  /** Max requests per minute */
  requests_per_minute: number;
  /** Max requests per day */
  requests_per_day: number;
  /** Max cost per day in USD */
  cost_per_day_usd: number;
  /** Current minute count */
  current_minute_count: number;
  /** Current day count */
  current_day_count: number;
  /** Current day cost */
  current_day_cost_usd: number;
  /** Minute window start */
  minute_window_start: string;
  /** Day window start */
  day_window_start: string;
}

/** Circuit breaker states */
export type CircuitState = "CLOSED" | "OPEN" | "HALF_OPEN";

export interface CircuitBreaker {
  provider: string;
  state: CircuitState;
  failure_count: number;
  last_failure_at: string;
  /** Cooldown before transitioning from OPEN to HALF_OPEN */
  cooldown_seconds: number;
  /** Successes needed in HALF_OPEN to close */
  half_open_success_threshold: number;
  half_open_success_count: number;
}

/** Task lease — prevents concurrent execution of the same task */
export interface TaskLease {
  task_key: string;
  leased_to: string; // container/worker ID
  leased_at: string;
  expires_at: string;
  /** Maximum lease duration in seconds */
  max_lease_seconds: number;
}

/**
 * The full governor state — stored in a Durable Object.
 * Immutable economic evidence stays in R2/DuckDB, never here.
 */
export interface GovernorState {
  /** Budget tracking */
  daily_spend_usd: number;
  reserved_spend_usd: number;  // atomic reservation ledger
  monthly_spend_usd: number;
  authorized_daily_budget_usd: number;
  authorized_monthly_budget_usd: number;

  /** Period tracking */
  current_day: string; // YYYY-MM-DD
  current_month: string; // YYYY-MM

  /** Per-provider rate limits */
  provider_rate_limits: Record<string, ProviderRateLimit>;

  /** Circuit breakers */
  circuit_breakers: Record<string, CircuitBreaker>;

  /** Active task leases */
  active_leases: Record<string, TaskLease>;

  /** Task dedup/idempotency */
  recent_task_keys: Record<string, string>; // task_key -> first_seen_at

  /** Last successful scheduling window */
  last_scheduled_window: string;

  /** Concurrency limits */
  max_concurrent_containers: number;
  active_containers: number;

  /** Provider cooldowns */
  provider_cooldowns: Record<string, {
    until: string;
    reason: string;
  }>;
}

/** Create initial governor state */
export function createInitialGovernorState(
  dailyBudget: number = 10.00,
  monthlyBudget: number = 200.00
): GovernorState {
  const now = new Date();
  return {
    daily_spend_usd: 0,
    reserved_spend_usd: 0,
    monthly_spend_usd: 0,
    authorized_daily_budget_usd: dailyBudget,
    authorized_monthly_budget_usd: monthlyBudget,
    current_day: now.toISOString().slice(0, 10),
    current_month: now.toISOString().slice(0, 7),
    provider_rate_limits: {},
    circuit_breakers: {},
    active_leases: {},
    recent_task_keys: {},
    last_scheduled_window: "",
    max_concurrent_containers: 3,
    active_containers: 0,
    provider_cooldowns: {},
  };
}

/**
 * Check if a task is allowed under current budget/rate constraints.
 * This is the pre-flight check that MUST happen before any paid request.
 */
export function canExecute(
  state: GovernorState,
  task_key: string,
  provider: string,
  expected_cost_usd: number
): { allowed: boolean; reason?: string } {
  // Check idempotency — same task key already completed recently
  if (state.recent_task_keys[task_key]) {
    return { allowed: false, reason: "DUPLICATE_TASK" };
  }

  // Check daily budget
  if (state.daily_spend_usd + expected_cost_usd > state.authorized_daily_budget_usd) {
    return { allowed: false, reason: "DAILY_BUDGET_EXCEEDED" };
  }

  // Check monthly budget
  if (state.monthly_spend_usd + expected_cost_usd > state.authorized_monthly_budget_usd) {
    return { allowed: false, reason: "MONTHLY_BUDGET_EXCEEDED" };
  }

  // Check circuit breaker
  const breaker = state.circuit_breakers[provider];
  if (breaker?.state === "OPEN") {
    return { allowed: false, reason: "CIRCUIT_BREAKER_OPEN" };
  }

  // Check provider cooldown
  const cooldown = state.provider_cooldowns[provider];
  if (cooldown && new Date(cooldown.until) > new Date()) {
    return { allowed: false, reason: `PROVIDER_COOLDOWN: ${cooldown.reason}` };
  }

  // Check provider rate limits
  const rateLimit = state.provider_rate_limits[provider];
  if (rateLimit) {
    if (rateLimit.current_minute_count >= rateLimit.requests_per_minute) {
      return { allowed: false, reason: "RATE_LIMIT_MINUTE" };
    }
    if (rateLimit.current_day_count >= rateLimit.requests_per_day) {
      return { allowed: false, reason: "RATE_LIMIT_DAY" };
    }
  }

  // Check concurrency
  if (state.active_containers >= state.max_concurrent_containers) {
    return { allowed: false, reason: "MAX_CONCURRENT_REACHED" };
  }

  return { allowed: true };
}
