"""Data coverage command center + value-of-information acquisition ranking.

Coverage is measured over the LIVE warehouse (never approximate doc counts).
VOI ranks the next acquisition actions deterministically:

  VOI = (coverage_gain × decision_importance × uniqueness × rights_usability)
        ÷ (engineering_cost + api_cost + rate_limit_cost + semantic_risk)

Higher is better; every action states exactly what coverage it attacks.
"""

from __future__ import annotations

from typing import Any


def _count(conn, sql: str) -> int:
    return int(conn.execute(sql).fetchone()[0])


def _pct(num: int, den: int) -> float:
    return round(num / den, 4) if den else 0.0


def coverage_dashboard(conn) -> dict[str, Any]:
    """Coverage percentages across data families (artist/event/venue/festival)."""
    artists = _count(conn, "SELECT COUNT(*) FROM core.artists")
    events = _count(conn, "SELECT COUNT(DISTINCT platform_object_id) FROM events.provider_event_snapshots WHERE provider='ticketmaster'")
    venues = _count(conn, "SELECT COUNT(*) FROM core.venues")
    festivals = _count(conn, "SELECT COUNT(*) FROM core.festivals")
    editions = _count(conn, "SELECT COUNT(*) FROM core.festival_editions")
    slots = _count(conn, "SELECT COUNT(*) FROM core.lineup_slots")
    performers = _count(conn, "SELECT COUNT(*) FROM core.event_performers")

    d: dict[str, Any] = {
        "artists": {
            "canonical": artists,
            "with_mbid": _count(conn, "SELECT COUNT(*) FROM core.artists WHERE musicbrainz_id IS NOT NULL"),
            "with_attention": _count(conn, "SELECT COUNT(DISTINCT artist_key) FROM metrics.artist_attention_observations WHERE status='ok'"),
        },
        "events": {
            "tm_events": events,
            "with_price": _count(conn, "SELECT COUNT(DISTINCT platform_object_id) FROM events.provider_event_snapshots WHERE provider='ticketmaster' AND price_min IS NOT NULL"),
            "with_promoter": _count(conn, "SELECT COUNT(DISTINCT platform_object_id) FROM events.provider_event_snapshots WHERE provider='ticketmaster' AND promoter IS NOT NULL"),
        },
        "venues": {
            "canonical": venues,
            "with_capacity": _count(conn, "SELECT COUNT(*) FROM core.venues WHERE capacity IS NOT NULL"),
            "capacity_claims": _count(conn, "SELECT COUNT(*) FROM economics.venue_capacity_claims"),
            "with_coords": _count(conn, "SELECT COUNT(*) FROM core.venues WHERE latitude IS NOT NULL"),
        },
        "festivals": {
            "festivals": festivals,
            "editions": editions,
            "lineup_slots": slots,
            "festival_events": _count(conn, "SELECT COUNT(*) FROM core.series_events"),
            "performers": performers,
        },
    }
    # coverage percentages (identity, live, venue, festival)
    d["coverage"] = {
        "artist_mbid_pct": _pct(d["artists"]["with_mbid"], artists),
        "artist_attention_pct": _pct(d["artists"]["with_attention"], artists),
        "event_price_pct": _pct(d["events"]["with_price"], events),
        "event_promoter_pct": _pct(d["events"]["with_promoter"], events),
        "venue_capacity_pct": _pct(d["venues"]["with_capacity"], venues),
        "venue_coords_pct": _pct(d["venues"]["with_coords"], venues),
        "festival_edition_depth": round(editions / festivals, 2) if festivals else 0.0,
        "lineup_slot_per_edition": round(slots / editions, 2) if editions else 0.0,
    }
    return d


# ---------------------------------------------------------------------------
# VOI ranking
# ---------------------------------------------------------------------------
def _voi(coverage_gain: float, decision_importance: float, uniqueness: float,
         rights_usability: float, cost: float) -> float:
    if cost <= 0:
        return 0.0
    return round((coverage_gain * decision_importance * uniqueness * rights_usability) / cost, 3)


def dense_panel_coverage(conn) -> dict[str, float]:
    """Measured coverage for dense-panel feature families (registry probe).

    Keys match feature_registry feature names so the registry can be admitted
    against real measured coverage.
    """
    venues = _count(conn, "SELECT COUNT(*) FROM core.venues")
    return {
        "venue_capacity_band": (_count(conn, "SELECT COUNT(*) FROM core.venues WHERE capacity IS NOT NULL") +
                                _count(conn, "SELECT COUNT(*) FROM economics.venue_capacity_claims")) / max(venues, 1),
        "venue_coordinates": _count(conn, "SELECT COUNT(*) FROM core.venues WHERE latitude IS NOT NULL") / max(venues, 1),
        "artist_attention_wikimedia_30d_at_cutoff": 0.0,  # historical PIT panel not yet built
        "artist_attention_listenbrainz_30d_at_cutoff": 0.0,
        "event_competition_same_day_market": 0.0,          # competition features not yet persisted
        "event_competition_14d_market": 0.0,
        "market_population_vintage": 0.0,                  # ACS vintages not yet ingested
        "market_median_income_vintage": 0.0,
        "tour_position": 0.0,
    }


