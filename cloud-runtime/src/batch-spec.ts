/**
 * Batch spec sanitization + container env construction (V1B P0-3, P1).
 *
 * P0-3: Job specs may contain only safe logical control data:
 *   job_id, job_type, params, source_generation, max_batches
 * Secrets (R2 credentials, FI_LISTENER_HMAC_SECRET) NEVER travel through the
 * job spec. The Durable Object constructs the container environment from its
 * OWN environment bindings.
 *
 * P1: Fixed machine-readable error codes replace raw exception text in the
 * status API. Full exception details go to internal logs only.
 */

/** Allowed batch job types (server-side + entrypoint enforcement). */
export const ALLOWED_BATCH_JOB_TYPES = new Set([
  "identity_graph_v2",
  "listenbrainz_map",
  "listenbrainz_reduce",
  "cloud_smoke",
  "terminal_serving_build_v1",
]);

/** Fixed safe error codes — never raw exception text. */
export const BATCH_ERROR_CODES = {
  JOB_VALIDATION_FAILED: "JOB_VALIDATION_FAILED",
  CONTAINER_START_FAILED: "CONTAINER_START_FAILED",
  JOB_EXEC_FAILED: "JOB_EXEC_FAILED",
  R2_READ_FAILED: "R2_READ_FAILED",
  R2_VERIFY_FAILED: "R2_VERIFY_FAILED",
  PUBLICATION_FAILED: "PUBLICATION_FAILED",
  LISTENER_KEY_CONFIG_FAILED: "LISTENER_KEY_CONFIG_FAILED",
  BATCH_AUTH_NOT_CONFIGURED: "BATCH_AUTH_NOT_CONFIGURED",
} as const;
export type BatchErrorCode = (typeof BATCH_ERROR_CODES)[keyof typeof BATCH_ERROR_CODES];

/** Logical job spec — safe control data only, NO secrets. */
export interface BatchJobSpec {
  job_id: string;
  job_type: string;
  params: Record<string, unknown>;
  source_generation?: string;
  max_batches?: number;
}

/** Forbidden keys that could carry secrets or arbitrary commands. */
const FORBIDDEN_SPEC_KEYS = [
  "command",
  "exec",
  "executable",
  "shell",
  "entrypoint",
  "cmd",
  "env_vars",
  "environment",
];

/**
 * Sanitize a raw trigger body into a safe logical BatchJobSpec.
 *
 * - Validates job_type against the allowlist.
 * - Rejects forbidden keys (arbitrary commands, env_vars/secrets).
 * - Bounds job_id and source_generation.
 * - Returns a NEW object containing only safe fields.
 */
export function sanitizeJobSpec(raw: unknown): BatchJobSpec {
  if (typeof raw !== "object" || raw === null) {
    throw new Error("JOB_VALIDATION_FAILED: spec must be a JSON object");
  }
  const body = raw as Record<string, unknown>;

  const jobType = body.job_type;
  if (typeof jobType !== "string" || !ALLOWED_BATCH_JOB_TYPES.has(jobType)) {
    throw new Error(
      `JOB_VALIDATION_FAILED: invalid job_type. Allowed: ${[...ALLOWED_BATCH_JOB_TYPES].join(", ")}`
    );
  }

  for (const key of FORBIDDEN_SPEC_KEYS) {
    if (key in body) {
      throw new Error(
        `JOB_VALIDATION_FAILED: spec contains forbidden key '${key}'`
      );
    }
  }

  const jobId = body.job_id;
  if (jobId !== undefined && (typeof jobId !== "string" || jobId.length > 128 || !/^[A-Za-z0-9_-]+$/.test(jobId))) {
    throw new Error("JOB_VALIDATION_FAILED: invalid job_id");
  }

  const sourceGeneration = body.source_generation;
  if (
    sourceGeneration !== undefined &&
    (typeof sourceGeneration !== "string" || sourceGeneration.length > 128 || !/^[A-Za-z0-9_-]+$/.test(sourceGeneration))
  ) {
    throw new Error("JOB_VALIDATION_FAILED: invalid source_generation");
  }

  const params = body.params;
  if (params !== undefined && (typeof params !== "object" || params === null || Array.isArray(params))) {
    throw new Error("JOB_VALIDATION_FAILED: params must be an object");
  }

  const maxBatches = body.max_batches;
  if (
    maxBatches !== undefined &&
    (typeof maxBatches !== "number" || !Number.isInteger(maxBatches) || maxBatches < 1 || maxBatches > 2000)
  ) {
    throw new Error("JOB_VALIDATION_FAILED: invalid max_batches");
  }

  return {
    job_id: (jobId as string) || `batch_${Date.now()}`,
    job_type: jobType,
    params: (params as Record<string, unknown>) || {},
    source_generation: sourceGeneration as string | undefined,
    max_batches: maxBatches as number | undefined,
  };
}

/**
 * Build the container environment from the DO's OWN bindings (P0-3).
 *
 * Never reads secrets from a job spec. The container gets R2 credentials and
 * the listener HMAC secret directly from the DO environment bindings, which
 * are provisioned via wrangler secrets.
 */
export function buildContainerEnv(env: Record<string, string | undefined>): Record<string, string> {
  const result: Record<string, string> = {
    FI_OBJECT_STORE: "R2",
    FI_R2_ENDPOINT: env.FI_R2_ENDPOINT || "",
    FI_R2_ACCESS_KEY_ID: env.FI_R2_ACCESS_KEY_ID || "",
    FI_R2_SECRET_ACCESS_KEY: env.FI_R2_SECRET_ACCESS_KEY || "",
    FI_R2_RAW_BUCKET: env.FI_R2_RAW_BUCKET || "festival-intelligence-raw",
    FI_R2_LAKE_BUCKET: env.FI_R2_LAKE_BUCKET || "festival-intelligence-lake",
    FI_R2_PRIVATE_BUCKET: env.FI_R2_PRIVATE_BUCKET || "festival-intelligence-private",
    FI_R2_BACKUP_BUCKET: env.FI_R2_BACKUP_BUCKET || "festival-intelligence-backups",
    FI_LISTENER_HMAC_SECRET: env.FI_LISTENER_HMAC_SECRET || "",
    FI_LISTENER_HMAC_SECRET_VERSION: env.FI_LISTENER_HMAC_SECRET_VERSION || "",
    PYTHONUNBUFFERED: "1",
    FI_SCRATCH_DIR: "/tmp/festival-bloomberg",
  };
  return result;
}

/** Sanitize a job summary error into a fixed safe error code (P1). */
export function mapErrorToCode(raw: unknown, fallback: BatchErrorCode = BATCH_ERROR_CODES.JOB_EXEC_FAILED): string {
  if (typeof raw === "string") {
    for (const code of Object.values(BATCH_ERROR_CODES)) {
      if (raw.includes(code)) return code;
    }
    // Known error-code-like prefixes from the Python side.
    if (raw.startsWith("R2_")) return raw;
    if (raw.startsWith("PUBLICATION_")) return BATCH_ERROR_CODES.PUBLICATION_FAILED;
    if (raw.startsWith("LISTENER_KEY_")) return BATCH_ERROR_CODES.LISTENER_KEY_CONFIG_FAILED;
  }
  return fallback;
}
