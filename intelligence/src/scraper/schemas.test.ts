import { describe, expect, it } from 'vitest';

import {
  ArtistAliasInputSchema,
  ArtistSpecSchema,
  DEFAULT_ENTITY_MATCH_WEIGHTS,
  EntityExternalIdSchema,
  EntityMatchCandidateSchema,
  EntityMatchWeightSchema,
  FestivalEditionSchema,
  FestivalSpecSchema,
  LineupObservationSchema,
  LineupSlotSchema,
  ScrapedFestivalSchema,
  VenueSpecSchema,
  blockingKey,
  normalizeDomain,
  normalizeHandle,
  normalizeName,
  withDerivedNameKeys,
} from './schemas';

const RADIOHEAD_MBID = 'a74b1b7f-36a9-4d22-a1cf-017dc00396d0';

describe('ArtistSpecSchema', () => {
  const richArtist = {
    musicbrainz_id: RADIOHEAD_MBID,
    name: 'Radiohead',
    normalized_name: 'radiohead',
    sort_name: 'Radiohead',
    aliases: ['On a Friday', { alias: 'Radio Head', alias_type: 'misspelling' as const }],
    country: 'gb',
    origin_city: 'Abingdon',
    origin_region: 'Oxfordshire',
    type: 'group' as const,
    primary_genre: 'alternative rock',
    genres: ['alternative rock', 'art rock'],
    subgenres: ['experimental rock'],
    formation_date: '1985-01-01',
    active_status: 'active' as const,
    is_active: true,
    members: [
      { member_name: 'Thom Yorke', member_role: 'vocals', instruments: ['guitar'], is_current: true },
    ],
    member_count: 5,
    labels: [{ label_name: 'XL Recordings', relationship_type: 'label' as const, is_current: true }],
    management: { manager_name: 'Chris Hufford', booking_agency: 'Wasserman Music' },
    official_website: 'https://www.radiohead.com',
    official_domains: ['radiohead.com'],
    social_handles: [
      { platform: 'instagram' as const, handle: 'radiohead', is_verified: true, follower_count: 3_000_000 },
    ],
    spotify_id: '4Z8W4fKeB5YxbusRsdQVPb',
    wikidata_id: 'Q4013',
    spotify_popularity: 79,
    spotify_followers: 9_500_000,
    popularity_score: 82.5,
    popularity_source: 'spotify' as const,
    popularity_observed_at: '2026-01-15T10:00:00Z',
    evidence: [
      {
        source_url: 'https://musicbrainz.org/artist/a74b1b7f-36a9-4d22-a1cf-017dc00396d0',
        source_system: 'musicbrainz' as const,
        retrieved_at: '2026-01-15T10:00:00Z',
        confidence: 1,
      },
    ],
    extraction_confidence: 0.98,
    source_system: 'musicbrainz' as const,
  };

  it('parses a fully specified artist', () => {
    const parsed = ArtistSpecSchema.parse(richArtist);

    expect(parsed.musicbrainz_id).toBe(RADIOHEAD_MBID);
    expect(parsed.members[0]?.member_name).toBe('Thom Yorke');
    expect(parsed.labels[0]?.label_name).toBe('XL Recordings');
    expect(parsed.management?.booking_agency).toBe('Wasserman Music');
    expect(parsed.official_domains).toEqual(['radiohead.com']);
    expect(parsed.social_handles[0]?.platform).toBe('instagram');
    expect(parsed.subgenres).toEqual(['experimental rock']);
    expect(parsed.formation_date).toBe('1985-01-01');
    expect(parsed.evidence[0]?.source_system).toBe('musicbrainz');
  });

  it('uppercases ISO country codes', () => {
    expect(ArtistSpecSchema.parse(richArtist).country).toBe('GB');
  });

  it('applies collection and resolution defaults', () => {
    const minimal = ArtistSpecSchema.parse({ name: 'Fred again..', normalized_name: 'fred again' });

    expect(minimal.aliases).toEqual([]);
    expect(minimal.genres).toEqual([]);
    expect(minimal.external_ids).toEqual({});
    expect(minimal.evidence).toEqual([]);
    expect(minimal.manually_reviewed).toBe(false);
    expect(minimal.resolution_status).toBe('unresolved');
  });

  it('rejects malformed external identifiers and out-of-range confidence', () => {
    expect(ArtistSpecSchema.safeParse({ ...richArtist, musicbrainz_id: 'not-a-uuid' }).success).toBe(false);
    expect(ArtistSpecSchema.safeParse({ ...richArtist, wikidata_id: '4013' }).success).toBe(false);
    expect(ArtistSpecSchema.safeParse({ ...richArtist, spotify_id: 'too-short' }).success).toBe(false);
    expect(ArtistSpecSchema.safeParse({ ...richArtist, extraction_confidence: 1.4 }).success).toBe(false);
    expect(ArtistSpecSchema.safeParse({ ...richArtist, country: 'GBR' }).success).toBe(false);
  });

  it('accepts aliases as bare strings or descriptors', () => {
    expect(ArtistAliasInputSchema.parse('On a Friday')).toMatchObject({
      alias: 'On a Friday',
      alias_type: 'alias',
      is_primary: false,
    });
    expect(ArtistAliasInputSchema.parse({ alias: 'Radio Head', alias_type: 'misspelling' })).toMatchObject({
      alias_type: 'misspelling',
    });
  });
});

