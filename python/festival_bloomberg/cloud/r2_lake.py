"""
R2 Lake Helpers — Parquet read/write, list, and checkpoint operations for
cloud batch processing.

All operations use the S3-compatible R2 API via boto3. No FUSE mounts.
Objects are written with deterministic keys and content-addressed where
appropriate. Checkpoints are JSON manifests that survive container restart.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import boto3

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class R2LakeConfig:
    """R2 lake configuration derived from environment."""
    endpoint: str
    access_key_id: str
    secret_access_key: str
    raw_bucket: str
    lake_bucket: str
    private_bucket: str
    backup_bucket: str

    @classmethod
    def from_env(cls) -> "R2LakeConfig":
        endpoint = os.environ.get("FI_R2_ENDPOINT", "")
        access_key = os.environ.get("FI_R2_ACCESS_KEY_ID", "")
        secret_key = os.environ.get("FI_R2_SECRET_ACCESS_KEY", "")
        if not endpoint or not access_key or not secret_key:
            raise ValueError(
                "R2LakeConfig requires FI_R2_ENDPOINT, FI_R2_ACCESS_KEY_ID, "
                "and FI_R2_SECRET_ACCESS_KEY environment variables."
            )
        return cls(
            endpoint=endpoint,
            access_key_id=access_key,
            secret_access_key=secret_key,
            raw_bucket=os.environ.get("FI_R2_RAW_BUCKET", "festival-intelligence-raw"),
            lake_bucket=os.environ.get("FI_R2_LAKE_BUCKET", "festival-intelligence-lake"),
            private_bucket=os.environ.get("FI_R2_PRIVATE_BUCKET", "festival-intelligence-private"),
            backup_bucket=os.environ.get("FI_R2_BACKUP_BUCKET", "festival-intelligence-backups"),
        )


class R2Lake:
    """S3-compatible R2 operations for the data lake."""

    def __init__(self, config: R2LakeConfig | None = None):
        self.config = config or R2LakeConfig.from_env()
        self._s3 = boto3.client(
            "s3",
            endpoint_url=self.config.endpoint,
            aws_access_key_id=self.config.access_key_id,
            aws_secret_access_key=self.config.secret_access_key,
            region_name="auto",
        )

    # ── Object I/O ──────────────────────────────────────────────

    def put_bytes(
        self, bucket: str, key: str, data: bytes,
        content_type: str = "application/octet-stream",
        metadata: dict | None = None,
    ) -> str:
        """Upload bytes to R2. Returns the r2:// URI."""
        self._s3.put_object(
            Bucket=bucket, Key=key, Body=data,
            ContentType=content_type,
            Metadata=metadata or {},
        )
        return f"r2://{bucket}/{key}"

    def get_bytes(self, bucket: str, key: str) -> bytes:
        """Download an object from R2."""
        resp = self._s3.get_object(Bucket=bucket, Key=key)
        return resp["Body"].read()

    def head(self, bucket: str, key: str) -> dict | None:
        """HEAD an object. Returns None if not found."""
        try:
            return self._s3.head_object(Bucket=bucket, Key=key)
        except self._s3.exceptions.ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return None
            raise

    def list_prefix(self, bucket: str, prefix: str, limit: int = 1000) -> list[dict]:
        """List objects under a prefix."""
        if limit <= 0:
            return []
        results: list[dict] = []
        token: str | None = None
        while True:
            kwargs: dict = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": min(1000, limit - len(results))}
            if token:
                kwargs["ContinuationToken"] = token
            resp = self._s3.list_objects_v2(**kwargs)
            for obj in resp.get("Contents", []):
                results.append({
                    "key": obj["Key"], "size": obj["Size"],
                    "etag": obj.get("ETag", ""), "modified": obj.get("LastModified"),
                })
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
            if len(results) >= limit:
                break
        return results

    def read_versioned_json(self, bucket: str, key: str) -> tuple[dict | None, str | None]:
        """Read control state and the ETag from the SAME response."""
        try:
            response = self._s3.get_object(Bucket=bucket, Key=key)
        except self._s3.exceptions.ClientError as exc:
            if exc.response["Error"]["Code"] in {"404", "NoSuchKey"}:
                return None, None
            raise
        with response["Body"] as body:
            return json.loads(body.read()), response["ETag"]

    def put_json_if_version(self, bucket: str, key: str, payload: dict, etag: str | None) -> None:
        """Atomic publish: a stale writer must never erase a newer generation."""
        condition = {"IfMatch": etag} if etag else {"IfNoneMatch": "*"}
        self._s3.put_object(
            Bucket=bucket, Key=key, Body=json.dumps(payload, sort_keys=True).encode(),
            ContentType="application/json", **condition,
        )

    def get_bytes_if_match(self, bucket: str, key: str, etag: str) -> bytes:
        response = self._s3.get_object(Bucket=bucket, Key=key, IfMatch=etag)
        with response["Body"] as body:
            return body.read()

    def delete_object(self, bucket: str, key: str) -> None:
        """Delete an object from R2."""
        self._s3.delete_object(Bucket=bucket, Key=key)

    def sha256(self, data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    # ── Parquet I/O ──────────────────────────────────────────────

    def put_parquet(
        self, bucket: str, key: str, table,
        metadata: dict | None = None,
    ) -> dict:
        """Upload a PyArrow Table as Parquet to R2.

        Returns dict with key, bytes, sha256, row_count.
        """
        import pyarrow.parquet as pq
        import pyarrow as pa

        buffer = io.BytesIO()
        pq.write_table(table, buffer, compression="zstd")
        data = buffer.getvalue()
        sha = self.sha256(data)
        self.put_bytes(bucket, key, data, content_type="application/octet-stream", metadata=metadata)
        return {
            "key": key, "bytes": len(data), "sha256": sha,
            "row_count": table.num_rows, "uri": f"r2://{bucket}/{key}",
        }

    def read_parquet(self, bucket: str, key: str, columns: list[str] | None = None):
        """Read a Parquet object from R2 into a PyArrow Table."""
        import pyarrow.parquet as pq
        resp = self._s3.get_object(Bucket=bucket, Key=key)
        return pq.read_table(resp["Body"], columns=columns)

    def read_parquet_prefix(
        self, bucket: str, prefix: str, columns: list[str] | None = None,
        limit: int = 1000,
    ):
        """Read all Parquet objects under a prefix as a single Table."""
        import pyarrow as pa
        objects = self.list_prefix(bucket, prefix, limit=limit)
        tables = []
        for obj in objects:
            if not obj["key"].endswith(".parquet"):
                continue
            tables.append(self.read_parquet(bucket, obj["key"], columns=columns))
        if not tables:
            return pa.table({})
        return pa.concat_tables(tables)

    # ── Range read (for streaming large objects) ──────────────────

    def range_read(self, bucket: str, key: str, start: int, end: int) -> bytes:
        """Read a byte range from an R2 object."""
        resp = self._s3.get_object(
            Bucket=bucket, Key=key, Range=f"bytes={start}-{end}",
        )
        return resp["Body"].read()

    # ── Checkpoint / manifest ────────────────────────────────────

    def write_checkpoint(self, bucket: str, key: str, state: dict) -> str:
        """Write a job checkpoint manifest to R2 (atomic-ish)."""
        payload = json.dumps(state, sort_keys=True, default=str).encode()
        return self.put_bytes(
            bucket, key, payload,
            content_type="application/json",
            metadata={"type": "checkpoint", "job_id": state.get("job_id", "")},
        )

    def read_checkpoint(self, bucket: str, key: str) -> dict | None:
        """Read a checkpoint manifest. Returns None if not found."""
        try:
            data = self.get_bytes(bucket, key)
            return json.loads(data)
        except Exception:
            return None

    def write_manifest(self, bucket: str, key: str, manifest: dict) -> str:
        """Write a job completion manifest to R2."""
        payload = json.dumps(manifest, indent=2, sort_keys=True, default=str).encode()
        return self.put_bytes(
            bucket, key, payload,
            content_type="application/json",
            metadata={"type": "manifest", "job_id": manifest.get("job_id", "")},
        )

    # ── Current pointer ──────────────────────────────────────────

    def write_current_pointer(self, bucket: str, key: str, target_key: str) -> str:
        """Write a CURRENT.json pointer to the latest generation/dataset."""
        pointer = json.dumps({
            "current": target_key, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        return self.put_bytes(bucket, key, pointer.encode(), content_type="application/json")

    def read_current_pointer(self, bucket: str, key: str) -> str | None:
        """Read the current pointer. Returns the target key or None."""
        try:
            data = self.get_bytes(bucket, key)
            return json.loads(data).get("current")
        except Exception:
            return None

    # ── Object verification (P5/P6 contract) ──────────────────────

    def verify_object(
        self, bucket: str, key: str, expected_sha256: str,
    ) -> bool:
        """Verify an R2 object exists and its SHA-256 matches.

        Returns True if the object exists and the hash matches.
        Returns False if the object is missing or the hash differs.
        Never raises on missing/mismatch — returns False so the caller
        can fail closed.
        """
        try:
            data = self.get_bytes(bucket, key)
        except Exception:
            return False
        actual_sha = hashlib.sha256(data).hexdigest()
        return actual_sha == expected_sha256

    def verify_object_exists(self, bucket: str, key: str) -> bool:
        """Check that an R2 object exists (HEAD)."""
        return self.head(bucket, key) is not None
