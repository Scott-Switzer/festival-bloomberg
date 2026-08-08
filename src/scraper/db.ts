/**
 * DuckDB (authoritative local cost/telemetry warehouse) and optional Supabase adapters.
 * R2 storage intentionally omitted. Integrations degrade gracefully when unconfigured.
 */
import { createHash } from "node:crypto";
import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import duckdb from "duckdb";
import {
  parseCostEvent,
  parseLineup,
  parseObservation,
  parseTelemetryEvent,
  type CostEvent,
  type Lineup,
  type Observation,
  type TelemetryEvent,
} from "./schemas";

export const DEFAULT_WAREHOUSE_PATH = "data/warehouse/festival_bloomberg.duckdb";

export type ScraperRepositories = {
  observations: ObservationRepo;
  lineups: LineupRepo;
  costs: CostRepo;
  telemetry: TelemetryRepo;
};

export type ObservationRepo = {
  upsert(obs: Observation): Promise<void>;
  getById(id: string): Promise<Observation | null>;
  listByFestival(festivalId: string, limit?: number): Promise<Observation[]>;
};

export type LineupRepo = {
  upsert(lineup: Lineup): Promise<void>;
  get(festivalId: string, editionId: string): Promise<Lineup | null>;
};

export type CostRepo = {
  append(event: CostEvent): Promise<void>;
  sumUsd(sinceIso?: string): Promise<number>;
};

export type TelemetryRepo = {
  append(event: TelemetryEvent): Promise<void>;
};

/** In-memory adapters for local/dev and tests. */
export function createMemoryRepos(): ScraperRepositories {
  const observations = new Map<string, Observation>();
  const lineups = new Map<string, Lineup>();
  const costs: CostEvent[] = [];
  const telemetry: TelemetryEvent[] = [];

  return {
    observations: {
      async upsert(obs) {
        observations.set(obs.id, parseObservation(obs));
      },
      async getById(id) {
        const row = observations.get(id);
        return row ? parseObservation(row) : null;
      },
      async listByFestival(festivalId, limit = 100) {
        return [...observations.values()]
          .filter((o) => o.festivalId === festivalId)
          .slice(0, limit)
          .map((o) => parseObservation(o));
      },
    },
    lineups: {
      async upsert(lineup) {
        lineups.set(`${lineup.festivalId}:${lineup.editionId}`, parseLineup(lineup));
      },
      async get(festivalId, editionId) {
        const row = lineups.get(`${festivalId}:${editionId}`);
        return row ? parseLineup(row) : null;
      },
    },
    costs: {
      async append(event) {
        costs.push(parseCostEvent(event));
      },
      async sumUsd(sinceIso) {
        const since = sinceIso ? Date.parse(sinceIso) : 0;
        return costs
          .filter((c) => Date.parse(c.at) >= since)
          .reduce((a, c) => a + c.totalCostUsd, 0);
      },
    },
    telemetry: {
      async append(event) {
        telemetry.push(parseTelemetryEvent(event));
      },
    },
  };
}

export type DuckDbClientLike = {
  run: (sql: string, ...params: unknown[]) => Promise<void> | void;
  all: <T = Record<string, unknown>>(sql: string, ...params: unknown[]) => Promise<T[]> | T[];
  close?: () => Promise<void> | void;
};

export type DuckDbAdapterOptions = {
  /** Optional DuckDB-like client; if absent, methods no-op / return empty. */
  client?: DuckDbClientLike | null;
  /** Ensure schema DDL on first use when client present. */
  ensureSchema?: boolean;
};

type ObservationRow = {
  id: string;
  source_url: string;
  raw_content: string | null;
  content_hash: string | null;
  retrieved_at: string;
  status: string | null;
  kind: string;
  festival_id: string | null;
  edition_id: string | null;
  source_domain: string;
  tier: string | null;
  evidence_json: string | null;
  payload_json: string | null;
};

type LineupRow = {
  id: string;
  festival_id: string;
  edition_id: string;
  raw_artists: string | null;
  parsed_artists: string | null;
  confidence: number | null;
  extracted_at: string | null;
  source_domain: string;
  announced_at: string | null;
};

type CostRow = {
  id: string;
  provider: string;
  endpoint: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  estimated_cost_usd: number | null;
  timestamp: string;
  operation: string;
  units: number | null;
  unit_cost_usd: number | null;
  currency: string | null;
  meta_json: string | null;
};

