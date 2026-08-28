#!/usr/bin/env python3
"""
Resumable HTTP byte-range → R2 multipart upload.

Downloads a remote file via HTTP range requests and uploads it to R2 using
S3-compatible multipart upload. No local disk staging required.

Features:
- 64 MiB parts (3,213 parts for 205 GB — well under R2's 10,000 limit)
- Persistent checkpoint for resume after interruption
- Bounded concurrency (default 4)
- Source identity verification on resume (ETag/Content-Length check)
- Whole-object SHA-256 verification against published checksums
- Zero local disk beyond the checkpoint file (~few KB)

Usage:
    python scripts/stream_url_to_r2.py \
        --source-url "https://..." \
        --bucket festival-intelligence-raw \
        --key "bulk/source/dump/file.tar.xz" \
        --expected-sha256 "<hex>" \
        --checkpoint-dir /tmp/r2_checkpoints

Resume is automatic: if the checkpoint file exists, upload resumes from the
last completed part.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import boto3
import requests
from botocore.config import Config

# ─── Constants ────────────────────────────────────────────────────────────────

PART_SIZE = 64 * 1024 * 1024  # 64 MiB
MAX_CONCURRENCY = 4  # default, overridden by --concurrency flag
MAX_RETRIES = 5
INITIAL_BACKOFF_S = 2.0
R2_ENDPOINT = "https://51b88c6a6ef833b3c2ff46e98d5d9356.r2.cloudflarestorage.com"


# ─── Checkpoint ───────────────────────────────────────────────────────────────

def checkpoint_path(checkpoint_dir: str, bucket: str, key: str) -> Path:
    safe_key = key.replace("/", "_").replace("\\", "_")
    return Path(checkpoint_dir) / f"{bucket}__{safe_key}.checkpoint.json"


def load_checkpoint(path: Path) -> Optional[dict]:
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return None


def save_checkpoint(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    tmp.rename(path)


def init_checkpoint(
    source_url: str,
    bucket: str,
    key: str,
    source_size: int,
    source_etag: str | None,
    source_last_modified: str | None,
    total_parts: int,
    upload_id: str,
    checkpoint_dir: str,
) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "source_url": source_url,
        "destination_bucket": bucket,
        "destination_key": key,
        "source_size": source_size,
        "source_etag": source_etag,
        "source_last_modified": source_last_modified,
        "part_size": PART_SIZE,
        "total_parts": total_parts,
        "upload_id": upload_id,
        "completed_parts": [],
        "created_at": now,
        "updated_at": now,
    }


# ─── Source probing ────────────────────────────────────────────────────────────

HEADERS = {"User-Agent": "FestivalIntelBot/2.0 (data-lake-ingestion)"}


def probe_source(url: str) -> dict[str, Any]:
    """HEAD request to get source metadata."""
    resp = requests.head(url, timeout=30, allow_redirects=True, headers=HEADERS)
    resp.raise_for_status()
    return {
        "content_length": int(resp.headers["Content-Length"]),
        "etag": resp.headers.get("ETag", "").strip('"'),
        "last_modified": resp.headers.get("Last-Modified"),
        "accept_ranges": resp.headers.get("Accept-Ranges", "none"),
    }


def verify_source_identity(
    checkpoint: dict,
    current: dict,
    strict: bool = True,
) -> bool:
    """Verify source hasn't changed since checkpoint was created."""
    if checkpoint.get("source_etag") and current.get("etag"):
        if checkpoint["source_etag"] != current["etag"]:
            print(f"  ⚠ ETag changed: {checkpoint['source_etag']} → {current['etag']}")
            if strict:
                return False
    if checkpoint.get("source_last_modified") and current.get("last_modified"):
        if checkpoint["source_last_modified"] != current["last_modified"]:
            print(f"  ⚠ Last-Modified changed")
            if strict:
                return False
    if checkpoint.get("source_size") != current["content_length"]:
        print(
            f"  ⚠ Source size changed: {checkpoint.get('source_size')} → {current['content_length']}"
        )
        if strict:
            return False
    return True


