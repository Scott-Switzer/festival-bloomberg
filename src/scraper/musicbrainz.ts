/**
 * MusicBrainz API client with concurrent-safe rate limiting and artist resolution.
 * MusicBrainz requires ~1 request/sec; reservation times are serialized.
 */

import { z } from 'zod';

const Alias = z.object({
  name: z.string(),
  'sort-name': z.string().optional(),
  locale: z.string().optional(),
  type: z.string().optional(),
  primary: z.boolean().optional(),
});

const Artist = z.object({
  id: z.string().uuid(),
  name: z.string(),
  'sort-name': z.string().optional(),
  disambiguation: z.string().optional(),
  type: z.string().optional(),
  country: z.string().optional(),
  aliases: z.array(Alias).optional(),
  tags: z.array(z.object({ count: z.number(), name: z.string() })).optional(),
  area: z
    .object({
      name: z.string(),
      'iso-3166-1-codes': z.array(z.string()).optional(),
    })
    .optional(),
  'begin-area': z.object({ name: z.string() }).optional(),
  'life-span': z
    .object({ begin: z.string().optional(), end: z.string().optional() })
    .optional(),
});

const Search = z.object({
  artists: z.array(Artist),
  count: z.number(),
});

export type MusicBrainzArtist = z.infer<typeof Artist>;
export type MusicBrainzSearchResponse = z.infer<typeof Search>;

export type MusicBrainzRelease = {
  id?: string;
  title?: string;
  date?: string;
  status?: string;
  country?: string;
  [key: string]: unknown;
};

/** Default UA: descriptive app name + maintainer contact (MusicBrainz policy). */
export const DEFAULT_MUSICBRAINZ_USER_AGENT =
  'FestivalBloomberg/1.0 (scott.t.switzer@gmail.com)';

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export class MusicBrainzClient {
  private readonly userAgent: string;
  private readonly rateLimitDelay: number;
  private readonly baseUrl: string;
  /** Earliest time a new request may start; reserved under the queue lock. */
  private nextAvailableAt = 0;
  /**
   * Promise chain that serializes slot reservation. A plain lastRequestTime
   * check is not concurrent-safe: overlapping callers can both pass the wait
   * before either updates the timestamp.
   */
  private reservationQueue: Promise<void> = Promise.resolve();

  constructor(
    userAgent: string,
    rateLimitDelay = 1000,
    baseUrl = 'https://musicbrainz.org/ws/2/',
  ) {
    if (!/\S+@\S+|https?:\/\/|contact=\S+/i.test(userAgent)) {
      throw new Error(
        'MusicBrainz User-Agent must include a maintainer email, URL, or contact=',
      );
    }
    if (/scott\.switzer@example\.com/i.test(userAgent)) {
      throw new Error(
        'MusicBrainz User-Agent must not use scott.switzer@example.com',
      );
    }
    this.userAgent = userAgent;
    this.rateLimitDelay = rateLimitDelay;
    this.baseUrl = baseUrl.endsWith('/') ? baseUrl : `${baseUrl}/`;
  }

  /**
   * Serialize request-start reservations so consecutive starts are at least
   * `rateLimitDelay` apart, even under concurrent callers.
   */
  private async reserveRequestSlot(): Promise<void> {
    const run = this.reservationQueue.then(async () => {
      const now = Date.now();
      const wait = Math.max(0, this.nextAvailableAt - now);
      if (wait > 0) {
        await sleep(wait);
      }
      // Reserve the next start time before releasing the queue lock.
      this.nextAvailableAt = Date.now() + this.rateLimitDelay;
    });
    this.reservationQueue = run.then(
      () => undefined,
      () => undefined,
    );
    await run;
  }

  private async request<T>(
    path: string,
    params: Record<string, string>,
  ): Promise<T> {
    await this.reserveRequestSlot();
    const response = await fetch(
      `${this.baseUrl}${path}?${new URLSearchParams(params)}`,
      {
        headers: {
          Accept: 'application/json',
          'User-Agent': this.userAgent,
        },
      },
    );
    if (!response.ok) {
      throw new Error(
        `MusicBrainz API error: ${response.status} ${response.statusText}`,
      );
    }
    return response.json() as Promise<T>;
  }

  async getArtist(id: string): Promise<MusicBrainzArtist | null> {
    try {
      return Artist.parse(
        await this.request(`artist/${id}`, {
          fmt: 'json',
          inc: 'aliases+releases+tags+area+artist-rels',
        }),
      );
    } catch (error) {
      console.error(`Error fetching artist ${id}:`, error);
      return null;
    }
  }

  async searchArtist(name: string, limit = 10): Promise<MusicBrainzArtist[]> {
    try {
      return Search.parse(
        await this.request('artist/', {
          query: `artist:"${name}"`,
          fmt: 'json',
          limit: String(limit),
        }),
      ).artists;
    } catch (error) {
      console.error(`Error searching for artist ${name}:`, error);
      return [];
    }
  }

  /** Fetch releases for an artist by MusicBrainz artist ID. */
  async getArtistReleases(
    artistId: string,
    releaseType?: string,
  ): Promise<MusicBrainzRelease[]> {
    try {
      const params: Record<string, string> = {
        query: `arid:${artistId}`,
        fmt: 'json',
        limit: '100',
      };
      if (releaseType) {
        params.type = releaseType;
      }
      const data = await this.request<{ releases?: MusicBrainzRelease[] }>(
        'release',
        params,
      );
      return data.releases ?? [];
    } catch (error) {
      console.error(`Error fetching releases for artist ${artistId}:`, error);
      return [];
    }
  }

  async getArtistTags(
    id: string,
  ): Promise<Array<{ count: number; name: string }>> {
    try {
      return (
        (
          await this.request<{ tags?: Array<{ count: number; name: string }> }>(
            `artist/${id}/tags`,
            { fmt: 'json' },
          )
        ).tags ?? []
      );
    } catch (error) {
      console.error(`Error fetching tags for artist ${id}:`, error);
      return [];
    }
  }
}

