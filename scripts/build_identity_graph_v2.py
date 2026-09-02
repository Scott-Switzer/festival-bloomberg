#!/usr/bin/env python3
"""Build/report Identity Graph V2 without mutating the canonical warehouse."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from festival_bloomberg.identity.graph_v2 import (
    build_graph, jsonable, read_estate_json, read_wikidata_parquets,
    rows_from_connection, write_graph_tables,
)


def validate_report_path(report: Path | None, inputs: list[Path]) -> None:
    """Prevent a report from overwriting any source or input manifest."""
    if report is None:
        return
    report_resolved = report.resolve()
    collisions = [path for path in inputs if report_resolved == path.resolve()]
    if collisions:
        raise ValueError(f"--report collides with input: {collisions[0]}")


def artifact(path: Path, role: str) -> dict[str, object]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return {"path": str(path.resolve()), "bytes": size, "sha256": digest.hexdigest(), "identity": digest.hexdigest(), "role": role}


def completion_path(output: Path) -> Path:
    """Return the last-published acceptance marker for a materialized DB."""
    return output.with_name(output.name + ".complete.json")


def completion_manifest(
    output: Path, report: Path | None, result: dict[str, object]
) -> dict[str, object]:
    """Hash every published artifact so an old/partial generation fails closed."""
    return {
        "schema_version": 1,
        "status": "COMPLETE",
        "run_key": result["run"]["run_key"],  # type: ignore[index]
        "created_at": result["run"]["created_at"],  # type: ignore[index]
        "output_db": artifact(output, "identity_graph_v2_db"),
        "report": artifact(report, "identity_graph_v2_report") if report else None,
    }


def verify_completion_manifest(marker: Path) -> dict[str, Any]:
    """Fail closed unless every artifact matches the last-published marker."""
    payload = json.loads(marker.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or payload.get("status") != "COMPLETE":
        raise ValueError("completion marker is not COMPLETE")
    for name in ("output_db", "report"):
        expected = payload.get(name)
        if expected is None and name == "report":
            continue
        if not isinstance(expected, Mapping):
            raise ValueError(f"completion marker has invalid {name}")
        path = Path(str(expected.get("path", "")))
        if not path.is_file():
            raise ValueError(f"completion artifact is missing: {name}")
        actual = artifact(path, str(expected.get("role") or name))
        if actual["bytes"] != expected.get("bytes") or actual["sha256"] != expected.get("sha256"):
            raise ValueError(f"completion artifact hash mismatch: {name}")
    return dict(payload)


def compact_report(result: dict[str, object]) -> dict[str, object]:
    conflicts = result.get("conflicts", [])
    counts = Counter((row.get("conflict_type"), row.get("provider")) for row in conflicts)  # type: ignore[union-attr]
    return {
        "run": result["run"], "scorecard": result["scorecard"],
        "conflict_summary": [
            {"conflict_type": key[0], "provider": key[1], "count": count}
            for key, count in sorted(counts.items(), key=lambda pair: (str(pair[0][0]), str(pair[0][1])))
        ],
    }


def write_report_atomic(report: Path, payload: str, *, max_bytes: int = 1_000_000) -> None:
    encoded = payload.encode("utf-8")
    if len(encoded) > max_bytes:
        raise ValueError("compact report exceeds 1 MiB")
    report.parent.mkdir(parents=True, exist_ok=True)
    temp = report.with_name(f".{report.name}.{os.getpid()}.tmp")
    try:
        temp.write_bytes(encoded)
        os.replace(temp, report)
    finally:
        if temp.exists():
            temp.unlink()


def output_space_requirement(result: dict[str, object]) -> int:
    row_count = sum(
        len(result.get(name, []))  # type: ignore[arg-type]
        for name in ("run", "nodes", "evidence", "edges", "conflicts", "scorecard")
    )
    return 1_000_000_000 + row_count * 2_048


def ensure_output_space(output: Path, result: dict[str, object]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    required = output_space_requirement(result)
    available = shutil.disk_usage(output.parent).free
    if available < required:
        raise ValueError(
            f"insufficient free space for output DB: need {required} bytes, have {available}"
        )


def validate_as_of(value: str) -> bool:
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|\+00:00)", value
    ):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(parsed)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, help="DuckDB source database (opened read-only)")
    parser.add_argument("--estate-json", type=Path, required=True, help="governed estate manifest with exactly canonical-limit artists")
    parser.add_argument("--wikidata-parquet", action="append", type=Path, default=[], help="optional local Wikidata generation Parquet; repeatable")
    parser.add_argument("--include-broad", action="store_true", help="materialize a bounded broad sample")
    parser.add_argument("--max-artists", type=int, default=25_000)
    parser.add_argument("--max-evidence", type=int, default=500_000)
    parser.add_argument("--max-edges", type=int, default=250_000)
    parser.add_argument("--output-db", type=Path, help="separate output DB; never the source DB")
    parser.add_argument("--replace-output", action="store_true")
    parser.add_argument("--as-of", required=True, help="RFC3339 UTC knowledge cutoff")
    parser.add_argument("--canonical-limit", type=int, default=25_000)
    parser.add_argument("--report", type=Path, help="write deterministic JSON report")
    parser.add_argument("--dry-run", action="store_true", help="required safety declaration; no database writes are ever performed")
    args = parser.parse_args()
    if not args.db:
        parser.error("--db is required; the builder has no implicit canonical database")
    if args.dry_run and args.output_db:
        parser.error("--dry-run and --output-db are mutually exclusive")
    if args.replace_output and not args.output_db:
        parser.error("--replace-output requires --output-db")
    if not validate_as_of(args.as_of):
        parser.error("--as-of must be RFC3339 UTC")
    if not args.dry_run and not args.output_db:
        parser.error("pass --dry-run or provide a separate --output-db")
    input_paths = [args.db, args.estate_json, *args.wikidata_parquet]
    if args.output_db:
        input_paths.append(args.output_db)
    try:
        validate_report_path(args.report, input_paths)
        if args.output_db and args.output_db.resolve() in {path.resolve() for path in [args.db, args.estate_json, *args.wikidata_parquet]}:
            raise ValueError("--output-db must differ from all inputs")
        if args.output_db and args.output_db.exists() and not args.replace_output:
            raise ValueError("--output-db exists; pass --replace-output")
        if args.output_db:
            complete = completion_path(args.output_db.resolve())
            source_paths = {
                path.resolve() for path in [args.db, args.estate_json, *args.wikidata_parquet]
            }
            if complete in source_paths:
                raise ValueError("output completion marker collides with an input")
            if args.report and args.report.resolve() == complete:
                raise ValueError("--report collides with output completion marker")
            if complete.exists() and not args.replace_output:
                raise ValueError("output completion marker exists; pass --replace-output")
    except ValueError as exc:
        parser.error(str(exc))
    estate_rows = read_estate_json(str(args.estate_json))
    governed_keys = [row["artist_key"] for row in estate_rows]
    import duckdb
    conn = duckdb.connect(str(args.db), read_only=True)
    try:
        artists, external_ids, linkages, source_tables, available_broad = rows_from_connection(
            conn, governed_keys, include_broad=args.include_broad, max_artists=args.max_artists,
        )
    finally:
        conn.close()
    result = build_graph(
        artists=artists, external_ids=external_ids, linkages=linkages,
        wikidata_rows=read_wikidata_parquets(args.wikidata_parquet, allowed_mbids=[row.get("musicbrainz_id") for row in artists]),
        estate_rows=estate_rows, as_of=args.as_of, canonical_limit=args.canonical_limit,
        source_tables=source_tables,
        source_artifacts=[artifact(args.db, "source_db"), artifact(args.estate_json, "governed_estate"), *(artifact(path, "wikidata_parquet") for path in args.wikidata_parquet)],
        available_broader_artist_count=available_broad,
        max_evidence=args.max_evidence, max_edges=args.max_edges,
    )
    if args.output_db:
        import duckdb
        output = args.output_db.resolve()
        try:
            ensure_output_space(output, result)
        except ValueError as exc:
            parser.error(str(exc))
        temp = output.with_name(f".{output.name}.{os.getpid()}.tmp")
        try:
            # The temporary database is unpublished until os.replace() below,
            # so it can safely carry the final state before the atomic commit.
            # This keeps the database and compact report status consistent.
            result["run"]["build_status"] = "MATERIALIZED"  # type: ignore[index]
            conn = duckdb.connect(str(temp))
            try:
                write_graph_tables(conn, result)
            finally:
                conn.close()
        except Exception:
            if temp.exists():
                temp.unlink()
            raise
        os.replace(temp, output)

    payload = json.dumps(compact_report(result), indent=2, sort_keys=True) + "\n"
    if args.report:
        try:
            write_report_atomic(args.report, payload)
        except ValueError as exc:
            parser.error(str(exc))
    else:
        print(payload, end="")
    if args.output_db:
        # The marker is the authoritative multi-artifact publication gate.  It
        # is written only after the DB and optional report are both durable and
        # contains their hashes, so a partial or stale pair is never accepted.
        manifest = json.dumps(
            completion_manifest(args.output_db.resolve(), args.report, result),
            indent=2,
            sort_keys=True,
        ) + "\n"
        write_report_atomic(completion_path(args.output_db.resolve()), manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
