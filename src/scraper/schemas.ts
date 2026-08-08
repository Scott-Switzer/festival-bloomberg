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

export function parseLineup(input: unknown): Lineup {
  return LineupSchema.parse(input);
}

export function parseCostEvent(input: unknown): CostEvent {
  return CostEventSchema.parse(input);
}

export function parseTelemetryEvent(input: unknown): TelemetryEvent {
  return TelemetryEventSchema.parse(input);
}
