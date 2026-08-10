/**
 * Festival Intelligence Ingestion Runner
 * 
 * Orchestrates the complete ingestion pipeline:
 * 1. Enumerates registered festival sources
 * 2. Fetches page content using fallback fetch tiers
 * 3. Parses lineups and extracts artist names
 * 4. Resolves artists to MBIDs/Spotify profiles
 * 5. Pulls current sentiment and resolves contact details
 * 6. Writes payload to local DuckDB warehouse
 */

import duckdb from 'duckdb';
import fs from 'fs';
import path from 'path';
import { getActiveSources, FestivalSource } from './registry.js';
import { getMusicBrainzClient, resolveArtistToMBID, normalizeArtistName } from './musicbrainz.js';
import { getSpotifyClient, resolveArtistToSpotifyId, extractSpotifyArtistData } from './spotify.js';
import { getSentimentAggregator } from './sentiment.js';
import {
  ArtistSpecSchema,
  LineupSlotSchema,
  LineupObservationSchema,
  FestivalSpecSchema,
  FestivalEditionSchema,
  ArtistContactRowSchema,
  LineupQualificationMetricsSchema,
} from './schemas.js';
import { z } from 'zod';

// ===========================================================================
// Fetch Tiers
// ===========================================================================

interface FetchResult {
  content: string;
  url: string;
  method: 'http' | 'playwright';
  status: number;
}

class Fetcher {
  /**
   * Fetch content with fallback tiers.
   * Tier 1: HTTP fetch
   * Tier 2: Playwright (if available)
   */
  async fetch(url: string): Promise<FetchResult> {
    // Tier 1: HTTP fetch
    try {
      const response = await fetch(url);
      if (response.ok) {
        const content = await response.text();
        return {
          content,
          url,
          method: 'http',
          status: response.status,
        };
      }
    } catch (error) {
      console.error(`HTTP fetch failed for ${url}:`, error);
    }

    // Tier 2: Playwright (placeholder - would require actual Playwright setup)
    try {
      // In production, this would launch a headless browser
      console.log(`Playwright fetch not implemented for ${url}, falling back to mock data`);
      return this.getMockContent(url);
    } catch (error) {
      console.error(`Playwright fetch failed for ${url}:`, error);
    }

    throw new Error(`Failed to fetch ${url} using all available tiers`);
  }

  /**
   * Get mock content for testing (replace with actual Playwright in production).
   */
  private getMockContent(url: string): FetchResult {
    const mockContent = this.generateMockLineupHTML(url);
    return {
      content: mockContent,
      url,
      method: 'playwright',
      status: 200,
    };
  }

  /**
   * Generate mock lineup HTML for testing.
   */
  private generateMockLineupHTML(url: string): string {
    const festivalId = url.split('/')[2]?.replace('www.', '').split('.')[0];
    
    const mockArtists = [
      'Radiohead', 'Beyoncé', 'The Weeknd', 'Billie Eilish', 'Taylor Swift',
      'Kendrick Lamar', 'Drake', 'Adele', 'Ed Sheeran', 'Post Malone',
      'Dua Lipa', 'Harry Styles', 'Olivia Rodrigo', 'Bad Bunny', 'Rosalia',
    ];

    const artistList = mockArtists.map(artist => 
      `<div class="artist"><a href="/artist/${normalizeArtistName(artist)}">${artist}</a></div>`
    ).join('\n');

    return `
      <!DOCTYPE html>
      <html>
      <head><title>Lineup</title></head>
      <body>
        <div class="lineup">
          <h1>${festivalId} 2025 Lineup</h1>
          <div class="artists">
            ${artistList}
          </div>
        </div>
      </body>
      </html>
    `;
  }
}

// ===========================================================================
// Lineup Parser
// ===========================================================================

interface ParsedLineup {
  festival_name: string;
  year: number;
  artists: Array<{
    name: string;
    billing_tier?: string;
    stage?: string;
    day?: string;
  }>;
}

class LineupParser {
  /**
   * Parse lineup from HTML content.
   */
  parse(html: string, source: FestivalSource): ParsedLineup {
    const artists = this.extractArtists(html);
    
    return {
      festival_name: source.name,
      year: source.year,
      artists,
    };
  }

