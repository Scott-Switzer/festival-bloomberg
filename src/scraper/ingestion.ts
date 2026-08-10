/**
 * Reusable source adapter and ingestion orchestration.
 *
 * Callers provide stable source record IDs and an idempotency key. The pipeline
 * validates adapter output, canonicalizes it, atomically persists each
 * observation/log pair, resumes identical failed runs, and short-circuits an
 * identical successful replay.
 */
import {
  IngestionRecordSchema,
  IngestionRunSchema,
  effectiveObservationTime,
  type IngestionLog,
  type IngestionRecord,
  type IngestionRun,
  type Observation,
} from "./schemas";
import type {
  CanonicalObservationInput,
  IngestionStore,
} from "./db";
import {
  DEDUP_POLICY_VERSION,
  NORMALIZATION_VERSION,
  URL_POLICY_VERSION,
  canonicalJson,
  canonicalizeUrl,
  mergeEvidence,
  normalizeJson,
  normalizeText,
  normalizedContent,
  stableHash,
  type JsonValue,
} from "./normalization";

export type IngestionAdapterContext = {
  source: string;
  sourceRecordId: string;
  inputHash: string;
};

export type IngestionSourceAdapter<Input> = {
  /** Stable source name used in audit and idempotency keys. */
  readonly source: string;
  /** Increment when adapter mapping semantics change. */
  readonly version: string;
  /** Stable and unique within one request; never derive this from array position. */
  sourceRecordId(input: Input): string;
  adapt(
    input: Input,
    context: IngestionAdapterContext,
  ): IngestionRecord | Promise<IngestionRecord>;
};

export type IngestionOptions = {
  /** Idempotent within an adapter source and versioned request payload. */
  idempotencyKey: string;
  metadata?: Record<string, unknown>;
};

export type IngestionResult = {
  run: IngestionRun;
  logs: IngestionLog[];
  replayed: boolean;
};

export class IdempotencyConflictError extends Error {
  constructor(source: string, idempotencyKey: string) {
    super(
      `Idempotency key ${JSON.stringify(idempotencyKey)} for ${JSON.stringify(
        source,
      )} was reused with different input or adapter version`,
    );
    this.name = "IdempotencyConflictError";
  }
}

type InputDescriptor<Input> = {
  input: Input;
  sourceRecordId: string;
  inputHash: string;
};

const STORE_LOCKS = new WeakMap<object, Promise<void>>();

async function withStoreLock<T>(
  store: object,
  operation: () => Promise<T>,
): Promise<T> {
  const previous = STORE_LOCKS.get(store) ?? Promise.resolve();
  let release: () => void = () => undefined;
  STORE_LOCKS.set(
    store,
    new Promise<void>((resolvePromise) => {
      release = resolvePromise;
    }),
  );
  await previous;
  try {
    return await operation();
  } finally {
    release();
  }
}

function compareStrings(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}

function asMetadata(value: unknown): Record<string, unknown> {
  const normalized = normalizeJson(value);
  if (normalized === null || Array.isArray(normalized) || typeof normalized !== "object") {
    throw new TypeError("Ingestion metadata must be a JSON object");
  }
  return normalized;
}

function errorMessage(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  return message
    .replace(/\bBearer\s+\S+/giu, "Bearer [redacted]")
    .replace(
      /([?&](?:access_token|api[_-]?key|key|token)=)[^&\s]+/giu,
      "$1[redacted]",
    )
    .replace(/(https?:\/\/)[^/@\s]+@/giu, "$1[redacted]@")
    .slice(0, 2_000);
}

function rawContent(payload: unknown): string {
  if (typeof payload === "string") return payload;
  const serialized = JSON.stringify(payload);
  if (serialized === undefined) {
    throw new TypeError("Ingestion payload must be JSON-compatible");
  }
  return serialized;
}

