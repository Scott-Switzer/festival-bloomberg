/**
 * Acquisition Router — picks the cheapest acceptable rail for a marketplace URL
 * and normalizes all rails to a single AcquisitionResult.
 *
 * Rail hierarchy (cheapest first):
 *   0. RAIL_0_DIRECT_HTTP     Worker fetch() (HTML/JSON/JSON-LD)
 *   1. RAIL_1_BROWSER_CONTENT Browser Run /content
 *   2. RAIL_2_BROWSER_SCRAPE  Browser Run /scrape (CSS selectors)
 *   4. RAIL_4_MONID           Monid context.dev fallback
 *
 * The router tries the cheapest rail first. It only escalates when a cheaper
 * rail fails, is unsupported/blocked, or fails extraction-quality requirements.
 */

import {
  fetchDirect,
  fetchBrowserContent,
  scrapeBrowserElements,
  BrowserBinding,
  FetchOutcome,
  RouterRail,
  httpErrorCategory,
} from "./browser";

/** Canonical normalized result for ALL rails — mirrors existing semantics. */
export interface AcquisitionResult {
  event_key: string;
  marketplace: string;
  source_url: string;
  final_url: string;

  acquisition_provider: string;
  acquisition_rail: string;

  observed_at: string;
  retrieved_at: string;
  knowledge_time: string;

  http_status: number;
  latency_ms: number;
  browser_ms: number;

  raw_content_type: string;
  raw_bytes: number;
  raw_sha256: string;
  /** Full canonical evidence bytes (the exact bytes the sha256 was computed over). */
  raw_body: string;
  raw_object_key: string;

  identity_status: string;

  // Uncertain economic fields — neutral, not auto-resale
  observed_offer_min_price: number | null;
  currency: string | null;
  price_basis: string;
  inventory_basis: string;
  availability_state: string;

  rights_status: string;
  commercial_use_status: string;

  parser_version: string;
  software_version: string;

  error_category?: string;
  error_detail?: string;
}

export type RouterMode = "cheapest" | "monid";
export type BrowserKind = "quick-actions" | "none";

/** What the router needs from env */
export interface RouterDeps {
  browser: BrowserBinding | null;
  monidApiKey: string | null;
  monidFetchPage: (apiKey: string, url: string) => Promise<{
    status: string;
    html: string;
    provider?: string;
    cost_usd: number;
    latency_ms: number;
    http_status?: number;
  }>;
}

/** Structured fields extracted deterministically (JSON-LD → __NEXT_DATA__ → selectors). */
export interface Extracted {
  observed_offer_min_price: number | null;
  currency: string | null;
  price_basis: string;
  inventory_basis: string;
  availability_state: string;
  identity_status: string;
}

/**
 * Deterministically parse a page for offer price + identity signals.
 * Priority: JSON-LD → __NEXT_DATA__ → raw selectors. No AI inference for
 * canonical economic evidence.
 */
export function extractStructured(html: string, marketplace: string): Extracted {
  const out: Extracted = {
    observed_offer_min_price: null,
    currency: null,
    price_basis: "NONE",
    inventory_basis: "UNKNOWN",
    availability_state: "UNKNOWN",
    identity_status: "UNKNOWN",
  };
  if (!html) return out;

  // 1. JSON-LD
  const ldRegex = /<script[^>]*type="application\/ld\+json"[^>]*>([\s\S]*?)<\/script>/gi;
  let m;
  while ((m = ldRegex.exec(html)) !== null) {
    try {
      const ld = JSON.parse(m[1]);
      if (typeof ld === "object" && ld && ["Event", "MusicEvent", "Concert"].includes(ld["@type"])) {
        const offers = ld.offers;
        if (offers && !Array.isArray(offers)) {
          const p = parseFloat(offers.price);
          if (!isNaN(p)) {
            out.observed_offer_min_price = p;
            out.price_basis = "PUBLIC_PAGE_JSON_LD_OFFER";
            out.currency = offers.priceCurrency || null;
          }
          const avail = String(offers.availability || "");
          if (avail) out.availability_state = avail;
        } else if (Array.isArray(offers)) {
          const prices = offers.map((o: any) => parseFloat(o?.price)).filter((p: number) => !isNaN(p));
          if (prices.length) {
            out.observed_offer_min_price = Math.min(...prices);
            out.price_basis = "PUBLIC_PAGE_JSON_LD_OFFER";
          }
        }
        if (ld.name) out.identity_status = "PRESENT";
        break;
      }
    } catch {
      // ignore malformed script block
    }
  }

  // 2. __NEXT_DATA__
  if (!out.observed_offer_min_price && !out.identity_status) {
    const nd = html.match(/<script[^>]*id="__NEXT_DATA__"[^>]*>([\s\S]*?)<\/script>/);
    if (nd) {
      try {
        const data = JSON.parse(nd[1]);
        const props = data?.props?.pageProps;
        if (props) {
          const p = props.event?.price ?? props.price;
          const pn = parseFloat(p);
          if (!isNaN(pn)) {
            out.observed_offer_min_price = pn;
            out.price_basis = "PUBLIC_PAGE_NEXT_DATA";
            out.currency = props.event?.currency || null;
          }
          if (props.event?.name || props.title) out.identity_status = "PRESENT";
        }
      } catch {
        // ignore
      }
    }
  }

  out.inventory_basis = "UNKNOWN";
  return out;
}

