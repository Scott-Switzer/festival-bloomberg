/** MusicBrainz client and artist enrichment with the required one-request-per-second limit. */
import { z } from 'zod';

const Alias = z.object({ name: z.string(), 'sort-name': z.string().optional(), locale: z.string().optional(), type: z.string().optional(), primary: z.boolean().optional() });
const Artist = z.object({ id: z.string().uuid(), name: z.string(), 'sort-name': z.string().optional(), disambiguation: z.string().optional(), type: z.string().optional(), country: z.string().optional(), aliases: z.array(Alias).optional(), tags: z.array(z.object({ count: z.number(), name: z.string() })).optional(), area: z.object({ name: z.string(), 'iso-3166-1-codes': z.array(z.string()).optional() }).optional(), 'begin-area': z.object({ name: z.string() }).optional(), 'life-span': z.object({ begin: z.string().optional(), end: z.string().optional() }).optional() });
const Search = z.object({ artists: z.array(Artist), count: z.number() });
export type MusicBrainzArtist = z.infer<typeof Artist>;
export type MusicBrainzSearchResponse = z.infer<typeof Search>;

export class MusicBrainzClient {
  private lastRequestTime = 0;
  constructor(private readonly userAgent: string, private readonly rateLimitDelay = 1000, private readonly baseUrl = 'https://musicbrainz.org/ws/2/') {
    if (!/\S+@\S+|https?:\/\//.test(userAgent)) throw new Error('MusicBrainz User-Agent must include a maintainer email or URL');
  }
  private async request<T>(path: string, params: Record<string, string>): Promise<T> {
    const wait = this.rateLimitDelay - (Date.now() - this.lastRequestTime); if (wait > 0) await new Promise(r => setTimeout(r, wait));
    this.lastRequestTime = Date.now();
    const response = await fetch(`${this.baseUrl}${path}?${new URLSearchParams(params)}`, { headers: { Accept: 'application/json', 'User-Agent': this.userAgent } });
    if (!response.ok) throw new Error(`MusicBrainz API error: ${response.status} ${response.statusText}`);
    return response.json() as Promise<T>;
  }
  async getArtist(id: string): Promise<MusicBrainzArtist | null> { try { return Artist.parse(await this.request(`artist/${id}`, { fmt: 'json', inc: 'aliases+tags+area+artist-rels' })); } catch { return null; } }
  async searchArtist(name: string, limit = 10): Promise<MusicBrainzArtist[]> { try { return Search.parse(await this.request('artist/', { query: `artist:"${name}"`, fmt: 'json', limit: String(limit) })).artists; } catch { return []; } }
  async getArtistTags(id: string): Promise<Array<{ count: number; name: string }>> { try { return ((await this.request<{ tags?: Array<{ count: number; name: string }> }>(`artist/${id}/tags`, { fmt: 'json' })).tags ?? []); } catch { return []; } }
}
export function normalizeArtistName(name: string): string { return name.toLowerCase().trim().replace(/[^a-z0-9'\- ]/g, '').replace(/\s+/g, ' '); }
export function extractArtistData(a: MusicBrainzArtist) { return { musicbrainz_id: a.id, normalized_name: normalizeArtistName(a.name), name: a.name, sort_name: a['sort-name'], disambiguation: a.disambiguation, aliases: (a.aliases ?? []).map(x => ({ alias: x.name, normalized_alias: normalizeArtistName(x.name), alias_type: x.type, locale: x.locale, is_primary: x.primary })), country: a.country, type: a.type, origin_city: a['begin-area']?.name, area: a.area?.name, life_span_begin: a['life-span']?.begin, life_span_end: a['life-span']?.end, tags: (a.tags ?? []).map(x => x.name) }; }
function similarity(a: string, b: string): number { const d = Array.from({ length: b.length + 1 }, (_, i) => [i, ...Array(a.length).fill(0)]); for (let j = 0; j <= a.length; j++) d[0][j] = j; for (let i = 1; i <= b.length; i++) for (let j = 1; j <= a.length; j++) d[i][j] = b[i - 1] === a[j - 1] ? d[i - 1][j - 1] : Math.min(d[i - 1][j - 1] + 1, d[i][j - 1] + 1, d[i - 1][j] + 1); return 1 - d[b.length][a.length] / Math.max(a.length, b.length); }
export async function resolveArtistToMBID(name: string, client: MusicBrainzClient, options: { limit?: number; minScore?: number } = {}) { const results = await client.searchArtist(name, options.limit ?? 5); const q = normalizeArtistName(name); const ranked = results.map(artist => { const alias = (artist.aliases ?? []).some(x => normalizeArtistName(x.name) === q); const value = normalizeArtistName(artist.name) === q ? 1 : alias ? .9 : similarity(q, normalizeArtistName(artist.name)); return { artist, score: value }; }).sort((a, b) => b.score - a.score); const best = ranked[0]; return best && best.score >= (options.minScore ?? .8) ? { mbid: best.artist.id, confidence: best.score, artist: best.artist } : { mbid: null, confidence: best?.score ?? 0, artist: best?.artist ?? null }; }
let defaultClient: MusicBrainzClient | null = null;
export function getMusicBrainzClient(userAgent = 'FestivalBloomberg/1.0 (maintainer: scott.switzer@example.com)'): MusicBrainzClient { return defaultClient ??= new MusicBrainzClient(userAgent); }
