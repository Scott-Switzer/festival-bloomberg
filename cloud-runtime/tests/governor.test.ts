import { describe, it, expect } from "vitest";
import {
  createInitialGovernorState,
  canExecute,
  GovernorState,
} from "../src/governor";

describe("Governor", () => {
  describe("createInitialGovernorState", () => {
    it("creates state with correct defaults", () => {
      const state = createInitialGovernorState(10, 200);
      expect(state.authorized_daily_budget_usd).toBe(10);
      expect(state.authorized_monthly_budget_usd).toBe(200);
      expect(state.daily_spend_usd).toBe(0);
      expect(state.monthly_spend_usd).toBe(0);
      expect(state.active_containers).toBe(0);
      expect(state.max_concurrent_containers).toBe(3);
      expect(state.reservations).toEqual({});
      expect(state.observation_state).toEqual({});
    });
  });

  describe("canExecute", () => {
    it("allows execution when budget is available", () => {
      const state = createInitialGovernorState(10, 200);
      const result = canExecute(state, "task_1", "monid", 0);
      expect(result.allowed).toBe(true);
    });

    it("blocks duplicate tasks", () => {
      const state = createInitialGovernorState(10, 200);
      state.recent_task_keys["task_1"] = "2026-08-26T12:00:00Z";
      const result = canExecute(state, "task_1", "monid", 0);
      expect(result.allowed).toBe(false);
      expect(result.reason).toBe("DUPLICATE_TASK");
    });

    it("blocks when daily budget exceeded", () => {
      const state = createInitialGovernorState(10, 200);
      state.daily_spend_usd = 9.50;
      const result = canExecute(state, "task_1", "monid", 1.00);
      expect(result.allowed).toBe(false);
      expect(result.reason).toBe("DAILY_BUDGET_EXCEEDED");
    });

    it("allows when cost fits within remaining budget", () => {
      const state = createInitialGovernorState(10, 200);
      state.daily_spend_usd = 9.00;
      const result = canExecute(state, "task_1", "monid", 0.50);
      expect(result.allowed).toBe(true);
    });

    it("blocks when monthly budget exceeded", () => {
      const state = createInitialGovernorState(10, 200);
      state.monthly_spend_usd = 199.50;
      const result = canExecute(state, "task_1", "monid", 1.00);
      expect(result.allowed).toBe(false);
      expect(result.reason).toBe("MONTHLY_BUDGET_EXCEEDED");
    });

    it("blocks when circuit breaker is open", () => {
      const state = createInitialGovernorState(10, 200);
      state.circuit_breakers["monid"] = {
        provider: "monid",
        state: "OPEN",
        failure_count: 5,
        last_failure_at: "2026-08-26T12:00:00Z",
        cooldown_seconds: 300,
        half_open_success_threshold: 3,
        half_open_success_count: 0,
      };
      const result = canExecute(state, "task_1", "monid", 0);
      expect(result.allowed).toBe(false);
      expect(result.reason).toBe("CIRCUIT_BREAKER_OPEN");
    });

    it("blocks when provider is in cooldown", () => {
      const state = createInitialGovernorState(10, 200);
      state.provider_cooldowns["monid"] = {
        until: new Date(Date.now() + 60000).toISOString(),
        reason: "rate_limited",
      };
      const result = canExecute(state, "task_1", "monid", 0);
      expect(result.allowed).toBe(false);
      expect(result.reason).toContain("PROVIDER_COOLDOWN");
    });

    it("blocks when max concurrent containers reached", () => {
      const state = createInitialGovernorState(10, 200);
      state.active_containers = 3;
      const result = canExecute(state, "task_1", "monid", 0);
      expect(result.allowed).toBe(false);
      expect(result.reason).toBe("MAX_CONCURRENT_REACHED");
    });

    it("blocks when rate limit per minute exceeded", () => {
      const state = createInitialGovernorState(10, 200);
      state.provider_rate_limits["monid"] = {
        provider: "monid",
        requests_per_minute: 10,
        requests_per_day: 1000,
        cost_per_day_usd: 5.0,
        current_minute_count: 10,
        current_day_count: 0,
        current_day_cost_usd: 0,
        minute_window_start: new Date().toISOString(),
        day_window_start: new Date().toISOString(),
      };
      const result = canExecute(state, "task_1", "monid", 0);
      expect(result.allowed).toBe(false);
      expect(result.reason).toBe("RATE_LIMIT_MINUTE");
    });
  });

  describe("Reservation accounting", () => {
    it("existing reservations count against available budget", () => {
      const state = createInitialGovernorState(10, 200);
      state.reservations["task_a"] = {
        task_key: "task_a",
        acquisition_provider: "monid",
        expected_cost_usd: 5.00,
        reserved_at: "2026-08-26T12:00:00Z",
        expires_at: "2026-08-26T12:05:00Z",
      };

      // 10 - 5.00 reserved = 5.00 available
      const result = canExecute(state, "task_b", "monid", 5.01);
      expect(result.allowed).toBe(false);
      expect(result.reason).toBe("DAILY_BUDGET_EXCEEDED");

      // But 5.00 fits exactly
      const result2 = canExecute(state, "task_b", "monid", 5.00);
      expect(result2.allowed).toBe(true);
    });

    it("spent + reservations are tracked independently", () => {
      const state = createInitialGovernorState(10, 200);
      state.daily_spend_usd = 3.00;
      state.reservations["task_a"] = {
        task_key: "task_a",
        acquisition_provider: "monid",
        expected_cost_usd: 4.00,
        reserved_at: "2026-08-26T12:00:00Z",
        expires_at: "2026-08-26T12:05:00Z",
      };

      // 3.00 spent + 4.00 reserved = 7.00 committed. Budget = 10.
      // 10 - 7.00 = 3.00 available
      const result = canExecute(state, "task_b", "monid", 3.01);
      expect(result.allowed).toBe(false);

      const result2 = canExecute(state, "task_b", "monid", 3.00);
      expect(result2.allowed).toBe(true);
    });
  });

  describe("Provider != marketplace", () => {
    it("canExecute uses acquisition_provider for circuit breaker", () => {
      const state = createInitialGovernorState(10, 200);
      state.circuit_breakers["monid"] = {
        provider: "monid",
        state: "OPEN",
        failure_count: 5,
        last_failure_at: "2026-08-26T12:00:00Z",
        cooldown_seconds: 300,
        half_open_success_threshold: 3,
        half_open_success_count: 0,
      };

      // Blocking monid should NOT block seatgeek
      const result1 = canExecute(state, "task_1", "monid", 0);
      expect(result1.allowed).toBe(false);

      const result2 = canExecute(state, "task_2", "seatgeek", 0);
      expect(result2.allowed).toBe(true);
    });

    it("rate limits are per acquisition_provider, not per marketplace", () => {
      const state = createInitialGovernorState(10, 200);
      state.provider_rate_limits["monid"] = {
        provider: "monid",
        requests_per_minute: 1,
        requests_per_day: 1000,
        cost_per_day_usd: 5.0,
        current_minute_count: 1,
        current_day_count: 0,
        current_day_cost_usd: 0,
        minute_window_start: new Date().toISOString(),
        day_window_start: new Date().toISOString(),
      };

      // monid is rate-limited
      const result1 = canExecute(state, "task_1", "monid", 0);
      expect(result1.allowed).toBe(false);
      expect(result1.reason).toBe("RATE_LIMIT_MINUTE");

      // seatgeek is not (no rate limit set for it)
      const result2 = canExecute(state, "task_2", "seatgeek", 0);
      expect(result2.allowed).toBe(true);
    });
  });
});
