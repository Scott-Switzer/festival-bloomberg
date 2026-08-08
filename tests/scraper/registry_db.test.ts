import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  getFestival,
  isPathAllowed,
  sourcesByPriority,
} from "../../src/scraper/registry";
import { createMemoryRepos, DuckDbAdapter, SupabaseAdapter } from "../../src/scraper/db";
import { loadMonidConfig } from "../../src/scraper/monid";

describe("registry + db + monid config", () => {
  it("orders sources by priority and checks allowed paths", () => {
    const ranked = sourcesByPriority();
    assert.ok(ranked[0].priority >= ranked[1].priority);
    assert.equal(isPathAllowed("www.coachella.com", "/lineup"), true);
    assert.equal(isPathAllowed("www.coachella.com", "/admin"), false);
    assert.equal(getFestival("fest_coachella")?.slug, "coachella");
  });

  it("memory repos round-trip observations", async () => {
    const db = createMemoryRepos();
    await db.observations.upsert({
      id: "o1",
      kind: "meta",
      festivalId: "fest_coachella",
      sourceDomain: "www.coachella.com",
      url: "https://www.coachella.com/",
      observedAt: "2026-04-01T00:00:00.000Z",
      payload: {},
      evidence: [],
    });
    const got = await db.observations.getById("o1");
    assert.equal(got?.festivalId, "fest_coachella");
  });

  it("optional duckdb/supabase adapters degrade without clients", async () => {
    const duck = new DuckDbAdapter({ client: null });
    const supa = new SupabaseAdapter({ client: null });
    assert.equal(duck.available, false);
    assert.equal(supa.available, false);
    assert.equal(await duck.observations.getById("x"), null);
    assert.equal(await supa.lineups.get("a", "b"), null);
  });

  it("loads monid config from env without hardcoded secrets", () => {
    const cfg = loadMonidConfig({
      MONID_API_KEY: "test_key",
      MONID_DEFAULT_PROVIDER: "monid-prod",
    } as NodeJS.ProcessEnv);
    assert.equal(cfg.apiKey, "test_key");
    assert.equal(cfg.defaultProvider, "monid-prod");
    assert.ok(cfg.baseUrl.includes("monid.ai"));
  });
});
