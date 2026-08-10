import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, it } from "node:test";
import { createDuckDbWarehouse } from "../../src/scraper/db";
import {
  IdempotencyConflictError,
  IngestionPipeline,
  type IngestionSourceAdapter,
} from "../../src/scraper/ingestion";

type Item = { id: string; text: string };

const adapter: IngestionSourceAdapter<Item> = {
  source: "unit_fixture",
  version: "1",
  sourceRecordId: (item) => item.id,
  adapt: (item) => ({
    kind: "meta",
    url: "https://example.com/status",
    observedAt: "2026-01-01T00:00:00Z",
    payload: item.text,
    deduplicationText: item.text,
    evidence: [],
    metadata: { sourceType: "unit" },
  }),
};

function tempDb(): { path: string; root: string } {
  const root = mkdtempSync(join(tmpdir(), "fb-ingestion-unit-"));
  return { root, path: join(root, "warehouse.duckdb") };
}

describe("ingestion idempotency", () => {
  it("serializes concurrent identical runs and preserves empty normalized content", async (context) => {
    const temp = tempDb();
    context.after(() => rmSync(temp.root, { recursive: true, force: true }));
    const db = await createDuckDbWarehouse({ path: temp.path });
    const firstPipeline = new IngestionPipeline(db.ingestion);
    const secondPipeline = new IngestionPipeline(db.ingestion);

    const results = await Promise.all([
      firstPipeline.ingest(adapter, [{ id: "empty", text: " \n " }], {
        idempotencyKey: "same-request",
      }),
      secondPipeline.ingest(adapter, [{ id: "empty", text: " \n " }], {
        idempotencyKey: "same-request",
      }),
    ]);
    assert.deepEqual(
      results.map((result) => result.replayed).sort(),
      [false, true],
    );
    const observationId = results[0].logs[0]?.observationId;
    assert.ok(observationId);
    const stored = await db.ingestion.getCanonicalObservation(observationId);
    assert.equal(stored?.normalizedContent, "");
    assert.equal(stored?.seenCount, 1);
    await db.close();
  });

  it("rejects concurrent reuse with changed input without overwriting the claim", async (context) => {
    const temp = tempDb();
    context.after(() => rmSync(temp.root, { recursive: true, force: true }));
    const db = await createDuckDbWarehouse({ path: temp.path });
    const firstPipeline = new IngestionPipeline(db.ingestion);
    const secondPipeline = new IngestionPipeline(db.ingestion);

    const outcomes = await Promise.allSettled([
      firstPipeline.ingest(adapter, [{ id: "status", text: "On sale" }], {
        idempotencyKey: "conflicting-request",
      }),
      secondPipeline.ingest(adapter, [{ id: "status", text: "Sold out" }], {
        idempotencyKey: "conflicting-request",
      }),
    ]);
    assert.equal(outcomes[0].status, "fulfilled");
    assert.equal(outcomes[1].status, "rejected");
    assert.ok(
      outcomes[1].status === "rejected" &&
        outcomes[1].reason instanceof IdempotencyConflictError,
    );
    const persisted = await db.ingestion.getRun(
      adapter.source,
      "conflicting-request",
    );
    assert.equal(
      persisted?.requestHash,
      outcomes[0].status === "fulfilled"
        ? outcomes[0].value.run.requestHash
        : undefined,
    );
    await db.close();
  });
});
