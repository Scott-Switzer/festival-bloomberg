/**
 * Acquisition Governor — centralized budget/rate/concurrency state.
 *
 * Uses a Cloudflare Durable Object for strong consistency.
 * Tracks CONTROL STATE ONLY — no canonical evidence here.
 *
 * Hard invariant:
 *   spent + SUM(reservations) + expected_next_cost <= authorized_budget
 *
 * must be checked BEFORE any paid network request.
 *
 * Provider ≠ marketplace:
 *   Governor budgets/rate limits operate on acquisition_provider.
 *   Marketplace remains evidence provenance.
 */

/** Per-task reservation — tracks the exact reserved amount */
export interface TaskReservation {
  task_key: string;
  acquisition_provider: string;
  expected_cost_usd: number;
  reserved_at: string;
  expires_at: string;
}

/** Per-event×marketplace observation state */
export interface ObservationState {
  event_key: string;
  marketplace: string;
  rail: string;
  last_successful_observation_at: string;
  last_successful_logical_window: string;
  last_failure_at: string;
  consecutive_failures: number;
}

/** Provider-specific rate limits */
export interface ProviderRateLimit {
  provider: string;
  requests_per_minute: number;
  requests_per_day: number;
  cost_per_day_usd: number;
  current_minute_count: number;
  current_day_count: number;
  current_day_cost_usd: number;
  minute_window_start: string;
  day_window_start: string;
}

/** Circuit breaker states */
export type CircuitState = "CLOSED" | "OPEN" | "HALF_OPEN";

export interface CircuitBreaker {
  provider: string;
  state: CircuitState;
  failure_count: number;
  last_failure_at: string;
  cooldown_seconds: number;
  half_open_success_threshold: number;
  half_open_success_count: number;
}

/** Task lease — prevents concurrent execution of the same task */
export interface TaskLease {
  task_key: string;
  leased_to: string;
  leased_at: string;
  expires_at: string;
  max_lease_seconds: number;
}

/**
 * The full governor state — stored in a Durable Object.
 * Immutable economic evidence stays in R2/DuckDB, never here.
 */
export interface GovernorState {
  /** Budget tracking */
  daily_spend_usd: number;
  reserved_spend_usd: number;
  monthly_spend_usd: number;
  authorized_daily_budget_usd: number;
  authorized_monthly_budget_usd: number;

  /** Period tracking */
  current_day: string;
  current_month: string;

  /** Explicit per-task reservations with amounts */
  reservations: Record<string, TaskReservation>;

  /** Current observation state per event×marketplace×rail */
  observation_state: Record<string, ObservationState>;

  /** Per-provider rate limits (keyed by acquisition_provider) */
  provider_rate_limits: Record<string, ProviderRateLimit>;

  /** Circuit breakers (keyed by acquisition_provider) */
  circuit_breakers: Record<string, CircuitBreaker>;

  /** Active task leases */
  active_leases: Record<string, TaskLease>;

  /** Task dedup/idempotency */
  recent_task_keys: Record<string, string>;

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

/**
 * Quick pre-flight check (non-atomic — Governor DO's reserveTask is the source of truth).
 * Used for testing and workflow planning.
 */
export function canExecute(
  state: GovernorState,
  task_key: string,
  acquisition_provider: string,
  expected_cost_usd: number
): { allowed: boolean; reason?: string } {
  if (state.recent_task_keys[task_key]) {
    return { allowed: false, reason: "DUPLICATE_TASK" };
  }
  const totalReserved = Object.values(state.reservations || {}).reduce(
    (sum, r) => sum + r.expected_cost_usd, 0
  );
  if (state.daily_spend_usd + totalReserved + expected_cost_usd > state.authorized_daily_budget_usd) {
    return { allowed: false, reason: "DAILY_BUDGET_EXCEEDED" };
  }
  if (state.monthly_spend_usd + totalReserved + expected_cost_usd > state.authorized_monthly_budget_usd) {
    return { allowed: false, reason: "MONTHLY_BUDGET_EXCEEDED" };
  }
  const breaker = state.circuit_breakers[acquisition_provider];
  if (breaker?.state === "OPEN") {
    return { allowed: false, reason: "CIRCUIT_BREAKER_OPEN" };
  }
  const cooldown = state.provider_cooldowns[acquisition_provider];
  if (cooldown && new Date(cooldown.until) > new Date()) {
    return { allowed: false, reason: `PROVIDER_COOLDOWN: ${cooldown.reason}` };
  }
  const rateLimit = state.provider_rate_limits[acquisition_provider];
  if (rateLimit) {
    if (rateLimit.current_minute_count >= rateLimit.requests_per_minute) {
      return { allowed: false, reason: "RATE_LIMIT_MINUTE" };
    }
    if (rateLimit.current_day_count >= rateLimit.requests_per_day) {
      return { allowed: false, reason: "RATE_LIMIT_DAY" };
    }
  }
  if (state.active_containers >= state.max_concurrent_containers) {
    return { allowed: false, reason: "MAX_CONCURRENT_REACHED" };
  }
  return { allowed: true };
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
    reservations: {},
    observation_state: {},
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
