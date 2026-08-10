/**
 * Keyless Wikimedia REST Pageviews adapter.
 *
 * Endpoint:
 *   GET /metrics/pageviews/per-article/{project}/{access}/{agent}/{article}/{granularity}/{start}/{end}
 *
 * No API key required. Titles are space→underscore then URI-encoded. Requests
 * use DomainRateLimiter + AbortController timeouts consistent with the scraper.
 */
import { createHash } from "node:crypto";
import { z } from "zod";
import {
  DomainRateLimiter,
  DEFAULT_DOMAIN_LIMITS,
  type DomainLimitConfig,
} from "./rate_limiter";

export const WIKIMEDIA_PAGEVIEWS_BASE =
  "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article";
export const WIKIMEDIA_PAGEVIEWS_DOMAIN = "wikimedia.org";
export const WIKIMEDIA_PAGEVIEWS_ADAPTER_VERSION = "wikimedia_pageviews_v1";
export const INTELLIGENCE_METRIC_VERSION = "intelligence_metrics_v1";

export const PageviewsAccessSchema = z.enum([
  "all-access",
  "desktop",
  "mobile-app",
  "mobile-web",
]);
export const PageviewsAgentSchema = z.enum([
  "all-agents",
  "user",
  "spider",
  "automated",
]);
export const PageviewsGranularitySchema = z.enum(["daily", "monthly"]);

export type PageviewsAccess = z.infer<typeof PageviewsAccessSchema>;
export type PageviewsAgent = z.infer<typeof PageviewsAgentSchema>;
export type PageviewsGranularity = z.infer<typeof PageviewsGranularitySchema>;

const PageviewItemSchema = z.object({
  project: z.string(),
  article: z.string(),
  granularity: z.string(),
  timestamp: z.string(),
  access: z.string(),
  agent: z.string(),
  views: z.number(),
});

export const PageviewsResponseSchema = z.object({
  items: z.array(PageviewItemSchema).min(1),
});

export type PageviewsResponse = z.infer<typeof PageviewsResponseSchema>;
export type PageviewItem = z.infer<typeof PageviewItemSchema>;

export type WikimediaPageviewsRequest = {
  /** Article title as humans write it (spaces allowed). */
  articleTitle: string;
  project?: string;
  access?: PageviewsAccess;
  agent?: PageviewsAgent;
  granularity?: PageviewsGranularity;
  /** YYYYMMDD or YYYYMMDDHH */
  start: string;
  /** YYYYMMDD or YYYYMMDDHH */
  end: string;
  artistKey?: string;
  festivalKey?: string;
  editionKey?: string;
  editionYear?: number;
};

export type WikimediaPageviewsResult = {
  ok: boolean;
  status: "ok" | "error" | "missing";
  httpStatus: number;
  sourceUrl: string;
  retrievedAt: string;
  project: string;
  access: PageviewsAccess;
  agent: PageviewsAgent;
  articleTitle: string;
  encodedArticle: string;
  granularity: PageviewsGranularity;
  start: string;
  end: string;
  items: PageviewItem[];
  valueSum: number | null;
  rawResponse: unknown;
  errorCode?: string;
  errorMessage?: string;
  adapterVersion: string;
  metricVersion: string;
  provenance: {
    sourceSystem: "wikimedia";
    endpoint: "per-article";
    userAgent: string;
    request: Record<string, string | number | undefined>;
  };
};

export type WikimediaPageviewsClientOptions = {
  fetchImpl?: typeof fetch;
  rateLimiter?: DomainRateLimiter;
  timeoutMs?: number;
  userAgent?: string;
  baseUrl?: string;
  now?: () => Date;
  domainLimits?: Partial<DomainLimitConfig>;
};

const DEFAULT_USER_AGENT =
  "FestivalBloomberg/0.1 (intelligence-metrics; keyless-pageviews; contact=festival-bloomberg)";

/**
 * Encode a Wikipedia article title for the per-article path segment.
 * Spaces become underscores, then URI-encoding is applied once.
 */
export function encodePageviewsArticleTitle(title: string): string {
  const normalized = title.trim().replace(/\s+/g, "_");
  if (!normalized) {
    throw new Error("article_title_empty");
  }
  return encodeURIComponent(normalized);
}

export function buildPageviewsUrl(input: {
  project: string;
  access: PageviewsAccess;
  agent: PageviewsAgent;
  articleTitle: string;
  granularity: PageviewsGranularity;
  start: string;
  end: string;
  baseUrl?: string;
}): { url: string; encodedArticle: string } {
  const encodedArticle = encodePageviewsArticleTitle(input.articleTitle);
  const project = input.project.replace(/\/+$/, "");
  const base = (input.baseUrl ?? WIKIMEDIA_PAGEVIEWS_BASE).replace(/\/+$/, "");
  const url = [
    base,
    encodeURIComponent(project),
    encodeURIComponent(input.access),
    encodeURIComponent(input.agent),
    encodedArticle,
    encodeURIComponent(input.granularity),
    encodeURIComponent(input.start),
    encodeURIComponent(input.end),
  ].join("/");
  return { url, encodedArticle };
}

