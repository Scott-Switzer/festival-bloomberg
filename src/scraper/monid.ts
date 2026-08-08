/**
 * Monid (monid-prod) client adapter — preferred managed/structured fetch
 * over Apify for cost efficiency. Credentials via env; never hardcode secrets.
 */
import { randomUUID } from "node:crypto";
import {
  CostEventSchema,
  TelemetryEventSchema,
  type CostEvent,
  type TelemetryEvent,
} from "./schemas";

export type MonidConfig = {
  apiKey: string;
  baseUrl: string;
  /** Default provider slug for structured festival fetches. */
  defaultProvider: string;
  timeoutMs: number;
  pollIntervalMs: number;
  maxPollAttempts: number;
};

export type MonidRunRequest = {
  provider: string;
  endpoint: string;
  input: Record<string, unknown>;
};

export type MonidRunResult = {
  ok: boolean;
  status: number;
  runId?: string;
  data?: unknown;
  error?: string;
  costUsd?: number;
};

export type CostSink = (event: CostEvent) => void;
export type TelemetrySink = (event: TelemetryEvent) => void;

const DEFAULT_BASE = "https://api.monid.ai";

/** Load config from env. Supports MONID_API_KEY / MONID_PROD_API_KEY. */
export function loadMonidConfig(
  env: NodeJS.ProcessEnv = process.env,
  overrides: Partial<MonidConfig> = {},
): MonidConfig {
  const apiKey = overrides.apiKey ?? env.MONID_API_KEY ?? env.MONID_PROD_API_KEY ?? "";
  return {
    apiKey,
    baseUrl: (overrides.baseUrl ?? env.MONID_BASE_URL ?? DEFAULT_BASE).replace(/\/$/, ""),
    defaultProvider: overrides.defaultProvider ?? env.MONID_DEFAULT_PROVIDER ?? "monid-prod",
    timeoutMs: overrides.timeoutMs ?? numEnv(env.MONID_TIMEOUT_MS, 60_000),
    pollIntervalMs: overrides.pollIntervalMs ?? numEnv(env.MONID_POLL_INTERVAL_MS, 1500),
    maxPollAttempts: overrides.maxPollAttempts ?? numEnv(env.MONID_MAX_POLL_ATTEMPTS, 40),
  };
}

