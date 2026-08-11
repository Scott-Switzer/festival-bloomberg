/**
 * Zod data contracts for the Festival Intelligence scrapers.
 *
 * Every "row" schema in this module mirrors, key for key, a table in
 * `schema/duckdb.sql`. Scalar SQL columns are top-level keys with the same
 * name; SQL `JSON` columns hold the nested descriptor schemas defined here.
 * That parity is enforced by `src/scraper/schemas.parity.test.ts`, so a column
 * cannot be added on one side without the other.
 *
 * Naming follows the warehouse (snake_case) rather than TypeScript convention
 * so that a validated object can be handed straight to the DuckDB writer and
 * to the Pydantic contracts in `contracts/entities.py`.
 */
import { z } from 'zod';

// ===========================================================================
// Primitives
// ===========================================================================

/** ISO-8601 calendar date, e.g. `2026-06-24`. */
export const IsoDateSchema = z.iso.date();

/** ISO-8601 timestamp; offsets and bare local timestamps are both accepted. */
export const IsoDateTimeSchema = z.iso.datetime({ offset: true, local: true });

/** ISO-8601 wall-clock time, e.g. `21:30:00`. */
export const IsoTimeSchema = z.iso.time();

/** Score in the closed interval [0, 1]. */
export const ConfidenceSchema = z.number().min(0).max(1);

/** MusicBrainz identifier (a UUID, matched leniently). */
export const MusicBrainzIdSchema = z
  .string()
  .regex(
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i,
    'must be a MusicBrainz UUID',
  );

/** Wikidata QID, e.g. `Q483203`. */
export const WikidataIdSchema = z.string().regex(/^Q[1-9]\d*$/, 'must be a Wikidata QID');

/** Spotify base-62 identifier. */
export const SpotifyIdSchema = z
  .string()
  .regex(/^[A-Za-z0-9]{22}$/, 'must be a 22-character Spotify id');

/** ISO-3166-1 alpha-2 country code, uppercased on parse. */
export const CountryCodeSchema = z
  .string()
  .trim()
  .transform((value) => value.toUpperCase())
  .pipe(z.string().regex(/^[A-Z]{2}$/, 'must be an ISO-3166-1 alpha-2 code'));

/** ISO-4217 currency code, uppercased on parse. */
export const CurrencyCodeSchema = z
  .string()
  .trim()
  .transform((value) => value.toUpperCase())
  .pipe(z.string().regex(/^[A-Z]{3}$/, 'must be an ISO-4217 code'));

/** Bare hostname (no scheme, no path), e.g. `coachella.com`. */
export const DomainSchema = z
  .string()
  .trim()
  .toLowerCase()
  .regex(/^(?!-)[a-z0-9-]+(\.[a-z0-9-]+)+$/, 'must be a bare domain');

export const UrlSchema = z.url();
export const LatitudeSchema = z.number().min(-90).max(90);
export const LongitudeSchema = z.number().min(-180).max(180);
export const MoneySchema = z.number().nonnegative();

// ===========================================================================
// Enumerations
// ===========================================================================

/**
 * Sources the platform is allowed to ingest from. Values track the approved
 * entries in `source_registry.yml` plus the scraper ensemble sources in
 * `scrapers/contracts.py`; new sources must be registered in both places.
 */
export const SourceSystemSchema = z.enum([
  'musicbrainz',
  'wikidata',
  'wikipedia',
  'wikimedia',
  'setlistfm',
  'ticketmaster',
  'youtube',
  'gdelt',
  'hackernews',
  'rss',
  'lastfm',
  'discogs',
  'spotify',
  'songkick',
  'bandsintown',
  'edmtrain',
  'nws',
  'noaa_ncei',
  'bts',
  'census',
  'bea',
  'bls',
  'openstreetmap',
  'official_site',
  'festival_site',
  'press_release',
  'manual',
  'scraper',
]);

/** Provenance classification, mirrors `MetricType` in `contracts/entities.py`. */
export const MetricTypeSchema = z.enum(['observed', 'modeled', 'assumption', 'private']);

/** Billing tiers, mirrors `BillingTier` in `contracts/entities.py`. */
export const BillingTierSchema = z.enum([
  'headliner',
  'sub_headliner',
  'main_stage',
  'secondary',
  'emerging',
  'unknown',
]);

/** How an artist appears on a given slot. */
export const ArtistRoleSchema = z.enum([
  'headliner',
  'co_headliner',
  'direct_support',
  'support',
  'special_guest',
  'opener',
  'host',
  'b2b',
  'surprise_guest',
  'unknown',
]);

/** Shape of the performance itself. */
export const SetTypeSchema = z.enum([
  'live',
  'dj_set',
  'b2b',
  'hybrid',
  'acoustic',
  'orchestral',
  'audiovisual',
  'unknown',
]);

export const ActiveStatusSchema = z.enum([
  'active',
  'hiatus',
  'disbanded',
  'deceased',
  'unknown',
]);

export const ArtistTypeSchema = z.enum([
  'person',
  'group',
  'orchestra',
  'choir',
  'character',
  'other',
]);

export const SelloutStatusSchema = z.enum([
  'sold_out',
  'low_availability',
  'available',
  'waitlist',
  'not_on_sale',
  'unknown',
]);

export const LineupStatusSchema = z.enum([
  'unannounced',
  'rumored',
  'partial',
  'announced',
  'final',
  'cancelled',
]);

export const TicketTierTypeSchema = z.enum([
  'ga',
  'ga_plus',
  'vip',
  'platinum',
  'single_day',
  'weekend',
  'camping',
  'parking',
  'shuttle',
  'payment_plan',
  'other',
]);

export const TicketTierStatusSchema = z.enum([
  'on_sale',
  'sold_out',
  'not_yet_on_sale',
  'waitlist',
  'withdrawn',
  'unknown',
]);

