import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  DEFAULT_TIER_ORDER,
  FallbackEnsemble,
  MemoryCacheStore,
} from "../../src/scraper/fallback";
import type { MonidClient } from "../../src/scraper/monid";

describe("fallback ensemble", () => {
  it("keeps Monid before Apify and excludes R2", () => {
    assert.deepEqual([...DEFAULT_TIER_ORDER], [
      "fresh_cache",
      "local_http",
      "playwright",
      "monid",
      "apify",
    ]);
    assert.ok(!DEFAULT_TIER_ORDER.includes("r2" as never));
  });

  it("returns fresh cache before calling http/monid", async () => {
    const cache = new MemoryCacheStore(() => Date.parse("2026-04-01T12:00:00.000Z"));
    await cache.put({
      url: "https://www.coachella.com/lineup",
      body: "<html>cached</html>",
      fetchedAt: "2026-04-01T11:00:00.000Z",
    });

    let httpCalls = 0;
    const ensemble = new FallbackEnsemble({
      cache,
      http: async () => {
        httpCalls += 1;
        return { ok: true, status: 200, body: "fresh" };
      },
      config: { softTtlMs: 6 * 60 * 60 * 1000 },
      now: () => new Date("2026-04-01T12:00:00.000Z"),
    });

    const res = await ensemble.fetch("https://www.coachella.com/lineup", {
      sourceDomain: "www.coachella.com",
    });
    assert.equal(res.ok, true);
    assert.equal(res.tier, "fresh_cache");
    assert.equal(res.body, "<html>cached</html>");
    assert.equal(httpCalls, 0);
  });

  it("prefers monid over apify when earlier tiers fail", async () => {
    let apifyCalls = 0;
    const monid = {
      isConfigured: true,
      fetchStructured: async () => ({
        ok: true,
        status: 200,
        data: { artists: ["A"] },
        costUsd: 0.01,
      }),
    } as unknown as MonidClient;

    const ensemble = new FallbackEnsemble({
      http: async () => ({ ok: false, status: 403, body: "", error: "http_403" }),
      monid,
      apify: async () => {
        apifyCalls += 1;
        return { ok: true, body: "apify" };
      },
      config: { enablePlaywright: false },
    });

    const res = await ensemble.fetch("https://www.bonnaroo.com/lineup");
    assert.equal(res.ok, true);
    assert.equal(res.tier, "monid");
    assert.equal(apifyCalls, 0);
  });
});
