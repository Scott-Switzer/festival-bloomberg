/**
 * Cache-first fetch tiers (explicit order, no R2):
 * Fresh Cache -> Local Fetch/HTTP -> Playwright -> Monid -> Apify
 *
 * Monid is preferred over Apify for managed/structured fetch (cost).
 */
import { randomUUID } from "node:crypto";
import type { MonidClient } from "./monid";
import {
  ObservationSchema,
  type CostEvent,
  type Observation,
  type TelemetryEvent,
} from "./schemas";

export type FetchTier =
  | "fresh_cache"
  | "local_http"
  | "playwright"
  | "monid"
  | "apify";

/** Explicit default ordering — keep R2 out of the chain. */
export const DEFAULT_TIER_ORDER: readonly FetchTier[] = [
  "fresh_cache",
  "local_http",
  "playwright",
  "monid",
  "apify",
] as const;

export type CachedDocument = {
  url: string;
  body: string;
  contentType?: string;
  fetchedAt: string;
  contentHash?: string;
};

export type FetchResult = {
  ok: boolean;
  tier: FetchTier;
  url: string;
  body?: string;
  contentType?: string;
  structured?: unknown;
  error?: string;
  observation?: Observation;
};

export type CacheStore = {
  getFresh(url: string, softTtlMs: number): Promise<CachedDocument | null>;
  put(doc: CachedDocument): Promise<void>;
};

export type HttpFetcher = (url: string) => Promise<{
  ok: boolean;
  status: number;
  body: string;
  contentType?: string;
  error?: string;
}>;

export type PlaywrightFetcher = (url: string) => Promise<{
  ok: boolean;
  body: string;
  contentType?: string;
  error?: string;
}>;

export type ApifyFetcher = (url: string) => Promise<{
  ok: boolean;
  body?: string;
  structured?: unknown;
  error?: string;
  costUsd?: number;
}>;

export type FallbackConfig = {
  tierOrder: readonly FetchTier[];
  softTtlMs: number;
  enablePlaywright: boolean;
  enableMonid: boolean;
  enableApify: boolean;
};

export const DEFAULT_FALLBACK_CONFIG: FallbackConfig = {
  tierOrder: DEFAULT_TIER_ORDER,
  softTtlMs: 6 * 60 * 60 * 1000,
  enablePlaywright: true,
  enableMonid: true,
  enableApify: true,
};

export type FallbackEnsembleDeps = {
  cache?: CacheStore;
  http?: HttpFetcher;
  playwright?: PlaywrightFetcher;
  monid?: MonidClient;
  apify?: ApifyFetcher;
  config?: Partial<FallbackConfig>;
  onCost?: (e: CostEvent) => void;
  onTelemetry?: (e: TelemetryEvent) => void;
  now?: () => Date;
};

export class FallbackEnsemble {
  private readonly cache?: CacheStore;
  private readonly http?: HttpFetcher;
  private readonly playwright?: PlaywrightFetcher;
  private readonly monid?: MonidClient;
  private readonly apify?: ApifyFetcher;
  private readonly config: FallbackConfig;
  private readonly onCost: (e: CostEvent) => void;
  private readonly onTelemetry: (e: TelemetryEvent) => void;
  private readonly now: () => Date;

  constructor(deps: FallbackEnsembleDeps = {}) {
    this.cache = deps.cache;
    this.http = deps.http;
    this.playwright = deps.playwright;
    this.monid = deps.monid;
    this.apify = deps.apify;
    this.config = { ...DEFAULT_FALLBACK_CONFIG, ...deps.config };
    this.onCost = deps.onCost ?? (() => undefined);
    this.onTelemetry = deps.onTelemetry ?? (() => undefined);
    this.now = deps.now ?? (() => new Date());
  }

  /** Resolve URL through configured tiers until one succeeds. */
  async fetch(url: string, meta?: { festivalId?: string; editionId?: string; sourceDomain?: string }): Promise<FetchResult> {
    const domain = meta?.sourceDomain ?? safeHost(url);
    const errors: string[] = [];

    for (const tier of this.config.tierOrder) {
      if (!this.tierEnabled(tier)) continue;
      const started = this.now();
      try {
        const result = await this.tryTier(tier, url);
        this.emitTelemetry(tier, started, result.ok, domain, url, result.error);
        if (!result.ok) {
          if (result.error) errors.push(`${tier}:${result.error}`);
          continue;
        }
        const observation = ObservationSchema.parse({
          id: randomUUID(),
          kind: "raw_html",
          festivalId: meta?.festivalId,
          editionId: meta?.editionId,
          sourceDomain: domain,
          url,
          observedAt: this.now().toISOString(),
          payload: {
            body: result.body,
            contentType: result.contentType,
            structured: result.structured,
          },
          evidence: [
            {
              url,
              fetchedAt: this.now().toISOString(),
              snippet: result.body?.slice(0, 280),
            },
          ],
          tier,
        });
        return { ...result, observation };
      } catch (err) {
        const msg = err instanceof Error ? err.message : "tier_error";
        errors.push(`${tier}:${msg}`);
        this.emitTelemetry(tier, started, false, domain, url, msg);
      }
    }

    return {
      ok: false,
      tier: "apify",
      url,
      error: errors.length ? errors.join("; ") : "all_tiers_failed",
    };
  }

