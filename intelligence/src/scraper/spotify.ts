/**
 * Spotify API client using Client Credentials flow.
 * Fetches popularity, follower counts, and genres for artists.
 */

import { z } from 'zod';

// ===========================================================================
// Spotify API Types
// ===========================================================================

const SpotifyArtistSchema = z.object({
  id: z.string(),
  name: z.string(),
  popularity: z.number().min(0).max(100),
  followers: z.object({
    total: z.number(),
  }),
  genres: z.array(z.string()),
  external_urls: z.object({
    spotify: z.string().url(),
  }),
  images: z.array(z.object({
    url: z.string().url(),
    height: z.number().optional(),
    width: z.number().optional(),
  })).optional(),
  type: z.literal('artist'),
  uri: z.string(),
});

const SpotifyTokenResponseSchema = z.object({
  access_token: z.string(),
  token_type: z.string(),
  expires_in: z.number(),
});

const SpotifySearchResponseSchema = z.object({
  artists: z.object({
    items: z.array(SpotifyArtistSchema),
    total: z.number(),
  }),
});

export type SpotifyArtist = z.infer<typeof SpotifyArtistSchema>;
export type SpotifySearchResponse = z.infer<typeof SpotifySearchResponseSchema>;

// ===========================================================================
// Spotify Client
// ===========================================================================

export class SpotifyClient {
  private clientId: string;
  private clientSecret: string;
  private accessToken: string | null = null;
  private tokenExpiry: number = 0;
  private baseUrl: string = 'https://api.spotify.com/v1/';

  constructor(clientId: string, clientSecret: string) {
    this.clientId = clientId;
    this.clientSecret = clientSecret;
  }

  /**
   * Get access token using Client Credentials flow.
   */
  private async authenticate(): Promise<void> {
    const now = Date.now();
    
    // Reuse existing token if still valid
    if (this.accessToken && now < this.tokenExpiry) {
      return;
    }

    const credentials = btoa(`${this.clientId}:${this.clientSecret}`);
    
    const response = await fetch('https://accounts.spotify.com/api/token', {
      method: 'POST',
      headers: {
        'Authorization': `Basic ${credentials}`,
        'Content-Type': 'application/x-www-form-urlencoded',
      },
      body: 'grant_type=client_credentials',
    });

    if (!response.ok) {
      throw new Error(`Spotify authentication failed: ${response.status} ${response.statusText}`);
    }

    const data = await response.json();
    const parsed = SpotifyTokenResponseSchema.parse(data);
    
    this.accessToken = parsed.access_token;
    this.tokenExpiry = now + (parsed.expires_in * 1000) - 60000; // Refresh 1 minute before expiry
  }

  /**
   * Make authenticated request to Spotify API.
   */
  private async fetch<T>(endpoint: string, params: Record<string, string> = {}): Promise<T> {
    await this.authenticate();

    const queryString = new URLSearchParams(params).toString();
    const url = `${this.baseUrl}${endpoint}${queryString ? `?${queryString}` : ''}`;

    const response = await fetch(url, {
      headers: {
        'Authorization': `Bearer ${this.accessToken}`,
        'Content-Type': 'application/json',
      },
    });

    if (!response.ok) {
      throw new Error(`Spotify API error: ${response.status} ${response.statusText}`);
    }

    return response.json() as Promise<T>;
  }

  /**
   * Get artist by Spotify ID.
   */
  async getArtist(spotifyId: string): Promise<SpotifyArtist | null> {
    try {
      const data = await this.fetch(`artists/${spotifyId}`) as SpotifyArtist;
      return SpotifyArtistSchema.parse(data);
    } catch (error) {
      console.error(`Error fetching Spotify artist ${spotifyId}:`, error);
      return null;
    }
  }

  /**
   * Search for artists by name.
   */
  async searchArtist(name: string, limit: number = 10): Promise<SpotifyArtist[]> {
    try {
      const params = {
        q: name,
        type: 'artist',
        limit: limit.toString(),
      };
      const data = await this.fetch('search', params) as SpotifySearchResponse;
      const parsed = SpotifySearchResponseSchema.parse(data);
      return parsed.artists.items;
    } catch (error) {
      console.error(`Error searching Spotify for artist ${name}:`, error);
      return [];
    }
  }

