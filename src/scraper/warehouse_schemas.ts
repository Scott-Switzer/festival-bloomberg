/**
 * Zod contracts for core warehouse tables used by the ingestion runner.
 * Keys mirror `schema/duckdb.sql` (snake_case) for direct DuckDB writes.
 */
import { z } from "zod";

const IsoDateTimeSchema = z.string().datetime({ offset: true });
const UrlSchema = z.string().url();

export const BillingTierSchema = z.enum([
  "headliner",
  "sub_headliner",
  "main_stage",
  "secondary",
  "emerging",
  "unknown",
]);

export const SourceSystemSchema = z.enum([
  "musicbrainz",
  "wikidata",
  "wikipedia",
  "wikimedia",
  "setlistfm",
  "ticketmaster",
  "youtube",
  "gdelt",
  "hackernews",
  "rss",
  "lastfm",
  "discogs",
  "spotify",
  "songkick",
  "bandsintown",
  "edmtrain",
  "nws",
  "noaa_ncei",
  "bts",
  "census",
  "bea",
  "bls",
  "openstreetmap",
  "official_site",
  "festival_site",
  "press_release",
  "manual",
  "scraper",
]);

export const ExtractionMethodSchema = z.enum([
  "html_selector",
  "structured_data",
  "json_ld",
  "api",
  "ocr",
  "llm",
  "heuristic",
  "manual",
]);

export const ResolutionStatusSchema = z.enum([
  "unresolved",
  "auto_resolved",
  "manual_resolved",
  "rejected",
]);

const EvidenceSchema = z.object({
  url: UrlSchema,
  selector: z.string().optional(),
  snippet: z.string().optional(),
  extraction_method: ExtractionMethodSchema.optional(),
  retrieved_at: IsoDateTimeSchema,
  confidence: z.number().min(0).max(1).optional(),
});

const provenanceFields = {
  evidence: z.array(EvidenceSchema).default([]),
  evidence_url: UrlSchema.optional(),
  extraction_confidence: z.number().min(0).max(1).optional(),
  extraction_method: ExtractionMethodSchema.optional(),
  source_system: SourceSystemSchema.optional(),
  source_url: UrlSchema.optional(),
  source_retrieved_at: IsoDateTimeSchema.optional(),
};

const resolutionFields = {
  match_confidence: z.number().min(0).max(1).optional(),
  match_method: z.string().optional(),
  manually_reviewed: z.boolean().default(false),
};

export const ArtistSpecSchema = z.object({
  artist_key: z.string().min(1).optional(),
  musicbrainz_id: z.string().optional(),
  spotify_id: z.string().optional(),
  name: z.string().min(1),
  normalized_name: z.string().min(1),
  aliases: z.array(z.unknown()).default([]),
  members: z.array(z.unknown()).default([]),
  labels: z.array(z.unknown()).default([]),
  genres: z.array(z.string()).default([]),
  subgenres: z.array(z.string()).default([]),
  tags: z.array(z.string()).default([]),
  popularity_score: z.number().optional(),
  spotify_popularity: z.number().optional(),
  spotify_followers: z.number().optional(),
  listener_countries: z.array(z.string()).default([]),
  official_domains: z.array(z.string()).default([]),
  social_handles: z.array(z.unknown()).default([]),
  external_ids: z.record(z.string()).default({}),
  ...provenanceFields,
  ...resolutionFields,
  resolution_status: ResolutionStatusSchema.default("unresolved"),
  ingested_at: IsoDateTimeSchema.optional(),
  updated_at: IsoDateTimeSchema.optional(),
});

export const FestivalSpecSchema = z.object({
  festival_key: z.string().min(1).optional(),
  name: z.string().min(1),
  normalized_name: z.string().min(1),
  aliases: z.array(z.unknown()).default([]),
  organizers: z.array(z.string()).default([]),
  promoters: z.array(z.string()).default([]),
  genre_focus: z.array(z.string()).default([]),
  subgenre_focus: z.array(z.string()).default([]),
  stages: z.array(z.unknown()).default([]),
  ticket_tiers: z.array(z.unknown()).default([]),
  lineup_announcements: z.array(z.unknown()).default([]),
  social_handles: z.array(z.unknown()).default([]),
  historical_editions: z.array(z.unknown()).default([]),
  official_website: UrlSchema.optional(),
  official_domains: z.array(z.string()).default([]),
  external_ids: z.record(z.string()).default({}),
  ...provenanceFields,
  ingested_at: IsoDateTimeSchema.optional(),
  updated_at: IsoDateTimeSchema.optional(),
});

