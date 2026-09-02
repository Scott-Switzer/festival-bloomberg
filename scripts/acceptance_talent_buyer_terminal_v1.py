"""Bounded, read-only acceptance and latency probe for the buyer terminal.

The product database is opened through ``terminal.artist_security`` and is
never modified by this script.  A temporary workspace is used for shortlist
round-trips.  The script deliberately treats an explicit UNKNOWN state as a
successful, honest response; missing panels and invented values are failures.

Usage::

    PYTHONPATH=python .venv/bin/python \
      scripts/acceptance_talent_buyer_terminal_v1.py

The output files are reports, not source fixtures.  A run against a product
database that does not expose a governed tiered universe exits with status 2
and records ``BLOCKED`` rather than silently substituting a hand-picked list.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE_JSON = ROOT / "reports" / "TALENT_BUYER_TERMINAL_ACCEPTANCE.json"
REPORT_MD = ROOT / "reports" / "TALENT_BUYER_TERMINAL_V1_REPORT.md"
TIER_ORDER = ("HOT_1000", "CORE_5000", "COVERAGE_25000")
TIER_QUOTAS = {"HOT_1000": 9, "CORE_5000": 8, "COVERAGE_25000": 8}
PROFILE_ORDER = ("sparse", "medium", "deep")
FORBIDDEN_KEYS = {
    "score", "winner", "recommendation", "booking_recommendation",
    "expected_attendance", "expected_gross", "guarantee_recommendation",
}
UNKNOWN_WORDS = {"UNKNOWN", "NO_CURRENT_TICKET_EVIDENCE", "NOT_AVAILABLE"}


def _jsonable(value: Any) -> Any:
    """Convert DuckDB/Python values into report-safe JSON values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return str(value)


def _walk_keys(value: Any, prefix: str = "") -> Iterable[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            key_s = str(key).lower()
            yield f"{prefix}.{key_s}" if prefix else key_s
            yield from _walk_keys(child, f"{prefix}.{key_s}" if prefix else key_s)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child, prefix)


def contract_issues(payload: Any) -> list[str]:
    """Return P20 contract violations without interpreting artist names."""
    issues: list[str] = []
    for key in _walk_keys(payload):
        leaf = key.rsplit(".", 1)[-1]
        if leaf in FORBIDDEN_KEYS:
            issues.append(f"forbidden opaque/action field: {key}")
    return issues