function canonicalObservation(
  record: IngestionRecord,
  context: IngestionAdapterContext,
): CanonicalObservationInput {
  const canonicalUrl = canonicalizeUrl(record.url);
  const observedAt = new Date(record.observedAt).toISOString();
  const publishedAt = record.publishedAt
    ? new Date(record.publishedAt).toISOString()
    : undefined;
  const effectiveAt = effectiveObservationTime({
    observedAt,
    ...(publishedAt ? { publishedAt } : {}),
  });
  const normalizedPayload = normalizeJson(record.payload);
  const content = normalizedContent(record.payload, record.deduplicationText);
  const contentHash = stableHash(
    `content-${NORMALIZATION_VERSION}`,
    content,
  );
  const subject = record.subjectKey
    ? normalizeText(record.subjectKey)
    : canonicalUrl;
  const dedupKey = stableHash(
    DEDUP_POLICY_VERSION,
    record.kind,
    record.festivalId ?? null,
    record.editionId ?? null,
    subject,
    contentHash,
  );
  const observationId = `obs_${dedupKey}`;
  const evidence = mergeEvidence(record.evidence);
  const winnerKey = canonicalJson([
    effectiveAt,
    canonicalUrl,
    context.source,
    context.sourceRecordId,
    context.inputHash,
  ]);

  return {
    observation: {
      id: observationId,
      kind: record.kind,
      festivalId: record.festivalId,
      editionId: record.editionId,
      sourceDomain: new URL(canonicalUrl).hostname,
      url: canonicalUrl,
      observedAt,
      ...(publishedAt ? { publishedAt } : {}),
      ...(record.publishedAtPrecision
        ? { publishedAtPrecision: record.publishedAtPrecision }
        : {}),
      payload: normalizedPayload,
      evidence,
      tier: record.tier,
      contentHash,
    },
    rawContent: rawContent(record.payload),
    canonicalUrl,
    normalizedContent: content,
    contentHash,
    dedupKey,
    winnerKey,
  };
}

/**
 * Adapter for existing scraper `Observation` values. The old observation ID is
 * treated only as the source record ID; canonical ingestion derives a stable ID.
 */
export function createObservationIngestionAdapter(
  source: string,
  version = "1",
): IngestionSourceAdapter<Observation> {
  return {
    source,
    version,
    sourceRecordId: (observation) => observation.id,
    adapt: (observation) => ({
      kind: observation.kind,
      festivalId: observation.festivalId,
      editionId: observation.editionId,
      url: observation.url,
      observedAt: observation.observedAt,
      ...(observation.publishedAt
        ? { publishedAt: observation.publishedAt }
        : {}),
      ...(observation.publishedAtPrecision
        ? { publishedAtPrecision: observation.publishedAtPrecision }
        : {}),
      payload: observation.payload,
      evidence: observation.evidence,
      tier: observation.tier,
      metadata: {
        legacyObservationId: observation.id,
        sourceDomain: observation.sourceDomain,
        ...(observation.contentHash
          ? { suppliedContentHash: observation.contentHash }
          : {}),
      },
    }),
  };
}

export class IngestionPipeline {
  private readonly store: IngestionStore;
  private readonly now: () => Date;

  constructor(store: IngestionStore, options: { now?: () => Date } = {}) {
    this.store = store;
    this.now = options.now ?? (() => new Date());
  }

  async ingest<Input>(
    adapter: IngestionSourceAdapter<Input>,
    values: Iterable<Input> | AsyncIterable<Input>,
    options: IngestionOptions,
  ): Promise<IngestionResult> {
    return withStoreLock(this.store, () =>
      this.ingestUnlocked(adapter, values, options),
    );
  }