export const StageTypeSchema = z.enum([
  'main',
  'secondary',
  'tent',
  'arena',
  'club',
  'boutique',
  'silent_disco',
  'other',
]);

export const VenueTypeSchema = z.enum([
  'outdoor',
  'indoor',
  'mixed',
  'park',
  'fairground',
  'beach',
  'stadium',
  'arena',
  'urban_multi_venue',
  'other',
]);

export const CapacityBasisSchema = z.enum(['daily', 'total', 'per_stage', 'unknown']);

export const AliasTypeSchema = z.enum([
  'alias',
  'legal_name',
  'stage_name',
  'transliteration',
  'abbreviation',
  'misspelling',
  'former_name',
  'search_hint',
]);

export const LabelRelationshipSchema = z.enum([
  'label',
  'publisher',
  'distributor',
  'management',
  'booking_agency',
]);

export const ExternalIdTypeSchema = z.enum([
  'musicbrainz',
  'wikidata',
  'spotify',
  'apple_music',
  'youtube',
  'soundcloud',
  'bandcamp',
  'discogs',
  'songkick',
  'bandsintown',
  'setlistfm',
  'ticketmaster',
  'edmtrain',
  'openstreetmap',
  'isni',
  'ipi',
  'other',
]);

export const SocialPlatformSchema = z.enum([
  'instagram',
  'twitter',
  'tiktok',
  'facebook',
  'youtube',
  'soundcloud',
  'bandcamp',
  'spotify',
  'threads',
  'twitch',
  'bluesky',
  'weibo',
  'other',
]);

export const ExtractionMethodSchema = z.enum([
  'html_selector',
  'structured_data',
  'json_ld',
  'api',
  'ocr',
  'llm',
  'heuristic',
  'manual',
]);

/**
 * Match strategies. Mirrors `MatchMethod` in `entity/entity_resolution.py`,
 * extended with the signals scored by the weighted-fuzzy matcher.
 */
export const MatchMethodSchema = z.enum([
  'exact_name',
  'normalized_name',
  'fuzzy_name',
  'alias_match',
  'mbid_lookup',
  'external_id',
  'wikidata_lookup',
  'social_handle',
  'domain_match',
  'cross_reference',
  'weighted_fuzzy',
  'manual_review',
]);

export const MatchDecisionSchema = z.enum(['accepted', 'rejected', 'review']);

export const ResolutionStatusSchema = z.enum([
  'unresolved',
  'auto_resolved',
  'review_required',
  'manually_resolved',
  'rejected',
]);

export const EntityTypeSchema = z.enum([
  'artist',
  'festival',
  'venue',
  'edition',
  'stage',
  'lineup_slot',
]);

// ===========================================================================
// Evidence and provenance
// ===========================================================================

/** A single citation backing an extracted claim. */
export const EvidenceSchema = z.object({
  source_url: UrlSchema,
  source_system: SourceSystemSchema.optional(),
  snippet: z.string().optional(),
  selector: z.string().optional(),
  extraction_method: ExtractionMethodSchema.optional(),
  retrieved_at: IsoDateTimeSchema,
  confidence: ConfidenceSchema.optional(),
});

/** Provenance columns shared by every extracted record. */
const provenanceFields = {
  evidence: z.array(EvidenceSchema).default([]),
  evidence_url: UrlSchema.optional(),
  extraction_confidence: ConfidenceSchema.optional(),
  extraction_method: ExtractionMethodSchema.optional(),
  source_system: SourceSystemSchema.optional(),
  source_url: UrlSchema.optional(),
  source_retrieved_at: IsoDateTimeSchema.optional(),
};

/** Entity-resolution columns shared by records that resolve to a canonical row. */
const resolutionFields = {
  match_confidence: ConfidenceSchema.optional(),
  match_method: MatchMethodSchema.optional(),
  manually_reviewed: z.boolean().default(false),
};

// ===========================================================================
// Artist descriptors (nested inside JSON columns of core.artists)
// ===========================================================================

const artistAliasFields = {
  alias: z.string().min(1),
  normalized_alias: z.string().min(1).optional(),
  alias_type: AliasTypeSchema.default('alias'),
  locale: z.string().optional(),
  is_primary: z.boolean().default(false),
  begin_date: IsoDateSchema.optional(),
  end_date: IsoDateSchema.optional(),
  source_system: SourceSystemSchema.optional(),
  evidence_url: UrlSchema.optional(),
  confidence: ConfidenceSchema.optional(),
};

export const ArtistAliasSchema = z.object(artistAliasFields);

/** Accepts a bare string (`"Radiohead"`) or a full alias descriptor. */
export const ArtistAliasInputSchema = z.preprocess(
  (value) => (typeof value === 'string' ? { alias: value } : value),
  ArtistAliasSchema,
);

const artistMemberFields = {
  member_name: z.string().min(1),
  normalized_member_name: z.string().optional(),
  member_musicbrainz_id: MusicBrainzIdSchema.optional(),
  member_role: z.string().optional(),
  instruments: z.array(z.string()).default([]),
  joined_date: IsoDateSchema.optional(),
  left_date: IsoDateSchema.optional(),
  is_current: z.boolean().optional(),
  source_system: SourceSystemSchema.optional(),
  evidence_url: UrlSchema.optional(),
  confidence: ConfidenceSchema.optional(),
};

export const ArtistMemberSchema = z.object(artistMemberFields);

const artistLabelFields = {
  label_name: z.string().min(1),
  normalized_label_name: z.string().optional(),
  label_musicbrainz_id: MusicBrainzIdSchema.optional(),
  relationship_type: LabelRelationshipSchema.default('label'),
  territory: z.string().optional(),
  contact_name: z.string().optional(),
  contact_email: z.email().optional(),
  start_date: IsoDateSchema.optional(),
  end_date: IsoDateSchema.optional(),
  is_current: z.boolean().optional(),
  source_system: SourceSystemSchema.optional(),
  evidence_url: UrlSchema.optional(),
  confidence: ConfidenceSchema.optional(),
};

