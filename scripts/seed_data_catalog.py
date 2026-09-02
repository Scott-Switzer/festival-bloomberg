"""Register the three RAW corpora and the canonical identity estate into the
data catalog. Run once at DATA_LAKE_PRODUCTIZATION_V1 start; re-run idempotently
after any raw refresh.

Usage:
    PYTHONPATH=python .venv/bin/python scripts/seed_data_catalog.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from festival_bloomberg.lake.catalog import register_dataset, catalog_summary

RAW_BUCKET = "festival-intelligence-raw"
LAKE_BUCKET = "festival-intelligence-lake"

# Checkpoint A/B/C acquisition facts (from transfer manifests / logs).
RAW_CORPORA = [
    {
        "dataset_id": "raw.musicbrainz_relational_mbdump",
        "source_version": "20260826-002522",
        "key": "bulk/musicbrainz/relational/dump=20260826-002522/mbdump.tar.bz2",
        "bytes": 7_471_268_863,
        "sha256": "a401af0e482dbe99509e6219c5428c9c90263301b8dbd60ec61f1e2bbd6421c8",
        "verification": "PUBLISHER_CHECKSUM_VERIFIED",
    },
    {
        "dataset_id": "raw.wikidata_truthy_rdf",
        "source_version": "latest-truthy@20260828",
        "key": "bulk/wikidata/dump=latest-truthy/latest-truthy.nt.bz2",
        # Verified against the finalized R2 object with HeadObject on
        # 2026-08-30.  The prior transfer-era estimate was 2,990,891 bytes low.
        "bytes": 43_329_477_419,
        "sha256": None,
        "verification": "R2_HEAD_SIZE_VERIFIED",
    },
    {
        "dataset_id": "raw.listenbrainz_full_dump",
        "source_version": "2593-20260712-000004",
        "key": "bulk/listenbrainz/dump=2593-20260712-000004/listenbrainz-spark-dump-2593-20260712-000004-full.tar",
        "bytes": 205_073_162_240,
        "sha256": None,  # internal fingerprint computed after multipart; filled by verify step
        "verification": "SIZE_VERIFIED+MULTIPART_COMPLETE",
    },
]

# Existing per-table MusicBrainz JSON wave-1 artifacts (already in R2 from PR #57 era).
MB_JSON_TABLES = ["area", "event", "place", "series", "recording"]


def main() -> None:
    for corpus in RAW_CORPORA:
        register_dataset(
            dataset_id=corpus["dataset_id"],
            dataset_version=corpus["source_version"],
            layer="RAW",
            source="musicbrainz" if "musicbrainz" in corpus["dataset_id"]
            else "wikidata" if "wikidata" in corpus["dataset_id"]
            else "listenbrainz",
            source_version=corpus["source_version"],
            r2_bucket=RAW_BUCKET,
            r2_prefix=corpus["key"],
            fmt=("tar" if corpus["dataset_id"] == "raw.listenbrainz_full_dump"
                 else "nt.bz2" if corpus["dataset_id"] == "raw.wikidata_truthy_rdf"
                 else "tar.bz2"),
            schema_version="source-native",
            byte_count=corpus["bytes"],
            source_checksum=corpus["sha256"],
            artifact_checksum=corpus["sha256"],
            verification_status=corpus["verification"],
            license="CC0-1.0",
            rights_status="PUBLIC_DOMAIN_DEDICATED",
            commercial_use_status="ALLOWED",
            upstream_dataset_ids=[],
            notes="Immutable source corpus; never rewritten in place.",
        )
        print(f"  registered RAW {corpus['dataset_id']} ({corpus['bytes'] / 1e9:.2f} GB)")

    for table in MB_JSON_TABLES:
        register_dataset(
            dataset_id=f"raw.musicbrainz_json_{table}",
            dataset_version="20260826-001001",
            layer="RAW",
            source="musicbrainz",
            source_version="20260826-001001",
            r2_bucket=RAW_BUCKET,
            r2_prefix=f"bulk/musicbrainz/{table}.jsonl.gz",
            fmt="jsonl.gz",
            schema_version="source-native",
            verification_status="PUBLISHER_CHECKSUM_VERIFIED",
            license="CC0-1.0",
            rights_status="PUBLIC_DOMAIN_DEDICATED",
            commercial_use_status="ALLOWED",
            upstream_dataset_ids=["raw.musicbrainz_relational_mbdump"],
        )
        print(f"  registered RAW musicbrainz_json_{table}")

    # Canonical identity estate (the 25K security universe snapshot in backups bucket).
    estate_ctl = Path("control/artist_security_25000/current.json")
    if estate_ctl.exists():
        ctl = json.loads(estate_ctl.read_text())
        estate_sha = ctl.get("artifact_sha256")
        register_dataset(
            dataset_id="identity.artist_security_25000",
            dataset_version=ctl.get("version", "v1"),
            layer="SILVER",
            source="internal",
            source_version=ctl.get("version", "v1"),
            r2_bucket="festival-intelligence-backups",
            r2_prefix=ctl.get("source", "control/artist_security_25000/"),
            fmt="json",
            schema_version="estate-v1",
            row_count=ctl.get("universe_size"),
            artifact_checksum=estate_sha,
            verification_status="SHA256_VERIFIED",
            license="PROPRIETARY",
            rights_status="INTERNAL_DERIVED",
            commercial_use_status="INTERNAL_ONLY",
            upstream_dataset_ids=["raw.musicbrainz_relational_mbdump"],
            notes="Canonical 25K tiered security universe (HOT_1000/CORE_5000/COVERAGE_25000).",
        )
        print(f"  registered identity.artist_security_25000 (universe={ctl.get('universe_size')})")

    summary = catalog_summary()
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
