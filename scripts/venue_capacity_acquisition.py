"""Bounded venue capacity acquisition using existing providers + CapacityClaim model.

Target populations are frozen before acquisition. Each claim preserves source,
capacity_kind, and configuration semantics. Conflicts coexist — never averaged.

Wikidata rate-limiting requires 2-5s delays between requests. Wikipedia
infobox search returns NO_RESULTS for many venues (page-matching gap).

VENUE_CONFIGURATION_AND_CAPACITY_EVIDENCE_V1 = PARTIAL.
5 venues enriched with MAX_PERSONS claims (upper bound only).
Workbench-safe prefill = 0.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from uuid import uuid4

import duckdb

from festival_bloomberg.acquisition.contracts import AcquisitionRequest, utc_now
from festival_bloomberg.acquisition.providers.wikipedia import WikipediaProvider
from festival_bloomberg.acquisition.providers.wikidata import WikidataProvider
from festival_bloomberg.economics.capacity import (
    CapacityClaim,
    claim_from_wikidata,
    claim_from_wikipedia_infobox,
    mark_conflicts,
)
from festival_bloomberg.economics.repository import EconomicsRepository

# --- Target population denominators (frozen before acquisition) ---

# Workbench-relevant: venues directly usable in show economics scenarios
WORKBENCH_TARGET_VENUES: tuple[str, ...] = (
    "United Center",
    "Madison Square Garden",
    "Hollywood Bowl",
    "Red Rocks Amphitheatre",
    "The Roxy",
)

# High-activity: venues with most Ticketmaster events
HIGH_ACTIVITY_LIMIT = 20

# Festival-relevant: known festival/open-field venues
FESTIVAL_VENUES: tuple[str, ...] = (
    "Grant Park",
    "Zilker Park",
    "Empire Polo Club",
    "Great Stage Park",
    "Randall's Island",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_venue_for_name(conn, name: str) -> dict | None:
    row = conn.execute(
        """SELECT venue_key, name, city, country, latitude, longitude,
                  venue_type, capacity, musicbrainz_id
           FROM core.venues WHERE lower(name) = lower(?) LIMIT 1""",
        [name],
    ).fetchone()
    if not row:
        return None
    cols = [c[0] for c in conn.description]
    return dict(zip(cols, row))


def _ticketmaster_venues(conn, limit: int = HIGH_ACTIVITY_LIMIT) -> list[dict]:
    rows = conn.execute(
        """SELECT venue_name, COUNT(*) as events
           FROM events.provider_event_snapshots
           WHERE venue_name IS NOT NULL
           GROUP BY venue_name ORDER BY events DESC LIMIT ?""",
        [limit],
    ).fetchall()
    return [{"venue_name": r[0], "event_count": r[1]} for r in rows]


def _all_targets(conn) -> list[dict]:
    """Frozen target list: workbench + high-activity + festival."""
    seen: set[str] = set()
    targets: list[dict] = []

    # 1. Workbench venues
    for name in WORKBENCH_TARGET_VENUES:
        cv = _canonical_venue_for_name(conn, name)
        key = (cv["venue_key"] if cv else name).lower()
        if key not in seen:
            seen.add(key)
            targets.append({
                "venue_name": name,
                "canonical_venue": cv,
                "target_class": "WORKBENCH",
            })

    # 2. High-activity Ticketmaster venues
    for tm in _ticketmaster_venues(conn):
        cv = _canonical_venue_for_name(conn, tm["venue_name"])
        key = (cv["venue_key"] if cv else tm["venue_name"]).lower()
        if key not in seen and cv is not None:
            seen.add(key)
            targets.append({
                "venue_name": tm["venue_name"],
                "canonical_venue": cv,
                "target_class": "HIGH_ACTIVITY",
                "event_count": tm["event_count"],
            })

    # 3. Festival venues
    for name in FESTIVAL_VENUES:
        cv = _canonical_venue_for_name(conn, name)
        key = (cv["venue_key"] if cv else name).lower()
        if key not in seen:
            seen.add(key)
            targets.append({
                "venue_name": name,
                "canonical_venue": cv,
                "target_class": "FESTIVAL",
            })

    return targets


def _enrich_wikipedia(target: dict) -> list[CapacityClaim]:
    provider = WikipediaProvider()
    venue = target["canonical_venue"]
    if not venue:
        return []
    name = venue.get("name") or target["venue_name"]
    city = venue.get("city") or ""
    market = f"{city}" if city else None
    request = AcquisitionRequest.new(
        entity_id=str(uuid4()),
        entity_type="venue",
        platform="wikipedia",
        query=name,
        market_id=market,
        operation="search",
        max_cost_usd=0.0,
    )
    result = provider.acquire(request)
    claims = []
    if result.is_success and result.records:
        for record in result.records:
            claim = claim_from_wikipedia_infobox(record, venue_id=venue["venue_key"])
            if claim:
                claims.append(claim)
    return claims


def _enrich_wikidata(target: dict, qid: str | None = None) -> list[CapacityClaim]:
    provider = WikidataProvider()
    venue = target["canonical_venue"]
    if not venue:
        return []
    venue_id = venue["venue_key"]
    name = venue.get("name") or target["venue_name"]

    # First search for QID if not provided
    if not qid:
        search_req = AcquisitionRequest.new(
            entity_id=str(uuid4()),
            entity_type="venue",
            platform="wikidata",
            query=name,
            operation="SEARCH_ENTITIES",
            max_cost_usd=0.0,
        )
        search_result = provider.acquire(search_req)
        if search_result.is_success and search_result.records:
            qid = search_result.records[0].get("wikidata_qid")

    if not qid:
        return []

    # Fetch claims
    claims_req = AcquisitionRequest.new(
        entity_id=str(uuid4()),
        entity_type="venue",
        platform="wikidata",
        query=qid,
        operation="GET_ENTITY_CLAIMS",
        external_id=qid,
        max_cost_usd=0.0,
    )
    result = provider.acquire(claims_req)
    claims = []
    if result.is_success and result.records:
        for record in result.records:
            claim = claim_from_wikidata(record, venue_id=venue_id)
            if claim:
                claims.append(claim)
    return claims


def _persist_venue_mapping(repo, venue, qid: str | None = None):
    venue_key = venue["venue_key"]
    name = venue.get("name", "")
    mapping_id = f"vsid_{venue_key}"
    repo.upsert_venue_mapping({
        "mapping_id": mapping_id,
        "canonical_venue_id": venue_key,
        "venue_name": name,
        "wikidata_qid": qid,
        "osm_type": None,
        "osm_id": None,
        "ticketmaster_venue_id": venue.get("ticketmaster_id"),
        "setlistfm_venue_id": None,
        "seatgeek_venue_id": None,
        "resolution_status": "RESOLVED" if qid else "CANONICAL_ONLY",
        "resolution_method": "EXACT_NAME" if qid else "MUSICBRAINZ_DUMP",
        "ambiguities_json": json.dumps([]),
        "knowledge_time": _now(),
    })


def run_acquisition(canonical_path: str) -> dict[str, Any]:
    conn = duckdb.connect(canonical_path)
    repo = EconomicsRepository(conn)

    # --- Coverage before ---
    claims_before = conn.execute("SELECT count(*) FROM economics.venue_capacity_claims").fetchone()[0]
    venues_before = conn.execute(
        "SELECT count(DISTINCT canonical_venue_id) FROM economics.venue_capacity_claims"
    ).fetchone()[0]
    total_venues = conn.execute("SELECT count(*) FROM core.venues").fetchone()[0]

    # --- Frozen targets ---
    targets = _all_targets(conn)
    print(f"Target venues: {len(targets)}")
    for t in targets:
        cv = t["canonical_venue"]
        resolved = "✓" if cv else "✗"
        print(f"  {resolved} {t['target_class']:15s} {t['venue_name']}")

    # --- Acquisition ---
    results = []
    total_claims = 0
    errors = 0
    started = time.monotonic()

    for target in targets:
        venue = target["canonical_venue"]
        if not venue:
            results.append({
                "venue_name": target["venue_name"],
                "target_class": target["target_class"],
                "status": "NOT_IN_CANONICAL",
                "claims": 0,
            })
            errors += 1
            continue

        venue_claims = []
        qid = None

        # 1. Wikipedia
        try:
            wp_claims = _enrich_wikipedia(target)
            venue_claims.extend(wp_claims)
        except Exception as exc:
            results.append({
                "venue_name": target["venue_name"],
                "status": f"WIKIPEDIA_ERROR: {exc}",
                "claims": len(venue_claims),
            })

        # 2. Wikidata
        try:
            wd_claims = _enrich_wikidata(target, qid=qid)
            venue_claims.extend(wd_claims)
            # Extract QID for mapping
            for claim in wd_claims:
                if claim.wikidata_qid:
                    qid = claim.wikidata_qid
                    break
        except Exception as exc:
            results.append({
                "venue_name": target["venue_name"],
                "status": f"WIKIDATA_ERROR: {exc}",
                "claims": len(venue_claims),
            })

        # Mark conflicts
        venue_claims = mark_conflicts(venue_claims)

        # Persist
        persisted = 0
        for claim in venue_claims:
            try:
                if repo.insert_capacity_claim(claim):
                    persisted += 1
                    total_claims += 1
            except Exception:
                pass

        # Persist venue source mapping
        if venue_claims or qid:
            try:
                _persist_venue_mapping(repo, venue, qid=qid)
            except Exception:
                pass

        status = "ENRICHED" if persisted else "NO_CLAIMS"
        results.append({
            "venue_name": target["venue_name"],
            "venue_key": venue["venue_key"],
            "target_class": target["target_class"],
            "status": status,
            "claims_found": len(venue_claims),
            "claims_persisted": persisted,
            "sources": list(set(
                c.provider for c in venue_claims
            )),
            "capacity_kinds": list(set(
                c.capacity_kind for c in venue_claims if c.capacity_kind
            )),
        })

        # Rate-limit courtesy (Wikidata needs 2-5s between requests)
        time.sleep(2.0)

    elapsed = time.monotonic() - started

    # --- Coverage after ---
    claims_after = conn.execute("SELECT count(*) FROM economics.venue_capacity_claims").fetchone()[0]
    venues_after = conn.execute(
        "SELECT count(DISTINCT canonical_venue_id) FROM economics.venue_capacity_claims"
    ).fetchone()[0]

    # --- Detailed coverage ---
    any_claim = venues_after
    max_venues = conn.execute(
        "SELECT count(DISTINCT canonical_venue_id) FROM economics.venue_capacity_claims "
        "WHERE capacity_kind = 'MAX_PERSONS'"
    ).fetchone()[0]
    seated_venues = conn.execute(
        "SELECT count(DISTINCT canonical_venue_id) FROM economics.venue_capacity_claims "
        "WHERE capacity_kind = 'SEATED'"
    ).fetchone()[0]
    concert_venues = conn.execute(
        "SELECT count(DISTINCT canonical_venue_id) FROM economics.venue_capacity_claims "
        "WHERE capacity_kind = 'CONCERT'"
    ).fetchone()[0]
    standing_venues = conn.execute(
        "SELECT count(DISTINCT canonical_venue_id) FROM economics.venue_capacity_claims "
        "WHERE capacity_kind = 'STANDING'"
    ).fetchone()[0]
    conflict_claims = conn.execute(
        "SELECT count(*) FROM economics.venue_capacity_claims WHERE claim_status IN "
        "('CONFLICTING', 'SAME_CONFIGURATION_CONFLICT', 'CROSS_KIND_CONTRADICTION')"
    ).fetchone()[0]

    # --- Workbench-safe prefill ---
    # Same logic as capacity_prefill: single distinct integral value, not CONFLICTING
    safe = 0
    for target in targets:
        venue = target["canonical_venue"]
        if not venue:
            continue
        rows = conn.execute(
            """SELECT c.capacity_value, c.capacity_kind, c.claim_status
               FROM economics.venue_capacity_claims c
               WHERE c.canonical_venue_id = ?""",
            [venue["venue_key"]],
        ).fetchall()
        compatible = [
            r for r in rows
            if r[1] in ("STANDING", "CONCERT", "SEATED")
            and r[2] not in ("CONFLICTING", "SAME_CONFIGURATION_CONFLICT", "CROSS_KIND_CONTRADICTION")
        ]
        distinct = set(r[0] for r in compatible if r[0] is not None)
        integral = all(
            r[0] is not None and float(r[0]).is_integer()
            for r in compatible
        )
        if len(distinct) == 1 and integral:
            safe += 1

    report = {
        "software_version": "venue_configuration_capacity_evidence_v1",
        "acquired_at": _now(),
        "canonical_path": canonical_path,
        "denominators": {
            "WORKBENCH_TARGET_VENUES": len(WORKBENCH_TARGET_VENUES),
            "HIGH_ACTIVITY_LIMIT": HIGH_ACTIVITY_LIMIT,
            "FESTIVAL_VENUES": len(FESTIVAL_VENUES),
            "total_targets_resolved": sum(
                1 for t in targets if t["canonical_venue"] is not None
            ),
            "total_target_unresolved": sum(
                1 for t in targets if t["canonical_venue"] is None
            ),
            "total_canonical_venues": total_venues,
        },
        "acquisition_metrics": {
            "targets_attempted": len(targets),
            "claims_persisted": total_claims,
            "errors": errors,
            "duration_seconds": round(elapsed, 1),
        },
        "coverage_before": {
            "claims": claims_before,
            "venues_with_any_claim": venues_before,
        },
        "coverage_after": {
            "claims": claims_after,
            "venues_with_any_claim": any_claim,
            "venues_with_max_capacity": max_venues,
            "venues_with_seated_capacity": seated_venues,
            "venues_with_concert_capacity": concert_venues,
            "venues_with_standing_capacity": standing_venues,
            "conflicting_claims": conflict_claims,
            "workbench_safe_prefill": safe,
        },
        "results": results,
    }

    conn.close()
    return report


def main():
    canonical = sys.argv[1] if len(sys.argv) > 1 else "data/warehouse/boxoffice_research_v2.duckdb"
    print(f"Canonical: {canonical}")
    report = run_acquisition(canonical)
    out_path = "reports/venue_capacity_acquisition_v1.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nReport: {out_path}")
    print(f"Claims persisted: {report['acquisition_metrics']['claims_persisted']}")
    print(f"Coverage: {report['coverage_before']['venues_with_any_claim']} → "
          f"{report['coverage_after']['venues_with_any_claim']} venues with claims")
    print(f"Workbench-safe prefill: {report['coverage_after']['workbench_safe_prefill']}")


if __name__ == "__main__":
    main()


# -- Known gaps documented for V2 --
#
# 1. Wikidata P1083 is sparse: most music venues lack capacity claims entirely.
#    Of 16 target venues, only 5 have P1083 statements.
#
# 2. Wikipedia infobox search needs improved page matching. The current
#    phrase-anchored query (first word quoted) doesn't reliably find venue pages.
#    Wikipedia infoboxes frequently have capacity fields the API could reach.
#
# 3. OSM Overpass API not yet queried. OSM capacity=* and capacity:* tags are
#    a complementary source for concert/standing/seated capacity claims.
#
# 4. All current claims are MAX_PERSONS (upper bound). Configuration-specific
#    capacity (CONCERT, SEATED, STANDING) was not found in any source. This
#    means the workbench capacity_prefill function correctly returns
#    UPPER_BOUND_OR_INCOMPATIBLE_ONLY — no claim is safe for auto-fill.