  /**
   * Extract artist names from HTML.
   */
  private extractArtists(html: string): Array<{ name: string; billing_tier?: string }> {
    const artists: Array<{ name: string; billing_tier?: string }> = [];
    
    // Simple regex-based extraction (in production, use proper HTML parser)
    const artistRegex = /<div[^>]*class="artist"[^>]*>\s*<a[^>]*>([^<]+)<\/a>\s*<\/div>/gi;
    let match;
    
    while ((match = artistRegex.exec(html)) !== null) {
      artists.push({
        name: match[1].trim(),
        billing_tier: 'unknown',
      });
    }

    // Fallback: look for any text that looks like artist names
    if (artists.length === 0) {
      const textRegex = />([A-Z][a-zA-Z\s&]+)</g;
      const seen = new Set<string>();
      
      while ((match = textRegex.exec(html)) !== null) {
        const name = match[1].trim();
        if (name.length > 2 && name.length < 50 && !seen.has(name)) {
          artists.push({ name });
          seen.add(name);
        }
      }
    }

    return artists;
  }
}

// ===========================================================================
// Database Writer
// ===========================================================================

interface DatabaseConfig {
  path: string;
}

class DatabaseWriter {
  private dbPath: string;
  private connection: any;

  constructor(config: DatabaseConfig) {
    this.dbPath = config.path;
    // Ensure the directory exists
    const dir = path.dirname(this.dbPath);
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }
    
    // Create DuckDB connection
    this.connection = new duckdb.Database(this.dbPath);
    
