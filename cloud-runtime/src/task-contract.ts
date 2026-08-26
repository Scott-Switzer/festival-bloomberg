/**
 * Acquisition Task Contract — deterministic task metadata for Cloudflare Queues.
 *
 * Every task carries only non-secret execution metadata.
 * The idempotency key MUST be deterministic from these fields.
 *
 * Cloudflare Queues are at-least-once: duplicate delivery MUST NOT create
 * duplicate economic truth.
 */

/** Acquisition rail classification */
export type AcquisitionRail = "FAST" | "DEEP" | "EVENT" | "OTHER";

/** Task priority — lower number = higher priority */
export type TaskPriority = 1 | 2 | 3 | 4 | 5;

/** Task status lifecycle */
export type TaskStatus =
  | "PLANNED"
  | "QUEUED"
  | "LEASED"
  | "EXECUTING"
  | "COMPLETED"
  | "FAILED"
  | "DLQ"
  | "SUPPRESSED"  // duplicate delivery detected
  | "BUDGET_BLOCKED"
  | "RIGHTS_BLOCKED";

/** Source/provider identifier */
export type SourceProvider =
  | "monid"
  | "tickets_dev"
  | "ticketmaster"
  | "seatgeek"
  | "stubhub"
  | "vividseats"
  | "tickpick"
  | "gametime"
  | "dice"
  | "eventbrite"
  | "bandsintown"
  | "songkick"
  | "other";

/**
 * The core acquisition task — everything needed to execute a single fetch.
 * No secrets. No credentials. No budget state.
 */
export interface AcquisitionTask {
  /** Deterministic idempotency key: hash of event_key + marketplace + rail + window + mapping_version */
  task_key: string;

  /** Canonical event identifier from acquisition.event_identifiers */
  event_key: string;

  /** Source provider */
  source: SourceProvider;

  /** Target marketplace */
  marketplace: string;

  /** Acquisition rail */
  rail: AcquisitionRail;

  /** Exact mapped URL or provider-specific event/object ID */
  target_url: string;

  /** Scheduled observation window (ISO-8601) */
  scheduled_window: string;

  /** Task priority (1=highest) */
  priority: TaskPriority;

  /** Expected maximum cost in USD for this single task */
  expected_max_cost_usd: number;

  /** When this task was created (ISO-8601) */
  created_at: string;

  /** Software version that created this task */
  software_version: string;

  /** Mapping version if relevant for dedup */
  mapping_version?: string;

  /** Event metadata for context (non-secret) */
  event_metadata?: {
    artist_name?: string;
    venue_name?: string;
    city?: string;
    event_date?: string;
    time_to_show_days?: number;
  };

  /** Trigger source — scheduled vs event-driven */
  trigger: "SCHEDULED" | "EVENT_DRIVEN";

  /** If event-driven, what triggered this capture */
  trigger_reason?: string;

  /** Acquisition run ID for grouping */
  run_id: string;
}

/**
 * Result of executing an acquisition task.
 */
export interface TaskResult {
  task_key: string;
  status: TaskStatus;
  started_at: string;
  completed_at: string;
  duration_ms: number;

  /** HTTP-level result */
  http_status?: number;
  http_success: boolean;

  /** Data written */
  raw_object_key?: string;
  raw_bytes?: number;
  raw_sha256?: string;

  /** Observation metadata */
  observations_written: number;
  snapshots_appended: number;

  /** Cost accounting */
  actual_cost_usd: number;
  cost_basis?: string;  // MEASURED | PUBLISHED_PRICE_ASSUMPTION | CONTRACT_VALIDATED_ONLY

  /** Error details if failed */
  error_category?:
    | "TIMEOUT"
    | "RATE_LIMIT"
    | "AUTH_FAILURE"
    | "BLOCKED"
    | "PARSE_FAILURE"
    | "R2_WRITE_FAILURE"
    | "BUDGET_EXCEEDED"
    | "RIGHTS_BLOCKED"
    | "UNSUPPORTED_MARKETPLACE"
    | "MALFORMED_PAYLOAD"
    | "PROVIDER_ERROR";
  error_detail?: string;

  /** Whether this was a duplicate delivery */
  duplicate_detected: boolean;
}

/**
 * Generate a deterministic idempotency key from task fields.
 * Same inputs MUST produce the same key.
 */
export function generateTaskKey(
  event_key: string,
  marketplace: string,
  rail: AcquisitionRail,
  scheduled_window: string,
  mapping_version: string = "v1"
): string {
  const raw = `${event_key}|${marketplace}|${rail}|${scheduled_window}|${mapping_version}`;
  // Use a simple deterministic hash (not cryptographic — just for dedup)
  let hash = 0;
  for (let i = 0; i < raw.length; i++) {
    const char = raw.charCodeAt(i);
    hash = ((hash << 5) - hash + char) | 0;
  }
  return `task_${Math.abs(hash).toString(36)}_${event_key.slice(0, 8)}`;
}

/**
 * A run record — tracks a single acquisition scheduling cycle.
 */
export interface AcquisitionRun {
  run_id: string;
  started_at: string;
  completed_at?: string;
  status: "RUNNING" | "COMPLETED" | "FAILED" | "PARTIAL";

  /** Planning summary */
  events_planned: number;
  tasks_planned: number;
  tasks_queued: number;
  tasks_suppressed: number;
  tasks_budget_blocked: number;

  /** Execution summary */
  tasks_completed: number;
  tasks_failed: number;
  tasks_dlq: number;
  tasks_retried: number;

  /** Data summary */
  raw_objects_written: number;
  raw_bytes_written: number;
  observations_written: number;
  snapshots_appended: number;

  /** Cost */
  total_cost_usd: number;

  /** Errors */
  errors: Array<{
    task_key: string;
    error_category: string;
    error_detail?: string;
  }>;
}