type TelemetryRow = {
  id: string;
  event_type: string;
  duration_ms: number | null;
  status: string | null;
  error: string | null;
  timestamp: string;
  level: string | null;
  domain: string | null;
  url: string | null;
  tier: string | null;
  meta_json: string | null;
};

function iso(value: unknown): string {
  if (value instanceof Date) return value.toISOString();
  if (typeof value === "string") {
    const ms = Date.parse(value);
    if (!Number.isNaN(ms)) return new Date(ms).toISOString();
    return value;
  }
  return String(value);
}

function lineupId(festivalId: string, editionId: string): string {
  return `${festivalId}:${editionId}`;
}

function observationFromRow(row: ObservationRow): Observation {
  return parseObservation({
    id: row.id,
    kind: row.kind,
    festivalId: row.festival_id ?? undefined,
    editionId: row.edition_id ?? undefined,
    sourceDomain: row.source_domain,
    url: row.source_url,
    observedAt: iso(row.retrieved_at),
    payload: JSON.parse(row.payload_json ?? row.raw_content ?? "null"),
    evidence: JSON.parse(row.evidence_json ?? "[]"),
    tier: row.tier ?? undefined,
    contentHash: row.content_hash ?? undefined,
  });
}

function lineupFromRow(row: LineupRow): Lineup {
  return parseLineup({
    festivalId: row.festival_id,
    editionId: row.edition_id,
    announcedAt: row.announced_at ? iso(row.announced_at) : undefined,
    slots: JSON.parse(row.parsed_artists ?? "[]"),
    sourceDomain: row.source_domain,
    confidence: row.confidence ?? 0.5,
  });
}

function costFromRow(row: CostRow): CostEvent {
  const meta = {
    ...(JSON.parse(row.meta_json ?? "{}") as Record<string, unknown>),
  };
  if (row.input_tokens != null) meta.input_tokens = row.input_tokens;
  if (row.output_tokens != null) meta.output_tokens = row.output_tokens;
  if (row.endpoint != null) meta.endpoint = row.endpoint;
  return parseCostEvent({
    id: row.id,
    provider: row.provider,
    operation: row.operation,
    units: row.units ?? 1,
    unitCostUsd: row.unit_cost_usd ?? 0,
    totalCostUsd: row.estimated_cost_usd ?? 0,
    currency: row.currency ?? "USD",
    at: iso(row.timestamp),
    meta,
  });
}

function telemetryStatus(event: TelemetryEvent): string {
  if (event.ok === false) return "error";
  if (event.ok === true) return "ok";
  return event.level;
}

/**
 * DuckDB adapter — authoritative local warehouse for observations, lineups,
 * costs, and telemetry. Validates rows with Zod schemas on read.
 */
export class DuckDbAdapter implements ScraperRepositories {
  readonly observations: ObservationRepo;
  readonly lineups: LineupRepo;
  readonly costs: CostRepo;
  readonly telemetry: TelemetryRepo;
  private readonly client: DuckDbClientLike | null;
  private ready: Promise<void>;