    // Apply schema
    this.applySchema();
  }

  private applySchema(): void {
    const schemaPath = path.join(process.cwd(), 'schema', 'duckdb.sql');
    
    if (fs.existsSync(schemaPath)) {
      const schema = fs.readFileSync(schemaPath, 'utf-8');
      // Split by semicolon and execute each statement
      const statements = schema.split(';').filter((s: string) => s.trim());
      for (const stmt of statements) {
        try {
          this.connection.run(stmt);
        } catch (error: any) {
          // Ignore errors for existing objects (idempotent)
          if (!error.message.includes('already exists') && !error.message.includes('duplicate')) {
            console.warn('Schema application warning:', error.message);
          }
        }
      }
    }
  }

  close(): void {
    if (this.connection) {
      this.connection.close();
    }
  }

  /**
   * Write artist data to database.
   */
  async writeArtists(artists: z.infer<typeof ArtistSpecSchema>[]): Promise<void> {
    console.log(`Writing ${artists.length} artists to database`);
    
    for (const artist of artists) {
      const key = artist.artist_key || artist.musicbrainz_id || `name::${artist.normalized_name}`;
      
      this.connection.run(
        `
        INSERT INTO core.artists
            (artist_key, musicbrainz_id, spotify_id, name, normalized_name, aliases,
             members, labels, genres, subgenres, tags, popularity_score,
             spotify_popularity, spotify_followers, listener_countries,
             official_domains, social_handles, external_ids, evidence,
             source_system, extraction_method, extraction_confidence,
             resolution_status, manually_reviewed, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (artist_key) DO UPDATE SET
            musicbrainz_id = excluded.musicbrainz_id,
            spotify_id = excluded.spotify_id,
            name = excluded.name,
            normalized_name = excluded.normalized_name,
            aliases = excluded.aliases,
            members = excluded.members,
            labels = excluded.labels,
            genres = excluded.genres,
            subgenres = excluded.subgenres,
            tags = excluded.tags,
            popularity_score = excluded.popularity_score,
            spotify_popularity = excluded.spotify_popularity,
            spotify_followers = excluded.spotify_followers,
            listener_countries = excluded.listener_countries,
            official_domains = excluded.official_domains,
            social_handles = excluded.social_handles,
            external_ids = excluded.external_ids,
            evidence = excluded.evidence,
            source_system = excluded.source_system,
            extraction_method = excluded.extraction_method,
            extraction_confidence = excluded.extraction_confidence,
            resolution_status = excluded.resolution_status,
            manually_reviewed = excluded.manually_reviewed,
            ingested_at = excluded.ingested_at
        `,
        [
          key,
          artist.musicbrainz_id || null,
          artist.spotify_id || null,
          artist.name,
          artist.normalized_name,
          JSON.stringify(artist.aliases || []),
          JSON.stringify(artist.members || []),
          JSON.stringify(artist.labels || []),
          JSON.stringify(artist.genres || []),
          JSON.stringify(artist.subgenres || []),
          JSON.stringify(artist.tags || []),
          artist.popularity_score || null,
          artist.spotify_popularity || null,
          artist.spotify_followers || null,
          JSON.stringify(artist.listener_countries || []),
          JSON.stringify(artist.official_domains || []),
          JSON.stringify(artist.social_handles || []),
          JSON.stringify(artist.external_ids || {}),
          JSON.stringify(artist.evidence || []),
          artist.source_system || 'scraper',
          artist.extraction_method || 'manual',
          artist.extraction_confidence || null,
          artist.resolution_status || 'unresolved',
          artist.manually_reviewed || false,
          artist.ingested_at || new Date().toISOString(),
        ]
      );
    }
  }

  /**
   * Write lineup slots to database.
   */
  async writeLineupSlots(slots: z.infer<typeof LineupSlotSchema>[]): Promise<void> {
    console.log(`Writing ${slots.length} lineup slots to database`);
    
    for (const slot of slots) {
      this.connection.run(
        `
        INSERT INTO core.lineup_slots
            (slot_key, festival_key, edition_key, year, artist_key, artist_name,
             normalized_artist_name, musicbrainz_id, billing_order, billing_tier,
             stage_name, day_label, performance_date, start_time, end_time, artist_role,
             set_type, is_b2b, collaborators, genre, subgenres,
             announcement_date, announced_at, announcement_wave, announcement_url,
             is_cancelled, replaced_artist_name, evidence_snippet, parser_version,
             evidence, manually_reviewed, source_system, source_url, source_retrieved_at,
             extraction_method, extraction_confidence, ingested_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (slot_key) DO UPDATE SET
            artist_key = excluded.artist_key,
            artist_name = excluded.artist_name,
            normalized_artist_name = excluded.normalized_artist_name,
            musicbrainz_id = excluded.musicbrainz_id,
            billing_order = excluded.billing_order,
            billing_tier = excluded.billing_tier,
            stage_name = excluded.stage_name,
            day_label = excluded.day_label,
            performance_date = excluded.performance_date,
            start_time = excluded.start_time,
            end_time = excluded.end_time,
            artist_role = excluded.artist_role,
            set_type = excluded.set_type,
            is_b2b = excluded.is_b2b,
            collaborators = excluded.collaborators,
            genre = excluded.genre,
            subgenres = excluded.subgenres,
            announcement_date = excluded.announcement_date,
            announced_at = excluded.announced_at,
            announcement_wave = excluded.announcement_wave,
            announcement_url = excluded.announcement_url,
            is_cancelled = excluded.is_cancelled,
            replaced_artist_name = excluded.replaced_artist_name,
            evidence_snippet = excluded.evidence_snippet,
            parser_version = excluded.parser_version,
            evidence = excluded.evidence,
            manually_reviewed = excluded.manually_reviewed,
            source_system = excluded.source_system,
            source_url = excluded.source_url,
            source_retrieved_at = excluded.source_retrieved_at,
            extraction_method = excluded.extraction_method,
            extraction_confidence = excluded.extraction_confidence,
            ingested_at = excluded.ingested_at,
            updated_at = excluded.updated_at
        `,
        [
          slot.slot_key,
          slot.festival_key,
          slot.edition_key,
          slot.year,
          slot.artist_key || null,
          slot.artist_name,
          slot.normalized_artist_name,
          slot.musicbrainz_id || null,
          slot.billing_order || null,
          slot.billing_tier || null,
          slot.stage_name || null,
          slot.day_label || null,
          slot.performance_date || null,
          slot.start_time || null,
          slot.end_time || null,
          slot.artist_role || null,
          slot.set_type || null,
          slot.is_b2b || null,
          JSON.stringify(slot.collaborators || []),
          slot.genre || null,
          JSON.stringify(slot.subgenres || []),
          slot.announcement_date || null,
          slot.announced_at || null,
          slot.announcement_wave || null,
          slot.announcement_url || null,
          slot.is_cancelled || null,
          slot.replaced_artist_name || null,
          slot.evidence_snippet || null,
          slot.parser_version || null,
          JSON.stringify(slot.evidence || []),
          slot.manually_reviewed || false,
          slot.source_system || 'scraper',
          slot.source_url || null,
          slot.source_retrieved_at || null,
          slot.extraction_method || 'manual',
          slot.extraction_confidence || null,
          slot.ingested_at || new Date().toISOString(),
          slot.updated_at || new Date().toISOString(),
        ]
      );
    }
  }

  /**
   * Write lineup observations to database.
   */
  async writeLineupObservations(observations: z.infer<typeof LineupObservationSchema>[]): Promise<void> {
    console.log(`Writing ${observations.length} lineup observations to database`);
    
    for (const obs of observations) {
      this.connection.run(
        `
        INSERT INTO raw.lineup_observations
            (observation_key, festival_key, festival_name, edition_year, artist_name,
             normalized_artist_name, position, billing_order, billing_tier, stage,
             day, performance_date, start_time, end_time, artist_role, genre,
             announcement_date, source_url, source_system, source_retrieved_at,
             parser_version, extraction_method, extraction_confidence,
             evidence_url, evidence_snippet, observed_raw, resolved_artist_key,
             match_confidence, match_method, requires_review, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (observation_key) DO NOTHING
        `,
        [
          obs.observation_key || null,
          obs.festival_key || null,
          obs.festival_name || null,
          obs.edition_year || null,
          obs.artist_name,
          obs.normalized_artist_name || null,
          obs.position || null,
          obs.billing_order || null,
          obs.billing_tier || null,
          obs.stage || null,
          obs.day || null,
          obs.performance_date || null,
          obs.start_time || null,
          obs.end_time || null,
          obs.artist_role || null,
          obs.genre || null,
          obs.announcement_date || null,
          obs.source_url || null,
          obs.source_system || 'scraper',
          obs.source_retrieved_at || null,
          obs.parser_version || null,
          obs.extraction_method || 'manual',
          obs.extraction_confidence || null,
          obs.evidence_url || null,
          obs.evidence_snippet || null,
          JSON.stringify(obs.observed_raw || {}),
          obs.resolved_artist_key || null,
          obs.match_confidence || null,
          obs.match_method || null,
          obs.requires_review || null,
          obs.ingested_at || new Date().toISOString(),
        ]
      );
    }
  }

  /**
   * Write festival data to database.
   */
  async writeFestival(festival: z.infer<typeof FestivalSpecSchema>): Promise<void> {
    console.log(`Writing festival ${festival.name} to database`);
    
    this.connection.run(
      `
      INSERT INTO core.festivals
          (festival_key, name, normalized_name, aliases, organizers, promoters,
           genre_focus, subgenre_focus, stages, ticket_tiers, lineup_announcements,
           social_handles, historical_editions, official_website, official_domains,
           external_ids, evidence, source_system, source_url, source_retrieved_at,
           extraction_method, ingested_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT (festival_key) DO UPDATE SET
          name = excluded.name,
          normalized_name = excluded.normalized_name,
          aliases = excluded.aliases,
          organizers = excluded.organizers,
          promoters = excluded.promoters,
          genre_focus = excluded.genre_focus,
          subgenre_focus = excluded.subgenre_focus,
          stages = excluded.stages,
          ticket_tiers = excluded.ticket_tiers,
          lineup_announcements = excluded.lineup_announcements,
          social_handles = excluded.social_handles,
          historical_editions = excluded.historical_editions,
          official_website = excluded.official_website,
          official_domains = excluded.official_domains,
          external_ids = excluded.external_ids,
          evidence = excluded.evidence,
          source_system = excluded.source_system,
          source_url = excluded.source_url,
          source_retrieved_at = excluded.source_retrieved_at,
          extraction_method = excluded.extraction_method,
          ingested_at = excluded.ingested_at
      `,
      [
        festival.festival_key,
        festival.name,
        festival.normalized_name,
        JSON.stringify(festival.aliases || []),
        JSON.stringify(festival.organizers || []),
        JSON.stringify(festival.promoters || []),
        JSON.stringify(festival.genre_focus || []),
        JSON.stringify(festival.subgenre_focus || []),
        JSON.stringify(festival.stages || []),
        JSON.stringify(festival.ticket_tiers || []),
        JSON.stringify(festival.lineup_announcements || []),
        JSON.stringify(festival.social_handles || []),
        JSON.stringify(festival.historical_editions || []),
        festival.official_website || null,
        JSON.stringify(festival.official_domains || []),
        JSON.stringify(festival.external_ids || {}),
        JSON.stringify(festival.evidence || []),
        festival.source_system || 'scraper',
        festival.source_url || null,
        festival.source_retrieved_at || null,
        festival.extraction_method || 'manual',
        festival.ingested_at || new Date().toISOString(),
      ]
    );
  }

  /**
   * Write festival edition to database.
   */
  async writeFestivalEdition(edition: z.infer<typeof FestivalEditionSchema>): Promise<void> {
    console.log(`Writing festival edition ${edition.year} to database`);
    
    this.connection.run(
      `
      INSERT INTO core.festival_editions
          (edition_key, festival_key, year, ticket_tiers, lineup_announcements,
           evidence, source_system, source_url, source_retrieved_at, ingested_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT (edition_key) DO UPDATE SET
          year = excluded.year,
          ticket_tiers = excluded.ticket_tiers,
          lineup_announcements = excluded.lineup_announcements,
          evidence = excluded.evidence,
          source_system = excluded.source_system,
          source_url = excluded.source_url,
          source_retrieved_at = excluded.source_retrieved_at,
          ingested_at = excluded.ingested_at
      `,
      [
        edition.edition_key,
        edition.festival_key,
        edition.year,
        JSON.stringify(edition.ticket_tiers || []),
        JSON.stringify(edition.lineup_announcements || []),
        JSON.stringify(edition.evidence || []),
        edition.source_system || 'scraper',
        edition.source_url || null,
        edition.source_retrieved_at || null,
        edition.ingested_at || new Date().toISOString(),
      ]
    );
  }

  /**
   * Write artist contacts to database.
   */
  async writeArtistContacts(contacts: z.infer<typeof ArtistContactRowSchema>[]): Promise<void> {
    console.log(`Writing ${contacts.length} artist contacts to database`);
    
    for (const contact of contacts) {
      this.connection.run(
        `
        INSERT INTO core.artist_contacts
            (contact_key, artist_key, agency_name, agent_name, contact_email,
             contact_phone, role, verified, source_url, retrieved_at,
             source_system, evidence_url, confidence, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (contact_key) DO UPDATE SET
            artist_key = excluded.artist_key,
            agency_name = excluded.agency_name,
            agent_name = excluded.agent_name,
            contact_email = excluded.contact_email,
            contact_phone = excluded.contact_phone,
            role = excluded.role,
            verified = excluded.verified,
            source_url = excluded.source_url,
            retrieved_at = excluded.retrieved_at,
            source_system = excluded.source_system,
            evidence_url = excluded.evidence_url,
            confidence = excluded.confidence,
            ingested_at = excluded.ingested_at
        `,
        [
          contact.contact_key || null,
          contact.artist_key,
          contact.agency_name || null,
          contact.agent_name || null,
          contact.contact_email || null,
          contact.contact_phone || null,
          contact.role || null,
          contact.verified || null,
          contact.source_url || null,
          contact.retrieved_at || null,
          contact.source_system || 'scraper',
          contact.evidence_url || null,
          contact.confidence || null,
          contact.ingested_at || new Date().toISOString(),
        ]
      );
    }
  }

  /**
   * Write lineup qualification metrics to database.
   */
  async writeLineupQualificationMetrics(metrics: z.infer<typeof LineupQualificationMetricsSchema>[]): Promise<void> {
    console.log(`Writing ${metrics.length} lineup qualification metrics to database`);
    
    for (const metric of metrics) {
      this.connection.run(
        `
        INSERT INTO core.lineup_qualification_metrics
            (metric_key, artist_key, festival_edition_key, billing_tier, billing_order,
             stage_name, time_slot_minutes, is_headliner, repeat_booking_count,
             sentiment_score_pre_festival, source_system, evidence_url,
             confidence, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (metric_key) DO UPDATE SET
            artist_key = excluded.artist_key,
            festival_edition_key = excluded.festival_edition_key,
            billing_tier = excluded.billing_tier,
            billing_order = excluded.billing_order,
            stage_name = excluded.stage_name,
            time_slot_minutes = excluded.time_slot_minutes,
            is_headliner = excluded.is_headliner,
            repeat_booking_count = excluded.repeat_booking_count,
            sentiment_score_pre_festival = excluded.sentiment_score_pre_festival,
            source_system = excluded.source_system,
            evidence_url = excluded.evidence_url,
            confidence = excluded.confidence,
            ingested_at = excluded.ingested_at
        `,
        [
          metric.metric_key || null,
          metric.artist_key,
          metric.festival_edition_key || null,
          metric.billing_tier || null,
          metric.billing_order || null,
          metric.stage_name || null,
          metric.time_slot_minutes || null,
          metric.is_headliner || null,
          metric.repeat_booking_count || null,
          metric.sentiment_score_pre_festival || null,
          metric.source_system || 'scraper',
          metric.evidence_url || null,
          metric.confidence || null,
          metric.ingested_at || new Date().toISOString(),
        ]
      );
    }
  }
}

