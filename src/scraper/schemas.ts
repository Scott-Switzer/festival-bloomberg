/**
 * Strongly typed Zod schemas for scraper observations, lineups, artists,
 * evidence coordinates, and cost/telemetry events.
 */
import { z } from "zod";

export const EvidenceCoordinateSchema = z.object({
  url: z.string().url(),
  selector: z.string().optional(),
  xpath: z.string().optional(),
  jsonPath: z.string().optional(),
  charStart: z.number().int().nonnegative().optional(),
  charEnd: z.number().int().nonnegative().optional(),
  snippet: z.string().max(2000).optional(),
  fetchedAt: z.string().datetime(),
});
export type EvidenceCoordinate = z.infer<typeof EvidenceCoordinateSchema>;

export const ArtistSchema = z.object({
  id: z.string().min(1),
  name: z.string().min(1),
  aliases: z.array(z.string()).default([]),
  genres: z.array(z.string()).default([]),
  externalIds: z
    .record(z.string(), z.string())
    .default({})
    .describe("e.g. { spotify, musicbrainz, bandsintown }"),
});
export type Artist = z.infer<typeof ArtistSchema>;

export const LineupSlotSchema = z.object({
  artistId: z.string().min(1),
  artistName: z.string().min(1),
  stage: z.string().optional(),
  day: z.string().optional(),
  startTime: z.string().datetime().optional(),
  endTime: z.string().datetime().optional(),
  billingOrder: z.number().int().nonnegative().optional(),
  evidence: z.array(EvidenceCoordinateSchema).default([]),
});
export type LineupSlot = z.infer<typeof LineupSlotSchema>;

export const LineupSchema = z.object({
  festivalId: z.string().min(1),
  editionId: z.string().min(1),
  announcedAt: z.string().datetime().optional(),
  slots: z.array(LineupSlotSchema).default([]),
  sourceDomain: z.string().min(1),
  confidence: z.number().min(0).max(1).default(0.5),
});
export type Lineup = z.infer<typeof LineupSchema>;

export const ObservationKindSchema = z.enum([
  "lineup",
  "artist",
  "venue",
  "schedule",
  "ticket",
  "meta",
  "raw_html",
]);
export type ObservationKind = z.infer<typeof ObservationKindSchema>;

export const ObservationSchema = z.object({
  id: z.string().min(1),
  kind: ObservationKindSchema,
  festivalId: z.string().optional(),
  editionId: z.string().optional(),
  sourceDomain: z.string().min(1),
  url: z.string().url(),
  observedAt: z.string().datetime(),
  payload: z.unknown(),
  evidence: z.array(EvidenceCoordinateSchema).default([]),
  tier: z
    .enum(["fresh_cache", "local_http", "playwright", "monid", "apify"])
    .optional(),
  contentHash: z.string().optional(),
});
export type Observation = z.infer<typeof ObservationSchema>;

/**
 * Source-neutral record emitted by ingestion adapters before canonicalization.
 * `subjectKey` can deliberately merge equivalent records across distinct URLs;
 * without it, the canonical URL is part of the deterministic deduplication key.
 */
export const IngestionRecordSchema = z.object({
  kind: ObservationKindSchema,
  festivalId: z.string().min(1).optional(),
  editionId: z.string().min(1).optional(),
  url: z.string().url(),
  observedAt: z.string().datetime(),
  payload: z.unknown(),
  evidence: z.array(EvidenceCoordinateSchema).default([]),
  tier: z
    .enum(["fresh_cache", "local_http", "playwright", "monid", "apify"])
    .optional(),
  deduplicationText: z.string().optional(),
  subjectKey: z.string().min(1).optional(),
  metadata: z.record(z.string(), z.unknown()).default({}),
});
export type IngestionRecord = z.infer<typeof IngestionRecordSchema>;

