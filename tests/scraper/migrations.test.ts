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
    assert.deepEqual(versions, Array.from({ length: 30 }, (_, i) => i + 1));
    await db.close();

    const rows = await queryMigrations(temp.path);
    assert.equal(rows.length, 30);
    assert.equal(rows[1]?.name, "published_at_point_in_time_v2");
    assert.equal(rows[2]?.name, "intelligence_metrics_v1");
    assert.equal(rows[3]?.name, "canonical_entity_resolution_v1");
    assert.equal(rows[4]?.name, "ticket_secondary_spread_v1");
    assert.equal(rows[5]?.name, "canonical_pit_temporal_fields_v1");
    assert.equal(rows[6]?.name, "acquisition_evidence_v1");
    assert.equal(rows[7]?.name, "evidence_semantics_v1");
    assert.equal(rows[8]?.name, "youtube_fan_signal_v1");
    assert.equal(rows[9]?.name, "event_performance_v1");
    assert.equal(rows[10]?.name, "market_economics_v1");
    assert.equal(rows[11]?.name, "forward_market_history_v1");
    assert.equal(rows[12]?.name, "historical_outcome_laboratory_v1");
    assert.equal(rows[13]?.name, "design_partner_retrospective_v1");
    assert.equal(rows[14]?.name, "public_boxscore_research_corpus_v1");
    assert.equal(rows[15]?.name, "public_boxscore_research_corpus_v2");
    assert.equal(rows[16]?.name, "data_flywheel_coverage_v1");
    assert.equal(rows[17]?.name, "data_acquisition_activation_v1");
    assert.equal(rows[18]?.name, "pr21_semantic_closure_v1");
    assert.equal(rows[19]?.name, "pre_event_cutoff_acquisition_v1");
    assert.equal(rows[20]?.name, "historical_decision_evidence_engine_v1");
    assert.equal(rows[21]?.name, "intelligence_terminal_mvp_v1");
    assert.equal(rows[22]?.name, "festival_spine_v1");
    assert.equal(rows[23]?.name, "live_data_activation_v1");
    assert.equal(rows[24]?.name, "live_entertainment_data_fabric_v1");
    assert.equal(rows[25]?.name, "national_coverage_entity_master_v1");
    assert.equal(rows[26]?.name, "music_security_master_v1");
    assert.equal(rows[27]?.name, "music_reference_graph_v1");
    assert.equal(rows[28]?.name, "music_terminal_productization_v1");
    assert.equal(rows[29]?.name, "alert_related_entities_and_acquisition_runs_v1");

    const reopened = await createDuckDbWarehouse({ path: temp.path });
    await reopened.close();
    const again = await queryMigrations(temp.path);
    assert.deepEqual(
      again.map((row) => row.version),
      Array.from({ length: 30 }, (_, i) => i + 1),
    );
  });
});
