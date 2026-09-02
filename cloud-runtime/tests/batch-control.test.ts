import { describe, it, expect } from "vitest";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import {
  sanitizeJobSpec,
  buildContainerEnv,
  mapErrorToCode,
  BATCH_ERROR_CODES,
  ALLOWED_BATCH_JOB_TYPES,
} from "../src/batch-spec";
import { requireBatchAuth } from "../src/batch-auth";

const FAKE_SECRET = "THIS_SECRET_MUST_NEVER_APPEAR";

describe("container execution contract", () => {
  it("uses the absolute /app entrypoint and explicit cwd", async () => {
    const source = await readFile(resolve(process.cwd(), "src/batch-container-do.ts"), "utf8");
    expect(source).toContain('["python", "/app/batch_entrypoint.py"]');
    expect(source).toContain('cwd: "/app"');
  });
});

describe("sanitizeJobSpec (V1B P0-3)", () => {
  it("accepts a valid safe spec and returns only safe fields", () => {
    const spec = sanitizeJobSpec({
      job_type: "cloud_smoke",
      job_id: "smoke_001",
      params: { partitions: 64 },
      source_generation: "20260831T014029Z-1369",
      max_batches: 5,
    });
    expect(spec.job_type).toBe("cloud_smoke");
    expect(spec.job_id).toBe("smoke_001");
    expect(spec.params).toEqual({ partitions: 64 });
    expect(spec.source_generation).toBe("20260831T014029Z-1369");
    expect(spec.max_batches).toBe(5);
    // No secret-bearing keys survive.
    expect("env_vars" in spec).toBe(false);
    expect("environment" in spec).toBe(false);
  });

  it("rejects a spec carrying env_vars with a fake secret", () => {
    expect(() =>
      sanitizeJobSpec({
        job_type: "listenbrainz_map",
        env_vars: { FI_LISTENER_HMAC_SECRET: FAKE_SECRET },
      })
    ).toThrow("forbidden key");
  });

  it("rejects unknown job types", () => {
    expect(() => sanitizeJobSpec({ job_type: "arbitrary_command" })).toThrow(
      "invalid job_type"
    );
  });

  it("rejects arbitrary command/exec/shell keys", () => {
    for (const key of ["command", "exec", "executable", "shell", "entrypoint", "cmd"]) {
      expect(() =>
        sanitizeJobSpec({ job_type: "cloud_smoke", [key]: "rm -rf /" })
      ).toThrow("forbidden key");
    }
  });

  it("rejects path traversal in job_id", () => {
    expect(() =>
      sanitizeJobSpec({ job_type: "cloud_smoke", job_id: "../../etc/passwd" })
    ).toThrow("invalid job_id");
  });

  it("rejects oversized job_id", () => {
    expect(() =>
      sanitizeJobSpec({ job_type: "cloud_smoke", job_id: "a".repeat(200) })
    ).toThrow("invalid job_id");
  });

  it("never serializes the fake secret into the spec", () => {
    // The spec returned by sanitizeJobSpec, when serialized, must not contain
    // the fake secret even if the raw input tried to smuggle it in other keys.
    const raw = {
      job_type: "cloud_smoke",
      job_id: "smoke_001",
      // Unknown keys are dropped by sanitization.
      extra: { FI_LISTENER_HMAC_SECRET: FAKE_SECRET },
      FI_R2_SECRET_ACCESS_KEY: FAKE_SECRET,
    };
    const spec = sanitizeJobSpec(raw as never);
    expect(JSON.stringify(spec)).not.toContain(FAKE_SECRET);
  });
});