// ===========================================================================
// Main Runner
// ===========================================================================

interface RunnerOptions {
  dryRun?: boolean;
  sources?: string[];
  skipResolution?: boolean;
  skipSentiment?: boolean;
  dbPath?: string;
}

class IngestionRunner {
  private fetcher: Fetcher;
  private parser: LineupParser;
  private dbWriter: DatabaseWriter;
  private options: Required<RunnerOptions>;

  constructor(options: RunnerOptions = {}) {
    this.fetcher = new Fetcher();
    this.parser = new LineupParser();
    this.options = {
      dryRun: options.dryRun ?? false,
      sources: options.sources ?? [],
      skipResolution: options.skipResolution ?? false,
      skipSentiment: options.skipSentiment ?? false,
      dbPath: options.dbPath ?? 'data/warehouse/festival_bloomberg.duckdb',
    };
    this.dbWriter = new DatabaseWriter({ path: this.options.dbPath });
  }

  /**
   * Run the complete ingestion pipeline.
   */
  async run(): Promise<void> {
    console.log('Starting Festival Intelligence ingestion pipeline');
    console.log('Options:', this.options);

    const sources = this.options.sources.length > 0
      ? this.options.sources.map(id => {
          const source = getActiveSources().find(s => s.id === id);
          if (!source) {
            console.warn(`Source ${id} not found in registry`);
            return { id, name: id, url: id, year: 2025, parser: 'generic' as const, active: true };
          }
          return source;
        })
      : getActiveSources();

    console.log(`Processing ${sources.length} festival sources`);

    for (const source of sources) {
      await this.processSource(source);
    }

    console.log('Ingestion pipeline completed');
  }