export const FestivalEditionSchema = z.object({
  edition_key: z.string().min(1).optional(),
  festival_key: z.string().min(1).optional(),
  year: z.number().int(),
  total_artists: z.number().int().nonnegative().optional(),
  ticket_tiers: z.array(z.unknown()).default([]),
  lineup_announcements: z.array(z.unknown()).default([]),
  evidence: z.array(EvidenceSchema).default([]),
  evidence_url: UrlSchema.optional(),
  extraction_confidence: z.number().min(0).max(1).optional(),
  source_system: SourceSystemSchema.optional(),
  source_url: UrlSchema.optional(),
  source_retrieved_at: IsoDateTimeSchema.optional(),
  ingested_at: IsoDateTimeSchema.optional(),
  updated_at: IsoDateTimeSchema.optional(),
});

export const LineupSlotSchema = z.object({
  slot_key: z.string().min(1).optional(),
  festival_key: z.string().min(1).optional(),
  edition_key: z.string().optional(),
  year: z.number().int().optional(),
  artist_key: z.string().optional(),
  artist_name: z.string().min(1),
  normalized_artist_name: z.string().optional(),
  musicbrainz_id: z.string().optional(),
  billing_order: z.number().int().nonnegative().optional(),
  billing_tier: BillingTierSchema.optional(),
  collaborators: z.array(z.string()).default([]),
  subgenres: z.array(z.string()).default([]),
  ...provenanceFields,
  evidence_snippet: z.string().optional(),
  parser_version: z.string().optional(),
  ...resolutionFields,
  ingested_at: IsoDateTimeSchema.optional(),
  updated_at: IsoDateTimeSchema.optional(),
});

export const LineupObservationSchema = z.object({
  observation_key: z.string().min(1).optional(),
  festival_key: z.string().optional(),
  festival_name: z.string().optional(),
  edition_year: z.number().int().optional(),
  artist_name: z.string().min(1),
  normalized_artist_name: z.string().optional(),
  billing_order: z.number().int().nonnegative().optional(),
  billing_tier: BillingTierSchema.optional(),
  source_url: UrlSchema.optional(),
  source_system: SourceSystemSchema.optional(),
  source_retrieved_at: IsoDateTimeSchema.optional(),
  extraction_method: ExtractionMethodSchema.optional(),
  extraction_confidence: z.number().min(0).max(1).optional(),
  observed_raw: z.record(z.unknown()).default({}),
  ingested_at: IsoDateTimeSchema.optional(),
});

export const ArtistContactRowSchema = z.object({
  contact_key: z.string().min(1).optional(),
  artist_key: z.string().min(1),
  agency_name: z.string().optional(),
  agent_name: z.string().optional(),
  contact_email: z.string().email().optional(),
  contact_phone: z.string().optional(),
  role: z.string().optional(),
  verified: z.boolean().optional(),
  source_url: UrlSchema.optional(),
  retrieved_at: IsoDateTimeSchema.optional(),
  source_system: SourceSystemSchema.optional(),
  evidence_url: UrlSchema.optional(),
  confidence: z.number().min(0).max(1).optional(),
  ingested_at: IsoDateTimeSchema.optional(),
});

export const LineupQualificationMetricsSchema = z.object({
  metric_key: z.string().min(1).optional(),
  artist_key: z.string().min(1),
  festival_edition_key: z.string().optional(),
  billing_tier: z.number().int().optional(),
  billing_order: z.number().int().optional(),
  stage_name: z.string().optional(),
  time_slot_minutes: z.number().int().positive().optional(),
  is_headliner: z.boolean().optional(),
  repeat_booking_count: z.number().int().nonnegative().optional(),
  sentiment_score_pre_festival: z.number().min(-1).max(1).optional(),
  source_system: SourceSystemSchema.optional(),
  evidence_url: UrlSchema.optional(),
  confidence: z.number().min(0).max(1).optional(),
  ingested_at: IsoDateTimeSchema.optional(),
});
