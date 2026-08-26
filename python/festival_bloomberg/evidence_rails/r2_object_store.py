"""
R2 Object Store — content-addressed raw evidence storage on Cloudflare R2.

Replaces BLOB-in-DuckDB pattern with:
  raw payload bytes → SHA-256 → compressed R2 object
  relational row references object URI/hash

Object layout:
  raw/<provider>/<sha[0:2]>/<sha[2:4]>/<sha>.<ext>.zst

Usage:
    from festival_bloomberg.evidence_rails.r2_object_store import R2ObjectStore

    store = R2ObjectStore.from_env()
    uri = store.put(provider="monid", payload=b"...", content_type="HTML")
    data = store.get(uri)
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import struct
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import boto3
import zstandard as zstd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_RAW_BUCKET = "festival-intelligence-raw"
DEFAULT_LAKE_BUCKET = "festival-intelligence-lake"
DEFAULT_BACKUP_BUCKET = "festival-intelligence-backups"

# Zstandard compression level (3 = fast, good ratio for HTML/JSON)
ZSTD_LEVEL = 3


@dataclass
class R2ObjectRef:
    """Reference to an object stored in R2."""

    bucket: str
    key: str
    sha256: str
    uncompressed_bytes: int
    compressed_bytes: int
    content_type: str
    provider: str
    stored_at: str  # ISO-8601

    @property
    def uri(self) -> str:
        return f"r2://{self.bucket}/{self.key}"

    def to_dict(self) -> dict:
        return {
            "bucket": self.bucket,
            "key": self.key,
            "sha256": self.sha256,
            "uncompressed_bytes": self.uncompressed_bytes,
            "compressed_bytes": self.compressed_bytes,
            "content_type": self.content_type,
            "provider": self.provider,
            "stored_at": self.stored_at,
            "uri": self.uri,
        }


class R2ObjectStore:
    """
    Content-addressed object store on Cloudflare R2.

    Objects are compressed with zstd and stored at a deterministic path:
        raw/<provider>/<sha[0:2]>/<sha[2:4]>/<sha>.<ext>.zst
    """

    def __init__(
        self,
        raw_bucket: str = DEFAULT_RAW_BUCKET,
        lake_bucket: str = DEFAULT_LAKE_BUCKET,
        backup_bucket: str = DEFAULT_BACKUP_BUCKET,
        endpoint_url: Optional[str] = None,
        aws_access_key_id: Optional[str] = None,
        aws_secret_access_key: Optional[str] = None,
        region_name: str = "auto",
    ):
        self.raw_bucket = raw_bucket
        self.lake_bucket = lake_bucket
        self.backup_bucket = backup_bucket

        endpoint = endpoint_url or os.environ.get("FI_R2_ENDPOINT")
        access_key = aws_access_key_id or os.environ.get("FI_R2_ACCESS_KEY_ID")
        secret_key = (
            aws_secret_access_key or os.environ.get("FI_R2_SECRET_ACCESS_KEY")
        )

        if not endpoint or not access_key or not secret_key:
            raise ValueError(
                "R2ObjectStore requires FI_R2_ENDPOINT, FI_R2_ACCESS_KEY_ID, "
                "and FI_R2_SECRET_ACCESS_KEY environment variables "
                "(or explicit constructor args)."
            )

        self._s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region_name,
        )
        self._compressor = zstd.ZstdCompressor(level=ZSTD_LEVEL)
        self._decompressor = zstd.ZstdDecompressor()

    @classmethod
    def from_env(cls) -> "R2ObjectStore":
        """Create store from FI_R2_* environment variables."""
        return cls()

    # ------------------------------------------------------------------
    # Content addressing
    # ------------------------------------------------------------------

    @staticmethod
    def sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def object_key(provider: str, sha256_hex: str, ext: str = "bin") -> str:
        """
        Deterministic object key for content-addressed storage.

        Layout: raw/<provider>/<sha[0:2]>/<sha[2:4]>/<sha>.<ext>.zst
        """
        return f"raw/{provider}/{sha256_hex[:2]}/{sha256_hex[2:4]}/{sha256_hex}.{ext}.zst"

    @staticmethod
    def content_type_ext(content_type: str) -> str:
        """Map content type to file extension."""
        ct = content_type.lower()
        if "html" in ct:
            return "html"
        if "json" in ct or "ld+json" in ct:
            return "json"
        if "xml" in ct:
            return "xml"
        return "bin"

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def put(
        self,
        provider: str,
        payload: bytes,
        content_type: str = "HTML",
        metadata: Optional[dict] = None,
    ) -> R2ObjectRef:
        """
        Store a raw payload in R2, content-addressed by SHA-256.

        Args:
            provider: Source provider name (monid, ticketmaster, etc.)
            payload: Raw bytes to store
            content_type: MIME type of the payload
            metadata: Optional extra metadata to store alongside

        Returns:
            R2ObjectRef with the object's location and hashes
        """
        sha = self.sha256(payload)
        ext = self.content_type_ext(content_type)
        key = self.object_key(provider, sha, ext)

        # Check if already exists (dedup)
        try:
            head = self._s3.head_object(Bucket=self.raw_bucket, Key=key)
            existing_size = head["ContentLength"]
            logger.info(
                "Object already exists: %s (%d bytes) — dedup, skipping upload",
                key,
                existing_size,
            )
            return R2ObjectRef(
                bucket=self.raw_bucket,
                key=key,
                sha256=sha,
                uncompressed_bytes=len(payload),
                compressed_bytes=existing_size,
                content_type=content_type,
                provider=provider,
                stored_at=datetime.now(timezone.utc).isoformat(),
            )
        except Exception as e:
            # Object doesn't exist (404), proceed with upload
            logger.debug("Object not found (dedup check): %s", e)
            pass

        # Compress with zstd
        compressed = self._compressor.compress(payload)

        now = datetime.now(timezone.utc).isoformat()

        # Build object metadata
        obj_metadata = {
            "payload-sha256": sha,
            "provider": provider,
            "content-type-original": content_type,
            "uncompressed-bytes": str(len(payload)),
            "stored-at": now,
        }
        if metadata:
            for k, v in metadata.items():
                obj_metadata[f"x-{k}"] = str(v)

        # Upload
        self._s3.put_object(
            Bucket=self.raw_bucket,
            Key=key,
            Body=compressed,
            ContentType="application/octet-stream",
            Metadata=obj_metadata,
        )

        logger.info(
            "Stored %s: %s (%d → %d bytes, %.1f%% ratio)",
            provider,
            key,
            len(payload),
            len(compressed),
            (1 - len(compressed) / max(len(payload), 1)) * 100,
        )

        return R2ObjectRef(
            bucket=self.raw_bucket,
            key=key,
            sha256=sha,
            uncompressed_bytes=len(payload),
            compressed_bytes=len(compressed),
            content_type=content_type,
            provider=provider,
            stored_at=now,
        )

    def get(self, uri: str) -> bytes:
        """
        Retrieve and decompress an object by its r2:// URI.

        Args:
            uri: r2://<bucket>/<key> URI

        Returns:
            Decompressed raw bytes
        """
        if not uri.startswith("r2://"):
            raise ValueError(f"Invalid R2 URI: {uri}")

        parts = uri[5:].split("/", 1)
        bucket = parts[0]
        key = parts[1]

        response = self._s3.get_object(Bucket=bucket, Key=key)
        compressed = response["Body"].read()
        return self._decompressor.decompress(compressed)

    def exists(self, provider: str, sha256_hex: str, exts: Optional[list[str]] = None) -> bool:
        """Check if an object already exists in R2. Tries common extensions if none specified."""
        if exts is None:
            exts = ["html", "json", "xml", "bin"]
        for ext in exts:
            key = self.object_key(provider, sha256_hex, ext)
            try:
                self._s3.head_object(Bucket=self.raw_bucket, Key=key)
                return True
            except Exception:
                continue
        return False

    def get_ref(self, provider: str, sha256_hex: str, exts: Optional[list[str]] = None) -> Optional[R2ObjectRef]:
        """Get metadata for an existing object. Tries common extensions if none specified."""
        if exts is None:
            exts = ["html", "json", "xml", "bin"]
        for ext in exts:
            key = self.object_key(provider, sha256_hex, ext)
            try:
                head = self._s3.head_object(Bucket=self.raw_bucket, Key=key)
                meta = head.get("Metadata", {})
                return R2ObjectRef(
                    bucket=self.raw_bucket,
                    key=key,
                    sha256=sha256_hex,
                    uncompressed_bytes=int(meta.get("uncompressed-bytes", head["ContentLength"])),
                    compressed_bytes=head["ContentLength"],
                    content_type=meta.get("content-type-original", "application/octet-stream"),
                    provider=meta.get("provider", provider),
                    stored_at=meta.get("stored-at", ""),
                )
            except Exception:
                continue
        return None

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------

    def migrate_duckdb_blobs(
        self,
        db_path: str,
        table: str = "acquisition.raw_evidence_store",
        payload_column: str = "payload",
        hash_column: str = "payload_hash",
        provider_column: str = "marketplace",
        content_type_column: str = "payload_type",
    ) -> list[R2ObjectRef]:
        """
        Migrate BLOB payloads from a DuckDB table to R2.

        For each row, moves the BLOB to R2 and returns the reference.
        Does NOT delete the original — call migration_mark_migrated() after verification.
        """
        import duckdb

        conn = duckdb.connect(db_path, read_only=True)
        try:
            rows = conn.execute(
                f"SELECT {hash_column}, {provider_column}, {content_type_column}, "
                f"octet_length({payload_column}) as blob_size "
                f"FROM {table} "
                f"WHERE {payload_column} IS NOT NULL"
            ).fetchall()
        finally:
            conn.close()

        refs = []
        for sha, provider, content_type, blob_size in rows:
            if self.exists(provider or "unknown", sha):
                logger.info("Skipping already-migrated: %s", sha[:16])
                continue

            # We need to re-read the BLOB for upload
            conn2 = duckdb.connect(db_path, read_only=True)
            try:
                row = conn2.execute(
                    f"SELECT {payload_column} FROM {table} "
                    f"WHERE {hash_column} = ?", 
                    [sha]
                ).fetchone()
                if row and row[0]:
                    payload = bytes(row[0])
                    ref = self.put(
                        provider=provider or "unknown",
                        payload=payload,
                        content_type=content_type or "HTML",
                    )
                    refs.append(ref)
            finally:
                conn2.close()

        logger.info("Migrated %d blobs to R2", len(refs))
        return refs

    def store_manifest(
        self,
        manifest: dict,
        name: str = "latest",
    ) -> str:
        """Store a JSON manifest in the backups bucket."""
        key = f"manifests/{name}.json"
        body = json.dumps(manifest, indent=2).encode()
        self._s3.put_object(
            Bucket=self.backup_bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
        )
        return f"r2://{self.backup_bucket}/{key}"
