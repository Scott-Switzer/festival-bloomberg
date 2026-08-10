/** Loader for the SQL schema shared by the TypeScript and Python clients. */
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

export const CANONICAL_SCHEMA_RELATIVE_PATH = "schema/duckdb.sql";

let cachedSchema: string | undefined;
let cachedSchemaRoot: string | undefined;

export function resolveSchemaRoot(): string {
  if (cachedSchemaRoot !== undefined) return cachedSchemaRoot;

  const candidates = [
    // Source tree and builds that copy schema/ under dist/.
    resolve(__dirname, "../../schema"),
    // Compiled dist/src/scraper code running from a repository checkout.
    resolve(__dirname, "../../../schema"),
    resolve(process.cwd(), "schema"),
  ];
  const root = candidates.find((candidate) =>
    existsSync(resolve(candidate, "duckdb.sql")),
  );
  if (!root) {
    throw new Error(
      `Canonical DuckDB schema not found (${CANONICAL_SCHEMA_RELATIVE_PATH})`,
    );
  }
  cachedSchemaRoot = root;
  return root;
}

export function loadCanonicalDuckDbSchema(): string {
  if (cachedSchema !== undefined) return cachedSchema;

  const path = resolve(resolveSchemaRoot(), "duckdb.sql");
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