def voi_ranking(conn) -> dict[str, Any]:
    """Deterministic top acquisition actions from MEASURED coverage gaps."""
    cov = coverage_dashboard(conn)
    c = cov["coverage"]
    artists = cov["artists"]["canonical"]
    events = cov["events"]["tm_events"]
    venues = cov["venues"]["canonical"]
    editions = cov["festivals"]["editions"]
    slots = cov["festivals"]["lineup_slots"]

    dense = dense_panel_coverage(conn)
    candidates: list[dict[str, Any]] = [
        {
            "action": "build historical Wikimedia PIT attention panel (30d windows at cutoff)",
            "coverage_gap": round(1.0 - dense["artist_attention_wikimedia_30d_at_cutoff"], 4),
            "decision_importance": 0.85, "uniqueness": 0.8, "rights_usability": 0.9,
            "engineering_cost": 5.0, "api_cost": 1.0, "rate_limit_cost": 2.0, "semantic_risk": 1.0,
            "category": "attention",
        },
        {
            "action": "persist event competition features (same-day / +-14d market counts)",
            "coverage_gap": round(1.0 - dense["event_competition_same_day_market"], 4),
            "decision_importance": 0.7, "uniqueness": 0.7, "rights_usability": 0.9,
            "engineering_cost": 3.0, "api_cost": 0.0, "rate_limit_cost": 0.0, "semantic_risk": 3.0,
            "category": "competition",
            "note": "PIT knowability of competing events at cutoff is a real semantic risk",
        },
    ] + [
        {
            "action": "resolve venue capacity claims",
            "coverage_gap": round(1.0 - c["venue_capacity_pct"], 4),
            "decision_importance": 0.9, "uniqueness": 0.7, "rights_usability": 0.9,
            "engineering_cost": 4.0, "api_cost": 2.0, "rate_limit_cost": 0.0, "semantic_risk": 1.0,
            "category": "venue_structure",
        },
        {
            "action": "backfill artist attention history (Wikimedia)",
            "coverage_gap": round(1.0 - c["artist_attention_pct"], 4),
            "decision_importance": 0.8, "uniqueness": 0.6, "rights_usability": 0.8,
            "engineering_cost": 5.0, "api_cost": 1.0, "rate_limit_cost": 2.0, "semantic_risk": 1.0,
            "category": "attention",
        },
        {
            "action": "resolve venue coordinates/geography",
            "coverage_gap": round(1.0 - c["venue_coords_pct"], 4),
            "decision_importance": 0.7, "uniqueness": 0.6, "rights_usability": 0.9,
            "engineering_cost": 3.0, "api_cost": 1.0, "rate_limit_cost": 0.0, "semantic_risk": 1.0,
            "category": "venue_structure",
        },
        {
            "action": "expand festival lineup slots (billing/stage/set depth)",
            "coverage_gap": round(1.0 - c["lineup_slot_per_edition"] / 20.0, 4) if c["lineup_slot_per_edition"] else 1.0,
            "decision_importance": 0.85, "uniqueness": 0.9, "rights_usability": 0.7,
            "engineering_cost": 6.0, "api_cost": 0.0, "rate_limit_cost": 0.0, "semantic_risk": 2.0,
            "category": "festival_history",
        },
        {
            "action": "capture event promoter coverage",
            "coverage_gap": round(1.0 - c["event_promoter_pct"], 4),
            "decision_importance": 0.75, "uniqueness": 0.7, "rights_usability": 0.8,
            "engineering_cost": 2.0, "api_cost": 1.0, "rate_limit_cost": 1.0, "semantic_risk": 1.0,
            "category": "live_tape",
        },
        {
            "action": "capture event price/onsale coverage",
            "coverage_gap": round(1.0 - c["event_price_pct"], 4),
            "decision_importance": 0.65, "uniqueness": 0.6, "rights_usability": 0.8,
            "engineering_cost": 2.0, "api_cost": 1.0, "rate_limit_cost": 1.0, "semantic_risk": 1.0,
            "category": "live_tape",
        },
        {
            "action": "obtain design-partner commercial outcomes",
            "coverage_gap": 1.0,  # commercial corpus is 0 by construction
            "decision_importance": 1.0, "uniqueness": 1.0, "rights_usability": 1.0,
            "engineering_cost": 8.0, "api_cost": 0.0, "rate_limit_cost": 0.0, "semantic_risk": 2.0,
            "category": "commercial",
        },
        {
            "action": "resolve MBIDs for canonical artists",
            "coverage_gap": round(1.0 - c["artist_mbid_pct"], 4),
            "decision_importance": 0.8, "uniqueness": 0.8, "rights_usability": 0.9,
            "engineering_cost": 3.0, "api_cost": 0.0, "rate_limit_cost": 0.0, "semantic_risk": 1.0,
            "category": "identity",
        },
    ]
    for cand in candidates:
        cost = cand["engineering_cost"] + cand["api_cost"] + cand["rate_limit_cost"] + cand["semantic_risk"]
        cand["cost"] = cost
        cand["voi"] = _voi(
            cand["coverage_gap"], cand["decision_importance"],
            cand["uniqueness"], cand["rights_usability"], cost,
        )
    ranked = sorted(candidates, key=lambda x: x["voi"], reverse=True)
    return {
        "measured": {
            "canonical_artists": artists, "tm_events": events,
            "canonical_venues": venues, "festival_editions": editions,
            "lineup_slots": slots,
        },
        "ranking": ranked,
    }
