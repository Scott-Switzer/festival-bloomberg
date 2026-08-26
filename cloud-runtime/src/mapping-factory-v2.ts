/**
 * EVENT_MAPPING_FACTORY_V2 — resolve once, observe forever.
 *
 * Three discovery sources, one identity contract:
 *
 *   SOURCE 1 — PROVIDER-ID PROMOTION
 *     Canonical events already carrying provider_event_id + canonical_url
 *     promote directly to EXACT_PROVIDER_ID. Zero scraper cost. Never
 *     crawl/search for an identity the provider already supplied.
 *
 *   SOURCE 2 — VENUE + PROMOTER CALENDARS
 *     Bounded discovery registry (venue calendars, promoter calendars,
 *     festival pages, official ticketing calendars). /links discovery,
 *     follow ticket links to marketplace URLs.
 *
 *   SOURCE 3 — COMMON CRAWL URL INDEX
 *     Bounded domain/pattern queries (candidate evidence only; never
 *     automatically accepted identity).
 *
 * Identity contract: artist + date + venue + city (artist-only forbidden).
 * Statuses: EXACT_PROVIDER_ID / EXACT_PAGE_MATCH / HIGH_CONFIDENCE accepted;
 * AMBIGUOUS / NOT_FOUND / UNSUPPORTED / RIGHTS_BLOCKED / STALE fail closed.
 *
 * Accepted mappings persist to `canonical/event_identifiers/<event_key>.json`
 * plus a mapping ledger `control/mappings/current.json`, and reconcile into
 * the active watch-universe pointer so the planner picks them up.
 */

import {
  EventIdentity,
  MappingRecord,
  MappingStatus,
  ACCEPTED_MAPPING_STATUSES,
  normalizeName,
  parseEventDate,
  marketplaceFromHost,
  selectBestMapping,
  ParsedCandidate,
  discoverLinks,
  fetchSitemapUrls,
} from "./mapping";
import { BrowserBinding } from "./browser";
import { queryCcUrlIndex, latestCrawlId, CcCaptureCandidate } from "./common-crawl";

/** Minimal env needed by the mapping factory. */
export interface MappingFactoryEnv {
  BACKUP_BUCKET: R2Bucket;
  BROWSER: any;
  SOFTWARE_VERSION: string;
}

/** A canonical event with provider-native identity, as loaded from the universe. */
export interface ProviderIdentityEvent {
  event_key: string;
  artist_name: string;
  event_date: string; // YYYY-MM-DD
  venue_name: string;
  city: string;
  /** Provider-native event ID (e.g. Ticketmaster event id). */
  provider_event_id?: string;
  /** Canonical provider URL (e.g. ticketmaster.com event URL). */
  canonical_url?: string;
  provider?: string;
}

/** Bounded discovery domain — no indiscriminate crawling. */
export interface DiscoveryDomain {
  name: string;
  entity_type: "VENUE" | "PROMOTER" | "FESTIVAL" | "TICKETING" | "ARTIST";
  entity_key: string;
  domain: string;
  start_url: string;
  sitemap_url?: string;
  include_patterns?: string[];
  exclude_patterns?: string[];
  rights_status: string;
  commercial_use_status: string;
  discovery_method: "SITEMAP" | "LINKS" | "CRAWL";
  parser_version: string;
}

/** A resolved/attempted mapping outcome for one event × marketplace. */
export interface MappingOutcome {
  event_key: string;
  marketplace: string;
  status: MappingStatus;
  mapping?: MappingRecord;
  source: "PROVIDER_ID" | "VENUE_CALENDAR" | "PROMOTER_CALENDAR" | "COMMON_CRAWL" | "LINKS";
  source_evidence?: string;
}

export interface MappingFactoryReport {
  status: "MAPPING_FACTORY_COMPLETE";
  events_considered: number;
  provider_id_eligible: number;
  provider_id_accepted: number;
  calendar_candidates: number;
  common_crawl_candidates: number;
  accepted_mappings: MappingRecord[];
  outcomes: MappingOutcome[];
  by_status: Record<string, number>;
  by_source: Record<string, number>;
  by_marketplace: Record<string, number>;
  run_id: string;
}

