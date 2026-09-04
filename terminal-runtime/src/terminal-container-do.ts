/**
 * TerminalContainer — Durable Object-backed product container.
 *
 * Cold-start contract (P3):
 *   fetch CURRENT.json metadata → stream terminal.duckdb → SHA verify →
 *   open READ_ONLY → serve mvp_server. All of that happens INSIDE the
 *   container image (docker/terminal_entrypoint.py); this DO only starts the
 *   container with the environment it needs (its own public origin, which the
 *   entrypoint uses to pull the artifact through the Worker's R2 binding) and
 *   proxies product traffic to its TCP port 8080.
 *
 * Security posture: no secrets reach the container. The bootstrap origin
 * serves ONLY the public read-only serving artifact. There are no acquisition
 * endpoints, no provider credentials, no admin surface here by construction.
 */

import { DurableObject } from "cloudflare:workers";

export interface TerminalRuntimeEnv {
  // Worker-level bindings are inherited by the DO; only what the container
  // image itself needs is passed via ctx.container.start({ env }).
  [key: string]: unknown;
}

const START_TIMEOUT_MS = 900_000; // generous: image pull + ~138MB artifact cold start
const HEALTH_POLL_MS = 5_000;
const PRODUCT_PORT = 8080;

export class TerminalContainer extends DurableObject<TerminalRuntimeEnv> {
  private containerReady = false;
  private startPromise: Promise<void> | null = null;
  private http: Fetcher | null = null;

  constructor(state: DurableObjectState, env: TerminalRuntimeEnv) {
    super(state, env);
  }

  /**
   * Proxy product traffic (and /health) to the container's HTTP server.
   * The first request triggers the cold-start bootstrap, which is polled to
   * completion so callers never see a half-initialized terminal.
   */
  async fetch(request: Request): Promise<Response> {
    const requestUrl = new URL(request.url);
    const configuredPath = requestUrl.pathname.match(/^\/[A-Za-z0-9_-]{16,96}/)?.[0] || "";
    const origin = `${requestUrl.origin}${configuredPath}`;
    const logicalPath = configuredPath
      ? requestUrl.pathname.slice(configuredPath.length) || "/"
      : requestUrl.pathname;
    await this.ensureContainerStarted(origin);
    const forwardedUrl = new URL(request.url);
    forwardedUrl.pathname = logicalPath;
    return this.http!.fetch(new Request(forwardedUrl, request));
  }

  /**
   * Admin RPC used by the deploy workflow: force the long-lived container
   * instance to restart so a newly deployed image (with a newer serving
   * generation) replaces a still-running old-image instance.
   */
  async restartContainer(reason = "deploy"): Promise<Record<string, unknown>> {
    const hadContainer = this.containerReady;
    this.containerReady = false;
    this.startPromise = null;
    this.http = null;
    try {
      if (this.ctx.container) {
        await this.ctx.container.destroy();
      }
    } catch (e) {
      console.error("Terminal container destroy failed (continuing):", e);
    }
    return { restarted: true, had_container: hadContainer, reason };
  }

  private ensureContainerStarted(origin: string): Promise<void> {
    if (this.containerReady) return Promise.resolve();
    if (!this.startPromise) {
      this.startPromise = this.startContainer(origin)
        .then(() => {
          this.containerReady = true;
        })
        .catch((e) => {
          this.startPromise = null; // allow retry on next request
          throw e;
        });
    }
    return this.startPromise;
  }

  private async startContainer(origin: string): Promise<void> {
    // No secrets here by design: the container pulls the PUBLIC serving
    // artifact from its own worker origin (_bootstrap route).
    const container = this.ctx.container!;
    if (!container.running) {
      await container.start({
        env: {
          BOOTSTRAP_BASE: origin,
          PRODUCT_SCRATCH_DIR: "/tmp/festival-bloomberg-terminal",
        },
        enableInternet: true, // needs to reach its own public origin for the artifact
      });
    }

    // monitor() resolves only when the container exits; it is a lifecycle
    // hook, not a readiness barrier. Poll the product port below while the
    // monitor runs in the background so the first request can complete.
    void container.monitor()
      .then(() => {
        this.containerReady = false;
        this.startPromise = null;
        this.http = null;
      })
      .catch((error) => {
        console.error("Terminal container exited with error:", error);
        this.containerReady = false;
        this.startPromise = null;
        this.http = null;
      });

    const http = container.getTcpPort(PRODUCT_PORT);

    // Poll /health until the entrypoint reports a verified, open generation.
    const deadline = Date.now() + START_TIMEOUT_MS;
    let lastError = "no health response yet";
    while (Date.now() < deadline) {
      try {
        const health = await http.fetch(new Request(`${origin}/health`));
        if (health.ok) {
          this.http = http;
          return;
        }
        lastError = `health status ${health.status}`;
      } catch (e) {
        lastError = String(e);
      }
      await new Promise((resolve) => setTimeout(resolve, HEALTH_POLL_MS));
    }
    throw new Error(`Terminal container bootstrap timeout: ${lastError}`);
  }
}
