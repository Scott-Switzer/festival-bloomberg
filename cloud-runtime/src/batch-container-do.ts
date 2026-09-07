/**
 * Batch Container — Durable Object managing a reusable Container instance
 * for heavy data-processing jobs (Identity Graph V2, ListenBrainz map/reduce).
 *
 * Lifecycle contract (V1B P0-2):
 *   POST /batch/trigger
 *     → validate
 *     → persist RUNNING durable status to R2
 *     → launch container process (no await on output())
 *     → return HTTP 202 immediately
 *
 * The logical job continues independently. Completion is read from durable
 * R2 manifest/checkpoint state, never from a long-lived HTTP connection.
 *
 * Secrets (V1B P0-3): the job spec contains ONLY safe logical control data.
 * R2 credentials and FI_LISTENER_HMAC_SECRET are read from THIS DO's own
 * environment bindings and passed to the container env — never through
 * FI_BATCH_JOB, never through the status API.
 *
 * Ephemeral container disk is NOT persistent. All canonical state is in R2.
 */

import { DurableObject } from "cloudflare:workers";
import { withJobManifest } from "./batch-status";
import {
  BatchJobSpec,
  buildContainerEnv,
  mapErrorToCode,
  BATCH_ERROR_CODES,
} from "./batch-spec";

export interface BatchJobResult {
  job_id: string;
  job_type: string;
  status: "RUNNING" | "COMPLETED" | "FAILED";
  started_at: string;
  completed_at: string;
  duration_ms: number;
  exit_code: number;
  manifest_key?: string;
  last_safe_error_code?: string;
  source_generation?: string;
  code_commit?: string;
  completed_batches?: number;
  total_batches?: number;
  bytes_read?: number;
  bytes_written?: number;
  rows_read?: number;
  rows_written?: number;
  smoke_checks?: unknown;
  publication_state?: string;
  // P8: stdout/stderr are NOT exposed in /batch/status.
  // They are kept here for internal logging only and never returned to clients.
  _stdout?: string;
  _stderr?: string;
}

// P8: Safe status response — structured fields only, no raw stdout/stderr.
export interface BatchStatusResponse {
  job_id: string;
  job_type: string;
  status: string;
  source_generation?: string;
  code_commit?: string;
  started_at: string;
  updated_at: string;
  completed_batches: number;
  total_batches: number;
  bytes_read: number;
  bytes_written: number;
  runtime_seconds: number;
  manifest_key?: string;
  last_safe_error_code?: string;
}

// P0-3: The DO obtains all secrets from its OWN environment bindings.
interface BatchEnv {
  PRIVATE_BUCKET: R2Bucket;
  LAKE_BUCKET: R2Bucket;
  FI_R2_ENDPOINT?: string;
  FI_R2_ACCESS_KEY_ID?: string;
  FI_R2_SECRET_ACCESS_KEY?: string;
  FI_R2_RAW_BUCKET?: string;
  FI_R2_LAKE_BUCKET?: string;
  FI_R2_PRIVATE_BUCKET?: string;
  FI_R2_BACKUP_BUCKET?: string;
  FI_LISTENER_HMAC_SECRET?: string;
  FI_LISTENER_HMAC_SECRET_VERSION?: string;
}

export class BatchContainer extends DurableObject<BatchEnv> {
  private containerReady = false;
  private currentJob: BatchJobSpec | null = null;
  private lastResult: BatchJobResult | null = null;

  constructor(state: DurableObjectState, env: BatchEnv) {
    super(state, env);
  }

  /**
   * P0-3: Start the container with env built from THIS DO's bindings only.
   * Never accepts env/secrets from a job spec.
   */
  /** P0-3: string-only view of the DO bindings for container env construction. */
  private containerEnvSource(): Record<string, string | undefined> {
    return {
      FI_R2_ENDPOINT: this.env.FI_R2_ENDPOINT,
      FI_R2_ACCESS_KEY_ID: this.env.FI_R2_ACCESS_KEY_ID,
      FI_R2_SECRET_ACCESS_KEY: this.env.FI_R2_SECRET_ACCESS_KEY,
      FI_R2_RAW_BUCKET: this.env.FI_R2_RAW_BUCKET,
      FI_R2_LAKE_BUCKET: this.env.FI_R2_LAKE_BUCKET,
      FI_R2_PRIVATE_BUCKET: this.env.FI_R2_PRIVATE_BUCKET,
      FI_R2_BACKUP_BUCKET: this.env.FI_R2_BACKUP_BUCKET,
      FI_LISTENER_HMAC_SECRET: this.env.FI_LISTENER_HMAC_SECRET,
      FI_LISTENER_HMAC_SECRET_VERSION: this.env.FI_LISTENER_HMAC_SECRET_VERSION,
    };
  }

