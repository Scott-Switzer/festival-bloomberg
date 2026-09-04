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

const CURRENT_KEY = "serving/artist_security_terminal_v1/CURRENT.json";
const GENERATION_OBJECT_PREFIX = "serving/artist_security_terminal_v1/generations/";
const SHA256_RE = /^[a-f0-9]{64}$/i;
const ACCESS_PATH_RE = /^\/[A-Za-z0-9_-]{16,96}$/;
const DENIED_PRODUCT_PATHS = [
  "/admin",
  "/batch",
  "/dispatch",
  "/governor",
  "/ops",
  "/reset-governor",
  "/trigger",
  "/test-fetch",
  "/test-monid",
];

function isDeniedProductPath(pathname: string): boolean {
  return DENIED_PRODUCT_PATHS.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}

export interface TerminalEnv {
  TERMINAL_CONTAINER: DurableObjectNamespace;
  LAKE_BUCKET: R2Bucket;
  ASSETS: Fetcher;
  // A random, deployment-specific path is the fallback allowlist when
  // Cloudflare Access has not yet been configured. The deployed Worker fails
  // closed if this is absent or malformed; local static assets remain useful.
  TERMINAL_ACCESS_PATH?: string;
  [key: string]: unknown;
}

function accessPath(env: TerminalEnv): string | null {
  const value = String(env.TERMINAL_ACCESS_PATH || "").trim();
  return ACCESS_PATH_RE.test(value) ? value : null;
}

function productPath(
  pathname: string,
  configuredPath: string,
): string | null {
  if (pathname === configuredPath || pathname === `${configuredPath}/`) return "/";
  if (pathname.startsWith(`${configuredPath}/`)) {
    return pathname.slice(configuredPath.length) || "/";
  }
  return null;
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
    if (current.sha256 && !SHA256_RE.test(current.sha256)) {
      return Response.json({ error: "TERMINAL_SHA256_INVALID" }, { status: 500 });
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
        "X-Serving-Generation": current.generation || "",
        "X-Serving-SHA256": current.sha256 || "",
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

    // The separate terminal is intentionally product-only. Do not let a
    // static-assets fallback turn an acquisition/admin path into a successful
    // HTML response, even if the SPA has a client-side fallback.
    if (isDeniedProductPath(url.pathname)) {
      return Response.json(
        { error: "not found" },
        { status: 404, headers: { "X-Content-Type-Options": "nosniff" } },
      );
    }

    const configuredPath = accessPath(env);
    const logicalPath = configuredPath
      ? productPath(url.pathname, configuredPath)
      : null;

    // Product/API/bootstrap traffic is never served without the deployment
    // allowlist path. This is the safe fallback until a Cloudflare Access
    // application is placed in front of the Worker.
    if (!configuredPath && (
      url.pathname === "/health" ||
      url.pathname === "/_bootstrap" ||
      url.pathname.startsWith("/api/")
    )) {
      return Response.json(
        { error: "TERMINAL_ACCESS_SETUP_REQUIRED" },
        { status: 503, headers: { "X-Content-Type-Options": "nosniff" } },
      );
    }

    if (logicalPath === "/_bootstrap" && request.method === "GET") {
      return bootstrapServing(env, url.searchParams.get("artifact") || "metadata");
    }

    if (logicalPath === "/health" || logicalPath?.startsWith("/api/")) {
      const stub = env.TERMINAL_CONTAINER.get(
        env.TERMINAL_CONTAINER.idFromName("terminal"),
      );
      // Keep the protected external path on the request until it reaches the
      // DO. The DO strips it for the product server but needs it in order to
      // give the container a bootstrap URL that can pass the same allowlist.
      return stub.fetch(request);
    }

    // Only the access-path root is the product shell. Static JS/CSS contain no
    // data and are served from the edge; the SPA prefixes its API calls with
    // the current access path.
    if (logicalPath === "/") {
      const shellUrl = new URL(request.url);
      shellUrl.pathname = "/index.html";
      return env.ASSETS.fetch(new Request(shellUrl, request));
    }
    if (logicalPath?.startsWith("/static/")) {
      const assetUrl = new URL(request.url);
      assetUrl.pathname = logicalPath;
      return env.ASSETS.fetch(new Request(assetUrl, request));
    }
    // index.html references /static/* from the origin root. Those assets are
    // immutable UI code only (no data or API credentials), so they may remain
    // edge-public while the shell and every data route stay behind the
    // deployment-specific access path.
    if (url.pathname.startsWith("/static/")) {
      return env.ASSETS.fetch(request);
    }
    if (url.pathname === "/" || url.pathname === "/index.html") {
      return Response.json(
        { error: configuredPath ? "not found" : "TERMINAL_ACCESS_SETUP_REQUIRED" },
        { status: configuredPath ? 404 : 503 },
      );
    }
    return Response.json({ error: "not found" }, { status: 404 });
  },
};
