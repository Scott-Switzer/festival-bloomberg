/**
 * Common Crawl URL Index — bounded discovery queries.
 *
 * Common Crawl's URL index is Parquet and compatible with DuckDB (the repo
 * already uses DuckDB for bulk analytical queries — see the Python provider).
 * This Worker-side module uses the SAME index through the official index API
 * with STRICT bounded domain/pattern queries (never bulk CDX dumps).
 *
 * Output is CANDIDATE EVIDENCE ONLY. It is never automatically accepted
 * identity evidence: deterministic validation (artist + date + venue + city)
 * runs afterward in the mapping factory.
 *
 * PIT semantics (preserved from the canonical Python provider):
 *   capture_time  = the archive's source_as_of (when the page was captured)
 *   retrieved_at  = when WE queried the index (now)
 *   ARCHIVE_CAPTURE != PUBLICATION_TIME. We never backdate knowledge.
 */

const CC_INDEX_API = "https://index.commoncrawl.org/";
const DEFAULT_CRAWL_ID = "CC-MAIN-2026-30";

export interface CcCaptureCandidate {
  url: string;
  crawl_id: string;
  capture_timestamp: string; // YYYYMMDDHHMMSS from CDX
  source_as_of: string | null; // ISO 8601 of capture
  status_code: string;
  digest: string;
  content_length: string;
  mime: string;
  retrieved_at: string;
  warc_locator: string;
}

export interface CcQueryOptions {
  /** Bounded URL pattern, e.g. "seatgeek.com/*" or "example.com/event/*". */
  urlPattern: string;
  crawlId?: string;
  matchType?: "prefix" | "domain" | "host";
  limit?: number;
  timeoutMs?: number;
}

/**
 * Query the Common Crawl URL index for captures matching a bounded pattern.
 * Returns candidate captures (candidate evidence only).
 */
export async function queryCcUrlIndex(opts: CcQueryOptions): Promise<{
  captures: CcCaptureCandidate[];
  query: string;
  error?: string;
}> {
  const crawlId = opts.crawlId || DEFAULT_CRAWL_ID;
  const limit = Math.min(opts.limit || 20, 200); // hard bound — no bulk
  const timeoutMs = opts.timeoutMs || 30000;

  const params = new URLSearchParams({
    url: opts.urlPattern,
    output: "json",
    limit: String(limit),
    filter: "status:200",
    fl: "timestamp,statuscode,urlkey,digest,length,mime,filename,offset",
  });
  if (opts.matchType) params.set("matchType", opts.matchType);
  const url = `${CC_INDEX_API}${crawlId}-index?${params.toString()}`;

  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    const resp = await fetch(url, {
      headers: { "User-Agent": "FestivalBloomberg/0.1 (research; historical-outcomes)" },
      signal: controller.signal,
    });
    clearTimeout(timer);

    if (resp.status === 404) return { captures: [], query: url }; // no captures
    if (!resp.ok) return { captures: [], query: url, error: `HTTP ${resp.status}` };

    const raw = await resp.text();
    const retrievedAt = new Date().toISOString();
    const captures: CcCaptureCandidate[] = [];

    for (const line of raw.split("\n")) {
      const l = line.trim();
      if (!l) continue;
      try {
        const row = JSON.parse(l);
        const ts = String(row.timestamp || "");
        captures.push({
          url: String(row.url || row.original || opts.urlPattern),
          crawl_id: crawlId,
          capture_timestamp: ts,
          source_as_of: cdxTimestampToIso(ts),
          status_code: String(row.statuscode || row.status || ""),
          digest: String(row.digest || ""),
          content_length: String(row.length || ""),
          mime: String(row.mime || ""),
          retrieved_at: retrievedAt,
          warc_locator: row.filename
            ? `https://data.commoncrawl.org/${row.filename}`
            : `https://data.commoncrawl.org/${crawlId}-index?url=${encodeURIComponent(opts.urlPattern)}`,
        });
      } catch {
        // skip malformed line
      }
      if (captures.length >= limit) break;
    }
    return { captures, query: url };
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return { captures: [], query: url, error: msg };
  }
}

/** CDX timestamps are YYYYMMDDHHMMSS → ISO 8601 UTC (or null). */
export function cdxTimestampToIso(ts: string): string | null {
  const t = String(ts || "").trim();
  if (t.length < 8 || !/^\d+$/.test(t)) return null;
  const year = t.slice(0, 4);
  const month = t.slice(4, 6);
  const day = t.slice(6, 8);
  const hour = t.slice(8, 10) || "00";
  const minute = t.slice(10, 12) || "00";
  const second = t.slice(12, 14) || "00";
  return `${year}-${month}-${day}T${hour}:${minute}:${second}Z`;
}

/**
 * Get the latest available crawl index id (e.g. CC-MAIN-2026-30).
 * Cached per Worker instance to avoid hammering collinfo.json.
 */
let cachedCrawlId: string | null = null;
let cacheExpiresAt = 0;

export async function latestCrawlId(timeoutMs = 15000): Promise<string> {
  const now = Date.now();
  if (cachedCrawlId && now < cacheExpiresAt) return cachedCrawlId;
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    const resp = await fetch("https://index.commoncrawl.org/collinfo.json", {
      signal: controller.signal,
    });
    clearTimeout(timer);
    if (resp.ok) {
      const data = (await resp.json()) as Array<{ id?: string }>;
      if (Array.isArray(data) && data.length && data[0]?.id) {
        cachedCrawlId = data[0].id;
        cacheExpiresAt = now + 60 * 60 * 1000; // 1h cache
        return cachedCrawlId;
      }
    }
  } catch {
    // fall through to default
  }
  return DEFAULT_CRAWL_ID;
}
