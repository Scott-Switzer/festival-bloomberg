/**
 * Marketplace Fingerprint Library — where economically meaningful state lives.
 *
 * For each marketplace we record the KNOWN structured representations, in the
 * order to try, and whether a legitimate structured endpoint exists that can be
 * promoted to direct HTTP (Rail 0). The goal is NOT a clever DOM scraper: it is
 * finding the cheapest stable structured representation for each site.
 *
 * Playwright/Browser Run is used as a DISCOVERY instrument (one-time) to learn
 * the page's underlying fetch/GraphQL/embedded-state calls; once a legitimate
 * structured endpoint is confirmed, future collection uses that cheaper rail.
 *
 * This library is declarative + deterministic. It never invents evidence.
 */

export type RepresentationKind =
  | "JSON_LD"
  | "NEXT_DATA"
  | "EMBEDDED_STATE"
  | "STRUCTURED_HTTP" // known XHR/fetch/GraphQL endpoint returning JSON
  | "CSS_SELECTORS"
  | "UNKNOWN";

export interface MarketplaceFingerprint {
  marketplace: string;
  /** Canonical host patterns (regex fragments) for identifying the site. */
  host_patterns: string[];
  /** Structured representations observed, in preferred try-order. */
  representations: RepresentationKind[];
  /**
   * A legitimate structured endpoint that returns the event payload directly,
   * if one has been discovered and validated. Requires an event id/url token
   * to substitute — use {TOKEN} placeholders.
   */
  structured_endpoint?: {
    url_template: string;
    method: "GET" | "POST";
    token_source: "EVENT_ID" | "URL_PATH";
    notes: string;
  };
  /** Known embedded-state script markers (e.g. window.__INITIAL_STATE__). */
  state_markers?: string[];
  /** Identity evidence typically present (artist/date/venue/city). */
  identity_evidence: string[];
  /** Rights/commercial notes — never assume scraped == commercial use. */
  rights_notes: string;
  /** Discovery status: IMPLEMENTED / DISCOVERED / PENDING_INVESTIGATION. */
  discovery_status: "IMPLEMENTED" | "DISCOVERED" | "PENDING_INVESTIGATION";
}

/** URL token patterns per marketplace, used to build structured endpoints. */
export const MARKETPLACE_URL_PATTERNS: Record<string, string> = {
  "ticketmaster.com": "/(?:event)/([A-F0-9]{8,})",
  "ticketweb.com": "/event/[^/]+/(\\d+)",
  "axs.com": "/events/([^/]+)/tickets",
  "seatgeek.com": "/(?:events|.+/events)/([^/]+)/tickets",
  "stubhub.com": "/[^/]+-[^/]+-(?:event|performer)/[^/]+/(\\d+)",
  "vividseats.com": "/[^/]+-[^/]+-[^/]+-tickets--prod-([\\d-]+)",
  "tickpick.com": "/[^/]+/[^/]+/event/(\\d+)",
  "gametime.com": "/events/([^/]+)",
};

/**
 * Fingerprint library. This is the authoritative registry the router and the
 * mapping factory consult when deciding which representation to attempt.
 */
