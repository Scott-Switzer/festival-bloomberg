"""VENUE_CONFIGURATION_CAPACITY_V2 — bounded real acquisition pipeline.

Rails:
  1. Wikidata batch (`wbgetentities`, claims P1083 + enwiki sitelinks)
  2. Wikipedia exact-page wikitext via enwiki sitelink -> mwparserfromhell
  3. OpenStreetMap Overpass capacity tags

Target universe is frozen at ~100 distinct canonical venues before acquisition.
Conflicts coexist; nothing is averaged; MAX_PERSONS stays an upper bound.
"""

from __future__ import annotations

import json
import math
import sys
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

import duckdb

from festival_bloomberg.acquisition.contracts import AcquisitionRequest
from festival_bloomberg.acquisition.providers.wikipedia import WikipediaProvider
from festival_bloomberg.acquisition.providers.wikidata import WikidataProvider, parse_p1083_statement
from festival_bloomberg.acquisition.transport import UrllibTransport
from festival_bloomberg.economics.capacity import (
    CapacityClaim,
    claim_from_wikidata,
    claim_from_wikipedia_infobox,
    claims_from_osm,
)
from festival_bloomberg.economics.repository import EconomicsRepository
from festival_bloomberg.economics.wikipedia_capacity import (
    extracts_to_records,
    parse_venue_infobox,
)

SOFTWARE_VERSION = "venue_configuration_capacity_v2"

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"

WORKBENCH_VENUES: tuple[str, ...] = (
    "United Center",
    "Madison Square Garden",
    "Hollywood Bowl",
    "Red Rocks Amphitheatre",
    "The Roxy",
)

FESTIVAL_VENUES: tuple[str, ...] = (
    "Grant Park",
    "Zilker Park",
    "Empire Polo Club",
    "Great Stage Park",
    "Randall's Island",
    "Golden Gate Park",
    "Forest Hills Stadium",
)

TARGET_UNIVERSE_SIZE = 100


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Target universe (frozen before acquisition)
# ---------------------------------------------------------------------------

@dataclass
class TargetVenue:
    venue_key: str | None      # canonical venue_key if resolved in core.venues
    venue_name: str
    city: str | None
    region: str | None
    country: str | None
    source_class: str
    tm_event_count: int | None = None


def _resolve_canonical(conn, name: str) -> dict | None:
    row = conn.execute(
        """SELECT venue_key, name, city, region, country, venue_type, capacity,
                  musicbrainz_id, wikidata_id, openstreetmap_id
           FROM core.venues WHERE lower(name) = lower(?) LIMIT 1""",
        [name],
    ).fetchone()
    if not row:
        return None
    cols = [c[0] for c in conn.description]
    return dict(zip(cols, row))


def freeze_target_universe(conn) -> list[TargetVenue]:
    """Build the frozen ~100-venue target universe deterministically."""
    seen: set[str] = set()
    targets: list[TargetVenue] = []

    def add(name: str, source_class: str, event_count: int | None = None) -> None:
        cv = _resolve_canonical(conn, name)
        key = (cv["venue_key"] if cv else f"unresolved::{name}").lower()
        if key in seen:
            return
        seen.add(key)
        targets.append(
            TargetVenue(
                venue_key=cv["venue_key"] if cv else None,
                venue_name=cv["name"] if cv else name,
                city=cv.get("city") if cv else None,
                region=cv.get("region") if cv else None,
                country=cv.get("country") if cv else None,
                source_class=source_class,
                tm_event_count=event_count,
            )
        )

    # 1. Workbench venues
    for name in WORKBENCH_VENUES:
        add(name, "WORKBENCH")

    # 2. Highest-activity Ticketmaster venues that resolve into canonical
    tm_rows = conn.execute(
        """SELECT venue_name, COUNT(*) c FROM events.provider_event_snapshots
           WHERE venue_name IS NOT NULL GROUP BY venue_name
           ORDER BY c DESC LIMIT 60"""
    ).fetchall()
    for (name, c) in tm_rows:
        if len(targets) >= TARGET_UNIVERSE_SIZE:
            break
        add(name, "HIGH_ACTIVITY", event_count=int(c))

    # 3. Festival / open-field venues
    for name in FESTIVAL_VENUES:
        add(name, "FESTIVAL")

    # 4. High-value box-office venues that resolve into canonical
    bo_rows = conn.execute(
        """SELECT venue, COUNT(*) c FROM research.boxoffice_engagements
           WHERE venue IS NOT NULL GROUP BY venue ORDER BY c DESC LIMIT 40"""
    ).fetchall()
    for (name, c) in bo_rows:
        if len(targets) >= TARGET_UNIVERSE_SIZE:
            break
        add(name, "BOX_OFFICE", event_count=int(c))

    # 5. Top unresolved-but-common Ticketmaster venues as direct names (still acquired)
    for (name, c) in tm_rows:
        if len(targets) >= TARGET_UNIVERSE_SIZE:
            break
        add(name, "HIGH_ACTIVITY_TM", event_count=int(c))

    return targets


