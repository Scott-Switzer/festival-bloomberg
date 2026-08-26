/**
 * Cloudflare Browser Run wrappers (and direct HTTP rail).
 *
 * Rail hierarchy (cheapest first):
 *   RAIL_0_DIRECT_HTTP     Worker fetch()              (HTML/JSON/JSON-LD, no JS)
 *   RAIL_1_BROWSER_CONTENT Browser Run /content        (JS-rendered HTML)
 *   RAIL_2_BROWSER_SCRAPE  Browser Run /scrape         (deterministic CSS selectors)
 *   RAIL_4_MONID           Monid context.dev fallback  (blocked/incomplete direct)
 *
 * Playwright (@cloudflare/playwright) is intentially NOT imported here; it
 * requires the package and a PersistentContext durable object. For V1 of the
 * router we use Browser Run Quick Actions, which cover JS-rendered pages and
 * structured selector extraction without the Playwright dependency. A thin
 * hook is exposed for future /captureWithPlaywright.
 *
 * Every wrapper returns browser execution ms where available (X-Browser-Ms-Used).
 */

/** Router acquisition provider — the entity making the HTTP call. */
export type RouterProvider =
  | "direct"
  | "browser_run"
  | "monid"
  | "playwright"
  | "none";

/** Router rail — which acquisition mechanism was used. */
export type RouterRail =
  | "RAIL_0_DIRECT_HTTP"
  | "RAIL_1_BROWSER_CONTENT"
  | "RAIL_2_BROWSER_SCRAPE"
  | "RAIL_3_PLAYWRIGHT"
  | "RAIL_4_MONID"
  | "RAIL_5_SPECIALIZED"
  | "RAIL_UNSUPPORTED";

export interface BrowserBinding {
  quickAction(action: string, body: Record<string, unknown>): Promise<unknown>;
}

export interface FetchOutcome {
  ok: boolean;
  http_status: number;
  final_url: string;
  raw: string;
  raw_bytes: number;
  raw_sha256: string;
  latency_ms: number;
  browser_ms: number;
  provider: RouterProvider;
  rail: RouterRail;
  error_category?: string;
  error_detail?: string;
}

export interface ScrapeResult {
  selector: string;
  results: Array<{
    text?: string;
    html?: string;
    attributes?: Array<{ name: string; value: string }>;
  }>;
}

/** sha256 hex */
async function sha256Hex(s: string): Promise<string> {
  const b = new TextEncoder().encode(s);
  const h = await crypto.subtle.digest("SHA-256", b.buffer as ArrayBuffer);
  return [...new Uint8Array(h)].map((x) => x.toString(16).padStart(2, "0")).join("");
}

/**
 * RAIL_0 — direct Worker fetch(). Cheapest. Use for HTML/JSON/JSON-LD where
 * the evidence is present without JavaScript.
 */
export async function fetchDirect(url: string, opts: { timeoutMs?: number } = {}): Promise<FetchOutcome> {
  const start = Date.now();
  const timeoutMs = opts.timeoutMs || 15000;
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    const resp = await fetch(url, {
      headers: {
        "User-Agent": "Mozilla/5.0 (compatible; FestivalIntelligenceBot/1.0)",
        Accept: "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
      },
      redirect: "follow",
      signal: controller.signal,
    });
    clearTimeout(timer);

    const raw = await resp.text();
    const rawBytes = raw.length;
    const rawSha256 = await sha256Hex(raw);
    return {
      ok: resp.ok,
      http_status: resp.status,
      final_url: resp.url || url,
      raw,
      raw_bytes: rawBytes,
      raw_sha256: rawSha256,
      latency_ms: Date.now() - start,
      browser_ms: 0,
      provider: "direct",
      rail: "RAIL_0_DIRECT_HTTP",
      error_category: resp.ok ? undefined : httpErrorCategory(resp.status),
    };
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return {
      ok: false,
      http_status: 0,
      final_url: url,
      raw: "",
      raw_bytes: 0,
      raw_sha256: "",
      latency_ms: Date.now() - start,
      browser_ms: 0,
      provider: "direct",
      rail: "RAIL_0_DIRECT_HTTP",
      error_category: msg.toLowerCase().includes("abort") ? "TIMEOUT" : "CONNECTION",
      error_detail: msg,
    };
  }
}

/**
 * RAIL_1 — Browser Run /content. Fully rendered HTML after JS execution.
 */