export const ArtistLabelSchema = z.object(artistLabelFields);

/** Contents of the `core.artists.management` JSON column. */
export const ArtistManagementSchema = z.object({
  manager_name: z.string().optional(),
  management_company: z.string().optional(),
  management_email: z.email().optional(),
  management_phone: z.string().optional(),
  booking_agent: z.string().optional(),
  booking_agency: z.string().optional(),
  booking_email: z.email().optional(),
  publicist: z.string().optional(),
  territory: z.string().optional(),
  source_system: SourceSystemSchema.optional(),
  evidence_url: UrlSchema.optional(),
  confidence: ConfidenceSchema.optional(),
});

const socialHandleFields = {
  platform: SocialPlatformSchema,
  handle: z.string().min(1),
  normalized_handle: z.string().min(1).optional(),
  url: UrlSchema.optional(),
  is_verified: z.boolean().optional(),
  follower_count: z.number().int().nonnegative().optional(),
  engagement_rate: z.number().min(0).optional(),
  observed_at: IsoDateTimeSchema.optional(),
  source_system: SourceSystemSchema.optional(),
  evidence_url: UrlSchema.optional(),
  confidence: ConfidenceSchema.optional(),
};

export const SocialHandleSchema = z.object(socialHandleFields);

/**
 * Long-tail identifiers that do not have a dedicated column on
 * `core.artists`. Stored in the `external_ids` JSON column and mirrored into
 * `core.entity_external_ids` for entity resolution.
 */
export const ExternalIdMapSchema = z.partialRecord(ExternalIdTypeSchema, z.string().min(1));

// ===========================================================================
// core.artists
// ===========================================================================

/**
 * Full artist specification. Keys mirror `core.artists`.
 */
export const ArtistSpecSchema = z.object({
  artist_key: z.string().min(1).optional(),
  musicbrainz_id: MusicBrainzIdSchema.optional(),
  name: z.string().min(1),
  normalized_name: z.string().min(1),
  sort_name: z.string().optional(),
  disambiguation: z.string().optional(),

  // Identity / origin
  aliases: z.array(ArtistAliasInputSchema).default([]),
  country: CountryCodeSchema.optional(),
  origin_city: z.string().optional(),
  origin_region: z.string().optional(),
  area: z.string().optional(),

  // Classification
  type: ArtistTypeSchema.optional(),
  primary_genre: z.string().optional(),
  genres: z.array(z.string()).default([]),
  subgenres: z.array(z.string()).default([]),
  tags: z.array(z.string()).default([]),

  // Lifecycle
  life_span_begin: z.string().optional(),
  life_span_end: z.string().optional(),
  formation_date: IsoDateSchema.optional(),
  disband_date: IsoDateSchema.optional(),
  active_status: ActiveStatusSchema.optional(),
  is_active: z.boolean().optional(),

  // Composition and business relationships
  members: z.array(ArtistMemberSchema).default([]),
  member_count: z.number().int().nonnegative().optional(),
  labels: z.array(ArtistLabelSchema).default([]),
  current_label: z.string().optional(),
  management: ArtistManagementSchema.optional(),
  manager_name: z.string().optional(),
  booking_agency: z.string().optional(),

  // Web presence
  official_website: UrlSchema.optional(),
  official_domains: z.array(DomainSchema).default([]),
  social_handles: z.array(SocialHandleSchema).default([]),

  // External identifiers
  wikidata_id: WikidataIdSchema.optional(),
  spotify_id: SpotifyIdSchema.optional(),
  apple_music_id: z.string().optional(),
  youtube_channel_id: z.string().optional(),
  soundcloud_id: z.string().optional(),
  bandcamp_id: z.string().optional(),
  discogs_id: z.string().optional(),
  songkick_id: z.string().optional(),
  bandsintown_id: z.string().optional(),
  setlistfm_id: z.string().optional(),
  ticketmaster_id: z.string().optional(),
  isni: z.string().optional(),
  ipi: z.string().optional(),
  external_ids: ExternalIdMapSchema.default({}),

  // Popularity snapshot (history lives in metrics.artist_popularity)
  popularity_score: z.number().min(0).max(100).optional(),
  popularity_rank: z.number().int().positive().optional(),
  popularity_source: SourceSystemSchema.optional(),
  popularity_observed_at: IsoDateTimeSchema.optional(),
  spotify_popularity: z.number().int().min(0).max(100).optional(),
  spotify_followers: z.number().int().nonnegative().optional(),
  monthly_listeners: z.number().int().nonnegative().optional(),
  listener_countries: z.array(CountryCodeSchema).default([]),

  ...provenanceFields,

  // Entity resolution
  blocking_key: z.string().optional(),
  ...resolutionFields,
  resolution_status: ResolutionStatusSchema.default('unresolved'),

  ingested_at: IsoDateTimeSchema.optional(),
  updated_at: IsoDateTimeSchema.optional(),
});

/** Row contract for `core.artist_aliases`. */
export const ArtistAliasRowSchema = z.object({
  alias_key: z.string().min(1).optional(),
  artist_key: z.string().min(1),
  ...artistAliasFields,
  ingested_at: IsoDateTimeSchema.optional(),
});

/** Row contract for `core.artist_members`. */
export const ArtistMemberRowSchema = z.object({
  member_key: z.string().min(1).optional(),
  artist_key: z.string().min(1),
  ...artistMemberFields,
  ingested_at: IsoDateTimeSchema.optional(),
});

/** Row contract for `core.artist_labels`. */
export const ArtistLabelRowSchema = z.object({
  artist_label_key: z.string().min(1).optional(),
  artist_key: z.string().min(1),
  ...artistLabelFields,
  ingested_at: IsoDateTimeSchema.optional(),
});