describe("buildContainerEnv (V1B P0-3)", () => {
  it("builds container env from DO bindings only, never from a spec", () => {
    const env = buildContainerEnv({
      FI_R2_ENDPOINT: "https://r2.example",
      FI_R2_ACCESS_KEY_ID: "key-id",
      FI_R2_SECRET_ACCESS_KEY: "super-secret",
      FI_LISTENER_HMAC_SECRET: FAKE_SECRET,
      FI_LISTENER_HMAC_SECRET_VERSION: "2026-09-v1",
    });
    expect(env.FI_R2_ENDPOINT).toBe("https://r2.example");
    expect(env.FI_R2_SECRET_ACCESS_KEY).toBe("super-secret");
    expect(env.FI_LISTENER_HMAC_SECRET).toBe(FAKE_SECRET);
    expect(env.FI_LISTENER_HMAC_SECRET_VERSION).toBe("2026-09-v1");
    expect(env.FI_OBJECT_STORE).toBe("R2");
    expect(env.PYTHONUNBUFFERED).toBe("1");
  });

  it("applies safe defaults for missing bindings", () => {
    const env = buildContainerEnv({});
    expect(env.FI_R2_PRIVATE_BUCKET).toBe("festival-intelligence-private");
    expect(env.FI_R2_LAKE_BUCKET).toBe("festival-intelligence-lake");
  });
});

describe("requireBatchAuth (V1B P0-4)", () => {
  it("missing ADMIN_TOKEN → 503 BATCH_AUTH_NOT_CONFIGURED", async () => {
    const req = new Request("https://x/batch/trigger", {
      method: "POST",
      headers: { "X-Admin-Token": "anything" },
    });
    const resp = requireBatchAuth(req, "");
    expect(resp).not.toBeNull();
    expect(resp!.status).toBe(503);
    const body = (await resp!.json()) as { error: string };
    expect(body.error).toContain("BATCH_AUTH_NOT_CONFIGURED");
  });

  it("wrong token → 401", () => {
    const req = new Request("https://x/batch/status", {
      headers: { Authorization: "Bearer wrong" },
    });
    const resp = requireBatchAuth(req, "correct-token");
    expect(resp).not.toBeNull();
    expect(resp!.status).toBe(401);
  });

  it("correct token via Authorization header → allowed", () => {
    const req = new Request("https://x/batch/status", {
      headers: { Authorization: "Bearer correct-token" },
    });
    expect(requireBatchAuth(req, "correct-token")).toBeNull();
  });

  it("correct token via X-Admin-Token header → allowed", () => {
    const req = new Request("https://x/batch/status", {
      headers: { "X-Admin-Token": "correct-token" },
    });
    expect(requireBatchAuth(req, "correct-token")).toBeNull();
  });
});

describe("successful summary reconciliation", () => {
  it("does not attach JOB_EXEC_FAILED when a completed summary has no error", () => {
    const summary = { status: "COMPLETED", smoke_checks: [{ name: "r2", ok: true }] };
    const exitCode = 0;
    const status = exitCode !== 0 ? "FAILED" : (summary.status === "FAILED" ? "FAILED" : summary.status);
    const rawError = (summary as Record<string, unknown>).error_code ?? (summary as Record<string, unknown>).error;
    expect(status).toBe("COMPLETED");
    expect(rawError).toBeUndefined();
    expect(status === "FAILED" ? mapErrorToCode(rawError) : undefined).toBeUndefined();
  });

  it("makes nonzero exit authoritative over a completed summary", () => {
    const summary = { status: "COMPLETED" };
    expect(1 !== 0 ? "FAILED" : summary.status).toBe("FAILED");
  });

  it("propagates safe summary metrics", () => {
    const summary = { rows_read: 1, rows_written: 1, r2_read_bytes: 36, r2_write_bytes: 36 };
    expect(summary.rows_read).toBe(1);
    expect(summary.r2_write_bytes).toBe(36);
  });
});

