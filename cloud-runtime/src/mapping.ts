/**
 * Mapping Factory — Cloudflare /links + sitemap discovery + deterministic match.
 *
 * Canonical flow (resolve once, observe forever):
 *   canonical event (artist + date + venue + city)
 *     → bounded site discovery (/links render:false, sitemaps)
 *     → candidate event URLs
 *     → parse event identity
 *     → deterministic match (artist + date + venue + city)
 *     → persist accepted mapping (event_identifiers contract)
 *
 * Artist-only matching is FORBIDDEN.
 * AMBIGUOUS fails closed.
 * Only EXACT_PROVIDER_ID / EXACT_PAGE_MATCH / HIGH_CONFIDENCE may enter
 * automated acquisition.
 *
 * Accepted mappings are persisted as `canonical/event_identifiers/<event_key>.json`
 * and reconciled into the active watch-universe pointer.
 */

import { BrowserBinding } from "./browser";

/** Mapping statuses — mirror the canonical Python url_resolver contract. */
export type MappingStatus =
  | "EXACT_PROVIDER_ID"
  | "EXACT_PAGE_MATCH"
  | "HIGH_CONFIDENCE"
  | "AMBIGUOUS"
  | "NOT_FOUND"
  | "UNSUPPORTED"
  | "RIGHTS_BLOCKED"
  | "STALE";

export const ACCEPTED_MAPPING_STATUSES: MappingStatus[] = [
  "EXACT_PROVIDER_ID",
  "EXACT_PAGE_MATCH",
  "HIGH_CONFIDENCE",
];

/** Canonical event identity evidence used for matching. */
export interface EventIdentity {
  event_key: string;
  artist_name: string;
  event_date: string; // YYYY-MM-DD
  venue_name: string;
  city: string;
}

/** A parsed discovery candidate (URL + identity signals). */
export interface ParsedCandidate {
  url: string;
  title: string;
  marketplace: string;
  artist?: string;
  event_date?: string; // YYYY-MM-DD
  venue?: string;
  city?: string;
}

/** Evidence weights — which identity fields actually matched. */
export interface MatchEvidence {
  artist: boolean;
  date: boolean;
  venue: boolean;
  city: boolean;
}

/** The canonical mapping record (mirrors acquisition.event_identifiers columns). */
export interface MappingRecord {
  event_key: string;
  marketplace: string;
  marketplace_event_id?: string;
  marketplace_event_url: string;
  mapping_status: MappingStatus;
  mapping_method: string;
  /** Explicit mapping pipeline version (v1 = links discovery, v2 = factory). */
  mapping_version?: string;
  confidence: number;
  first_resolved_at: string;
  last_verified_at: string;
  source_evidence: string;
  rights_status: string;
  commercial_use_status: string;
}

/** Site discovery configuration — bounded, explicit, no indiscriminate crawling. */
export interface DiscoveryTarget {
  name: string;
  marketplace: string;
  /** Entry URL (calendar / directory / sitemap). */
  start_url: string;
  /** Optional sitemap URL to harvest candidate event links from. */
  sitemap_url?: string;
  include_patterns?: string[];
  exclude_patterns?: string[];
  max_links?: number;
}

/** Browser /links quick-action result element shape (subset). */
interface LinksOutcome {
  url?: string;
  href?: string;
  text?: string;
}

/**
 * Normalize a name for deterministic comparison:
 * lowercase, strip punctuation, collapse whitespace, drop stop-words.
 */