  /**
   * Get multiple artists by IDs.
   */
  async getSeveralArtists(ids: string[]): Promise<SpotifyArtist[]> {
    if (ids.length === 0) return [];
    if (ids.length > 50) {
      // Spotify API limits to 50 IDs per request
      const chunks = [];
      for (let i = 0; i < ids.length; i += 50) {
        chunks.push(ids.slice(i, i + 50));
      }
      const results = await Promise.all(chunks.map(chunk => this.getSeveralArtists(chunk)));
      return results.flat();
    }

    try {
      const params = {
        ids: ids.join(','),
      };
      const data = await this.fetch('artists', params) as { artists: (SpotifyArtist | null)[] };
      return data.artists.filter((a): a is SpotifyArtist => a !== null);
    } catch (error) {
      console.error('Error fetching several Spotify artists:', error);
      return [];
    }
  }

  /**
   * Get artist's top tracks.
   */
  async getArtistTopTracks(spotifyId: string, market: string = 'US'): Promise<any[]> {
    try {
      const params = {
        market,
      };
      const data = await this.fetch(`artists/${spotifyId}/top-tracks`, params) as { tracks: any[] };
      return data.tracks || [];
    } catch (error) {
      console.error(`Error fetching top tracks for artist ${spotifyId}:`, error);
      return [];
    }
  }

  /**
   * Get artist's related artists.
   */
  async getArtistRelatedArtists(spotifyId: string): Promise<SpotifyArtist[]> {
    try {
      const data = await this.fetch(`artists/${spotifyId}/related-artists`) as { artists: SpotifyArtist[] };
      return data.artists || [];
    } catch (error) {
      console.error(`Error fetching related artists for ${spotifyId}:`, error);
      return [];
    }
  }
}

// ===========================================================================
// Artist Resolution Utilities
// ===========================================================================

/**
 * Extract relevant artist data from Spotify response.
 */
export function extractSpotifyArtistData(spotifyData: SpotifyArtist) {
  return {
    spotify_id: spotifyData.id,
    name: spotifyData.name,
    popularity: spotifyData.popularity,
    followers: spotifyData.followers.total,
    genres: spotifyData.genres,
    spotify_url: spotifyData.external_urls.spotify,
    images: spotifyData.images?.map(img => ({
      url: img.url,
      height: img.height,
      width: img.width,
    })) || [],
  };
}

/**
 * Resolve artist name to Spotify ID with confidence scoring.
 */
export async function resolveArtistToSpotifyId(
  name: string,
  client: SpotifyClient,
  options: { limit?: number; minPopularity?: number } = {}
): Promise<{ spotifyId: string | null; confidence: number; artist: SpotifyArtist | null }> {
  const { limit = 5, minPopularity = 30 } = options;
  
  const results = await client.searchArtist(name, limit);
  if (results.length === 0) {
    return { spotifyId: null, confidence: 0, artist: null };
  }

  const normalizedQuery = name.toLowerCase().trim();
  
  // Score each result
  const scored = results.map(artist => {
    const normalizedName = artist.name.toLowerCase().trim();
    const exactMatch = normalizedName === normalizedQuery;
    
    let score = 0;
    if (exactMatch) {
      score = 0.5; // Base score for exact name match
    } else {
      // Simple similarity
      const similarity = calculateSimilarity(normalizedQuery, normalizedName);
      score = similarity * 0.4; // Lower base score for fuzzy match
    }

    // Boost by popularity (normalized to 0-0.5)
    const popularityBoost = (artist.popularity / 100) * 0.5;
    score += popularityBoost;

    return { artist, score };
  });

  // Sort by score descending
  scored.sort((a, b) => b.score - a.score);

  const best = scored[0];
  if (best.score >= 0.6 && best.artist.popularity >= minPopularity) {
    return { spotifyId: best.artist.id, confidence: best.score, artist: best.artist };
  }

  return { spotifyId: null, confidence: best.score, artist: best.artist };
}

/**
 * Calculate simple string similarity.
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

let defaultClient: SpotifyClient | null = null;

export function getSpotifyClient(clientId?: string, clientSecret?: string): SpotifyClient {
  if (!defaultClient) {
    const id = clientId || process.env.SPOTIFY_CLIENT_ID;
    const secret = clientSecret || process.env.SPOTIFY_CLIENT_SECRET;
    
    if (!id || !secret) {
      throw new Error('Spotify client ID and secret must be provided or set in environment variables');
    }
    
    defaultClient = new SpotifyClient(id, secret);
  }
  return defaultClient;
}