/** Registry of bounded discovery domains (venue/promoter calendars). */
export const DEFAULT_DISCOVERY_DOMAINS: DiscoveryDomain[] = [
  {
    name: "seatgeek-concerts",
    entity_type: "TICKETING",
    entity_key: "seatgeek",
    domain: "seatgeek.com",
    start_url: "https://seatgeek.com/concerts",
    sitemap_url: "https://seatgeek.com/sitemap.xml",
    include_patterns: ["seatgeek\\.com/(events|concerts)/"],
    exclude_patterns: ["/tickets$", "/checkout", "/login"],
    rights_status: "TERMS_REVIEW_REQUIRED",
    commercial_use_status: "PROTOTYPE_ONLY",
    discovery_method: "SITEMAP",
    parser_version: "v2",
  },
  {
    name: "vivid-concerts",
    entity_type: "TICKETING",
    entity_key: "vivid",
    domain: "vividseats.com",
    start_url: "https://www.vividseats.com/concerts",
    sitemap_url: "https://www.vividseats.com/sitemap.xml",
    include_patterns: ["vividseats\\.com/[^/]+-tickets"],
    exclude_patterns: ["/checkout", "/login", "/cart"],
    rights_status: "TERMS_REVIEW_REQUIRED",
    commercial_use_status: "PROTOTYPE_ONLY",
    discovery_method: "SITEMAP",
    parser_version: "v2",
  },
  {
    name: "tickpick-concerts",
    entity_type: "TICKETING",
    entity_key: "tickpick",
    domain: "tickpick.com",
    start_url: "https://www.tickpick.com/concerts",
    sitemap_url: "https://www.tickpick.com/sitemap.xml",
    include_patterns: ["tickpick\\.com/[^/]+/[^/]+/event/"],
    exclude_patterns: ["/checkout", "/login"],
    rights_status: "TERMS_REVIEW_REQUIRED",
    commercial_use_status: "PROTOTYPE_ONLY",
    discovery_method: "SITEMAP",
    parser_version: "v2",
  },
  {
    name: "gametime-concerts",
    entity_type: "TICKETING",
    entity_key: "gametime",
    domain: "gametime.co",
    start_url: "https://gametime.com/concerts",
    sitemap_url: "https://gametime.com/sitemap.xml",
    include_patterns: ["gametime\\.co(m)?/events/"],
    exclude_patterns: ["/checkout", "/login"],
    rights_status: "TERMS_REVIEW_REQUIRED",
    commercial_use_status: "PROTOTYPE_ONLY",
    discovery_method: "SITEMAP",
    parser_version: "v2",
  },
];

/** Build a canonical EventIdentity from a universe event row. */
export function toEventIdentity(e: ProviderIdentityEvent): EventIdentity | null {
  const date = parseEventDate(e.event_date || "");
  if (!e.event_key || !e.artist_name || !date || !e.venue_name || !e.city) return null;
  return {
    event_key: e.event_key,
    artist_name: e.artist_name,
    event_date: date,
    venue_name: e.venue_name,
    city: e.city,
  };
}

/** Known marketplace hosts (the identity-master marketplace vocabulary). */
const KNOWN_MARKETPLACES = [
  "ticketmaster.com",
  "ticketweb.com",
  "axs.com",
  "seatgeek.com",
  "stubhub.com",
  "vividseats.com",
  "tickpick.com",
  "gametime.com",
  "dice.fm",
  "eventbrite.com",
];

/**
 * Resolve the canonical marketplace for a provider-native event.
 *
 * The provider is Ticketmaster; its canonical_url may be a white-label venue
 * ticketing host (universe.com, venue sites). The event id is still a
 * Ticketmaster event id, so the MARKETPLACE stays ticketmaster.com — the
 * provider's primary marketplace — while the canonical event URL is preserved
 * as the acquisition target. Never invent a marketplace from an unknown host.
 */
export function resolveMarketplaceForProvider(e: ProviderIdentityEvent): string {
  const hostMp = marketplaceFromHost(e.canonical_url || "");
  if (hostMp && hostMp !== "unknown" && KNOWN_MARKETPLACES.includes(hostMp)) {
    return hostMp;
  }
  const provider = (e.provider || "").toLowerCase();
  if (provider.includes("ticketmaster")) return "ticketmaster.com";
  if (provider.includes("ticketweb")) return "ticketweb.com";
  if (provider.includes("axs")) return "axs.com";
  if (provider.includes("seatgeek")) return "seatgeek.com";
  if (provider.includes("stubhub")) return "stubhub.com";
  if (provider.includes("vivid")) return "vividseats.com";
  if (provider.includes("tickpick")) return "tickpick.com";
  if (provider.includes("gametime")) return "gametime.com";
  // The estate is Ticketmaster-native (event::tm:* keys, provider=ticketmaster
  // in the lake parquet). Unknown hosts are Ticketmaster white-label platforms
  // (universe.com, venue sites) — never invent a marketplace from the host.
  return "ticketmaster.com";
}

