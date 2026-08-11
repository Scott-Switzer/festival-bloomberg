/**
 * MusicBrainz API client with rate limiting and artist resolution.
 * MusicBrainz is the primary source for artist identity resolution.
 */

import { z } from 'zod';

// ===========================================================================
// MusicBrainz API Types
// ===========================================================================

const MusicBrainzArtistSchema = z.object({
  id: z.string().uuid(),
  name: z.string(),
  'sort-name': z.string().optional(),
  disambiguation: z.string().optional(),
  type: z.string().optional(),
  country: z.string().optional(),
  'life-span': z.object({
    begin: z.string().optional(),
    end: z.string().optional(),
  }).optional(),
  aliases: z.array(z.object({
    name: z.string(),
    'sort-name': z.string().optional(),
    locale: z.string().optional(),
    type: z.string().optional(),
    primary: z.boolean().optional(),
  })).optional(),
  tags: z.array(z.object({
    count: z.number(),
    name: z.string(),
  })).optional(),
  'area': z.object({
    name: z.string(),
    'iso-3166-1-codes': z.array(z.string()).optional(),
  }).optional(),
  'begin-area': z.object({
    name: z.string(),
  }).optional(),
});

const MusicBrainzSearchResponseSchema = z.object({
  artists: z.array(MusicBrainzArtistSchema),
  count: z.number(),
});

export type MusicBrainzArtist = z.infer<typeof MusicBrainzArtistSchema>;
export type MusicBrainzSearchResponse = z.infer<typeof MusicBrainzSearchResponseSchema>;

// ===========================================================================
// MusicBrainz Client
// ===========================================================================

export class MusicBrainzClient {
  private baseUrl: string = 'https://musicbrainz.org/ws/2/';
  private userAgent: string;
  private rateLimitDelay: number;
  private lastRequestTime: number = 0;

  constructor(userAgent: string, rateLimitDelay: number = 1000) {
    this.userAgent = userAgent;
    this.rateLimitDelay = rateLimitDelay;
  }

  private async rateLimit(): Promise<void> {
    const now = Date.now();
    const timeSinceLastRequest = now - this.lastRequestTime;
    if (timeSinceLastRequest < this.rateLimitDelay) {
      const delay = this.rateLimitDelay - timeSinceLastRequest;
      await new Promise(resolve => setTimeout(resolve, delay));
    }
    this.lastRequestTime = Date.now();
  }

  private async fetch<T>(url: string, params: Record<string, string> = {}): Promise<T> {
    await this.rateLimit();

    const queryString = new URLSearchParams(params).toString();
    const fullUrl = `${url}${queryString ? `?${queryString}` : ''}`;

    const response = await fetch(fullUrl, {
      headers: {
        'User-Agent': this.userAgent,
        'Accept': 'application/json',
      },
    });

    if (!response.ok) {
      throw new Error(`MusicBrainz API error: ${response.status} ${response.statusText}`);
    }

    return response.json() as Promise<T>;
  }

  /**
   * Fetch artist data by MusicBrainz ID.
   */
  async getArtist(artistId: string): Promise<MusicBrainzArtist | null> {
    try {
      const url = `${this.baseUrl}artist/${artistId}`;
      const params = {
        fmt: 'json',
        inc: 'aliases+releases+tags+area+artist-rels',
      };
      const data = await this.fetch(url, params);
      return MusicBrainzArtistSchema.parse(data as unknown);
    } catch (error) {
      console.error(`Error fetching artist ${artistId}:`, error);
      return null;
    }
  }

  /**
   * Search for artists by name.
   */
  async searchArtist(name: string, limit: number = 10): Promise<MusicBrainzArtist[]> {
    try {
      const url = `${this.baseUrl}artist/`;
      const params = {
        query: `artist:"${name}"`,
        fmt: 'json',
        limit: limit.toString(),
      };
      const data = await this.fetch(url, params) as MusicBrainzSearchResponse;
      const parsed = MusicBrainzSearchResponseSchema.parse(data);
      return parsed.artists;
    } catch (error) {
      console.error(`Error searching for artist ${name}:`, error);
      return [];
    }
  }

  /**
   * Fetch releases for an artist.
   */
  async getArtistReleases(artistId: string, releaseType?: string): Promise<any[]> {
    try {
      const url = `${this.baseUrl}release`;
      const params: Record<string, string> = {
        query: `arid:${artistId}`,
        fmt: 'json',
        limit: '100',
      };
      if (releaseType) {
        params.type = releaseType;
      }
      const data = await this.fetch(url, params) as { releases: any[] };
      return data.releases || [];
    } catch (error) {
      console.error(`Error fetching releases for artist ${artistId}:`, error);
      return [];
    }
  }

