/**
 * Batch Container — Durable Object managing a reusable Container instance
 * for heavy data-processing jobs (Identity Graph V2, ListenBrainz map/reduce).
 *
 * Unlike AcquisitionContainer (short tasks), this DO runs long multi-hour
 * Python/DuckDB jobs via exec(). The contract is:
 *
 *   1. Job spec arrives via runJob() RPC.
 *   2. DO starts container if needed (standard-4: 4 vCPU / 12 GiB / 20 GB).
 *   3. DO exec()s batch_entrypoint.py with the job spec as env.
 *   4. The entrypoint reads source from R2, processes with bounded scratch,
 *      writes partials + checkpoint to R2, then exits.
 *   5. On restart, the entrypoint reads the R2 checkpoint, skips completed
 *      batches, and resumes. The DO can re-exec() safely.
 *
 * Ephemeral container disk is NOT persistent. All canonical state is in R2.
 */

import { DurableObject } from "cloudflare:workers";

export interface BatchJobSpec {
  job_id: string;
  job_type: string; // "identity_graph_v2" | "listenbrainz_map" | "listenbrainz_reduce" | ...
  params: Record<string, unknown>;
  source_generation?: string;
  max_batches?: number;
  env_vars: Record<string, string>;
}

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

interface BatchEnv {
  PRIVATE_BUCKET: R2Bucket;
  LAKE_BUCKET: R2Bucket;
}


export class BatchContainer extends DurableObject<BatchEnv> {
  private containerReady = false;
  private currentJob: BatchJobSpec | null = null;
  private lastResult: BatchJobResult | null = null;

  constructor(state: DurableObjectState, env: BatchEnv) {
    super(state, env);
  }

  private async ensureContainer(envVars?: Record<string, string>): Promise<void> {
    if (this.containerReady) return;
    try {
      await this.ctx.container!.start({
        env: envVars,
        enableInternet: true, // R2 S3 API needs outbound
      });
      this.containerReady = true;
    } catch (e) {
      console.error("Batch container start failed:", e);
      throw e;
    }
  }

  /**
   * RPC: Execute a batch job in the container.
   * The entrypoint reads checkpoints from R2 on startup, so re-exec()ing
   * after a crash resumes without duplicating work.
   */
  async runJob(spec: BatchJobSpec): Promise<BatchJobResult> {
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

    try {
      await this.ensureContainer(spec.env_vars);

      // Pass the job spec as a JSON env var. The entrypoint parses it.
      const jobEnv = {
        ...spec.env_vars,
        FI_BATCH_JOB: JSON.stringify(spec),
        FI_SCRATCH_DIR: "/tmp/festival-bloomberg",
        PYTHONUNBUFFERED: "1",
      };

      const execResult = await this.ctx.container!.exec(
        ["python", "batch_entrypoint.py"],
        {
          env: jobEnv,
          stdout: "pipe",
          stderr: "pipe",
        }
      );

      // P8: Buffer output for internal logging only.
      // Jobs produce a final JSON summary on the last line.
      // stdout/stderr are NEVER returned in /batch/status responses.
      const output = await execResult.output();
      const decoder = new TextDecoder();
      const stdout = decoder.decode(output.stdout);
      const stderr = decoder.decode(output.stderr);

      result._stdout = stdout; // internal only, not exposed via API
      result._stderr = stderr; // internal only, not exposed via API
      result.exit_code = output.exitCode;
      result.completed_at = new Date().toISOString();
      result.duration_ms = Date.now() - start;

      // The entrypoint prints a JSON summary on the last stdout line.
      // We only extract safe structured fields, never raw text.
      const lines = stdout.trim().split("\n").filter((l) => l.startsWith("{"));
      if (lines.length > 0) {
        try {
          const summary = JSON.parse(lines[lines.length - 1]);
          result.manifest_key = summary.manifest_key;
          result.status = summary.status || (output.exitCode === 0 ? "COMPLETED" : "FAILED");
          // P8: Only safe error code, no raw stack traces or full error text.
          if (summary.error) {
            result.last_safe_error_code =
              typeof summary.error === "string"
                ? summary.error.slice(0, 120)
                : "JOB_ERROR";
          }
        } catch {
          result.status = output.exitCode === 0 ? "COMPLETED" : "FAILED";
        }
      } else {
        result.status = output.exitCode === 0 ? "COMPLETED" : "FAILED";
        if (output.exitCode !== 0) {
          result.last_safe_error_code = `EXIT_${output.exitCode}`;
        }
      }

      // P9: Persist durable status to R2 (survives DO/Worker/container restart).
      await this.persistDurableStatus(result);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      result.status = "FAILED";
      result.last_safe_error_code = msg.slice(0, 120);
      result.completed_at = new Date().toISOString();
      result.duration_ms = Date.now() - start;
      // P9: Even failure state is persisted durably.
      await this.persistDurableStatus(result).catch(() => {});
    } finally {
      this.currentJob = null;
      this.lastResult = result;
    }

    return result;
  }

  /**
   * P9: Persist durable job status to R2.
   *
   * Status truth comes from R2 manifests/checkpoints, not from DO process
   * memory. Closing the browser or restarting Freebuff/Worker/DO/container
   * must not erase logical job state.
   */
  private async persistDurableStatus(result: BatchJobResult): Promise<void> {
    const env = this.env as BatchEnv;
    const statusKey = `control/jobs/_durable_status/${result.job_id}.json`;
    const safeStatus: BatchStatusResponse = {
      job_id: result.job_id,
      job_type: result.job_type,
      status: result.status,
      started_at: result.started_at,
      updated_at: result.completed_at || new Date().toISOString(),
      completed_batches: 0,
      total_batches: 0,
      bytes_read: 0,
      bytes_written: 0,
      runtime_seconds: result.duration_ms / 1000,
      manifest_key: result.manifest_key,
      last_safe_error_code: result.last_safe_error_code,
    };
    try {
      await env.PRIVATE_BUCKET.put(
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
   *
   * Called when getStatus() finds no in-memory lastResult (e.g. after
   * DO restart).
   */
  private async reconstructDurableStatus(jobId: string): Promise<BatchStatusResponse | null> {
    const env = this.env as BatchEnv;
    const statusKey = `control/jobs/_durable_status/${jobId}.json`;
    try {
      const obj = await env.PRIVATE_BUCKET.get(statusKey);
      if (!obj) return null;
      return await obj.json() as BatchStatusResponse;
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
          completed_batches: 0,
          total_batches: 0,
          bytes_read: 0,
          bytes_written: 0,
          runtime_seconds: r.duration_ms / 1000,
          manifest_key: r.manifest_key,
          last_safe_error_code: r.last_safe_error_code,
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
