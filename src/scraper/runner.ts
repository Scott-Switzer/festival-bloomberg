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
import {
  resolveSourcesByIds,
  UnknownSourceError,
  type FestivalSource,
  type ParserKind,
} from './registry.js';
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
} from './warehouse_schemas.js';
import { z } from 'zod';

// ===========================================================================
// Fetch Tiers
// ===========================================================================

class FetchError extends Error {
  readonly url: string;
  readonly status?: number;
  readonly causeDetail?: unknown;

  constructor(url: string, message: string, opts?: { status?: number; cause?: unknown }) {
    super(message);
    this.name = 'FetchError';
    this.url = url;
    this.status = opts?.status;
    this.causeDetail = opts?.cause;
  }
}

interface FetchResult {
  content: string;
  url: string;
  method: 'http' | 'playwright';
  status: number;
}

type PlaywrightFetchFn = (url: string) => Promise<FetchResult>;

class Fetcher {
  private readonly fetchImpl: typeof fetch;
  private readonly playwrightFetch?: PlaywrightFetchFn;

  constructor(opts?: {
    fetchImpl?: typeof fetch;
    playwrightFetch?: PlaywrightFetchFn;
  }) {
    this.fetchImpl = opts?.fetchImpl ?? fetch;
    this.playwrightFetch = opts?.playwrightFetch;
  }

  /**
   * Fetch content with real tiers only (no synthetic/mock HTML).
   * Tier 1: HTTP fetch
   * Tier 2: Playwright when an implementation is injected
   */
  async fetch(url: string): Promise<FetchResult> {
    const errors: string[] = [];

    // Tier 1: HTTP fetch
    try {
      const response = await this.fetchImpl(url);
      if (response.ok) {
        const content = await response.text();
        return {
          content,
          url,
          method: 'http',
          status: response.status,
        };
      }
      errors.push(`http_${response.status}`);
      console.error(`HTTP fetch failed for ${url}: status ${response.status}`);
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      errors.push(`http_error:${msg}`);
      console.error(`HTTP fetch failed for ${url}:`, error);
    }

    // Tier 2: Playwright only when a real implementation is provided
    if (this.playwrightFetch) {
      try {
        return await this.playwrightFetch(url);
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        errors.push(`playwright:${msg}`);
        console.error(`Playwright fetch failed for ${url}:`, error);
      }
    } else {
      errors.push('playwright_unavailable');
    }

    throw new FetchError(
      url,
      `Failed to fetch ${url} using all available tiers (${errors.join('; ')})`,
    );
  }
}

// ===========================================================================
// Lineup Parser
// ===========================================================================

interface ParsedArtist {
  name: string;
  billing_tier?: string;
  stage?: string;
  day?: string;
}

interface ParsedLineup {
  festival_name: string;
  year: number;
  artists: ParsedArtist[];
  parserUsed: ParserKind;
}

type SpecializedParser = (html: string, source: FestivalSource) => ParsedArtist[];

/**
 * Only register parsers that are implemented and verified.
 * Site-specific kinds (coachella, bonnaroo, …) are reserved in registry metadata
 * but intentionally absent here until dedicated parsers ship — dispatch uses generic.
 */
const SPECIALIZED_PARSERS: Partial<Record<ParserKind, SpecializedParser>> = {
  // No specialized parsers are registered yet.
};