export function normalizeArtistName(name: string): string {
  return name
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9'\- ]/g, '')
    .replace(/\s+/g, ' ');
}

export function extractArtistData(a: MusicBrainzArtist) {
  return {
    musicbrainz_id: a.id,
    normalized_name: normalizeArtistName(a.name),
    name: a.name,
    sort_name: a['sort-name'],
    disambiguation: a.disambiguation,
    aliases: (a.aliases ?? []).map((x) => ({
      alias: x.name,
      normalized_alias: normalizeArtistName(x.name),
      alias_type: x.type,
      locale: x.locale,
      is_primary: x.primary,
    })),
    country: a.country,
    type: a.type,
    origin_city: a['begin-area']?.name,
    area: a.area?.name,
    life_span_begin: a['life-span']?.begin,
    life_span_end: a['life-span']?.end,
    tags: (a.tags ?? []).map((x) => x.name),
  };
}

function similarity(a: string, b: string): number {
  const d = Array.from({ length: b.length + 1 }, (_, i) => [
    i,
    ...Array(a.length).fill(0),
  ]);
  for (let j = 0; j <= a.length; j++) d[0][j] = j;
  for (let i = 1; i <= b.length; i++) {
    for (let j = 1; j <= a.length; j++) {
      d[i][j] =
        b[i - 1] === a[j - 1]
          ? d[i - 1][j - 1]
          : Math.min(d[i - 1][j - 1] + 1, d[i][j - 1] + 1, d[i - 1][j] + 1);
    }
  }
  return 1 - d[b.length][a.length] / Math.max(a.length, b.length);
}

export async function resolveArtistToMBID(
  name: string,
  client: MusicBrainzClient,
  options: { limit?: number; minScore?: number } = {},
) {
  const results = await client.searchArtist(name, options.limit ?? 5);
  const q = normalizeArtistName(name);
  const ranked = results
    .map((artist) => {
      const alias = (artist.aliases ?? []).some(
        (x) => normalizeArtistName(x.name) === q,
      );
      const value =
        normalizeArtistName(artist.name) === q
          ? 1
          : alias
            ? 0.9
            : similarity(q, normalizeArtistName(artist.name));
      return { artist, score: value };
    })
    .sort((left, right) => right.score - left.score);
  const best = ranked[0];
  return best && best.score >= (options.minScore ?? 0.8)
    ? { mbid: best.artist.id, confidence: best.score, artist: best.artist }
    : {
        mbid: null,
        confidence: best?.score ?? 0,
        artist: best?.artist ?? null,
      };
}

let defaultClient: MusicBrainzClient | null = null;

export function getMusicBrainzClient(
  userAgent = DEFAULT_MUSICBRAINZ_USER_AGENT,
): MusicBrainzClient {
  return (defaultClient ??= new MusicBrainzClient(userAgent));
}
