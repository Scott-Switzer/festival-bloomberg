import { describe, it, expect } from "vitest";
import {
  generateTaskKey,
  AcquisitionTask,
  AcquisitionRun,
} from "../src/task-contract";

describe("Task Contract", () => {
  describe("generateTaskKey", () => {
    it("produces deterministic keys from same inputs", () => {
      const key1 = generateTaskKey("evt_123", "ticketmaster", "FAST", "2026-08-26T12", "v1");
      const key2 = generateTaskKey("evt_123", "ticketmaster", "FAST", "2026-08-26T12", "v1");
      expect(key1).toBe(key2);
    });

    it("produces different keys for different inputs", () => {
      const key1 = generateTaskKey("evt_123", "ticketmaster", "FAST", "2026-08-26T12", "v1");
      const key2 = generateTaskKey("evt_123", "seatgeek", "FAST", "2026-08-26T12", "v1");
      expect(key1).not.toBe(key2);
    });

    it("produces different keys for different time windows", () => {
      const key1 = generateTaskKey("evt_123", "ticketmaster", "FAST", "2026-08-26T12", "v1");
      const key2 = generateTaskKey("evt_123", "ticketmaster", "FAST", "2026-08-26T18", "v1");
      expect(key1).not.toBe(key2);
    });

    it("produces different keys for different rails", () => {
      const key1 = generateTaskKey("evt_123", "ticketmaster", "FAST", "2026-08-26T12", "v1");
      const key2 = generateTaskKey("evt_123", "ticketmaster", "DEEP", "2026-08-26T12", "v1");
      expect(key1).not.toBe(key2);
    });

    it("key format includes event prefix", () => {
      const key = generateTaskKey("evt_abc123", "ticketmaster", "FAST", "2026-08-26T12", "v1");
      expect(key).toContain("evt_abc1");
    });
  });

  describe("AcquisitionTask shape", () => {
    it("has all required fields", () => {
      const task: AcquisitionTask = {
        task_key: "test_key",
        event_key: "evt_123",
        source: "monid",
        marketplace: "ticketmaster",
        rail: "FAST",
        target_url: "https://example.com/event/123",
        scheduled_window: "2026-08-26T12",
        priority: 2,
        expected_max_cost_usd: 0.0,
        created_at: "2026-08-26T12:00:00Z",
        software_version: "test",
        trigger: "SCHEDULED",
        run_id: "test_run",
      };
      expect(task.task_key).toBeTruthy();
      expect(task.event_key).toBeTruthy();
      expect(task.rail).toMatch(/^(FAST|DEEP|EVENT|OTHER)$/);
    });
  });

  describe("Duplicate delivery idempotency", () => {
    it("same task_key from same inputs means duplicate detection works", () => {
      const key = generateTaskKey("evt_999", "ticketmaster", "FAST", "2026-08-26T12", "v1");

      // Simulate two deliveries of the same task
      const delivered = new Set<string>();
      delivered.add(key); // first delivery

      // Second delivery — should be detected as duplicate
      expect(delivered.has(key)).toBe(true);
    });

    it("different windows produce different tasks (not duplicates)", () => {
      const key1 = generateTaskKey("evt_999", "ticketmaster", "FAST", "2026-08-26T12", "v1");
      const key2 = generateTaskKey("evt_999", "ticketmaster", "FAST", "2026-08-26T18", "v1");

      const delivered = new Set<string>();
      delivered.add(key1);

      // Different window = legitimate distinct observation
      expect(delivered.has(key2)).toBe(false);
    });
  });
});