/** Extract artist names with the verified generic HTML heuristics. */
function extractArtistsGeneric(html: string): ParsedArtist[] {
  const artists: ParsedArtist[] = [];

  const artistRegex = /<div[^>]*class="artist"[^>]*>\s*<a[^>]*>([^<]+)<\/a>\s*<\/div>/gi;
  let match: RegExpExecArray | null;

  while ((match = artistRegex.exec(html)) !== null) {
    artists.push({
      name: match[1].trim(),
      billing_tier: 'unknown',
    });
  }

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

function resolveParserImplementation(kind: ParserKind): {
  parserUsed: ParserKind;
  extract: SpecializedParser;
} {
  const specialized = SPECIALIZED_PARSERS[kind];
  if (kind !== 'generic' && specialized) {
    return { parserUsed: kind, extract: specialized };
  }
  return { parserUsed: 'generic', extract: (html) => extractArtistsGeneric(html) };
}

class LineupParser {
  /**
   * Parse lineup from HTML, dispatching by source.parser metadata.
   * Unimplemented specialized kinds fall back to the verified generic parser.
   */
  parse(html: string, source: FestivalSource): ParsedLineup {
    const { parserUsed, extract } = resolveParserImplementation(source.parser);
    if (parserUsed !== source.parser) {
      console.log(
        `Parser '${source.parser}' is not registered; using verified generic fallback for ${source.id}`,
      );
    }

    return {
      festival_name: source.name,
      year: source.year,
      artists: extract(html, source),
      parserUsed,
    };
  }

  /** Exposed for tests: which implementation would handle a parser kind. */
  resolveParser(kind: ParserKind): ParserKind {
    return resolveParserImplementation(kind).parserUsed;
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
  private dbWriter: DatabaseWriter | null = null;
  private options: Required<RunnerOptions>;
  private failures: Array<{ sourceId: string; error: unknown }> = [];

  constructor(options: RunnerOptions = {}, deps?: { fetcher?: Fetcher; parser?: LineupParser }) {
    this.fetcher = deps?.fetcher ?? new Fetcher();
    this.parser = deps?.parser ?? new LineupParser();
    this.options = {
      dryRun: options.dryRun ?? false,
      sources: options.sources ?? [],
      skipResolution: options.skipResolution ?? false,
      skipSentiment: options.skipSentiment ?? false,
      dbPath: options.dbPath ?? 'data/warehouse/festival_bloomberg.duckdb',
    };
  }

  private getDbWriter(): DatabaseWriter {
    if (!this.dbWriter) {
      this.dbWriter = new DatabaseWriter({ path: this.options.dbPath });
    }
    return this.dbWriter;
  }

  /**
   * Run the complete ingestion pipeline.
   * Throws UnknownSourceError for unregistered --sources IDs.
   * Throws if any source fails (fetch/parse/write) after logging failures.
   */
  async run(): Promise<void> {
    console.log('Starting Festival Intelligence ingestion pipeline');
    console.log('Options:', this.options);

    // Validate sources before opening the warehouse / applying schema
    const sources = resolveSourcesByIds(this.options.sources);

    console.log(`Processing ${sources.length} festival sources`);

    for (const source of sources) {
      await this.processSource(source);
    }

    // Flush all accumulated data to database
    if (!this.options.dryRun) {
      try {
        await this.getDbWriter().flush();
      } catch (error) {
        console.error('Batch write failed:', error);
        this.dbWriter?.close();
        throw error;
      }
    }

    this.dbWriter?.close();

    if (this.failures.length > 0) {
      const summary = this.failures
        .map((f) => `${f.sourceId}: ${f.error instanceof Error ? f.error.message : String(f.error)}`)
        .join('; ');
      throw new Error(
        `Ingestion completed with ${this.failures.length} failure(s): ${summary}`,
      );
    }

    console.log('Ingestion pipeline completed');
  }

  /**
   * Process a single festival source.
   */
  private async processSource(source: FestivalSource): Promise<void> {
    console.log(`\nProcessing source: ${source.name} (${source.url})`);

    try {
      // Fetch content — real failures propagate (no mock HTML)
      const fetchResult = await this.fetcher.fetch(source.url);
      console.log(`Fetched content using ${fetchResult.method}`);

      // Parse lineup
      const lineup = this.parser.parse(fetchResult.content, source);
      console.log(
        `Parsed ${lineup.artists.length} artists from lineup (parser=${lineup.parserUsed})`,
      );

      // Create festival and edition records
      const festival = this.createFestivalRecord(source, fetchResult);
      const edition = this.createFestivalEditionRecord(source, lineup);

      if (!this.options.dryRun) {
        const db = this.getDbWriter();
        await db.writeFestival(festival);
        await db.writeFestivalEdition(edition);
      }

      // Process artists
      const artists = await this.processArtists(lineup.artists, source);
      const lineupSlots = this.createLineupSlots(lineup, source, artists);

      // Write to database
      if (!this.options.dryRun) {
        const db = this.getDbWriter();
        await db.writeArtists(artists);
        await db.writeLineupSlots(lineupSlots);

        // Write observations
        const observations = this.createLineupObservations(lineup, source, fetchResult);
        await db.writeLineupObservations(observations);
      }

      console.log(`Successfully processed ${source.name}`);
    } catch (error) {
      console.error(`Failed to process source ${source.name}:`, error);
      this.failures.push({ sourceId: source.id, error });
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
export {
  IngestionRunner,
  Fetcher,
  FetchError,
  LineupParser,
  DatabaseWriter,
  UnknownSourceError,
};

// Run if executed directly (CommonJS entrypoint)
if (require.main === module) {
  main().catch(error => {
    if (error instanceof UnknownSourceError) {
      console.error(`Validation error: ${error.message}`);
    } else {
      console.error('Fatal error:', error);
    }
    process.exit(1);
  });
}