describe('FestivalSpecSchema', () => {
  const coachella = {
    name: 'Coachella Valley Music and Arts Festival',
    normalized_name: 'coachella valley music and arts festival',
    aliases: ['Coachella'],
    location_city: 'Indio',
    location_region: 'California',
    location_country: 'US',
    latitude: 33.6797,
    longitude: -116.2377,
    time_zone: 'America/Los_Angeles',
    venue_name: 'Empire Polo Club',
    venue_type: 'outdoor' as const,
    capacity: 125_000,
    daily_capacity: 125_000,
    capacity_basis: 'daily' as const,
    duration_days: 3,
    typical_month: 4,
    genre_focus: ['indie', 'electronic', 'hip-hop'],
    organizer: 'Goldenvoice',
    organizers: [{ name: 'Goldenvoice', organization_role: 'organizer' as const, country: 'US' }],
    promoter: 'AEG Presents',
    parent_company: 'AEG',
    stage_count: 8,
    stages: [{ stage_name: 'Coachella Stage', stage_type: 'main' as const, stage_rank: 1 }],
    ticket_tiers: [
      {
        tier_name: 'General Admission',
        tier_type: 'ga' as const,
        price: 549,
        fees: 65.5,
        currency: 'usd',
        sold_out: true,
        sold_out_at: '2026-01-16T18:20:00Z',
      },
    ],
    currency: 'USD',
    ticket_price_min: 549,
    ticket_price_max: 1_269,
    on_sale_date: '2026-01-16',
    sellout_status: 'sold_out' as const,
    sold_out: true,
    sold_out_at: '2026-01-16T18:20:00Z',
    sellout_duration_hours: 2.5,
    lineup_status: 'announced' as const,
    lineup_announced_at: '2026-01-14T17:00:00Z',
    lineup_announcement_url: 'https://www.coachella.com/lineup',
    lineup_announcements: [
      { wave: 'initial', announcement_date: '2026-01-14', artist_count: 158 },
    ],
    official_website: 'https://www.coachella.com',
    official_domains: ['coachella.com'],
    historical_editions: [
      { year: 2025, attendance: 250_000, sold_out: true, headliners: ['Lady Gaga'] },
      { year: 2024, attendance: 250_000, sold_out: true, headliners: ['Lana Del Rey'] },
    ],
    first_edition_year: 1999,
    latest_edition_year: 2026,
    edition_count: 25,
    source_system: 'festival_site' as const,
    source_url: 'https://www.coachella.com',
    source_retrieved_at: '2026-01-20T08:00:00Z',
    source_last_modified: '2026-01-19T22:00:00Z',
    extraction_confidence: 0.91,
  };

  it('parses venue, capacity, organizer, stage, ticket and sellout specifications', () => {
    const parsed = FestivalSpecSchema.parse(coachella);

    expect(parsed.venue_name).toBe('Empire Polo Club');
    expect(parsed.location_country).toBe('US');
    expect(parsed.capacity).toBe(125_000);
    expect(parsed.organizers[0]?.name).toBe('Goldenvoice');
    expect(parsed.promoter).toBe('AEG Presents');
    expect(parsed.stages[0]?.stage_type).toBe('main');
    expect(parsed.ticket_tiers[0]?.currency).toBe('USD');
    expect(parsed.sellout_status).toBe('sold_out');
    expect(parsed.sellout_duration_hours).toBe(2.5);
  });

  it('captures lineup announcements, source timestamps and historical editions', () => {
    const parsed = FestivalSpecSchema.parse(coachella);

    expect(parsed.lineup_announcements[0]?.wave).toBe('initial');
    expect(parsed.lineup_announced_at).toBe('2026-01-14T17:00:00Z');
    expect(parsed.source_retrieved_at).toBe('2026-01-20T08:00:00Z');
    expect(parsed.source_last_modified).toBe('2026-01-19T22:00:00Z');
    expect(parsed.historical_editions.map((edition) => edition.year)).toEqual([2025, 2024]);
  });

  it('rejects an impossible typical month and a non-ISO currency', () => {
    expect(FestivalSpecSchema.safeParse({ ...coachella, typical_month: 13 }).success).toBe(false);
    expect(FestivalSpecSchema.safeParse({ ...coachella, currency: 'dollars' }).success).toBe(false);
  });

  it('parses an edition with sellout and attendance outcomes', () => {
    const edition = FestivalEditionSchema.parse({
      festival_key: 'name::coachella',
      year: 2026,
      edition_label: 'Weekend 1',
      weekend_number: 1,
      start_date: '2026-04-10',
      end_date: '2026-04-12',
      attendance: 125_000,
      headliner_count: 3,
      total_artists: 158,
      sold_out: true,
      sellout_status: 'sold_out',
      sold_out_at: '2026-01-16T18:20:00Z',
      tickets_sold: 125_000,
      gross_revenue: 114_600_000,
      lineup_status: 'final',
    });

    expect(edition.weekend_number).toBe(1);
    expect(edition.gross_revenue).toBe(114_600_000);
    expect(edition.ticket_tiers).toEqual([]);
  });

  it('parses a venue specification', () => {
    const venue = VenueSpecSchema.parse({
      name: 'Empire Polo Club',
      normalized_name: 'empire polo club',
      city: 'Indio',
      region: 'California',
      country: 'US',
      capacity: 125_000,
      is_outdoor: true,
      latitude: 33.6797,
      longitude: -116.2377,
    });

    expect(venue.capacity).toBe(125_000);
    expect(venue.external_ids).toEqual({});
  });
});