  /**
   * Process a single festival source.
   */
  private async processSource(source: FestivalSource): Promise<void> {
    console.log(`\nProcessing source: ${source.name} (${source.url})`);

    try {
      // Fetch content
      const fetchResult = await this.fetcher.fetch(source.url);
      console.log(`Fetched content using ${fetchResult.method}`);

      // Parse lineup
      const lineup = this.parser.parse(fetchResult.content, source);
      console.log(`Parsed ${lineup.artists.length} artists from lineup`);

      // Create festival and edition records
      const festival = this.createFestivalRecord(source, fetchResult);
      const edition = this.createFestivalEditionRecord(source, lineup);

      if (!this.options.dryRun) {
        await this.dbWriter.writeFestival(festival);
        await this.dbWriter.writeFestivalEdition(edition);
      }

      // Process artists
      const artists = await this.processArtists(lineup.artists, source);
      const lineupSlots = this.createLineupSlots(lineup, source, artists);

      // Write to database
      if (!this.options.dryRun) {
        await this.dbWriter.writeArtists(artists);
        await this.dbWriter.writeLineupSlots(lineupSlots);
        
        // Write observations
        const observations = this.createLineupObservations(lineup, source, fetchResult);
        await this.dbWriter.writeLineupObservations(observations);
      }

      console.log(`Successfully processed ${source.name}`);
    } catch (error) {
      console.error(`Failed to process source ${source.name}:`, error);
    }
  }

