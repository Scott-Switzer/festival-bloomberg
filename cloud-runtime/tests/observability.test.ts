import { describe, it, expect } from "vitest";
import { createScorecard } from "../src/observability";

describe("Observability", () => {
  describe("createScorecard", () => {
    it("creates scorecard with zeroed metrics", () => {
      const scorecard = createScorecard("test_run_1");

      expect(scorecard.run_id).toBe("test_run_1");
      expect(scorecard.runs.runs_started).toBe(1);
      expect(scorecard.runs.runs_completed).toBe(0);
      expect(scorecard.tasks.tasks_planned).toBe(0);
      expect(scorecard.tasks.tasks_completed).toBe(0);
      expect(scorecard.network.http_requests).toBe(0);
      expect(scorecard.data.raw_objects_written).toBe(0);
      expect(scorecard.economics.measured_spend_usd).toBe(0);
    });

    it("has correct policy and software version", () => {
      const scorecard = createScorecard("test");
      expect(scorecard.policy_version).toBe("1.0.0");
      expect(scorecard.software_version).toBe("cloud-acquisition-runtime-v1");
    });

    it("timestamp is valid ISO-8601", () => {
      const scorecard = createScorecard("test");
      expect(new Date(scorecard.timestamp).toISOString()).toBe(scorecard.timestamp);
    });
  });
});