/** Row contract for `core.artist_social_handles`. */
export const ArtistSocialHandleRowSchema = z.object({
  handle_key: z.string().min(1).optional(),
  artist_key: z.string().min(1),
  ...socialHandleFields,
  ingested_at: IsoDateTimeSchema.optional(),
});

/** Row contract for `core.artist_contacts`. */
export const ArtistContactRowSchema = z.object({
  contact_key: z.string().min(1).optional(),
  artist_key: z.string().min(1),
  agency_name: z.string().optional(),
  agent_name: z.string().optional(),
  contact_email: z.email().optional(),
  contact_phone: z.string().optional(),
  role: z.string().optional(),
  verified: z.boolean().optional(),
  source_url: UrlSchema.optional(),
  retrieved_at: IsoDateTimeSchema.optional(),
  source_system: SourceSystemSchema.optional(),
  evidence_url: UrlSchema.optional(),
  confidence: ConfidenceSchema.optional(),
  ingested_at: IsoDateTimeSchema.optional(),
});

// ===========================================================================
// core.venues
// ===========================================================================

/** Row contract for `core.venues`. */
export const VenueSpecSchema = z.object({
  venue_key: z.string().min(1).optional(),
  name: z.string().min(1),
  normalized_name: z.string().min(1),
  venue_type: VenueTypeSchema.optional(),
  address: z.string().optional(),
  city: z.string().optional(),
  region: z.string().optional(),
  country: CountryCodeSchema.optional(),
  postal_code: z.string().optional(),
  latitude: LatitudeSchema.optional(),
  longitude: LongitudeSchema.optional(),
  time_zone: z.string().optional(),
  capacity: z.number().int().nonnegative().optional(),
  is_outdoor: z.boolean().optional(),
  website: UrlSchema.optional(),
  wikidata_id: WikidataIdSchema.optional(),
  musicbrainz_id: MusicBrainzIdSchema.optional(),
  ticketmaster_id: z.string().optional(),
  openstreetmap_id: z.string().optional(),
  external_ids: ExternalIdMapSchema.default({}),
  source_system: SourceSystemSchema.optional(),
  source_url: UrlSchema.optional(),
  source_retrieved_at: IsoDateTimeSchema.optional(),
  evidence_url: UrlSchema.optional(),
  extraction_confidence: ConfidenceSchema.optional(),
  ingested_at: IsoDateTimeSchema.optional(),
  updated_at: IsoDateTimeSchema.optional(),
});

// ===========================================================================
// Festival descriptors
// ===========================================================================

const festivalStageFields = {
  stage_name: z.string().min(1),
  normalized_stage_name: z.string().min(1).optional(),
  stage_type: StageTypeSchema.optional(),
  stage_rank: z.number().int().positive().optional(),
  capacity: z.number().int().nonnegative().optional(),
  is_indoor: z.boolean().optional(),
  sponsor: z.string().optional(),
  host: z.string().optional(),
  latitude: LatitudeSchema.optional(),
  longitude: LongitudeSchema.optional(),
  source_system: SourceSystemSchema.optional(),
  source_url: UrlSchema.optional(),
  evidence_url: UrlSchema.optional(),
  extraction_confidence: ConfidenceSchema.optional(),
};

export const FestivalStageSchema = z.object(festivalStageFields);

const ticketTierFields = {
  tier_name: z.string().min(1),
  normalized_tier_name: z.string().optional(),
  tier_type: TicketTierTypeSchema.optional(),
  tier_rank: z.number().int().positive().optional(),
  price: MoneySchema.optional(),
  fees: MoneySchema.optional(),
  price_with_fees: MoneySchema.optional(),
  currency: CurrencyCodeSchema.optional(),
  quantity: z.number().int().nonnegative().optional(),
  quantity_sold: z.number().int().nonnegative().optional(),
  on_sale_at: IsoDateTimeSchema.optional(),
  sold_out_at: IsoDateTimeSchema.optional(),
  sold_out: z.boolean().optional(),
  tier_status: TicketTierStatusSchema.optional(),
  inclusions: z.array(z.string()).default([]),
  source_system: SourceSystemSchema.optional(),
  source_url: UrlSchema.optional(),
  evidence_url: UrlSchema.optional(),
  extraction_confidence: ConfidenceSchema.optional(),
};

export const TicketTierSchema = z.object(ticketTierFields);

/** One announcement wave (`lineup_announcements` JSON column). */
export const LineupAnnouncementSchema = z.object({
  announcement_date: IsoDateSchema.optional(),
  announced_at: IsoDateTimeSchema.optional(),
  wave: z.string().optional(),
  headline: z.string().optional(),
  artist_count: z.number().int().nonnegative().optional(),
  announcement_url: UrlSchema.optional(),
  source_system: SourceSystemSchema.optional(),
  evidence_url: UrlSchema.optional(),
  confidence: ConfidenceSchema.optional(),
});

/** Compact record of a prior edition (`historical_editions` JSON column). */
export const HistoricalEditionSchema = z.object({
  year: z.number().int(),
  edition_label: z.string().optional(),
  start_date: IsoDateSchema.optional(),
  end_date: IsoDateSchema.optional(),
  location_city: z.string().optional(),
  location_country: CountryCodeSchema.optional(),
  venue_name: z.string().optional(),
  capacity: z.number().int().nonnegative().optional(),
  attendance: z.number().int().nonnegative().optional(),
  headliners: z.array(z.string()).default([]),
  sellout_status: SelloutStatusSchema.optional(),
  sold_out: z.boolean().optional(),
  is_cancelled: z.boolean().optional(),
  evidence_url: UrlSchema.optional(),
});

