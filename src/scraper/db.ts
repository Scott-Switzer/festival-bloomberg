/**
 * DuckDB and Supabase interfaces/adapters.
 * R2 storage intentionally omitted. Integrations are optional and degrade gracefully.
 */
import type { CostEvent, Lineup, Observation, TelemetryEvent } from "./schemas";

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
        observations.set(obs.id, obs);
      },
      async getById(id) {
        return observations.get(id) ?? null;
      },
      async listByFestival(festivalId, limit = 100) {
        return [...observations.values()]
          .filter((o) => o.festivalId === festivalId)
          .slice(0, limit);
      },
    },
    lineups: {
      async upsert(lineup) {
        lineups.set(`${lineup.festivalId}:${lineup.editionId}`, lineup);
      },
      async get(festivalId, editionId) {
        return lineups.get(`${festivalId}:${editionId}`) ?? null;
      },
    },
    costs: {
      async append(event) {
        costs.push(event);
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
        telemetry.push(event);
      },
    },
  };
}

export type DuckDbClientLike = {
  run: (sql: string, ...params: unknown[]) => Promise<void> | void;
  all: <T = Record<string, unknown>>(sql: string, ...params: unknown[]) => Promise<T[]> | T[];
};

export type DuckDbAdapterOptions = {
  /** Optional DuckDB-like client; if absent, methods no-op / return empty. */
  client?: DuckDbClientLike | null;
  /** Ensure schema DDL on first use when client present. */
  ensureSchema?: boolean;
};

