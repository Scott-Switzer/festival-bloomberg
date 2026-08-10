/** Versioned DuckDB migrations shared by the TypeScript warehouse client. */
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { basename, resolve } from "node:path";
import type { DuckDbClientLike } from "./db";
import {
  loadCanonicalDuckDbStatements,
  resolveSchemaRoot,
  splitSqlStatements,
} from "./duckdb_schema";

export type SchemaMigration = {
  version: number;
  name: string;
  statements: string[];
};

const MIGRATION_FILE_PATTERN = /^(\d+)_(.+)\.sql$/u;

let cachedMigrations: SchemaMigration[] | undefined;

function migrationNameFromFile(fileName: string): string {
  const match = MIGRATION_FILE_PATTERN.exec(fileName);
  if (!match) {
    throw new Error(`Invalid migration filename: ${fileName}`);
  }
  return match[2];
}

export function loadSchemaMigrations(): SchemaMigration[] {
  if (cachedMigrations !== undefined) return cachedMigrations;

  const root = resolveSchemaRoot();
  const migrationDir = resolve(root, "migrations");
  if (!existsSync(migrationDir)) {
    throw new Error(`DuckDB migrations directory not found: ${migrationDir}`);
  }

  cachedMigrations = readdirSync(migrationDir)
    .filter((fileName) => fileName.endsWith(".sql"))
    .map((fileName) => {
      const match = MIGRATION_FILE_PATTERN.exec(fileName);
      if (!match) {
        throw new Error(`Invalid migration filename: ${fileName}`);
      }
      const version = Number(match[1]);
      const sql = readFileSync(resolve(migrationDir, fileName), "utf8");
      return {
        version,
        name: match[2],
        statements: splitSqlStatements(sql),
      };
    })
    .sort((left, right) => left.version - right.version);

  for (let index = 1; index < cachedMigrations.length; index += 1) {
    if (cachedMigrations[index].version <= cachedMigrations[index - 1].version) {
      throw new Error(
        `Migration versions must be strictly increasing (${basename(migrationDir)})`,
      );
    }
  }

  return cachedMigrations;
}

async function appliedMigrationVersions(
  client: DuckDbClientLike,
): Promise<Set<number>> {
  const rows = await client.all<{ version: number }>(
    "SELECT version FROM schema_migrations ORDER BY version",
  );
  return new Set(rows.map((row) => Number(row.version)));
}

/**
 * Apply the base schema and any pending versioned migrations transactionally.
 * Returns the number of migrations applied during this call.
 */
export async function applyPendingMigrations(
  client: DuckDbClientLike,
): Promise<number> {
  for (const statement of loadCanonicalDuckDbStatements()) {
    await client.run(statement);
  }

  const applied = await appliedMigrationVersions(client);
  let appliedNow = 0;

  for (const migration of loadSchemaMigrations()) {
    if (applied.has(migration.version)) continue;

    try {
      for (const statement of migration.statements) {
        await client.run(statement);
      }
      await client.run(
        "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
        migration.version,
        migration.name,
      );
      applied.add(migration.version);
      appliedNow += 1;
    } catch (error) {
      throw error;
    }
  }

  return appliedNow;
}

export function migrationCatalog(): ReadonlyArray<
  Pick<SchemaMigration, "version" | "name">
> {
  return loadSchemaMigrations().map(({ version, name }) => ({ version, name }));
}