function numEnv(raw: string | undefined, fallback: number): number {
  const n = Number(raw);
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

export class MonidClient {
  private readonly config: MonidConfig;
  private readonly fetchImpl: typeof fetch;
  private readonly onCost: CostSink;
  private readonly onTelemetry: TelemetrySink;
  private readonly now: () => Date;

  constructor(opts: {
    config?: Partial<MonidConfig>;
    env?: NodeJS.ProcessEnv;
    fetchImpl?: typeof fetch;
    onCost?: CostSink;
    onTelemetry?: TelemetrySink;
    now?: () => Date;
  } = {}) {
    this.config = loadMonidConfig(opts.env ?? process.env, opts.config);
    this.fetchImpl = opts.fetchImpl ?? fetch;
    this.onCost = opts.onCost ?? (() => undefined);
    this.onTelemetry = opts.onTelemetry ?? (() => undefined);
    this.now = opts.now ?? (() => new Date());
  }

  get isConfigured(): boolean {
    return this.config.apiKey.length > 0;
  }

  /** Prefer monid-prod structured endpoint when available. */
  async fetchStructured(input: {
    url: string;
    endpoint?: string;
    provider?: string;
    extra?: Record<string, unknown>;
  }): Promise<MonidRunResult> {
    const provider = input.provider ?? this.config.defaultProvider;
    const endpoint = input.endpoint ?? "/web/fetch-structured";
    return this.run({
      provider,
      endpoint,
      input: { url: input.url, ...input.extra },
    });
  }

  async run(req: MonidRunRequest): Promise<MonidRunResult> {
    const started = this.now();
    if (!this.isConfigured) {
      const result: MonidRunResult = {
        ok: false,
        status: 0,
        error: "monid_not_configured",
      };
      this.emitTelemetry("monid.run", started, result);
      return result;
    }

    try {
      const res = await this.request("/v1/run", {
        method: "POST",
        body: JSON.stringify(req),
      });

      if (res.status === 202) {
        const body = (await res.json()) as { runId?: string; id?: string };
        const runId = body.runId ?? body.id;
        if (!runId) {
          const result: MonidRunResult = {
            ok: false,
            status: 202,
            error: "monid_missing_run_id",
          };
          this.emitTelemetry("monid.run", started, result);
          return result;
        }
        const polled = await this.pollRun(runId);
        this.trackCost(req, polled);
        this.emitTelemetry("monid.run", started, polled, { runId });
        return polled;
      }

      const data = await safeJson(res);
      const result: MonidRunResult = res.ok
        ? {
            ok: true,
            status: res.status,
            data,
            costUsd: extractCostUsd(data),
          }
        : {
            ok: false,
            status: res.status,
            data,
            error: extractError(data) ?? `monid_http_${res.status}`,
          };
      this.trackCost(req, result);
      this.emitTelemetry("monid.run", started, result);
      return result;
    } catch (err) {
      const result: MonidRunResult = {
        ok: false,
        status: 0,
        error: err instanceof Error ? err.message : "monid_unknown_error",
      };
      this.emitTelemetry("monid.run", started, result);
      return result;
    }
  }

  private async pollRun(runId: string): Promise<MonidRunResult> {
    for (let i = 0; i < this.config.maxPollAttempts; i++) {
      await delay(this.config.pollIntervalMs);
      const res = await this.request(`/v1/runs/${encodeURIComponent(runId)}`, {
        method: "GET",
      });
      const data = await safeJson(res);
      const status = String(
        (data as { status?: string })?.status ?? "",
      ).toUpperCase();
      if (status === "COMPLETED") {
        return {
          ok: true,
          status: res.status,
          runId,
          data,
          costUsd: extractCostUsd(data),
        };
      }
      if (status === "FAILED") {
        return {
          ok: false,
          status: res.status,
          runId,
          data,
          error: extractError(data) ?? "monid_run_failed",
        };
      }
    }
    return {
      ok: false,
      status: 408,
      runId,
      error: "monid_poll_timeout",
    };
  }

  private async request(path: string, init: RequestInit): Promise<Response> {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), this.config.timeoutMs);
    try {
      return await this.fetchImpl(`${this.config.baseUrl}${path}`, {
        ...init,
        headers: {
          Authorization: `Bearer ${this.config.apiKey}`,
          "Content-Type": "application/json",
          Accept: "application/json",
          ...(init.headers ?? {}),
        },
        signal: ctrl.signal,
      });
    } finally {
      clearTimeout(timer);
    }
  }

  private trackCost(req: MonidRunRequest, result: MonidRunResult): void {
    const total = result.costUsd ?? 0;
    const event = CostEventSchema.parse({
      id: randomUUID(),
      provider: "monid",
      operation: `${req.provider}:${req.endpoint}`,
      units: 1,
      unitCostUsd: total,
      totalCostUsd: total,
      currency: "USD",
      at: this.now().toISOString(),
      meta: {
        ok: result.ok,
        status: result.status,
        runId: result.runId,
        error: result.error,
      },
    });
    this.onCost(event);
  }

  private emitTelemetry(
    name: string,
    started: Date,
    result: MonidRunResult,
    meta: Record<string, unknown> = {},
  ): void {
    const at = this.now();
    const event = TelemetryEventSchema.parse({
      id: randomUUID(),
      name,
      level: result.ok ? "info" : "warn",
      at: at.toISOString(),
      durationMs: Math.max(0, at.getTime() - started.getTime()),
      tier: "monid",
      ok: result.ok,
      errorCode: result.error,
      meta: { status: result.status, ...meta },
    });
    this.onTelemetry(event);
  }
}

async function safeJson(res: Response): Promise<unknown> {
  try {
    return await res.json();
  } catch {
    return null;
  }
}

function extractCostUsd(data: unknown): number | undefined {
  if (!data || typeof data !== "object") return undefined;
  const o = data as Record<string, unknown>;
  const price = o.price ?? o.cost ?? o.billing;
  if (typeof price === "number") return price;
  if (price && typeof price === "object") {
    const amount = (price as { amount?: unknown }).amount;
    if (typeof amount === "number") return amount;
  }
  if (typeof o.costUsd === "number") return o.costUsd;
  return undefined;
}

function extractError(data: unknown): string | undefined {
  if (!data || typeof data !== "object") return undefined;
  const o = data as Record<string, unknown>;
  if (typeof o.error === "string") return o.error;
  if (typeof o.message === "string") return o.message;
  return undefined;
}

function delay(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}
