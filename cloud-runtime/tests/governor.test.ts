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

  describe("Race condition simulation", () => {
    it("canExecute checks budget without reserving (DO handles atomicity)", () => {
      const state = createInitialGovernorState(10, 200);

      // canExecute is a pre-check — the Governor DO's reserveTask is atomic.
      // Without reservation, all 5 pre-checks pass (expected behavior).
      // The DO's reserveTask prevents actual overcommitment.
      const results = [];
      for (let i = 0; i < 5; i++) {
        results.push(canExecute(state, `task_${i}`, "monid", 3.00));
      }

      // All pre-checks pass because canExecute doesn't reserve
      const allowed = results.filter((r) => r.allowed);
      expect(allowed.length).toBe(5);

      // Atomic reservation is tested via the Governor DO integration tests
      // (Phase 13+ real pilot)
    });
  });
});