/** Organiser or promoter attribution (`organizers` / `promoters` columns). */
export const OrganizationRefSchema = z.object({
  name: z.string().min(1),
  normalized_name: z.string().optional(),
  organization_role: z.enum(['organizer', 'promoter', 'owner', 'production', 'booking']).optional(),
  country: CountryCodeSchema.optional(),
  website: UrlSchema.optional(),
  parent_company: z.string().optional(),
  contact_email: z.email().optional(),
  source_system: SourceSystemSchema.optional(),
  evidence_url: UrlSchema.optional(),
  confidence: ConfidenceSchema.optional(),
});

// ===========================================================================
// core.festivals
// ===========================================================================

/** Full festival specification. Keys mirror `core.festivals`. */
export const FestivalSpecSchema = z.object({
  festival_key: z.string().min(1).optional(),
  name: z.string().min(1),
  normalized_name: z.string().min(1),
  aliases: z.array(ArtistAliasInputSchema).default([]),

  // Location
  location_country: CountryCodeSchema.optional(),
  location_city: z.string().optional(),
  location_region: z.string().optional(),
  postal_code: z.string().optional(),
  latitude: LatitudeSchema.optional(),
  longitude: LongitudeSchema.optional(),
  time_zone: z.string().optional(),

  // Venue
  venue_key: z.string().optional(),
  venue_name: z.string().optional(),
  venue_address: z.string().optional(),
  venue_type: VenueTypeSchema.optional(),

  // Scale
  capacity: z.number().int().nonnegative().optional(),
  daily_capacity: z.number().int().nonnegative().optional(),
  total_capacity: z.number().int().nonnegative().optional(),
  capacity_basis: CapacityBasisSchema.optional(),
  duration_days: z.number().int().positive().optional(),
  typical_month: z.number().int().min(1).max(12).optional(),

  // Classification
  genre_focus: z.array(z.string()).default([]),
  subgenre_focus: z.array(z.string()).default([]),
  festival_type: z.string().optional(),

  // Organisation
  organizer: z.string().optional(),
  organizers: z.array(OrganizationRefSchema).default([]),
  promoter: z.string().optional(),
  promoters: z.array(OrganizationRefSchema).default([]),
  parent_company: z.string().optional(),
  booking_contact: z.string().optional(),

  // Stages and ticketing
  stage_count: z.number().int().nonnegative().optional(),
  stages: z.array(FestivalStageSchema).default([]),
  ticket_tiers: z.array(TicketTierSchema).default([]),
  currency: CurrencyCodeSchema.optional(),
  ticket_price_min: MoneySchema.optional(),
  ticket_price_max: MoneySchema.optional(),
  on_sale_date: IsoDateSchema.optional(),
  sellout_status: SelloutStatusSchema.optional(),
  sold_out: z.boolean().optional(),
  sold_out_at: IsoDateTimeSchema.optional(),
  sellout_duration_hours: z.number().nonnegative().optional(),

  // Lineup announcement state
  lineup_status: LineupStatusSchema.optional(),
  lineup_announced_at: IsoDateTimeSchema.optional(),
  lineup_announcement_url: UrlSchema.optional(),
  lineup_announcements: z.array(LineupAnnouncementSchema).default([]),

  // Web presence
  official_website: UrlSchema.optional(),
  official_domains: z.array(DomainSchema).default([]),
  social_handles: z.array(SocialHandleSchema).default([]),

  // History
  first_edition_year: z.number().int().optional(),
  latest_edition_year: z.number().int().optional(),
  edition_count: z.number().int().nonnegative().optional(),
  historical_editions: z.array(HistoricalEditionSchema).default([]),
  is_active: z.boolean().optional(),
  active_status: ActiveStatusSchema.optional(),

  // External identifiers
  wikidata_id: WikidataIdSchema.optional(),
  musicbrainz_id: MusicBrainzIdSchema.optional(),
  ticketmaster_id: z.string().optional(),
  songkick_id: z.string().optional(),
  edmtrain_id: z.string().optional(),
  external_ids: ExternalIdMapSchema.default({}),

  ...provenanceFields,
  source_last_modified: IsoDateTimeSchema.optional(),

  ingested_at: IsoDateTimeSchema.optional(),
  updated_at: IsoDateTimeSchema.optional(),
});

/** Row contract for `core.festival_editions`. */
export const FestivalEditionSchema = z.object({
  edition_key: z.string().min(1).optional(),
  festival_key: z.string().min(1).optional(),
  year: z.number().int(),
  edition_name: z.string().optional(),
  edition_label: z.string().optional(),
  weekend_number: z.number().int().positive().optional(),

  // Dates
  start_date: IsoDateSchema.optional(),
  end_date: IsoDateSchema.optional(),
  duration_days: z.number().int().positive().optional(),
  time_zone: z.string().optional(),

  // Location
  venue_key: z.string().optional(),
  venue_name: z.string().optional(),
  location_city: z.string().optional(),
  location_region: z.string().optional(),
  location_country: CountryCodeSchema.optional(),
  latitude: LatitudeSchema.optional(),
  longitude: LongitudeSchema.optional(),

  // Scale and outcome
  capacity: z.number().int().nonnegative().optional(),
  daily_capacity: z.number().int().nonnegative().optional(),
  attendance: z.number().int().nonnegative().optional(),
  headliner_count: z.number().int().nonnegative().optional(),
  total_artists: z.number().int().nonnegative().optional(),
  stage_count: z.number().int().nonnegative().optional(),

  // Ticketing
  ticket_tiers: z.array(TicketTierSchema).default([]),
  currency: CurrencyCodeSchema.optional(),
  ticket_price_min: MoneySchema.optional(),
  ticket_price_max: MoneySchema.optional(),
  on_sale_date: IsoDateSchema.optional(),
  sellout_status: SelloutStatusSchema.optional(),
  sold_out: z.boolean().optional(),
  sold_out_at: IsoDateTimeSchema.optional(),
  sellout_duration_hours: z.number().nonnegative().optional(),
  tickets_sold: z.number().int().nonnegative().optional(),
  gross_revenue: MoneySchema.optional(),

  // Lineup announcement state
  lineup_status: LineupStatusSchema.optional(),
  lineup_announced_at: IsoDateTimeSchema.optional(),
  lineup_announcement_url: UrlSchema.optional(),
  lineup_announcements: z.array(LineupAnnouncementSchema).default([]),
  poster_url: UrlSchema.optional(),

  // Organisation
  organizer: z.string().optional(),
  promoter: z.string().optional(),

  // Status
  is_cancelled: z.boolean().optional(),
  cancellation_reason: z.string().optional(),
  weather_summary: z.string().optional(),

  evidence: z.array(EvidenceSchema).default([]),
  evidence_url: UrlSchema.optional(),
  extraction_confidence: ConfidenceSchema.optional(),
  source_system: SourceSystemSchema.optional(),
  source_url: UrlSchema.optional(),
  source_retrieved_at: IsoDateTimeSchema.optional(),

  ingested_at: IsoDateTimeSchema.optional(),
  updated_at: IsoDateTimeSchema.optional(),
});

