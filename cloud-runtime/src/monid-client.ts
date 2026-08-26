/**
 * Monid API Client — native Worker implementation.
 *
 * Calls api.monid.ai directly via fetch(). No Container needed for FAST rail.
 * Cost contract: MONID_HTML = $0.0009/call (MEASURED), tinyfish/fetch = $0 when free.
 */

const MONID_BASE = "https://api.monid.ai";

export interface MonidRunResult {
  run_id: string;
  status: string;
  output: any;
  cost: any;
  latency_ms: number;
  error?: string;
}

export interface PageFetchResult {
  status: "FETCHED" | "FETCH_FAILED" | "TIMEOUT";
  html: string;
  provider: string;
  cost_usd: number;
  latency_ms: number;
}

/**
 * Run a Monid endpoint and poll until complete.
 */
export async function monidRun(
  apiKey: string,
  provider: string,
  endpoint: string,
  queryParams?: Record<string, string>,
  body?: Record<string, any>,
  maxPolls = 15,
  pollIntervalMs = 2000
): Promise<MonidRunResult> {
  const start = Date.now();

  const runBody: Record<string, any> = { provider, endpoint };
  if (queryParams) runBody.queryParams = queryParams;
  if (body) runBody.body = body;

  const resp = await fetch(`${MONID_BASE}/v1/run`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(runBody),
  });

  if (!resp.ok) {
    return {
      run_id: "",
      status: "ERROR",
      output: null,
      cost: null,
      latency_ms: Date.now() - start,
      error: `HTTP ${resp.status}: ${(await resp.text()).slice(0, 200)}`,
    };
  }

  const data = await resp.json() as any;
  const runId = data.runId || data.run_id;
  let status = data.status || "RUNNING";

  // Poll until complete
  let polls = 0;
  while (status !== "COMPLETED" && status !== "FAILED" && status !== "ERROR" && polls < maxPolls) {
    await new Promise((r) => setTimeout(r, pollIntervalMs));
    const pollResp = await fetch(`${MONID_BASE}/v1/runs/${runId}`, {
      headers: { Authorization: `Bearer ${apiKey}` },
    });
    if (pollResp.ok) {
      const pollData = await pollResp.json() as any;
      status = pollData.status || "RUNNING";
      if (status === "COMPLETED" || status === "FAILED" || status === "ERROR") {
        return {
          run_id: runId,
          status,
          output: pollData.output || pollData.data || null,
          cost: pollData.cost || pollData.price || null,
          latency_ms: Date.now() - start,
        };
      }
    }
    polls++;
  }

  return {
    run_id: runId,
    status: status === "RUNNING" ? "TIMEOUT" : status,
    output: data.output || data.data || null,
    cost: data.cost || data.price || null,
    latency_ms: Date.now() - start,
  };
}

/**
 * Fetch a marketplace page via Monid.
 * Tries tinyfish/fetch first (free), falls back to context.dev ($0.0009).
 */
export async function fetchPage(
  apiKey: string,
  url: string
): Promise<PageFetchResult> {
  const start = Date.now();

  // Try tinyfish/fetch first (free tier)
  const tinyfishResult = await monidRun(
    apiKey,
    "tinyfish",
    "/fetch",
    undefined,
    { urls: [url], format: "html", ttl: 3600 }
  );

  if (tinyfishResult.status === "COMPLETED") {
    const output = tinyfishResult.output || {};
    const pages = output.pages || output.results || [];
    if (pages.length > 0) {
      const page = pages[0];
      return {
        status: "FETCHED",
        html: page.html || page.content || page.text || "",
        provider: "tinyfish",
        cost_usd: 0,
        latency_ms: Date.now() - start,
      };
    }
  }

  // Fallback: context.dev ($0.0009/call)
  const contextResult = await monidRun(
    apiKey,
    "context.dev",
    "/web/scrape/html",
    { url }
  );

  if (contextResult.status === "COMPLETED") {
    const output = contextResult.output || {};
    return {
      status: "FETCHED",
      html: output.html || output.content || output.text || JSON.stringify(output),
      provider: "context.dev",
      cost_usd: 0.0009,
      latency_ms: Date.now() - start,
    };
  }

  return {
    status: "FETCH_FAILED",
    html: "",
    provider: "none",
    cost_usd: 0,
    latency_ms: Date.now() - start,
  };
}

/**
 * Extract structured ticket-market data from HTML.
 * Priority: JSON-LD → __NEXT_DATA__ → raw text.
 */
export function extractFromPage(
  html: string,
  marketplace: string
): Record<string, any> {
  if (!html) return { has_structured_data: false };

  const extracted: Record<string, any> = {};

  // 1. JSON-LD extraction
  const ldRegex = /<script[^>]*type="application\/ld\+json"[^>]*>([\s\S]*?)<\/script>/gi;
  let match;
  while ((match = ldRegex.exec(html)) !== null) {
    try {
      const ldData = JSON.parse(match[1]);
      if (ldData && typeof ldData === "object") {
        const ldType = ldData["@type"] || "";
        if (ldType === "Event" || ldType === "MusicEvent" || ldType === "Concert") {
          const offers = ldData.offers;
          if (offers && typeof offers === "object" && !Array.isArray(offers)) {
            extracted.price = offers.price;
            extracted.currency = offers.priceCurrency;
            extracted.availability = offers.availability;
          } else if (Array.isArray(offers) && offers.length > 0) {
            const prices = offers
              .map((o: any) => parseFloat(o.price))
              .filter((p: number) => !isNaN(p));
            if (prices.length > 0) {
              extracted.price_min = Math.min(...prices);
            }
          }
          extracted.name = ldData.name;
          extracted.startDate = ldData.startDate;
          const loc = ldData.location;
          if (loc && typeof loc === "object") {
            extracted.venue_name = loc.name;
            const addr = loc.address;
            if (addr && typeof addr === "object") {
              extracted.venue_city = addr.addressLocality;
            }
          }
          break;
        }
      }
    } catch {
      // parse error, continue
    }
  }

  // 2. __NEXT_DATA__ fallback
  if (!extracted.price && !extracted.name) {
    const nextMatch = html.match(
      /<script[^>]*id="__NEXT_DATA__"[^>]*>([\s\S]*?)<\/script>/
    );
    if (nextMatch) {
      try {
        const nd = JSON.parse(nextMatch[1]);
        const props = nd?.props?.pageProps;
        if (props) {
          extracted.title = props.event?.name || props.title;
          extracted.price = props.event?.price || props.price;
          extracted.venue = props.event?.venue?.name || props.venue?.name;
        }
      } catch {
        // parse error
      }
    }
  }

  extracted.has_structured_data = !!(extracted.price || extracted.name);
  return extracted;
}