export function normalizeName(s: string): string {
  return String(s || "")
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/&amp;/g, "&")
    .replace(/[^a-z0-9&]+/g, " ")
    .replace(/\b(the|a|an|and|live|presents|presented by)\b/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/** Parse a date string into YYYY-MM-DD or null. Supports ISO, US, and textual. */
export function parseEventDate(s: string): string | null {
  if (!s) return null;
  const iso = String(s).trim().slice(0, 10);
  if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) return iso;
  let m = String(s).match(/(\d{4})[-/](\d{1,2})[-/](\d{1,2})/);
  if (m) return `${m[1]}-${m[2].padStart(2, "0")}-${m[3].padStart(2, "0")}`;
  m = String(s).match(/(\d{1,2})[/-](\d{1,2})[/-](\d{4})/);
  if (m) return `${m[3]}-${m[1].padStart(2, "0")}-${m[2].padStart(2, "0")}`;
  const months: Record<string, string> = { jan: "01", feb: "02", mar: "03", apr: "04", may: "05", jun: "06", jul: "07", aug: "08", sep: "09", oct: "10", nov: "11", dec: "12" };
  // Day-first textual: "6 Nov 2026" / "6th Nov, 2026"
  m = String(s).match(/\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*,?\s+(\d{4})\b/i);
  if (m) return `${m[3]}-${months[m[2].toLowerCase().slice(0, 3)]}-${m[1].padStart(2, "0")}`;
  // Month-first textual: "Nov 6, 2026" (optionally with weekday)
  m = String(s).match(/\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\s*,?\s+(\d{4})\b/i);
  if (m) return `${m[3]}-${months[m[1].toLowerCase().slice(0, 3)]}-${m[2].padStart(2, "0")}`;
  return null;
}

/** Extract the marketplace host from a URL. */
export function marketplaceFromHost(url: string): string {
  const host = (() => {
    try { return new URL(url).hostname; } catch { return ""; }
  })();
  if (/ticketmaster\.com$/i.test(host)) return "ticketmaster.com";
  if (/ticketweb\.com$/i.test(host)) return "ticketweb.com";
  if (/axs\.com$/i.test(host)) return "axs.com";
  if (/seatgeek\.com$/i.test(host)) return "seatgeek.com";
  if (/stubhub\.com$/i.test(host)) return "stubhub.com";
  if (/vividseats\.com$/i.test(host)) return "vividseats.com";
  if (/tickpick\.com$/i.test(host)) return "tickpick.com";
  if (/gametime\.co/i.test(host)) return "gametime.com";
  if (/dice\.fm$/i.test(host)) return "dice.fm";
  if (/eventbrite\.com$/i.test(host)) return "eventbrite.com";
  return host || "unknown";
}

/** Does the candidate's text mention the artist (normalized substring)? */
function mentions(needle: string, haystack: string): boolean {
  const n = normalizeName(needle);
  const h = normalizeName(haystack);
  if (!n || !h) return false;
  return h.includes(n) || n.includes(h);
}

/**
 * Deterministic identity match.
 *
 * Rules (artist-only matching is FORBIDDEN):
 *   EXACT_PAGE_MATCH: artist + date + venue + city
 *   HIGH_CONFIDENCE:  artist + date + (venue OR city)
 *   AMBIGUOUS:        artist + date but venue/city conflict or incomplete
 *   NOT_FOUND:        no artist+date signal at all
 */
export function matchCandidate(
  identity: EventIdentity,
  candidate: ParsedCandidate
): { status: MappingStatus; evidence: MatchEvidence; confidence: number } {
  const evidence: MatchEvidence = {
    artist: mentions(identity.artist_name, candidate.artist || candidate.title),
    date: !!candidate.event_date && candidate.event_date === identity.event_date,
    venue: !!candidate.venue && mentions(identity.venue_name, candidate.venue),
    city: !!candidate.city && mentions(identity.city, candidate.city),
  };

  if (evidence.artist && evidence.date && evidence.venue && evidence.city) {
    return { status: "EXACT_PAGE_MATCH", evidence, confidence: 1.0 };
  }
  if (evidence.artist && evidence.date && (evidence.venue || evidence.city)) {
    return { status: "HIGH_CONFIDENCE", evidence, confidence: 0.85 };
  }
  if (evidence.artist && evidence.date) {
    return { status: "AMBIGUOUS", evidence, confidence: 0.5 };
  }
  return { status: "NOT_FOUND", evidence, confidence: 0 };
}