  private tierEnabled(tier: FetchTier): boolean {
    if (tier === "playwright") return this.config.enablePlaywright && !!this.playwright;
    if (tier === "monid") return this.config.enableMonid && !!this.monid?.isConfigured;
    if (tier === "apify") return this.config.enableApify && !!this.apify;
    if (tier === "fresh_cache") return !!this.cache;
    if (tier === "local_http") return !!this.http;
    return false;
  }

  private async tryTier(tier: FetchTier, url: string): Promise<FetchResult> {
    switch (tier) {
      case "fresh_cache": {
        const doc = await this.cache!.getFresh(url, this.config.softTtlMs);
        if (!doc) return { ok: false, tier, url, error: "cache_miss" };
        return {
          ok: true,
          tier,
          url,
          body: doc.body,
          contentType: doc.contentType,
        };
      }
      case "local_http": {
        const res = await this.http!(url);
        if (!res.ok) return { ok: false, tier, url, error: res.error ?? `http_${res.status}` };
        await this.cache?.put({
          url,
          body: res.body,
          contentType: res.contentType,
          fetchedAt: this.now().toISOString(),
        });
        return {
          ok: true,
          tier,
          url,
          body: res.body,
          contentType: res.contentType,
        };
      }
      case "playwright": {
        const res = await this.playwright!(url);
        if (!res.ok) return { ok: false, tier, url, error: res.error ?? "playwright_failed" };
        await this.cache?.put({
          url,
          body: res.body,
          contentType: res.contentType,
          fetchedAt: this.now().toISOString(),
        });
        return {
          ok: true,
          tier,
          url,
          body: res.body,
          contentType: res.contentType,
        };
      }
      case "monid": {
        const res = await this.monid!.fetchStructured({ url });
        if (!res.ok) return { ok: false, tier, url, error: res.error ?? "monid_failed" };
        const body =
          typeof res.data === "string"
            ? res.data
            : JSON.stringify(res.data ?? {});
        await this.cache?.put({
          url,
          body,
          contentType: "application/json",
          fetchedAt: this.now().toISOString(),
        });
        return {
          ok: true,
          tier,
          url,
          body,
          contentType: "application/json",
          structured: res.data,
        };
      }
      case "apify": {
        const res = await this.apify!(url);
        if (!res.ok) return { ok: false, tier, url, error: res.error ?? "apify_failed" };
        if (typeof res.costUsd === "number") {
          this.onCost({
            id: randomUUID(),
            provider: "apify",
            operation: "fetch",
            units: 1,
            unitCostUsd: res.costUsd,
            totalCostUsd: res.costUsd,
            currency: "USD",
            at: this.now().toISOString(),
            meta: { url },
          });
        }
        if (res.body) {
          await this.cache?.put({
            url,
            body: res.body,
            fetchedAt: this.now().toISOString(),
          });
        }
        return {
          ok: true,
          tier,
          url,
          body: res.body,
          structured: res.structured,
        };
      }
      default: {
        const _exhaustive: never = tier;
        return { ok: false, tier: _exhaustive, url, error: "unknown_tier" };
      }
    }
  }

  private emitTelemetry(
    tier: FetchTier,
    started: Date,
    ok: boolean,
    domain: string,
    url: string,
    errorCode?: string,
  ): void {
    const at = this.now();
    this.onTelemetry({
      id: randomUUID(),
      name: "fallback.fetch",
      level: ok ? "info" : "debug",
      at: at.toISOString(),
      durationMs: Math.max(0, at.getTime() - started.getTime()),
      domain,
      url,
      tier,
      ok,
      errorCode,
      meta: {},
    });
  }
}

/** In-memory cache for tests / local runs (not durable; no R2). */
export class MemoryCacheStore implements CacheStore {
  private readonly map = new Map<string, CachedDocument>();
  private readonly now: () => number;

  constructor(now: () => number = Date.now) {
    this.now = now;
  }

  async getFresh(url: string, softTtlMs: number): Promise<CachedDocument | null> {
    const doc = this.map.get(url);
    if (!doc) return null;
    const age = this.now() - Date.parse(doc.fetchedAt);
    if (!Number.isFinite(age) || age > softTtlMs) return null;
    return doc;
  }

  async put(doc: CachedDocument): Promise<void> {
    this.map.set(doc.url, doc);
  }
}

export function createDefaultHttpFetcher(fetchImpl: typeof fetch = fetch): HttpFetcher {
  return async (url: string) => {
    try {
      const res = await fetchImpl(url, {
        headers: { Accept: "text/html,application/json;q=0.9,*/*;q=0.8" },
      });
      const body = await res.text();
      return {
        ok: res.ok,
        status: res.status,
        body,
        contentType: res.headers.get("content-type") ?? undefined,
        error: res.ok ? undefined : `http_${res.status}`,
      };
    } catch (err) {
      return {
        ok: false,
        status: 0,
        body: "",
        error: err instanceof Error ? err.message : "http_error",
      };
    }
  };
}

function safeHost(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return "unknown";
  }
}
