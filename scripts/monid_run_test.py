"""Test Monid /v1/run endpoint formats until we find the right one."""
import json, os, urllib.request, urllib.error
from festival_bloomberg.localenv import load_local_env

load_local_env()
key = os.environ["MONID_API_KEY"]

tests = [
    {"provider": "tinyfish", "endpoint": "/search", "query": "site:seatgeek.com Jodeci"},
    {"provider": "tinyfish", "endpoint": "/search", "body": {"query": "site:seatgeek.com Jodeci"}},
    {"provider": "tinyfish", "endpoint": "/search", "queryParams": {"query": "site:seatgeek.com Jodeci"}},
    {"provider": "tinyfish", "endpoint": "/search", "input": {"queryParams": {"query": "site:seatgeek.com Jodeci"}}},
]

for body in tests:
    try:
        req = urllib.request.Request(
            "https://api.monid.ai/v1/run",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            r = json.loads(resp.read().decode())
        print(f"MATCH {json.dumps(body)[:80]} -> status={r.get('status')} dataLen={len(str(r.get('data','')))}")
    except urllib.error.HTTPError as e:
        print(f"FAIL  {json.dumps(body)[:80]} -> HTTP {e.code} {e.read().decode()[:150]}")