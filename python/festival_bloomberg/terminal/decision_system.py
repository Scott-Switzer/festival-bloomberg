"""Competitive Buyer Decision System V1 — pre-offer underwriting + buyer memory.

This module turns the Talent Buyer MVP (artist research browser) into a
pre-offer underwriting workstation with four pillars:

  AUDITABLE EVIDENCE     — every number carries source/provenance/knowledge-time
  POINT-IN-TIME          — retrospective features are reconstructed only from
                           evidence dated strictly before the decision cutoff
  BUYER-OWNED PRIVATE    — outcome vault and show history stay in the local
                           workspace DB (PRIVATE_ONLY default), never in the
                           public serving DuckDB
  DETERMINISTIC MATH     — scenarios run through economics.show_economics;
                           no model, no calibrated forecast, no BOOK/PASS

Every request reuses the existing compact serving DuckDB (read-only) plus the
buyer workspace DB (mutable, private). Nothing here depends on the canonical
warehouse; nothing ever writes private outcomes into public serving data.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from . import artist_security


def _rows(conn, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    cur = conn.execute(sql, params or [])
    cols = [column[0] for column in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _one(conn, sql: str, params: list[Any] | None = None) -> dict[str, Any] | None:
    rows = _rows(conn, sql, params)
    return rows[0] if rows else None


def _market_pretty(market_key: str) -> str:
    """'chicago-il' -> 'Chicago, IL'; 'london-gb' -> 'London, GB'."""
    parts = [p for p in str(market_key or "").split("-") if p]
    if not parts:
        return str(market_key or "")
    if len(parts) >= 2:
        city = " ".join(parts[:-1]).title()
        region = parts[-1].upper()
        return f"{city}, {region}"
    return str(market_key).title()

from ..economics import design_partner
from ..economics.show_economics import (
    BackendBasis,
    DealDefinition,
    DealType,
    FixedCosts,
    Provenance,
    ShowEconomicsScenario,
    TicketTier,
    TypedInput,
    evaluate,
    output_to_dict,
    scenario_to_dict,
)

DECISION_SYSTEM_VERSION = "buyer_decision_system_v1"

# ── Buyer workflow statuses (workflow only — never model recommendations) ──
DECISION_STATUSES = ("RESEARCHING", "INTEREST", "HOLD", "OFFER_SENT", "PASSED", "CONFIRMED")

# ── Private tables in the workspace DB (PRIVATE_ONLY by default) ────────────
DECISION_SCHEMA = """
CREATE TABLE IF NOT EXISTS private_imports (
    import_id VARCHAR PRIMARY KEY,
    file_name VARCHAR,
    row_count INTEGER,
    mapped_columns_json VARCHAR,
    pii_quarantine_json VARCHAR,
    sharing_policy VARCHAR NOT NULL DEFAULT 'PRIVATE_ONLY',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS private_shows (
    show_id VARCHAR PRIMARY KEY,
    import_id VARCHAR,
    artist_name VARCHAR,
    artist_key VARCHAR,
    event_date VARCHAR,
    venue_name VARCHAR,
    venue_external_id VARCHAR,
    market VARCHAR,
    city VARCHAR,
    state VARCHAR,
    country VARCHAR,
    venue_capacity VARCHAR,
    usable_capacity VARCHAR,
    ticket_capacity VARCHAR,
    booking_date VARCHAR,
    announcement_date VARCHAR,
    onsale_date VARCHAR,
    deal_type VARCHAR,
    artist_guarantee VARCHAR,
    artist_backend_pct VARCHAR,
    artist_expenses VARCHAR,
    tickets_sold VARCHAR,
    paid_tickets VARCHAR,
    comp_tickets VARCHAR,
    refunded_tickets VARCHAR,
    scanned_attendance VARCHAR,
    paid_attendance VARCHAR,
    reported_attendance VARCHAR,
    ticket_gross VARCHAR,
    ticket_net VARCHAR,
    average_paid_ticket VARCHAR,
    face_value_min VARCHAR,
    face_value_max VARCHAR,
    sold_out VARCHAR,
    marketing_spend VARCHAR,
    venue_cost VARCHAR,
    production_cost VARCHAR,
    labor_cost VARCHAR,
    security_cost VARCHAR,
    insurance_cost VARCHAR,
    other_cost VARCHAR,
    merch_revenue VARCHAR,
    fnb_revenue VARCHAR,
    parking_revenue VARCHAR,
    vip_revenue VARCHAR,
    sponsor_revenue VARCHAR,
    other_revenue VARCHAR,
    promoter_contribution VARCHAR,
    settlement_gross VARCHAR,
    settlement_net VARCHAR,
    currency VARCHAR,
    source_system VARCHAR,
    notes VARCHAR,
    provenance VARCHAR NOT NULL DEFAULT 'OBSERVED_PRIVATE',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS outcome_vault (
    vault_id VARCHAR PRIMARY KEY,
    show_id VARCHAR,
    artist_key VARCHAR,
    event_date VARCHAR,
    observations_json VARCHAR,
    revealed INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS decision_snapshots (
    snapshot_id VARCHAR PRIMARY KEY,
    artist_key VARCHAR,
    artist_name VARCHAR,
    market_key VARCHAR,
    venue VARCHAR,
    event_date VARCHAR,
    status VARCHAR NOT NULL DEFAULT 'RESEARCHING',
    assumption_set_json VARCHAR,
    brief_json VARCHAR,
    evidence_generation VARCHAR,
    notes VARCHAR,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS monitor_baselines (
    artist_key VARCHAR PRIMARY KEY,
    future_events INTEGER,
    markets INTEGER,
    festivals INTEGER,
    attention INTEGER,
    seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


# ────────────────────────────────────────────────────────────────────────────
# UNDERWRITE — the one-page buyer decision brief
# ────────────────────────────────────────────────────────────────────────────

def _ov(item: dict[str, Any], key: str) -> str | None:
    """Extract a numeric-ish value as a display string or None (UNKNOWN)."""
    value = item.get(key)
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None
    return str(value)


def _decode_or_none(value: str | None) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("$", "").replace("%", "")
    if text == "":
        return None
    try:
        return Decimal(text)
    except Exception:
        return None


def build_scenario(inputs: dict[str, Any]) -> ShowEconomicsScenario:
    """Construct the deterministic economics scenario from a buyer's inputs.

    Every field entered by the buyer is USER_ASSUMPTION; everything missing is
    UNKNOWN. Never silently fill missing deal numbers.
    """
    usable = _decode_or_none(inputs.get("usable_capacity"))
    sellable = _decode_or_none(inputs.get("sellable_capacity"))
    if sellable is None and usable is not None:
        sellable = usable
    atp = _decode_or_none(inputs.get("average_ticket_price"))
    sell_through = _decode_or_none(inputs.get("sell_through"))
    guarantee = _decode_or_none(inputs.get("guarantee"))
    backend_pct = _decode_or_none(inputs.get("backend_percentage"))
    deal_type = (inputs.get("deal_type") or "GUARANTEE_VS_PERCENTAGE").strip().upper()
    if deal_type not in {d.value for d in DealType}:
        deal_type = DealType.GUARANTEE_VS_PERCENTAGE.value

    def assumption(value: Decimal | None) -> TypedInput:
        if value is None:
            return TypedInput.unknown()
        return TypedInput.assumption(value)

    tier_quantity = sellable
    price = atp if atp is not None else Decimal("0")

    def money_field(key: str) -> TypedInput:
        return assumption(_decode_or_none(inputs.get(key)))

    scenario = ShowEconomicsScenario(
        currency=TypedInput.assumption("USD"),
        usable_capacity=assumption(usable),
        sellable_capacity=assumption(sellable),
        ticket_scale=(
            TicketTier(
                name="general",
                price=TypedInput.assumption(price) if price > 0 else TypedInput.unknown(),
                quantity=assumption(tier_quantity),
            ),
        ),
        sell_through=assumption(sell_through),
        ticketing_deduction_per_paid_ticket=assumption(
            _decode_or_none(inputs.get("ticketing_deduction")) or Decimal("0")
        ),
        tax_rate_on_gross=assumption(_decode_or_none(inputs.get("tax_rate")) or Decimal("0")),
        deal=DealDefinition(
            deal_type=TypedInput.assumption(deal_type),
            guarantee=assumption(guarantee),
            backend_percentage=assumption(backend_pct),
            backend_basis=TypedInput.assumption(
                inputs.get("backend_basis") or BackendBasis.ADJUSTED_GROSS.value
            ),
            artist_expenses=money_field("artist_expenses"),
            approved_expense_names=TypedInput.assumption(("marketing", "production")),
        ),
        costs=FixedCosts(
            marketing=money_field("cost_marketing"),
            production=money_field("cost_production"),
            venue=money_field("cost_venue"),
            labor=money_field("cost_labor"),
            insurance=money_field("cost_insurance"),
            other=money_field("cost_other"),
        ),
        ancillary_revenue=money_field("ancillary_revenue"),
        sponsorship_allocation=money_field("sponsorship"),
    )
    return scenario


def scenario_sets(inputs: dict[str, Any]) -> dict[str, ShowEconomicsScenario]:
    """downside / base / upside = USER-DEFINED scenario sell-through sets.

    Labels are explicitly "user-defined scenario" — never probability.
    """
    base = _decode_or_none(inputs.get("sell_through"))
    base_value = base if base is not None else Decimal("0.55")
    lo = _decode_or_none(inputs.get("sell_through_down"))
    hi = _decode_or_none(inputs.get("sell_through_up"))
    return {
        "downside": _with_sell_through(inputs, lo if lo is not None else max(Decimal("0.30"), base_value - Decimal("0.25"))),
        "base": _with_sell_through(inputs, base_value),
        "upside": _with_sell_through(inputs, hi if hi is not None else min(Decimal("0.85"), base_value + Decimal("0.25"))),
    }


def _with_sell_through(inputs: dict[str, Any], rate: Decimal) -> ShowEconomicsScenario:
    clone = dict(inputs)
    clone["sell_through"] = str(rate)
    return build_scenario(clone)


def build_underwrite(
    conn,
    workspace: Any,
    *,
    artist_key: str,
    market_key: str | None,
    inputs: dict[str, Any],
    generation: str | None = None,
) -> dict[str, Any]:
    """Assemble the one-page buyer decision brief (sections A–H)."""
    payload = artist_security.get_artist_security(conn, artist_key)
    if payload is None:
        raise ValueError(f"artist not found: {artist_key}")
    artist = payload.get("artist", {})
    markets = (payload.get("markets") or {}).get("items") or []
    future = (payload.get("future") or {}).get("items") or []
    attention = payload.get("attention") or {}
    peers = (payload.get("peers") or {}).get("items") or []
    alternatives = (payload.get("alternatives") or {}).get("items") or []

    market_row = None
    for m in markets:
        key = m.get("market_key") or m.get("market") or m.get("market_name") or ""
        if market_key and key.lower() == (market_key or "").lower():
            market_row = m
            break
    if market_row is None and market_key:
        market_row = _one(
            conn,
            "SELECT market_key, observed_shows, first_play_date, last_play_date, future_events "
            "FROM artist_markets WHERE artist_key = ? AND market_key = ?",
            [artist_key, market_key],
        )

    event_date = inputs.get("event_date") or ""

    # ── B. artist state (concise digest of the artist page) ──
    artist_state = {
        "name": artist.get("name"),
        "tier": artist.get("tier"),
        "identity_status": (artist.get("coverage_state") or {}).get("identity"),
        "historical_events": artist.get("historical_event_count"),
        "festival_appearances": artist.get("festival_appearance_count"),
        "markets": artist.get("market_count"),
        "audience_peers": len(peers),
        "forward_events": (payload.get("future") or {}).get("items") and len(future),
        "attention_sources": sorted({k for k, v in attention.items() if (v or {}).get("status") == "OBSERVED"}),
    }

    # ── C. market state ──
    market_state = {
        "market_key": (market_row or {}).get("market_key") or market_key,
        "observed_shows": (market_row or {}).get("observed_shows"),
        "first_play_date": (market_row or {}).get("first_play_date"),
        "last_play_date": (market_row or {}).get("last_play_date"),
        "future_events": (market_row or {}).get("future_events"),
    }
    competing = _competing_events(conn, artist_key, market_key, event_date)

    # ── D. comparables (explainable components, no hidden score) ──
    comparables = build_comparables(conn, artist_key, market_key)

    # ── E. economics (deterministic scenario math) ──
    scenarios: dict[str, Any] = {}
    for label, scenario in scenario_sets(inputs).items():
        try:
            evaluation = evaluate(scenario)
            scenarios[label] = {
                "label": "USER-DEFINED SCENARIO",
                "sell_through_assumed": str(inputs.get("sell_through")) if label == "base" else None,
                "outputs": {name: output_to_dict(value) for name, value in evaluation.outputs.items()},
                "scenario": scenario_to_dict(scenario),
            }
        except Exception as exc:  # validation errors propagate as UNKNOWN-gated
            scenarios[label] = {"label": "USER-DEFINED SCENARIO", "error": str(exc)}
    base_outputs = (scenarios.get("base") or {}).get("outputs") or {}

    # ── F. risk flags (deterministic / evidence-backed only) ──
    risk_flags = build_risk_flags(
        conn, artist_key, artist_state, market_state, competing,
        attention, peers, scenarios, guarantee=inputs.get("guarantee"),
    )

    # ── G. alternatives (3–5 with why) ──
    alternatives_short = [
        {
            "artist_key": a.get("artist_key"),
            "artist_name": a.get("artist_name"),
            "reasons": (a.get("reasons") or [])[:4],
        }
        for a in alternatives[:5]
    ]

    # ── H. evidence drum-down provenance ──
    evidence = (payload.get("evidence") or {}).get("items") or []

    generation = generation

    return {
        "version": DECISION_SYSTEM_VERSION,
        "artist_key": artist_key,
        "artist": artist_state,
        "market": market_state,
        "competing_events": competing,
        "comparables": comparables,
        "economics": scenarios,
        "risk_flags": risk_flags,
        "alternatives": alternatives_short,
        "evidence": evidence,
        "generation": generation,
        "assumptions_entered": {k: v for k, v in inputs.items() if v not in (None, "")},
    }


def _serving_generation(current_json_path) -> str | None:
    """Read the generation label from the launcher-written CURRENT.json."""
    try:
        if current_json_path is None:
            return None
        import json as _json
        from pathlib import Path as _Path
        raw = _Path(current_json_path).read_text(encoding="utf-8")
        return _json.loads(raw).get("generation")
    except Exception:
        return None


def _competing_events(conn, artist_key: str, market_key: str | None, event_date: str) -> list[dict[str, Any]]:
    """Forward events in the same market window as the planned date, excluding
    the subject artist. Provider-listed shows only — not sales."""
    if not market_key or not event_date:
        return []
    city = _market_pretty(market_key).split(",")[0].lower()
    try:
        event_dt = datetime.fromisoformat(event_date[:10])
    except Exception:
        return []
    lo = (event_dt - timedelta(days=14)).date().isoformat()
    hi = (event_dt + timedelta(days=14)).date().isoformat()
    return _rows(
        conn,
        """SELECT f.artist_key, a.name AS artist_name, f.event_date, f.venue_name,
                  f.city AS venue_city, f.event_status, f.ticket_price_min, f.ticket_price_max
           FROM future_events f JOIN artists a USING (artist_key)
           WHERE f.artist_key != ?
             AND lower(f.city) = ?
             AND f.event_date BETWEEN ? AND ?
           ORDER BY f.event_date
           LIMIT 20""",
        [artist_key, city, lo, hi],
    )


def build_risk_flags(
    conn, artist_key: str, artist_state: dict[str, Any], market_state: dict[str, Any],
    competing: list[dict[str, Any]], attention: dict[str, Any], peers: list[dict[str, Any]],
    scenarios: dict[str, Any], *, guarantee: str | None,
) -> list[dict[str, str]]:
    """Deterministic, evidence-backed flags. No black-box score."""
    flags: list[dict[str, str]] = []
    guarantee_dec = _decode_or_none(guarantee)

    if market_state.get("last_play_date"):
        try:
            last = datetime.fromisoformat(str(market_state["last_play_date"])[:10])
            if (datetime.now(timezone.utc).date() - last.date()).days <= 365:
                flags.append({
                    "flag": "recent_market_play",
                    "label": "Recent market play",
                    "detail": f"Last observed play in this market on {last.date().isoformat()} (within 12 months).",
                })
        except Exception:
            pass
    if market_state.get("future_events"):
        flags.append({
            "flag": "nearby_future_event",
            "label": "Another forward event in market",
            "detail": f"{market_state['future_events']} provider-listed forward event(s) already in this market.",
        })
    if competing:
        flags.append({
            "flag": "competing_events",
            "label": "Relevant competing events",
            "detail": f"{len(competing)} provider-listed show(s) by other artists in this market ±14 days of the planned date.",
        })
    shows = market_state.get("observed_shows")
    if shows is None:
        flags.append({
            "flag": "limited_local_evidence",
            "label": "Limited local evidence",
            "detail": "No observed play history in this market is in the serving evidence.",
        })
    elif int(shows) < 2:
        flags.append({
            "flag": "limited_local_evidence",
            "label": "Limited local evidence",
            "detail": f"Only {shows} observed show(s) in this market are in the serving evidence.",
        })
    if not peers:
        flags.append({
            "flag": "thin_audience_evidence",
            "label": "Thin audience evidence",
            "detail": "No audience-peer evidence exists for this artist in the pilot sample.",
        })
    lb = attention.get("listenbrainz") or {}
    latest = lb.get("latest_observation")
    if latest:
        try:
            latest_dt = datetime.fromisoformat(str(latest)[:10])
            if (datetime.now(timezone.utc).date() - latest_dt.date()).days > 180:
                flags.append({
                    "flag": "stale_attention",
                    "label": "Stale attention evidence",
                    "detail": f"Latest audience observation is {latest_dt.date().isoformat()} (older than 180 days).",
                })
        except Exception:
            pass
    future_note = (artist_state.get("forward_events"))
    if not future_exists(conn, artist_key):
        flags.append({
            "flag": "ticket_evidence_missing",
            "label": "Ticket evidence missing",
            "detail": "No provider-listed forward event with an advertised ticket range exists for this artist.",
        })
    base = (scenarios.get("base") or {}).get("outputs") or {}
    downside = (scenarios.get("downside") or {}).get("outputs") or {}
    gross = base.get("gross_ticket_revenue") or {}
    gross_v = _decode_or_none(gross.get("value"))
    if guarantee_dec is not None and gross_v is not None and gross_v > 0:
        ratio = guarantee_dec / gross_v
        if ratio > Decimal("0.60"):
            flags.append({
                "flag": "high_guarantee_ratio",
                "label": "Guarantee high vs base scenario gross",
                "detail": f"Guarantee is {ratio:.0%} of base-scenario gross ticket revenue.",
            })
    be_st = base.get("break_even_sell_through") or {}
    be_v = _decode_or_none(be_st.get("value"))
    if be_v is not None and be_v > Decimal("0.85"):
        flags.append({
            "flag": "excessive_breakeven",
            "label": "Excessive breakeven sell-through",
            "detail": f"Break-even sell-through is {be_v:.0%} (above 85%).",
        })
    return flags


def future_exists(conn, artist_key: str) -> bool:
    try:
        row = _one(
            conn,
            "SELECT 1 FROM future_events WHERE artist_key = ? AND ticket_price_min IS NOT NULL LIMIT 1",
            [artist_key],
        )
        return row is not None
    except Exception:
        return False


# ────────────────────────────────────────────────────────────────────────────
# COMPARABLES — explainable distance, never one hidden score
# ────────────────────────────────────────────────────────────────────────────

def build_comparables(conn, artist_key: str, market_key: str | None, limit: int = 8) -> list[dict[str, Any]]:
    """Comparable candidates with explicit WHY components:

      audience  — shared-listener edges (∈ pilot sample)
      markets   — shared market footprint
      festivals — shared festival bills
      footprint — comparable live density (observed-show band)
    """
    artist = _one(conn, "SELECT artist_key, name, tier, historical_event_count, market_count "
                        "FROM artists WHERE artist_key = ?", [artist_key])
    if artist is None:
        return []
    my_markets = {r["market_key"] for r in _rows(
        conn, "SELECT market_key FROM artist_markets WHERE artist_key = ?", [artist_key])}
    my_festivals = {r["festival_key"] for r in _rows(
        conn, "SELECT festival_key FROM festival_appearances WHERE artist_key = ? "
              "AND festival_key IS NOT NULL", [artist_key])}
    my_historical = artist.get("historical_event_count") or 0

    comp_map: dict[str, dict[str, Any]] = {}

    # audience component
    for row in _rows(
        conn, "SELECT peer_key, shared_listeners, jaccard FROM artist_peers "
              "WHERE subject_key = ? ORDER BY shared_listeners DESC NULLS LAST LIMIT 25",
        [artist_key],
    ):
        peer = row["peer_key"]
        entry = comp_map.setdefault(peer, {"audience": None, "markets": [], "festivals": [], "footprint": None})
        entry["audience"] = {"shared_listeners": row.get("shared_listeners"), "jaccard": row.get("jaccard")}

    # markets component (shared footprint)
    other_rows = _rows(
        conn,
        """SELECT artist_key, market_key FROM artist_markets
           WHERE market_key IN (SELECT market_key FROM artist_markets WHERE artist_key = ?)
             AND artist_key != ?
           LIMIT 3000""",
        [artist_key, artist_key],
    )
    for row in other_rows:
        entry = comp_map.setdefault(row["artist_key"], {"audience": None, "markets": [], "festivals": [], "footprint": None})
        entry["markets"].append(row["market_key"])

    # festivals component
    fest_rows = _rows(
        conn,
        """SELECT artist_key, festival_key FROM festival_appearances
           WHERE festival_key IN (SELECT festival_key FROM festival_appearances
                                  WHERE artist_key = ? AND festival_key IS NOT NULL)
             AND artist_key != ?
             AND festival_key IS NOT NULL
           LIMIT 3000""",
        [artist_key, artist_key],
    )
    for row in fest_rows:
        entry = comp_map.setdefault(row["artist_key"], {"audience": None, "markets": [], "festivals": [], "footprint": None})
        entry["festivals"].append(row["festival_key"])

    # footprint band: comparable live density (±50% of observed historical shows)
    if my_historical and my_historical > 0:
        lo = max(1, int(my_historical * 0.5))
        hi = int(my_historical * 1.5) + 1
        for row in _rows(
            conn,
            "SELECT artist_key, historical_event_count FROM artists "
            "WHERE historical_event_count BETWEEN ? AND ? AND artist_key != ? LIMIT 400",
            [lo, hi, artist_key],
        ):
            entry = comp_map.setdefault(row["artist_key"], {"audience": None, "markets": [], "festivals": [], "footprint": None})
            entry["footprint"] = {"historical_events": row.get("historical_event_count")}

    results: list[dict[str, Any]] = []
    for peer_key, parts in comp_map.items():
        name_row = _one(conn, "SELECT name, tier FROM artists WHERE artist_key = ?", [peer_key])
        if name_row is None:
            # Peer exists in the affinity edges but its artists row is absent —
            # fall back to the name carried by the peer edge itself.
            edge = _one(
                conn, "SELECT peer_name FROM artist_peers WHERE subject_key = ? AND peer_key = ?",
                [artist_key, peer_key],
            )
            name_row = ({"name": (edge or {}).get("peer_name") or peer_key,
                         "tier": None}, None)[0] if edge else {"name": None, "tier": None}
        if not name_row.get("name"):
            continue
        markets_shared = set(parts["markets"])
        festivals_shared = set(parts["festivals"])
        strength = 0
        components: list[str] = []
        if parts["audience"] is not None:
            strength += min(10, int(parts["audience"]["shared_listeners"] or 0))
            components.append(f"{parts['audience']['shared_listeners'] or 0} observed shared listeners")
        if markets_shared:
            strength += len(markets_shared) * 2
            components.append(f"{len(markets_shared)} shared market(s)")
        if festivals_shared:
            strength += len(festivals_shared) * 3
            components.append(f"{len(festivals_shared)} shared festival bill(s)")
        if parts["footprint"] is not None:
            components.append(
                f"comparable live footprint ({parts['footprint']['historical_events']} observed shows)"
            )
        if not components:
            continue
        # The only ordering key is the explicit component strength; the UI
        # always renders the components, never just the number.
        results.append({
            "artist_key": peer_key,
            "artist_name": name_row["name"],
            "tier": name_row["tier"],
            "components": components,
            "component_strength": strength,
            "same_market_now": bool(market_key and market_key in markets_shared),
            "audience": parts["audience"],
            "shared_markets": sorted(markets_shared)[:6],
            "shared_festivals": sorted(festivals_shared)[:6],
            "knowledge_time": None,
        })
    results.sort(key=lambda r: (-r["component_strength"], r["artist_name"]))
    # Prefer same-market comps among equal strength, then cap.
    results.sort(key=lambda r: (0 if r["same_market_now"] else 1,))
    return results[:limit]


# ────────────────────────────────────────────────────────────────────────────
# POINT-IN-TIME reconstruction — only evidence dated before the cutoff
# ────────────────────────────────────────────────────────────────────────────

def pit_features_at(conn, artist_key: str, cutoff: str | None) -> dict[str, Any]:
    """Reconstruct decision-time evidence strictly before ``cutoff``.

    Uses only dates/observations that were knowable before the decision:
      - live history with event_date < cutoff
      - markets with last play before cutoff
      - festival appearances dated before cutoff
      - attention observations with knowledge_time <= cutoff

    Never gates on retrieved_at; never uses post-cutoff rows. If cutoff is
    missing → UNKNOWN (PIT_INSUFFICIENT), never a silent empty reconstruction.
    """
    if not cutoff:
        return {
            "cutoff": None,
            "status": "PIT_INSUFFICIENT",
            "reason": "no decision cutoff supplied",
        }
    try:
        cutoff_dt = datetime.fromisoformat(str(cutoff)[:10])
    except Exception:
        return {"cutoff": None, "status": "PIT_INSUFFICIENT", "reason": "unparseable cutoff"}

    prior = _rows(
        conn,
        "SELECT COUNT(*) AS n FROM event_history WHERE artist_key = ? AND event_date < ?",
        [artist_key, cutoff_dt.date().isoformat()],
    )[0]["n"]
    prior_markets = _rows(
        conn,
        "SELECT COUNT(*) AS n FROM artist_markets WHERE artist_key = ? AND last_play_date < ?",
        [artist_key, cutoff_dt.date().isoformat()],
    )[0]["n"]
    prior_festivals = _rows(
        conn,
        "SELECT COUNT(*) AS n FROM festival_appearances WHERE artist_key = ? AND event_date < ?",
        [artist_key, cutoff_dt.date().isoformat()],
    )[0]["n"]
    attention = _rows(
        conn,
        "SELECT COUNT(*) AS n FROM attention_observations WHERE artist_key = ? AND knowledge_time <= ?",
        [artist_key, cutoff_dt.isoformat()],
    )[0]["n"]

    return {
        "cutoff": cutoff_dt.date().isoformat(),
        "status": "PIT_COMPLETE" if (prior or prior_markets or prior_festivals or attention) else "PIT_PARTIAL",
        "prior_live_events": prior,
        "prior_markets": prior_markets,
        "prior_festivals": prior_festivals,
        "prior_attention_observations": attention,
        "note": "reconstructed from serving rows dated strictly before the decision cutoff",
    }


# ────────────────────────────────────────────────────────────────────────────
# PRIVATE HISTORY — import, quarantine, retrospective
# ────────────────────────────────────────────────────────────────────────────

def preview_private_file(file_name: str, content: str) -> dict[str, Any]:
    """Parse CSV/TSV, map columns, scan PII. Values are previewed only so the
    buyer can confirm; nothing is imported or read into analytics here."""
    sep = "\t" if file_name.lower().endswith((".tsv", ".tab")) else ","
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines = [ln for ln in lines if ln.strip() != ""]
    if not lines:
        return {"error": "file is empty"}
    headers = [h.strip().lstrip("\ufeff") for h in lines[0].split(sep)]
    rows: list[list[str]] = []
    for ln in lines[1:]:
        cells = ln.split(sep)
        rows.append([c.strip() for c in cells])
    mapping = [m.to_dict() for m in design_partner.map_columns(headers)]
    pii = design_partner.scan_pii_columns(headers)
    preview_rows = rows[:5]
    return {
        "file_name": file_name,
        "header_count": len(headers),
        "row_count": len(rows),
        "headers": headers,
        "mapping": mapping,
        "pii": pii,
        "preview_rows": preview_rows,
        "auto_mapped": sum(1 for m in mapping if m["status"] == "AUTO_ACCEPTED"),
        "review_required": [m for m in mapping if m["status"] == "REVIEW_REQUIRED"],
        "unmapped": [m for m in mapping if m["status"] == "UNMAPPED"],
        "prohibited_pii": [h for h, s in pii.items() if s == "PROHIBITED"],
        "potential_pii": [h for h, s in pii.items() if s == "POTENTIAL_PII"],
    }


def import_private_shows(
    conn: Any,
    workspace: Any,
    *,
    file_name: str,
    headers: list[str],
    rows: list[list[str]],
    mapping: list[dict[str, Any]],
    forced_mapping: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Commit a previewed file into the private workspace tables.

    - PII columns (PROHIBITED / POTENTIAL_PII) are quarantined: values are
      never read into private_shows.
    - Only AUTO_ACCEPTED + explicitly forced mappings are ingested.
    - Every stored show is OBSERVED_PRIVATE; nothing touches public serving.
    """
    pii_statuses = design_partner.scan_pii_columns(headers)
    import_id = "imp_" + hashlib.sha256(
        (file_name + str(datetime.now(timezone.utc).timestamp())).encode()
    ).hexdigest()[:16]
    forced = forced_mapping or {}
    header_to_field: dict[str, str] = {}
    for m in mapping:
        resolved = m.get("canonical_field")
        if m["status"] == "AUTO_ACCEPTED" and resolved:
            header_to_field[m["header"]] = resolved
        elif m["header"] in forced and forced[m["header"]] in design_partner.CANONICAL_FIELDS:
            header_to_field[m["header"]] = forced[m["header"]]

    quarantined: list[dict[str, Any]] = []
    for header, status in pii_statuses.items():
        if status in ("PROHIBITED", "POTENTIAL_PII"):
            header_to_field.pop(header, None)
            quarantined.append({"column": header, "status": status})

    _FIELDS = [
        "import_id", "artist_name", "artist_key", "event_date", "venue_name",
        "venue_external_id", "market", "city", "state", "country",
        "venue_capacity", "usable_capacity", "ticket_capacity", "booking_date",
        "announcement_date", "onsale_date", "deal_type", "artist_guarantee",
        "artist_backend_pct", "artist_expenses", "tickets_sold", "paid_tickets",
        "comp_tickets", "refunded_tickets", "scanned_attendance", "paid_attendance",
        "reported_attendance", "ticket_gross", "ticket_net", "average_paid_ticket",
        "face_value_min", "face_value_max", "sold_out", "marketing_spend",
        "venue_cost", "production_cost", "labor_cost", "security_cost",
        "insurance_cost", "other_cost", "merch_revenue", "fnb_revenue",
        "parking_revenue", "vip_revenue", "sponsor_revenue", "other_revenue",
        "promoter_contribution", "settlement_gross", "settlement_net",
        "currency", "source_system", "notes",
    ]
    inserted = 0
    skipped = 0
    resolved_artists = 0
    for row_index, row in enumerate(rows):
        record: dict[str, str | None] = {}
        for idx, header in enumerate(headers):
            field = header_to_field.get(header)
            if not field:
                continue
            value = row[idx] if idx < len(row) else ""
            if value == "":
                value = None
            if field in design_partner._NUMERIC_FIELDS and value is not None:
                cleaned = value.replace(",", "").replace("$", "").replace("%", "")
                if cleaned == "":
                    value = None
            record[field] = value
        artist_name = record.get("artist_name")
        event_date = record.get("event_date")
        if not artist_name or not event_date:
            skipped += 1
            continue
        # Resolve to a serving identity where possible (first exact-ish hit).
        artist_key = None
        try:
            hits = artist_security.search_artists(conn, artist_name, limit=5)
            for hit in hits:
                if hit.get("name", "").lower() == artist_name.lower():
                    artist_key = hit.get("entity_id")
                    break
            if artist_key is None and hits:
                artist_key = hits[0].get("entity_id")
        except Exception:
            artist_key = None
        if artist_key:
            resolved_artists += 1
        record["artist_key"] = artist_key
        show_id = "show_" + hashlib.sha256(
            (f"{artist_name}|{event_date}|{record.get('venue_name') or ''}|{row_index}").encode()
        ).hexdigest()[:20]
        values = [show_id, import_id] + [record.get(c) for c in _FIELDS if c != "import_id"]
        placeholders = ",".join("?" * (len(_FIELDS) + 1))
        workspace.execute(
            f"INSERT OR REPLACE INTO private_shows (show_id, {', '.join(_FIELDS)}, provenance) "
            f"VALUES ({placeholders}, 'OBSERVED_PRIVATE')",
            values,
        )
        inserted += 1
    workspace.commit()

    workspace.execute(
        "INSERT OR REPLACE INTO private_imports "
        "(import_id, file_name, row_count, mapped_columns_json, pii_quarantine_json, sharing_policy) "
        "VALUES (?, ?, ?, ?, ?, 'PRIVATE_ONLY')",
        [import_id, file_name, inserted, json.dumps(header_to_field), json.dumps(quarantined)],
    )
    workspace.commit()
    return {
        "import_id": import_id,
        "rows_imported": inserted,
        "rows_skipped": skipped,
        "artists_resolved": resolved_artists,
        "pii_quarantined": quarantined,
        "sharing_policy": "PRIVATE_ONLY",
        "note": "private history never enters the public serving DuckDB",
    }


def retrospective(workspace: Any, conn: Any) -> dict[str, Any]:
    """Immediate retrospective value from imported private history.

    Distributions are OBSERVED_PRIVATE; public serving counts are labeled
    separately. Never merges the two into a single number.
    """
    shows = _rows(
        workspace,
        "SELECT * FROM private_shows ORDER BY event_date NULLS LAST, artist_name",
    )
    def num(row: dict[str, Any], key: str) -> Decimal | None:
        return _decode_or_none(row.get(key))
    sell_throughs: list[float] = []
    grosses: list[float] = []
    guarantees: list[float] = []
    contributions: list[float] = []
    by_artist: dict[str, int] = {}
    by_market: dict[str, int] = {}
    by_venue: dict[str, int] = {}
    for s in shows:
        cap = num(s, "ticket_capacity") or num(s, "usable_capacity") or num(s, "venue_capacity")
        sold = num(s, "tickets_sold") or num(s, "paid_tickets")
        if cap and sold and Decimal(cap) > 0:
            sell_throughs.append(float(sold / Decimal(cap)))
        gross = num(s, "ticket_gross") or num(s, "settlement_gross")
        if gross is not None:
            grosses.append(float(gross))
        gte = num(s, "artist_guarantee")
        if gte is not None:
            guarantees.append(float(gte))
        contrib = num(s, "promoter_contribution")
        if contrib is not None:
            contributions.append(float(contrib))
        by_artist[s.get("artist_name") or "UNKNOWN"] = by_artist.get(s.get("artist_name") or "UNKNOWN", 0) + 1
        by_market[s.get("market") or "UNKNOWN"] = by_market.get(s.get("market") or "UNKNOWN", 0) + 1
        by_venue[s.get("venue_name") or "UNKNOWN"] = by_venue.get(s.get("venue_name") or "UNKNOWN", 0) + 1

    def dist(vals: list[float]) -> dict[str, Any]:
        if not vals:
            return {"count": 0, "status": "UNKNOWN"}
        vals_sorted = sorted(vals)
        n = len(vals_sorted)
        q = lambda p: vals_sorted[min(n - 1, int(n * p))]
        return {
            "count": n,
            "min": round(vals_sorted[0], 2),
            "p25": round(q(0.25), 2),
            "median": round(q(0.5), 2),
            "p75": round(q(0.75), 2),
            "max": round(vals_sorted[-1], 2),
            "status": "OBSERVED_PRIVATE",
        }

    return {
        "total_shows": len(shows),
        "status": "OBSERVED_PRIVATE" if shows else "NO_PRIVATE_HISTORY",
        "show_ids": [s["show_id"] for s in shows] if shows else [],
        "distributions": {
            "sell_through": dist(sell_throughs),
            "gross": dist(grosses),
            "guarantee": dist(guarantees),
            "contribution": dist(contributions),
        },
        "top_artists": sorted(by_artist.items(), key=lambda kv: -kv[1])[:10],
        "top_markets": sorted(by_market.items(), key=lambda kv: -kv[1])[:10],
        "top_venues": sorted(by_venue.items(), key=lambda kv: -kv[1])[:10],
    }


def pit_retrospective(workspace: Any, conn: Any, show_id: str) -> dict[str, Any] | None:
    """Open one historical show: decision-time evidence vs realized outcome."""
    show = _one(workspace, "SELECT * FROM private_shows WHERE show_id = ?", [show_id])
    if show is None:
        return None
    cutoff = show.get("booking_date") or show.get("announcement_date") or show.get("onsale_date")
    artist_key = show.get("artist_key")
    pit = {"status": "PIT_INSUFFICIENT", "reason": "artist not resolved to a serving identity (no PIT public evidence)"}
    if artist_key:
        pit = pit_features_at(conn, artist_key, cutoff)
    realized: list[tuple[str, Any]] = []
    fields = [
        ("tickets_sold", "Tickets sold"), ("paid_attendance", "Paid attendance"),
        ("scanned_attendance", "Scanned attendance"), ("ticket_gross", "Ticket gross"),
        ("average_paid_ticket", "Average ticket"), ("artist_guarantee", "Guarantee"),
        ("artist_settlement", "Artist settlement"), ("promoter_contribution", "Promoter contribution"),
        ("marketing_spend", "Marketing"), ("production_cost", "Production"),
        ("other_cost", "Other expenses"), ("merch_revenue", "Ancillary revenue"),
    ]
    for field, label in fields:
        raw = show.get(field)
        if raw is not None and str(raw).strip() != "":
            realized.append({"label": label, "value": str(raw), "provenance": "OBSERVED_PRIVATE"})
    return {
        "show_id": show_id,
        "artist_name": show.get("artist_name"),
        "event_date": show.get("event_date"),
        "venue": show.get("venue_name"),
        "market": show.get("market"),
        "decision_cutoff": cutoff,
        "pit": pit,
        "realized_outcome": realized,
        "note": "no causality asserted; PIT evidence and outcome are shown side by side",
    }


# ────────────────────────────────────────────────────────────────────────────
# DECISION SNAPSHOTS + CLOSE-OUT
# ────────────────────────────────────────────────────────────────────────────

def save_decision_snapshot(
    workspace: Any,
    *,
    artist_key: str,
    artist_name: str,
    market_key: str | None,
    venue: str | None,
    event_date: str | None,
    inputs: dict[str, Any],
    brief: dict[str, Any],
    status: str = "RESEARCHING",
    notes: str = "",
) -> dict[str, Any]:
    import uuid
    if status not in DECISION_STATUSES:
        status = "RESEARCHING"
    snapshot_id = "snap_" + uuid.uuid4().hex[:16]
    workspace.execute(
        """INSERT INTO decision_snapshots
           (snapshot_id, artist_key, artist_name, market_key, venue, event_date, status,
            assumption_set_json, brief_json, evidence_generation, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            snapshot_id, artist_key, artist_name, market_key, venue, event_date, status,
            json.dumps({k: v for k, v in inputs.items() if v not in (None, "")}),
            json.dumps(brief, default=str),
            brief.get("generation"),
            notes,
        ],
    )
    workspace.commit()
    return {"snapshot_id": snapshot_id, "status": status, "generation": brief.get("generation")}


def list_decision_snapshots(workspace: Any) -> list[dict[str, Any]]:
    return _rows(
        workspace,
        "SELECT snapshot_id, artist_key, artist_name, market_key, venue, event_date, status, "
        "       evidence_generation, notes, created_at "
        "FROM decision_snapshots ORDER BY created_at DESC",
    )


def get_decision_snapshot(workspace: Any, snapshot_id: str) -> dict[str, Any] | None:
    row = _one(workspace, "SELECT * FROM decision_snapshots WHERE snapshot_id = ?", [snapshot_id])
    if row is None:
        return None
    try:
        row["assumption_set"] = json.loads(row["assumption_set_json"] or "{}")
    except Exception:
        row["assumption_set"] = {}
    try:
        row["brief"] = json.loads(row["brief_json"] or "{}")
    except Exception:
        row["brief"] = {}
    return row


def update_decision_status(workspace: Any, snapshot_id: str, status: str) -> dict[str, Any]:
    if status not in DECISION_STATUSES:
        raise ValueError(f"invalid decision status {status!r}")
    workspace.execute(
        "UPDATE decision_snapshots SET status = ? WHERE snapshot_id = ?",
        [status, snapshot_id],
    )
    workspace.commit()
    return {"snapshot_id": snapshot_id, "status": status}


def close_out_show(workspace: Any, snapshot_id: str, actuals: dict[str, Any]) -> dict[str, Any]:
    """Attach realized outcomes to a saved decision as OBSERVED_PRIVATE."""
    snap = get_decision_snapshot(workspace, snapshot_id)
    if snap is None:
        raise ValueError("snapshot not found")
    import uuid
    vault_id = "vault_" + uuid.uuid4().hex[:16]
    show_id = "show_" + uuid.uuid4().hex[:16]
    # Outcome vault entry — hidden until the buyer reveals it for their own
    # scoring; never exposed in public serving data.
    workspace.execute(
        "INSERT INTO outcome_vault (vault_id, show_id, artist_key, event_date, observations_json, revealed) "
        "VALUES (?, ?, ?, ?, ?, 0)",
        [
            vault_id, show_id, snap.get("artist_key"), snap.get("event_date"),
            json.dumps({"snapshot_id": snapshot_id, "actuals": actuals}, default=str),
        ],
    )
    # Also store as a private show so it feeds the retrospective + readiness.
    fields = [
        ("paid_tickets", "tickets_sold"), ("scanned_attendance", "scanned_attendance"),
        ("paid_attendance", "paid_attendance"), ("ticket_gross", "ticket_gross"),
        ("settlement_gross", "settlement_gross"), ("artist_settlement", "settlement_net"),
        ("marketing_spend", "marketing_spend"), ("production_cost", "production_cost"),
        ("other_cost", "other_cost"), ("promoter_contribution", "promoter_contribution"),
    ]
    record: dict[str, Any] = {
        "artist_name": snap.get("artist_name"),
        "artist_key": snap.get("artist_key"),
        "event_date": snap.get("event_date"),
        "venue_name": snap.get("venue"),
        "market": snap.get("market_key"),
    }
    for src, dst in fields:
        value = actuals.get(src)
        if value not in (None, ""):
            record[dst] = str(value)
    cols = list(record.keys())
    vals = [record[c] for c in cols]
    workspace.execute(
        f"INSERT OR REPLACE INTO private_shows (show_id, {', '.join(cols)}, provenance) "
        f"VALUES (?, {','.join('?' * len(cols))}, 'OBSERVED_PRIVATE')",
        [show_id] + vals,
    )
    workspace.commit()
    return {"vault_id": vault_id, "show_id": show_id, "snapshot_id": snapshot_id, "provenance": "OBSERVED_PRIVATE"}


def outcome_vault_summary(workspace: Any) -> dict[str, Any]:
    row = _one(workspace, "SELECT COUNT(*) AS n FROM outcome_vault")
    hidden = _one(workspace, "SELECT COUNT(*) AS n FROM outcome_vault WHERE revealed = 0")
    return {
        "entries": row["n"] if row else 0,
        "hidden": hidden["n"] if hidden else 0,
        "privacy": "PRIVATE_ONLY — never in public serving DuckDB or R2 terminal generations",
    }


# ────────────────────────────────────────────────────────────────────────────
# MONITOR — what changed since the last look
# ────────────────────────────────────────────────────────────────────────────

def monitor_changes(conn: Any, workspace: Any, watch_keys: list[str]) -> dict[str, Any]:
    """For each watched artist: current serving counts vs the counts recorded
    the last time the buyer looked. First look records a baseline."""
    changes: list[dict[str, Any]] = []
    for artist_key in watch_keys:
        if not artist_key:
            continue
        name = _one(conn, "SELECT name FROM artists WHERE artist_key = ?", [artist_key])
        if name is None:
            continue
        current = _serving_counts(conn, artist_key)
        baseline = _one(workspace, "SELECT * FROM monitor_baselines WHERE artist_key = ?", [artist_key])
        deltas: list[dict[str, Any]] = []
        if baseline is None:
            workspace.execute(
                "INSERT OR REPLACE INTO monitor_baselines "
                "(artist_key, future_events, markets, festivals, attention, seen_at) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                [artist_key, current["future_events"], current["markets"], current["festivals"], current["attention"]],
            )
            workspace.commit()
            deltas = [{
                "metric": "baseline",
                "detail": "first look — baseline for future change tracking",
                "before": None, "after": current["future_events"],
                "source": "public serving generation",
            }]
        else:
            for metric in ("future_events", "markets", "festivals", "attention"):
                before = int(baseline.get(metric) or 0)
                after = int(current.get(metric) or 0)
                if after != before:
                    deltas.append({
                        "metric": metric,
                        "detail": f"{after - before:+d} {'new ' if after > before else ''}{metric.replace('_', ' ')}",
                        "before": before, "after": after,
                        "source": "public serving generation",
                    })
            workspace.execute(
                "UPDATE monitor_baselines SET future_events = ?, markets = ?, festivals = ?, attention = ?, seen_at = CURRENT_TIMESTAMP "
                "WHERE artist_key = ?",
                [current["future_events"], current["markets"], current["festivals"], current["attention"], artist_key],
            )
            workspace.commit()
        changes.append({
            "artist_key": artist_key,
            "artist_name": name["name"],
            "current": current,
            "changes": deltas,
        })
    return {"watch_count": len(changes), "artists": changes}


def _serving_counts(conn: Any, artist_key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for short_key, table in (
        ("future_events", "future_events"),
        ("markets", "artist_markets"),
        ("festivals", "festival_appearances"),
        ("attention", "attention_observations"),
    ):
        try:
            counts[short_key] = int(_one(
                conn, f"SELECT COUNT(*) AS n FROM {table} WHERE artist_key = ?", [artist_key]
            )["n"])
        except Exception:
            counts[short_key] = 0
    return counts


# ────────────────────────────────────────────────────────────────────────────
# MODEL READINESS — gate, not training
# ────────────────────────────────────────────────────────────────────────────

def model_readiness(workspace: Any, conn: Any) -> dict[str, Any]:
    shows = _rows(workspace, "SELECT * FROM private_shows")
    n = len(shows)
    with_cutoff = 0
    with_pit = 0
    with_tickets = 0
    with_gross = 0
    with_guarantee = 0
    with_expenses = 0
    with_contribution = 0
    markets: set[str] = set()
    venues: set[str] = set()
    artists: set[str] = set()
    dates: list[str] = []
    eligible_oos = 0
    for s in shows:
        cutoff = s.get("booking_date") or s.get("announcement_date") or s.get("onsale_date")
        if cutoff:
            with_cutoff += 1
        if s.get("artist_key"):
            with_pit += 1
        if _decode_or_none(s.get("tickets_sold")) is not None or _decode_or_none(s.get("paid_tickets")) is not None:
            with_tickets += 1
        if _decode_or_none(s.get("ticket_gross")) is not None:
            with_gross += 1
        if _decode_or_none(s.get("artist_guarantee")) is not None:
            with_guarantee += 1
        if any(_decode_or_none(s.get(k)) is not None for k in
               ("marketing_spend", "production_cost", "venue_cost", "labor_cost", "other_cost")):
            with_expenses += 1
        if _decode_or_none(s.get("promoter_contribution")) is not None:
            with_contribution += 1
        markets.add(s.get("market") or "UNKNOWN")
        venues.add(s.get("venue_name") or "UNKNOWN")
        artists.add(s.get("artist_name") or "UNKNOWN")
        if s.get("event_date"):
            dates.append(str(s["event_date"])[:10])
        if s.get("artist_key") and cutoff and (
            _decode_or_none(s.get("tickets_sold")) is not None or
            _decode_or_none(s.get("paid_tickets")) is not None
        ):
            eligible_oos += 1
    time_span = None
    if dates:
        time_span = {"start": min(dates), "end": max(dates)}
    verdict = "no model" if n < 200 else ("exploratory_baselines_only" if n < 1000 else "begin_serious_oos_evaluation")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "private_settled_shows": n,
        "with_booking_cutoff": with_cutoff,
        "with_valid_pit_reconstruction": with_pit,
        "with_tickets_sold": with_tickets,
        "with_gross": with_gross,
        "with_guarantee": with_guarantee,
        "with_expenses": with_expenses,
        "with_profit_or_contribution": with_contribution,
        "markets": len(markets),
        "venues": len(venues),
        "artists": len(artists),
        "time_span": time_span,
        "eligible_oos_rows": eligible_oos,
        "verdict": verdict,
        "progression": {
            "<200 useful outcomes": "no model",
            "200–1,000": "exploratory baselines only",
            "1,000+ diverse settled outcomes": "begin serious OOS evaluation",
        },
        "note": "no predictive model is trained in this milestone",
    }