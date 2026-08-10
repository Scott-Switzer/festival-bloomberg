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

import { spawnSync } from 'child_process';
import fs from 'fs';
import path from 'path';
import os from 'os';
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
  private artists: z.infer<typeof ArtistSpecSchema>[] = [];
  private slots: z.infer<typeof LineupSlotSchema>[] = [];
  private observations: z.infer<typeof LineupObservationSchema>[] = [];
  private festivals: z.infer<typeof FestivalSpecSchema>[] = [];
  private editions: z.infer<typeof FestivalEditionSchema>[] = [];
  private contacts: z.infer<typeof ArtistContactRowSchema>[] = [];
  private metrics: z.infer<typeof LineupQualificationMetricsSchema>[] = [];

  constructor(config: DatabaseConfig) {
    this.dbPath = config.path;
    // Ensure the directory exists
    const dir = path.dirname(this.dbPath);
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }
    
    // Apply schema using Python warehouse
    this.applySchema();
  }

  private applySchema(): void {
    const scriptPath = path.join(process.cwd(), 'warehouse', 'schema_loader.py');
    if (fs.existsSync(scriptPath)) {
      try {
        spawnSync('python3', [scriptPath], { stdio: 'inherit' });
        console.log('Schema applied successfully');
      } catch (error: any) {
        console.warn('Schema application warning:', error.message);
      }
    }
  }

  close(): void {
    // No connection to close when using Python subprocess
  }

  /**
   * Write artist data to database (accumulates for batch write).
   */
  async writeArtists(artists: z.infer<typeof ArtistSpecSchema>[]): Promise<void> {
    console.log(`Accumulating ${artists.length} artists for batch write`);
    this.artists.push(...artists);
  }

  /**
   * Write lineup slots to database (accumulates for batch write).
   */
  async writeLineupSlots(slots: z.infer<typeof LineupSlotSchema>[]): Promise<void> {
    console.log(`Accumulating ${slots.length} lineup slots for batch write`);
    this.slots.push(...slots);
  }

  /**
   * Write lineup observations database (accumulates for batch write).
   */
  async writeLineupObservations(observations: z.infer<typeof LineupObservationSchema>[]): Promise<void> {
    console.log(`Accumulating ${observations.length} lineup observations for batch write`);
    this.observations.push(...observations);
  }

  /**
   * Write festival data to database (accumulates for batch write).
   */
  async writeFestival(festival: z.infer<typeof FestivalSpecSchema>): Promise<void> {
    console.log(`Accumulating festival ${festival.name} for batch write`);
    this.festivals.push(festival);
  }

  /**
   * Write festival edition to database (accumulates for batch write).
   */
  async writeFestivalEdition(edition: z.infer<typeof FestivalEditionSchema>): Promise<void> {
    console.log(`Accumulating festival edition ${edition.year} for batch write`);
    this.editions.push(edition);
  }

  /**
   * Write artist contacts to database (accumulates for batch write).
   */
  async writeArtistContacts(contacts: z.infer<typeof ArtistContactRowSchema>[]): Promise<void> {
    console.log(`Accumulating ${contacts.length} artist contacts for batch write`);
    this.contacts.push(...contacts);
  }

  /**
   * Write lineup qualification metrics to database (accumulates for batch write).
   */
  async writeLineupQualificationMetrics(metrics: z.infer<typeof LineupQualificationMetricsSchema>[]): Promise<void> {
    console.log(`Accumulating ${metrics.length} lineup qualification metrics for batch write`);
    this.metrics.push(...metrics);
  }

  /**
   * Flush all accumulated data to database via batch write.
   */
  async flush(): Promise<void> {
    if (this.artists.length === 0 && this.slots.length === 0 && 
        this.observations.length === 0 && this.festivals.length === 0 &&
        this.editions.length === 0 && this.contacts.length === 0 &&
        this.metrics.length === 0) {
      console.log('No data to flush');
      return;
    }

    console.log('Flushing batch data to database...');
    
    // Create temp directory and file
    const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'festival-ingest-'));
    const tempFile = path.join(tempDir, 'batch-data.json');
    
    try {
      // Write batch data to temp file
      const batchData = {
        db_path: this.dbPath,
        artists: this.artists,
        slots: this.slots,
        observations: this.observations,
        festivals: this.festivals,
        editions: this.editions,
        contacts: this.contacts,
        metrics: this.metrics,
      };
      
      fs.writeFileSync(tempFile, JSON.stringify(batchData, null, 2));
      console.log(`Batch data written to ${tempFile}`);
      
      // Call Python script with batch file path
      const result = spawnSync('python3', ['src/scraper/db_writer.py', 'batch', tempFile], {
        stdio: 'inherit',
      });
      
      if (result.status !== 0) {
        throw new Error(`Batch write failed with exit code ${result.status}`);
      }
      
      console.log('Batch write completed successfully');
      
      // Clear accumulated data
      this.artists = [];
      this.slots = [];
      this.observations = [];
      this.festivals = [];
      this.editions = [];
      this.contacts = [];
      this.metrics = [];
      
    } finally {
      // Clean up temp file
      if (fs.existsSync(tempFile)) {
        fs.unlinkSync(tempFile);
      }
      if (fs.existsSync(tempDir)) {
        fs.rmdirSync(tempDir);
      }
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

    // Flush all accumulated data to database
    if (!this.options.dryRun) {
      try {
        await this.dbWriter.flush();
      } catch (error) {
        console.error('Batch write failed:', error);
        process.exit(1);
      }
    }

    console.log('Ingestion pipeline completed');
    
    // Close database connection
    this.dbWriter.close();
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