# ---------------------------------------------------------------------------
# Wikidata batch access via the canonical transport
# ---------------------------------------------------------------------------

def _wikidata_batch_qids(transport, qids: list[str]) -> dict[str, dict]:
    """Fetch claims + sitelinks for up to 50 QIDs in one wbgetentities call."""
    out: dict[str, dict] = {}
    if not qids:
        return out
    params = {
        "action": "wbgetentities",
        "ids": "|".join(qids),
        "props": "claims|labels|sitelinks",
        "languages": "en",
        "format": "json",
    }
    url = f"{WIKIDATA_API}?{urllib.parse.urlencode(params)}"
    response = transport.request("GET", url, timeout_seconds=45.0)
    if response.status != 200:
        return {}
    try:
        payload = response.json()
    except (ValueError, TypeError):
        return {}
    entities = payload.get("entities") or {}
    for qid, entity in entities.items():
        claims = ((entity.get("claims") or {}).get("P1083")) or []
        label = ((entity.get("labels") or {}).get("en") or {}).get("value")
        sitelinks = entity.get("sitelinks") or {}
        enwiki = (sitelinks.get("enwiki") or {}).get("title")
        out[qid] = {
            "label": label,
            "enwiki_title": enwiki,
            "claims": claims,
        }
    return out


# ---------------------------------------------------------------------------
# Wikipedia wikitext via exact title (from QID sitelink)
# ---------------------------------------------------------------------------