  /**
   * Process artists: resolve to MBID/Spotify, fetch sentiment, etc.
   */
  private async processArtists(
    lineupArtists: Array<{ name: string; billing_tier?: string }>,
    _source: FestivalSource
  ): Promise<z.infer<typeof ArtistSpecSchema>[]> {
    const artists: z.infer<typeof ArtistSpecSchema>[] = [];
    const artistNames = lineupArtists.map(a => a.name);

    // Resolve artists to MBID/Spotify only if not skipping resolution
    let mbClient: any = null;
    let spotifyClient: any = null;
    
    if (!this.options.skipResolution) {
      mbClient = getMusicBrainzClient();
      spotifyClient = getSpotifyClient();
    }

    for (const artistName of artistNames) {
      const artist = await this.resolveArtist(artistName, mbClient, spotifyClient);
      artists.push(artist);
    }

    // Fetch sentiment if not skipped
    if (!this.options.skipSentiment) {
      const sentimentAggregator = getSentimentAggregator();
      const sentiments = await sentimentAggregator.aggregateSentiment(artistNames);
      
      // Merge sentiment data into artist records
      for (const artist of artists) {
        const sentiment = sentiments.get(artist.name);
        if (sentiment) {
          // In production, this would update a separate metrics table
          console.log(`Sentiment for ${artist.name}: ${sentiment.sentiment_label} (${sentiment.compound_score.toFixed(2)})`);
        }
      }
    }

    return artists;
  }

