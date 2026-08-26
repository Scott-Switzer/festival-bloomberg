"""
R2 Storage Configuration — cutover contract for Cloudflare R2 object store.

Environment variables:
    FI_OBJECT_STORE          — "R2" or "local" (default: "local" for dev/test)
    FI_R2_ENDPOINT           — S3-compatible endpoint URL
    FI_R2_ACCESS_KEY_ID      — R2 API token access key
    FI_R2_SECRET_ACCESS_KEY  — R2 API token secret key
    FI_R2_RAW_BUCKET         — Bucket for raw evidence objects
    FI_R2_LAKE_BUCKET        — Bucket for Parquet lake
    FI_R2_BACKUP_BUCKET      — Bucket for canonical backups

Local mode: data stays on disk at data/
R2 mode:    data is served from R2, local paths become cache/staging/temp
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class R2Config:
    """Immutable R2 storage configuration."""

    enabled: bool
    endpoint: Optional[str]
    access_key_id: Optional[str]
    raw_bucket: str
    lake_bucket: str
    backup_bucket: str

    @classmethod
    def from_env(cls) -> "R2Config":
        store_type = os.environ.get("FI_OBJECT_STORE", "local").upper()
        enabled = store_type == "R2"

        return cls(
            enabled=enabled,
            endpoint=os.environ.get("FI_R2_ENDPOINT"),
            access_key_id=os.environ.get("FI_R2_ACCESS_KEY_ID"),
            raw_bucket=os.environ.get("FI_R2_RAW_BUCKET", "festival-intelligence-raw"),
            lake_bucket=os.environ.get("FI_R2_LAKE_BUCKET", "festival-intelligence-lake"),
            backup_bucket=os.environ.get("FI_R2_BACKUP_BUCKET", "festival-intelligence-backups"),
        )

    @property
    def raw_uri(self) -> str:
        return f"r2://{self.raw_bucket}"

    @property
    def lake_uri(self) -> str:
        return f"r2://{self.lake_bucket}"

    @property
    def backup_uri(self) -> str:
        return f"r2://{self.backup_bucket}"


# ---------------------------------------------------------------------------
# Local path conventions (used when R2 is not enabled)
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

DATA_DIR = PROJECT_ROOT / "data"
WAREHOUSE_DIR = DATA_DIR / "warehouse"
WORKSPACE_DIR = DATA_DIR / "workspace"
SERVING_DIR = DATA_DIR / "serving"
EVIDENCE_DIR = DATA_DIR / "evidence"
RAW_DIR = DATA_DIR / "raw"

# Canonical DB paths (local mode)
BOXOFFICE_V2_DB = WAREHOUSE_DIR / "boxoffice_research_v2.duckdb"
FESTIVAL_BLOOMBERG_DB = WAREHOUSE_DIR / "festival_bloomberg.duckdb"
TICKET_MARKET_DB = WORKSPACE_DIR / "ticket_market" / "ticket_market.duckdb"


def get_config() -> R2Config:
    """Get the current storage configuration."""
    return R2Config.from_env()