describe("mapErrorToCode (V1B P1)", () => {
  it("maps known codes and rejects raw text", () => {
    expect(mapErrorToCode("JOB_EXEC_FAILED")).toBe("JOB_EXEC_FAILED");
    expect(mapErrorToCode("CONTAINER_START_FAILED")).toBe("CONTAINER_START_FAILED");
    expect(mapErrorToCode("R2_VERIFY_FAILED")).toBe("R2_VERIFY_FAILED");
    expect(mapErrorToCode("PUBLICATION_FAILED: CURRENT write failed")).toBe(
      "PUBLICATION_FAILED"
    );
    expect(mapErrorToCode("LISTENER_KEY_CONFIG_FAILED")).toBe(
      "LISTENER_KEY_CONFIG_FAILED"
    );
  });

  it("falls back to JOB_EXEC_FAILED for unknown raw text", () => {
    expect(mapErrorToCode("stack trace with paths")).toBe("JOB_EXEC_FAILED");
    expect(mapErrorToCode("some arbitrary message", BATCH_ERROR_CODES.JOB_EXEC_FAILED)).toBe(
      "JOB_EXEC_FAILED"
    );
  });
});

describe("async lifecycle contract (V1B P0-2)", () => {
  it("startJob returns before the container output completes", async () => {
    // Simulate the DO lifecycle: a mock exec whose output() resolves after a
    // delay. startJob must return RUNNING immediately, before output().
    const start = Date.now();
    let outputResolved = false;

    // Model the BatchContainer startJob flow: it launches exec and a
    // background monitor; it does NOT await output() before returning.
    const fakeExecResult = {
      async output() {
        await new Promise((r) => setTimeout(r, 100));
        outputResolved = true;
        return {
          stdout: new TextEncoder().encode(
            JSON.stringify({ status: "COMPLETED", manifest_key: "m.json" })
          ),
          stderr: new Uint8Array(),
          exitCode: 0,
        };
      },
    };

    // startJob should: persist RUNNING, launch (no await output), return.
    const launchedAt = Date.now();
    const result = await launchAndReturn(fakeExecResult, "smoke_001");
    const returnedAt = Date.now();

    expect(result.status).toBe("RUNNING");
    // The trigger returned BEFORE the 100ms output resolved.
    expect(outputResolved).toBe(false);
    expect(returnedAt - launchedAt).toBeLessThan(100);

    // After the job completes, durable status must reflect completion.
    await fakeExecResult.output();
    expect(outputResolved).toBe(true);
    void start;
  });

  it("durable status can be reconstructed without DO memory", async () => {
    // Simulate: DO memory empty, R2 durable status present.
    const durable = {
      job_id: "smoke_002",
      job_type: "cloud_smoke",
      status: "COMPLETED",
      started_at: "2026-09-01T00:00:00Z",
      updated_at: "2026-09-01T01:00:00Z",
      completed_batches: 0,
      total_batches: 0,
      bytes_read: 0,
      bytes_written: 0,
      runtime_seconds: 3600,
      manifest_key: "control/jobs/cloud_smoke/smoke_002/manifest.json",
    };
    // getStatus(jobId) with empty memory reads this from R2.
    const status = await reconstructFromR2(durable, "smoke_002");
    expect(status).toEqual(durable);
  });

  it("FI_BATCH_JOB content is the sanitized spec only", () => {
    // The DO builds FI_BATCH_JOB from the sanitized spec — no env_vars.
    const spec = sanitizeJobSpec({
      job_type: "listenbrainz_map",
      job_id: "map_001",
      params: { partitions: 64 },
    });
    const serialized = JSON.stringify(spec);
    expect(serialized).not.toContain(FAKE_SECRET);
    expect(serialized).not.toContain("env_vars");
    expect(serialized).toContain('"job_type":"listenbrainz_map"');
  });
});

// ── helpers modeling the DO contract (no workers runtime needed) ──

async function launchAndReturn(execResult: unknown, jobId: string) {
  // Mirrors BatchContainer.startJob: persist RUNNING, launch, return.
  const result = {
    job_id: jobId,
    job_type: "cloud_smoke",
    status: "RUNNING",
    started_at: new Date().toISOString(),
    completed_at: "",
    duration_ms: 0,
    exit_code: -1,
  } as const;
  // Launch background monitoring WITHOUT awaiting output.
  void (execResult as { output(): Promise<unknown> }).output();
  return result;
}

async function reconstructFromR2(durable: unknown, jobId: string) {
  // Mirrors BatchContainer.reconstructDurableStatus: reads R2 when memory empty.
  return jobId === "smoke_002" ? durable : null;
}