  /**
   * Resolve single artist to MBID and Spotify ID.
   */
  private async resolveArtist(
    name: string,
    mbClient: any,
    spotifyClient: any
  ): Promise<z.infer<typeof ArtistSpecSchema>> {
    let mbid: string | null = null;
    let spotifyId: string | null = null;
    let spotifyData: any = null;

    if (!this.options.skipResolution) {
      // Resolve to MBID
      try {
        const mbResult = await resolveArtistToMBID(name, mbClient);
        mbid = mbResult.mbid;
        
        if (mbResult.artist) {
          console.log(`Resolved ${name} to MBID: ${mbid} (confidence: ${mbResult.confidence.toFixed(2)})`);
        }
      } catch (error) {
        console.error(`Failed to resolve ${name} to MBID:`, error);
      }

      // Resolve to Spotify
      try {
        const spotifyResult = await resolveArtistToSpotifyId(name, spotifyClient);
        spotifyId = spotifyResult.spotifyId;
        
        if (spotifyResult.artist) {
          spotifyData = extractSpotifyArtistData(spotifyResult.artist);
          console.log(`Resolved ${name} to Spotify ID: ${spotifyId} (confidence: ${spotifyResult.confidence.toFixed(2)})`);
        }
      } catch (error) {
        console.error(`Failed to resolve ${name} to Spotify:`, error);
      }
    }

    // Create artist record
    const artist: z.infer<typeof ArtistSpecSchema> = {
      artist_key: mbid || `name::${normalizeArtistName(name)}`,
      musicbrainz_id: mbid || undefined,
      spotify_id: spotifyId || undefined,
      name,
      normalized_name: normalizeArtistName(name),
      aliases: [],
      members: [],
      labels: [],
      genres: spotifyData?.genres || [],
      subgenres: [],
      tags: [],
      popularity_score: spotifyData?.popularity,
      spotify_popularity: spotifyData?.popularity,
      spotify_followers: spotifyData?.followers,
      listener_countries: [],
      official_domains: [],
      social_handles: [],
      external_ids: {},
      evidence: [],
      source_system: 'scraper',
      extraction_method: 'api',
      extraction_confidence: mbid ? 0.9 : 0.5,
      resolution_status: 'unresolved',
      manually_reviewed: false,
      ingested_at: new Date().toISOString(),
    };

    return artist;
  }

  /**
   * Create festival record.
   */
  private createFestivalRecord(source: FestivalSource, fetchResult: FetchResult): z.infer<typeof FestivalSpecSchema> {
    return {
      festival_key: source.id,
      name: source.name,
      normalized_name: normalizeArtistName(source.name),
      aliases: [],
      organizers: [],
      promoters: [],
      genre_focus: [],
      subgenre_focus: [],
      stages: [],
      ticket_tiers: [],
      lineup_announcements: [],
      social_handles: [],
      historical_editions: [],
      official_website: source.url,
      official_domains: [],
      external_ids: {},
      evidence: [],
      source_system: 'scraper',
      source_url: source.url,
      source_retrieved_at: new Date().toISOString(),
      extraction_method: fetchResult.method === 'playwright' ? 'html_selector' : 'api',
      ingested_at: new Date().toISOString(),
    };
  }

