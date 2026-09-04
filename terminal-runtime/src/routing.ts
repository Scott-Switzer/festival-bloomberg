export const DENIED_PRODUCT_PATHS = [
  "/admin",
  "/batch",
  "/dispatch",
  "/governor",
  "/ops",
  "/reset-governor",
  "/trigger",
  "/test-fetch",
  "/test-monid",
  "/private",
  "/customer",
  "/workspace",
] as const;

// Public staging is a read-only product demo. These APIs expose durable
// customer/workspace, upload/import, or watchlist state and are intentionally
// unavailable even though the product container implements them privately.
// Underwrite itself remains available because it evaluates only the caller's
// transient assumptions against public serving data.
export const PUBLIC_DEMO_PRIVATE_PREFIXES = [
  "/api/shortlist",
  "/api/monitor",
  "/api/decisions",
  "/api/backtest",
  "/api/vault",
  "/api/readiness",
  "/api/pace",
  "/api/underwrite/save",
] as const;

export function isDeniedProductPath(pathname: string): boolean {
  return DENIED_PRODUCT_PATHS.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}

export function isPublicDemoPathBlocked(method: string, pathname: string): boolean {
  // Portfolio GET is the one safe read needed by the public demo. Lineup
  // writes and every other portfolio endpoint remain blocked.
  if (method === "GET" && pathname === "/api/portfolio") return false;
  return PUBLIC_DEMO_PRIVATE_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  ) || pathname === "/api/portfolio" || pathname.startsWith("/api/portfolio/");
}