export const MARKETPLACE_FINGERPRINTS: MarketplaceFingerprint[] = [
  {
    marketplace: "ticketmaster.com",
    host_patterns: ["ticketmaster\\.com"],
    representations: ["JSON_LD", "NEXT_DATA", "EMBEDDED_STATE", "STRUCTURED_HTTP"],
    state_markers: ["window.__INITIAL_STATE__", "window.__PRELOADED_STATE__"],
    identity_evidence: ["artist", "date", "venue", "city"],
    rights_notes: "Official primary ticketing; generic JSON-LD offer != resale evidence.",
    discovery_status: "IMPLEMENTED",
  },
  {
    marketplace: "ticketweb.com",
    host_patterns: ["ticketweb\\.com"],
    representations: ["JSON_LD", "CSS_SELECTORS"],
    identity_evidence: ["artist", "date", "venue", "city"],
    rights_notes: "Primary ticketing (Live Nation independent arm).",
    discovery_status: "IMPLEMENTED",
  },
  {
    marketplace: "axs.com",
    host_patterns: ["axs\\.com"],
    representations: ["JSON_LD", "NEXT_DATA", "EMBEDDED_STATE"],
    identity_evidence: ["artist", "date", "venue", "city"],
    rights_notes: "Primary ticketing; venue-scale inventory.",
    discovery_status: "PENDING_INVESTIGATION",
  },
  {
    marketplace: "seatgeek.com",
    host_patterns: ["seatgeek\\.com"],
    representations: ["NEXT_DATA", "EMBEDDED_STATE", "STRUCTURED_HTTP"],
    state_markers: ["__NEXT_DATA__", "window.__INITIAL_STATE__"],
    identity_evidence: ["artist", "date", "venue", "city"],
    rights_notes: "Marketplace (primary+resale mixed). JSON-LD offer may be resale.",
    discovery_status: "PENDING_INVESTIGATION",
  },
  {
    marketplace: "stubhub.com",
    host_patterns: ["stubhub\\.com"],
    representations: ["NEXT_DATA", "EMBEDDED_STATE", "STRUCTURED_HTTP"],
    state_markers: ["__NEXT_DATA__"],
    identity_evidence: ["artist", "date", "venue", "city"],
    rights_notes: "Resale marketplace. Listing price != face value.",
    discovery_status: "PENDING_INVESTIGATION",
  },
  {
    marketplace: "vividseats.com",
    host_patterns: ["vividseats\\.com"],
    representations: ["EMBEDDED_STATE", "STRUCTURED_HTTP"],
    state_markers: ["window.__PRELOADED_STATE__", "__NEXT_DATA__"],
    identity_evidence: ["artist", "date", "venue", "city"],
    rights_notes: "Resale marketplace. Listing price != face value.",
    discovery_status: "PENDING_INVESTIGATION",
  },
  {
    marketplace: "tickpick.com",
    host_patterns: ["tickpick\\.com"],
    representations: ["EMBEDDED_STATE", "CSS_SELECTORS"],
    state_markers: ["window.__INITIAL_STATE__"],
    identity_evidence: ["artist", "date", "venue", "city"],
    rights_notes: "Resale marketplace (all-in pricing).",
    discovery_status: "PENDING_INVESTIGATION",
  },
  {
    marketplace: "gametime.com",
    host_patterns: ["gametime\\.co", "gametime\\.com"],
    representations: ["NEXT_DATA", "EMBEDDED_STATE", "STRUCTURED_HTTP"],
    state_markers: ["__NEXT_DATA__"],
    identity_evidence: ["artist", "date", "venue", "city"],
    rights_notes: "Resale marketplace (mobile-first, all-in pricing).",
    discovery_status: "PENDING_INVESTIGATION",
  },
  {
    marketplace: "dice.fm",
    host_patterns: ["dice\\.fm"],
    representations: ["EMBEDDED_STATE", "STRUCTURED_HTTP"],
    state_markers: ["window.__PRELOADED_STATE__"],
    identity_evidence: ["artist", "date", "venue", "city"],
    rights_notes: "Primary ticketing (independent venues).",
    discovery_status: "PENDING_INVESTIGATION",
  },
  {
    marketplace: "eventbrite.com",
    host_patterns: ["eventbrite\\.com"],
    representations: ["JSON_LD", "EMBEDDED_STATE"],
    state_markers: ["window.__INITIAL_STATE__"],
    identity_evidence: ["artist", "date", "venue", "city"],
    rights_notes: "Primary ticketing for smaller/independent events.",
    discovery_status: "PENDING_INVESTIGATION",
  },
];

/** Look up the fingerprint for a marketplace host string. */
export function fingerprintFor(marketplace: string): MarketplaceFingerprint | null {
  const m = (marketplace || "").toLowerCase();
  const direct = MARKETPLACE_FINGERPRINTS.find((f) => f.marketplace === m);
  if (direct) return direct;
  const byHost = MARKETPLACE_FINGERPRINTS.find((f) =>
    f.host_patterns.some((p) => new RegExp(p, "i").test(m))
  );
  return byHost || null;
}

/** Extract the provider event id/token from a marketplace URL using the registry. */
export function extractProviderToken(url: string, marketplace: string): string | null {
  const patterns: string[] = [];
  const direct = MARKETPLACE_URL_PATTERNS[marketplace];
  if (direct) patterns.push(direct);
  const fp = fingerprintFor(marketplace);
  if (fp) {
    for (const p of fp.host_patterns) {
      patterns.push(p);
    }
  }
  for (const p of patterns) {
    try {
      const m = url.match(new RegExp(p, "i"));
      if (m && m[1]) return m[1];
    } catch {
      // skip malformed patterns
    }
  }
  return null;
}

/**
 * Choose the cheapest representation to attempt for a marketplace, given
 * what has been discovered so far. Direct structured HTTP (Rail 0) is always
 * preferred over browser rendering.
 */
export function preferredRepresentations(marketplace: string): RepresentationKind[] {
  const fp = fingerprintFor(marketplace);
  if (!fp) return ["UNKNOWN"];
  return fp.representations;
}
