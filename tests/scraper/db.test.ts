import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, describe, it } from "node:test";
import {
  createDuckDbWarehouse,
  DuckDbAdapter,
  SupabaseAdapter,
  type SupabaseClientLike,
  type SupabaseFilterBuilder,
} from "../../src/scraper/db";
import type { CostEvent, Lineup, Observation } from "../../src/scraper/schemas";

const tempRoots: string[] = [];

function tempDbPath(): string {
  const dir = mkdtempSync(join(tmpdir(), "fb-duckdb-"));
  tempRoots.push(dir);
  return join(dir, "warehouse.duckdb");
}

after(() => {
  for (const dir of tempRoots) {
    try {
      rmSync(dir, { recursive: true, force: true });
    } catch {
      /* ignore cleanup races */
    }
  }
});

const sampleObservation = (id: string, festivalId = "fest_coachella"): Observation => ({
  id,
  kind: "lineup",
  festivalId,
  editionId: "2026",
  sourceDomain: "www.coachella.com",
  url: "https://www.coachella.com/lineup",
  observedAt: "2026-04-01T12:00:00.000Z",
  payload: { html: "<ul><li>Artist A</li></ul>" },
  evidence: [
    {
      url: "https://www.coachella.com/lineup",
      fetchedAt: "2026-04-01T12:00:00.000Z",
      snippet: "Artist A",
    },
  ],
  tier: "local_http",
  contentHash: "abc123",
});

const sampleLineup = (): Lineup => ({
  festivalId: "fest_coachella",
  editionId: "2026",
  announcedAt: "2026-01-15T00:00:00.000Z",
  sourceDomain: "www.coachella.com",
  confidence: 0.91,
  slots: [
    {
      artistId: "a1",
      artistName: "Artist A",
      stage: "Main",
      billingOrder: 1,
      evidence: [],
    },
  ],
});

const sampleCost = (id: string, total: number, at: string): CostEvent => ({
  id,
  provider: "monid",
  operation: "scrape",
  units: 1,
  unitCostUsd: total,
  totalCostUsd: total,
  currency: "USD",
  at,
  meta: { endpoint: "/v1/scrape", input_tokens: 10, output_tokens: 2 },
});

describe("DuckDB warehouse", () => {
  it("initializes and migrates schema idempotently", async () => {
    const path = tempDbPath();
    const db1 = await createDuckDbWarehouse({ path });
    assert.equal(db1.available, true);
    await db1.observations.upsert(sampleObservation("o-init"));
    await db1.close();

    // Re-open same file: CREATE TABLE IF NOT EXISTS is idempotent; data persists.
    const db2 = await createDuckDbWarehouse({ path });
    assert.equal(db2.available, true);
    const got = await db2.observations.getById("o-init");
    assert.equal(got?.id, "o-init");
    await db2.close();
  });

  it("CRUD/upserts observations and lineups with Zod type preservation", async () => {
    const db = await createDuckDbWarehouse({ path: tempDbPath() });
    const obs = sampleObservation("o1");
    await db.observations.upsert(obs);
    await db.observations.upsert({ ...obs, contentHash: "updated-hash" });
    const gotObs = await db.observations.getById("o1");
    assert.equal(gotObs?.contentHash, "updated-hash");
    assert.equal(gotObs?.url, obs.url);
    assert.deepEqual(gotObs?.payload, obs.payload);
    assert.equal(gotObs?.evidence[0]?.snippet, "Artist A");

    const listed = await db.observations.listByFestival("fest_coachella");
    assert.equal(listed.length, 1);

    const lineup = sampleLineup();
    await db.lineups.upsert(lineup);
    await db.lineups.upsert({ ...lineup, confidence: 0.99 });
    const gotLineup = await db.lineups.get("fest_coachella", "2026");
    assert.equal(gotLineup?.confidence, 0.99);
    assert.equal(gotLineup?.slots[0]?.artistName, "Artist A");
    assert.equal(gotLineup?.announcedAt, "2026-01-15T00:00:00.000Z");
    await db.close();
  });

  it("logs cost events and aggregates estimated_cost_usd", async () => {
    const db = await createDuckDbWarehouse({ path: tempDbPath() });
    await db.costs.append(sampleCost("c1", 0.25, "2026-04-01T00:00:00.000Z"));
    await db.costs.append(sampleCost("c2", 0.75, "2026-04-02T00:00:00.000Z"));
    await db.telemetry.append({
      id: "t1",
      name: "scrape_complete",
      level: "info",
      at: "2026-04-02T00:01:00.000Z",
      durationMs: 120,
      ok: true,
      meta: {},
    });
    assert.equal(await db.costs.sumUsd(), 1);
    assert.equal(await db.costs.sumUsd("2026-04-01T12:00:00.000Z"), 0.75);
    await db.close();
  });

  it("degrades without a client", async () => {
    const duck = new DuckDbAdapter({ client: null });
    assert.equal(duck.available, false);
    assert.equal(await duck.observations.getById("x"), null);
    assert.equal(await duck.costs.sumUsd(), 0);
  });
});

describe("Supabase costs.sumUsd", () => {
  it("sums total_cost_usd when a client is configured", async () => {
    const rows = [
      { total_cost_usd: 1.5, at: "2026-04-01T00:00:00.000Z" },
      { total_cost_usd: 2.5, at: "2026-04-03T00:00:00.000Z" },
    ];

    const makeBuilder = (filtered: typeof rows): SupabaseFilterBuilder => {
      const builder: SupabaseFilterBuilder = {
        eq: () => builder,
        gte: (col, val) => {
          assert.equal(col, "at");
          return makeBuilder(
            rows.filter((r) => Date.parse(r.at) >= Date.parse(String(val))),
          );
        },
        maybeSingle: async () => ({ data: filtered[0] ?? null, error: null }),
        limit: async () => ({ data: filtered, error: null }),
      };
      return builder;
    };

    const client: SupabaseClientLike = {
      from: (table) => {
        assert.equal(table, "scraper_cost_events");
        return {
          upsert: async () => ({ error: null }),
          insert: async () => ({ error: null }),
          select: () => makeBuilder(rows),
        };
      },
    };

    const supa = new SupabaseAdapter({ client });
    assert.equal(await supa.costs.sumUsd(), 4);
    assert.equal(await supa.costs.sumUsd("2026-04-02T00:00:00.000Z"), 2.5);
  });
});