/** Normalize a FetchOutcome (any rail) into a partially-filled AcquisitionResult. */
export function baseResult(
  event_key: string,
  marketplace: string,
  source_url: string,
  outcome: FetchOutcome,
  software_version: string,
  now: string
): AcquisitionResult {
  return {
    event_key,
    marketplace,
    source_url,
    final_url: outcome.final_url || source_url,
    acquisition_provider: outcome.provider,
    acquisition_rail: outcome.rail,
    observed_at: now,
    retrieved_at: now,
    knowledge_time: now,
    http_status: outcome.http_status,
    latency_ms: outcome.latency_ms,
    browser_ms: outcome.browser_ms,
    raw_content_type: outcome.http_status === 200 ? "text/html" : "",
    raw_bytes: outcome.raw_bytes,
    raw_sha256: outcome.raw_sha256,
    raw_body: outcome.raw || "",
    raw_object_key: "",
    identity_status: outcome.ok ? "UNKNOWN" : "FAILED",
    observed_offer_min_price: null,
    currency: null,
    price_basis: "NONE",
    inventory_basis: "UNKNOWN",
    availability_state: "UNKNOWN",
    rights_status: "TERMS_REVIEW_REQUIRED",
    commercial_use_status: "PROTOTYPE_ONLY",
    parser_version: "router_v1",
    software_version,
    error_category: outcome.error_category,
    error_detail: outcome.error_detail,
  };
}

/**
 * Decide which rail to attempt and execute the cheapest acceptable one.
 *
 * Returns an AcquisitionResult with the chosen rail/provider and extraction.
 */
export async function acquireUrl(
  deps: RouterDeps,
  event_key: string,
  marketplace: string,
  source_url: string,
  software_version: string,
  opts: { mode?: RouterMode; force_rail?: string } = {}
): Promise<AcquisitionResult> {
  const now = new Date().toISOString();
  const mode = opts.mode || "cheapest";
  const force = opts.force_rail;

  // ---- RAIL_0: direct HTTP ----
  if ((!force || force === "RAIL_0_DIRECT_HTTP") && mode === "cheapest") {
    const direct = await fetchDirect(source_url);
    if (direct.ok && direct.raw) {
      const res = baseResult(event_key, marketplace, source_url, direct, software_version, now);
      applyExtraction(res, extractStructured(direct.raw, marketplace));
      // Escalate on extraction-quality failure (no identity + no economics).
      if (hasUsableSignal(res)) return res;
    }
  }

  // ---- RAIL_1: Browser /content ----
  if ((!force || force === "RAIL_1_BROWSER_CONTENT") && mode === "cheapest" && deps.browser) {
    const content = await fetchBrowserContent(deps.browser, source_url, { waitUntil: "networkidle2" });
    if (content.ok && content.raw) {
      const res = baseResult(event_key, marketplace, source_url, content, software_version, now);
      applyExtraction(res, extractStructured(content.raw, marketplace));
      if (hasUsableSignal(res)) return res;
    }
  }

  // ---- RAIL_2: Browser /scrape ----
  if ((!force || force === "RAIL_2_BROWSER_SCRAPE") && mode === "cheapest" && deps.browser) {
    const selectors = genericOfferSelectors();
    const scrape = await scrapeBrowserElements(deps.browser, source_url, selectors, { waitUntil: "networkidle2" });
    if (scrape.ok) {
      const text = scrapeToString(scrape.results);
      const res = baseResult(event_key, marketplace, source_url, {
        ok: true,
        http_status: 200,
        final_url: source_url,
        raw: text,
        raw_bytes: text.length,
        raw_sha256: scrape.raw_sha256,
        latency_ms: scrape.latency_ms,
        browser_ms: scrape.browser_ms,
        provider: "browser_run",
        rail: "RAIL_2_BROWSER_SCRAPE",
      }, software_version, now);
      res.raw_content_type = "application/x-scrape-json";
      const extracted = extractFromScrape(scrape.results);
      applyExtraction(res, extracted);
      return res;
    }
  }

  // ---- RAIL_4: Monid fallback ----
  if (!force || force === "RAIL_4_MONID") {
    if (deps.monidApiKey && deps.monidFetchPage) {
      const page = await deps.monidFetchPage(deps.monidApiKey, source_url);
      if (page.status === "FETCHED" && page.html) {
        const outcome: FetchOutcome = {
          ok: true,
          http_status: page.http_status || 200,
          final_url: source_url,
          raw: page.html,
          raw_bytes: page.html.length,
          raw_sha256: page.html.length ? await sha256Hex(page.html) : "",
          latency_ms: page.latency_ms,
          browser_ms: 0,
          provider: page.provider === "context.dev" ? "monid" : "monid",
          rail: "RAIL_4_MONID",
        };
        const res = baseResult(event_key, marketplace, source_url, outcome, software_version, now);
        applyExtraction(res, extractStructured(page.html, marketplace));
        return res;
      }
    }
  }

  // All rails failed/unsupported — return a failed result.
  const failedOutcome: FetchOutcome = {
    ok: false,
    http_status: 0,
    final_url: source_url,
    raw: "",
    raw_bytes: 0,
    raw_sha256: "",
    latency_ms: 0,
    browser_ms: 0,
    provider: "none",
    rail: "RAIL_UNSUPPORTED",
    error_category: "BLOCKED",
    error_detail: "all cost rails failed",
  };
  return baseResult(event_key, marketplace, source_url, failedOutcome, software_version, now);
}

