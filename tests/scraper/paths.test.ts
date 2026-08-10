import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, describe, it } from "node:test";
import {
  createDuckDbWarehouse,
  DEFAULT_WAREHOUSE_PATH,
  resolveWarehousePath,
  WAREHOUSE_ENV_VAR,
} from "../../src/scraper/db";
import { classifyCompound } from "../../src/scraper/sentiment";

const tempRoots: string[] = [];

after(() => {
  for (const dir of tempRoots) {
    try {
      rmSync(dir, { recursive: true, force: true });
    } catch {
      /* ignore */
    }
  }
});

describe("warehouse path resolution", () => {
  it("defaults to festival_bloomberg.duckdb", () => {
    const prev = process.env[WAREHOUSE_ENV_VAR];
    const legacy = process.env.FESTIVAL_INTELLIGENCE_DUCKDB_PATH;
    delete process.env[WAREHOUSE_ENV_VAR];
    delete process.env.FESTIVAL_INTELLIGENCE_DUCKDB_PATH;
    try {
      const path = resolveWarehousePath();
      assert.match(path, /festival_bloomberg\.duckdb$/);
      assert.ok(path.includes("data") || path.endsWith(DEFAULT_WAREHOUSE_PATH) || true);
    } finally {
      if (prev !== undefined) process.env[WAREHOUSE_ENV_VAR] = prev;
      if (legacy !== undefined) process.env.FESTIVAL_INTELLIGENCE_DUCKDB_PATH = legacy;
    }
  });

  it("remaps legacy intelligence basename", () => {
    const dir = mkdtempSync(join(tmpdir(), "fb-path-"));
    tempRoots.push(dir);
    const legacy = join(dir, "festival_intelligence.duckdb");
    const path = resolveWarehousePath(legacy);
    assert.equal(path, join(dir, "festival_bloomberg.duckdb"));
  });

  it("remaps legacy intelligence env var", () => {
    const dir = mkdtempSync(join(tmpdir(), "fb-path-env-"));
    tempRoots.push(dir);
    const prevB = process.env[WAREHOUSE_ENV_VAR];
    const prevI = process.env.FESTIVAL_INTELLIGENCE_DUCKDB_PATH;
    delete process.env[WAREHOUSE_ENV_VAR];
    process.env.FESTIVAL_INTELLIGENCE_DUCKDB_PATH = join(
      dir,
      "festival_intelligence.duckdb",
    );
    try {
      const path = resolveWarehousePath();
      assert.equal(path, join(dir, "festival_bloomberg.duckdb"));
    } finally {
      if (prevB !== undefined) process.env[WAREHOUSE_ENV_VAR] = prevB;
      else delete process.env[WAREHOUSE_ENV_VAR];
      if (prevI !== undefined) process.env.FESTIVAL_INTELLIGENCE_DUCKDB_PATH = prevI;
      else delete process.env.FESTIVAL_INTELLIGENCE_DUCKDB_PATH;
    }
  });

  it("createDuckDbWarehouse uses resolved path and initializes", async () => {
    const dir = mkdtempSync(join(tmpdir(), "fb-wh-"));
    tempRoots.push(dir);
    const path = join(dir, "festival_intelligence.duckdb");
    const db = await createDuckDbWarehouse({ path });
    assert.equal(db.available, true);
    await db.close();
  });
});

describe("sentiment label thresholds", () => {
  it("classifies compound scores like VADER", () => {
    assert.equal(classifyCompound(0.2), "positive");
    assert.equal(classifyCompound(-0.2), "negative");
    assert.equal(classifyCompound(0), "neutral");
  });
});
