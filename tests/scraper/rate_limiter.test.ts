import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  DomainRateLimiter,
  backoffWithJitter,
  parseRetryAfter,
} from "../../src/scraper/rate_limiter";

describe("rate_limiter", () => {
  it("parses Retry-After seconds and HTTP-date", () => {
    assert.equal(parseRetryAfter("2"), 2000);
    const now = Date.parse("2026-04-01T00:00:00.000Z");
    assert.equal(parseRetryAfter("Wed, 01 Apr 2026 00:00:05 GMT", now), 5000);
    assert.equal(parseRetryAfter(null), null);
  });

  it("applies jittered backoff within [0, cap]", () => {
    const v = backoffWithJitter(3, 100, 1000, () => 0.5);
    assert.ok(v >= 0 && v <= 800);
  });

  it("serializes domain schedule and retries on 429", async () => {
    let now = 1_000_000;
    const sleeps: number[] = [];
    const limiter = new DomainRateLimiter({
      defaults: {
        tokensPerSecond: 100,
        bucketSize: 10,
        minSpacingMs: 0,
        maxRetries: 2,
        baseBackoffMs: 10,
        maxBackoffMs: 50,
      },
      now: () => now,
      sleep: async (ms) => {
        sleeps.push(ms);
        now += ms;
      },
    });

    let attempts = 0;
    const result = await limiter.executeWithRetry("example.com", async () => {
      attempts += 1;
      if (attempts < 2) {
        const err = Object.assign(new Error("rate limited"), {
          status: 429,
          retryAfter: "1",
        });
        throw err;
      }
      return "ok";
    });

    assert.equal(result, "ok");
    assert.equal(attempts, 2);
    assert.ok(sleeps.includes(1000));
  });
});