  private async ingestUnlocked<Input>(
    adapter: IngestionSourceAdapter<Input>,
    values: Iterable<Input> | AsyncIterable<Input>,
    options: IngestionOptions,
  ): Promise<IngestionResult> {
    const source = adapter.source.trim();
    const adapterVersion = adapter.version.trim();
    const idempotencyKey = options.idempotencyKey.trim();
    if (!source || !adapterVersion || !idempotencyKey) {
      throw new TypeError(
        "Adapter source, adapter version, and idempotency key are required",
      );
    }

    const descriptors: InputDescriptor<Input>[] = [];
    for await (const input of values) {
      const sourceRecordId = adapter.sourceRecordId(input).trim();
      if (!sourceRecordId) {
        throw new TypeError("Adapter sourceRecordId must not be empty");
      }
      descriptors.push({
        input,
        sourceRecordId,
        inputHash: stableHash(
          "ingestion-input-v1",
          normalizeJson(input) as JsonValue,
        ),
      });
    }
    descriptors.sort((left, right) =>
      compareStrings(left.sourceRecordId, right.sourceRecordId),
    );
    for (let index = 1; index < descriptors.length; index += 1) {
      if (
        descriptors[index - 1].sourceRecordId ===
        descriptors[index].sourceRecordId
      ) {
        throw new TypeError(
          `Duplicate source record ID: ${descriptors[index].sourceRecordId}`,
        );
      }
    }

    const requestHash = stableHash(
      "ingestion-request-v1",
      source,
      adapterVersion,
      descriptors.map(({ sourceRecordId, inputHash }) => [
        sourceRecordId,
        inputHash,
      ]),
    );
    const runId = `ing_${stableHash(
      "ingestion-run-v1",
      source,
      idempotencyKey,
    )}`;
    const existingRun = await this.store.getRun(source, idempotencyKey);
    if (
      existingRun &&
      (existingRun.requestHash !== requestHash ||
        existingRun.adapterVersion !== adapterVersion)
    ) {
      throw new IdempotencyConflictError(source, idempotencyKey);
    }
    if (existingRun?.status === "succeeded") {
      return {
        run: existingRun,
        logs: await this.store.listLogs(existingRun.id),
        replayed: true,
      };
    }

    const startedAt = existingRun?.startedAt ?? this.now().toISOString();
    const run = IngestionRunSchema.parse({
      id: runId,
      source,
      idempotencyKey,
      requestHash,
      adapterVersion,
      status: "running",
      startedAt,
      attemptedCount: descriptors.length,
      insertedCount: 0,
      duplicateCount: 0,
      failedCount: 0,
      metadata: asMetadata(options.metadata ?? {}),
    });
    await this.store.beginRun(run);
    const claimedRun = await this.store.getRun(source, idempotencyKey);
    if (
      !claimedRun ||
      claimedRun.requestHash !== requestHash ||
      claimedRun.adapterVersion !== adapterVersion ||
      claimedRun.id !== run.id
    ) {
      throw new IdempotencyConflictError(source, idempotencyKey);
    }
    if (claimedRun.status === "succeeded") {
      return {
        run: claimedRun,
        logs: await this.store.listLogs(claimedRun.id),
        replayed: true,
      };
    }

    const previousLogs = new Map(
      (await this.store.listLogs(run.id)).map((log) => [
        log.sourceRecordId,
        log,
      ]),
    );
    for (const descriptor of descriptors) {
      const previous = previousLogs.get(descriptor.sourceRecordId);
      if (
        previous?.inputHash === descriptor.inputHash &&
        (previous.status === "inserted" || previous.status === "duplicate")
      ) {
        continue;
      }

      const context: IngestionAdapterContext = {
        source,
        sourceRecordId: descriptor.sourceRecordId,
        inputHash: descriptor.inputHash,
      };
      const updatedAt = this.now().toISOString();
      const logBase = {
        id: `log_${stableHash(
          "ingestion-log-v1",
          run.id,
          descriptor.sourceRecordId,
        )}`,
        runId: run.id,
        source,
        sourceRecordId: descriptor.sourceRecordId,
        inputHash: descriptor.inputHash,
        metadata: {} as Record<string, unknown>,
        createdAt: previous?.createdAt ?? updatedAt,
        updatedAt,
      };

      let phase: "adapter" | "normalization" | "persistence" = "adapter";
      let record: IngestionRecord | undefined;
      let canonical: CanonicalObservationInput | undefined;
      let logMetadata: Record<string, unknown> = {};
      try {
        record = IngestionRecordSchema.parse(
          await adapter.adapt(descriptor.input, context),
        );
        phase = "normalization";
        logMetadata = asMetadata(record.metadata);
        canonical = canonicalObservation(record, context);
        phase = "persistence";
        await this.store.commitObservation(canonical, {
          ...logBase,
          metadata: logMetadata,
        });
      } catch (error) {
        let failureUrl = canonical?.canonicalUrl;
        if (!failureUrl && record) {
          try {
            failureUrl = canonicalizeUrl(record.url);
          } catch {
            // Invalid URLs are represented by the error fields.
          }
        }
        const failure: IngestionLog = {
          ...logBase,
          metadata: logMetadata,
          status: "failed",
          ...(failureUrl ? { canonicalUrl: failureUrl } : {}),
          ...(canonical ? { contentHash: canonical.contentHash } : {}),
          errorCode: `${phase}_error`,
          errorMessage: errorMessage(error),
        };
        await this.store.upsertLog(failure);
      }
    }

    const logs = await this.store.listLogs(run.id);
    const insertedCount = logs.filter((log) => log.status === "inserted").length;
    const duplicateCount = logs.filter((log) => log.status === "duplicate").length;
    const failedCount = logs.filter((log) => log.status === "failed").length;
    const successfulCount = insertedCount + duplicateCount;
    const status =
      failedCount === 0
        ? "succeeded"
        : successfulCount === 0
          ? "failed"
          : "partial";
    const completedRun = IngestionRunSchema.parse({
      ...run,
      status,
      completedAt: this.now().toISOString(),
      insertedCount,
      duplicateCount,
      failedCount,
      ...(failedCount > 0
        ? {
            errorCode: "item_failures",
            errorMessage: `${failedCount} ingestion item(s) failed`,
          }
        : {}),
    });
    await this.store.finishRun(completedRun);
    return { run: completedRun, logs, replayed: false };
  }
}

export const INGESTION_POLICY_VERSIONS = {
  normalization: NORMALIZATION_VERSION,
  url: URL_POLICY_VERSION,
  deduplication: DEDUP_POLICY_VERSION,
} as const;
