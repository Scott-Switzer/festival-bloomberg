"""Promote a locally built watch_universe_v2 artifact to Cloudflare R2.

This is intentionally a separate explicit command because advancing the active
pointer changes production scheduling behavior.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--bucket", default="festival-intelligence-backups")
    parser.add_argument("--remote", action="store_true", help="perform remote writes")
    args = parser.parse_args()
    data = json.loads(args.artifact.read_text())
    counts = data.get("counts", {})
    if data.get("version") != "watch_universe_v2":
        raise SystemExit("artifact version must be watch_universe_v2")
    if counts.get("universe_size") != len(data.get("events", [])):
        raise SystemExit("universe_size does not match events")
    if counts.get("youtube_channels") != len(data.get("youtube_channels", [])):
        raise SystemExit("youtube channel count does not match identities")
    digest = sha256(args.artifact)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    key = f"control/watch_universe/v2/watch_universe_v2_{stamp}_{digest[:12]}.json"
    pointer = {
        "version": "v2",
        "source": key,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "artifact_sha256": digest,
        "universe_size": counts["universe_size"],
        "active_paid_acquisition_size": counts.get("active_paid_acquisition_size", 0),
    }
    print(json.dumps({"artifact": str(args.artifact), "artifact_sha256": digest, "object_key": key, "pointer": pointer}, indent=2))
    if not args.remote:
        print("DRY_RUN: no R2 writes performed")
        return
    subprocess.run(["npx", "wrangler", "r2", "object", "put", f"{args.bucket}/{key}", "--remote", "--file", str(args.artifact)], check=True)
    pointer_path = Path("/tmp/cloud_watch_pointer.json")
    pointer_path.write_text(json.dumps(pointer, indent=2) + "\n")
    try:
        subprocess.run(["npx", "wrangler", "r2", "object", "put", f"{args.bucket}/control/watch_universe/current.json", "--remote", "--file", str(pointer_path)], check=True)
    finally:
        pointer_path.unlink(missing_ok=True)
    print("PROMOTED")


if __name__ == "__main__":
    main()
