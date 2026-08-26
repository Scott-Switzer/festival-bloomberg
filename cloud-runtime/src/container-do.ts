/**
 * Acquisition Container — Durable Object managing a reusable Container instance.
 * P0.4 fix: Uses correct DurableObject base + ctx.container.exec().
 */

import { DurableObject } from "cloudflare:workers";
import { AcquisitionTask, TaskResult } from "./task-contract";

// eslint-disable-next-line @typescript-eslint/no-empty-interface
interface ContainerEnv {}

export class AcquisitionContainer extends DurableObject<ContainerEnv> {
  private containerReady = false;

  constructor(state: DurableObjectState, env: ContainerEnv) {
    super(state, env);
  }

  private async ensureContainer(): Promise<void> {
    if (this.containerReady) return;
    try {
      await this.ctx.container!.start();
      this.containerReady = true;
    } catch (e) {
      console.error("Container start failed:", e);
      throw e;
    }
  }

  /**
   * RPC method: Execute an acquisition task in the container.
   */
  async runTask(params: {
    task: AcquisitionTask;
    env_vars: Record<string, string>;
    timeout_seconds?: number;
  }): Promise<TaskResult> {
    const { task, env_vars, timeout_seconds = 120 } = params;
    const start = Date.now();

    const result: TaskResult = {
      task_key: task.task_key,
      status: "COMPLETED",
      started_at: new Date().toISOString(),
      completed_at: "",
      duration_ms: 0,
      http_success: false,
      observations_written: 0,
      snapshots_appended: 0,
      actual_cost_usd: 0,
      duplicate_detected: false,
    };

    try {
      await this.ensureContainer();

      // exec() runs a command in the running container
      const execResult = await this.ctx.container!.exec(
        ["python", "cloud_entrypoint.py"],
        {
          env: {
            ...env_vars,
            ACQUISITION_TASK: JSON.stringify(task),
          },
        }
      );

      // Read stdout — exec returns a ReadableStream
      const reader = execResult.stdout?.getReader();
      if (reader) {
        const chunks: Uint8Array[] = [];
        let done = false;
        while (!done) {
          const { value, done: streamDone } = await reader.read();
          if (value) chunks.push(value);
          done = streamDone;
        }
        const decoder = new TextDecoder();
        const stdout = decoder.decode(new Uint8Array(
          chunks.reduce((acc, c) => acc + c.length, 0)
        ));
        // Re-read properly
        const fullText = chunks.map(c => decoder.decode(c)).join("");
        const lines = fullText.trim().split("\n").filter((l: string) => l.startsWith("{"));
        if (lines.length > 0) {
          const parsed = JSON.parse(lines[lines.length - 1]);
          Object.assign(result, parsed);
        }
      }

      result.http_success = result.status === "COMPLETED";
      result.completed_at = new Date().toISOString();
      result.duration_ms = Date.now() - start;

    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      result.status = "FAILED";
      result.error_category = "TIMEOUT";
      result.error_detail = msg;
      result.completed_at = new Date().toISOString();
      result.duration_ms = Date.now() - start;
    }

    return result;
  }

  /** RPC method: Get container health status. */
  async health(): Promise<{ ready: boolean; identity?: string }> {
    return {
      ready: this.containerReady,
      identity: this.ctx.id.toString(),
    };
  }
}
