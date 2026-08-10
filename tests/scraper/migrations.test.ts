import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import duckdb from "duckdb";
import { describe, it } from "node:test";
import { createDuckDbWarehouse } from "../../src/scraper/db";
import { migrationCatalog } from "../../src/scraper/migrations";

function tempDb(): { path: string; root: string } {
  const root = mkdtempSync(join(tmpdir(), "fb-migrations-"));
  return { root, path: join(root, "warehouse.duckdb") };
}

function queryMigrations(path: string): Promise<Array<{ version: number; name: string }>> {
  return new Promise((resolvePromise, reject) => {
    const connection = new duckdb.Database(path);
    connection.all(
      "SELECT version, name FROM schema_migrations ORDER BY version",
      (error, rows) => {
        connection.close((closeError) => {
          if (error) reject(error);
          else if (closeError) reject(closeError);
          else resolvePromise(rows as Array<{ version: number; name: string }>);
        });
      },
    );
  });
}

describe("schema migrations", () => {
  it("records applied versions idempotently", async (context) => {
    const temp = tempDb();
    context.after(() => rmSync(temp.root, { recursive: true, force: true }));

    const db = await createDuckDbWarehouse({ path: temp.path });
    const versions = migrationCatalog().map((migration) => migration.version);
    assert.deepEqual(versions, [1, 2, 3, 4]);
    await db.close();

    const rows = await queryMigrations(temp.path);
    assert.equal(rows.length, 4);
    assert.equal(rows[1]?.name, "published_at_point_in_time_v2");
    assert.equal(rows[2]?.name, "intelligence_metrics_v1");
    assert.equal(rows[3]?.name, "canonical_entity_resolution_v1");

    const reopened = await createDuckDbWarehouse({ path: temp.path });
    await reopened.close();
    const again = await queryMigrations(temp.path);
    assert.deepEqual(
      again.map((row) => row.version),
      [1, 2, 3, 4],
    );
  });
});
