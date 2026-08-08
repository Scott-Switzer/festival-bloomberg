/**
 * Domain-aware request queue with token-bucket limits, spacing,
 * Retry-After support, and exponential backoff with jitter.
 */

export type DomainLimitConfig = {
  /** Sustained tokens per second. */
  tokensPerSecond: number;
  /** Burst capacity. */
  bucketSize: number;
  /** Minimum spacing between requests to the same domain (ms). */
  minSpacingMs: number;
  /** Max retries after 429/5xx. */
  maxRetries: number;
  /** Base backoff ms before jitter. */
  baseBackoffMs: number;
  /** Cap for backoff ms. */
  maxBackoffMs: number;
};

export const DEFAULT_DOMAIN_LIMITS: DomainLimitConfig = {
  tokensPerSecond: 1,
  bucketSize: 3,
  minSpacingMs: 250,
  maxRetries: 4,
  baseBackoffMs: 500,
  maxBackoffMs: 30_000,
};

type BucketState = {
  tokens: number;
  updatedAt: number;
  lastRequestAt: number;
  queue: Promise<void>;
};

export type RateLimiterOptions = {
  defaults?: Partial<DomainLimitConfig>;
  perDomain?: Record<string, Partial<DomainLimitConfig>>;
  now?: () => number;
  sleep?: (ms: number) => Promise<void>;
};

function sleepDefault(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Full jitter in [0, backoff]. */
export function backoffWithJitter(
  attempt: number,
  baseMs: number,
  maxMs: number,
  random: () => number = Math.random,
): number {
  const exp = Math.min(maxMs, baseMs * 2 ** attempt);
  return Math.floor(random() * exp);
}

export function parseRetryAfter(header: string | null | undefined, now = Date.now()): number | null {
  if (!header) return null;
  const asInt = Number(header);
  if (Number.isFinite(asInt) && asInt >= 0) {
    // RFC: integer seconds
    return Math.ceil(asInt * 1000);
  }
  const dateMs = Date.parse(header);
  if (!Number.isNaN(dateMs)) {
    return Math.max(0, dateMs - now);
  }
  return null;
}

export class DomainRateLimiter {
  private readonly defaults: DomainLimitConfig;
  private readonly perDomain: Record<string, Partial<DomainLimitConfig>>;
  private readonly buckets = new Map<string, BucketState>();
  private readonly now: () => number;
  private readonly sleep: (ms: number) => Promise<void>;

  constructor(opts: RateLimiterOptions = {}) {
    this.defaults = { ...DEFAULT_DOMAIN_LIMITS, ...opts.defaults };
    this.perDomain = opts.perDomain ?? {};
    this.now = opts.now ?? Date.now;
    this.sleep = opts.sleep ?? sleepDefault;
  }

  configFor(domain: string): DomainLimitConfig {
    return { ...this.defaults, ...this.perDomain[domain] };
  }

  private bucket(domain: string): BucketState {
    let b = this.buckets.get(domain);
    if (!b) {
      const cfg = this.configFor(domain);
      b = {
        tokens: cfg.bucketSize,
        updatedAt: this.now(),
        lastRequestAt: 0,
        queue: Promise.resolve(),
      };
      this.buckets.set(domain, b);
    }
    return b;
  }

  private refill(domain: string): void {
    const cfg = this.configFor(domain);
    const b = this.bucket(domain);
    const t = this.now();
    const elapsed = Math.max(0, t - b.updatedAt) / 1000;
    b.tokens = Math.min(cfg.bucketSize, b.tokens + elapsed * cfg.tokensPerSecond);
    b.updatedAt = t;
  }

  /** Serialize and rate-limit work for a domain. */
  async schedule<T>(domain: string, fn: () => Promise<T>): Promise<T> {
    const b = this.bucket(domain);
    const run = b.queue.then(async () => {
      const cfg = this.configFor(domain);
      this.refill(domain);
      if (b.tokens < 1) {
        const need = 1 - b.tokens;
        const waitMs = Math.ceil((need / cfg.tokensPerSecond) * 1000);
        await this.sleep(waitMs);
        this.refill(domain);
      }
      const sinceLast = this.now() - b.lastRequestAt;
      if (b.lastRequestAt > 0 && sinceLast < cfg.minSpacingMs) {
        await this.sleep(cfg.minSpacingMs - sinceLast);
      }
      b.tokens -= 1;
      b.lastRequestAt = this.now();
      return fn();
    });
    // Keep queue alive even if this task fails.
    b.queue = run.then(
      () => undefined,
      () => undefined,
    );
    return run;
  }

  /**
   * Execute with Retry-After / exponential backoff on retryable failures.
   * `isRetryable` defaults to treating thrown objects with status 429/5xx.
   */
  async executeWithRetry<T>(
    domain: string,
    fn: (attempt: number) => Promise<T>,
    opts?: {
      isRetryable?: (err: unknown) => boolean;
      retryAfterMs?: (err: unknown) => number | null;
    },
  ): Promise<T> {
    const cfg = this.configFor(domain);
    const isRetryable =
      opts?.isRetryable ??
      ((err: unknown) => {
        const status = (err as { status?: number })?.status;
        return status === 429 || (typeof status === "number" && status >= 500);
      });
    const retryAfterMs =
      opts?.retryAfterMs ??
      ((err: unknown) => {
        const header = (err as { retryAfter?: string | null })?.retryAfter;
        return parseRetryAfter(header ?? null, this.now());
      });

    let lastErr: unknown;
    for (let attempt = 0; attempt <= cfg.maxRetries; attempt++) {
      try {
        return await this.schedule(domain, () => fn(attempt));
      } catch (err) {
        lastErr = err;
        if (attempt >= cfg.maxRetries || !isRetryable(err)) throw err;
        const ra = retryAfterMs(err);
        const wait =
          ra != null
            ? ra
            : backoffWithJitter(attempt, cfg.baseBackoffMs, cfg.maxBackoffMs);
        await this.sleep(wait);
      }
    }
    throw lastErr;
  }
}