describe('LineupSlotSchema', () => {
  const slot = {
    festival_key: 'name::coachella',
    year: 2026,
    artist_key: RADIOHEAD_MBID,
    artist_name: 'Radiohead',
    normalized_artist_name: 'radiohead',
    musicbrainz_id: RADIOHEAD_MBID,
    billing_order: 1,
    billing_tier: 'headliner' as const,
    poster_line: 1,
    is_headliner: true,
    stage_name: 'Coachella Stage',
    performance_date: '2026-04-11',
    day_of_festival: 2,
    start_time: '2026-04-11T23:15:00-07:00',
    end_time: '2026-04-12T00:45:00-07:00',
    local_start_time: '23:15:00',
    local_end_time: '00:45:00',
    time_zone: 'America/Los_Angeles',
    set_duration_minutes: 90,
    artist_role: 'headliner' as const,
    set_type: 'live' as const,
    genre: 'alternative rock',
    subgenres: ['art rock'],
    announcement_date: '2026-01-14',
    announcement_wave: 'initial',
    announcement_url: 'https://www.coachella.com/lineup',
    evidence_url: 'https://www.coachella.com/lineup',
    extraction_confidence: 0.93,
    extraction_method: 'html_selector' as const,
    source_system: 'festival_site' as const,
    match_confidence: 0.99,
    match_method: 'weighted_fuzzy' as const,
  };

  it('parses billing, stage, schedule, role, genre and evidence fields', () => {
    const parsed = LineupSlotSchema.parse(slot);

    expect(parsed.billing_order).toBe(1);
    expect(parsed.billing_tier).toBe('headliner');
    expect(parsed.stage_name).toBe('Coachella Stage');
    expect(parsed.performance_date).toBe('2026-04-11');
    expect(parsed.local_start_time).toBe('23:15:00');
    expect(parsed.set_duration_minutes).toBe(90);
    expect(parsed.artist_role).toBe('headliner');
    expect(parsed.genre).toBe('alternative rock');
    expect(parsed.evidence_url).toBe('https://www.coachella.com/lineup');
    expect(parsed.extraction_confidence).toBe(0.93);
    expect(parsed.announcement_date).toBe('2026-01-14');
    expect(parsed.manually_reviewed).toBe(false);
  });

  it('rejects a negative billing order and an out-of-range extraction confidence', () => {
    expect(LineupSlotSchema.safeParse({ ...slot, billing_order: -1 }).success).toBe(false);
    expect(LineupSlotSchema.safeParse({ ...slot, extraction_confidence: 1.2 }).success).toBe(false);
  });

  it('rejects an unparseable performance date', () => {
    expect(LineupSlotSchema.safeParse({ ...slot, performance_date: '11/04/2026' }).success).toBe(false);
  });

  it('parses a b2b slot with collaborators', () => {
    const parsed = LineupSlotSchema.parse({
      artist_name: 'Sherelle b2b LCY',
      artist_role: 'b2b',
      set_type: 'b2b',
      is_b2b: true,
      collaborators: ['Sherelle', 'LCY'],
    });

    expect(parsed.collaborators).toEqual(['Sherelle', 'LCY']);
  });

  it('keeps raw observations separate from resolved slots', () => {
    const observation = LineupObservationSchema.parse({
      festival_key: 'name::coachella',
      edition_year: 2026,
      artist_name: 'RADIOHEAD',
      normalized_artist_name: 'radiohead',
      position: 'headliner',
      stage: 'Coachella Stage',
      day: 'Saturday',
      source_url: 'https://www.coachella.com/lineup',
      parser_version: '1.0',
      extraction_confidence: 0.8,
      observed_raw: { row: 3, cell: 'RADIOHEAD' },
      requires_review: true,
    });

    expect(observation.observed_raw).toEqual({ row: 3, cell: 'RADIOHEAD' });
    expect(observation.requires_review).toBe(true);
    expect(observation.resolved_artist_key).toBeUndefined();
  });
});