/**
 * SOURCE 1 — provider-ID promotion.
 * A canonical event with provider_event_id + canonical_url promotes directly
 * to EXACT_PROVIDER_ID. Never rediscover what the provider already supplied.
 */
export function promoteProviderId(e: ProviderIdentityEvent, now = new Date().toISOString()): MappingRecord | null {
  if (!e.provider_event_id || !e.canonical_url) return null;
  const marketplace = resolveMarketplaceForProvider(e);
  return {
    event_key: e.event_key,
    marketplace,
    marketplace_event_id: e.provider_event_id,
    marketplace_event_url: e.canonical_url,
    mapping_status: "EXACT_PROVIDER_ID",
    mapping_method: "provider_id_promotion",
    mapping_version: "v2",
    confidence: 1.0,
    first_resolved_at: now,
    last_verified_at: now,
    source_evidence: `provider_native:${marketplace}:${e.provider_event_id}`,
    rights_status: "TERMS_REVIEW_REQUIRED",
    commercial_use_status: "PROTOTYPE_ONLY",
  };
}

/**
 * SOURCE 2 — venue/promoter calendar discovery.
 * Discover candidates from a bounded discovery domain, then deterministic match.
 */
export async function discoverFromDomain(
  browser: BrowserBinding | null,
  domain: DiscoveryDomain,
  identity: EventIdentity,
  opts: { maxUrls?: number } = {}
): Promise<MappingOutcome | null> {
  const maxUrls = opts.maxUrls || 200;
  const candidates: ParsedCandidate[] = [];

  // Sitemap first (cheapest, no browser)
  if (domain.sitemap_url) {
    const urls = await fetchSitemapUrls(domain.sitemap_url, { maxUrls });
    for (const u of urls.slice(0, maxUrls)) {
      candidates.push(parseDiscoveryCandidate(u, domain));
    }
  }

  // /links discovery from the entry calendar page
  if (browser && candidates.length === 0) {
    const links = await discoverLinks(browser, domain.start_url, {
      includePatterns: domain.include_patterns,
      excludePatterns: domain.exclude_patterns,
      maxLinks: maxUrls,
    });
    for (const u of links.slice(0, maxUrls)) {
      candidates.push(parseDiscoveryCandidate(u, domain));
    }
  }

  if (candidates.length === 0) return null;
  const { record, status } = selectBestMapping(identity, candidates);
  if (!record) {
    return {
      event_key: identity.event_key,
      marketplace: domain.domain,
      status,
      source: domain.entity_type === "PROMOTER" ? "PROMOTER_CALENDAR" : "VENUE_CALENDAR",
      source_evidence: domain.start_url,
    };
  }
  return {
    event_key: identity.event_key,
    marketplace: record.marketplace,
    status: record.mapping_status,
    mapping: record,
    source: domain.entity_type === "PROMOTER" ? "PROMOTER_CALENDAR" : "VENUE_CALENDAR",
    source_evidence: record.source_evidence,
  };
}

/** Parse a discovery URL into a candidate (URL only — title absent for sitemap URLs). */
function parseDiscoveryCandidate(url: string, domain: DiscoveryDomain): ParsedCandidate {
  return {
    url,
    title: url,
    marketplace: domain.domain,
  };
}

/**
 * SOURCE 3 — Common Crawl URL index.
 * Bounded domain/pattern query → candidate captures → deterministic match.
 * Candidate evidence only; never auto-accepted.
 */
export async function discoverFromCommonCrawl(
  identity: EventIdentity,
  opts: { urlPattern?: string; crawlId?: string; maxUrls?: number } = {}
): Promise<MappingOutcome | null> {
  const crawlId = opts.crawlId || (await latestCrawlId());
  const artistNorm = normalizeName(identity.artist_name).replace(/\s+/g, "-").slice(0, 60);
  // Bounded pattern: look for the artist slug across known marketplace domains.
  const urlPattern =
    opts.urlPattern ||
    `https://www.ticketmaster.com/${artistNorm}*`;
  const { captures } = await queryCcUrlIndex({
    urlPattern,
    crawlId,
    limit: opts.maxUrls || 25,
    matchType: "prefix",
  });
  if (captures.length === 0) {
    return {
      event_key: identity.event_key,
      marketplace: "ticketmaster.com",
      status: "NOT_FOUND",
      source: "COMMON_CRAWL",
      source_evidence: urlPattern,
    };
  }

  const candidates: ParsedCandidate[] = captures.map((c: CcCaptureCandidate) => ({
    url: c.url,
    title: c.url,
    marketplace: marketplaceFromHost(c.url) || "unknown",
  }));

  const { record, status } = selectBestMapping(identity, candidates);
  if (!record) {
    return {
      event_key: identity.event_key,
      marketplace: "ticketmaster.com",
      status,
      source: "COMMON_CRAWL",
      source_evidence: urlPattern,
    };
  }
  return {
    event_key: identity.event_key,
    marketplace: record.marketplace,
    status: record.mapping_status,
    mapping: record,
    source: "COMMON_CRAWL",
    source_evidence: record.source_evidence,
  };
}

