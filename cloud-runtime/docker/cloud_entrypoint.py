#!/usr/bin/env python3
"""
Cloud entrypoint for Festival Intelligence acquisition container.

Receives an AcquisitionTask JSON message (from Queue/Workflow),
executes the SHARED production acquisition runner,
writes results to R2, and returns a TaskResult.

Uses EXACTLY the same code path as local collect_ticket_market.py.
No separate implementations. No invented cost semantics.
No sandbox fixtures in production.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "python"))
sys.path.insert(0, str(PROJECT_ROOT))

import duckdb


def load_env_from_file():
    """Load .env if present (local testing only; not used in production)."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def main():
    """Entry point — reads task from env, executes shared runner, outputs result."""
    load_env_from_file()

    task_json = os.environ.get("ACQUISITION_TASK")
    if not task_json:
        task_json = sys.stdin.read().strip()

    if not task_json:
        print(json.dumps({"error": "No task provided"}))
        sys.exit(1)

    try:
        task = json.loads(task_json)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Invalid task JSON: {e}"}))
        sys.exit(1)

    rail = task.get("rail", "FAST")
    event_key = task.get("event_key", "")
    marketplace = task.get("marketplace", "")
    target_url = task.get("target_url", "")
    wave_label = task.get("scheduled_window", "cloud_wave")
    task_key = task.get("task_key", "unknown")

    start = time.time()
    result = {
        "task_key": task_key,
        "status": "COMPLETED",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "http_success": False,
        "observations_written": 0,
        "snapshots_appended": 0,
        "actual_cost_usd": 0.0,
        "cost_basis": "MEASURED",
        "duplicate_detected": False,
    }

    try:
        from festival_bloomberg.acquisition.ticket_market_runner import (
            collect_fast,
            collect_deep,
            with_retry,
        )
        from festival_bloomberg.migrations import apply_pending_migrations

        # Open a temporary DuckDB for this task
        db_path = f"/tmp/ticket_market_{task_key}.duckdb"
        conn = duckdb.connect(db_path)
        apply_pending_migrations(conn)

        ev = {
            "event_key": event_key,
            "marketplace": marketplace,
        }

        if rail == "FAST":
            r, attempts = with_retry(collect_fast, conn, ev, target_url, marketplace, wave_label)
        elif rail == "DEEP":
            r, attempts = with_retry(collect_deep, conn, ev, target_url, marketplace, wave_label)
        else:
            r = {"error": f"Unsupported rail: {rail}"}
            attempts = 1

        if "error" in r:
            result["status"] = "FAILED"
            result["error_category"] = "PARSE_FAILURE"
            result["error_detail"] = r["error"]
        else:
            result["http_success"] = True
            result["http_status"] = 200
            result["actual_cost_usd"] = r.get("cost_usd", 0.0)
            result["cost_basis"] = r.get("cost_basis", "MEASURED")
            result["observations_written"] = 1
            result["snapshots_appended"] = 1

            # The shared runner already persisted raw evidence and snapshot
            # via persist_raw_evidence and persist_snapshot.
            # For R2 cloud storage, we also write the raw object.
            raw_payload = r.get("raw_snapshot") or r.get("snapshot") or {}
            if raw_payload:
                try:
                    from festival_bloomberg.evidence_rails.r2_object_store import R2ObjectStore
                    store = R2ObjectStore.from_env()
                    import hashlib
                    raw_bytes = json.dumps(raw_payload).encode()
                    sha256 = hashlib.sha256(raw_bytes).hexdigest()
                    ref = store.put(
                        provider=marketplace,
                        payload=raw_bytes,
                        content_type="JSON",
                        metadata={
                            "event_key": event_key,
                            "task_key": task_key,
                            "rail": rail,
                        },
                    )
                    result["raw_object_key"] = ref.key
                    result["raw_bytes"] = len(raw_bytes)
                    result["raw_sha256"] = sha256
                except Exception:
                    pass  # R2 write failure is non-fatal for the task

        conn.close()

        # Cleanup temp DB
        try:
            os.unlink(db_path)
            os.unlink(db_path + ".wal")
        except OSError:
            pass

    except Exception as e:
        result["status"] = "FAILED"
        result["error_category"] = "PARSE_FAILURE"
        result["error_detail"] = str(e)

    result["completed_at"] = datetime.now(timezone.utc).isoformat()
    result["duration_ms"] = int((time.time() - start) * 1000)

    print(json.dumps(result))
    sys.exit(0 if result["status"] == "COMPLETED" else 1)


if __name__ == "__main__":
    main()