/** Row contract for `core.festival_stages`. */
export const FestivalStageRowSchema = z.object({
  stage_key: z.string().min(1).optional(),
  festival_key: z.string().min(1),
  edition_key: z.string().optional(),
  year: z.number().int().optional(),
  ...festivalStageFields,
  ingested_at: IsoDateTimeSchema.optional(),
});

/** Row contract for `core.festival_ticket_tiers`. */
export const FestivalTicketTierRowSchema = z.object({
  tier_key: z.string().min(1).optional(),
  festival_key: z.string().min(1),
  edition_key: z.string().optional(),
  year: z.number().int().optional(),
  ...ticketTierFields,
  ingested_at: IsoDateTimeSchema.optional(),
});

// ===========================================================================
// Lineup
// ===========================================================================

/** Row contract for `core.lineup_slots` - one resolved performance slot. */
export const LineupSlotSchema = z.object({
  slot_key: z.string().min(1).optional(),
  festival_key: z.string().min(1).optional(),
  edition_key: z.string().optional(),
  year: z.number().int().optional(),

  // Artist
  artist_key: z.string().optional(),
  artist_name: z.string().min(1),
  normalized_artist_name: z.string().optional(),
  musicbrainz_id: MusicBrainzIdSchema.optional(),

  // Billing
  billing_order: z.number().int().nonnegative().optional(),
  billing_tier: BillingTierSchema.optional(),
  poster_line: z.number().int().positive().optional(),
  poster_position: z.number().int().positive().optional(),
  is_headliner: z.boolean().optional(),

  // Stage and scheduling
  stage_key: z.string().optional(),
  stage_name: z.string().optional(),
  performance_date: IsoDateSchema.optional(),
  day_of_festival: z.number().int().positive().optional(),
  day_label: z.string().optional(),
  start_time: IsoDateTimeSchema.optional(),
  end_time: IsoDateTimeSchema.optional(),
  local_start_time: IsoTimeSchema.optional(),
  local_end_time: IsoTimeSchema.optional(),
  time_zone: z.string().optional(),
  set_duration_minutes: z.number().int().positive().optional(),

  // Performance shape
  artist_role: ArtistRoleSchema.optional(),
  set_type: SetTypeSchema.optional(),
  is_b2b: z.boolean().optional(),
  collaborators: z.array(z.string()).default([]),
  genre: z.string().optional(),
  subgenres: z.array(z.string()).default([]),

  // Announcement lifecycle
  announcement_date: IsoDateSchema.optional(),
  announced_at: IsoDateTimeSchema.optional(),
  announcement_wave: z.string().optional(),
  announcement_url: UrlSchema.optional(),
  is_cancelled: z.boolean().optional(),
  replaced_artist_name: z.string().optional(),

  ...provenanceFields,
  evidence_snippet: z.string().optional(),
  parser_version: z.string().optional(),

  ...resolutionFields,

  ingested_at: IsoDateTimeSchema.optional(),
  updated_at: IsoDateTimeSchema.optional(),
});

/** Row contract for `raw.lineup_observations` - pre-resolution evidence. */
export const LineupObservationSchema = z.object({
  observation_key: z.string().min(1).optional(),
  festival_key: z.string().optional(),
  festival_name: z.string().optional(),
  edition_year: z.number().int().optional(),
  artist_name: z.string().min(1),
  normalized_artist_name: z.string().optional(),
  position: z.string().optional(),
  billing_order: z.number().int().nonnegative().optional(),
  billing_tier: BillingTierSchema.optional(),
  stage: z.string().optional(),
  day: z.string().optional(),
  performance_date: IsoDateSchema.optional(),
  start_time: IsoDateTimeSchema.optional(),
  end_time: IsoDateTimeSchema.optional(),
  artist_role: ArtistRoleSchema.optional(),
  genre: z.string().optional(),
  announcement_date: IsoDateSchema.optional(),
  source_url: UrlSchema.optional(),
  source_system: SourceSystemSchema.optional(),
  source_retrieved_at: IsoDateTimeSchema.optional(),
  parser_version: z.string().optional(),
  extraction_method: ExtractionMethodSchema.optional(),
  extraction_confidence: ConfidenceSchema.optional(),
  evidence_url: UrlSchema.optional(),
  evidence_snippet: z.string().optional(),
  observed_raw: z.record(z.string(), z.unknown()).default({}),

  resolved_artist_key: z.string().optional(),
  match_confidence: ConfidenceSchema.optional(),
  match_method: MatchMethodSchema.optional(),
  requires_review: z.boolean().optional(),

  ingested_at: IsoDateTimeSchema.optional(),
});