function applyExtraction(res: AcquisitionResult, ex: Extracted): void {
  if (ex.observed_offer_min_price != null) res.observed_offer_min_price = ex.observed_offer_min_price;
  if (ex.currency) res.currency = ex.currency;
  if (ex.price_basis !== "NONE") res.price_basis = ex.price_basis;
  if (ex.availability_state) res.availability_state = ex.availability_state;
  if (ex.identity_status) res.identity_status = ex.identity_status;
}

/**
 * Extraction-quality gate: a rail's result is acceptable when it carries
 * identity evidence OR an economically relevant field. Cheap rails that only
 * return an empty shell escalate to the next rail (extraction-quality failure).
 */
function hasUsableSignal(res: AcquisitionResult): boolean {
  if (res.identity_status === "FAILED") return false;
  const hasIdentity = res.identity_status === "PRESENT" || res.identity_status === "MATCHED";
  const hasEconomics =
    res.observed_offer_min_price != null ||
    !!res.currency ||
    (!!res.availability_state && res.availability_state !== "UNKNOWN");
  return hasIdentity || hasEconomics;
}

/** Generic CSS selectors for common ticket-market price elements. */
function genericOfferSelectors(): string[] {
  return [
    "meta[property='og:price:amount']",
    "meta[itemprop=price]",
    "[data-testid='price']",
    ".price",
    ".offer-price",
    ".ticket-price",
    "[data-price]",
  ];
}

/** Build a searchable text blob from scrape results. */
function scrapeToString(results: Array<{ selector: string; results: Array<{ text?: string }> }>): string {
  return results
    .flatMap((r) => r.results.map((e) => e.text || ""))
    .filter(Boolean)
    .join("\n");
}

/** Deterministically extract offer price from scrape element results. */
function extractFromScrape(results: Array<{ selector: string; results: Array<{ text?: string; attributes?: Array<{ name: string; value: string }> }> }>): Extracted {
  const out: Extracted = {
    observed_offer_min_price: null,
    currency: null,
    price_basis: "NO_STRUCTURED",
    inventory_basis: "UNKNOWN",
    availability_state: "UNKNOWN",
    identity_status: "PRESENT",
  };
  let best: number | null = null;
  for (const block of results) {
    for (const el of block.results) {
      // og:price:amount meta
      const metaAttr = (el.attributes || []).find((a) => a.name === "property" && a.value === "og:price:amount" || a.name === "itemprop" && a.value === "price");
      if (metaAttr && metaAttr.value !== null) {
        const p = parsePriceFromString(el.text || "");
        if (p != null && (best == null || p < best)) best = p;
        continue;
      }
      // element text like "$45.00"
      const p = parsePriceFromString(el.text || "");
      if (p != null && (best == null || p < best)) best = p;
      // data-price attribute
      const dp = (el.attributes || []).find((a) => a.name === "data-price");
      if (dp) {
        const pv = parseCurrencyValue(dp.value);
        if (pv != null && (best == null || pv.value < best)) best = pv.value;
      }
    }
  }
  if (best != null) {
    out.observed_offer_min_price = best;
    out.price_basis = "PUBLIC_PAGE_CSS";
  }
  return out;
}

/** Parse a string like "$45.00" or "45.00" into a number. */
function parsePriceFromString(s: string): number | null {
  const m = String(s).replace(/[,$\s]/g, "");
  if (!m) return null;
  const n = parseFloat(m);
  return isNaN(n) ? null : n;
}

/** Parse a value possibly prefixed with a currency symbol. */
function parseCurrencyValue(v: string): { value: number; currency: string | null } | null {
  const m = String(v || "").trim();
  const n = parseFloat(m.replace(/[^0-9.]/g, ""));
  if (isNaN(n)) return null;
  const cur = /[$€£]/.test(m) ? m.match(/[$€£]/)![0] : null;
  return { value: n, currency: cur };
}

async function sha256Hex(s: string): Promise<string> {
  const b = new TextEncoder().encode(s);
  const h = await crypto.subtle.digest("SHA-256", b.buffer as ArrayBuffer);
  return [...new Uint8Array(h)].map((x) => x.toString(16).padStart(2, "0")).join("");
}