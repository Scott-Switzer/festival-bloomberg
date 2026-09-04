#!/usr/bin/env python3
"""Atomic local test for the dedicated read-only terminal runtime.

The test owns the complete lifecycle in one invocation:

  local bootstrap HTTP server → terminal entrypoint subprocess → health/status
  and product API assertions → child shutdown → bootstrap shutdown.

It never uses a fixed port, a persistent server, the acquisition Worker, or
an external R2 credential. The bootstrap serves the exact local serving
artifact selected by ``--serving-db`` and its matching CURRENT.json, which
makes SHA verification and generation pinning part of the test.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "serving" / "artist_security_terminal_v1" / "terminal.duckdb"
DEFAULT_CURRENT = ROOT / "serving" / "artist_security_terminal_v1" / "CURRENT.json"
ENTRYPOINT = ROOT / "terminal-runtime" / "docker" / "terminal_entrypoint.py"


class _BootstrapHandler(BaseHTTPRequestHandler):
    current: dict[str, Any] = {}
    db_path: Path

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/_bootstrap":
            self.send_error(404)
            return
        artifact = urllib.parse.parse_qs(parsed.query).get("artifact", ["metadata"])[0]
        if artifact == "metadata":
            body = json.dumps(self.current, sort_keys=True).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if artifact == "db":
            size = self.db_path.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with self.db_path.open("rb") as source:
                while chunk := source.read(1 << 20):
                    self.wfile.write(chunk)
            return
        self.send_error(400, "unknown artifact")

    def log_message(self, *_args: Any) -> None:
        pass


def _get_json(url: str, timeout: float = 10.0) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        assert response.status == 200, f"{url} returned HTTP {response.status}"
        return json.loads(response.read().decode("utf-8"))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _find_artist(db_path: Path) -> tuple[str, str]:
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        row = conn.execute(
            """
            SELECT a.artist_key, a.name
            FROM artists a
            WHERE EXISTS (
                SELECT 1 FROM artist_factor_observations f
                WHERE f.artist_key = a.artist_key
            )
              AND EXISTS (
                SELECT 1 FROM artist_sentiment_observations s
                WHERE s.artist_key = a.artist_key
            )
            ORDER BY a.name, a.artist_key
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            raise AssertionError("serving artifact has no artist with factor + sentiment rows")
        return str(row[0]), str(row[1])
    finally:
        conn.close()


def _assert_runtime(base_url: str, current: dict[str, Any], db_path: Path) -> None:
    health = _get_json(f"{base_url}/health")
    assert health["status"] == "ok", health
    assert health["generation"] == current["generation"], health
    assert health["sha256"] == current["sha256"], health

    status = _get_json(f"{base_url}/api/status")
    assert status["generation"] == current["generation"], status
    assert status["sha256"] == current["sha256"], status

    artist_key, artist_name = _find_artist(db_path)
    search = _get_json(
        f"{base_url}/api/search?q={urllib.parse.quote(artist_name)}&limit=5"
    )
    assert any(item.get("entity_id") == artist_key for item in search), search

    security = _get_json(
        f"{base_url}/api/artist-security/{urllib.parse.quote(artist_key, safe='')}"
    )
    assert security["artist"]["artist_key"] == artist_key, security
    assert security["factor_tape"]["status"] == "OBSERVED", security
    assert security["sentiment"]["status"] == "OBSERVED", security

    # The dedicated runtime must not inherit acquisition/admin endpoints.
    try:
        urllib.request.urlopen(f"{base_url}/batch/trigger")
    except urllib.error.HTTPError as exc:
        assert exc.code == 404, exc.code
    else:
        raise AssertionError("dedicated terminal exposed an acquisition route")

    print(json.dumps({
        "status": "PASS",
        "generation": current["generation"],
        "sha256": current["sha256"],
        "artist_key": artist_key,
        "artist_name": artist_name,
        "checks": ["health", "status", "search", "artist_security", "no_admin_route"],
    }, sort_keys=True))


def run(db_path: Path, current_path: Path, timeout_seconds: float) -> int:
    if not db_path.is_file():
        raise SystemExit(f"serving DB not found: {db_path}")
    if not current_path.is_file():
        raise SystemExit(f"CURRENT.json not found: {current_path}")
    current = json.loads(current_path.read_text(encoding="utf-8"))
    expected_sha = str(current.get("sha256") or "")
    actual_sha = hashlib.sha256(db_path.read_bytes()).hexdigest()
    if not expected_sha or actual_sha != expected_sha:
        raise SystemExit(
            f"local serving artifact SHA mismatch: expected={expected_sha} actual={actual_sha}"
        )

    handler = type(
        "BoundBootstrapHandler",
        (_BootstrapHandler,),
        {"current": current, "db_path": db_path},
    )
    bootstrap = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    bootstrap_thread = threading.Thread(target=bootstrap.serve_forever, daemon=True)
    bootstrap_thread.start()

    process: subprocess.Popen[str] | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="festival-terminal-runtime-") as scratch:
            product_port = _free_port()
            env = os.environ.copy()
            env.update({
                "BOOTSTRAP_BASE": f"http://127.0.0.1:{bootstrap.server_port}",
                "PRODUCT_SCRATCH_DIR": scratch,
                "PRODUCT_SERVING_PORT": str(product_port),
                "PRODUCT_PYTHONPATH": str(ROOT / "python"),
                "PYTHONPATH": str(ROOT / "python"),
            })
            process = subprocess.Popen(
                [sys.executable, str(ENTRYPOINT)],
                cwd=str(ROOT),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            deadline = time.monotonic() + timeout_seconds
            base_url = f"http://127.0.0.1:{product_port}"
            last_error = "not started"
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    output = process.stdout.read() if process.stdout else ""
                    raise AssertionError(
                        f"terminal entrypoint exited {process.returncode}:\n{output}"
                    )
                try:
                    _assert_runtime(base_url, current, db_path)
                    return 0
                except (AssertionError, urllib.error.URLError, ConnectionError) as exc:
                    last_error = str(exc)
                    time.sleep(0.25)
            process.terminate()
            try:
                output, _ = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                output, _ = process.communicate(timeout=10)
            raise AssertionError(
                f"terminal runtime did not become ready within {timeout_seconds}s: "
                f"{last_error}\n{output}"
            )
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        bootstrap.shutdown()
        bootstrap.server_close()
        bootstrap_thread.join(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serving-db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--current-json", type=Path, default=DEFAULT_CURRENT)
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()
    return run(args.serving_db, args.current_json, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