# ─── Download a single part ───────────────────────────────────────────────────

def download_part(
    url: str,
    part_number: int,
    start_byte: int,
    end_byte: int,
) -> tuple[int, bytes]:
    """Download a byte range from the source URL."""
    headers = {"Range": f"bytes={start_byte}-{end_byte}"}
    for attempt in range(MAX_RETRIES + 10):  # extra room for 429 retries
        try:
            hdrs = {**HEADERS, **headers}
            resp = requests.get(url, headers=hdrs, timeout=180, stream=True)
            if resp.status_code == 206:
                data = resp.content
                expected_len = end_byte - start_byte + 1
                if len(data) != expected_len:
                    raise ValueError(
                        f"Part {part_number}: expected {expected_len} bytes, got {len(data)}"
                    )
                return (part_number, data)
            elif resp.status_code == 200:
                raise ValueError(
                    f"Part {part_number}: server returned 200 instead of 206 — range not supported?"
                )
            elif resp.status_code == 429:
                # Rate limited — respect Retry-After header
                retry_after = int(resp.headers.get("Retry-After", "30"))
                print(f"  ⚠ Part {part_number}: rate limited (429), waiting {retry_after}s...")
                time.sleep(retry_after)
                continue
            else:
                resp.raise_for_status()
        except (requests.ConnectionError, requests.Timeout, ValueError) as e:
            backoff = INITIAL_BACKOFF_S * (2 ** attempt)
            print(f"  ⚠ Part {part_number} attempt {attempt+1} failed: {e}")
            if attempt < MAX_RETRIES - 1:
                print(f"    Retrying in {backoff:.0f}s...")
                time.sleep(backoff)
            else:
                raise RuntimeError(f"Part {part_number} failed after {MAX_RETRIES} attempts: {e}")
    raise RuntimeError(f"Part {part_number} failed unexpectedly")


# ─── R2 multipart upload ──────────────────────────────────────────────────────

def _load_credentials():
    """Load R2 credentials from env vars or rclone.conf."""
    ak = os.environ.get("R2_ACCESS_KEY_ID", "")
    sk = os.environ.get("R2_SECRET_ACCESS_KEY", "")
    if ak and sk:
        return ak, sk

    rclone_conf = Path.home() / ".config" / "rclone" / "rclone.conf"
    if rclone_conf.exists():
        import configparser
        cfg = configparser.ConfigParser()
        cfg.read(str(rclone_conf))
        if "r2" in cfg:
            ak = cfg["r2"].get("access_key_id", "")
            sk = cfg["r2"].get("secret_access_key", "")
            if ak and sk:
                os.environ["R2_ACCESS_KEY_ID"] = ak
                os.environ["R2_SECRET_ACCESS_KEY"] = sk
                return ak, sk
    return "", ""


# Load credentials at import time
_load_credentials()


def create_r2_client():
    ak = os.environ.get("R2_ACCESS_KEY_ID", "")
    sk = os.environ.get("R2_SECRET_ACCESS_KEY", "")
    if not ak or not sk:
        raise RuntimeError("R2 credentials not found. Set R2_ACCESS_KEY_ID/R2_SECRET_ACCESS_KEY or configure rclone.")
    return boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=ak,
        aws_secret_access_key=sk,
        config=Config(
            max_pool_connections=MAX_CONCURRENCY + 2,
            retries={"max_attempts": 3, "mode": "adaptive"},
        ),
        region_name="auto",
    )


def start_multipart_upload(s3, bucket: str, key: str) -> str:
    resp = s3.create_multipart_upload(Bucket=bucket, Key=key)
    return resp["UploadId"]