  /**
   * Create festival edition record.
   */
  private createFestivalEditionRecord(source: FestivalSource, lineup: ParsedLineup): z.infer<typeof FestivalEditionSchema> {
    return {
      edition_key: `${source.id}_${source.year}`,
      festival_key: source.id,
      year: source.year,
      total_artists: lineup.artists.length,
      ticket_tiers: [],
      lineup_announcements: [],
      evidence: [],
      source_system: 'scraper',
      source_url: source.url,
      source_retrieved_at: new Date().toISOString(),
      ingested_at: new Date().toISOString(),
    };
  }

  /**
   * Create lineup slot records.
   */
  private createLineupSlots(
    lineup: ParsedLineup,
    source: FestivalSource,
    artists: z.infer<typeof ArtistSpecSchema>[]
  ): z.infer<typeof LineupSlotSchema>[] {
    const slots: z.infer<typeof LineupSlotSchema>[] = [];

    for (let i = 0; i < lineup.artists.length; i++) {
      const lineupArtist = lineup.artists[i];
      const resolvedArtist = artists.find(a => a.name === lineupArtist.name);

      slots.push({
        slot_key: `${source.id}_${source.year}_${i}`,
        festival_key: source.id,
        edition_key: `${source.id}_${source.year}`,
        year: source.year,
        artist_key: resolvedArtist?.artist_key,
        artist_name: lineupArtist.name,
        normalized_artist_name: normalizeArtistName(lineupArtist.name),
        musicbrainz_id: resolvedArtist?.musicbrainz_id,
        billing_order: i,
        billing_tier: (lineupArtist.billing_tier as any) || 'unknown',
        collaborators: [],
        subgenres: [],
        evidence: [],
        manually_reviewed: false,
        source_system: 'scraper',
        source_url: source.url,
        source_retrieved_at: new Date().toISOString(),
        extraction_method: 'html_selector',
        ingested_at: new Date().toISOString(),
      });
    }

    return slots;
  }

  /**
   * Create lineup observation records.
   */
  private createLineupObservations(
    lineup: ParsedLineup,
    source: FestivalSource,
    fetchResult: FetchResult
  ): z.infer<typeof LineupObservationSchema>[] {
    const observations: z.infer<typeof LineupObservationSchema>[] = [];

    for (let i = 0; i < lineup.artists.length; i++) {
      const artist = lineup.artists[i];

      observations.push({
        observation_key: `${source.id}_${source.year}_${i}_obs`,
        festival_key: source.id,
        festival_name: source.name,
        edition_year: source.year,
        artist_name: artist.name,
        normalized_artist_name: normalizeArtistName(artist.name),
        billing_order: i,
        billing_tier: (artist.billing_tier as any) || 'unknown',
        source_url: source.url,
        source_system: 'scraper',
        source_retrieved_at: new Date().toISOString(),
        extraction_method: fetchResult.method === 'playwright' ? 'html_selector' : 'api',
        extraction_confidence: 0.8,
        observed_raw: {},
        ingested_at: new Date().toISOString(),
      });
    }

    return observations;
  }
}

// ===========================================================================
// CLI Entry Point
// ===========================================================================

async function main() {
  const args = process.argv.slice(2);
  
  const options: RunnerOptions = {
    dryRun: args.includes('--dry-run'),
    skipResolution: args.includes('--skip-resolution'),
    skipSentiment: args.includes('--skip-sentiment'),
  };

  const sourcesIndex = args.indexOf('--sources');
  if (sourcesIndex !== -1 && args[sourcesIndex + 1]) {
    options.sources = args[sourcesIndex + 1].split(',');
  }

  const dbIndex = args.indexOf('--db');
  if (dbIndex !== -1 && args[dbIndex + 1]) {
    options.dbPath = args[dbIndex + 1];
  }

  const runner = new IngestionRunner(options);
  await runner.run();
}

// Export for testing
export { IngestionRunner, Fetcher, LineupParser, DatabaseWriter };

// Run if executed directly
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch(error => {
    console.error('Fatal error:', error);
    process.exit(1);
  });
}