def _wikipedia_wikitext(transport, title: str) -> dict:
    """Fetch wikitext + revision id for one exact Wikipedia title."""
    params = {
        "action": "query",
        "prop": "revisions",
        "rvprop": "content|ids|timestamp",
        "rvslots": "main",
        "format": "json",
        "formatversion": "2",
        "titles": title,
    }
    url = f"{WIKIPEDIA_API}?{urllib.parse.urlencode(params)}"
    response = transport.request("GET", url, timeout_seconds=45.0)
    if response.status != 200:
        return {}
    try:
        payload = response.json()
    except (ValueError, TypeError):
        return {}
    pages = ((payload.get("query") or {}).get("pages")) or []
    for page in pages:
        title_actual = page.get("title")
        if page.get("missing") or not title_actual:
            continue
        revisions = page.get("revisions") or []
        if not revisions:
            continue
        rev = revisions[0]
        slots = rev.get("slots") or {}
        main = slots.get("main") or {}
        return {
            "title": title_actual,
            "revid": rev.get("revid"),
            "timestamp": rev.get("timestamp"),
            "wikitext": main.get("content") or "",
        }
    return {}


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_pipeline(canonical_path: str, *, limit: int | None = None) -> dict:
    conn = duckdb.connect(canonical_path)
    repo = EconomicsRepository(conn)
    transport = UrllibTransport()

    # --- Coverage before ---
    def _coverage(conn):
        claims = conn.execute("SELECT count(*) FROM economics.venue_capacity_claims").fetchone()[0]
        venues = conn.execute(
            "SELECT count(DISTINCT canonical_venue_id) FROM economics.venue_capacity_claims"
        ).fetchone()[0]
        return {"claims": claims, "venues": venues}

    before = _coverage(conn)

    targets = freeze_target_universe(conn)
    if limit:
        targets = targets[:limit]
    print(f"Frozen target universe: {len(targets)} venues")
    for t in targets:
        resolved = "✓" if t.venue_key else "✗"
        print(f"  {resolved} {t.source_class:15s} {t.venue_name}")

    metrics = {
        "targets": len(targets),
        "wikidata": {"requests": 0, "successes": 0, "rate_limited": 0, "failures": 0},
        "wikipedia": {"requests": 0, "successes": 0, "failures": 0},
        "osm": {"requests": 0, "successes": 0, "failures": 0},
        "claims": {"wikidata": 0, "wikipedia": 0, "osm": 0},
    }
    started = time.monotonic()

    # Existing VenueResolver-style mapping reuse for QID lookup
    wd = WikidataProvider(transport=transport)

    # Phase A: resolve QIDs for all targets (search + existing mapping; retry on
    # rate limit with backoff; a single truncated name for suffix-qualified names)
    name_to_qid: dict[str, str | None] = {}
    for t in targets:
        # Prefer existing mapping
        if t.venue_key:
            row = conn.execute(
                "SELECT wikidata_qid FROM economics.venue_source_ids WHERE canonical_venue_id = ?",
                [t.venue_key],
            ).fetchone()
            if row and row[0]:
                name_to_qid[t.venue_name] = row[0]
                continue
        # Fresh search (single request per unresolved venue; retry on rate limit)
        attempts = 0
        while True:
            attempts += 1
            try:
                req = AcquisitionRequest.new(
                    entity_id=str(uuid4()), entity_type="venue", platform="wikidata",
                    query=t.venue_name, operation="SEARCH_ENTITIES", max_cost_usd=0.0,
                )
                res = wd.acquire(req)
                metrics["wikidata"]["requests"] += 1
                if res.status.value == "RATE_LIMITED" or res.error_category == "http_error":
                    metrics["wikidata"]["rate_limited"] += 1
                    if attempts < 5:
                        time.sleep(5 * attempts)
                        continue
                    name_to_qid[t.venue_name] = None
                    break
                if res.is_success and res.records:
                    qid = res.records[0].get("wikidata_qid")
                    name_to_qid[t.venue_name] = qid
                    metrics["wikidata"]["successes"] += 1
                else:
                    name_to_qid[t.venue_name] = None
                    metrics["wikidata"]["failures"] += 1
                break
            except Exception:
                if attempts < 2:
                    time.sleep(3)
                    continue
                name_to_qid[t.venue_name] = None
                metrics["wikidata"]["failures"] += 1
                break
        time.sleep(0.7)

    # Phase B: batched entity fetch (claims + sitelinks) for all resolved QIDs
    resolved = [(t, name_to_qid[t.venue_name]) for t in targets if name_to_qid.get(t.venue_name)]
    qid_meta: dict[str, dict] = {}
    qid_by_venue: dict[int, str] = {}
    chunk = 50
    for i in range(0, len(resolved), chunk):
        batch = resolved[i:i + chunk]
        qids = [q for _, q in batch]
        meta = _wikidata_batch_qids(transport, qids)
        metrics["wikidata"]["requests"] += 1
        metrics["wikidata"]["successes"] += 1
        qid_meta.update(meta)
        for t, q in batch:
            qid_by_venue[id(t)] = q
        # Store mappings
        for t, q in batch:
            if t.venue_key:
                try:
                    repo.upsert_venue_mapping({
                        "mapping_id": f"vsid_{t.venue_key}",
                        "canonical_venue_id": t.venue_key,
                        "venue_name": t.venue_name,
                        "wikidata_qid": q,
                        "osm_type": None, "osm_id": None,
                        "ticketmaster_venue_id": None,
                        "setlistfm_venue_id": None, "seatgeek_venue_id": None,
                        "resolution_status": "RESOLVED" if q else "CANONICAL_ONLY",
                        "resolution_method": "WIKIDATA_SEARCH" if q else "MUSICBRAINZ_DUMP",
                        "ambiguities_json": json.dumps([]),
                        "knowledge_time": utc_now(),
                    })
                except Exception:
                    pass
        time.sleep(0.3)

    # Phase B2: parse Wikidata P1083 claims
    for t, qid in resolved:
        meta = qid_meta.get(qid) or {}
        for statement in meta.get("claims") or []:
            parsed = parse_p1083_statement(statement, qid=qid, label=meta.get("label"), retrieved_at=utc_now())
            if parsed is None:
                continue
            claim = claim_from_wikidata(parsed, venue_id=t.venue_key or f"unresolved::{t.venue_name}")
            if claim and t.venue_key:
                try:
                    if repo.insert_capacity_claim(claim):
                        metrics["claims"]["wikidata"] += 1
                except Exception:
                    pass

    # Phase C: Wikipedia exact-page wikitext via enwiki sitelink
    for t, qid in resolved:
        meta = qid_meta.get(qid) or {}
        enwiki_title = meta.get("enwiki_title")
        if not enwiki_title:
            continue
        page = _wikipedia_wikitext(transport, enwiki_title)
        metrics["wikipedia"]["requests"] += 1
        if not page:
            metrics["wikipedia"]["failures"] += 1
            continue
        metrics["wikipedia"]["successes"] += 1
        parsed = parse_venue_infobox(page.get("wikitext") or "")
        records = extracts_to_records(
            parsed,
            page_title=page.get("title") or enwiki_title,
            source_url=f"https://en.wikipedia.org/wiki/{urllib.parse.quote(enwiki_title.replace(' ', '_'))}",
            wikidata_qid=qid,
            retrieved_at=utc_now(),
        )
        for rec in records:
            rec["source_revision_time"] = page.get("timestamp")
            claim = claim_from_wikipedia_infobox(rec, venue_id=t.venue_key or f"unresolved::{t.venue_name}")
            if claim and t.venue_key:
                try:
                    if repo.insert_capacity_claim(claim):
                        metrics["claims"]["wikipedia"] += 1
                except Exception:
                    pass
        time.sleep(0.3)

    # Phase D: OSM Overpass
    osm_provider = None
    try:
        from festival_bloomberg.acquisition.providers.openstreetmap import OpenStreetMapProvider
        osm_provider = OpenStreetMapProvider(transport=transport)
    except Exception:
        osm_provider = None

    if osm_provider is not None:
        for t in targets:
            if not t.venue_key:
                continue
            city = t.city or ""
            attempts = 0
            while True:
                attempts += 1
                try:
                    req = AcquisitionRequest.new(
                        entity_id=str(uuid4()), entity_type="venue", platform="openstreetmap",
                        query=t.venue_name, market_id=city, operation="search", max_cost_usd=0.0,
                    )
                    res = osm_provider.acquire(req)
                    metrics["osm"]["requests"] += 1
                    if res.status.value in ("PROVIDER_ERROR", "RATE_LIMITED") or res.error_category == "http_error":
                        if attempts < 3:
                            time.sleep(4 * attempts)
                            continue
                        metrics["osm"]["failures"] += 1
                        break
                    if res.is_success and res.records:
                        metrics["osm"]["successes"] += 1
                        for record in res.records:
                            for claim in claims_from_osm(record, venue_id=t.venue_key):
                                try:
                                    if repo.insert_capacity_claim(claim):
                                        metrics["claims"]["osm"] += 1
                                except Exception:
                                    pass
                    else:
                        metrics["osm"]["failures"] += 1
                    break
                except Exception:
                    if attempts < 2:
                        time.sleep(3)
                        continue
                    metrics["osm"]["failures"] += 1
                    break
            time.sleep(1.2)

    elapsed = time.monotonic() - started
    after = _coverage(conn)

    # --- Detailed coverage after ---
    def _kind_count(conn, kind: str) -> int:
        return conn.execute(
            "SELECT count(DISTINCT canonical_venue_id) FROM economics.venue_capacity_claims WHERE capacity_kind = ?",
            [kind],
        ).fetchone()[0]

    def _blocked_count(conn) -> int:
        return conn.execute(
            "SELECT count(*) FROM economics.venue_capacity_claims "
            "WHERE claim_status IN "
            "('CONFLICTING', 'SAME_CONFIGURATION_CONFLICT', 'CROSS_KIND_CONTRADICTION')"
        ).fetchone()[0]

    # One shared reconciliation/prefill contract (same as production
    # capacity_prefill); persists reconciled claim_status, never overwrites
    # raw claims.
    reconciliation = repo.reconcile_capacity_claims()
    venue_name_by_key = {t.venue_key: t.venue_name for t in targets if t.venue_key}

    def _name(venue_id: str) -> str:
        return venue_name_by_key.get(venue_id, venue_id)

    coverage_after = {
        "claims": after["claims"],
        "venues_with_any_claim": after["venues"],
        "max_capacity_venues": _kind_count(conn, "MAX_PERSONS"),
        "seated_venues": _kind_count(conn, "SEATED"),
        "standing_venues": _kind_count(conn, "STANDING"),
        "concert_venues": _kind_count(conn, "CONCERT"),
        "sports_venues": _kind_count(conn, "SPORTS"),
        "conflicting_claims": _blocked_count(conn),
        "workbench_safe_prefill": len(reconciliation["venues_with_ge1_safe_pair"]),
        "workbench_safe_venues": [
            _name(v) for v in reconciliation["venues_with_ge1_safe_pair"]
        ],
        "reconciliation": {
            "venues_assessed": reconciliation["venues_assessed"],
            "safe_pairs": [
                {**p, "venue_name": _name(p["venue_id"])}
                for p in reconciliation["safe_pairs"]
            ],
            "review_required_pairs": [
                {**p, "venue_name": _name(p["venue_id"])}
                for p in reconciliation["review_required_pairs"]
            ],
            "same_configuration_conflicts": [
                {**c, "venue_name": _name(c["venue_id"])}
                for c in reconciliation["same_configuration_conflicts"]
            ],
            "cross_kind_contradictions": [
                {**c, "venue_name": _name(c["venue_id"])}
                for c in reconciliation["cross_kind_contradictions"]
            ],
            "upper_bound_only_venues": [
                _name(v) for v in reconciliation["upper_bound_only_venues"]
            ],
            "unknown_venues": [
                _name(v) for v in reconciliation["unknown_venues"]
            ],
        },
    }

    report = {
        "software_version": SOFTWARE_VERSION,
        "generated_at": utc_now(),
        "target_universe": {
            "requested": TARGET_UNIVERSE_SIZE,
            "frozen": len(targets),
            "resolved_canonical": sum(1 for t in targets if t.venue_key),
            "by_class": _class_counts(targets),
        },
        "before": before,
        "after": coverage_after,
        "metrics": metrics,
        "runtime_seconds": round(elapsed, 1),
        "per_venue": _per_venue_detail(conn, targets),
    }

    out_path = "reports/venue_capacity_v2_report.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    conn.close()
    return report


