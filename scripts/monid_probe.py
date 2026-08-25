"""Monid API probe — discover + inspect endpoints via the direct API.

Uses the repo's established direct-API pattern (api.monid.ai/v1/*) with
MONID_API_KEY. Inspection is free; this script never runs paid endpoints
unless --run is explicitly given with --provider/--endpoint.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from festival_bloomberg.localenv import load_local_env

BASE = "https://api.monid.ai"


def _key() -> str:
    load_local_env()
    key = os.environ.get("MONID_API_KEY")
    if not key:
        raise SystemExit("MONID_API_KEY not set")
    return key


def _post(path: str, body: dict) -> tuple[int, dict]:
    key = _key()
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:500]}


def discover(query: str, limit: int = 8) -> list[dict]:
    status, payload = _post("/v1/discover", {"query": query, "limit": limit})
    if status != 200:
        print(f"discover HTTP {status}: {payload}")
        return []
    return payload.get("results") or payload.get("endpoints") or []


def inspect(provider: str, endpoint: str) -> dict:
    status, payload = _post("/v1/inspect", {"provider": provider, "endpoint": endpoint})
    if status != 200:
        return {"status": status, "error": payload.get("message") or payload.get("error", "unknown")}
    return payload


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--discover", default="")
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--inspect-provider", default="")
    ap.add_argument("--inspect-endpoint", default="")
    args = ap.parse_args()

    if args.discover:
        for r in discover(args.discover, args.limit):
            pid = r.get("provider")
            endpoint = r.get("endpoint") or r.get("id")
            price = r.get("price") or {}
            print(
                f"  [{r.get('score', '?'):.3f}] {pid} {endpoint} | "
                f"{r.get('providerName','')[:30]:30s} | "
                f"{price.get('type','')} {price.get('amount',{}).get('value','') if isinstance(price.get('amount'), dict) else price.get('amount','')}"
            )
            print(f"      {str(r.get('description',''))[:130]}")

    if args.inspect_provider and args.inspect_endpoint:
        info = inspect(args.inspect_provider, args.inspect_endpoint)
        print(json.dumps(info, indent=2)[:12000])


if __name__ == "__main__":
    main()