  constructor(opts: DuckDbAdapterOptions = {}) {
    this.client = opts.client ?? null;
    this.ready =
      this.client && opts.ensureSchema !== false
        ? Promise.resolve(this.migrate())
        : Promise.resolve();

    this.observations = {
      upsert: async (obs) => {
        await this.ready;
        if (!this.client) return;
        const parsed = parseObservation(obs);
        const payloadJson = JSON.stringify(parsed.payload ?? null);
        const rawContent =
          typeof parsed.payload === "string" ? parsed.payload : payloadJson;
        const contentHash =
          parsed.contentHash ??
          createHash("sha256").update(rawContent).digest("hex");
        await this.client.run(
          `INSERT INTO observations (
            id, source_url, raw_content, content_hash, retrieved_at, status,
            kind, festival_id, edition_id, source_domain, tier, evidence_json, payload_json
          ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
          ON CONFLICT (id) DO UPDATE SET
            source_url = EXCLUDED.source_url,
            raw_content = EXCLUDED.raw_content,
            content_hash = EXCLUDED.content_hash,
            retrieved_at = EXCLUDED.retrieved_at,
            status = EXCLUDED.status,
            kind = EXCLUDED.kind,
            festival_id = EXCLUDED.festival_id,
            edition_id = EXCLUDED.edition_id,
            source_domain = EXCLUDED.source_domain,
            tier = EXCLUDED.tier,
            evidence_json = EXCLUDED.evidence_json,
            payload_json = EXCLUDED.payload_json`,
          parsed.id,
          parsed.url,
          rawContent,
          contentHash,
          parsed.observedAt,
          "ok",
          parsed.kind,
          parsed.festivalId ?? null,
          parsed.editionId ?? null,
          parsed.sourceDomain,
          parsed.tier ?? null,
          JSON.stringify(parsed.evidence ?? []),
          payloadJson,
        );
      },
      getById: async (id) => {
        await this.ready;
        if (!this.client) return null;
        const rows = await this.client.all<ObservationRow>(
          `SELECT * FROM observations WHERE id = ? LIMIT 1`,
          id,
        );
        return rows[0] ? observationFromRow(rows[0]) : null;
      },
      listByFestival: async (festivalId, limit = 100) => {
        await this.ready;
        if (!this.client) return [];
        const rows = await this.client.all<ObservationRow>(
          `SELECT * FROM observations WHERE festival_id = ? LIMIT ?`,
          festivalId,
          limit,
        );
        return rows.map(observationFromRow);
      },
    };

    this.lineups = {
      upsert: async (lineup) => {
        await this.ready;
        if (!this.client) return;
        const parsed = parseLineup(lineup);
        const id = lineupId(parsed.festivalId, parsed.editionId);
        const parsedArtists = JSON.stringify(parsed.slots ?? []);
        const rawArtists = (parsed.slots ?? []).map((s) => s.artistName).join(", ");
        await this.client.run(
          `INSERT INTO lineups (
            id, festival_id, edition_id, raw_artists, parsed_artists,
            confidence, extracted_at, source_domain, announced_at
          ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
          ON CONFLICT (id) DO UPDATE SET
            festival_id = EXCLUDED.festival_id,
            edition_id = EXCLUDED.edition_id,
            raw_artists = EXCLUDED.raw_artists,
            parsed_artists = EXCLUDED.parsed_artists,
            confidence = EXCLUDED.confidence,
            extracted_at = EXCLUDED.extracted_at,
            source_domain = EXCLUDED.source_domain,
            announced_at = EXCLUDED.announced_at`,
          id,
          parsed.festivalId,
          parsed.editionId,
          rawArtists,
          parsedArtists,
          parsed.confidence,
          new Date().toISOString(),
          parsed.sourceDomain,
          parsed.announcedAt ?? null,
        );
      },
      get: async (festivalId, editionId) => {
        await this.ready;
        if (!this.client) return null;
        const rows = await this.client.all<LineupRow>(
          `SELECT * FROM lineups WHERE festival_id = ? AND edition_id = ? LIMIT 1`,
          festivalId,
          editionId,
        );
        return rows[0] ? lineupFromRow(rows[0]) : null;
      },
    };

    this.costs = {
      append: async (event) => {
        await this.ready;
        if (!this.client) return;
        const parsed = parseCostEvent(event);
        const meta = parsed.meta ?? {};
        await this.client.run(
          `INSERT INTO costs (
            id, provider, endpoint, input_tokens, output_tokens, estimated_cost_usd,
            timestamp, operation, units, unit_cost_usd, currency, meta_json
          ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
          ON CONFLICT (id) DO UPDATE SET
            provider = EXCLUDED.provider,
            endpoint = EXCLUDED.endpoint,
            input_tokens = EXCLUDED.input_tokens,
            output_tokens = EXCLUDED.output_tokens,
            estimated_cost_usd = EXCLUDED.estimated_cost_usd,
            timestamp = EXCLUDED.timestamp,
            operation = EXCLUDED.operation,
            units = EXCLUDED.units,
            unit_cost_usd = EXCLUDED.unit_cost_usd,
            currency = EXCLUDED.currency,
            meta_json = EXCLUDED.meta_json`,
          parsed.id,
          parsed.provider,
          (meta.endpoint as string | undefined) ?? parsed.operation,
          typeof meta.input_tokens === "number" ? meta.input_tokens : null,
          typeof meta.output_tokens === "number" ? meta.output_tokens : null,
          parsed.totalCostUsd,
          parsed.at,
          parsed.operation,
          parsed.units,
          parsed.unitCostUsd,
          parsed.currency,
          JSON.stringify(meta),
        );
      },
      sumUsd: async (sinceIso) => {
        await this.ready;
        if (!this.client) return 0;
        const rows = sinceIso
          ? await this.client.all<{ total: number | null }>(
              `SELECT COALESCE(SUM(estimated_cost_usd), 0) AS total FROM costs WHERE timestamp >= ?`,
              sinceIso,
            )
          : await this.client.all<{ total: number | null }>(
              `SELECT COALESCE(SUM(estimated_cost_usd), 0) AS total FROM costs`,
            );
        return Number(rows[0]?.total ?? 0);
      },
    };

    this.telemetry = {
      append: async (event) => {
        await this.ready;
        if (!this.client) return;
        const parsed = parseTelemetryEvent(event);
        await this.client.run(
          `INSERT INTO telemetry (
            id, event_type, duration_ms, status, error, timestamp,
            level, domain, url, tier, meta_json
          ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
          ON CONFLICT (id) DO UPDATE SET
            event_type = EXCLUDED.event_type,
            duration_ms = EXCLUDED.duration_ms,
            status = EXCLUDED.status,
            error = EXCLUDED.error,
            timestamp = EXCLUDED.timestamp,
            level = EXCLUDED.level,
            domain = EXCLUDED.domain,
            url = EXCLUDED.url,
            tier = EXCLUDED.tier,
            meta_json = EXCLUDED.meta_json`,
          parsed.id,
          parsed.name,
          parsed.durationMs ?? null,
          telemetryStatus(parsed),
          parsed.errorCode ?? null,
          parsed.at,
          parsed.level,
          parsed.domain ?? null,
          parsed.url ?? null,
          parsed.tier ?? null,
          JSON.stringify(parsed.meta ?? {}),
        );
      },
    };
  }