  private async ensureContainer(): Promise<void> {
    if (this.containerReady) return;
    try {
      const containerEnv = buildContainerEnv(this.containerEnvSource());
      // Exec-based batch work must outlive the short default inactivity window.
      // Tar-map slices are ~1h; keep the instance alive for the full exec.
      // Completed jobs release their instance in monitorJob's finally block.
      await this.ctx.container!.setInactivityTimeout(3 * 60 * 60 * 1000);
      await this.ctx.container!.start({
        env: containerEnv,
        enableInternet: true, // R2 S3 API needs outbound
      });
      this.containerReady = true;
    } catch (e) {
      console.error("Batch container start failed:", e);
      throw new Error(BATCH_ERROR_CODES.CONTAINER_START_FAILED);
    }
  }

  /**
   * Admin-only: force the per-DO container instance to restart so a newly
   * deployed image (new Worker version) replaces a still-running old-image
   * instance. Cloudflare containers keep the original image until the
   * instance is destroyed and recreated, so a deploy alone does not pick up
   * code changes for a long-lived idle container.
   */
  async restartContainer(reason = "admin"): Promise<Record<string, unknown>> {
    const hadContainer = this.containerReady;
    this.containerReady = false;
    try {
      if (this.ctx.container) {
        await this.ctx.container.destroy();
      }
    } catch (e) {
      console.error("Batch container destroy failed (continuing):", e);
    }
    // Recreate lazily on the next startJob (ensureContainer).
    return {
      restarted: true,
      had_container: hadContainer,
      reason,
      container_ready: this.containerReady,
    };
  }

  /**
   * P0-2: RPC — start a batch job and return IMMEDIATELY (RUNNING).
   *
   * The container process is launched and monitored in the background.
   * output() is NEVER awaited on the request path. Completion is observed
   * through durable R2 status / manifests.
   */
  async startJob(spec: BatchJobSpec): Promise<BatchJobResult> {
    const start = Date.now();
    this.currentJob = spec;

    const result: BatchJobResult = {
      job_id: spec.job_id,
      job_type: spec.job_type,
      status: "RUNNING",
      started_at: new Date().toISOString(),
      completed_at: "",
      duration_ms: 0,
      exit_code: -1,
    };

    // Persist RUNNING durable status BEFORE launching — a trigger response
    // is only meaningful if the logical job state already exists in R2.
    await this.persistDurableStatus(result);

    try {
      await this.ensureContainer();

      // FI_BATCH_JOB carries ONLY the sanitized logical spec (P0-3).
      // Secrets live in the container env (built from this.env), not here.
      const jobEnv = {
        ...buildContainerEnv(this.containerEnvSource()),
        FI_BATCH_JOB: JSON.stringify(spec),
      };

      const execResult = await this.ctx.container!.exec(
        ["python", "/app/batch_entrypoint.py"],
        {
          cwd: "/app",
          env: jobEnv,
          stdout: "pipe",
          stderr: "pipe",
        }
      );

      // Keep the Durable Object alive for the full exec. A bare `void`
      // lets the DO idle-evict after the 202 returns, which destroys the
      // container mid-job on Cloudflare's side.
      this.ctx.waitUntil(this.monitorJob(spec, execResult, start, result));
      // Heartbeat alarm so long maps (~1h) are not treated as idle even if
      // the waitUntil edge case trips.
      try {
        await this.ctx.storage.setAlarm(Date.now() + 30_000);
      } catch (e) {
        console.error("batch alarm schedule failed:", e);
      }
    } catch (e: unknown) {
      result.status = "FAILED";
      result.last_safe_error_code = mapErrorToCode(e, BATCH_ERROR_CODES.CONTAINER_START_FAILED);
      result.completed_at = new Date().toISOString();
      result.duration_ms = Date.now() - start;
      await this.persistDurableStatus(result).catch(() => {});
      this.lastResult = result;
      this.currentJob = null;
    }

    return result;
  }

