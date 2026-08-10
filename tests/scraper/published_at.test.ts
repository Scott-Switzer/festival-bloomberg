import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, it } from "node:test";
import { createDuckDbWarehouse } from "../../src/scraper/db";
import {
  IngestionPipeline,
  type IngestionSourceAdapter,
} from "../../src/scraper/ingestion";
import {
  effectiveObservationTime,
  isObservationKnowableAt,
} from "../../src/scraper/schemas";

type Item = {
  id: string;
  text: string;
  observedAt: string;
  publishedAt?: string;
};

const adapter: IngestionSourceAdapter<Item> = {
  source: "published_at_fixture",
  version: "1",
  sourceRecordId: (item) => item.id,
  adapt: (item) => ({
    kind: "lineup",
    festivalId: "fest_example",
    editionId: "ed_example_2026",
    url: "https://example.com/lineup",
    observedAt: item.observedAt,
    ...(item.publishedAt ? { publishedAt: item.publishedAt } : {}),
    publishedAtPrecision: item.publishedAt ? "day" : undefined,
    payload: item.text,
    deduplicationText: item.text,
    evidence: [],
    metadata: {},
  }),
};

function tempDb(): { path: string; root: string } {
  const root = mkdtempSync(join(tmpdir(), "fb-published-at-"));
  return { root, path: join(root, "warehouse.duckdb") };
}

describe("published_at point-in-time correctness", () => {
  it("falls back to observedAt when publishedAt is absent", () => {
    const observedAt = "2026-01-15T12:00:00.000Z";
    assert.equal(
      effectiveObservationTime({ observedAt }),
      observedAt,
    );
  });

  it("prefers publishedAt for effective ordering and look-ahead checks", () => {
    const observedAt = "2026-01-20T12:00:00.000Z";
    const publishedAt = "2026-01-01T00:00:00.000Z";
    assert.equal(
      effectiveObservationTime({ observedAt, publishedAt }),
      publishedAt,
    );
    assert.equal(
      isObservationKnowableAt({ observedAt, publishedAt }, "2026-01-05T00:00:00.000Z"),
      true,
    );
    assert.equal(
      isObservationKnowableAt(
        { observedAt: "2026-01-03T00:00:00.000Z", publishedAt: "2026-01-10T00:00:00.000Z" },
        "2026-01-05T00:00:00.000Z",
      ),
      false,
    );
  });

  it("selects duplicate winners by publishedAt instead of late retrieval time", async (context) => {
    const temp = tempDb();
    context.after(() => rmSync(temp.root, { recursive: true, force: true }));
    const db = await createDuckDbWarehouse({ path: temp.path });
    const pipeline = new IngestionPipeline(db.ingestion);

    const lateRetrieval: Item = {
      id: "late-retrieval",
      text: "Shared lineup",
      observedAt: "2026-01-20T12:00:00.000Z",
      publishedAt: "2026-01-01T00:00:00.000Z",
    };
    const earlierObservedOnly: Item = {
      id: "earlier-observed",
      text: "Shared lineup",
      observedAt: "2026-01-10T12:00:00.000Z",
    };

    const result = await pipeline.ingest(
      adapter,
      [lateRetrieval, earlierObservedOnly],
      { idempotencyKey: "published-at-winner" },
    );
    assert.equal(result.run.status, "succeeded");
    const inserted = result.logs.find((log) => log.status === "inserted");
    const duplicate = result.logs.find((log) => log.status === "duplicate");
    assert.ok(inserted?.observationId);
    assert.equal(duplicate?.observationId, inserted?.observationId);

    const canonical = await db.ingestion.getCanonicalObservation(
      inserted.observationId,
    );
    assert.ok(canonical);
    assert.equal(canonical.observation.publishedAt, "2026-01-01T00:00:00.000Z");
    assert.equal(
      canonical.observation.observedAt,
      "2026-01-20T12:00:00.000Z",
    );

    await db.close();
  });

  it("orders festival listings by effective time (published_at fallback retrieved_at)", async (context) => {
    const temp = tempDb();
    context.after(() => rmSync(temp.root, { recursive: true, force: true }));
    const db = await createDuckDbWarehouse({ path: temp.path });

    await db.observations.upsert({
      id: "late-retrieval",
      kind: "lineup",
      festivalId: "fest_order",
      editionId: "2026",
      sourceDomain: "example.com",
      url: "https://example.com/a",
      observedAt: "2026-02-01T00:00:00.000Z",
      publishedAt: "2026-01-01T00:00:00.000Z",
      payload: "A",
      evidence: [],
    });
    await db.observations.upsert({
      id: "recent-only",
      kind: "lineup",
      festivalId: "fest_order",
      editionId: "2026",
      sourceDomain: "example.com",
      url: "https://example.com/b",
      observedAt: "2026-01-15T00:00:00.000Z",
      payload: "B",
      evidence: [],
    });

    const listed = await db.observations.listByFestival("fest_order");
    assert.deepEqual(
      listed.map((row) => row.id),
      ["recent-only", "late-retrieval"],
    );
    await db.close();
  });
});
