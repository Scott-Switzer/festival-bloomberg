/**
 * Acquisition Operations Scorecard.
 *
 * Never hide failures behind aggregate success rates.
 * Every metric is individually meaningful.
 */

/** Run-level metrics */
export interface RunMetrics {
  runs_started: number;
  runs_completed: number;
  runs_failed: number;
  runs_partial: number;
}

/** Task-level metrics */
export interface TaskMetrics {
  tasks_planned: number;
  tasks_queued: number;
  tasks_started: number;
  tasks_completed: number;
  tasks_retried: number;
  tasks_dlq: number;
  duplicate_deliveries_suppressed: number;
}

/** Network metrics */
export interface NetworkMetrics {
  http_requests: number;
  http_successes: number;
  http_429s: number;
  http_5xx: number;
  http_timeouts: number;
  auth_failures: number;
  connection_failures: number;
}

/** Data metrics */
export interface DataMetrics {
  raw_objects_written: number;
  raw_bytes_written: number;
  snapshots_appended: number;
  listing_observations_written: number;
  new_event_mappings: number;
}

/** Economics metrics */
export interface EconomicsMetrics {
  measured_spend_usd: number;
  assumed_spend_usd: number;
  spend_by_provider: Record<string, number>;
  cost_per_useful_observation: number;
}

/** Latency metrics */
export interface LatencyMetrics {
  queue_lag_seconds: number;
  task_execution_latency_ms: number;
  end_to_end_observation_latency_ms: number;
}

/** Full scorecard */
export interface AcquisitionScorecard {
  timestamp: string;
  run_id: string;
  policy_version: string;
  software_version: string;
  runs: RunMetrics;
  tasks: TaskMetrics;
  network: NetworkMetrics;
  data: DataMetrics;
  economics: EconomicsMetrics;
  latency: LatencyMetrics;
}

/** Create an empty scorecard */
export function createScorecard(run_id: string): AcquisitionScorecard {
  return {
    timestamp: new Date().toISOString(),
    run_id,
    policy_version: "1.0.0",
    software_version: "cloud-acquisition-runtime-v1",
    runs: { runs_started: 1, runs_completed: 0, runs_failed: 0, runs_partial: 0 },
    tasks: {
      tasks_planned: 0, tasks_queued: 0, tasks_started: 0,
      tasks_completed: 0, tasks_retried: 0, tasks_dlq: 0,
      duplicate_deliveries_suppressed: 0,
    },
    network: {
      http_requests: 0, http_successes: 0, http_429s: 0, http_5xx: 0,
      http_timeouts: 0, auth_failures: 0, connection_failures: 0,
    },
    data: {
      raw_objects_written: 0, raw_bytes_written: 0, snapshots_appended: 0,
      listing_observations_written: 0, new_event_mappings: 0,
    },
    economics: {
      measured_spend_usd: 0, assumed_spend_usd: 0, spend_by_provider: {},
      cost_per_useful_observation: 0,
    },
    latency: {
      queue_lag_seconds: 0, task_execution_latency_ms: 0,
      end_to_end_observation_latency_ms: 0,
    },
  };
}