  get available(): boolean {
    return this.client != null;
  }

  async close(): Promise<void> {
    await this.ready;
    await this.client?.close?.();
  }

  /** Idempotent schema creation / migration. */
  private async migrate(): Promise<void> {
    if (!this.client) return;
    await this.client.run(`
      CREATE TABLE IF NOT EXISTS observations (
        id VARCHAR PRIMARY KEY,
        source_url VARCHAR NOT NULL,
        raw_content VARCHAR,
        content_hash VARCHAR,
        retrieved_at TIMESTAMP NOT NULL,
        status VARCHAR,
        kind VARCHAR NOT NULL,
        festival_id VARCHAR,
        edition_id VARCHAR,
        source_domain VARCHAR NOT NULL,
        tier VARCHAR,
        evidence_json VARCHAR,
        payload_json VARCHAR
      )
    `);
    await this.client.run(`
      CREATE TABLE IF NOT EXISTS lineups (
        id VARCHAR PRIMARY KEY,
        festival_id VARCHAR NOT NULL,
        edition_id VARCHAR NOT NULL,
        raw_artists VARCHAR,
        parsed_artists VARCHAR,
        confidence DOUBLE,
        extracted_at TIMESTAMP,
        source_domain VARCHAR NOT NULL,
        announced_at TIMESTAMP,
        UNIQUE (festival_id, edition_id)
      )
    `);
    await this.client.run(`
      CREATE TABLE IF NOT EXISTS costs (
        id VARCHAR PRIMARY KEY,
        provider VARCHAR NOT NULL,
        endpoint VARCHAR,
        input_tokens INTEGER,
        output_tokens INTEGER,
        estimated_cost_usd DOUBLE,
        timestamp TIMESTAMP NOT NULL,
        operation VARCHAR NOT NULL,
        units DOUBLE,
        unit_cost_usd DOUBLE,
        currency VARCHAR,
        meta_json VARCHAR
      )
    `);
    await this.client.run(`
      CREATE TABLE IF NOT EXISTS telemetry (
        id VARCHAR PRIMARY KEY,
        event_type VARCHAR NOT NULL,
        duration_ms DOUBLE,
        status VARCHAR,
        error VARCHAR,
        timestamp TIMESTAMP NOT NULL,
        level VARCHAR,
        domain VARCHAR,
        url VARCHAR,
        tier VARCHAR,
        meta_json VARCHAR
      )
    `);
  }
}

