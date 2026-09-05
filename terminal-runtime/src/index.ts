/**
 * Festival Bloomberg — dedicated read-only terminal Worker.
 *
 * Routes:
 *   /_bootstrap?artifact=metadata|db  → streams CURRENT.json / the current
 *        terminal.duckdb from the R2 LAKE binding (used by the container at
 *        cold start; serves only the PUBLIC read-only product artifact).
 *   /health, /api/*                    → proxied to TerminalContainer, which
 *        owns the READ_ONLY serving DuckDB and the product API.
 *   everything else                    → static terminal assets (SPA).
 *
 * Deliberately absent: acquisition admin API, batch triggers, provider
 * secrets, warehouse writes, Gold materialization.
 */

export { TerminalContainer } from "./terminal-container-do";
import {
  isDeniedProductPath,
  isPublicDemoPathBlocked,
} from "./routing";

const CURRENT_KEY = "serving/artist_security_terminal_v1/CURRENT.json";
const GENERATION_OBJECT_PREFIX = "serving/artist_security_terminal_v1/generations/";
const SHA256_RE = /^[a-f0-9]{64}$/i;
const ACCESS_PATH_RE = /^\/[A-Za-z0-9_-]{16,96}$/;
const INTERNAL_PREFIX_HEADER = "X-Terminal-Access-Prefix";
const STAGING_PUBLIC_DEMO = "STAGING_PUBLIC_DEMO";
const PRODUCTION_PRIVATE = "PRODUCTION_PRIVATE";

export interface TerminalEnv {
  TERMINAL_CONTAINER: DurableObjectNamespace;
  LAKE_BUCKET: R2Bucket;
  ASSETS: Fetcher;
  TERMINAL_MODE?: string;
  // Production-only fallback when Cloudflare Access is not yet attached.
  // This is a deployment secret, never a source-controlled value.
  TERMINAL_ACCESS_PATH?: string;
  [key: string]: unknown;
}

function terminalMode(env: TerminalEnv): string {
  return String(env.TERMINAL_MODE || PRODUCTION_PRIVATE).trim().toUpperCase();
}

function isPublicDemo(env: TerminalEnv): boolean {
  return terminalMode(env) === STAGING_PUBLIC_DEMO;
}

function accessPath(env: TerminalEnv): string | null {
  const value = String(env.TERMINAL_ACCESS_PATH || "").trim();
  return ACCESS_PATH_RE.test(value) ? value : null;
}

function productPath(pathname: string, configuredPath: string): string | null {
  if (pathname === configuredPath || pathname === `${configuredPath}/`) return "/";
  if (pathname.startsWith(`${configuredPath}/`)) {
    return pathname.slice(configuredPath.length) || "/";
  }
  return null;
}

