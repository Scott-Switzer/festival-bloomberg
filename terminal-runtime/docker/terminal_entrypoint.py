#!/usr/bin/env python
"""Festival Bloomberg — terminal container entrypoint.

Cold-start contract:
  1. read CURRENT.json metadata from the terminal Worker (_bootstrap)
  2. stream terminal.duckdb from the Worker (_bootstrap?artifact=db)
  3. SHA-256 verify against the CURRENT metadata
  4. open the serving DB READ_ONLY
  5. serve the product API + /health on 0.0.0.0:{PRODUCT_SERVING_PORT}

The Worker's _bootstrap route serves ONLY the public read-only serving
artifact, so no credentials exist in this container by design.
"""

import hashlib
import json
import os
import sys
import time
import urllib.request
from pathlib import Path


class _BrowserUserAgentRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep a browser-like User-Agent across Cloudflare redirects."""


_HTTP_OPENER = urllib.request.build_opener(_BrowserUserAgentRedirectHandler())
_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept": "application/json, application/octet-stream, */*",
}

BASE = os.environ.get("BOOTSTRAP_BASE", "").rstrip("/")
SCRATCH = Path(os.environ.get("PRODUCT_SCRATCH_DIR", "/tmp/festival-bloomberg-terminal"))
DB_PATH = SCRATCH / "terminal.duckdb"
CURRENT_PATH = SCRATCH / "CURRENT.json"
WORKSPACE_PATH = SCRATCH / "workspace.db"
PORT = int(os.environ.get("PRODUCT_SERVING_PORT", "8080"))


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers=_REQUEST_HEADERS)
    with _HTTP_OPENER.open(request, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def stream_verify(url: str, dest: Path, expected_sha: str | None) -> str:
    h = hashlib.sha256()
    request = urllib.request.Request(url, headers=_REQUEST_HEADERS)
    with _HTTP_OPENER.open(request, timeout=1800) as resp, open(dest, "wb") as fh:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
            fh.write(chunk)
    actual = h.hexdigest()
    if expected_sha and actual != expected_sha:
        raise SystemExit(f"TERMINAL_ARTIFACT_SHA_MISMATCH expected={expected_sha} actual={actual}")
    return actual


def main() -> None:
    started = time.time()
    print(f"terminal entrypoint starting (bootstrap={BASE})", flush=True)
    if not BASE:
        raise SystemExit("BOOTSTRAP_BASE not set — cannot fetch the serving artifact")

    SCRATCH.mkdir(parents=True, exist_ok=True)

    current = fetch_json(f"{BASE}/_bootstrap?artifact=metadata")
    generation = current.get("generation")
    sha256 = current.get("sha256")
    print(f"CURRENT generation={generation} sha256={sha256}", flush=True)
    CURRENT_PATH.write_text(json.dumps(current, indent=2, sort_keys=True, default=str), encoding="utf-8")

    db_url = f"{BASE}/_bootstrap?artifact=db"
    print("streaming serving artifact...", flush=True)
    actual = stream_verify(db_url, DB_PATH, sha256)
    print(f"serving artifact verified: {DB_PATH.stat().st_size} bytes sha256={actual} "
          f"({time.time() - started:.1f}s)", flush=True)

    sys.path.insert(0, "/app/python")
    from festival_bloomberg.terminal import mvp_server

    # workspace DB is an empty per-instance scratch file (ephemeral disk) —
    # no private/customer data is copied into the container.
    os.environ["TERMINAL_STATIC_DIR"] = "/app/public"
    app = mvp_server.make_app(
        serving_db=DB_PATH,
        current_json=CURRENT_PATH,
        workspace_db=str(WORKSPACE_PATH),
    )
    print(f"serving product on 0.0.0.0:{PORT}", flush=True)
    mvp_server.serve(app, PORT, host="0.0.0.0")


if __name__ == "__main__":
    main()