  /**
   * P0-2: Background monitor — awaits container output, updates durable
   * status. If the DO is evicted mid-job, the container process continues
   * and /batch/status reconstructs truth from R2.
   */
  private async monitorJob(
    spec: BatchJobSpec,
    execResult: unknown,
    start: number,
    result: BatchJobResult,
  ): Promise<void> {
    let observedExit = false;
    try {
      const output = await (execResult as {
        output(): Promise<{ stdout: Uint8Array; stderr: Uint8Array; exitCode: number }>;
      }).output();
      observedExit = true;
      const decoder = new TextDecoder();
      const stdout = decoder.decode(output.stdout);
      const stderr = decoder.decode(output.stderr);

      result._stdout = stdout; // internal only, never exposed via API
      result._stderr = stderr; // internal only, never exposed via API
      result.exit_code = output.exitCode;
      result.completed_at = new Date().toISOString();
      result.duration_ms = Date.now() - start;

      // The entrypoint prints a JSON summary on the last stdout line.
      // Extract only safe structured fields, never raw text.
      const lines = stdout.trim().split("\n").filter((l) => l.startsWith("{"));
      if (lines.length > 0) {
        try {
          const summary = JSON.parse(lines[lines.length - 1]);
          result.manifest_key = summary.manifest_key;
          result.source_generation = summary.source_generation;
          result.code_commit = summary.code_commit;
          result.completed_batches = summary.completed_batches;
          result.total_batches = summary.total_batches;
          result.bytes_read = summary.bytes_read ?? summary.r2_read_bytes;
          result.bytes_written = summary.bytes_written ?? summary.r2_write_bytes;
          result.rows_read = summary.rows_read;
          result.rows_written = summary.rows_written;
          result.smoke_checks = summary.smoke_checks;
          result.publication_state = summary.publication_state;
          const summaryStatus = summary.status;
          result.status = output.exitCode !== 0 ? "FAILED" : (summaryStatus === "FAILED" ? "FAILED" : (summaryStatus || "COMPLETED"));
          const rawError = summary.error_code ?? summary.error;
          if (result.status === "FAILED") {
            result.last_safe_error_code = mapErrorToCode(rawError);
          } else {
            delete result.last_safe_error_code;
          }
        } catch {
          result.status = output.exitCode === 0 ? "COMPLETED" : "FAILED";
        }
      } else {
        result.status = output.exitCode === 0 ? "COMPLETED" : "FAILED";
        if (output.exitCode !== 0) {
          result.last_safe_error_code = BATCH_ERROR_CODES.JOB_EXEC_FAILED;
        }
      }

      // P9: Persist durable status — survives DO/Worker/container restart.
      await this.persistDurableStatus(result);

      if (result.status === "FAILED") {
        // P8 stays intact: never expose raw stdout/stderr via the status API.
        // These internal-only logs make container failures debuggable via
        // `wrangler tail` without weakening the safe-status contract.
        const tailOut = (result._stdout || "").split("\n").filter((l) => l.trim()).slice(-6).join(" | ");
        const tailErr = (result._stderr || "").split("\n").filter((l) => l.trim()).slice(-6).join(" | ");
        console.error(JSON.stringify({
          event: "BATCH_JOB_FAILED",
          job_id: result.job_id,
          job_type: result.job_type,
          exit_code: result.exit_code,
          last_safe_error_code: result.last_safe_error_code,
          stdout_tail: tailOut.slice(0, 1500),
          stderr_tail: tailErr.slice(0, 1500),
        }));
      }
    } catch (e: unknown) {
      // Durable Object reset/eviction while awaiting a long exec must NOT be
      // treated as job failure and must NOT destroy the container — that was
      // killing ~1h ListenBrainz map slices after a few minutes. The container
      // keeps running; /batch/status reconstructs progress from R2 checkpoints.
      const msg = e instanceof Error ? e.message : String(e);
      console.error(JSON.stringify({
        event: "BATCH_MONITOR_INTERRUPTED",
        job_id: spec.job_id,
        job_type: spec.job_type,
        error: msg.slice(0, 500),
        observed_exit: observedExit,
      }));
      if (!observedExit) {
        // Leave logical status as RUNNING in R2; do not overwrite with FAILED.
        return;
      }
      result.status = "FAILED";
      result.last_safe_error_code = mapErrorToCode(e, BATCH_ERROR_CODES.JOB_EXEC_FAILED);
      result.completed_at = new Date().toISOString();
      result.duration_ms = Date.now() - start;
      await this.persistDurableStatus(result).catch(() => {});
    } finally {
      if (observedExit) {
        this.lastResult = result;
        this.currentJob = null;
        this.containerReady = false;
        await this.ctx.container!.destroy().catch(() => {});
      }
      // If the monitor was interrupted before process exit, keep currentJob
      // and containerReady so a subsequent status/restart path can reconnect
      // without immediately spawning a replacement that steals the instance.
    }
  }

  /**
   * Keep long-running batch execs from looking idle to the platform.
   * Reschedules while a job is in memory; no-ops when idle.
   */
  async alarm(): Promise<void> {
    if (this.currentJob) {
      try {
        await this.ctx.storage.setAlarm(Date.now() + 30_000);
      } catch (e) {
        console.error("batch alarm reschedule failed:", e);
      }
    }
  }