/**
 * DuckDB adapter — optional. Does not import `duckdb` itself to keep deps light.
 * Pass a client that matches DuckDbClientLike when available.
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
        await this.client.run(
          `INSERT OR REPLACE INTO observations (id, json) VALUES (?, ?)`,
          obs.id,
          JSON.stringify(obs),
        );
      },
      getById: async (id) => {
        await this.ready;
        if (!this.client) return null;
        const rows = await this.client.all<{ json: string }>(
          `SELECT json FROM observations WHERE id = ? LIMIT 1`,
          id,
        );
        return rows[0] ? (JSON.parse(rows[0].json) as Observation) : null;
      },
      listByFestival: async (festivalId, limit = 100) => {
        await this.ready;
        if (!this.client) return [];
        const rows = await this.client.all<{ json: string }>(
          `SELECT json FROM observations WHERE json_extract_string(json, '$.festivalId') = ? LIMIT ?`,
          festivalId,
          limit,
        );
        return rows.map((r) => JSON.parse(r.json) as Observation);
      },
    };

    this.lineups = {
      upsert: async (lineup) => {
        await this.ready;
        if (!this.client) return;
        await this.client.run(
          `INSERT OR REPLACE INTO lineups (festival_id, edition_id, json) VALUES (?, ?, ?)`,
          lineup.festivalId,
          lineup.editionId,
          JSON.stringify(lineup),
        );
      },
      get: async (festivalId, editionId) => {
        await this.ready;
        if (!this.client) return null;
        const rows = await this.client.all<{ json: string }>(
          `SELECT json FROM lineups WHERE festival_id = ? AND edition_id = ? LIMIT 1`,
          festivalId,
          editionId,
        );
        return rows[0] ? (JSON.parse(rows[0].json) as Lineup) : null;
      },
    };

    this.costs = {
      append: async (event) => {
        await this.ready;
        if (!this.client) return;
        await this.client.run(
          `INSERT INTO cost_events (id, json) VALUES (?, ?)`,
          event.id,
          JSON.stringify(event),
        );
      },
      sumUsd: async (sinceIso) => {
        await this.ready;
        if (!this.client) return 0;
        const rows = await this.client.all<{ json: string }>(`SELECT json FROM cost_events`);
        const since = sinceIso ? Date.parse(sinceIso) : 0;
        return rows
          .map((r) => JSON.parse(r.json) as CostEvent)
          .filter((c) => Date.parse(c.at) >= since)
          .reduce((a, c) => a + c.totalCostUsd, 0);
      },
    };

    this.telemetry = {
      append: async (event) => {
        await this.ready;
        if (!this.client) return;
        await this.client.run(
          `INSERT INTO telemetry_events (id, json) VALUES (?, ?)`,
          event.id,
          JSON.stringify(event),
        );
      },
    };
  }

  get available(): boolean {
    return this.client != null;
  }

  private async migrate(): Promise<void> {
    if (!this.client) return;
    await this.client.run(
      `CREATE TABLE IF NOT EXISTS observations (id VARCHAR PRIMARY KEY, json VARCHAR)`,
    );
    await this.client.run(
      `CREATE TABLE IF NOT EXISTS lineups (festival_id VARCHAR, edition_id VARCHAR, json VARCHAR, PRIMARY KEY (festival_id, edition_id))`,
    );
    await this.client.run(
      `CREATE TABLE IF NOT EXISTS cost_events (id VARCHAR PRIMARY KEY, json VARCHAR)`,
    );
    await this.client.run(
      `CREATE TABLE IF NOT EXISTS telemetry_events (id VARCHAR PRIMARY KEY, json VARCHAR)`,
    );
  }
}

export type SupabaseClientLike = {
  from: (table: string) => {
    upsert: (row: Record<string, unknown> | Record<string, unknown>[]) => Promise<{ error: { message: string } | null }>;
    select: (cols?: string) => {
      eq: (col: string, val: unknown) => {
        eq?: (col: string, val: unknown) => {
          maybeSingle: () => Promise<{ data: Record<string, unknown> | null; error: { message: string } | null }>;
          limit: (n: number) => Promise<{ data: Record<string, unknown>[] | null; error: { message: string } | null }>;
        };
        maybeSingle: () => Promise<{ data: Record<string, unknown> | null; error: { message: string } | null }>;
        limit: (n: number) => Promise<{ data: Record<string, unknown>[] | null; error: { message: string } | null }>;
      };
    };
    insert: (row: Record<string, unknown>) => Promise<{ error: { message: string } | null }>;
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
 * Supabase adapter — optional. No `@supabase/supabase-js` hard dependency.
 * When client is missing, operations gracefully no-op.
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
        const { error } = await this.client.from(this.tables.observations).upsert({
          id: obs.id,
          festival_id: obs.festivalId ?? null,
          payload: obs,
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
        return (data?.payload as Observation | undefined) ?? null;
      },
      listByFestival: async (festivalId, limit = 100) => {
        if (!this.client) return [];
        const { data, error } = await this.client
          .from(this.tables.observations)
          .select("payload")
          .eq("festival_id", festivalId)
          .limit(limit);
        if (error) throw new Error(`supabase_observations_list: ${error.message}`);
        return (data ?? []).map((r) => r.payload as Observation);
      },
    };

    this.lineups = {
      upsert: async (lineup) => {
        if (!this.client) return;
        const { error } = await this.client.from(this.tables.lineups).upsert({
          festival_id: lineup.festivalId,
          edition_id: lineup.editionId,
          payload: lineup,
        });
        if (error) throw new Error(`supabase_lineups_upsert: ${error.message}`);
      },
      get: async (festivalId, editionId) => {
        if (!this.client) return null;
        const q = this.client.from(this.tables.lineups).select("payload").eq("festival_id", festivalId);
        const { data, error } = q.eq
          ? await q.eq("edition_id", editionId).maybeSingle()
          : await q.maybeSingle();
        if (error) throw new Error(`supabase_lineups_get: ${error.message}`);
        return (data?.payload as Lineup | undefined) ?? null;
      },
    };

    this.costs = {
      append: async (event) => {
        if (!this.client) return;
        const { error } = await this.client.from(this.tables.costs).insert({
          id: event.id,
          payload: event,
          total_cost_usd: event.totalCostUsd,
          at: event.at,
        });
        if (error) throw new Error(`supabase_costs_append: ${error.message}`);
      },
      sumUsd: async () => {
        // Without RPC aggregation, optional client returns 0 when unavailable.
        if (!this.client) return 0;
        return 0;
      },
    };

    this.telemetry = {
      append: async (event) => {
        if (!this.client) return;
        const { error } = await this.client.from(this.tables.telemetry).insert({
          id: event.id,
          payload: event,
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
 * Compose repos: prefer primary when available, else fallback (e.g. memory).
 * Never references R2.
 */
export function createScraperRepos(opts?: {
  duckdb?: DuckDbClientLike | null;
  supabase?: SupabaseClientLike | null;
  prefer?: "duckdb" | "supabase" | "memory";
}): ScraperRepositories {
  const prefer = opts?.prefer ?? "memory";
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
