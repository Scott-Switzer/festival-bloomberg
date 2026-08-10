import assert from "node:assert/strict";
import { existsSync, readFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { describe, it } from "node:test";
import { createDuckDbWarehouse } from "../../src/scraper/db";
import {
  IdempotencyConflictError,
  IngestionPipeline,
  type IngestionSourceAdapter,
} from "../../src/scraper/ingestion";
import type { IngestionRecord } from "../../src/scraper/schemas";

type FixtureItem = {
  sourceRecordId: string;
  transientFailure?: boolean;
  record: IngestionRecord;
};

type Fixture = {
  source: string;
  adapterVersion: string;
  idempotencyKey: string;
  records: FixtureItem[];
};

const fixturePath = resolve(
  __dirname,
  "../../../tests/fixtures/ingestion/coachella-2026.json",
);
const fixture = JSON.parse(readFileSync(fixturePath, "utf8")) as Fixture;

function tempDb(): { path: string; root: string } {
  const root = mkdtempSync(join(tmpdir(), "fb-ingestion-e2e-"));
  return { root, path: join(root, "festival_bloomberg.duckdb") };
}

describe("fixture ingestion pipeline", () => {
  it("initializes, logs, normalizes, deduplicates, resumes, and replays idempotently", async (context) => {
    const { path, root } = tempDb();
    context.after(() => rmSync(root, { recursive: true, force: true }));

    // Opening the same fresh database twice exercises idempotent initialization.
    const initialized = await createDuckDbWarehouse({ path });
    await initialized.close();
    let db = await createDuckDbWarehouse({ path });

    const transientAttempts = new Set<string>();
    const adapter: IngestionSourceAdapter<FixtureItem> = {
      source: fixture.source,
      version: fixture.adapterVersion,
      sourceRecordId: (item) => item.sourceRecordId,
      adapt: (item) => {
        if (
          item.transientFailure &&
          !transientAttempts.has(item.sourceRecordId)
        ) {
          transientAttempts.add(item.sourceRecordId);
          throw new Error("fixture_transient_failure");
        }
        return item.record;
      },
    };

    let clockMs = Date.parse("2026-08-10T10:00:00.000Z");
    const pipeline = new IngestionPipeline(db.ingestion, {
      now: () => new Date(clockMs),
    });
    const first = await pipeline.ingest(adapter, fixture.records, {
      idempotencyKey: fixture.idempotencyKey,
      metadata: { fixture: "coachella-2026.json" },
    });

    assert.equal(first.run.status, "partial");
    assert.equal(first.run.attemptedCount, 4);
    assert.equal(first.run.insertedCount, 2);
    assert.equal(first.run.duplicateCount, 1);
    assert.equal(first.run.failedCount, 1);
    assert.equal(first.logs.length, 4);
    const failed = first.logs.find(
      (log) => log.sourceRecordId === "official-passes",
    );
    assert.equal(failed?.status, "failed");
    assert.equal(failed?.errorCode, "adapter_error");
    assert.match(failed?.errorMessage ?? "", /fixture_transient_failure/);
    assert.deepEqual(failed?.metadata, {});

    const laterLog = first.logs.find(
      (log) => log.sourceRecordId === "official-lineup-later",
    );
    const originalLog = first.logs.find(
      (log) => log.sourceRecordId === "official-lineup-original",
    );
    const updateLog = first.logs.find(
      (log) => log.sourceRecordId === "official-lineup-update",
    );
    assert.equal(laterLog?.status, "inserted");
    assert.deepEqual(laterLog?.metadata, {
      feed: "official-site",
      httpStatus: 200,
    });
    assert.equal(originalLog?.status, "duplicate");
    assert.equal(originalLog?.observationId, laterLog?.observationId);
    assert.equal(originalLog?.duplicateOf, laterLog?.observationId);
    assert.notEqual(updateLog?.observationId, laterLog?.observationId);

    const canonicalId = laterLog?.observationId;
    assert.ok(canonicalId);
    const canonical = await db.ingestion.getCanonicalObservation(canonicalId);
    assert.ok(canonical);
    assert.equal(
      canonical.canonicalUrl,
      "https://www.coachella.com/lineup?a=1&b=2",
    );
    assert.equal(canonical.normalizedContent, "Beyoncé • Artist A");
    assert.equal(canonical.seenCount, 2);
    assert.equal(canonical.firstSeenAt, "2026-01-15T12:00:00.000Z");
    assert.equal(canonical.lastSeenAt, "2026-01-15T12:10:00.000Z");
    // The earliest observation deterministically wins regardless of arrival.
    assert.equal(
      canonical.observation.url,
      "https://www.coachella.com/lineup?a=1&b=2",
    );
    assert.equal(canonical.observation.tier, "fresh_cache");
    assert.deepEqual(canonical.observation.evidence, [
      {
        url: "https://www.coachella.com/lineup?a=1&b=2",
        selector: "#lineup",
        snippet: "Beyoncé • Artist A",
        fetchedAt: "2026-01-15T12:00:00.000Z",
      },
    ]);

    // The retry resumes only the failed source record; successful logs are not
    // replayed and therefore do not inflate seen_count.
    clockMs += 60_000;
    const resumed = await pipeline.ingest(adapter, fixture.records, {
      idempotencyKey: fixture.idempotencyKey,
      metadata: { fixture: "coachella-2026.json" },
    });
    assert.equal(resumed.replayed, false);
    assert.equal(resumed.run.status, "succeeded");
    assert.equal(resumed.run.insertedCount, 3);
    assert.equal(resumed.run.duplicateCount, 1);
    assert.equal(resumed.run.failedCount, 0);
    assert.equal(
      (await db.ingestion.getCanonicalObservation(canonicalId))?.seenCount,
      2,
    );
    const observationIds = [
      ...new Set(
        resumed.logs
          .map((log) => log.observationId)
          .filter((id): id is string => id !== undefined),
      ),
    ].sort();
    const canonicalRows = await Promise.all(
      observationIds.map((id) => db.ingestion.getCanonicalObservation(id)),
    );

    const beforeReplay = {
      run: resumed.run,
      logs: resumed.logs,
      observation: await db.ingestion.getCanonicalObservation(canonicalId),
    };
    clockMs += 60_000;
    const replay = await pipeline.ingest(
      adapter,
      [...fixture.records].reverse(),
      {
        idempotencyKey: fixture.idempotencyKey,
        metadata: { fixture: "coachella-2026.json" },
      },
    );
    assert.equal(replay.replayed, true);
    assert.deepEqual(replay.run, beforeReplay.run);
    assert.deepEqual(replay.logs, beforeReplay.logs);
    assert.deepEqual(
      await db.ingestion.getCanonicalObservation(canonicalId),
      beforeReplay.observation,
    );

    const changedRecords = JSON.parse(
      JSON.stringify(fixture.records),
    ) as FixtureItem[];
    changedRecords[0].record.deduplicationText = "changed request";
    await assert.rejects(
      pipeline.ingest(adapter, changedRecords, {
        idempotencyKey: fixture.idempotencyKey,
      }),
      IdempotencyConflictError,
    );

    assert.deepEqual(
      await db.ingestion.getRun(fixture.source, fixture.idempotencyKey),
      resumed.run,
    );
    await db.close();
    assert.equal(existsSync(path), true);
    db = await createDuckDbWarehouse({ path });
    assert.deepEqual(
      await db.ingestion.getRun(fixture.source, fixture.idempotencyKey),
      resumed.run,
    );
    assert.equal(
      (await db.ingestion.getCanonicalObservation(canonicalId))?.seenCount,
      2,
    );
    await db.close();

    // A fresh database fed in reverse order must converge on the same rows,
    // rather than merely passing through the successful replay shortcut.
    const reversedTemp = tempDb();
    context.after(() =>
      rmSync(reversedTemp.root, { recursive: true, force: true }),
    );
    const reversedDb = await createDuckDbWarehouse({ path: reversedTemp.path });
    const reversedPipeline = new IngestionPipeline(reversedDb.ingestion, {
      now: () => new Date(clockMs),
    });
    const reversed = await reversedPipeline.ingest(
      adapter,
      [...fixture.records].reverse(),
      { idempotencyKey: fixture.idempotencyKey },
    );
    assert.equal(reversed.run.status, "succeeded");
    assert.deepEqual(
      await Promise.all(
        observationIds.map((id) =>
          reversedDb.ingestion.getCanonicalObservation(id),
        ),
      ),
      canonicalRows,
    );
    await reversedDb.close();
  });
});