export const IngestionRunStatusSchema = z.enum([
  "running",
  "succeeded",
  "partial",
  "failed",
]);
export type IngestionRunStatus = z.infer<typeof IngestionRunStatusSchema>;

export const IngestionLogStatusSchema = z.enum([
  "inserted",
  "duplicate",
  "failed",
  "skipped",
]);
export type IngestionLogStatus = z.infer<typeof IngestionLogStatusSchema>;

export const IngestionRunSchema = z.object({
  id: z.string().min(1),
  source: z.string().min(1),
  idempotencyKey: z.string().min(1),
  requestHash: z.string().length(64),
  adapterVersion: z.string().min(1),
  status: IngestionRunStatusSchema,
  startedAt: z.string().datetime(),
  completedAt: z.string().datetime().optional(),
  attemptedCount: z.number().int().nonnegative().default(0),
  insertedCount: z.number().int().nonnegative().default(0),
  duplicateCount: z.number().int().nonnegative().default(0),
  failedCount: z.number().int().nonnegative().default(0),
  errorCode: z.string().optional(),
  errorMessage: z.string().optional(),
  metadata: z.record(z.string(), z.unknown()).default({}),
});
export type IngestionRun = z.infer<typeof IngestionRunSchema>;

export const IngestionLogSchema = z.object({
  id: z.string().min(1),
  runId: z.string().min(1),
  source: z.string().min(1),
  sourceRecordId: z.string().min(1),
  inputHash: z.string().length(64),
  status: IngestionLogStatusSchema,
  observationId: z.string().optional(),
  canonicalUrl: z.string().url().optional(),
  contentHash: z.string().length(64).optional(),
  duplicateOf: z.string().optional(),
  errorCode: z.string().optional(),
  errorMessage: z.string().optional(),
  metadata: z.record(z.string(), z.unknown()).default({}),
  createdAt: z.string().datetime(),
  updatedAt: z.string().datetime(),
});
export type IngestionLog = z.infer<typeof IngestionLogSchema>;

export const CostEventSchema = z.object({
  id: z.string().min(1),
  provider: z.enum(["local", "monid", "apify", "playwright", "cache"]),
  operation: z.string().min(1),
  units: z.number().nonnegative().default(1),
  unitCostUsd: z.number().nonnegative().default(0),
  totalCostUsd: z.number().nonnegative().default(0),
  currency: z.literal("USD").default("USD"),
  at: z.string().datetime(),
  meta: z.record(z.string(), z.unknown()).default({}),
});
export type CostEvent = z.infer<typeof CostEventSchema>;

export const TelemetryEventSchema = z.object({
  id: z.string().min(1),
  name: z.string().min(1),
  level: z.enum(["debug", "info", "warn", "error"]).default("info"),
  at: z.string().datetime(),
  durationMs: z.number().nonnegative().optional(),
  domain: z.string().optional(),
  url: z.string().url().optional(),
  tier: z
    .enum(["fresh_cache", "local_http", "playwright", "monid", "apify"])
    .optional(),
  ok: z.boolean().optional(),
  errorCode: z.string().optional(),
  meta: z.record(z.string(), z.unknown()).default({}),
});
export type TelemetryEvent = z.infer<typeof TelemetryEventSchema>;

export function parseObservation(input: unknown): Observation {
  return ObservationSchema.parse(input);
}

export function parseIngestionRecord(input: unknown): IngestionRecord {
  return IngestionRecordSchema.parse(input);
}

export function parseIngestionRun(input: unknown): IngestionRun {
  return IngestionRunSchema.parse(input);
}

export function parseIngestionLog(input: unknown): IngestionLog {
  return IngestionLogSchema.parse(input);
}

export function parseLineup(input: unknown): Lineup {
  return LineupSchema.parse(input);
}

export function parseCostEvent(input: unknown): CostEvent {
  return CostEventSchema.parse(input);
}

export function parseTelemetryEvent(input: unknown): TelemetryEvent {
  return TelemetryEventSchema.parse(input);
}
