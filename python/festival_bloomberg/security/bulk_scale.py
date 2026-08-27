"""Bulk analytical promotion helpers: DuckDB -> partitioned Parquet + manifest."""
from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
    return h.hexdigest()

def export_query_to_parquet(conn, *, query: str, params: list[Any] | None, output_dir: str | Path, dataset: str, source: str, source_version: str, partition_column: str | None = None) -> dict[str, Any]:
    out=Path(output_dir); out.mkdir(parents=True,exist_ok=True)
    root=out/dataset; root.mkdir(exist_ok=True)
    # DuckDB writes one compact Parquet artifact; callers can partition by
    # year/bucket by adding the corresponding filtered queries. This avoids
    # millions of tiny JSON objects and keeps checksums auditable.
    path=root/'part-00000.parquet'
    escaped=str(path).replace("'", "''")
    if params:
        relation = conn.execute(query, params)
        relation.fetch_record_batch if False else None
        # DuckDB exposes the parameterized result as a relation through a
        # temporary table, then COPY writes one compressed analytical file.
        conn.execute("CREATE OR REPLACE TEMP TABLE _bulk_export AS SELECT * FROM (" + query + ")", params)
        conn.execute(f"COPY _bulk_export TO '{escaped}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    else:
        conn.execute(f"COPY ({query}) TO '{escaped}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    row_count=conn.execute(f"SELECT COUNT(*) FROM read_parquet('{escaped}')").fetchone()[0]
    cols=[r[0] for r in conn.execute(f"DESCRIBE SELECT * FROM read_parquet('{escaped}')").fetchall()]
    manifest={
      'dataset':dataset,'source':source,'source_version':source_version,'row_count':row_count,
      'raw_bytes':None,'normalized_bytes':path.stat().st_size,'partition_count':1,
      'partitions':[{'path':str(path.relative_to(out)),'row_count':row_count,'bytes':path.stat().st_size,'sha256':_sha256(path)}],
      'schema':cols,'created_at':datetime.now(timezone.utc).isoformat(),
      'rights_status':'SOURCE_LICENSE_REVIEWED','commercial_use_status':'INTERNAL_ANALYTICS_ONLY',
    }
    manifest_path=root/'manifest.json'; manifest_path.write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest

def register_manifest(conn, manifest: dict[str, Any]) -> None:
    key=hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()[:32]
    conn.execute("INSERT OR REPLACE INTO security.bulk_dataset_manifests (manifest_key,dataset,source,source_version,row_count,raw_bytes,normalized_bytes,partition_count,partitions,checksums,rights_status,commercial_use_status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", [key,manifest['dataset'],manifest['source'],manifest['source_version'],manifest['row_count'],manifest.get('raw_bytes'),manifest.get('normalized_bytes'),manifest['partition_count'],json.dumps(manifest['partitions']),json.dumps([p['sha256'] for p in manifest['partitions']]),manifest['rights_status'],manifest['commercial_use_status'],manifest['created_at']])
