/** Loader for the SQL schema shared by the TypeScript and Python clients. */
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

export const CANONICAL_SCHEMA_RELATIVE_PATH = "schema/duckdb.sql";

let cachedSchema: string | undefined;

export function loadCanonicalDuckDbSchema(): string {
  if (cachedSchema !== undefined) return cachedSchema;

  const candidates = [
    // Source tree and builds that copy schema/ under dist/.
    resolve(__dirname, "../../schema/duckdb.sql"),
    // Compiled dist/src/scraper code running from a repository checkout.
    resolve(__dirname, "../../../schema/duckdb.sql"),
    resolve(process.cwd(), CANONICAL_SCHEMA_RELATIVE_PATH),
  ];
  const path = candidates.find((candidate) => existsSync(candidate));
  if (!path) {
    throw new Error(
      `Canonical DuckDB schema not found (${CANONICAL_SCHEMA_RELATIVE_PATH})`,
    );
  }
  cachedSchema = readFileSync(path, "utf8");
  return cachedSchema;
}

/**
 * The legacy DuckDB Node binding can report completion before every statement
 * in a multi-statement string is checkpointed. Execute this schema sequentially.
 * The canonical SQL intentionally contains no semicolons inside literals.
 */
export function loadCanonicalDuckDbStatements(): string[] {
  return loadCanonicalDuckDbSchema()
    .split(";")
    .map((statement) => statement.trim())
    .filter(Boolean);
}