function promisifyDuckDb(db: duckdb.Database): DuckDbClientLike {
  const run = (sql: string, ...params: unknown[]): Promise<void> =>
    new Promise((resolvePromise, reject) => {
      db.run(sql, ...params, (err: Error | null) => {
        if (err) reject(err);
        else resolvePromise();
      });
    });

  const all = <T = Record<string, unknown>>(
    sql: string,
    ...params: unknown[]
  ): Promise<T[]> =>
    new Promise((resolvePromise, reject) => {
      const cb = (err: Error | null, rows: duckdb.TableData) => {
        if (err) reject(err);
        else resolvePromise((rows ?? []) as T[]);
      };
      // duckdb typings require a rest tuple ending in Callback; cast keeps runtime API.
      (db.all as unknown as (sql: string, ...args: unknown[]) => void)(
        sql,
        ...params,
        cb,
      );
    });

  const close = (): Promise<void> =>
    new Promise((resolvePromise, reject) => {
      db.close((err: Error | null) => {
        if (err) reject(err);
        else resolvePromise();
      });
    });

  return { run, all, close };
}

export type DuckDbWarehouseOptions = {
  /** Filesystem path; default data/warehouse/festival_bloomberg.duckdb (or env). */
  path?: string;
};

/**
 * Open the local DuckDB warehouse at a configurable path, creating parent dirs
 * and running idempotent migrations. DuckDB is the authoritative local store
 * for cost events and telemetry (and observations/lineups).
 */
export async function createDuckDbWarehouse(
  opts: DuckDbWarehouseOptions = {},
): Promise<DuckDbAdapter> {
  const path = resolve(
    opts.path ??
      process.env.FESTIVAL_BLOOMBERG_DUCKDB_PATH ??
      DEFAULT_WAREHOUSE_PATH,
  );
  mkdirSync(dirname(path), { recursive: true });
  const db = new duckdb.Database(path);
  const client = promisifyDuckDb(db);
  return new DuckDbAdapter({ client, ensureSchema: true });
}

export type SupabaseFilterBuilder = {
  eq: (col: string, val: unknown) => SupabaseFilterBuilder;
  gte: (col: string, val: unknown) => SupabaseFilterBuilder;
  maybeSingle: () => Promise<{
    data: Record<string, unknown> | null;
    error: { message: string } | null;
  }>;
  limit: (n: number) => Promise<{
    data: Record<string, unknown>[] | null;
    error: { message: string } | null;
  }>;
};

export type SupabaseClientLike = {
  from: (table: string) => {
    upsert: (
      row: Record<string, unknown> | Record<string, unknown>[],
    ) => Promise<{ error: { message: string } | null }>;
    select: (cols?: string) => SupabaseFilterBuilder;
    insert: (
      row: Record<string, unknown>,
    ) => Promise<{ error: { message: string } | null }>;
  };
};

export type SupabaseAdapterOptions = {
  client?: SupabaseClientLike | null;
  /** Table name overrides. */
  tables?: Partial<{
    observations: string;
    lineups: string;
    costs: string;
    telemetry: string;
  }>;
};

/**
 * Supabase adapter — optional remote mirror. No `@supabase/supabase-js` hard dependency.
 * Cost/telemetry aggregation for local ops should use DuckDB; Supabase sumUsd still
 * computes a real sum when a client is configured.
 */
export class SupabaseAdapter implements ScraperRepositories {
  readonly observations: ObservationRepo;
  readonly lineups: LineupRepo;
  readonly costs: CostRepo;
  readonly telemetry: TelemetryRepo;
  private readonly client: SupabaseClientLike | null;
  private readonly tables: Required<NonNullable<SupabaseAdapterOptions["tables"]>>;

