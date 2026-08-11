/**
 * Keeps `src/scraper/schemas.ts` and `schema/duckdb.sql` in lockstep: every
 * row schema must expose exactly the columns of the table it mirrors, and the
 * default matcher weights must match the values seeded by the schema file.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';
import type { ZodObject } from 'zod';

import { DEFAULT_ENTITY_MATCH_WEIGHTS, TABLE_SCHEMAS } from './schemas';

const SCHEMA_SQL = readFileSync(
  fileURLToPath(new URL('../../schema/duckdb.sql', import.meta.url)),
  'utf8',
);

/** Strip `--` line comments so prose cannot be mistaken for SQL. */
function stripComments(sql: string): string {
  return sql
    .split('\n')
    .map((line) => line.replace(/--.*$/, ''))
    .join('\n');
}

/**
 * Map every `CREATE TABLE` in the schema file to its column names. Column
 * definitions are `name TYPE ...`; table-level constraints start with an
 * uppercase keyword (`CHECK`, `PRIMARY KEY`, ...) and are skipped.
 */
function parseTableColumns(sql: string): Map<string, string[]> {
  const tables = new Map<string, string[]>();
  const tablePattern = /CREATE TABLE IF NOT EXISTS\s+([\w.]+)\s*\(([\s\S]*?)\n\);/g;

  for (const match of stripComments(sql).matchAll(tablePattern)) {
    const [, tableName, body] = match;
    if (!tableName || !body) continue;

    const columns = body
      .split('\n')
      .map((line) => line.trim())
      .map((line) => /^([a-z_][a-z0-9_]*)\s+[A-Z]/.exec(line)?.[1])
      .filter((column): column is string => Boolean(column));

    tables.set(tableName, columns);
  }

  return tables;
}

/** Parse the seeded rows of `core.entity_match_weights`. */
function parseSeededWeights(sql: string): Array<{ entity_type: string; feature_name: string; weight: number }> {
  const insertPattern =
    /INSERT OR IGNORE INTO core\.entity_match_weights[\s\S]*?VALUES([\s\S]*?);/;
  const values = insertPattern.exec(sql)?.[1];
  if (!values) return [];

  const rowPattern = /\(\s*'[^']*'\s*,\s*'([^']*)'\s*,\s*'([^']*)'\s*,\s*([\d.]+)/g;
  return [...values.matchAll(rowPattern)].map((match) => ({
    entity_type: match[1] ?? '',
    feature_name: match[2] ?? '',
    weight: Number(match[3]),
  }));
}

const SQL_TABLES = parseTableColumns(SCHEMA_SQL);

describe('schema/duckdb.sql parsing', () => {
  it('finds the expected warehouse layers', () => {
    for (const layer of ['raw', 'core', 'metrics', 'model', 'audit']) {
      expect(SCHEMA_SQL).toContain(`CREATE SCHEMA IF NOT EXISTS ${layer};`);
    }
  });

  it('parses every table with a non-empty column list', () => {
    expect(SQL_TABLES.size).toBeGreaterThanOrEqual(Object.keys(TABLE_SCHEMAS).length);
    for (const [table, columns] of SQL_TABLES) {
      expect(columns.length, `${table} should have columns`).toBeGreaterThan(0);
    }
  });
});

describe('Zod row schemas mirror their DuckDB tables', () => {
  for (const [tableName, schema] of Object.entries(TABLE_SCHEMAS)) {
    it(`${tableName} has identical fields`, () => {
      const columns = SQL_TABLES.get(tableName);
      expect(columns, `${tableName} is missing from schema/duckdb.sql`).toBeDefined();

      const sqlColumns = new Set(columns);
      const zodKeys = new Set(Object.keys((schema as ZodObject).shape));

      const missingFromZod = [...sqlColumns].filter((column) => !zodKeys.has(column));
      const missingFromSql = [...zodKeys].filter((key) => !sqlColumns.has(key));

      expect(missingFromZod, `columns absent from the Zod model for ${tableName}`).toEqual([]);
      expect(missingFromSql, `Zod fields absent from ${tableName}`).toEqual([]);
    });
  }
});

describe('entity resolution support', () => {
  it('indexes the fields the weighted-fuzzy matcher blocks on', () => {
    const expectedIndexes = [
      'idx_artists_normalized_name',
      'idx_artists_country',
      'idx_artists_primary_genre',
      'idx_artists_blocking_key',
      'idx_artist_aliases_normalized',
      'idx_artist_handles_lookup',
      'idx_entity_external_ids_lookup',
      'idx_match_candidates_blocking',
    ];

    for (const index of expectedIndexes) {
      expect(SCHEMA_SQL).toContain(index);
    }
  });

  it('exposes a resolution key view covering names, aliases, ids and handles', () => {
    expect(SCHEMA_SQL).toContain('CREATE OR REPLACE VIEW core.artist_resolution_keys');
    for (const keyType of ["'name'", "'alias'", "'external_id:'", "'social:'"]) {
      expect(SCHEMA_SQL).toContain(keyType);
    }
  });

  it('seeds the same default weights the TypeScript matcher uses', () => {
    const seeded = parseSeededWeights(SCHEMA_SQL);

    expect(seeded).toEqual([...DEFAULT_ENTITY_MATCH_WEIGHTS]);
  });
});
