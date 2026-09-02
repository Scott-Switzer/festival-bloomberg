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
import re
from dataclasses import dataclass, fields
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

from ..economics import design_partner, partner_import
from ..economics.design_partner import (
    AUTO_ACCEPTED,
    PROHIBITED,
    POTENTIAL_PII,
    SAFE,
)
from ..economics.repository import EconomicsRepository
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
    identity_status VARCHAR,
    identity_detail VARCHAR,
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
ALTER TABLE private_shows ADD COLUMN IF NOT EXISTS identity_status VARCHAR;
ALTER TABLE private_shows ADD COLUMN IF NOT EXISTS identity_detail VARCHAR;
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


SYSTEM_TEMPLATES: dict[str, dict[str, str]] = {
    # Conservative / moderate / aggressive sell-through assumption sets.
    # Applying a template is an explicit buyer action; every populated field is
    # reported as SYSTEM_TEMPLATE_ASSUMPTION until the buyer accepts it.
    "CONSERVATIVE": {"downside": "0.25", "base": "0.45", "upside": "0.65"},
    "MODERATE": {"downside": "0.35", "base": "0.55", "upside": "0.75"},
    "AGGRESSIVE": {"downside": "0.45", "base": "0.65", "upside": "0.85"},
}


def build_scenario(inputs: dict[str, Any]) -> ShowEconomicsScenario:
    """Construct the deterministic economics scenario from a buyer's inputs.

    Contract:
      BLANK                  = UNKNOWN               (TypedInput.unknown)
      EXPLICIT "0"           = USER_ASSUMPTION ZERO
      SYSTEM template values = SYSTEM_TEMPLATE_ASSUMPTION (tracked separately
      via assumption_provenance(); the engine itself only knows UNKNOWN vs
      USER_ASSUMPTION because Provenance has no template member).

    There are no hidden defaults. A missing deal type, backend basis, tax rate,
    or deduction stays UNKNOWN and propagates as UNKNOWN outputs.
    """
    usable = _decode_or_none(inputs.get("usable_capacity"))
    sellable = _decode_or_none(inputs.get("sellable_capacity"))
    sellable_derived = sellable is None and usable is not None
    if sellable_derived:
        sellable = usable
    atp = _decode_or_none(inputs.get("average_ticket_price"))
    sell_through = _decode_or_none(inputs.get("sell_through"))
    guarantee = _decode_or_none(inputs.get("guarantee"))
    backend_pct = _decode_or_none(inputs.get("backend_percentage"))

    def assumption(value: Decimal | None) -> TypedInput:
        if value is None:
            return TypedInput.unknown()
        return TypedInput.assumption(value)

    def assumption_str(value: str | None, enum_type) -> TypedInput:
        if not value or not value.strip():
            return TypedInput.unknown()
        cleaned = value.strip().upper()
        if cleaned not in {e.value for e in enum_type}:
            return TypedInput.unknown()  # invalid enum value stays UNKNOWN, never coerced
        return TypedInput.assumption(cleaned)

    def money_field(key: str) -> TypedInput:
        return assumption(_decode_or_none(inputs.get(key)))

    deal_type_raw = (inputs.get("deal_type") or "").strip().upper()
    basis_raw = (inputs.get("backend_basis") or "").strip().upper()

    approved_names_raw = inputs.get("approved_expense_names")
    approved_names: tuple[str, ...] | None = None
    if isinstance(approved_names_raw, (list, tuple)) and approved_names_raw:
        approved_names = tuple(str(n).strip().lower() for n in approved_names_raw if str(n).strip())
    if isinstance(approved_names_raw, str) and approved_names_raw.strip():
        candidate = tuple(
            n.strip().lower() for n in approved_names_raw.split(",") if n.strip()
        )
        valid = {f.name for f in fields(FixedCosts)}
        if candidate and all(n in valid for n in candidate):
            approved_names = candidate

    scenario = ShowEconomicsScenario(
        currency=TypedInput.assumption("USD"),
        usable_capacity=assumption(usable),
        sellable_capacity=assumption(sellable),
        ticket_scale=(
            TicketTier(
                name="general",
                price=assumption(atp),
                quantity=assumption(sellable),
            ),
        ),
        sell_through=assumption(sell_through),
        ticketing_deduction_per_paid_ticket=money_field("ticketing_deduction"),
        tax_rate_on_gross=money_field("tax_rate"),
        deal=DealDefinition(
            deal_type=assumption_str(deal_type_raw, DealType),
            guarantee=assumption(guarantee),
            backend_percentage=assumption(backend_pct),
            backend_basis=assumption_str(basis_raw, BackendBasis),
            artist_expenses=money_field("artist_expenses"),
            approved_expense_names=(
                TypedInput.assumption(approved_names) if approved_names else TypedInput.unknown()
            ),
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


def assumption_provenance(inputs: dict[str, Any], *, template_applied: dict[str, str] | None = None) -> dict[str, str]:
    """Per-input provenance for the economics surface.

    BLANK -> UNKNOWN; explicit value -> USER_ASSUMPTION; value supplied by an
    accepted template -> SYSTEM_TEMPLATE_ASSUMPTION. sellable derived from
    usable is DERIVED.
    """
    template_applied = {k: v for k, v in (template_applied or {}).items()}
    # Normalize template keys: sell_through_base -> sell_through, etc.
    for label, field in (("base", "sell_through"), ("downside", "sell_through_down"), ("upside", "sell_through_up")):
        if f"sell_through_{label}" in template_applied:
            template_applied.setdefault(field, template_applied.pop(f"sell_through_{label}"))
    provenance: dict[str, str] = {}

    _ENUM_FIELDS = {"deal_type": {e.value for e in DealType}, "backend_basis": {e.value for e in BackendBasis}}

    def mark(field: str, raw_value) -> None:
        if field in template_applied:
            provenance[field] = "SYSTEM_TEMPLATE_ASSUMPTION"
        elif raw_value is None or str(raw_value).strip() == "":
            provenance[field] = "UNKNOWN"
        elif field in _ENUM_FIELDS and str(raw_value).strip().upper() not in _ENUM_FIELDS[field]:
            provenance[field] = "UNKNOWN"  # invalid enum stays UNKNOWN, never coerced
        else:
            provenance[field] = "USER_ASSUMPTION"

    for key in (
        "usable_capacity", "sellable_capacity", "average_ticket_price",
        "guarantee", "backend_percentage", "deal_type", "backend_basis",
        "artist_expenses", "approved_expense_names", "ticketing_deduction",
        "tax_rate", "cost_marketing", "cost_production", "cost_venue",
        "cost_labor", "cost_insurance", "cost_other", "ancillary_revenue",
        "sponsorship", "sell_through", "sell_through_down", "sell_through_up",
    ):
        mark(key, inputs.get(key))
    usable_raw = inputs.get("usable_capacity")
    sellable_raw = inputs.get("sellable_capacity")
    if (sellable_raw is None or str(sellable_raw).strip() == "") and usable_raw is not None and str(usable_raw).strip() != "":
        provenance["sellable_capacity"] = "DERIVED"
    return provenance


def scenario_sets(inputs: dict[str, Any]) -> tuple[dict[str, ShowEconomicsScenario], dict[str, str]]:
    """downside / base / upside scenarios from EXPLICIT inputs only.

    Nothing is filled in implicitly. A scenario is only created when its
    sell-through rate is entered by the buyer, or when the buyer explicitly
    applies and accepts one of the SYSTEM_TEMPLATES (each populated field is
    then tracked as SYSTEM_TEMPLATE_ASSUMPTION). Labels remain
    "USER-DEFINED SCENARIO" — never probability.

    Returns (scenarios, template_applied_fields).
    """
    template_applied: dict[str, str] = {}
    rates: dict[str, Decimal | None] = {
        "base": _decode_or_none(inputs.get("sell_through")),
        "downside": _decode_or_none(inputs.get("sell_through_down")),
        "upside": _decode_or_none(inputs.get("sell_through_up")),
    }
    template_name = (inputs.get("template") or "").strip().upper()
    accept = str(inputs.get("accept_template") or "").strip().lower() in ("1", "true", "yes", "accept")
    if template_name in SYSTEM_TEMPLATES and accept:
        for label, raw in SYSTEM_TEMPLATES[template_name].items():
            template_rate = Decimal(raw)
            if rates[label] is None:
                rates[label] = template_rate
            # A field that still equals the template value keeps
            # SYSTEM_TEMPLATE_ASSUMPTION provenance; if the buyer edited it,
            # it becomes their own USER_ASSUMPTION.
            if rates[label] == template_rate:
                template_applied[f"sell_through_{label}"] = template_name
    scenarios: dict[str, ShowEconomicsScenario] = {}
    for label in ("downside", "base", "upside"):
        if rates[label] is not None:
            scenarios[label] = _with_sell_through(inputs, rates[label])
    return scenarios, template_applied


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

    # ── E. economics (deterministic scenario math; strict UNKNOWN contract) ──
    scenario_map, template_applied = scenario_sets(inputs)
    scenarios: dict[str, Any] = {}
    for label, scenario in scenario_map.items():
        try:
            evaluation = evaluate(scenario)
            scenarios[label] = {
                "label": "USER-DEFINED SCENARIO",
                "sell_through_assumed": (
                    str(inputs.get("sell_through")) if label == "base"
                    else str(inputs.get(f"sell_through_{label}"))
                ),
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
        "comparables_meta": COMPARABLE_ORDERING_V1,
        "economics": scenarios,
        "economics_input_provenance": assumption_provenance(inputs, template_applied=template_applied),
        "economics_template": template_applied or None,
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

# Deterministic component weights used ONLY for ordering; the UI always renders
# the components themselves. Never learned, never a prediction.
COMPARABLE_ORDERING_V1 = {
    "heuristic": "HEURISTIC_ORDERING_V1",
    "weights": {
        "shared_listeners": {"cap": 10, "per_listener": 1},
        "shared_markets": {"per_market": 2},
        "shared_festival_bills": {"per_festival": 3},
        "footprint_band": {"weight": 0},
        "same_market_preference": {"tiebreak": 1},
    },
    "note": "deterministic sum of explicit evidence components for ordering only; not a learned or calibrated score",
}


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
            "ordering_heuristic": "HEURISTIC_ORDERING_V1",
            "knowledge_time": None,
        })
    results.sort(key=lambda r: (-r["component_strength"], r["artist_name"]))
    # Deterministic tiebreak: same-market comps first, then name.
    results.sort(key=lambda r: (0 if r["same_market_now"] else 1, r["artist_name"]))
    return results[:limit]


# ────────────────────────────────────────────────────────────────────────────
# POINT-IN-TIME reconstruction — only evidence dated before the cutoff
# ────────────────────────────────────────────────────────────────────────────

# Point-in-time doctrine:
#   occurrence_time != knowledge_time
#   historical occurrence != historical knowability
#   a row is admissible for decision-time reconstruction only if its own
#   knowledge_time (when the observer/source knew about it) is <= the decision
#   cutoff. `retrieved_at`, build dates, and event dates are never entry
#   tickets. Rows without a knowledge_time are NOT admissible — reported
#   honestly as excluded, never silently admitted.
_PIT_FAMILIES = (
    ("live_history", "event_history", "event_date"),
    ("markets", "artist_markets", "last_play_date"),
    ("festivals", "festival_appearances", "performance_date"),
)


def _pit_family(conn, artist_key: str, family: str, table: str, occurrence_col: str, cutoff_date: str) -> dict[str, Any]:
    occurrence = f"(COALESCE({occurrence_col}, event_date))" if table == "festival_appearances" else occurrence_col
    try:
        occurred = int(_one(
            conn, f"SELECT COUNT(*) AS n FROM {table} WHERE artist_key = ? AND {occurrence} < ?",
            [artist_key, cutoff_date],
        )["n"])
        admissible = int(_one(
            conn, f"SELECT COUNT(*) AS n FROM {table} WHERE artist_key = ? AND {occurrence} < ? "
                  f"AND knowledge_time IS NOT NULL AND knowledge_time <= ?",
            [artist_key, cutoff_date, cutoff_date],
        )["n"])
        excluded_no_kt = int(_one(
            conn, f"SELECT COUNT(*) AS n FROM {table} WHERE artist_key = ? AND {occurrence} < ? "
                  f"AND knowledge_time IS NULL",
            [artist_key, cutoff_date],
        )["n"])
        excluded_after = int(_one(
            conn, f"SELECT COUNT(*) AS n FROM {table} WHERE artist_key = ? AND {occurrence} < ? "
                  f"AND knowledge_time IS NOT NULL AND knowledge_time > ?",
            [artist_key, cutoff_date, cutoff_date],
        )["n"])
    except Exception as exc:
        occurred = admissible = excluded_no_kt = excluded_after = 0
        return {"family": family, "status": "UNKNOWN", "detail": f"query failed: {exc}"}
    if not occurred:
        status = "EMPTY"
    elif admissible:
        status = "ADMISSIBLE"
    else:
        status = "PIT_INSUFFICIENT"
    return {
        "family": family,
        "occurred_before_cutoff": occurred,
        "admissible": admissible,
        "excluded_missing_knowledge_time": excluded_no_kt,
        "excluded_knowledge_after_cutoff": excluded_after,
        "status": status,
        "detail": (
            "rows occurred before cutoff but no leakage-safe knowledge_time evidence exists; "
            "not admissible for point-in-time reconstruction"
            if status == "PIT_INSUFFICIENT" else None
        ),
    }


def pit_features_at(conn, artist_key: str, cutoff: str | None) -> dict[str, Any]:
    """Leakage-safe decision-time reconstruction.

    Each family admits ONLY rows whose own knowledge_time <= cutoff. Rows that
    merely occurred before the cutoff (missing/after-cutoff knowledge_time) are
    counted and explicitly excluded. If any admissible row exists the
    reconstruction is PIT_COMPLETE (each family reports its own status); with
    occurrences but zero admissible rows the result is PIT_INSUFFICIENT — never
    a silent empty reconstruction.
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
    cutoff_date = cutoff_dt.date().isoformat()

    families = [
        _pit_family(conn, artist_key, family, table, col, cutoff_date)
        for (family, table, col) in _PIT_FAMILIES
    ]
    attention = {
        "family": "attention",
        "occurred_before_cutoff": None,
        "admissible": 0,
        "excluded_missing_knowledge_time": 0,
        "excluded_knowledge_after_cutoff": 0,
        "status": "EMPTY",
    }
    try:
        att = _rows(
            conn,
            "SELECT COUNT(*) AS n FROM attention_observations WHERE artist_key = ? AND knowledge_time <= ?",
            [artist_key, cutoff_date],
        )[0]["n"]
        att_missing = _rows(
            conn,
            "SELECT COUNT(*) AS n FROM attention_observations WHERE artist_key = ? AND knowledge_time IS NULL",
            [artist_key],
        )[0]["n"]
        att_after = _rows(
            conn,
            "SELECT COUNT(*) AS n FROM attention_observations WHERE artist_key = ? AND knowledge_time IS NOT NULL AND knowledge_time > ?",
            [artist_key, cutoff_date],
        )[0]["n"]
        attention["admissible"] = int(att)
        attention["excluded_missing_knowledge_time"] = int(att_missing)
        attention["excluded_knowledge_after_cutoff"] = int(att_after)
        attention["status"] = "ADMISSIBLE" if att else ("PIT_INSUFFICIENT" if (att_missing or att_after) else "EMPTY")
        if attention["status"] == "PIT_INSUFFICIENT":
            attention["detail"] = "attention observations exist but none has leakage-safe knowledge_time <= cutoff"
    except Exception as exc:
        attention["status"] = "UNKNOWN"
        attention["detail"] = f"query failed: {exc}"
    families.append(attention)

    any_admissible = any(f.get("admissible") for f in families)
    any_occurrence = any((f.get("occurred_before_cutoff") or 0) for f in families[:3]) or bool(
        attention.get("admissible") or attention.get("excluded_missing_knowledge_time")
        or attention.get("excluded_knowledge_after_cutoff")
    )
    if any_admissible:
        status = "PIT_COMPLETE"
        reason = None
    elif any_occurrence:
        status = "PIT_INSUFFICIENT"
        reason = (
            "events occurred before the cutoff but no leakage-safe knowledge_time evidence exists; "
            "rows without admissible knowledge time are NOT included in the reconstruction"
        )
    else:
        status = "PIT_INSUFFICIENT"
        reason = "no evidence that the artist occurred before the cutoff is in the serving data"

    return {
        "cutoff": cutoff_date,
        "status": status,
        "reason": reason,
        "families": families,
        "prior_live_events": next(f["admissible"] for f in families if f["family"] == "live_history"),
        "prior_markets": next(f["admissible"] for f in families if f["family"] == "markets"),
        "prior_festivals": next(f["admissible"] for f in families if f["family"] == "festivals"),
        "prior_attention_observations": attention["admissible"],
        "note": "admissible only where knowledge_time <= decision cutoff; occurrence without knowledge is never admitted",
    }


# ────────────────────────────────────────────────────────────────────────────
# PRIVATE HISTORY — import, quarantine, retrospective
# ────────────────────────────────────────────────────────────────────────────

def _parse_tabular(file_name: str, content: str | bytes) -> tuple[list[str], list[dict[str, str]]]:
    """Robust CSV/TSV/XLSX parsing (proper CSV parser, quotes/BOM/multiline
    handled; openpyxl for XLSX). Returns headers + dict rows."""
    import csv as _csv
    import io as _io
    import tempfile as _tmp

    suffix = file_name.lower().rsplit(".", 1)[-1] if "." in file_name else ""
    if suffix == "xlsx":
        raw = content if isinstance(content, bytes) else content.encode("utf-8")
        with _tmp.NamedTemporaryFile(suffix=".xlsx", delete=False) as tf:
            tf.write(raw)
            tmp_path = tf.name
        try:
            return partner_import.read_tabular(tmp_path)
        finally:
            try:
                import os
                os.unlink(tmp_path)
            except OSError:
                pass
    text = content if isinstance(content, str) else content.decode("utf-8-sig", errors="replace")
    text = text.lstrip("\ufeff")
    delimiter = "\t" if suffix in ("tsv", "tab") else None
    stream = _io.StringIO(text)
    sample = stream.read(8192)
    stream.seek(0)
    if delimiter is None:
        try:
            delimiter = _csv.Sniffer().sniff(sample, delimiters=",\t;").delimiter
        except _csv.Error:
            delimiter = ","
    reader = _csv.DictReader(stream, delimiter=delimiter)
    headers = [h.strip().lstrip("\ufeff") for h in (reader.fieldnames or [])]
    rows: list[dict[str, str]] = []
    for row in reader:
        cleaned = {h: (row.get(h) or "").strip() for h in headers}
        if any(v != "" for v in cleaned.values()):
            rows.append(cleaned)
    return headers, rows


def preview_private_file(file_name: str, content: str | bytes) -> dict[str, Any]:
    """Parse CSV/TSV/XLSX, map columns, scan PII and return a redacted preview.

    PII values (PROHIBITED / POTENTIAL_PII) are REDACTED before any value is
    returned to the browser — only the column name, classification and reason
    are preserved. Nothing is imported or read into analytics here.
    """
    try:
        headers, rows = _parse_tabular(file_name, content)
    except Exception as exc:
        return {"error": f"could not parse file: {exc}"}
    if not headers or not rows:
        return {"error": "file is empty or has no data rows"}
    mapping = [m.to_dict() for m in design_partner.map_columns(headers)]
    pii = design_partner.scan_pii_columns(headers)
    pii_indices = {i for i, h in enumerate(headers) if pii.get(h) in (PROHIBITED, POTENTIAL_PII)}
    # Redact PII VALUES before they ever reach the browser.
    preview_rows: list[dict[str, str]] = []
    for row in rows[:5]:
        preview_rows.append({
            h: ("[REDACTED PII]" if i in pii_indices else row.get(h, ""))
            for i, h in enumerate(headers)
        })
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
        "pii_redacted": sorted(pii_indices),
        "note": "PII values are redacted in previews and never ingested",
    }


def _norm_artist_name(value: str) -> str:
    """Punctuation-robust normalization (commas/periods/etc. never block a
    VERIFIED_EXACT match): lowercase, non-alphanumeric -> spaces."""
    return re.sub(r"[^a-z0-9]+\s*", " ", value.lower()).strip()


def _resolve_artist_fail_closed(conn: Any, artist_name: str) -> tuple[str | None, str, str]:
    """Fail-closed identity resolution.

    Only a single exact normalized-name/search-term match attaches an
    artist_key (VERIFIED_EXACT). Any ambiguity, near match, or miss leaves the
    key unset and the row in REVIEW_REQUIRED / CONFLICT / UNRESOLVED.
    Outcome data is never silently attached to a guessed identity.
    """
    norm = _norm_artist_name(artist_name)
    if not norm:
        return None, "UNRESOLVED", "blank or unnormalizable artist name"
    try:
        exact = _rows(
            conn,
            "SELECT st.artist_key, a.name FROM artist_search_terms st "
            "JOIN artists a USING (artist_key) WHERE st.normalized_term = ?",
            [norm],
        )
    except Exception as exc:
        return None, "UNRESOLVED", f"resolution query failed: {exc}"
    if len(exact) == 1:
        return exact[0]["artist_key"], "VERIFIED_EXACT", f"exact name match: {exact[0]['name']}"
    if len(exact) > 1:
        return None, "CONFLICT", f"{len(exact)} exact matches in the universe; buyer review required"
    try:
        near = artist_security.search_artists(conn, artist_name, limit=10)
    except Exception:
        near = []
    if not near:
        try:
            near = artist_security.search_artists(conn, norm, limit=10)
        except Exception:
            near = []
    if near:
        return None, "REVIEW_REQUIRED", (
            f"no exact match; {len(near)} near match(es) require buyer review: "
            + ", ".join(h.get("name") or "?" for h in near[:4])
        )
    return None, "UNRESOLVED", "no candidate in the serving artist universe"


def import_private_shows(
    conn: Any,
    workspace: Any,
    *,
    file_name: str,
    headers: list[str],
    rows: list[list[str]] | list[dict[str, str]],
    mapping: list[dict[str, Any]],
    forced_mapping: dict[str, str] | None = None,
    customer_id: str | None = None,
) -> dict[str, Any]:
    """Commit a previewed file into the private workspace tables.

    - PII columns (PROHIBITED / POTENTIAL_PII) are quarantined: values are
      never read into private_shows or canonical claims.
    - Only AUTO_ACCEPTED + explicitly forced mappings are ingested.
    - Artist identity resolution FAILS CLOSED: artist_key attaches only on
      VERIFIED_EXACT; every other row is REVIEW_REQUIRED / CONFLICT /
      UNRESOLVED and stays unlinked.
    - Every stored show is OBSERVED_PRIVATE; nothing touches public serving.
    - Canonical economics outcome claims + dataset/ingestion lineage are
      written into the SAME canonical contract used by partner_import
      (economics.customer_datasets / outcome_claims / pii_quarantine), so the
      workspace UI surface and the canonical outcome vault converge.
    """
    from ..acquisition.contracts import content_hash_of, utc_now



    pii_statuses = design_partner.scan_pii_columns(headers)
    import_id = "imp_" + hashlib.sha256(
        (file_name + str(datetime.now(timezone.utc).timestamp())).encode()
    ).hexdigest()[:16]
    forced = forced_mapping or {}
    header_to_field: dict[str, str] = {}
    for m in mapping:
        resolved = m.get("canonical_field")
        header = m.get("header", "")
        if m.get("status") == "AUTO_ACCEPTED" and resolved:
            header_to_field[header] = resolved
        elif header in forced and forced[header] in design_partner.CANONICAL_FIELDS:
            header_to_field[header] = forced[header]
    mapping_by_canonical = {v: k for k, v in header_to_field.items()}

    quarantined: list[dict[str, Any]] = []
    for header, status in pii_statuses.items():
        if status in (PROHIBITED, POTENTIAL_PII):
            header_to_field.pop(header, None)
            mapping_by_canonical = {k: v for k, v in mapping_by_canonical.items() if v != header}
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
    # Normalize rows to dicts keyed by header (preview now returns dict rows;
    # positional rows are still accepted for backwards compatibility).
    dict_rows: list[dict[str, str]] = []
    for row in rows:
        if isinstance(row, dict):
            dict_rows.append({h: str(row.get(h) or "") for h in headers})
        else:
            cells = [str(c) if c is not None else "" for c in row]
            dict_rows.append({h: (cells[i] if i < len(cells) else "") for i, h in enumerate(headers)})

    inserted = 0
    skipped = 0
    identity_counts: dict[str, int] = {"VERIFIED_EXACT": 0, "SUPPORTED": 0, "REVIEW_REQUIRED": 0, "UNRESOLVED": 0, "CONFLICT": 0}
    cust_id = customer_id or f"workspace_{import_id}"

    # ── canonical lineage + claims on the shared economics contract ──
    canonical: dict[str, Any] = {"status": "NOT_APPLIED", "note": "canonical economics schema not initialized"}
    file_id = f"f_{content_hash_of((file_name, json.dumps(dict_rows[:1], default=str)))[:16]}"
    try:
        _ensure_canonical_schema(workspace)
        econ = EconomicsRepository(workspace)
        dataset_id = f"ds_{import_id}"
        econ.create_customer_dataset(
            dataset_id=dataset_id, customer_id=cust_id,
            sharing_policy="PRIVATE_ONLY", source_system="terminal_backtest",
            notes=f"{file_name} via backtest UI",
        )
        econ.insert_source_file(
            file_id=file_id, dataset_id=dataset_id, file_name=file_name,
            format=(file_name.rsplit(".", 1)[-1] if "." in file_name else "csv"),
            row_count=len(dict_rows),
            raw_content_hash=content_hash_of(json.dumps(dict_rows[:20], default=str)),
            created_at=utc_now().isoformat(),
        )
        econ.insert_ingestion_run(
            ingestion_run_id=f"ir_{import_id}", dataset_id=dataset_id,
            software_version="buyer_decision_system_v1",
            created_at=utc_now().isoformat(),
        )
        for header, status in pii_statuses.items():
            if status in (PROHIBITED, POTENTIAL_PII):
                econ.insert_pii_quarantine(
                    quarantine_id=f"pq_{content_hash_of((file_id, header))[:16]}",
                    file_id=file_id, column_name=header,
                    reason="prohibited_buyer_pii" if status == PROHIBITED else "potential_pii_review_required",
                    sample_count=len(dict_rows),
                    created_at=utc_now().isoformat(),
                )
        claims_inserted = 0
        claims_skipped = 0
        quality_issues: list[dict[str, Any]] = partner_import.data_quality_audit(
            dict_rows, mapping_by_canonical=mapping_by_canonical,
        )
        for row_index, row_dict in enumerate(dict_rows, start=1):
            canonical_event_id = partner_import.resolve_event_key(
                row_dict, customer_id=cust_id, mapping_by_canonical=mapping_by_canonical,
            )
            currency_raw = row_dict.get(mapping_by_canonical.get("currency", "currency"), "")
            currency = str(currency_raw).strip().upper() if currency_raw else None
            for claim in partner_import.build_claims_for_row(
                row_dict,
                canonical_event_id=canonical_event_id,
                customer_id=cust_id,
                dataset_id=dataset_id,
                source_file_id=file_id,
                row_number=row_index,
                mapping_by_canonical=mapping_by_canonical,
                currency=currency,
            ):
                if econ.insert_outcome_claim(claim):
                    claims_inserted += 1
                else:
                    claims_skipped += 1
            econ.upsert_decision_cutoffs({
                "event_id": canonical_event_id,
                "canonical_event_id": canonical_event_id,
                "booking_cutoff": row_dict.get(mapping_by_canonical.get("booking_date", "booking_date"), "") or None,
                "announcement_cutoff": row_dict.get(mapping_by_canonical.get("announcement_date", "announcement_date"), "") or None,
                "onsale_cutoff": row_dict.get(mapping_by_canonical.get("onsale_date", "onsale_date"), "") or None,
                "event_cutoff": row_dict.get(mapping_by_canonical.get("event_date", "event_date"), "") or None,
                "cutoff_notes": "imported via terminal backtest UI",
                "software_version": "buyer_decision_system_v1",
            })
        canonical = {
            "status": "APPLIED",
            "dataset_id": dataset_id,
            "customer_id": cust_id,
            "claims_inserted": claims_inserted,
            "duplicates_skipped": claims_skipped,
            "quality_issues": quality_issues[:10],
        }
    except Exception as exc:  # canonical contract unavailable → honest degraded state
        canonical = {
            "status": "FAILED",
            "error": str(exc)[:300],
            "note": "workspace rows remain PRIVATE_ONLY; canonical outcome contract was not written",
        }

    _COLUMNS = [c for c in _FIELDS if c != "import_id"] + ["identity_status", "identity_detail"]
    for row_index, row_dict in enumerate(dict_rows):
        record: dict[str, str | None] = {}
        for header in headers:
            field = header_to_field.get(header)
            if not field:
                continue
            value = row_dict.get(header, "")
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
        # Fail-closed identity resolution — never guess, never first-hit.
        artist_key, identity_status, identity_detail = _resolve_artist_fail_closed(conn, artist_name)
        identity_counts[identity_status] = identity_counts.get(identity_status, 0) + 1
        record["artist_key"] = artist_key
        record["identity_status"] = identity_status
        record["identity_detail"] = identity_detail
        show_id = "show_" + hashlib.sha256(
            (f"{artist_name}|{event_date}|{record.get('venue_name') or ''}|{row_index}").encode()
        ).hexdigest()[:20]
        values = [show_id, import_id] + [record.get(c) for c in _COLUMNS]
        placeholders = ",".join(["?"] * (len(_COLUMNS) + 2))
        workspace.execute(
            f"INSERT OR REPLACE INTO private_shows (show_id, import_id, {', '.join(_COLUMNS)}, provenance) "
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
        "artists_resolved": identity_counts.get("VERIFIED_EXACT", 0),
        "identity": identity_counts,
        "identity_review_required": identity_counts.get("REVIEW_REQUIRED", 0) + identity_counts.get("CONFLICT", 0) + identity_counts.get("UNRESOLVED", 0),
        "pii_quarantined": quarantined,
        "canonical": canonical,
        "sharing_policy": "PRIVATE_ONLY",
        "note": "private history never enters the public serving DuckDB; only VERIFIED_EXACT identities are linked to serving artists",
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
    # Canonical convergence: realized actuals become outcome claims on the SAME
    # canonical contract as design-partner imports (knowledge_time = event date).
    canonical = _write_canonical_outcome_claims(workspace, record, suffix=f"closeout_{snapshot_id[:12]}")
    return {
        "vault_id": vault_id, "show_id": show_id, "snapshot_id": snapshot_id,
        "provenance": "OBSERVED_PRIVATE", "canonical": canonical,
    }


def _ensure_canonical_schema(workspace: Any) -> None:
    """Apply the canonical economics migrations at most once per workspace."""
    from ..migrations import apply_pending_migrations
    try:
        has = workspace.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'schema_migrations' AND table_schema = 'main'"
        ).fetchone()[0]
    except Exception:
        has = 0
    if not has:
        apply_pending_migrations(workspace)


def _write_canonical_outcome_claims(workspace: Any, record: dict[str, Any], *, suffix: str) -> dict[str, Any]:
    """Write realized-outcome claims into the canonical economics contract.

    Best-effort and honest: if the canonical schema is unavailable the call
    reports FAILED and the workspace row remains PRIVATE_ONLY.
    """
    from ..acquisition.contracts import utc_now
    row_dict: dict[str, str] = {}
    for field in partner_import.OUTCOME_FIELD_MAP:
        value = record.get(field)
        if value is not None and str(value).strip() != "":
            row_dict[field] = str(value)
    copy_identity = ("artist_name", "venue_name", "event_date", "market", "city", "state",
                     "country", "currency", "deal_type", "artist_guarantee", "artist_backend_pct",
                     "announcement_date", "booking_date", "onsale_date")
    for field in copy_identity:
        value = record.get(field)
        if value is not None and str(value).strip() != "":
            row_dict[field] = str(value)
    if not row_dict:
        return {"status": "NOT_APPLIED", "note": "no realized outcome fields to claim"}
    try:
        _ensure_canonical_schema(workspace)
        econ = EconomicsRepository(workspace)
        dataset_id = f"ds_{suffix[:24]}"
        econ.create_customer_dataset(
            dataset_id=dataset_id, customer_id="workspace_closeout",
            sharing_policy="PRIVATE_ONLY", source_system="terminal_closeout",
            notes=f"post-show actuals {suffix}", created_at=utc_now().isoformat(),
        )
        econ.insert_ingestion_run(
            ingestion_run_id=f"ir_{suffix[:24]}", dataset_id=dataset_id,
            software_version="buyer_decision_system_v1", created_at=utc_now().isoformat(),
        )
        mapping_by_canonical = {f: f for f in row_dict}
        claims_inserted = 0
        claims_skipped = 0
        for claim in partner_import.build_claims_for_row(
            row_dict,
            canonical_event_id=f"closeout_{suffix[:24]}",
            customer_id="workspace_closeout",
            dataset_id=dataset_id,
            source_file_id=f"f_closeout_{suffix[:24]}",
            row_number=1,
            mapping_by_canonical=mapping_by_canonical,
            currency=row_dict.get("currency"),
        ):
            if econ.insert_outcome_claim(claim):
                claims_inserted += 1
            else:
                claims_skipped += 1
        return {"status": "APPLIED", "dataset_id": dataset_id, "claims_inserted": claims_inserted, "duplicates_skipped": claims_skipped}
    except Exception as exc:
        return {"status": "FAILED", "error": str(exc)[:300], "note": "workspace row remains PRIVATE_ONLY"}


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
    pit_family_breakdown: dict[str, int] = {}
    for s in shows:
        cutoff = s.get("booking_date") or s.get("announcement_date") or s.get("onsale_date")
        if cutoff:
            with_cutoff += 1
        pit_ok = False
        if s.get("artist_key") and cutoff:
            pit = pit_features_at(conn, s["artist_key"], cutoff)
            # leakage-safe reconstruction PASS = at least one admissible family
            pit_ok = pit.get("status") == "PIT_COMPLETE"
            if pit_ok:
                with_pit += 1
            else:
                pit_family_breakdown["PIT_INSUFFICIENT"] = pit_family_breakdown.get("PIT_INSUFFICIENT", 0) + 1
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
        # OOS eligibility: resolved event AND decision cutoff AND leakage-safe
        # PIT reconstruction AND a target outcome AND OBSERVED_PRIVATE provenance.
        if (
            pit_ok
            and (s.get("provenance") or "OBSERVED_PRIVATE") == "OBSERVED_PRIVATE"
            and (_decode_or_none(s.get("tickets_sold")) is not None
                 or _decode_or_none(s.get("paid_tickets")) is not None
                 or _decode_or_none(s.get("promoter_contribution")) is not None)
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
        "pit_insufficient": pit_family_breakdown.get("PIT_INSUFFICIENT", 0),
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
        "note": "with_valid_pit_reconstruction means an actual leakage-safe PIT reconstruction (knowledge_time <= cutoff) PASSED; nothing is counted on resolution alone",
    }


# ────────────────────────────────────────────────────────────────────────────
# DECISION MOAT — SALES PACE TAPE (private, PRIVATE_ONLY)
#
# Every pace row is ONE ACTUAL OBSERVATION from a ticketing source snapshot.
# sold != scanned != attendance; hold != sold; listing != sale. Derived pace
# points are computed ONLY from actual observations and are labeled DERIVED,
# never presented as observations. No interpolation is ever shown as data.
# ────────────────────────────────────────────────────────────────────────────

MOAT_SCHEMA = """
CREATE TABLE IF NOT EXISTS sales_pace_events (
    event_id VARCHAR PRIMARY KEY,
    artist_name VARCHAR,
    artist_key VARCHAR,
    venue_name VARCHAR,
    market VARCHAR,
    event_date VARCHAR,
    onsale_date VARCHAR,
    capacity VARCHAR,
    source_system VARCHAR,
    provenance VARCHAR NOT NULL DEFAULT 'OBSERVED_PRIVATE',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sales_pace_snapshots (
    snapshot_id VARCHAR PRIMARY KEY,
    event_id VARCHAR,
    snapshot_at VARCHAR,
    days_to_event VARCHAR,
    tickets_sold VARCHAR,
    tickets_available VARCHAR,
    holds VARCHAR,
    comps VARCHAR,
    refunded VARCHAR,
    ticket_gross VARCHAR,
    source VARCHAR,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS lineups (
    lineup_id VARCHAR PRIMARY KEY,
    name VARCHAR,
    budget VARCHAR,
    notes VARCHAR,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS lineup_members (
    lineup_id VARCHAR,
    snapshot_id VARCHAR,
    added_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (lineup_id, snapshot_id)
);
"""

PACE_SNAPSHOT_FIELDS = {
    "snapshot_at": "snapshot date/time of the observation",
    "tickets_sold": "cumulative tickets sold at snapshot",
    "tickets_available": "still available for sale at snapshot",
    "holds": "held inventory at snapshot",
    "comps": "complementary tickets at snapshot",
    "refunded": "refunded tickets at snapshot",
    "ticket_gross": "cumulative gross at snapshot",
}


def _ensure_moat_schema(workspace: Any) -> None:
    try:
        workspace.execute(MOAT_SCHEMA)
        workspace.commit()
    except Exception:
        workspace.rollback()


def _pace_num(value: Any) -> str | None:
    """Normalize an observed numeric to a decimal string; None stays UNKNOWN."""
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    text = text.replace(",", "").replace("$", "").replace("%", "")
    try:
        return str(Decimal(text))
    except Exception:
        return None


def _event_id_for(artist: str, venue: str, event_date: str) -> str:
    key = "|".join([(artist or "").strip().lower(), (venue or "").strip().lower(), (event_date or "").strip()])
    return "pace_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:14]


def import_sales_pace(workspace: Any, *, rows: list[dict[str, Any]], source: str = "customer_export") -> dict[str, Any]:
    """Import one row per OBSERVED snapshot. PRIVATE_ONLY, stays in workspace."""
    import uuid
    _ensure_moat_schema(workspace)
    events_seen: set[str] = set()
    snapshots = 0
    skipped = 0
    for row in rows:
        artist = str(row.get("artist_name") or "").strip()
        venue = str(row.get("venue_name") or "").strip()
        event_date = str(row.get("event_date") or "").strip()[:10]
        snapshot_at = str(row.get("snapshot_at") or "").strip()
        if not artist or not event_date or not snapshot_at:
            skipped += 1
            continue
        event_id = _event_id_for(artist, venue, event_date)
        if event_id not in events_seen:
            workspace.execute(
                """INSERT OR IGNORE INTO sales_pace_events
                   (event_id, artist_name, artist_key, venue_name, market, event_date,
                    onsale_date, capacity, source_system)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [event_id, artist, row.get("artist_key") or None,
                 venue or None, row.get("market") or None, event_date,
                 str(row.get("onsale_date") or "").strip()[:10] or None,
                 _pace_num(row.get("capacity")), source],
            )
            events_seen.add(event_id)
        days_to_event: str | None = None
        try:
            days_to_event = str((datetime.fromisoformat(event_date) - datetime.fromisoformat(snapshot_at[:10])).days)
        except Exception:
            days_to_event = None
        workspace.execute(
            """INSERT INTO sales_pace_snapshots
               (snapshot_id, event_id, snapshot_at, days_to_event, tickets_sold,
                tickets_available, holds, comps, refunded, ticket_gross, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ["snap_" + uuid.uuid4().hex[:16], event_id, snapshot_at[:19], days_to_event,
             _pace_num(row.get("tickets_sold")), _pace_num(row.get("tickets_available")),
             _pace_num(row.get("holds")), _pace_num(row.get("comps")),
             _pace_num(row.get("refunded")), _pace_num(row.get("ticket_gross")),
             str(row.get("source") or source)],
        )
        snapshots += 1
    workspace.commit()
    return {
        "events": len(events_seen),
        "snapshots": snapshots,
        "skipped": skipped,
        "privacy": "PRIVATE_ONLY — sales pace never leaves the workspace",
    }


def list_pace_events(workspace: Any) -> list[dict[str, Any]]:
    _ensure_moat_schema(workspace)
    return _rows(
        workspace,
        """SELECT e.event_id, e.artist_name, e.venue_name, e.market, e.event_date,
                  e.onsale_date, e.capacity, COUNT(s.snapshot_id) AS snapshot_count,
                  MAX(s.snapshot_at) AS latest_snapshot, MAX(s.tickets_sold) AS latest_sold
           FROM sales_pace_events e LEFT JOIN sales_pace_snapshots s USING (event_id)
           GROUP BY e.event_id, e.artist_name, e.venue_name, e.market, e.event_date,
                    e.onsale_date, e.capacity
           ORDER BY e.event_date DESC""",
    )


def _nearest_snapshot(snapshots: list[dict[str, Any]], marker_offset: int) -> dict[str, Any] | None:
    """Nearest ACTUAL observation to an offset; offset sign is explicit."""
    best = None
    best_gap: int | None = None
    for s in snapshots:
        if s.get("days_to_event") is None:
            continue
        try:
            gap = abs(int(s["days_to_event"]) - marker_offset)
        except Exception:
            continue
        if best_gap is None or gap < best_gap:
            best, best_gap = s, gap
    return best


def sales_curve(workspace: Any, event_id: str) -> dict[str, Any] | None:
    """Ordered actual observations + DERIVED pace markers (never interpolated)."""
    _ensure_moat_schema(workspace)
    event = _one(workspace, "SELECT * FROM sales_pace_events WHERE event_id = ?", [event_id])
    if event is None:
        return None
    snaps = _rows(
        workspace,
        """SELECT snapshot_id, snapshot_at, days_to_event, tickets_sold, tickets_available,
                  holds, comps, refunded, ticket_gross, source
           FROM sales_pace_snapshots WHERE event_id = ?
           ORDER BY snapshot_at ASC""",
        [event_id],
    )
    enriched: list[dict[str, Any]] = []
    for s in snaps:
        sold = s.get("tickets_sold")
        avail = s.get("tickets_available")
        gross = s.get("ticket_gross")
        row = dict(s)
        row["sell_through_derived"] = None
        row["atp_derived"] = None
        try:
            sold_d, avail_d = Decimal(sold), Decimal(avail)
            if avail_d >= 0 and sold_d + avail_d > 0:
                row["sell_through_derived"] = str((sold_d / (sold_d + avail_d)) * Decimal("100")) + "%"
        except Exception:
            pass
        try:
            if sold and gross:
                row["atp_derived"] = str(Decimal(gross) / Decimal(sold))
        except Exception:
            pass
        enriched.append(row)
    markers: list[dict[str, Any]] = []
    if event.get("onsale_date"):
        try:
            onsale = datetime.fromisoformat(str(event["onsale_date"])[:10]).date()
            event_dt = datetime.fromisoformat(str(event["event_date"])[:10]).date()
            total = (event_dt - onsale).days
            for label, offset in [("T+1", 1), ("T+3", 3), ("T+7", 7), ("T+14", 14), ("T+30", 30)]:
                target = total - offset if total >= 0 else None
                if target is None or target < 0:
                    continue
                near = _nearest_snapshot(enriched, target)
                if near is not None:
                    markers.append({
                        "label": label, "days_to_event_target": target,
                        "observed_days_to_event": near.get("days_to_event"),
                        "tickets_sold": near.get("tickets_sold"),
                        "sell_through_derived": near.get("sell_through_derived"),
                        "atp_derived": near.get("atp_derived"),
                        "basis": "NEAREST_OBSERVED_ACTUAL — not interpolated",
                    })
        except Exception:
            pass
    for label, target in [("30d-to-event", 30), ("14d-to-event", 14), ("7d-to-event", 7)]:
        near = _nearest_snapshot(enriched, target)
        if near is not None:
            markers.append({
                "label": label, "days_to_event_target": target,
                "observed_days_to_event": near.get("days_to_event"),
                "tickets_sold": near.get("tickets_sold"),
                "sell_through_derived": near.get("sell_through_derived"),
                "atp_derived": near.get("atp_derived"),
                "basis": "NEAREST_OBSERVED_ACTUAL — not interpolated",
            })
    return {
        "event": {k: v for k, v in event.items() if k not in ("created_at",)},
        "snapshots": enriched,
        "pace_markers": markers,
        "privacy": "PRIVATE_ONLY",
    }


def private_pace_comps(workspace: Any, *, artist_key: str | None = None,
                       market: str | None = None, limit: int = 8) -> list[dict[str, Any]]:
    """Private pace events that share the artist or market and have >=2 observations."""
    _ensure_moat_schema(workspace)
    conds: list[str] = []
    params: list[Any] = []
    if artist_key:
        conds.append("e.artist_key = ?")
        params.append(artist_key)
    if market:
        conds.append("e.market = ?")
        params.append(market)
    if not conds:
        return []
    params.append(limit)
    sql = (
        "SELECT e.event_id, e.artist_name, e.artist_key, e.venue_name, e.market, e.event_date, "
        "e.capacity, COUNT(s.snapshot_id) AS snapshot_count, MAX(s.snapshot_at) AS latest_snapshot "
        "FROM sales_pace_events e JOIN sales_pace_snapshots s USING (event_id) WHERE "
        + " OR ".join(conds)
        + " GROUP BY e.event_id, e.artist_name, e.artist_key, e.venue_name, e.market, "
        + "e.event_date, e.capacity "
        + "HAVING COUNT(s.snapshot_id) >= 2 ORDER BY e.event_date DESC LIMIT ?"
    )
    return _rows(workspace, sql, params)


# ────────────────────────────────────────────────────────────────────────────
# DECISION MOAT — PORTFOLIO / LINEUP RISK
#
# Aggregate saved decision briefs. Every number is a sum of KNOWN values only;
# events with UNKNOWN values are counted but never silently zeroed. Stress
# variants re-run the SAME deterministic economics engine on the buyer's own
# saved base scenario with one explicit input changed and labeled
# USER-DEFINED STRESS — never probability.
# ────────────────────────────────────────────────────────────────────────────

def _brief_scenario(brief: dict[str, Any], label: str = "base") -> dict[str, Any] | None:
    econ = brief.get("economics") or {}
    scen = (econ.get(label) or {}).get("scenario") or {}
    return scen or None


def _out(brief: dict[str, Any], scenario_label: str, key: str) -> dict[str, Any] | None:
    item = ((brief.get("economics") or {}).get(scenario_label) or {}).get("outputs") or {}
    out = item.get(key) or {}
    if out.get("status") != "KNOWN" or out.get("value") is None:
        return None
    return out


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _guarantee_of(brief: dict[str, Any]) -> Decimal | None:
    scen = _brief_scenario(brief)
    if scen:
        g = ((scen.get("deal") or {}).get("guarantee") or {}).get("value")
        if g is not None:
            return _dec(g)
    assumptions = brief.get("assumptions_entered") or {}
    return _dec(assumptions.get("guarantee"))


def _capacity_band_of(brief: dict[str, Any]) -> str | None:
    scen = _brief_scenario(brief)
    if not scen:
        return None
    cap = _dec(((scen.get("sellable_capacity") or {}).get("value")))
    if cap is None:
        cap = _dec(((scen.get("usable_capacity") or {}).get("value")))
    if cap is None:
        return None
    if cap < 500:
        return "<500"
    if cap < 1500:
        return "500–1,500"
    if cap < 5000:
        return "1,500–5,000"
    return ">5,000"


def _stress_evaluation(brief: dict[str, Any], variant: str) -> dict[str, Any] | None:
    """Re-run the deterministic engine on the saved base scenario with exactly
    one input changed. Only events whose base scenario is fully lossless and
    carries the shocked input can be stressed; everything else stays UNKNOWN."""
    from dataclasses import replace as dc_replace
    from ..economics.show_economics import scenario_from_dict
    scen_dict = _brief_scenario(brief)
    if not scen_dict:
        return None
    try:
        scenario = scenario_from_dict(scen_dict)
    except Exception:
        return None
    try:
        if variant == "SELL_THROUGH_MINUS_15PP":
            base_rate = _dec((scen_dict.get("sell_through") or {}).get("value"))
            if base_rate is None:
                return None
            shocked = max(Decimal("0"), base_rate - Decimal("0.15"))
            scenario = dc_replace(
                scenario,
                sell_through=TypedInput(shocked, Provenance.DERIVED,
                                        evidence_ref="portfolio.stress.v1"),
            )
        elif variant == "MARKETING_PLUS_15PCT":
            costs = scenario.costs
            marketing = costs.marketing
            if marketing.value is None or marketing.provenance == Provenance.UNKNOWN:
                return None
            shocked = _dec(marketing.value) * Decimal("1.15")
            scenario = dc_replace(
                scenario,
                costs=dc_replace(costs, marketing=TypedInput(
                    shocked, Provenance.DERIVED, evidence_ref="portfolio.stress.v1")),
            )
        else:
            return None
    except Exception:
        return None
    try:
        evaluation = evaluate(scenario)
    except Exception:
        return None
    contribution = evaluation.outputs.get("promoter_contribution")
    base_contribution = _out(brief, "base", "promoter_contribution")
    out = {"variant": variant}
    if contribution is not None and contribution.status.value == "KNOWN":
        out["promoter_contribution"] = str(contribution.value)
    else:
        out["promoter_contribution"] = None
    if base_contribution is not None:
        out["delta_vs_base"] = str(Decimal(out["promoter_contribution"] or 0) - Decimal(base_contribution["value"])) if out["promoter_contribution"] is not None else None
    return out


def portfolio_risk(workspace: Any, *, lineup_id: str | None = None) -> dict[str, Any]:
    """Aggregate saved decision briefs into portfolio-level exposure.

    Not a black-box score: totals are KNOWN-only sums, UNKNOWN counts are
    reported, and concentration/clustering are simple observed ratios.
    """
    _ensure_moat_schema(workspace)
    snaps = list_decision_snapshots(workspace)
    if lineup_id:
        members = {
            r["snapshot_id"] for r in _rows(
                workspace, "SELECT snapshot_id FROM lineup_members WHERE lineup_id = ?", [lineup_id]
            )
        }
        snaps = [s for s in snaps if s["snapshot_id"] in members]
    snaps = [s for s in snaps if s.get("status") != "PASSED"]

    events: list[dict[str, Any]] = []
    guarantee_known: list[Decimal] = []
    base_contrib_known: list[Decimal] = []
    down_contrib_known: list[Decimal] = []
    events_below_breakeven = 0
    for snap in snaps:
        brief = snap.get("brief") or {}
        if not brief:
            try:
                raw = _one(workspace, "SELECT brief_json FROM decision_snapshots WHERE snapshot_id = ?", [snap["snapshot_id"]])
                brief = json.loads((raw or {}).get("brief_json") or "{}") if raw else {}
            except Exception:
                brief = {}
        guarantee = _guarantee_of(brief)
        cap_band = _capacity_band_of(brief)
        base_contrib = _out(brief, "base", "promoter_contribution")
        down_contrib = _out(brief, "downside", "promoter_contribution")
        base_gross = _out(brief, "base", "gross_ticket_revenue")
        be_output = (((brief.get("economics") or {}).get("base") or {}).get("outputs") or {}).get("break_even_sell_through") or {}
        breakeven = _out(brief, "base", "break_even_sell_through")
        breakeven_status = be_output.get("status")
        if guarantee is not None:
            guarantee_known.append(guarantee)
        if base_contrib is not None:
            base_contrib_known.append(Decimal(base_contrib["value"]))
            if base_contrib["value"] is not None and Decimal(base_contrib["value"]) < 0:
                events_below_breakeven += 1
        if down_contrib is not None:
            down_contrib_known.append(Decimal(down_contrib["value"]))
        guarantee_vs_gross = None
        if guarantee is not None and base_gross is not None:
            gross_d = Decimal(base_gross["value"])
            if gross_d > 0:
                guarantee_vs_gross = float(guarantee / gross_d)
        events.append({
            "snapshot_id": snap["snapshot_id"],
            "artist_name": snap.get("artist_name"),
            "artist_key": snap.get("artist_key"),
            "market": snap.get("market_key"),
            "venue": snap.get("venue"),
            "event_date": snap.get("event_date"),
            "status": snap.get("status"),
            "guarantee": str(guarantee) if guarantee is not None else None,
            "capacity_band": cap_band,
            "base_contribution": base_contrib["value"] if base_contrib else None,
            "downside_contribution": down_contrib["value"] if down_contrib else None,
            "breakeven_sell_through": breakeven["value"] if breakeven else None,
            "breakeven_status": breakeven_status,
            "breakeven_reason": be_output.get("reason") if breakeven_status != "KNOWN" else None,
            "guarantee_vs_base_gross": guarantee_vs_gross,
            "brief": brief,
        })

    n = len(events)
    exposure = {
        "events": n,
        "total_guarantee": str(sum(guarantee_known)) if guarantee_known else None,
        "guarantee_known": len(guarantee_known),
        "guarantee_unknown": n - len(guarantee_known),
        "base_contribution_sum": str(sum(base_contrib_known)) if base_contrib_known else None,
        "base_contribution_known": len(base_contrib_known),
        "downside_contribution_sum": str(sum(down_contrib_known)) if down_contrib_known else None,
        "downside_contribution_known": len(down_contrib_known),
        "events_below_breakeven_at_base": events_below_breakeven,
    }

    # Concentration: simple observed shares — UNKNOWN when data is absent.
    concentration: dict[str, Any] = {"markets": {}, "capacity_bands": {}, "high_guarantee_share": None, "note": "observed ratios, not a score"}
    markets: dict[str, int] = {}
    bands: dict[str, int] = {}
    for e in events:
        m = e.get("market") or "UNKNOWN"
        markets[m] = markets.get(m, 0) + 1
        b = e.get("capacity_band") or "UNKNOWN"
        bands[b] = bands.get(b, 0) + 1
    concentration["markets"] = {k: v for k, v in sorted(markets.items(), key=lambda kv: -kv[1])}
    concentration["capacity_bands"] = {k: v for k, v in sorted(bands.items(), key=lambda kv: -kv[1])}
    if guarantee_known:
        total_g = sum(guarantee_known)
        if total_g > 0:
            top = max(guarantee_known)
            concentration["high_guarantee_share"] = float(top / total_g)
            concentration["high_guarantee_share_top"] = str(top)

    # Calendar clustering: max events whose dates fall within any 30-day window.
    dates: list[datetime] = []
    for e in events:
        try:
            dates.append(datetime.fromisoformat(str(e["event_date"])[:10]))
        except Exception:
            pass
    cluster = None
    if len(dates) >= 2:
        dates.sort()
        best = 0
        for i, d in enumerate(dates):
            window_end = d + timedelta(days=30)
            best = max(best, sum(1 for x in dates if d <= x <= window_end))
        cluster = best
    calendar: dict[str, Any] = {"max_events_in_30d_window": cluster}
    if cluster is not None and n > 0:
        calendar["note"] = f"{cluster} of {n} events fall inside a single 30-day window"

    # Stress: deterministic re-runs on the buyer's own base scenario.
    stress: dict[str, Any] = {}
    for variant in ("SELL_THROUGH_MINUS_15PP", "MARKETING_PLUS_15PCT"):
        contribs: list[Decimal] = []
        not_applicable = 0
        per_event: list[dict[str, Any]] = []
        for e in events:
            brief = e.get("brief") or {}
            if not brief:
                continue
            result = _stress_evaluation(brief, variant)
            if result is None or result.get("promoter_contribution") is None:
                not_applicable += 1
                continue
            contribs.append(Decimal(result["promoter_contribution"]))
            per_event.append({"snapshot_id": e["snapshot_id"], "promoter_contribution": result["promoter_contribution"]})
        stress[variant] = {
            "sum_contribution": str(sum(contribs)) if contribs else None,
            "events_stressed": len(contribs),
            "not_applicable": not_applicable,
            "label": "USER-DEFINED STRESS V1 — deterministic re-run of your saved base scenario with one explicit input changed; not a probability",
            "events": per_event[:50],
        }

    return {
        "portfolio": {"lineup_id": lineup_id, "events": n},
        "events": [{k: v for k, v in e.items() if k != "brief"} for e in events],
        "exposure": exposure,
        "concentration": concentration,
        "calendar": calendar,
        "stress": stress,
        "note": "totals sum KNOWN values only; UNKNOWN is never zeroed",
    }


def create_lineup(workspace: Any, *, name: str, budget: str | None = None, notes: str = "") -> dict[str, Any]:
    import uuid
    _ensure_moat_schema(workspace)
    lineup_id = "lineup_" + uuid.uuid4().hex[:12]
    workspace.execute(
        "INSERT INTO lineups (lineup_id, name, budget, notes) VALUES (?, ?, ?, ?)",
        [lineup_id, name, budget, notes],
    )
    workspace.commit()
    return {"lineup_id": lineup_id, "name": name}


def lineup_add(workspace: Any, lineup_id: str, snapshot_ids: list[str]) -> dict[str, Any]:
    _ensure_moat_schema(workspace)
    added = 0
    for sid in snapshot_ids:
        try:
            workspace.execute(
                "INSERT OR IGNORE INTO lineup_members (lineup_id, snapshot_id) VALUES (?, ?)",
                [lineup_id, sid],
            )
            added += 1
        except Exception:
            pass
    workspace.commit()
    return {"lineup_id": lineup_id, "added": added}


def list_lineups(workspace: Any) -> list[dict[str, Any]]:
    _ensure_moat_schema(workspace)
    return _rows(
        workspace,
        """SELECT l.lineup_id, l.name, l.budget, l.notes, l.created_at,
                  COUNT(m.snapshot_id) AS member_count
           FROM lineups l LEFT JOIN lineup_members m USING (lineup_id)
           GROUP BY l.lineup_id, l.name, l.budget, l.notes, l.created_at
           ORDER BY l.created_at DESC""",
    )


def portfolio_surface(workspace: Any) -> dict[str, Any]:
    """Full portfolio view: all saved decisions plus each named lineup."""
    all_risk = portfolio_risk(workspace)
    lineups: list[dict[str, Any]] = []
    for lineup in list_lineups(workspace):
        risk = portfolio_risk(workspace, lineup_id=lineup["lineup_id"])
        lineup["risk"] = risk.get("exposure")
        lineups.append(lineup)
    return {
        "all_decisions": all_risk,
        "lineups": lineups,
        "privacy": "PRIVATE_ONLY — portfolio math runs on your saved briefs in the workspace",
    }