  constructor(opts: SupabaseAdapterOptions = {}) {
    this.client = opts.client ?? null;
    this.tables = {
      observations: opts.tables?.observations ?? "scraper_observations",
      lineups: opts.tables?.lineups ?? "scraper_lineups",
      costs: opts.tables?.costs ?? "scraper_cost_events",
      telemetry: opts.tables?.telemetry ?? "scraper_telemetry_events",
    };

    this.observations = {
      upsert: async (obs) => {
        if (!this.client) return;
        const parsed = parseObservation(obs);
        const { error } = await this.client.from(this.tables.observations).upsert({
          id: parsed.id,
          festival_id: parsed.festivalId ?? null,
          payload: parsed,
        });
        if (error) throw new Error(`supabase_observations_upsert: ${error.message}`);
      },
      getById: async (id) => {
        if (!this.client) return null;
        const { data, error } = await this.client
          .from(this.tables.observations)
          .select("payload")
          .eq("id", id)
          .maybeSingle();
        if (error) throw new Error(`supabase_observations_get: ${error.message}`);
        return data?.payload != null ? parseObservation(data.payload) : null;
      },
      listByFestival: async (festivalId, limit = 100) => {
        if (!this.client) return [];
        const { data, error } = await this.client
          .from(this.tables.observations)
          .select("payload")
          .eq("festival_id", festivalId)
          .limit(limit);
        if (error) throw new Error(`supabase_observations_list: ${error.message}`);
        return (data ?? []).map((r) => parseObservation(r.payload));
      },
    };

    this.lineups = {
      upsert: async (lineup) => {
        if (!this.client) return;
        const parsed = parseLineup(lineup);
        const { error } = await this.client.from(this.tables.lineups).upsert({
          festival_id: parsed.festivalId,
          edition_id: parsed.editionId,
          payload: parsed,
        });
        if (error) throw new Error(`supabase_lineups_upsert: ${error.message}`);
      },
      get: async (festivalId, editionId) => {
        if (!this.client) return null;
        const { data, error } = await this.client
          .from(this.tables.lineups)
          .select("payload")
          .eq("festival_id", festivalId)
          .eq("edition_id", editionId)
          .maybeSingle();
        if (error) throw new Error(`supabase_lineups_get: ${error.message}`);
        return data?.payload != null ? parseLineup(data.payload) : null;
      },
    };

    this.costs = {
      append: async (event) => {
        if (!this.client) return;
        const parsed = parseCostEvent(event);
        const { error } = await this.client.from(this.tables.costs).insert({
          id: parsed.id,
          payload: parsed,
          total_cost_usd: parsed.totalCostUsd,
          at: parsed.at,
        });
        if (error) throw new Error(`supabase_costs_append: ${error.message}`);
      },
      sumUsd: async (sinceIso) => {
        // Remote convenience sum — DuckDB remains authoritative for local warehouse totals.
        if (!this.client) return 0;
        let query = this.client
          .from(this.tables.costs)
          .select("total_cost_usd,at,payload");
        if (sinceIso) query = query.gte("at", sinceIso);
        const { data, error } = await query.limit(100_000);
        if (error) throw new Error(`supabase_costs_sum: ${error.message}`);
        return (data ?? []).reduce((sum, row) => {
          if (typeof row.total_cost_usd === "number") return sum + row.total_cost_usd;
          if (row.payload != null) {
            try {
              return sum + parseCostEvent(row.payload).totalCostUsd;
            } catch {
              return sum;
            }
          }
          return sum;
        }, 0);
      },
    };

    this.telemetry = {
      append: async (event) => {
        if (!this.client) return;
        const parsed = parseTelemetryEvent(event);
        const { error } = await this.client.from(this.tables.telemetry).insert({
          id: parsed.id,
          payload: parsed,
        });
        if (error) throw new Error(`supabase_telemetry_append: ${error.message}`);
      },
    };
  }

  get available(): boolean {
    return this.client != null;
  }
}

/**
 * Compose repos. Prefer DuckDB (authoritative local warehouse) when a client/path
 * is provided; Supabase is an optional remote mirror. Never references R2.
 */
export function createScraperRepos(opts?: {
  duckdb?: DuckDbClientLike | null;
  supabase?: SupabaseClientLike | null;
  prefer?: "duckdb" | "supabase" | "memory";
}): ScraperRepositories {
  const prefer = opts?.prefer ?? (opts?.duckdb ? "duckdb" : "memory");
  if (prefer === "duckdb" && opts?.duckdb) {
    return new DuckDbAdapter({ client: opts.duckdb });
  }
  if (prefer === "supabase" && opts?.supabase) {
    return new SupabaseAdapter({ client: opts.supabase });
  }
  if (opts?.duckdb) return new DuckDbAdapter({ client: opts.duckdb });
  if (opts?.supabase) return new SupabaseAdapter({ client: opts.supabase });
  return createMemoryRepos();
}