/**
 * Fetch sitemap URLs (direct HTTP, render:false equivalent — no browser).
 * Handles sitemap index + urlset. Bounded.
 */
export async function fetchSitemapUrls(
  sitemapUrl: string,
  opts: { maxUrls?: number; timeoutMs?: number } = {}
): Promise<string[]> {
  const { maxUrls = 2000, timeoutMs = 20000 } = opts;
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    const resp = await fetch(sitemapUrl, {
      headers: { "User-Agent": "Mozilla/5.0 (compatible; FestivalIntelligenceBot/1.0)" },
      signal: controller.signal,
    });
    clearTimeout(timer);
    if (!resp.ok) return [];
    const xml = await resp.text();
    const urls: string[] = [];
    // urlset loc entries
    const locRe = /<loc>([^<]+)<\/loc>/gi;
    let m;
    while ((m = locRe.exec(xml)) !== null && urls.length < maxUrls) {
      urls.push(m[1].trim());
    }
    // sitemap index → recurse once into child sitemaps (bounded)
    if (urls.length === 0 && /<sitemapindex/i.test(xml)) {
      const children = xml.match(/<loc>([^<]+)<\/loc>/gi) || [];
      for (let i = 0; i < Math.min(children.length, 20) && urls.length < maxUrls; i++) {
        const child = children[i].replace(/<\/?loc>/gi, "").trim();
        const sub = await fetchSitemapUrls(child, { maxUrls: maxUrls - urls.length, timeoutMs });
        urls.push(...sub);
      }
    }
    return urls;
  } catch {
    return [];
  }
}

/**
 * Discover page links via Browser Run /links quick action.
 * render:false is preferred (static) — the quick action default. Returns URLs.
 */