describe('entity resolution contracts', () => {
  it('parses a scored candidate with weighted-fuzzy features', () => {
    const candidate = EntityMatchCandidateSchema.parse({
      entity_type: 'artist',
      source_record_key: 'obs::coachella::2026::radiohead',
      source_name: 'RADIOHEAD',
      normalized_source_name: 'radiohead',
      blocking_key: 'radiohea',
      candidate_entity_key: RADIOHEAD_MBID,
      candidate_musicbrainz_id: RADIOHEAD_MBID,
      name_similarity: 1,
      alias_similarity: 0.82,
      musicbrainz_id_match: true,
      external_id_match: true,
      country_match: true,
      genre_similarity: 0.75,
      social_handle_match: false,
      weighted_score: 0.97,
      match_method: 'weighted_fuzzy',
      match_confidence: 0.97,
      decision: 'accepted',
      feature_scores: { name_similarity: 1, alias_similarity: 0.82 },
    });

    expect(candidate.weighted_score).toBe(0.97);
    expect(candidate.musicbrainz_id_match).toBe(true);
    expect(candidate.decision).toBe('accepted');
    expect(candidate.evidence).toEqual([]);
  });

  it('rejects scores outside the unit interval', () => {
    expect(
      EntityMatchCandidateSchema.safeParse({
        entity_type: 'artist',
        source_record_key: 'obs::1',
        weighted_score: 1.5,
      }).success,
    ).toBe(false);
  });

  it('parses external identifier rows used for exact-match blocking', () => {
    const externalId = EntityExternalIdSchema.parse({
      entity_type: 'artist',
      entity_key: RADIOHEAD_MBID,
      id_type: 'spotify',
      id_value: '4Z8W4fKeB5YxbusRsdQVPb',
      is_primary: true,
      confidence: 1,
    });

    expect(externalId.id_type).toBe('spotify');
    expect(EntityExternalIdSchema.safeParse({ entity_type: 'artist', id_type: 'myspace', id_value: 'x' }).success).toBe(
      false,
    );
  });

  it('parses matcher weights and defaults them to active', () => {
    const weight = EntityMatchWeightSchema.parse({
      entity_type: 'artist',
      feature_name: 'name_similarity',
      weight: 0.45,
      threshold_accept: 0.9,
      threshold_review: 0.7,
    });

    expect(weight.is_active).toBe(true);
    expect(EntityMatchWeightSchema.safeParse({ entity_type: 'artist', feature_name: 'x', weight: 2 }).success).toBe(
      false,
    );
  });

  it('exposes default weights that validate against the weight schema', () => {
    for (const weight of DEFAULT_ENTITY_MATCH_WEIGHTS) {
      expect(EntityMatchWeightSchema.safeParse(weight).success).toBe(true);
    }
    expect(DEFAULT_ENTITY_MATCH_WEIGHTS.length).toBeGreaterThan(0);
  });
});