/** Run the full mapping factory across all sources. */
export async function runMappingFactory(
  env: MappingFactoryEnv,
  opts: {
    max_events?: number;
    /** Start offset into the estate — enables chunked waves under Worker limits. */
    offset?: number;
    dry_run?: boolean;
    include_provider_id?: boolean;
    include_calendars?: boolean;
    include_common_crawl?: boolean;
    calendar_domains?: DiscoveryDomain[];
  } = {}
): Promise<MappingFactoryReport> {
  const {
    max_events = 100,
    offset = 0,
    dry_run = false,
    include_provider_id = true,
    include_calendars = true,
    include_common_crawl = false,
    calendar_domains = DEFAULT_DISCOVERY_DOMAINS,
  } = opts;

  const now = new Date().toISOString();
  const runId = `mf2_${now.slice(0, 13).replace(/[-:T]/g, "")}`;

  // Load canonical universe (provider-native events with IDs).
  const events = await loadUniverseEvents(env);
  const outcomes: MappingOutcome[] = [];
  const accepted: MappingRecord[] = [];
  const byStatus: Record<string, number> = {};
  const bySource: Record<string, number> = {};
  const byMarketplace: Record<string, number> = {};

  const note = (o: MappingOutcome) => {
    outcomes.push(o);
    byStatus[o.status] = (byStatus[o.status] || 0) + 1;
    bySource[o.source] = (bySource[o.source] || 0) + 1;
    const mp = o.mapping?.marketplace || o.marketplace;
    byMarketplace[mp] = (byMarketplace[mp] || 0) + 1;
    if (o.mapping) accepted.push(o.mapping);
  };

  let providerEligible = 0;
  let providerAccepted = 0;
  let calendarCandidates = 0;
  let ccCandidates = 0;

  for (const event of events.slice(offset, offset + max_events)) {
    const identity = toEventIdentity(event);
    if (!identity) continue;

    // ── SOURCE 1: provider-ID promotion ─────────────────────────────
    if (include_provider_id) {
      const promo = promoteProviderId(event, now);
      if (promo) {
        providerEligible++;
        providerAccepted++;
        note({ event_key: event.event_key, marketplace: promo.marketplace, status: promo.mapping_status, mapping: promo, source: "PROVIDER_ID" });
        continue; // provider-native is authoritative — no need to rediscover
      }
    }

    // ── SOURCE 2: venue/promoter calendars (bounded) ────────────────
    if (include_calendars) {
      const browser = env.BROWSER ? (env.BROWSER as BrowserBinding) : null;
      for (const domain of calendar_domains) {
        const outcome = await discoverFromDomain(browser, domain, identity, { maxUrls: 100 });
        if (outcome) {
          calendarCandidates++;
          note(outcome);
          if (outcome.mapping) break; // first accepted mapping wins
        }
      }
    }

    // ── SOURCE 3: Common Crawl (bounded, candidate-only) ────────────
    if (include_common_crawl) {
      const outcome = await discoverFromCommonCrawl(identity, { maxUrls: 20 });
      if (outcome) {
        ccCandidates++;
        note(outcome);
      }
    }
  }

  // Persist accepted mappings (unless dry run).
  if (!dry_run && accepted.length > 0) {
    await persistMappings(env, accepted, runId);
  }

  return {
    status: "MAPPING_FACTORY_COMPLETE",
    events_considered: events.slice(offset, offset + max_events).length,
    provider_id_eligible: providerEligible,
    provider_id_accepted: providerAccepted,
    calendar_candidates: calendarCandidates,
    common_crawl_candidates: ccCandidates,
    accepted_mappings: accepted,
    outcomes,
    by_status: byStatus,
    by_source: bySource,
    by_marketplace: byMarketplace,
    run_id: runId,
  };
}