export async function discoverLinks(
  browser: BrowserBinding,
  url: string,
  opts: { includePatterns?: string[]; excludePatterns?: string[]; maxLinks?: number } = {}
): Promise<string[]> {
  try {
    const body: Record<string, unknown> = { url };
    if (opts.includePatterns?.length) body.includePatterns = opts.includePatterns;
    if (opts.excludePatterns?.length) body.excludePatterns = opts.excludePatterns;
    if (opts.maxLinks) body.maxLinks = opts.maxLinks;
    const outcome = (await browser.quickAction("links", body)) as unknown;
    let list: LinksOutcome[] = [];
    if (outcome instanceof Response) {
      const data = (await outcome.json()) as any;
      list = data?.result || data?.links || [];
    } else if (outcome && typeof outcome === "object") {
      const o = outcome as any;
      list = o.result || o.links || [];
    }
    const links: string[] = [];
    for (const item of list) {
      const u = item?.url || item?.href;
      if (u && /^https?:\/\//i.test(u)) links.push(u);
    }
    return links;
  } catch {
    return [];
  }
}

/**
 * Parse a candidate URL + title into identity signals.
 * Tries: URL path tokens (provider event IDs), title structure.
 * The title is the primary identity carrier for discovery candidates.
 */
export function parseCandidate(url: string, title: string, artistHint?: string, dateHint?: string): ParsedCandidate {
  const cand: ParsedCandidate = {
    url,
    title: title || "",
    marketplace: marketplaceFromHost(url),
  };

  // Date from URL (common: /event/2026-11-06/...)
  const dateFromUrl = parseEventDate(url);
  // Date from title (e.g. "Artist - Sat, Nov 7, 2026 at Venue, City")
  const dateFromTitle = parseEventDate(title);

  cand.event_date = dateFromTitle || dateFromUrl || dateHint;

  // Venue + city from title tail patterns: "at Venue, City" or "— Venue, City"
  const atMatch = title.match(/\bat\s+([^,|—–-]+?)(?:,\s*([A-Za-z .]+?))?\s*$/i);
  if (atMatch) {
    if (atMatch[1]) cand.venue = atMatch[1].trim();
    if (atMatch[2]) cand.city = atMatch[2].trim();
  } else {
    const dashMatch = title.match(/[—–-]\s*([^,|]+?)(?:,\s*([A-Za-z .]+?))?\s*$/i);
    if (dashMatch) {
      if (dashMatch[1]) cand.venue = dashMatch[1].trim();
      if (dashMatch[2]) cand.city = dashMatch[2].trim();
    }
  }

  // Artist: cut the title at the first date token and trim trailing separators.
  const dateIdx = title.search(/\b(?:sat|sun|mon|tue|wed|thu|fri)\w*\.?,\s+[a-z]{3}\w*\.?\s+\d{1,2},?\s+\d{4}\b/i);
  let head = title;
  if (dateIdx >= 0) {
    head = title.slice(0, dateIdx).replace(/[\s|—–-]+$/g, "").trim();
  }
  cand.artist = head || artistHint;

  return cand;
}

/**
 * Discover candidates for a discovery target and return parsed candidates
 * (deduplicated by URL, bounded).
 */
export async function discoverCandidates(
  browser: BrowserBinding | null,
  target: DiscoveryTarget,
  opts: { maxUrls?: number } = {}
): Promise<ParsedCandidate[]> {
  const maxUrls = opts.maxUrls || 400;
  const seen = new Set<string>();
  const candidates: ParsedCandidate[] = [];

  // 1. Sitemap harvest (cheapest — direct HTTP, no browser)
  if (target.sitemap_url) {
    const sitemapUrls = await fetchSitemapUrls(target.sitemap_url, { maxUrls });
    for (const u of sitemapUrls) {
      if (seen.has(u)) continue;
      seen.add(u);
      candidates.push(parseCandidate(u, u));
      if (candidates.length >= maxUrls) return candidates;
    }
  }

  // 2. Browser /links discovery from the entry page (if browser available)
  if (browser) {
    const links = await discoverLinks(browser, target.start_url, {
      includePatterns: target.include_patterns,
      excludePatterns: target.exclude_patterns,
      maxLinks: maxUrls,
    });
    for (const u of links) {
      if (seen.has(u)) continue;
      seen.add(u);
      candidates.push(parseCandidate(u, u));
      if (candidates.length >= maxUrls) return candidates;
    }
  }

  return candidates;
}

/** Pick the best accepted mapping among candidates, or report the failure status. */
export function selectBestMapping(
  identity: EventIdentity,
  candidates: ParsedCandidate[]
): { record: MappingRecord | null; status: MappingStatus; best: ParsedCandidate | null } {
  let best: ParsedCandidate | null = null;
  let bestScore = -1;
  let bestStatus: MappingStatus = "NOT_FOUND";

  for (const cand of candidates) {
    const { status, confidence } = matchCandidate(identity, cand);
    if (status === "EXACT_PAGE_MATCH" || status === "HIGH_CONFIDENCE") {
      if (confidence > bestScore) {
        bestScore = confidence;
        best = cand;
        bestStatus = status;
      }
    } else if (status === "AMBIGUOUS" && bestStatus === "NOT_FOUND") {
      bestStatus = "AMBIGUOUS";
    }
  }

  if (best) {
    const now = new Date().toISOString();
    const record: MappingRecord = {
      event_key: identity.event_key,
      marketplace: best.marketplace,
      marketplace_event_url: best.url,
      mapping_status: bestStatus,
      mapping_method: "cloud_links_discovery",
      confidence: bestScore,
      first_resolved_at: now,
      last_verified_at: now,
      source_evidence: best.url,
      rights_status: "TERMS_REVIEW_REQUIRED",
      commercial_use_status: "PROTOTYPE_ONLY",
    };
    return { record, status: bestStatus, best };
  }

  return { record: null, status: bestStatus, best: null };
}