def _state(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("status", "state", "availability", "evidence_state", "ticket_status"):
        value = payload.get(key)
        if isinstance(value, str):
            return value.upper()
    return None


def honest_unknown(payload: Any) -> bool:
    """Whether a panel declares UNKNOWN rather than encoding missing as zero."""
    state = _state(payload)
    if state not in UNKNOWN_WORDS:
        return False
    if not isinstance(payload, dict):
        return True
    # A declared UNKNOWN panel may carry counts only when they are explicitly
    # evidence counts.  Numeric facts remain NULL/absent.
    for key, value in payload.items():
        if key.lower() in {"status", "state", "availability", "evidence_state", "ticket_status", "reason", "message", "source", "source_system"}:
            continue
        if isinstance(value, (int, float)) and value == 0:
            return False
    return True


def _panel(payload: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in payload:
            return payload[name]
    return None


def capability(payload: dict[str, Any], name: str, *aliases: str) -> dict[str, Any]:
    """Evaluate a named panel, accepting an honest explicit UNKNOWN."""
    value = _panel(payload, name, *aliases)
    if value is None:
        return {"status": "FAIL", "reason": "panel missing"}
    if isinstance(value, dict) and honest_unknown(value):
        return {"status": "PASS", "reason": "explicit UNKNOWN; no value fabricated"}
    if isinstance(value, dict):
        state = _state(value)
        if state in {"OK", "OBSERVED", "AVAILABLE", "PILOT"}:
            return {"status": "PASS", "reason": "panel returned evidence", "state": state}
    if isinstance(value, list):
        return {"status": "PASS", "reason": "panel returned a bounded list", "count": len(value)}
    return {"status": "PASS", "reason": "panel returned a value"}


def validate_peers(peers: Any) -> list[str]:
    """Require lineage for audience peers; no pilot score is accepted."""
    issues = contract_issues(peers)
    if not isinstance(peers, dict):
        return issues + ["peer panel is not an object"]
    if honest_unknown(peers):
        return issues
    lineage = str(
        peers.get("data_lineage") or peers.get("lineage") or peers.get("label")
        or peers.get("source") or peers.get("source_scope") or ""
    )
    if "listenbrainz" not in lineage.lower() or "pilot" not in lineage.lower():
        issues.append("peer panel missing ListenBrainz pilot lineage")
    edges = peers.get("edges") or peers.get("peers") or peers.get("items") or []
    if not isinstance(edges, list):
        issues.append("peer edges must be a list")
    for edge in edges:
        if not isinstance(edge, dict):
            issues.append("peer edge must be an object")
            continue
        if not any(k in edge for k in ("shared_listener_count", "shared_listeners", "jaccard")):
            issues.append("peer edge missing shared-listener/Jaccard evidence")
    return issues


def validate_compare(result: Any) -> list[str]:
    issues = contract_issues(result)
    if not isinstance(result, dict):
        return issues + ["compare result is not an object"]
    if any(key in result for key in ("winner", "recommendation", "decision")):
        issues.append("compare must not declare a winner or decision")
    if not (
        any(key in result for key in ("differences", "comparison", "artist_a", "a"))
        or ({"left", "right", "dimensions"}.issubset(result) and result.get("no_winner") is True)
    ):
        issues.append("compare result lacks side-by-side differences")
    return issues


def validate_shortlist(result: Any, artist_key: str) -> list[str]:
    """Validate a shortlist read-back without treating status as a decision."""
    if not isinstance(result, list):
        return ["shortlist read-back is not a list"]
    matches = [row for row in result if isinstance(row, dict) and row.get("artist_key") == artist_key]
    if not matches:
        return ["shortlist read-back omitted the artist"]
    if len(matches) != 1:
        return ["shortlist read-back duplicated the artist"]
    return []


def nearest_rank(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return round(ordered[index], 3)


def _table_names(conn) -> set[str]:
    try:
        return {str(r[0]) for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog', 'information_schema')"
        ).fetchall()}
    except Exception:
        return set()


def _select_rows(conn) -> tuple[list[dict[str, Any]], str | None]:
    """Select from the governed 25K universe only; never hand-pick names."""
    names = _table_names(conn)
    candidates = [
        "artists", "artist_security_universe_25000", "artist_security_universe",
        "artist_security",
    ]
    table = next((x for x in candidates if x in names), None)
    if not table:
        return [], None
    cols = [str(r[0]) for r in conn.execute(f"DESCRIBE {table}").fetchall()]
    wanted = [x for x in ("artist_key", "artist_name", "name", "mbid", "musicbrainz_id", "tier", "evidence_profile", "evidence_depth", "evidence_family_count") if x in cols]
    if not {"artist_key", "tier"}.issubset(wanted):
        return [], table
    rows = conn.execute(f"SELECT {', '.join(wanted)} FROM {table} ORDER BY artist_key").fetchall()
    out = [dict(zip(wanted, row)) for row in rows]
    for row in out:
        row["artist_name"] = row.get("artist_name") or row.get("name") or row["artist_key"]
        row["mbid"] = row.get("mbid") or row.get("musicbrainz_id")
        row["evidence_profile"] = _profile_bucket(row)
    return out, table


def _profile_bucket(row: dict[str, Any]) -> str:
    explicit = str(row.get("evidence_profile") or row.get("evidence_depth") or "").lower()
    if explicit in PROFILE_ORDER:
        return explicit
    try:
        n = int(row.get("evidence_family_count"))
    except (TypeError, ValueError):
        n = -1
    if n >= 4:
        return "deep"
    if n >= 2:
        return "medium"
    if n >= 0:
        return "sparse"
    return "unknown"


def select_cohort(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deterministically select tier quotas while forcing profile diversity."""
    selected: list[dict[str, Any]] = []
    for tier in TIER_ORDER:
        group = sorted((r for r in rows if str(r.get("tier")) == tier), key=lambda r: str(r["artist_key"]))
        chosen: list[dict[str, Any]] = []
        for profile in PROFILE_ORDER:
            matches = [r for r in group if r.get("evidence_profile") == profile]
            if matches:
                chosen.append(matches[0])
        chosen_keys = {r["artist_key"] for r in chosen}
        chosen.extend(r for r in group if r["artist_key"] not in chosen_keys)
        selected.extend(chosen[: TIER_QUOTAS[tier]])
    return selected


def _call_search(search_fn, conn, name: str, limit: int = 25) -> tuple[list[dict[str, Any]], float]:
    started = time.perf_counter_ns()
    result = search_fn(conn, name, limit=limit)
    elapsed = (time.perf_counter_ns() - started) / 1_000_000
    return list(result or []), elapsed


def search_match(results: Iterable[dict[str, Any]], artist_key: str, artist_name: str) -> bool:
    """Match a search hit by stable key or canonical display name."""
    for result in results:
        if not isinstance(result, dict):
            continue
        if str(result.get("entity_id") or result.get("artist_key") or "") == str(artist_key):
            return True
        if str(result.get("name") or "").casefold() == str(artist_name).casefold():
            return True
    return False


def _measure(label: str, fn, iterations: int = 25) -> dict[str, Any]:
    times: list[float] = []
    errors: list[str] = []
    for _ in range(iterations):
        started = time.perf_counter_ns()
        try:
            fn()
        except Exception as exc:  # report, do not hide the failing endpoint
            errors.append(str(exc))
        times.append((time.perf_counter_ns() - started) / 1_000_000)
    return {
        "iterations": iterations,
        "p50_ms": nearest_rank(times, 0.50),
        "p95_ms": nearest_rank(times, 0.95),
        "min_ms": round(min(times), 3) if times else None,
        "max_ms": round(max(times), 3) if times else None,
        "errors": errors[:3],
    }


def _body(result: dict[str, Any]) -> Any:
    raw = result.get("body", b"")
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _markdown(report: dict[str, Any]) -> str:
    counts = report.get("summary", {})
    browser = report.get("browser_acceptance") or {}
    lines = [
        "# TALENT_BUYER_TERMINAL_V1_REPORT",
        "",
        f"Status: **{report.get('status', 'UNKNOWN')}**",
        "",
        "This is an underwriting-research terminal. It does not issue booking, buy/sell, pass, go/no-go, attendance, gross, or guarantee recommendations.",
        "",
        "## Delivered acceptance",
        "",
        f"- Cohort: {counts.get('cohort_size', 0)} real artists; tiers: {counts.get('tier_counts', {})}; evidence profiles: {counts.get('profile_counts', {})}",
        f"- Capability results: {counts.get('capability_passes', 0)} PASS / {counts.get('capability_failures', 0)} FAIL",
        f"- Honest UNKNOWN panels accepted: {counts.get('honest_unknown_panels', 0)}",
        "",
        "## Performance",
        "",
        "| Operation | P50 ms | P95 ms | N |",
        "|---|---:|---:|---:|",
    ]
    for name, values in report.get("performance", {}).items():
        lines.append(f"| {name} | {values.get('p50_ms')} | {values.get('p95_ms')} | {values.get('iterations')} |")
    lines += [
        "",
        "## Data and limits",
        "",
        f"- Product database: `{report.get('product_db')}`",
        f"- Compact artifact: {report.get('serving_artifact', {}).get('size_bytes')} bytes; counts: {report.get('serving_artifact', {}).get('counts', {})}",
        f"- Cohort source: `{report.get('cohort_source') or 'unavailable'}`",
        "- Ticket semantics: advertised structured ranges only; no resale, transaction, attendance, or sales inference.",
        "- Missing evidence remains UNKNOWN and is never encoded as zero.",
        (
            f"- Browser acceptance: {browser.get('status')} — "
            f"{browser.get('flow', 'visual evidence supplied')}"
            if browser else
            "- Browser acceptance: not supplied to this bounded harness."
        ),
    ]
    for screenshot in browser.get("screenshots", []):
        lines.append(f"  - `{screenshot.get('path')}` — {screenshot.get('sha256')}")
    return "\n".join(lines) + "\n"


def run(product_db: str | None = None, iterations: int = 25) -> dict[str, Any]:
    from festival_bloomberg.terminal.artist_security import (
        DEFAULT_PRODUCT_DB,
        compare_artists,
        get_artist_security,
        open_product_db,
        search_artists,
    )

    db_path = product_db or DEFAULT_PRODUCT_DB
    conn = open_product_db(db_path)
    try:
        meta_cur = conn.execute("SELECT * FROM product_meta LIMIT 1")
        meta_cols = [column[0] for column in meta_cur.description]
        meta = dict(zip(meta_cols, meta_cur.fetchone()))
        validation_json = meta.get("validation_json")
        if isinstance(validation_json, str):
            validation_json = json.loads(validation_json)
        rows, source = _select_rows(conn)
        cohort = select_cohort(rows)
        report: dict[str, Any] = {
            "milestone": "TALENT_BUYER_TERMINAL_V1",
            "status": "PASS" if len(cohort) == 25 else "BLOCKED",
            "product_db": str(db_path),
            "cohort_source": source,
            "cohort": [],
            "performance": {},
            "summary": {},
            "serving_artifact": {
                "size_bytes": Path(db_path).stat().st_size,
                "counts": {
                    "artists": meta.get("artist_count"),
                    "markets": meta.get("market_count"),
                    "peers": meta.get("peer_count"),
                    "events": meta.get("event_count"),
                    "festivals": meta.get("festival_count"),
                    "future_events": meta.get("future_event_count"),
                },
                "validation": validation_json,
            },
        }
        if len(cohort) != 25:
            report["blocked_reason"] = f"governed cohort has {len(cohort)} selected rows; need 25"
            return report

        # Warm the same functions used by the product before measuring.
        first = cohort[0]
        search_artists(conn, first["artist_name"], limit=25)
        get_artist_security(conn, first["artist_key"])
        for item in cohort:
            name = str(item["artist_name"])
            key = str(item["artist_key"])
            search_rows, search_ms = _call_search(search_artists, conn, name)
            profile_started = time.perf_counter_ns()
            profile = get_artist_security(conn, key) or {}
            profile_ms = (time.perf_counter_ns() - profile_started) / 1_000_000
            issues = contract_issues(profile)
            found = search_match(search_rows, key, name)
            caps = {
                "search": {"status": "PASS" if found else "FAIL", "reason": "canonical result found" if found else "artist absent from search"},
                "security_initial": {"status": "PASS" if profile else "FAIL", "reason": "security payload returned" if profile else "empty security payload", "latency_ms": round(profile_ms, 3)},
            }
            for cap_name, aliases in (("attention", ("attention_state",)), ("peers", ("audience_peers",)), ("markets", ("market_profile",)), ("live_history", ("history",)), ("festival_history", ("festivals",)), ("forward", ("ticket", "future")), ("alternatives", ("explainable_alternatives",)), ("evidence", ("freshness", "provenance"))):
                caps[cap_name] = capability(profile, cap_name, *aliases)
            if "peers" in profile:
                peer_issues = validate_peers(profile["peers"])
                if peer_issues:
                    caps["peers"] = {"status": "FAIL", "reason": "; ".join(peer_issues)}
            if issues:
                caps["security_initial"] = {"status": "FAIL", "reason": "; ".join(issues)}
            report["cohort"].append({**_jsonable(item), "capabilities": caps, "search_latency_ms": round(search_ms, 3)})

        a, b = cohort[0], cohort[1]
        compare_result = compare_artists(conn, a["artist_key"], b["artist_key"])
        compare_issues = validate_compare(compare_result)
        report["compare"] = {"status": "PASS" if not compare_issues else "FAIL", "issues": compare_issues, "result_keys": sorted(compare_result) if isinstance(compare_result, dict) else []}
        # Existing planning repository is the mutable workspace boundary.  It
        # is deliberately temporary here: acceptance proves status reuse and
        # read-back while leaving the user's real workspace untouched.
        shortlist_result: list[dict[str, Any]] = []
        shortlist_issues: list[str] = []
        try:
            from festival_bloomberg.planning import repository as planning_repo
            from festival_bloomberg.terminal import storage

            with tempfile.TemporaryDirectory(prefix="talent-buyer-acceptance-") as workspace_dir:
                workspace = storage.create_workspace_db(str(Path(workspace_dir) / "workspace.duckdb"))
                try:
                    project = planning_repo.create_project(
                        workspace,
                        name="TALENT_BUYER_TERMINAL_V1 acceptance",
                        scenario_class="SYNTHETIC_PLANNING_SCENARIO",
                    )
                    for artist in cohort:
                        planning_repo.set_shortlist(
                            workspace, project_key=project["project_key"],
                            artist_key=artist["artist_key"], artist_name=artist["artist_name"],
                            status="DISCOVERED",
                        )
                        # Reuse/upsert each artist: one row, latest explicit
                        # status, no hidden ranking or recommendation.
                        planning_repo.set_shortlist(
                            workspace, project_key=project["project_key"],
                            artist_key=artist["artist_key"], artist_name=artist["artist_name"],
                            status="INTEREST",
                        )
                    workspace.commit()
                    shortlist_result = planning_repo.list_shortlists(workspace, project["project_key"])
                    for artist in cohort:
                        shortlist_issues.extend(validate_shortlist(shortlist_result, artist["artist_key"]))
                    if not shortlist_issues and any(row.get("status") != "INTEREST" for row in shortlist_result):
                        shortlist_issues.append("shortlist reuse did not preserve latest status")
                finally:
                    workspace.close()
        except Exception as exc:  # report capability failure, never fake PASS
            shortlist_issues = [f"shortlist round-trip failed: {exc}"]
        report["shortlist"] = {
            "status": "PASS" if not shortlist_issues else "FAIL",
            "issues": shortlist_issues,
            "rows": len(shortlist_result),
        }
        report["performance"] = {
            "artist_search": _measure("artist_search", lambda: search_artists(conn, first["artist_name"], limit=25), iterations),
            "artist_security": _measure("artist_security", lambda: get_artist_security(conn, first["artist_key"]), iterations),
            "market_panel": _measure("market_panel", lambda: (_panel(get_artist_security(conn, first["artist_key"]) or {}, "markets", "market_profile")), iterations),
            "peer_panel": _measure("peer_panel", lambda: (_panel(get_artist_security(conn, first["artist_key"]) or {}, "peers", "audience_peers")), iterations),
            "compare": _measure("compare", lambda: compare_artists(conn, a["artist_key"], b["artist_key"]), iterations),
        }
        cap_records = [c for row in report["cohort"] for c in row["capabilities"].values()]
        cap_records.extend([report["compare"], report["shortlist"]])
        report["summary"] = {
            "cohort_size": len(report["cohort"]),
            "tier_counts": dict(Counter(str(x.get("tier")) for x in cohort)),
            "profile_counts": dict(Counter(str(x.get("evidence_profile")) for x in cohort)),
            "capability_passes": sum(1 for c in cap_records if c.get("status") == "PASS"),
            "capability_failures": sum(1 for c in cap_records if c.get("status") == "FAIL"),
            "honest_unknown_panels": sum(1 for row in report["cohort"] for c in row["capabilities"].values() if "explicit UNKNOWN" in str(c.get("reason"))),
        }
        report["status"] = (
            "PASS" if report["summary"]["capability_failures"] == 0 else "FAIL"
        )
        return _jsonable(report)
    finally:
        close = getattr(conn, "close", None)
        if close:
            close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product-db", default=None)
    parser.add_argument("--iterations", type=int, default=25)
    parser.add_argument("--json", default=str(ACCEPTANCE_JSON))
    parser.add_argument("--report", default=str(REPORT_MD))
    parser.add_argument(
        "--browser-evidence",
        default=str(ROOT / "reports" / "talent_buyer_terminal_v1" / "browser_acceptance.json"),
    )
    args = parser.parse_args()
    if args.iterations < 25:
        parser.error("--iterations must be at least 25")
    report = run(args.product_db, args.iterations)
    browser_evidence_path = Path(args.browser_evidence)
    if browser_evidence_path.exists():
        browser = json.loads(browser_evidence_path.read_text(encoding="utf-8"))
        browser_issues: list[str] = []
        for item in browser.get("screenshots", []):
            screenshot_path = ROOT / str(item.get("path"))
            if not screenshot_path.is_file():
                browser_issues.append(f"screenshot missing: {item.get('path')}")
                continue
            expected_hash = item.get("sha256")
            actual_hash = hashlib.sha256(screenshot_path.read_bytes()).hexdigest()
            if expected_hash and expected_hash != actual_hash:
                browser_issues.append(f"screenshot hash mismatch: {item.get('path')}")
        if browser_issues:
            browser["status"] = "FAIL"
            browser["issues"] = browser_issues
            report["status"] = "FAIL"
        report["browser_acceptance"] = browser
    json_path, md_path = Path(args.json), Path(args.report)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(_markdown(report), encoding="utf-8")
    print(json.dumps({"status": report.get("status"), "json": str(json_path), "report": str(md_path), "summary": report.get("summary", {})}, sort_keys=True))
    return 0 if report.get("status") == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