  /**
   * P9: Persist durable job status to R2.
   *
   * Status truth comes from R2 manifests/checkpoints, not from DO process
   * memory. Closing the browser or restarting Freebuff/Worker/DO/container
   * must not erase logical job state.
   */
  private async persistDurableStatus(result: BatchJobResult): Promise<void> {
    const statusKey = `control/jobs/_durable_status/${result.job_id}.json`;
    const safeStatus: BatchStatusResponse = {
      job_id: result.job_id,
      job_type: result.job_type,
      code_commit: result.code_commit,
      status: result.status,
      started_at: result.started_at,
      updated_at: result.completed_at || new Date().toISOString(),
      completed_batches: result.completed_batches ?? 0,
      total_batches: result.total_batches ?? 0,
      bytes_read: result.bytes_read ?? 0,
      bytes_written: result.bytes_written ?? 0,
      runtime_seconds: result.duration_ms / 1000,
      manifest_key: result.manifest_key,
      last_safe_error_code: result.status === "FAILED" ? result.last_safe_error_code : undefined,
    };
    try {
      await this.env.PRIVATE_BUCKET.put(
        statusKey,
        JSON.stringify(safeStatus, null, 2),
        { httpMetadata: { contentType: "application/json" } },
      );
    } catch {
      // Best-effort — the R2 manifest written by the job itself is the
      // primary durable record. This is a secondary status pointer.
    }
  }

  /**
   * P9: Reconstruct durable status from R2 when DO memory is empty.
   */
  private async reconstructDurableStatus(jobId: string): Promise<BatchStatusResponse | null> {
    const statusKey = `control/jobs/_durable_status/${jobId}.json`;
    try {
      const obj = await this.env.PRIVATE_BUCKET.get(statusKey);
      if (!obj) return null;
      const status = await obj.json() as BatchStatusResponse;
      const manifestKey = `control/jobs/${status.job_type}/${jobId}/manifest.json`;
      const lakeJobTypes = new Set([
        "artist_factor_tape_build_v1",
        "artist_sentiment_build_v1",
        "terminal_serving_build_v1",
        "listenbrainz_tar_map",
        "listenbrainz_map",
        "listenbrainz_reduce",
        "identity_graph_v2",
        "cloud_smoke",
      ]);
      const bucket = lakeJobTypes.has(status.job_type)
        ? this.env.LAKE_BUCKET : this.env.PRIVATE_BUCKET;
      const manifest = await bucket.get(manifestKey);
      return manifest ? withJobManifest(status, await manifest.json() as Record<string, unknown>) as BatchStatusResponse : status;
    } catch {
      return null;
    }
  }

  /**
   * RPC: Get the current/last job status.
   * P8: Returns only safe structured fields — no stdout/stderr.
   * P9: Falls back to durable R2 status when DO memory is empty.
   */
  async getStatus(jobId?: string): Promise<{
    container_ready: boolean;
    current_job: BatchJobSpec | null;
    status: BatchStatusResponse | null;
    durable_source: "memory" | "r2" | "none";
  }> {
    if (jobId) {
      const durable = await this.reconstructDurableStatus(jobId);
      if (durable) return { container_ready: this.containerReady, current_job: this.currentJob, status: durable, durable_source: "r2" };
    }
    // If we have in-memory state, use it (but strip unsafe fields).
    if (this.lastResult) {
      const r = this.lastResult;
      return {
        container_ready: this.containerReady,
        current_job: this.currentJob,
        status: {
          job_id: r.job_id,
          job_type: r.job_type,
          status: r.status,
          started_at: r.started_at,
          updated_at: r.completed_at || "",
          completed_batches: r.completed_batches ?? 0,
          total_batches: r.total_batches ?? 0,
          bytes_read: r.bytes_read ?? 0,
          bytes_written: r.bytes_written ?? 0,
          runtime_seconds: r.duration_ms / 1000,
          manifest_key: r.manifest_key,
          last_safe_error_code: r.status === "FAILED" ? r.last_safe_error_code : undefined,
        },
        durable_source: "memory",
      };
    }

    // P9: Reconstruct from R2 if DO memory is empty.
    if (jobId) {
      const durable = await this.reconstructDurableStatus(jobId);
      if (durable) {
        return {
          container_ready: this.containerReady,
          current_job: null,
          status: durable,
          durable_source: "r2",
        };
      }
    }

    return {
      container_ready: this.containerReady,
      current_job: this.currentJob,
      status: null,
      durable_source: "none",
    };
  }

  /** RPC: Health check. */
  async health(): Promise<{ ready: boolean; identity?: string }> {
    return {
      ready: this.containerReady,
      identity: this.ctx.id.toString(),
    };
  }
}