def _class_counts(targets: list) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in targets:
        out[t.source_class] = out.get(t.source_class, 0) + 1
    return out


def _per_venue_detail(conn, targets: list) -> list[dict]:
    out = []
    for t in targets:
        if not t.venue_key:
            out.append({"venue": t.venue_name, "status": "UNRESOLVED_CANONICAL"})
            continue
        rows = conn.execute(
            """SELECT capacity_value, capacity_kind, claim_status, provider,
                      configuration_description, usage_label, raw_value, parser_version
               FROM economics.venue_capacity_claims WHERE canonical_venue_id = ?
               ORDER BY capacity_kind, capacity_value""",
            [t.venue_key],
        ).fetchall()
        claims = []
        for r in rows:
            claims.append({
                "value": r[0], "kind": r[1], "status": r[2], "provider": r[3],
                "configuration": r[4], "usage": r[5],
                "raw": r[6], "parser": r[7],
            })
        out.append({"venue": t.venue_name, "venue_key": t.venue_key,
                    "source_class": t.source_class, "claims": claims})
    return out


def main():
    canonical = sys.argv[1] if len(sys.argv) > 1 else "data/warehouse/boxoffice_research_v2.duckdb"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else None
    report = run_pipeline(canonical, limit=limit)
    print("\n=== RESULT ===")
    print(f"Target universe: {report['target_universe']['frozen']}")
    print(f"Before: {report['before']}")
    print(f"After:  {report['after']}")
    print(f"Metrics: {json.dumps(report['metrics'], indent=2)}")
    print(f"Runtime: {report['runtime_seconds']}s")


if __name__ == "__main__":
    main()