export function parsePageviewsResponse(payload: unknown): {
  items: PageviewItem[];
  valueSum: number;
} {
  const parsed = PageviewsResponseSchema.parse(payload);
  const valueSum = parsed.items.reduce((sum, item) => sum + item.views, 0);
  return { items: parsed.items, valueSum };
}

export function attentionObservationKey(parts: {
  artistKey: string;
  sourceSystem: string;
  metricKind: string;
  project?: string | null;
  periodStart?: string | null;
  periodEnd?: string | null;
  metricVersion: string;
}): string {
  const material = [
    parts.artistKey,
    parts.sourceSystem,
    parts.metricKind,
    parts.project ?? "",
    parts.periodStart ?? "",
    parts.periodEnd ?? "",
    parts.metricVersion,
  ].join("|");
  return createHash("sha256").update(material).digest("hex").slice(0, 32);
}

function yyyymmddToIsoDate(raw: string): string {
  const digits = raw.replace(/\D/g, "").slice(0, 8);
  if (digits.length !== 8) {
    throw new Error(`invalid_pageviews_date:${raw}`);
  }
  return `${digits.slice(0, 4)}-${digits.slice(4, 6)}-${digits.slice(6, 8)}`;
}

export class WikimediaPageviewsClient {
  private readonly fetchImpl: typeof fetch;
  private readonly rateLimiter: DomainRateLimiter;
  private readonly timeoutMs: number;
  private readonly userAgent: string;
  private readonly baseUrl: string;
  private readonly now: () => Date;

  constructor(opts: WikimediaPageviewsClientOptions = {}) {
    this.fetchImpl = opts.fetchImpl ?? fetch;
    this.timeoutMs = opts.timeoutMs ?? 15_000;
    this.userAgent = opts.userAgent ?? DEFAULT_USER_AGENT;
    this.baseUrl = opts.baseUrl ?? WIKIMEDIA_PAGEVIEWS_BASE;
    this.now = opts.now ?? (() => new Date());
    this.rateLimiter =
      opts.rateLimiter ??
      new DomainRateLimiter({
        defaults: {
          ...DEFAULT_DOMAIN_LIMITS,
          tokensPerSecond: 2,
          bucketSize: 4,
          minSpacingMs: 200,
          ...opts.domainLimits,
        },
      });
  }

  async fetchPerArticle(
    request: WikimediaPageviewsRequest,
  ): Promise<WikimediaPageviewsResult> {
    const project = request.project ?? "en.wikipedia";
    const access = request.access ?? "all-access";
    const agent = request.agent ?? "user";
    const granularity = request.granularity ?? "daily";
    const retrievedAt = this.now().toISOString();
    const { url, encodedArticle } = buildPageviewsUrl({
      project,
      access,
      agent,
      articleTitle: request.articleTitle,
      granularity,
      start: request.start,
      end: request.end,
      baseUrl: this.baseUrl,
    });

    const provenance: WikimediaPageviewsResult["provenance"] = {
      sourceSystem: "wikimedia",
      endpoint: "per-article",
      userAgent: this.userAgent,
      request: {
        project,
        access,
        agent,
        articleTitle: request.articleTitle,
        encodedArticle,
        granularity,
        start: request.start,
        end: request.end,
        artistKey: request.artistKey,
        festivalKey: request.festivalKey,
        editionKey: request.editionKey,
        editionYear: request.editionYear,
      },
    };

    const baseResult: Omit<
      WikimediaPageviewsResult,
      "ok" | "status" | "httpStatus" | "items" | "valueSum" | "rawResponse"
    > & {
      items: PageviewItem[];
      valueSum: number | null;
      rawResponse: unknown;
    } = {
      sourceUrl: url,
      retrievedAt,
      project,
      access,
      agent,
      articleTitle: request.articleTitle.trim(),
      encodedArticle,
      granularity,
      start: request.start,
      end: request.end,
      items: [],
      valueSum: null,
      rawResponse: null,
      adapterVersion: WIKIMEDIA_PAGEVIEWS_ADAPTER_VERSION,
      metricVersion: INTELLIGENCE_METRIC_VERSION,
      provenance,
    };

    try {
      const response = await this.rateLimiter.executeWithRetry(
        WIKIMEDIA_PAGEVIEWS_DOMAIN,
        async () => this.requestOnce(url),
      );

      if (response.httpStatus === 404) {
        return {
          ...baseResult,
          ok: false,
          status: "missing",
          httpStatus: 404,
          rawResponse: response.body,
          errorCode: "pageviews_not_found",
          errorMessage: "Article or date range not found in Pageviews API",
        };
      }

      if (!response.ok) {
        return {
          ...baseResult,
          ok: false,
          status: "error",
          httpStatus: response.httpStatus,
          rawResponse: response.body,
          errorCode: `http_${response.httpStatus}`,
          errorMessage: response.errorMessage ?? `HTTP ${response.httpStatus}`,
        };
      }

      try {
        const { items, valueSum } = parsePageviewsResponse(response.body);
        return {
          ...baseResult,
          ok: true,
          status: "ok",
          httpStatus: response.httpStatus,
          items,
          valueSum,
          rawResponse: response.body,
        };
      } catch (error) {
        return {
          ...baseResult,
          ok: false,
          status: "error",
          httpStatus: response.httpStatus,
          rawResponse: response.body,
          errorCode: "response_parse_error",
          errorMessage: error instanceof Error ? error.message : String(error),
        };
      }
    } catch (error) {
      const status =
        typeof (error as { status?: number })?.status === "number"
          ? (error as { status: number }).status
          : 0;
      return {
        ...baseResult,
        ok: false,
        status: "error",
        httpStatus: status,
        rawResponse: null,
        errorCode:
          (error as { code?: string })?.code ??
          (status ? `http_${status}` : "request_failed"),
        errorMessage: error instanceof Error ? error.message : String(error),
      };
    }
  }