describe('normalization helpers', () => {
  it('folds diacritics, case and punctuation', () => {
    expect(normalizeName('Beyoncé')).toBe('beyonce');
    expect(normalizeName('  Sigur   Rós!  ')).toBe('sigur ros');
    expect(normalizeName('AC/DC')).toBe('acdc');
    expect(normalizeName("Guns N' Roses")).toBe("guns n' roses");
  });

  it('normalizes handles and domains', () => {
    expect(normalizeHandle('@Radiohead')).toBe('radiohead');
    expect(normalizeDomain('https://www.Coachella.com/lineup')).toBe('coachella.com');
    expect(normalizeDomain('WWW.GLASTONBURYFESTIVALS.CO.UK')).toBe('glastonburyfestivals.co.uk');
  });

  it('derives short blocking keys for candidate generation', () => {
    expect(blockingKey('Radiohead')).toBe('radiohea');
    expect(blockingKey('The Weeknd')).toBe('theweekn');
    expect(blockingKey('AC/DC')).toBe('acdc');
  });

  it('fills normalized_name and blocking_key from name', () => {
    const derived = withDerivedNameKeys({ name: 'Sigur Rós' });

    expect(derived.normalized_name).toBe('sigur ros');
    expect(derived.blocking_key).toBe('sigurros');
    expect(ArtistSpecSchema.parse(derived).normalized_name).toBe('sigur ros');
  });
});

describe('ScrapedFestivalSchema', () => {
  it('validates a full scrape envelope', () => {
    const envelope = ScrapedFestivalSchema.parse({
      festival: withDerivedNameKeys({ name: 'Primavera Sound', location_country: 'ES' }),
      editions: [{ year: 2026, start_date: '2026-06-04', end_date: '2026-06-06' }],
      stages: [{ stage_name: 'Estrella Damm', stage_type: 'main' }],
      ticket_tiers: [{ tier_name: 'Full Festival Ticket', tier_type: 'ga', price: 295, currency: 'EUR' }],
      lineup: [{ artist_name: 'Charli XCX', billing_tier: 'headliner', billing_order: 1 }],
      observations: [{ artist_name: 'Charli XCX', position: 'headliner' }],
      scraped_at: '2026-02-01T09:30:00Z',
      scraper_version: '2.0.0',
      source_system: 'festival_site',
      source_url: 'https://www.primaverasound.com',
    });

    expect(envelope.festival.normalized_name).toBe('primavera sound');
    expect(envelope.lineup[0]?.billing_tier).toBe('headliner');
    expect(envelope.ticket_tiers[0]?.currency).toBe('EUR');
    expect(envelope.editions[0]?.year).toBe(2026);
  });
});