async function serveShell(
  env: TerminalEnv,
  request: Request,
  publicDemo: boolean,
  prefix: string,
): Promise<Response> {
  const shellUrl = new URL(request.url);
  shellUrl.pathname = "/index.html";
  const response = await env.ASSETS.fetch(new Request(shellUrl, request));
  const label = publicDemo
    ? "STAGING / PUBLIC DATA DEMO"
    : "PRODUCTION / PRIVATE ACCESS";
  const headers = new Headers(response.headers);
  // The body is rewritten only to make the deployment mode visible. Remove
  // entity headers that would describe the original asset bytes.
  headers.delete("content-length");
  headers.delete("content-encoding");
  const assetPrefix = prefix || "";
  const body = (await response.text())
    .replace('href="/static/', `href="${assetPrefix}/static/`)
    .replace('src="/static/', `src="${assetPrefix}/static/`)
    .replace(
      "<!-- TERMINAL_ENV_BADGE -->",
      `<span class="env-badge">${label}</span>`,
    );
  return new Response(body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

async function bootstrapServing(
  env: TerminalEnv,
  artifact: string,
): Promise<Response> {
  // Narrow, read-only bootstrap for the compact serving artifact ONLY:
  // CURRENT.json metadata or the current terminal.duckdb, streamed from the
  // LAKE R2 binding. No arbitrary R2 key access, no bucket listing.
  const currentObj = await env.LAKE_BUCKET.get(CURRENT_KEY);
  if (!currentObj) {
    return Response.json(
      { error: "TERMINAL_CURRENT_NOT_FOUND", key: CURRENT_KEY },
      { status: 404 },
    );
  }
  const current = (await currentObj.json()) as {
    generation?: string;
    object_key?: string;
    sha256?: string;
    [k: string]: unknown;
  };
  if (
    !current.generation ||
    !current.sha256 ||
    !SHA256_RE.test(current.sha256)
  ) {
    return Response.json({ error: "TERMINAL_CURRENT_INVALID" }, { status: 500 });
  }
  if (artifact === "metadata") {
    return Response.json(current, {
      headers: { "X-Content-Type-Options": "nosniff" },
    });
  }
  if (artifact === "db") {
    const objectKey = current.object_key;
    if (
      !objectKey ||
      !objectKey.startsWith(GENERATION_OBJECT_PREFIX) ||
      !objectKey.endsWith("/terminal.duckdb")
    ) {
      return Response.json({ error: "TERMINAL_OBJECT_KEY_INVALID" }, { status: 500 });
    }
    const db = await env.LAKE_BUCKET.get(objectKey);
    if (!db) {
      return Response.json(
        { error: "TERMINAL_ARTIFACT_NOT_FOUND", key: objectKey },
        { status: 404 },
      );
    }
    return new Response(db.body, {
      headers: {
        "Content-Type": "application/octet-stream",
        "Content-Length": String(db.size),
        "X-Serving-Generation": current.generation,
        "X-Serving-SHA256": current.sha256,
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
      },
    });
  }
  return Response.json({ error: "unknown artifact type" }, { status: 400 });
}

export default {
  async fetch(request: Request, env: TerminalEnv): Promise<Response> {
    const url = new URL(request.url);
    const publicDemo = isPublicDemo(env);
    const configuredPath = accessPath(env);

    // Public staging is deliberately product-only. Production requires the
    // deployment-specific secret path; without it, the Worker fails closed.
    // Cloudflare Access can be placed in front of that path independently.
    let logicalPath: string | null;
    let prefix = "";
    let authorized = publicDemo;
    if (publicDemo) {
      logicalPath = url.pathname;
    } else {
      const pathMatch = configuredPath ? productPath(url.pathname, configuredPath) : null;
      if (pathMatch !== null) {
        logicalPath = pathMatch;
        prefix = configuredPath || "";
        authorized = true;
      } else {
        logicalPath = null;
      }
    }

    // Check both the external and logical path so an access-prefix request
    // cannot turn /secret/admin into a product response.
    if (
      isDeniedProductPath(url.pathname) ||
      (logicalPath && isDeniedProductPath(logicalPath)) ||
      (publicDemo && logicalPath && isPublicDemoPathBlocked(request.method, logicalPath))
    ) {
      return Response.json(
        { error: "not found" },
        { status: 404, headers: { "X-Content-Type-Options": "nosniff" } },
      );
    }

    if (!authorized || logicalPath === null) {
      return Response.json(
        { error: "TERMINAL_AUTH_REQUIRED", mode: terminalMode(env) },
        { status: 401, headers: { "X-Content-Type-Options": "nosniff" } },
      );
    }

    if (logicalPath === "/_bootstrap" && request.method === "GET") {
      return bootstrapServing(env, url.searchParams.get("artifact") || "metadata");
    }

    if (logicalPath === "/health" || logicalPath.startsWith("/api/")) {
      const stub = env.TERMINAL_CONTAINER.get(
        env.TERMINAL_CONTAINER.idFromName("terminal"),
      );
      // The header is an internal routing hint. It prevents the DO from
      // guessing that /api or /health is an access prefix in public mode.
      const headers = new Headers(request.headers);
      headers.set(INTERNAL_PREFIX_HEADER, prefix);
      return stub.fetch(new Request(request, { headers }));
    }

    if (logicalPath === "/" || logicalPath === "/index.html") {
      return serveShell(env, request, publicDemo, prefix);
    }
    if (logicalPath.startsWith("/static/")) {
      const assetUrl = new URL(request.url);
      assetUrl.pathname = logicalPath.slice("/static".length);
      return env.ASSETS.fetch(new Request(assetUrl, request));
    }
    // Root static assets contain UI code only. They are safe to serve at the
    // root in both modes; product HTML, API, and artifact bootstrap are not.
    if (url.pathname.startsWith("/static/") && publicDemo) {
      return env.ASSETS.fetch(request);
    }
    return Response.json({ error: "not found" }, { status: 404 });
  },
};