  /**
   * Map a successful/failed fetch into a normalized attention observation row.
   */
  toAttentionObservation(
    result: WikimediaPageviewsResult,
    identity: {
      artistKey: string;
      festivalKey?: string;
      editionKey?: string;
      editionYear?: number;
    },
  ) {
    let periodStart: string | null = null;
    let periodEnd: string | null = null;
    try {
      periodStart = yyyymmddToIsoDate(result.start);
      periodEnd = yyyymmddToIsoDate(result.end);
    } catch {
      periodStart = null;
      periodEnd = null;
    }

    const observationKey = attentionObservationKey({
      artistKey: identity.artistKey,
      sourceSystem: "wikimedia",
      metricKind: "pageviews",
      project: result.project,
      periodStart,
      periodEnd,
      metricVersion: result.metricVersion,
    });

    return {
      observation_key: observationKey,
      artist_key: identity.artistKey,
      festival_key: identity.festivalKey ?? null,
      edition_key: identity.editionKey ?? null,
      edition_year: identity.editionYear ?? null,
      source_system: "wikimedia" as const,
      metric_kind: "pageviews" as const,
      project: result.project,
      access_method: result.access,
      agent: result.agent,
      article_title: result.articleTitle,
      granularity: result.granularity,
      period_start: periodStart,
      period_end: periodEnd,
      value: result.valueSum,
      value_sum: result.valueSum,
      value_unit: "pageviews",
      status: result.status,
      error_code: result.errorCode ?? null,
      error_message: result.errorMessage ?? null,
      source_url: result.sourceUrl,
      retrieved_at: result.retrievedAt,
      raw_response_json: result.rawResponse,
      provenance_json: result.provenance,
      metric_version: result.metricVersion,
    };
  }

  private async requestOnce(url: string): Promise<{
    ok: boolean;
    httpStatus: number;
    body: unknown;
    errorMessage?: string;
  }> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const response = await this.fetchImpl(url, {
        method: "GET",
        headers: {
          Accept: "application/json",
          "User-Agent": this.userAgent,
        },
        signal: controller.signal,
      });

      const text = await response.text();
      let body: unknown = text;
      if (text) {
        try {
          body = JSON.parse(text);
        } catch {
          body = text;
        }
      } else {
        body = null;
      }

      if (!response.ok) {
        const retryable =
          response.status === 429 || response.status >= 500;
        if (retryable) {
          const err = Object.assign(
            new Error(`Wikimedia Pageviews HTTP ${response.status}`),
            {
              status: response.status,
              retryAfter: response.headers.get("retry-after"),
              code: `http_${response.status}`,
            },
          );
          throw err;
        }
      }

      return {
        ok: response.ok,
        httpStatus: response.status,
        body,
        errorMessage: response.ok
          ? undefined
          : `Wikimedia Pageviews HTTP ${response.status}`,
      };
    } catch (error) {
      if ((error as { name?: string })?.name === "AbortError") {
        const err = Object.assign(new Error("Wikimedia Pageviews timeout"), {
          status: 0,
          code: "timeout",
        });
        throw err;
      }
      throw error;
    } finally {
      clearTimeout(timer);
    }
  }
}
