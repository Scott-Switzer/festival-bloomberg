/**
 * DuckDB (authoritative local cost/telemetry warehouse) and optional Supabase adapters.
 * R2 storage intentionally omitted. Integrations degrade gracefully when unconfigured.
 */
import { createHash } from "node:crypto";
import { mkdirSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";
import duckdb from "duckdb";
import {
  parseIngestionLog,
  parseIngestionRun,
  parseCostEvent,
  parseLineup,
  parseObservation,
  parseTelemetryEvent,
  type CostEvent,
  type IngestionLog,
  type IngestionRun,
  type Lineup,
  type Observation,
  type TelemetryEvent,
} from "./schemas";
import { loadCanonicalDuckDbStatements } from "./duckdb_schema";
import {
  canonicalizeUrl,
  mergeEvidence,
  normalizeText,
  normalizedContent,
} from "./normalization";

export const DEFAULT_WAREHOUSE_PATH = "data/warehouse/festival_bloomberg.duckdb";
export const WAREHOUSE_ENV_VAR = "FESTIVAL_BLOOMBERG_DUCKDB_PATH";

/** Legacy env keys from mis-targeted festival-intelligence CI attempts. */
const LEGACY_WAREHOUSE_ENV_VARS = [
  "FESTIVAL_INTELLIGENCE_DUCKDB_PATH",
  "FESTIVAL_INTEL_DUCKDB_PATH",
] as const;

const LEGACY_WAREHOUSE_BASENAMES = new Set([
  "festival_intelligence.duckdb",
  "festival-intelligence.duckdb",
]);

/**
 * Resolve the DuckDB warehouse path with bloomberg-correct defaults.
 * Remaps legacy intelligence filenames/env vars that caused CI path failures.
 */
export function resolveWarehousePath(explicit?: string | null): string {
  let raw =
    explicit?.trim() ||
    process.env[WAREHOUSE_ENV_VAR]?.trim() ||
    undefined;

  if (!raw) {
    for (const key of LEGACY_WAREHOUSE_ENV_VARS) {
      const legacy = process.env[key]?.trim();
      if (legacy) {
        raw = legacy;
        break;
      }
    }
  }

  raw = raw || DEFAULT_WAREHOUSE_PATH;
  const base = raw.split(/[/\\]/).pop()?.toLowerCase() ?? "";
  if (LEGACY_WAREHOUSE_BASENAMES.has(base)) {
    const dir = dirname(raw);
    raw =
      dir === "."
        ? basename(DEFAULT_WAREHOUSE_PATH)
        : join(dir, basename(DEFAULT_WAREHOUSE_PATH));
  }
  return resolve(raw);
}

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

/** Canonical observation prepared by the source-neutral ingestion pipeline. */
export type CanonicalObservationInput = {
  observation: Observation;
  rawContent: string;
  canonicalUrl: string;
  normalizedContent: string;
  contentHash: string;
  dedupKey: string;
  winnerKey: string;
};

export type StoredCanonicalObservation = {
  observation: Observation;
  canonicalUrl: string;
  normalizedContent: string;
  dedupKey: string;
  firstSeenAt: string;
  lastSeenAt: string;
  seenCount: number;
};

export type IngestionLogContext = Pick<
  IngestionLog,
  | "id"
  | "runId"
  | "source"
  | "sourceRecordId"
  | "inputHash"
  | "metadata"
  | "createdAt"
  | "updatedAt"
>;

/**
 * Persistence contract used by ingestion adapters. Observation merge and the
 * corresponding success log are committed atomically by `commitObservation`.
 */
export type IngestionStore = {
  getRun(source: string, idempotencyKey: string): Promise<IngestionRun | null>;
  beginRun(run: IngestionRun): Promise<void>;
  finishRun(run: IngestionRun): Promise<void>;
  listLogs(runId: string): Promise<IngestionLog[]>;
  upsertLog(log: IngestionLog): Promise<void>;
  commitObservation(
    input: CanonicalObservationInput,
    log: IngestionLogContext,
  ): Promise<IngestionLog>;
  getCanonicalObservation(id: string): Promise<StoredCanonicalObservation | null>;
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
  canonical_url: string | null;
  raw_content: string | null;
  normalized_content: string | null;
  content_hash: string | null;
  dedup_key: string | null;
  retrieved_at: string;
  first_seen_at: string | null;
  last_seen_at: string | null;
  seen_count: number | null;
  winner_key: string | null;
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

type IngestionRunRow = {
  id: string;
  source: string;
  idempotency_key: string;
  request_hash: string;
  adapter_version: string;
  status: string;
  started_at: string;
  completed_at: string | null;
  attempted_count: number;
  inserted_count: number;
  duplicate_count: number;
  failed_count: number;
  error_code: string | null;
  error_message: string | null;
  metadata_json: string | null;
};

type IngestionLogRow = {
  id: string;
  run_id: string;
  source: string;
  source_record_id: string;
  input_hash: string;
  status: string;
  observation_id: string | null;
  canonical_url: string | null;
  content_hash: string | null;
  duplicate_of: string | null;
  error_code: string | null;
  error_message: string | null;
  metadata_json: string | null;
  created_at: string;
  updated_at: string;
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

function storedCanonicalObservationFromRow(
  row: ObservationRow,
): StoredCanonicalObservation {
  if (
    row.canonical_url === null ||
    row.normalized_content === null ||
    row.dedup_key === null ||
    row.first_seen_at === null ||
    row.last_seen_at === null
  ) {
    throw new Error(`Observation ${row.id} is not a canonical ingestion row`);
  }
  return {
    observation: observationFromRow(row),
    canonicalUrl: row.canonical_url,
    normalizedContent: row.normalized_content,
    dedupKey: row.dedup_key,
    firstSeenAt: iso(row.first_seen_at),
    lastSeenAt: iso(row.last_seen_at),
    seenCount: Number(row.seen_count ?? 1),
  };
}

function ingestionRunFromRow(row: IngestionRunRow): IngestionRun {
  return parseIngestionRun({
    id: row.id,
    source: row.source,
    idempotencyKey: row.idempotency_key,
    requestHash: row.request_hash,
    adapterVersion: row.adapter_version,
    status: row.status,
    startedAt: iso(row.started_at),
    ...(row.completed_at ? { completedAt: iso(row.completed_at) } : {}),
    attemptedCount: Number(row.attempted_count),
    insertedCount: Number(row.inserted_count),
    duplicateCount: Number(row.duplicate_count),
    failedCount: Number(row.failed_count),
    ...(row.error_code ? { errorCode: row.error_code } : {}),
    ...(row.error_message ? { errorMessage: row.error_message } : {}),
    metadata: JSON.parse(row.metadata_json ?? "{}"),
  });
}

function ingestionLogFromRow(row: IngestionLogRow): IngestionLog {
  return parseIngestionLog({
    id: row.id,
    runId: row.run_id,
    source: row.source,
    sourceRecordId: row.source_record_id,
    inputHash: row.input_hash,
    status: row.status,
    ...(row.observation_id ? { observationId: row.observation_id } : {}),
    ...(row.canonical_url ? { canonicalUrl: row.canonical_url } : {}),
    ...(row.content_hash ? { contentHash: row.content_hash } : {}),
    ...(row.duplicate_of ? { duplicateOf: row.duplicate_of } : {}),
    ...(row.error_code ? { errorCode: row.error_code } : {}),
    ...(row.error_message ? { errorMessage: row.error_message } : {}),
    metadata: JSON.parse(row.metadata_json ?? "{}"),
    createdAt: iso(row.created_at),
    updatedAt: iso(row.updated_at),
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
  readonly ingestion: IngestionStore;
  private readonly client: DuckDbClientLike | null;
  private ready: Promise<void>;
  private ingestionTail: Promise<void> = Promise.resolve();

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
        let canonicalUrl = parsed.url;
        try {
          canonicalUrl = canonicalizeUrl(parsed.url);
        } catch {
          // The legacy API accepts any Zod URL; strict web-only handling lives
          // in the canonical ingestion pipeline.
        }
        let normalized = normalizeText(rawContent);
        try {
          normalized = normalizedContent(parsed.payload);
        } catch {
          // Preserve compatibility for legacy non-JSON payload objects.
        }
        await this.client.run(
          `INSERT INTO observations (
            id, source_url, canonical_url, raw_content, normalized_content,
            content_hash, retrieved_at, first_seen_at, last_seen_at, seen_count,
            status, kind, festival_id, edition_id, source_domain, tier,
            evidence_json, payload_json
          ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
          ON CONFLICT (id) DO UPDATE SET
            source_url = EXCLUDED.source_url,
            canonical_url = EXCLUDED.canonical_url,
            raw_content = EXCLUDED.raw_content,
            normalized_content = EXCLUDED.normalized_content,
            content_hash = EXCLUDED.content_hash,
            retrieved_at = EXCLUDED.retrieved_at,
            first_seen_at = COALESCE(observations.first_seen_at, EXCLUDED.first_seen_at),
            last_seen_at = EXCLUDED.last_seen_at,
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
          canonicalUrl,
          rawContent,
          normalized,
          contentHash,
          parsed.observedAt,
          parsed.observedAt,
          parsed.observedAt,
          1,
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
          `SELECT * FROM observations
           WHERE festival_id = ?
           ORDER BY retrieved_at DESC, id
           LIMIT ?`,
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

    this.ingestion = {
      getRun: async (source, idempotencyKey) => {
        await this.ready;
        if (!this.client) return null;
        const rows = await this.client.all<IngestionRunRow>(
          `SELECT * FROM ingestion_runs
           WHERE source = ? AND idempotency_key = ?
           LIMIT 1`,
          source,
          idempotencyKey,
        );
        return rows[0] ? ingestionRunFromRow(rows[0]) : null;
      },
      beginRun: async (run) => {
        await this.ready;
        if (!this.client) throw new Error("duckdb_ingestion_unavailable");
        const parsed = parseIngestionRun(run);
        const client = this.client;
        await this.withIngestionLock(() =>
          Promise.resolve(client.run(
            `INSERT INTO ingestion_runs (
            id, source, idempotency_key, request_hash, adapter_version, status,
            started_at, completed_at, attempted_count, inserted_count,
            duplicate_count, failed_count, error_code, error_message, metadata_json
          ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
          ON CONFLICT (source, idempotency_key) DO UPDATE SET
            request_hash = EXCLUDED.request_hash,
            adapter_version = EXCLUDED.adapter_version,
            status = EXCLUDED.status,
            completed_at = NULL,
            error_code = NULL,
            error_message = NULL,
            metadata_json = EXCLUDED.metadata_json
          WHERE ingestion_runs.request_hash = EXCLUDED.request_hash
            AND ingestion_runs.adapter_version = EXCLUDED.adapter_version
            AND ingestion_runs.status <> 'succeeded'`,
            parsed.id,
            parsed.source,
            parsed.idempotencyKey,
            parsed.requestHash,
            parsed.adapterVersion,
            parsed.status,
            parsed.startedAt,
            parsed.completedAt ?? null,
            parsed.attemptedCount,
            parsed.insertedCount,
            parsed.duplicateCount,
            parsed.failedCount,
            parsed.errorCode ?? null,
            parsed.errorMessage ?? null,
            JSON.stringify(parsed.metadata),
          )),
        );
      },
      finishRun: async (run) => {
        await this.ready;
        if (!this.client) throw new Error("duckdb_ingestion_unavailable");
        const parsed = parseIngestionRun(run);
        const client = this.client;
        await this.withIngestionLock(() =>
          Promise.resolve(client.run(
            `UPDATE ingestion_runs SET
            status = ?, completed_at = ?, attempted_count = ?,
            inserted_count = ?, duplicate_count = ?, failed_count = ?,
            error_code = ?, error_message = ?, metadata_json = ?
           WHERE id = ?`,
            parsed.status,
            parsed.completedAt ?? null,
            parsed.attemptedCount,
            parsed.insertedCount,
            parsed.duplicateCount,
            parsed.failedCount,
            parsed.errorCode ?? null,
            parsed.errorMessage ?? null,
            JSON.stringify(parsed.metadata),
            parsed.id,
          )),
        );
      },
      listLogs: async (runId) => {
        await this.ready;
        if (!this.client) return [];
        const rows = await this.client.all<IngestionLogRow>(
          `SELECT * FROM ingestion_logs
           WHERE run_id = ?
           ORDER BY source_record_id, id`,
          runId,
        );
        return rows.map(ingestionLogFromRow);
      },
      upsertLog: async (log) => {
        await this.ready;
        if (!this.client) throw new Error("duckdb_ingestion_unavailable");
        await this.withIngestionLock(() =>
          this.writeIngestionLog(parseIngestionLog(log)),
        );
      },
      commitObservation: async (input, log) => {
        await this.ready;
        if (!this.client) throw new Error("duckdb_ingestion_unavailable");
        return this.withIngestionLock(() =>
          this.commitCanonicalObservation(input, log),
        );
      },
      getCanonicalObservation: async (id) => {
        await this.ready;
        if (!this.client) return null;
        const rows = await this.client.all<ObservationRow>(
          `SELECT * FROM observations WHERE id = ? LIMIT 1`,
          id,
        );
        const row = rows[0];
        if (!row?.dedup_key) return null;
        return storedCanonicalObservationFromRow(row);
      },
    };
  }

  private async withIngestionLock<T>(operation: () => Promise<T>): Promise<T> {
    const previous = this.ingestionTail;
    let release: () => void = () => undefined;
    this.ingestionTail = new Promise<void>((resolvePromise) => {
      release = resolvePromise;
    });
    await previous;
    try {
      return await operation();
    } finally {
      release();
    }
  }

  private async writeIngestionLog(log: IngestionLog): Promise<void> {
    if (!this.client) throw new Error("duckdb_ingestion_unavailable");
    await this.client.run(
      `INSERT INTO ingestion_logs (
        id, run_id, source, source_record_id, input_hash, status,
        observation_id, canonical_url, content_hash, duplicate_of,
        error_code, error_message, metadata_json, created_at, updated_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT (run_id, source_record_id) DO UPDATE SET
        input_hash = EXCLUDED.input_hash,
        status = EXCLUDED.status,
        observation_id = EXCLUDED.observation_id,
        canonical_url = EXCLUDED.canonical_url,
        content_hash = EXCLUDED.content_hash,
        duplicate_of = EXCLUDED.duplicate_of,
        error_code = EXCLUDED.error_code,
        error_message = EXCLUDED.error_message,
        metadata_json = EXCLUDED.metadata_json,
        updated_at = EXCLUDED.updated_at`,
      log.id,
      log.runId,
      log.source,
      log.sourceRecordId,
      log.inputHash,
      log.status,
      log.observationId ?? null,
      log.canonicalUrl ?? null,
      log.contentHash ?? null,
      log.duplicateOf ?? null,
      log.errorCode ?? null,
      log.errorMessage ?? null,
      JSON.stringify(log.metadata),
      log.createdAt,
      log.updatedAt,
    );
  }

  private async commitCanonicalObservation(
    input: CanonicalObservationInput,
    log: IngestionLogContext,
  ): Promise<IngestionLog> {
    if (!this.client) throw new Error("duckdb_ingestion_unavailable");
    const observation = parseObservation(input.observation);
    if (observation.contentHash !== input.contentHash) {
      throw new Error("canonical_observation_content_hash_mismatch");
    }

    await this.client.run("BEGIN TRANSACTION");
    try {
      const rows = await this.client.all<ObservationRow>(
        `SELECT * FROM observations WHERE dedup_key = ? LIMIT 1`,
        input.dedupKey,
      );
      const existing = rows[0];
      let status: "inserted" | "duplicate";
      let duplicateOf: string | undefined;

      if (!existing) {
        const payloadJson = JSON.stringify(observation.payload ?? null);
        await this.client.run(
          `INSERT INTO observations (
            id, source_url, canonical_url, raw_content, normalized_content,
            content_hash, dedup_key, retrieved_at, first_seen_at, last_seen_at,
            seen_count, winner_key, status, kind, festival_id, edition_id,
            source_domain, tier, evidence_json, payload_json
          ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
          ON CONFLICT (dedup_key) DO NOTHING`,
          observation.id,
          observation.url,
          input.canonicalUrl,
          input.rawContent,
          input.normalizedContent,
          input.contentHash,
          input.dedupKey,
          observation.observedAt,
          observation.observedAt,
          observation.observedAt,
          1,
          input.winnerKey,
          "ok",
          observation.kind,
          observation.festivalId ?? null,
          observation.editionId ?? null,
          observation.sourceDomain,
          observation.tier ?? null,
          JSON.stringify(observation.evidence),
          payloadJson,
        );
        const inserted = await this.client.all<ObservationRow>(
          `SELECT * FROM observations WHERE dedup_key = ? LIMIT 1`,
          input.dedupKey,
        );
        if (!inserted[0]) throw new Error("canonical_observation_insert_failed");
        status = inserted[0].id === observation.id ? "inserted" : "duplicate";
        duplicateOf = status === "duplicate" ? inserted[0].id : undefined;
      } else {
        const existingObservation = observationFromRow(existing);
        const evidence = mergeEvidence(
          existingObservation.evidence,
          observation.evidence,
        );
        const incomingWins =
          !existing.winner_key || input.winnerKey < existing.winner_key;
        const winner = incomingWins ? observation : existingObservation;
        const payloadJson = incomingWins
          ? JSON.stringify(observation.payload ?? null)
          : (existing.payload_json ?? "null");
        const rawContent = incomingWins
          ? input.rawContent
          : existing.raw_content;
        const existingFirstSeen = iso(
          existing.first_seen_at ?? existing.retrieved_at,
        );
        const existingLastSeen = iso(
          existing.last_seen_at ?? existing.retrieved_at,
        );
        const firstSeenAt =
          Date.parse(existingFirstSeen) <= Date.parse(observation.observedAt)
            ? existingFirstSeen
            : observation.observedAt;
        const lastSeenAt =
          Date.parse(existingLastSeen) >= Date.parse(observation.observedAt)
            ? existingLastSeen
            : observation.observedAt;

        await this.client.run(
          `UPDATE observations SET
            source_url = ?, canonical_url = ?, raw_content = ?,
            normalized_content = ?, content_hash = ?, retrieved_at = ?,
            first_seen_at = ?, last_seen_at = ?, seen_count = ?,
            winner_key = ?, status = 'ok', kind = ?, festival_id = ?,
            edition_id = ?, source_domain = ?, tier = ?, evidence_json = ?,
            payload_json = ?
           WHERE dedup_key = ?`,
          winner.url,
          incomingWins ? input.canonicalUrl : existing.canonical_url,
          rawContent,
          incomingWins ? input.normalizedContent : existing.normalized_content,
          input.contentHash,
          winner.observedAt,
          firstSeenAt,
          lastSeenAt,
          Number(existing.seen_count ?? 1) + 1,
          incomingWins ? input.winnerKey : existing.winner_key,
          winner.kind,
          winner.festivalId ?? null,
          winner.editionId ?? null,
          winner.sourceDomain,
          winner.tier ?? null,
          JSON.stringify(evidence),
          payloadJson,
          input.dedupKey,
        );
        status = "duplicate";
        duplicateOf = existing.id;
      }

      const committedLog = parseIngestionLog({
        ...log,
        status,
        observationId: observation.id,
        canonicalUrl: input.canonicalUrl,
        contentHash: input.contentHash,
        duplicateOf,
      });
      await this.writeIngestionLog(committedLog);
      await this.client.run("COMMIT");
      return committedLog;
    } catch (error) {
      try {
        await this.client.run("ROLLBACK");
      } catch {
        // Preserve the original storage error.
      }
      throw error;
    }
  }

  get available(): boolean {
    return this.client != null;
  }

  async close(): Promise<void> {
    await this.ready;
    // Explicitly checkpoint before closing. The legacy Node binding can retain
    // recently committed pages across rapid close/reopen cycles in test/worker
    // processes unless a checkpoint is requested.
    if (this.client) await this.client.run("CHECKPOINT");
    await this.client?.close?.();
  }

  /** Idempotent schema creation / migration. */
  private async migrate(): Promise<void> {
    if (!this.client) return;
    for (const statement of loadCanonicalDuckDbStatements()) {
      await this.client.run(statement);
    }
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
  const path = resolveWarehousePath(opts.path);
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
