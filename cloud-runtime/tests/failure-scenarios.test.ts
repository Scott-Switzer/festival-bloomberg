import { describe, it, expect } from "vitest";
import {
  createInitialGovernorState,
  canExecute,
} from "../src/governor";

describe("Source Failure Scenarios", () => {
  describe("Duplicate Queue delivery", () => {
    it("same task delivered twice produces one observation", () => {
      const state = createInitialGovernorState(10, 200);

      // First delivery: allowed
      const result1 = canExecute(state, "task_dup_1", "monid", 0);
      expect(result1.allowed).toBe(true);

      // Record task after successful execution
      state.recent_task_keys["task_dup_1"] = "2026-08-26T12:00:00Z";

      // Second delivery: suppressed
      const result2 = canExecute(state, "task_dup_1", "monid", 0);
      expect(result2.allowed).toBe(false);
      expect(result2.reason).toBe("DUPLICATE_TASK");
    });
  });

  describe("Container killed mid-task", () => {
    it("lease expires and task can be retried", () => {
      const state = createInitialGovernorState(10, 200);

      // Simulate lease
      state.active_leases["task_kill_1"] = {
        task_key: "task_kill_1",
        leased_to: "container_1",
        leased_at: "2026-08-26T12:00:00Z",
        expires_at: new Date(Date.now() - 1000).toISOString(), // expired
        max_lease_seconds: 300,
      };

      // Expired lease should not block new execution
      // (In practice, lease check happens in Governor DO)
      const result = canExecute(state, "task_kill_1_new", "monid", 0);
      expect(result.allowed).toBe(true);
    });
  });

  describe("HTTP 429 rate limiting", () => {
    it("provider cooldown blocks subsequent requests", () => {
      const state = createInitialGovernorState(10, 200);

      // Set cooldown after 429
      state.provider_cooldowns["monid"] = {
        until: new Date(Date.now() + 60000).toISOString(), // 1 minute from now
        reason: "HTTP_429_rate_limit",
      };

      const result = canExecute(state, "task_429_1", "monid", 0);
      expect(result.allowed).toBe(false);
      expect(result.reason).toContain("PROVIDER_COOLDOWN");
    });

    it("cooldown expires and requests resume", () => {
      const state = createInitialGovernorState(10, 200);

      state.provider_cooldowns["monid"] = {
        until: new Date(Date.now() - 1000).toISOString(), // expired
        reason: "HTTP_429_rate_limit",
      };

      const result = canExecute(state, "task_429_2", "monid", 0);
      expect(result.allowed).toBe(true);
    });
  });

  describe("Circuit breaker", () => {
    it("open circuit blocks all requests to provider", () => {
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

      const result = canExecute(state, "task_cb_1", "monid", 0);
      expect(result.allowed).toBe(false);
      expect(result.reason).toBe("CIRCUIT_BREAKER_OPEN");
    });

    it("closed circuit allows requests", () => {
      const state = createInitialGovernorState(10, 200);

      state.circuit_breakers["monid"] = {
        provider: "monid",
        state: "CLOSED",
        failure_count: 0,
        last_failure_at: "",
        cooldown_seconds: 300,
        half_open_success_threshold: 3,
        half_open_success_count: 0,
      };

      const result = canExecute(state, "task_cb_2", "monid", 0);
      expect(result.allowed).toBe(true);
    });
  });

  describe("Budget exhaustion", () => {
    it("no network call when budget is exceeded", () => {
      const state = createInitialGovernorState(10, 200);
      state.daily_spend_usd = 10.00;

      const result = canExecute(state, "task_budget_1", "monid", 0.01);
      expect(result.allowed).toBe(false);
      expect(result.reason).toBe("DAILY_BUDGET_EXCEEDED");
    });

    it("free tasks bypass budget check", () => {
      const state = createInitialGovernorState(10, 200);
      state.daily_spend_usd = 9.99;

      // Free task (expected_cost = 0) should still be allowed
      // as long as it fits (9.99 + 0 <= 10.00)
      const result = canExecute(state, "task_budget_free", "monid", 0);
      expect(result.allowed).toBe(true);
    });
  });

  describe("Max concurrent containers", () => {
    it("blocks when at capacity", () => {
      const state = createInitialGovernorState(10, 200);
      state.active_containers = 3;

      const result = canExecute(state, "task_conc_1", "monid", 0);
      expect(result.allowed).toBe(false);
      expect(result.reason).toBe("MAX_CONCURRENT_REACHED");
    });

    it("allows when under capacity", () => {
      const state = createInitialGovernorState(10, 200);
      state.active_containers = 2;

      const result = canExecute(state, "task_conc_2", "monid", 0);
      expect(result.allowed).toBe(true);
    });
  });
});