export async function fetchBrowserContent(
  browser: BrowserBinding,
  url: string,
  opts: { waitUntil?: string; timeoutMs?: number } = {}
): Promise<FetchOutcome> {
  const start = Date.now();
  try {
    const body: Record<string, unknown> = { url };
    if (opts.waitUntil) {
      body.gotoOptions = { waitUntil: opts.waitUntil };
    }
    const outcome = (await browser.quickAction("content", body)) as any;
    // outcome may be a Response (streaming) or a parsed object.
    let html = "";
    let browserMs = 0;
    if (outcome instanceof Response) {
      browserMs = Number(outcome.headers.get("X-Browser-Ms-Used")) || 0;
      html = await outcome.text();
    } else if (outcome && typeof outcome === "object") {
      html = outcome.html || outcome.content || outcome.text || "";
      browserMs = Number((outcome as any).browser_ms) || 0;
    }
    const ok = !!html;
    return {
      ok,
      http_status: ok ? 200 : 0,
      final_url: url,
      raw: html,
      raw_bytes: html.length,
      raw_sha256: await sha256Hex(html),
      latency_ms: Date.now() - start,
      browser_ms: browserMs,
      provider: "browser_run",
      rail: "RAIL_1_BROWSER_CONTENT",
      error_category: ok ? undefined : "EMPTY_BODY",
    };
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return {
      ok: false,
      http_status: 0,
      final_url: url,
      raw: "",
      raw_bytes: 0,
      raw_sha256: "",
      latency_ms: Date.now() - start,
      browser_ms: 0,
      provider: "browser_run",
      rail: "RAIL_1_BROWSER_CONTENT",
      error_category: classifyBrowserError(msg),
      error_detail: msg,
    };
  }
}

/**
 * RAIL_2 — Browser Run /scrape. Extract deterministic CSS selectors.
 * Returns selector results which callers normalize into structured fields.
 */
export async function scrapeBrowserElements(
  browser: BrowserBinding,
  url: string,
  selectors: string[],
  opts: { waitUntil?: string } = {}
): Promise<{
  ok: boolean;
  http_status: number;
  final_url: string;
  results: ScrapeResult[];
  latency_ms: number;
  browser_ms: number;
  raw_sha256: string;
  error_category?: string;
  error_detail?: string;
}> {
  const start = Date.now();
  try {
    const body: Record<string, unknown> = {
      url,
      elements: selectors.map((selector) => ({ selector })),
    };
    if (opts.waitUntil) body.gotoOptions = { waitUntil: opts.waitUntil };
    const outcome = (await browser.quickAction("scrape", body)) as any;
    let list: any[] = [];
    let browserMs = 0;
    if (outcome instanceof Response) {
      browserMs = Number(outcome.headers.get("X-Browser-Ms-Used")) || 0;
      const data = (await outcome.json()) as any;
      list = data?.result || data?.results || [];
    } else if (outcome && typeof outcome === "object") {
      const o = outcome as any;
      list = o.result || o.results || [];
    }
    const results: ScrapeResult[] = (list as any[]).map((r) => ({
      selector: r.selector || "",
      results: (r.results || []).map((el: any) => ({
        text: el.text,
        html: el.html,
        attributes: el.attributes,
      })),
    }));
    const digestSource = `${url}|${JSON.stringify(results).slice(0, 2000)}`;
    return {
      ok: results.length > 0,
      http_status: results.length > 0 ? 200 : 0,
      final_url: url,
      results,
      latency_ms: Date.now() - start,
      browser_ms: browserMs,
      raw_sha256: await sha256Hex(digestSource),
      error_category: results.length > 0 ? undefined : "EMPTY_SCRAPE",
    };
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return {
      ok: false,
      http_status: 0,
      final_url: url,
      results: [],
      latency_ms: Date.now() - start,
      browser_ms: 0,
      raw_sha256: "",
      error_category: classifyBrowserError(msg),
      error_detail: msg,
    };
  }
}

/** Map an HTTP status to a stable error category. */
export function httpErrorCategory(status: number): string {
  if (status === 429) return "RATE_LIMIT";
  if (status === 403) return "BLOCKED";
  if (status === 401) return "AUTH_FAILURE";
  if (status >= 500) return "SERVER_ERROR";
  if (status === 404) return "NOT_FOUND";
  return "HTTP_ERROR";
}

function classifyBrowserError(msg: string): string {
  const m = msg.toLowerCase();
  if (m.includes("timeout") || m.includes("navigation timeout")) return "TIMEOUT";
  if (m.includes("blocked") || m.includes("403")) return "BLOCKED";
  if (m.includes("not found") || m.includes("404")) return "NOT_FOUND";
  return "BROWSER_ERROR";
}