/** Row contract for `core.lineup_qualification_metrics`. */
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
  confidence: ConfidenceSchema.optional(),
  ingested_at: IsoDateTimeSchema.optional(),
});

// ===========================================================================
// Entity resolution
// ===========================================================================

/** Row contract for `core.entity_external_ids`. */
export const EntityExternalIdSchema = z.object({
  external_id_key: z.string().min(1).optional(),
  entity_type: EntityTypeSchema,
  entity_key: z.string().min(1).optional(),
  id_type: ExternalIdTypeSchema,
  id_value: z.string().min(1),
  url: UrlSchema.optional(),
  is_primary: z.boolean().optional(),
  confidence: ConfidenceSchema.optional(),
  source_system: SourceSystemSchema.optional(),
  evidence_url: UrlSchema.optional(),
  ingested_at: IsoDateTimeSchema.optional(),
});

/** Row contract for `core.entity_match_weights`. */
export const EntityMatchWeightSchema = z.object({
  weight_key: z.string().min(1).optional(),
  entity_type: EntityTypeSchema,
  feature_name: z.string().min(1),
  weight: ConfidenceSchema,
  threshold_accept: ConfidenceSchema.optional(),
  threshold_review: ConfidenceSchema.optional(),
  model_version: z.string().optional(),
  is_active: z.boolean().default(true),
  notes: z.string().optional(),
  updated_at: IsoDateTimeSchema.optional(),
});

/**
 * Individual signals scored by the weighted-fuzzy matcher. Similarity fields
 * are 0-1; boolean fields record exact agreement on a high-precision key.
 */
export const EntityMatchFeaturesSchema = z.object({
  name_similarity: ConfidenceSchema.optional(),
  alias_similarity: ConfidenceSchema.optional(),
  external_id_match: z.boolean().optional(),
  musicbrainz_id_match: z.boolean().optional(),
  country_match: z.boolean().optional(),
  genre_similarity: ConfidenceSchema.optional(),
  social_handle_match: z.boolean().optional(),
  domain_match: z.boolean().optional(),
  date_proximity: ConfidenceSchema.optional(),
  context_similarity: ConfidenceSchema.optional(),
});

/** Row contract for `core.entity_match_candidates`. */
export const EntityMatchCandidateSchema = z.object({
  candidate_key: z.string().min(1).optional(),
  entity_type: EntityTypeSchema,
  source_record_key: z.string().min(1),
  source_name: z.string().optional(),
  normalized_source_name: z.string().optional(),
  blocking_key: z.string().optional(),

  candidate_entity_key: z.string().optional(),
  candidate_name: z.string().optional(),
  candidate_musicbrainz_id: MusicBrainzIdSchema.optional(),

  ...EntityMatchFeaturesSchema.shape,
  weighted_score: ConfidenceSchema.optional(),

  match_method: MatchMethodSchema.optional(),
  match_confidence: ConfidenceSchema.optional(),
  decision: MatchDecisionSchema.optional(),
  requires_review: z.boolean().optional(),
  reviewed_by: z.string().optional(),
  reviewed_at: IsoDateTimeSchema.optional(),
  model_version: z.string().optional(),
  feature_scores: z.record(z.string(), z.number()).default({}),
  evidence: z.array(EvidenceSchema).default([]),
  created_at: IsoDateTimeSchema.optional(),
});

/**
 * Default weights seeded by `schema/duckdb.sql` into
 * `core.entity_match_weights`. Kept here so the TypeScript matcher and the
 * warehouse agree; the parity test asserts both copies stay identical.
 */
export const DEFAULT_ENTITY_MATCH_WEIGHTS = [
  { entity_type: 'artist', feature_name: 'musicbrainz_id_match', weight: 1.0 },
  { entity_type: 'artist', feature_name: 'external_id_match', weight: 0.9 },
  { entity_type: 'artist', feature_name: 'name_similarity', weight: 0.45 },
  { entity_type: 'artist', feature_name: 'alias_similarity', weight: 0.25 },
  { entity_type: 'artist', feature_name: 'social_handle_match', weight: 0.2 },
  { entity_type: 'artist', feature_name: 'genre_similarity', weight: 0.1 },
  { entity_type: 'artist', feature_name: 'country_match', weight: 0.05 },
  { entity_type: 'festival', feature_name: 'name_similarity', weight: 0.5 },
  { entity_type: 'festival', feature_name: 'alias_similarity', weight: 0.2 },
  { entity_type: 'festival', feature_name: 'country_match', weight: 0.15 },
  { entity_type: 'festival', feature_name: 'domain_match', weight: 0.15 },
] as const satisfies ReadonlyArray<{
  entity_type: 'artist' | 'festival';
  feature_name: string;
  weight: number;
}>;

// ===========================================================================
// Scraper envelope
// ===========================================================================

/**
 * Everything a festival scrape yields in one pass: the festival itself, the
 * edition being scraped, its stages and ticket tiers, the resolved lineup, and
 * the raw observations the lineup was derived from.
 */
export const ScrapedFestivalSchema = z.object({
  festival: FestivalSpecSchema,
  editions: z.array(FestivalEditionSchema).default([]),
  stages: z.array(FestivalStageSchema).default([]),
  ticket_tiers: z.array(TicketTierSchema).default([]),
  lineup: z.array(LineupSlotSchema).default([]),
  observations: z.array(LineupObservationSchema).default([]),
  scraped_at: IsoDateTimeSchema,
  scraper_version: z.string().optional(),
  source_system: SourceSystemSchema.optional(),
  source_url: UrlSchema.optional(),
});

// ===========================================================================
// Normalization helpers
// ===========================================================================

/**
 * Matching form of a name: Unicode-folded, lowercased, punctuation stripped,
 * whitespace collapsed. Mirrors `EntityResolver.normalize_name` in
 * `pipelines/entity_resolution.py`, with diacritic folding added so that
 * "Beyoncé" and "Beyonce" land on the same key.
 */