/**
 * Load canonical universe events (provider-native rows).
 *
 * Priority:
 *   1. control/event_estate/identity_estate_v1.json (the provider-native
 *      identity estate exported from the lake parquet — SOURCE 1 material)
 *   2. control/watch_universe/current.json pointer (frozen watch universe)
 *   3. legacy frozen universe
 */
async function loadUniverseEvents(env: MappingFactoryEnv): Promise<ProviderIdentityEvent[]> {
  // 1. Provider-native identity estate (control artifact, deliberately updated)
  const estate = await env.BACKUP_BUCKET.get("control/event_estate/identity_estate_v1.json");
  if (estate) {
    try {
      const data = (await estate.json()) as { events?: any[] };
      if (data.events && data.events.length > 0) {
        return data.events.map(normalizeUniverseEvent);
      }
    } catch {
      // fall through to watch universe
    }
  }

  // 2. Stable watch-universe control pointer
  const pointer = await env.BACKUP_BUCKET.get("control/watch_universe/current.json");
  if (pointer) {
    try {
      const p = (await pointer.json()) as { source?: string };
      if (p.source) {
        const obj = await env.BACKUP_BUCKET.get(p.source);
        if (obj) {
          const data = (await obj.json()) as { events?: any[] };
          return (data.events || []).map(normalizeUniverseEvent);
        }
      }
    } catch {
      // fall through
    }
  }
  // Legacy frozen universe
  const legacy = await env.BACKUP_BUCKET.get("canonical/2026-08-26T01-00-58Z/watch_universe_v1.json");
  if (legacy) {
    try {
      const data = (await legacy.json()) as { events?: any[] };
      return (data.events || []).map(normalizeUniverseEvent);
    } catch {
      // no events
    }
  }
  return [];
}

/** Map a raw universe row to the ProviderIdentityEvent shape. */
function normalizeUniverseEvent(e: any): ProviderIdentityEvent {
  return {
    event_key: e.event_key || e.id,
    artist_name: e.artist_name,
    event_date: e.event_date || e.date,
    venue_name: e.venue_name,
    city: e.city,
    provider_event_id: e.provider_event_id || e.tm_event_id || e.marketplace_event_id,
    canonical_url: e.canonical_url || e.marketplace_event_url,
    provider: e.provider || (e.canonical_url ? marketplaceFromHost(e.canonical_url) : undefined),
  };
}

/**
 * Persist accepted mappings + mapping ledger (canonical security master).
 *
 * The per-event files are written by the ESTATE EXPORT (bulk, offline via
 * rclone — see scripts/export_identity_estate.py) for large waves. This
 * endpoint persists a bounded in-flight wave and maintains the ledger merge
 * in memory (single read + single write, not O(n) reads/writes per event).
 */
async function persistMappings(env: MappingFactoryEnv, mappings: MappingRecord[], runId: string): Promise<void> {
  const now = new Date().toISOString();

  // Per-event immutable records — write-only fast path. The factory never
  // overwrites a conflicting identity claim from the SAME wave; the ledger
  // merge below resolves higher-confidence wins deterministically.
  for (const m of mappings) {
    const key = `canonical/event_identifiers/${m.event_key}.json`;
    const body = JSON.stringify({ event_key: m.event_key, mappings: [m], updated_at: now, run_id: runId }, null, 2);
    await env.BACKUP_BUCKET.put(key, body, {
      httpMetadata: { contentType: "application/json" },
    });
  }

  // Mapping ledger — read once, merge in memory, write once.
  const ledgerKey = "control/mappings/current.json";
  const existingLedger = await env.BACKUP_BUCKET.get(ledgerKey);
  const merged = new Map<string, MappingRecord>();
  if (existingLedger) {
    try {
      const data = (await existingLedger.json()) as { mappings?: MappingRecord[] };
      for (const m of data.mappings || []) {
        merged.set(`${m.event_key}|${m.marketplace}`, m);
      }
    } catch {
      // fresh ledger
    }
  }
  for (const m of mappings) {
    const key = `${m.event_key}|${m.marketplace}`;
    const cur = merged.get(key);
    if (!cur || m.confidence > cur.confidence) merged.set(key, m);
  }
  await env.BACKUP_BUCKET.put(
    ledgerKey,
    JSON.stringify(
      { mappings: [...merged.values()], count: merged.size, updated_at: now, run_id: runId },
      null,
      2
    ),
    { httpMetadata: { contentType: "application/json" } }
  );
}
