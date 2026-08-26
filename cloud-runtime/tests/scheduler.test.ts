import { describe, it, expect } from "vitest";
import {
  shouldObserveNow,
  nextObservationTime,
  DEFAULT_CADENCE_POLICY,
} from "../src/scheduler";

describe("Scheduler", () => {
  describe("shouldObserveNow", () => {
    it("returns false for post-show events", () => {
      expect(shouldObserveNow(-1, 999)).toBe(false);
    });

    it("returns true when enough time has passed for weekly cadence", () => {
      // 90 days to show → weekly cadence → 168 hours between observations
      expect(shouldObserveNow(90, 200)).toBe(true);
    });

    it("returns false when not enough time has passed for weekly cadence", () => {
      // 90 days to show → weekly → 168 hours; only 24 hours ago
      expect(shouldObserveNow(90, 24)).toBe(false);
    });

    it("returns true for daily cadence after 24+ hours", () => {
      // 45 days to show → daily → 24 hours between
      expect(shouldObserveNow(45, 25)).toBe(true);
    });

    it("returns false for daily cadence before 24 hours", () => {
      expect(shouldObserveNow(45, 12)).toBe(false);
    });

    it("returns true for 2x/day after 12+ hours", () => {
      // 10 days to show → 2x/day → 12 hours between
      expect(shouldObserveNow(10, 13)).toBe(true);
    });

    it("returns true for show day (0 days) after appropriate interval", () => {
      // 0.5 days to show → show day cadence → ~4 hours between
      expect(shouldObserveNow(0.5, 5)).toBe(true);
    });
  });

  describe("nextObservationTime", () => {
    it("returns null for post-show", () => {
      expect(nextObservationTime(-1)).toBeNull();
    });

    it("returns 2x/week interval for 90-day events (60-120 range)", () => {
      const result = nextObservationTime(90);
      expect(result).not.toBeNull();
      expect(result!.rule.label).toBe("2x/week");
    });

    it("returns weekly interval for 150-day events (120+ range)", () => {
      const result = nextObservationTime(150);
      expect(result).not.toBeNull();
      expect(result!.rule.label).toBe("weekly");
      expect(result!.hours_until).toBeCloseTo(168, 0);
    });

    it("returns daily interval for mid-range events", () => {
      const result = nextObservationTime(45);
      expect(result).not.toBeNull();
      expect(result!.rule.label).toBe("daily");
    });

    it("returns 2x/day for close events", () => {
      const result = nextObservationTime(10);
      expect(result).not.toBeNull();
      expect(result!.rule.label).toBe("2x/day");
    });
  });
});