  /**
   * Fetch genre tags for an artist.
   */
  async getArtistTags(artistId: string): Promise<Array<{ count: number; name: string }>> {
    try {
      const url = `${this.baseUrl}artist/${artistId}/tags`;
      const params = { fmt: 'json' };
      const data = await this.fetch(url, params) as { tags: Array<{ count: number; name: string }> };
      return data.tags || [];
    } catch (error) {
      console.error(`Error fetching tags for artist ${artistId}:`, error);
      return [];
    }
  }
}

// ===========================================================================
// Artist Resolution Utilities
// ===========================================================================

/**
 * Normalize artist name for comparison.
 */
export function normalizeArtistName(name: string): string {
  const normalized = name.toLowerCase().trim();
  // Remove special characters except apostrophes and hyphens
  const cleaned = normalized.replace(/[^a-z0-9'\- ]/g, '');
  // Remove extra spaces
  return cleaned.replace(/\s+/g, ' ');
}

/**
 * Extract relevant artist data from MusicBrainz response.
 */
export function extractArtistData(mbData: MusicBrainzArtist) {
  return {
    musicbrainz_id: mbData.id,
    normalized_name: normalizeArtistName(mbData.name),
    name: mbData.name,
    sort_name: mbData['sort-name'],
    disambiguation: mbData.disambiguation,
    aliases: mbData.aliases?.map(alias => ({
      alias: alias.name,
      normalized_alias: normalizeArtistName(alias.name),
      alias_type: alias.type,
      locale: alias.locale,
      is_primary: alias.primary,
    })) || [],
    country: mbData.country,
    type: mbData.type,
    origin_city: mbData['begin-area']?.name,
    area: mbData.area?.name,
    life_span_begin: mbData['life-span']?.begin,
    life_span_end: mbData['life-span']?.end,
    tags: mbData.tags?.map(tag => tag.name) || [],
  };
}

/**
 * Resolve artist name to MusicBrainz ID with confidence scoring.
 */
export async function resolveArtistToMBID(
  name: string,
  client: MusicBrainzClient,
  options: { limit?: number; minScore?: number } = {}
): Promise<{ mbid: string | null; confidence: number; artist: MusicBrainzArtist | null }> {
  const { limit = 5, minScore = 0.8 } = options;
  
  const results = await client.searchArtist(name, limit);
  if (results.length === 0) {
    return { mbid: null, confidence: 0, artist: null };
  }

  const normalizedQuery = normalizeArtistName(name);
  
  // Score each result
  const scored = results.map(artist => {
    const normalizedName = normalizeArtistName(artist.name);
    const exactMatch = normalizedName === normalizedQuery;
    const aliasMatch = artist.aliases?.some(
      alias => normalizeArtistName(alias.name) === normalizedQuery
    );
    
    let score = 0;
    if (exactMatch) score = 1.0;
    else if (aliasMatch) score = 0.9;
    else {
      // Fuzzy match using simple similarity
      const similarity = calculateSimilarity(normalizedQuery, normalizedName);
      score = similarity;
    }

    return { artist, score };
  });

  // Sort by score descending
  scored.sort((a, b) => b.score - a.score);

  const best = scored[0];
  if (best.score >= minScore) {
    return { mbid: best.artist.id, confidence: best.score, artist: best.artist };
  }

  return { mbid: null, confidence: best.score, artist: best.artist };
}

/**
 * Calculate simple string similarity (Levenshtein-based).
 */
function calculateSimilarity(s1: string, s2: string): number {
  if (s1 === s2) return 1.0;
  if (s1.length === 0 || s2.length === 0) return 0.0;

  const matrix: number[][] = [];
  for (let i = 0; i <= s2.length; i++) {
    matrix[i] = [i];
  }
  for (let j = 0; j <= s1.length; j++) {
    matrix[0][j] = j;
  }

  for (let i = 1; i <= s2.length; i++) {
    for (let j = 1; j <= s1.length; j++) {
      if (s2.charAt(i - 1) === s1.charAt(j - 1)) {
        matrix[i][j] = matrix[i - 1][j - 1];
      } else {
        matrix[i][j] = Math.min(
          matrix[i - 1][j - 1] + 1,
          matrix[i][j - 1] + 1,
          matrix[i - 1][j] + 1
        );
      }
    }
  }

  const distance = matrix[s2.length][s1.length];
  const maxLength = Math.max(s1.length, s2.length);
  return 1 - distance / maxLength;
}

// ===========================================================================
// Default Client Instance
// ===========================================================================

let defaultClient: MusicBrainzClient | null = null;

export function getMusicBrainzClient(userAgent?: string): MusicBrainzClient {
  if (!defaultClient) {
    const ua = userAgent || 'FestivalIntelligence/1.0 (https://github.com/Scott-Switzer/festival-intelligence)';
    defaultClient = new MusicBrainzClient(ua);
  }
  return defaultClient;
}