def list_multipart_parts(s3, bucket: str, key: str, upload_id: str) -> list[dict]:
    """List already-uploaded parts for a multipart upload."""
    parts = []
    continuation = None
    while True:
        kwargs: dict[str, Any] = {
            "Bucket": bucket,
            "Key": key,
            "UploadId": upload_id,
            "PartNumberMarker": continuation,
        }
        resp = s3.list_parts(**kwargs)
        for p in resp.get("Parts", []):
            parts.append(
                {
                    "part_number": p["PartNumber"],
                    "etag": p["ETag"],
                    "size": p["Size"],
                }
            )
        if resp.get("IsTruncated"):
            continuation = resp["NextPartNumberMarker"]
        else:
            break
    return parts


def upload_part_to_r2(
    s3,
    bucket: str,
    key: str,
    upload_id: str,
    part_number: int,
    data: bytes,
) -> dict:
    """Upload a single part to R2 with retry."""
    import base64
    md5_b64 = base64.b64encode(hashlib.md5(data).digest()).decode()
    for attempt in range(MAX_RETRIES):
        try:
            resp = s3.upload_part(
                Bucket=bucket,
                Key=key,
                UploadId=upload_id,
                PartNumber=part_number,
                Body=data,
                ContentMD5=md5_b64,
            )
            return {
                "part_number": part_number,
                "etag": resp["ETag"],
                "size": len(data),
                "md5_hex": hashlib.md5(data).hexdigest(),
            }
        except Exception as e:
            backoff = INITIAL_BACKOFF_S * (2 ** attempt)
            print(f"  ⚠ R2 upload part {part_number} attempt {attempt+1} failed: {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(backoff)
            else:
                raise
    raise RuntimeError(f"R2 upload part {part_number} failed after {MAX_RETRIES} attempts")


def complete_multipart_upload(
    s3,
    bucket: str,
    key: str,
    upload_id: str,
    parts: list[dict],
) -> dict:
    """Complete the multipart upload."""
    sorted_parts = sorted(parts, key=lambda p: p["part_number"])
    multipart_upload = {
        "Parts": [{"PartNumber": p["part_number"], "ETag": p["etag"]} for p in sorted_parts]
    }
    return s3.complete_multipart_upload(
        Bucket=bucket,
        Key=key,
        UploadId=upload_id,
        MultipartUpload=multipart_upload,
    )


def abort_multipart_upload(s3, bucket: str, key: str, upload_id: str) -> None:
    """Abort a multipart upload."""
    try:
        s3.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
    except Exception:
        pass


# ─── Whole-object verification ────────────────────────────────────────────────

def verify_whole_object_r2(s3, bucket: str, key: str, expected_size: int) -> dict:
    """HEAD the R2 object and verify size."""
    resp = s3.head_object(Bucket=bucket, Key=key)
    actual_size = resp["ContentLength"]
    return {
        "r2_content_length": actual_size,
        "expected_size": expected_size,
        "size_match": actual_size == expected_size,
        "etag": resp.get("ETag", "").strip('"'),
    }


def stream_r2_sha256(
    s3,
    bucket: str,
    key: str,
    chunk_size: int = 8 * 1024 * 1024,
) -> str:
    """Stream an R2 object through SHA-256 without writing to disk."""
    resp = s3.get_object(Bucket=bucket, Key=key)
    sha = hashlib.sha256()
    total = 0
    while True:
        chunk = resp["Body"].read(chunk_size)
        if not chunk:
            break
        sha.update(chunk)
        total += len(chunk)
        if total % (512 * 1024 * 1024) == 0:
            print(f"    Verification progress: {total / (1024**3):.1f} GB...")
    return sha.hexdigest()


# ─── Main transfer pipeline ───────────────────────────────────────────────────

def transfer(
    source_url: str,
    bucket: str,
    key: str,
    expected_sha256: str | None = None,
    checkpoint_dir: str = "/tmp/r2_checkpoints",
    concurrency: int = MAX_CONCURRENCY,
    inter_part_delay: float = 0.0,
) -> dict:
    """
    Main transfer: download byte ranges from source, upload to R2 via multipart.

    Returns a manifest dict with all verification results.
    """
    cp_dir = Path(checkpoint_dir)
    cp_dir.mkdir(parents=True, exist_ok=True)
    cp_file = checkpoint_path(checkpoint_dir, bucket, key)
    checkpoint = load_checkpoint(cp_file)

    # ── Phase 1: Probe source ──
    print(f"Probing source: {source_url}")
    source_info = probe_source(source_url)
    print(f"  Size: {source_info['content_length']:,} bytes ({source_info['content_length'] / (1024**3):.2f} GB)")
    print(f"  ETag: {source_info['etag']}")
    print(f"  Last-Modified: {source_info['last_modified']}")
    print(f"  Accept-Ranges: {source_info['accept_ranges']}")

    source_size = source_info["content_length"]
    total_parts = (source_size + PART_SIZE - 1) // PART_SIZE
    print(f"  Parts (64 MiB each): {total_parts}")

    # ── Phase 2: Resume or start new ──
    s3 = create_r2_client()
    completed_parts: list[dict] = []
    upload_id: str | None = None

    if checkpoint:
        print(f"\nExisting checkpoint found: {cp_file}")
        if not verify_source_identity(checkpoint, source_info):
            print("  ⚠ Source identity changed! Aborting old upload and starting fresh.")
            if checkpoint.get("upload_id"):
                abort_multipart_upload(s3, bucket, key, checkpoint["upload_id"])
            checkpoint = None
        else:
            print("  ✓ Source identity verified — resuming")
            upload_id = checkpoint["upload_id"]
            # List actual R2 parts to reconcile
            remote_parts = list_multipart_parts(s3, bucket, key, upload_id)
            completed_parts = remote_parts
            print(f"  R2 has {len(remote_parts)} parts already uploaded")

    if not checkpoint or not upload_id:
        print(f"\nStarting new multipart upload → r2:{bucket}/{key}")
        upload_id = start_multipart_upload(s3, bucket, key)
        completed_parts = []
        state = init_checkpoint(
            source_url=source_url,
            bucket=bucket,
            key=key,
            source_size=source_size,
            source_etag=source_info["etag"],
            source_last_modified=source_info["last_modified"],
            total_parts=total_parts,
            upload_id=upload_id,
            checkpoint_dir=checkpoint_dir,
        )
        save_checkpoint(cp_file, state)

    # ── Phase 3: Determine missing parts ──
    uploaded_numbers = {p["part_number"] for p in completed_parts}
    missing = [i for i in range(1, total_parts + 1) if i not in uploaded_numbers]
    uploaded_bytes = sum(p.get("size", PART_SIZE) for p in completed_parts)
    remaining_bytes = source_size - uploaded_bytes

    print(f"\nTransfer status:")
    print(f"  Total parts: {total_parts}")
    print(f"  Uploaded: {len(completed_parts)}")
    print(f"  Missing: {len(missing)}")
    print(f"  Uploaded bytes: {uploaded_bytes:,} ({uploaded_bytes / (1024**3):.2f} GB)")
    print(f"  Remaining: {remaining_bytes:,} ({remaining_bytes / (1024**3):.2f} GB)")
    print(f"  Progress: {uploaded_bytes / source_size * 100:.1f}%")

    if not missing:
        print("\n✓ All parts already uploaded — skipping to completion")
    else:
        # ── Phase 4: Upload missing parts ──
        print(f"\nUploading {len(missing)} parts (concurrency={concurrency}, inter_delay={inter_part_delay}s)...")
        start_time = time.time()
        bytes_uploaded_session = 0
        errors = 0

        def process_part(part_num: int) -> dict:
            if inter_part_delay > 0:
                time.sleep(inter_part_delay)
            start_byte = (part_num - 1) * PART_SIZE
            end_byte = min(part_num * PART_SIZE - 1, source_size - 1)
            _pn, raw_data = download_part(source_url, part_num, start_byte, end_byte)
            part_hash = hashlib.sha256(raw_data).hexdigest()
            result = upload_part_to_r2(s3, bucket, key, upload_id, part_num, raw_data)
            result["sha256"] = part_hash
            return result

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {}
            for part_num in missing:
                futures[executor.submit(process_part, part_num)] = part_num

            for future in as_completed(futures):
                part_num = futures[future]
                try:
                    result = future.result()
                    completed_parts.append(result)
                    bytes_uploaded_session += result["size"]
                    elapsed = time.time() - start_time
                    rate = bytes_uploaded_session / elapsed if elapsed > 0 else 0
                    pct = (uploaded_bytes + bytes_uploaded_session) / source_size * 100
                    done = len([p for p in completed_parts if p["part_number"] in uploaded_numbers or True])
                    print(
                        f"  ✓ Part {part_num:5d}/{total_parts} | "
                        f"{pct:5.1f}% | "
                        f"{rate / (1024**2):.0f} MiB/s | "
                        f"{errors} errors"
                    )
                except Exception as e:
                    errors += 1
                    print(f"  ✗ Part {part_num} FAILED: {e}")
                    if errors > 20:
                        print(f"\n✗ Too many errors ({errors}). Aborting.")
                        abort_multipart_upload(s3, bucket, key, upload_id)
                        raise

                # Save checkpoint after EVERY completed part (process may be killed)
                state["completed_parts"] = [
                    {"part_number": p["part_number"], "etag": p["etag"], "size": p["size"]}
                    for p in completed_parts
                ]
                state["updated_at"] = datetime.now(timezone.utc).isoformat()
                save_checkpoint(cp_file, state)

        elapsed_total = time.time() - start_time
        rate_avg = bytes_uploaded_session / elapsed_total if elapsed_total > 0 else 0
        print(f"\n  Upload session: {bytes_uploaded_session / (1024**3):.2f} GB in {elapsed_total / 60:.1f} min ({rate_avg / (1024**2):.0f} MiB/s)")

    # ── Phase 5: Complete multipart ──
    print(f"\nCompleting multipart upload ({len(completed_parts)} parts)...")
    complete_result = complete_multipart_upload(s3, bucket, key, upload_id, completed_parts)
    print(f"  Location: {complete_result.get('Location', 'N/A')}")

    # ── Phase 6: Verify R2 object ──
    print(f"\nVerifying R2 object...")
    verify = verify_whole_object_r2(s3, bucket, key, source_size)
    print(f"  R2 size: {verify['r2_content_length']:,}")
    print(f"  Expected: {verify['expected_size']:,}")
    print(f"  Size match: {verify['size_match']}")

    if not verify["size_match"]:
        print(f"\n✗ SIZE MISMATCH — object may be corrupt")
        return {
            "status": "SIZE_MISMATCH",
            "r2_key": key,
            "r2_bucket": bucket,
            **verify,
        }

    # ── Phase 7: SHA-256 verification ──
    sha256_status = "NO_CHECKSUM_PUBLISHED"
    remote_sha256 = None

    if expected_sha256:
        print(f"\nVerifying SHA-256 (streaming from R2)...")
        remote_sha256 = stream_r2_sha256(s3, bucket, key)
        sha256_status = "VERIFIED" if remote_sha256 == expected_sha256 else "HASH_MISMATCH"
        print(f"  Remote SHA-256: {remote_sha256}")
        print(f"  Published SHA-256: {expected_sha256}")
        print(f"  Status: {sha256_status}")

        if sha256_status == "HASH_MISMATCH":
            print(f"\n✗ SHA-256 MISMATCH — DO NOT promote this dataset")
    else:
        print(f"\nNo published SHA-256 — skipping whole-object hash verification")

    # ── Phase 8: Clean up checkpoint ──
    if sha256_status != "HASH_MISMATCH":
        cp_file.unlink(missing_ok=True)
        print(f"\n  Checkpoint removed: {cp_file}")

    # ── Build manifest ──
    now = datetime.now(timezone.utc).isoformat()
    manifest = {
        "source_url": source_url,
        "source_size": source_size,
        "source_etag": source_info["etag"],
        "source_last_modified": source_info["last_modified"],
        "published_sha256": expected_sha256,
        "verified_remote_sha256": remote_sha256,
        "verification_status": sha256_status if expected_sha256 else "NO_CHECKSUM_PUBLISHED",
        "r2_bucket": bucket,
        "r2_key": key,
        "r2_size": verify["r2_content_length"],
        "r2_etag": verify["etag"],
        "part_size": PART_SIZE,
        "part_count": len(completed_parts),
        "completed_at": now,
    }

    # Write manifest
    manifest_path = cp_dir / f"{bucket}__{key.replace('/', '_')}.manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"  Manifest: {manifest_path}")

    return manifest


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Resumable HTTP→R2 multipart uploader")
    parser.add_argument("--source-url", required=True, help="Source file URL")
    parser.add_argument("--bucket", required=True, help="R2 bucket name")
    parser.add_argument("--key", required=True, help="R2 object key")
    parser.add_argument("--expected-sha256", default=None, help="Published SHA-256 for verification")
    parser.add_argument("--checkpoint-dir", default="/tmp/r2_checkpoints", help="Checkpoint directory")
    parser.add_argument("--concurrency", type=int, default=MAX_CONCURRENCY, help=f"Upload concurrency (default {MAX_CONCURRENCY})")
    parser.add_argument("--inter-part-delay", type=float, default=0.0, help="Seconds to wait between starting each part (for rate-limited sources)")
    parser.add_argument("--abort", action="store_true", help="Abort existing upload and start fresh")
    args = parser.parse_args()

    # Load credentials from rclone config
    rclone_conf = Path.home() / ".config" / "rclone" / "rclone.conf"
    if rclone_conf.exists() and not os.environ.get("R2_ACCESS_KEY_ID"):
        import configparser
        cfg = configparser.ConfigParser()
        cfg.read(str(rclone_conf))
        if "r2" in cfg:
            os.environ.setdefault("R2_ACCESS_KEY_ID", cfg["r2"].get("access_key_id", ""))
            os.environ.setdefault("R2_SECRET_ACCESS_KEY", cfg["r2"].get("secret_access_key", ""))

    if not os.environ.get("R2_ACCESS_KEY_ID"):
        print("ERROR: R2_ACCESS_KEY_ID not set and not found in rclone.conf")
        sys.exit(1)

    if args.abort:
        cp_file = checkpoint_path(args.checkpoint_dir, args.bucket, args.key)
        cp = load_checkpoint(cp_file)
        if cp and cp.get("upload_id"):
            s3 = create_r2_client()
            abort_multipart_upload(s3, args.bucket, args.key, cp["upload_id"])
            cp_file.unlink(missing_ok=True)
            print(f"Aborted upload {cp['upload_id']} and removed checkpoint")
        else:
            print("No active upload to abort")
        return

    result = transfer(
        source_url=args.source_url,
        bucket=args.bucket,
        key=args.key,
        expected_sha256=args.expected_sha256,
        checkpoint_dir=args.checkpoint_dir,
        concurrency=args.concurrency,
        inter_part_delay=args.inter_part_delay,
    )

    if result.get("verification_status") == "HASH_MISMATCH":
        sys.exit(2)
    elif result.get("status") == "SIZE_MISMATCH":
        sys.exit(3)
    else:
        print(f"\n{'='*60}")
        print(f"✓ TRANSFER COMPLETE")
        print(f"  r2://{result['r2_bucket']}/{result['r2_key']}")
        print(f"  Size: {result['r2_size']:,} bytes ({result['r2_size'] / (1024**3):.2f} GB)")
        print(f"  Verification: {result['verification_status']}")
        print(f"{'='*60}")


if __name__ == "__main__":
    main()