export function normalizeName(value: string): string {
  return value
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9'\- ]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

/** Social handle without the leading `@`, lowercased. */
export function normalizeHandle(value: string): string {
  return value.trim().toLowerCase().replace(/^@+/, '').replace(/\/+$/, '');
}

/** Bare hostname for a URL or host string, with `www.` removed. */
export function normalizeDomain(value: string): string {
  const withoutScheme = value.trim().toLowerCase().replace(/^[a-z][a-z0-9+.-]*:\/\//, '');
  const host = withoutScheme.split('/')[0] ?? '';
  return host.replace(/^www\./, '').replace(/:\d+$/, '');
}

/**
 * Blocking key used to shortlist candidates before weighted scoring: the
 * normalized name with all non-alphanumerics removed, truncated to 8
 * characters. Short enough to survive typos, long enough to keep blocks small.
 */
export function blockingKey(value: string): string {
  return normalizeName(value).replace(/[^a-z0-9]/g, '').slice(0, 8);
}

/** Fill in `normalized_name` and `blocking_key` from `name` when absent. */
export function withDerivedNameKeys<
  T extends { name: string; normalized_name?: string; blocking_key?: string },
>(input: T): T & { normalized_name: string; blocking_key: string } {
  const normalized_name = input.normalized_name ?? normalizeName(input.name);
  return {
    ...input,
    normalized_name,
    blocking_key: input.blocking_key ?? blockingKey(normalized_name),
  };
}

// ===========================================================================
// Inferred types
// ===========================================================================

export type Evidence = z.infer<typeof EvidenceSchema>;
export type SourceSystem = z.infer<typeof SourceSystemSchema>;
export type MetricType = z.infer<typeof MetricTypeSchema>;
export type BillingTier = z.infer<typeof BillingTierSchema>;
export type ArtistRole = z.infer<typeof ArtistRoleSchema>;
export type SetType = z.infer<typeof SetTypeSchema>;
export type ActiveStatus = z.infer<typeof ActiveStatusSchema>;
export type SelloutStatus = z.infer<typeof SelloutStatusSchema>;
export type LineupStatus = z.infer<typeof LineupStatusSchema>;
export type MatchMethod = z.infer<typeof MatchMethodSchema>;
export type MatchDecision = z.infer<typeof MatchDecisionSchema>;
export type ResolutionStatus = z.infer<typeof ResolutionStatusSchema>;
export type EntityType = z.infer<typeof EntityTypeSchema>;

export type ArtistAlias = z.infer<typeof ArtistAliasSchema>;
export type ArtistMember = z.infer<typeof ArtistMemberSchema>;
export type ArtistLabel = z.infer<typeof ArtistLabelSchema>;
export type ArtistManagement = z.infer<typeof ArtistManagementSchema>;
export type SocialHandle = z.infer<typeof SocialHandleSchema>;
export type ArtistSpec = z.infer<typeof ArtistSpecSchema>;
export type ArtistAliasRow = z.infer<typeof ArtistAliasRowSchema>;
export type ArtistMemberRow = z.infer<typeof ArtistMemberRowSchema>;
export type ArtistLabelRow = z.infer<typeof ArtistLabelRowSchema>;
export type ArtistSocialHandleRow = z.infer<typeof ArtistSocialHandleRowSchema>;

export type VenueSpec = z.infer<typeof VenueSpecSchema>;
export type FestivalStage = z.infer<typeof FestivalStageSchema>;
export type TicketTier = z.infer<typeof TicketTierSchema>;
export type LineupAnnouncement = z.infer<typeof LineupAnnouncementSchema>;
export type HistoricalEdition = z.infer<typeof HistoricalEditionSchema>;
export type OrganizationRef = z.infer<typeof OrganizationRefSchema>;
export type FestivalSpec = z.infer<typeof FestivalSpecSchema>;
export type FestivalEdition = z.infer<typeof FestivalEditionSchema>;
export type FestivalStageRow = z.infer<typeof FestivalStageRowSchema>;
export type FestivalTicketTierRow = z.infer<typeof FestivalTicketTierRowSchema>;

export type LineupSlot = z.infer<typeof LineupSlotSchema>;
export type LineupObservation = z.infer<typeof LineupObservationSchema>;

export type EntityExternalId = z.infer<typeof EntityExternalIdSchema>;
export type EntityMatchWeight = z.infer<typeof EntityMatchWeightSchema>;
export type EntityMatchFeatures = z.infer<typeof EntityMatchFeaturesSchema>;
export type EntityMatchCandidate = z.infer<typeof EntityMatchCandidateSchema>;

export type ScrapedFestival = z.infer<typeof ScrapedFestivalSchema>;

/**
 * Row schemas keyed by the DuckDB table they mirror. Used by the parity test
 * and by writers that need to validate before an insert.
 */
export const TABLE_SCHEMAS = {
  'core.artists': ArtistSpecSchema,
  'core.artist_aliases': ArtistAliasRowSchema,
  'core.artist_members': ArtistMemberRowSchema,
  'core.artist_labels': ArtistLabelRowSchema,
  'core.artist_social_handles': ArtistSocialHandleRowSchema,
  'core.venues': VenueSpecSchema,
  'core.festivals': FestivalSpecSchema,
  'core.festival_editions': FestivalEditionSchema,
  'core.festival_stages': FestivalStageRowSchema,
  'core.festival_ticket_tiers': FestivalTicketTierRowSchema,
  'core.lineup_slots': LineupSlotSchema,
  'core.entity_external_ids': EntityExternalIdSchema,
  'core.entity_match_weights': EntityMatchWeightSchema,
  'core.entity_match_candidates': EntityMatchCandidateSchema,
  'raw.lineup_observations': LineupObservationSchema,
} as const;
