/**
 * Batch control auth (V1B P0-4).
 *
 * /batch/trigger and /batch/status MUST fail closed:
 *   - ADMIN_TOKEN missing/empty → 503 BATCH_AUTH_NOT_CONFIGURED
 *   - wrong token              → 401
 *   - correct token            → allowed (returns null)
 *
 * There is NO development-open fallback for batch control. Existing other
 * dev endpoints preserve their own semantics outside this module.
 */

/** Fixed response body when ADMIN_TOKEN is not configured. */
export function batchAuthNotConfiguredResponse(): Response {
  return Response.json(
    {
      error: "BATCH_AUTH_NOT_CONFIGURED: ADMIN_TOKEN is not set; batch control is disabled",
      code: "BATCH_AUTH_NOT_CONFIGURED",
    },
    { status: 503 },
  );
}

/**
 * Fail-closed auth for batch control endpoints.
 *
 * Returns a Response (503/401) when the request is NOT authorized, or null
 * when authorized.
 */
export function requireBatchAuth(request: Request, adminToken: string): Response | null {
  if (!adminToken) {
    // Production deployment MUST set ADMIN_TOKEN. No silent development-open
    // behavior for cloud batch endpoints.
    return batchAuthNotConfiguredResponse();
  }
  const authHeader = request.headers.get("Authorization");
  const tokenHeader = request.headers.get("X-Admin-Token");
  if (authHeader !== `Bearer ${adminToken}` && tokenHeader !== adminToken) {
    return Response.json(
      { error: "Unauthorized: batch control requires admin auth" },
      { status: 401 },
    );
  }
  return null;
}
