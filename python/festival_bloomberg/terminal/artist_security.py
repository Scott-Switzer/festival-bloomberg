"""Compact read models for the Talent Buyer Terminal.

The browser-facing product reads only the materialized DuckDB artifact under
``serving/artist_security_terminal_v1``.  It never joins the canonical
warehouse or the full terminal snapshot at request time.  Every returned fact
retains a source/status/time boundary; an absent observation is represented as
``UNKNOWN`` rather than a numeric zero.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import duckdb

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PRODUCT_DB = str(
    _PROJECT_ROOT / "serving" / "artist_security_terminal_v1" / "CURRENT.duckdb"
)
CONTRACT_VERSION = "artist_security_terminal_v1"
RELEASE_LABEL = "UNDERWRITING RESEARCH — not automated booking advice"


class ArtistSecurityServingError(RuntimeError):
    """The compact serving artifact is missing or incompatible."""


def _rows(conn, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    cur = conn.execute(sql, params or [])
    cols = [column[0] for column in cur.description]
    return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


def _one(conn, sql: str, params: list[Any] | None = None) -> dict[str, Any] | None:
    rows = _rows(conn, sql, params)
    return rows[0] if rows else None


def _table_exists(conn, table: str) -> bool:
    schema, name = table.split(".", 1) if "." in table else ("main", table)
    return bool(conn.execute(
        "SELECT COUNT(*) FROM duckdb_tables() WHERE schema_name=? AND table_name=?",
        [schema, name],
    ).fetchone()[0])


def open_product_db(path: str = DEFAULT_PRODUCT_DB) -> duckdb.DuckDBPyConnection:
    """Open and validate the immutable compact product artifact read-only."""
    artifact = Path(path)
    if not artifact.is_file():
        raise ArtistSecurityServingError(f"ARTIST_SECURITY_SERVING_MISSING: {artifact}")
    conn = duckdb.connect(str(artifact), read_only=True)
    try:
        required = (
            "product_meta", "artists", "artist_search_terms", "artist_external_ids",
            "attention_observations", "artist_peers", "artist_markets", "event_history",
            "festival_appearances", "future_events",
        )
        missing = [table for table in required if not _table_exists(conn, table)]
        if missing:
            raise ArtistSecurityServingError(
                "ARTIST_SECURITY_SERVING_INCOMPATIBLE: missing " + ", ".join(missing)
            )
        meta = _one(conn, "SELECT * FROM product_meta LIMIT 1") or {}
        version = meta.get("contract_version") or meta.get("product_version")
        if version != CONTRACT_VERSION:
            raise ArtistSecurityServingError(
                f"ARTIST_SECURITY_SERVING_INCOMPATIBLE: contract_version={version!r}"
            )
        return conn
    except Exception:
        conn.close()
        raise


def normalize_search_term(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def search_artists(conn, query: str, limit: int = 25) -> list[dict[str, Any]]:
    """Search the 25K product universe by canonical name and stored aliases."""
    q = normalize_search_term(query)
    if not q:
        return []
    limit = max(1, min(int(limit), 100))
    rows = _rows(conn, """
        WITH candidates AS (
            SELECT st.artist_key, a.name, a.musicbrainz_id, a.tier, st.term_type,
                   CASE
                     WHEN st.normalized_term = ? THEN 0
                     WHEN st.normalized_term LIKE ? THEN 1
                     ELSE 2
                   END AS match_priority,
                   length(st.normalized_term) AS term_length
            FROM artist_search_terms st
            JOIN artists a USING (artist_key)
            WHERE st.normalized_term = ? OR st.normalized_term LIKE ? OR st.normalized_term LIKE ?
        ), ranked AS (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY artist_key ORDER BY match_priority, term_length, term_type
            ) AS artist_rank
            FROM candidates
        )
        SELECT artist_key, name, musicbrainz_id, tier, term_type, match_priority
        FROM ranked WHERE artist_rank = 1
        ORDER BY match_priority, length(name), lower(name), artist_key
        LIMIT ?
    """, [q, f"{q}%", q, f"{q}%", f"%{q}%", limit])
    return [{
        "entity_type": "ARTIST",
        "entity_id": row["artist_key"],
        "name": row["name"],
        "mbid": row.get("musicbrainz_id"),
        "tier": row.get("tier"),
        "matched_term_type": row.get("term_type"),
    } for row in rows]


def _unknown(source_system: str, note: str) -> dict[str, Any]:
    return {
        "status": "UNKNOWN",
        "source_system": source_system,
        "latest_observation": None,
        "latest_knowledge_time": None,
        "metrics": [],
        "items": [],
        "note": note,
    }


def has_advertised_structured_range(event: dict[str, Any]) -> bool:
    """Whether a forward row carries provider-advertised structured prices."""
    return (
        event.get("ticket_price_basis") == "ADVERTISED_STRUCTURED_RANGE"
        and event.get("ticket_evidence_status") == "ADVERTISED_RANGE"
        and (event.get("ticket_price_min") is not None or event.get("ticket_price_max") is not None)
    )


def _table_exists(conn, table: str) -> bool:
    try:
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ? LIMIT 1",
            [table],
        ).fetchone()
        return row is not None
    except Exception:
        return False


def _public_ticket_market(conn, artist_key: str) -> dict[str, Any]:
    """PUBLIC TICKET MARKET panel — listing observations, never demand/sales."""
    note = (
        "PUBLIC TICKET MARKET observations from accepted marketplace pages. "
        "Missing prices stay missing. Listing disappearance is LISTING_NO_LONGER_OBSERVED, "
        "not a sale. This is not ticket demand, sell-through, attendance, or transaction price."
    )
    if not _table_exists(conn, "ticket_market_observations"):
        return {
            "status": "UNKNOWN",
            "label": "PUBLIC TICKET MARKET",
            "items": [],
            "events": [],
            "note": note + " No ticket_market_observations table in this serving generation.",
        }
    rows = _rows(conn, """
        SELECT *
        FROM ticket_market_observations
        WHERE artist_key = ?
        ORDER BY event_key, marketplace, observed_at ASC NULLS LAST
        LIMIT 500
    """, [artist_key])
    if not rows:
        return {
            "status": "UNKNOWN",
            "label": "PUBLIC TICKET MARKET",
            "items": [],
            "events": [],
            "note": note,
        }

    # Group by event×marketplace; current = latest; priors = earlier chronological.
    from collections import defaultdict
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        groups[(row.get("event_key") or "", row.get("marketplace") or "")].append(row)

    events_out: list[dict[str, Any]] = []
    for (event_key, marketplace), obs in groups.items():
        obs_sorted = sorted(obs, key=lambda r: str(r.get("observed_at") or ""))
        current = obs_sorted[-1]
        priors = obs_sorted[:-1]
        current_price = current.get("all_in_price")
        if current_price is None:
            current_price = current.get("resale_min_price")
        if current_price is None:
            current_price = current.get("face_value")

        def _delta(hours: int) -> dict[str, Any] | None:
            """1D/7D change only when a prior observation exists in the window."""
            if current.get("observed_at") is None or current_price is None:
                return None
            try:
                from datetime import datetime, timedelta
                cur_t = current["observed_at"]
                if isinstance(cur_t, str):
                    cur_t = datetime.fromisoformat(cur_t.replace("Z", "+00:00"))
                cutoff = cur_t - timedelta(hours=hours)
                candidates = [
                    r for r in priors
                    if r.get("observed_at") is not None
                    and (
                        datetime.fromisoformat(str(r["observed_at"]).replace("Z", "+00:00"))
                        if isinstance(r["observed_at"], str) else r["observed_at"]
                    ) >= cutoff
                ]
                if not candidates:
                    return None
                base = candidates[0]
                base_price = base.get("all_in_price")
                if base_price is None:
                    base_price = base.get("resale_min_price")
                if base_price is None:
                    base_price = base.get("face_value")
                if base_price is None:
                    return None
                # Only compare when both price_basis values are present and equal.
                if base.get("price_basis") and current.get("price_basis"):
                    if base["price_basis"] != current["price_basis"]:
                        return {"status": "NOT_COMPARABLE", "reason": "price_basis_mismatch"}
                return {
                    "status": "OBSERVED",
                    "from_price": base_price,
                    "to_price": current_price,
                    "delta": float(current_price) - float(base_price),
                    "from_observed_at": base.get("observed_at"),
                    "basis": current.get("price_basis"),
                }
            except Exception:
                return None

        events_out.append({
            "event_key": event_key,
            "provider_event_id": current.get("provider_event_id"),
            "artist_name": current.get("artist_name"),
            "marketplace": marketplace,
            "venue_name": current.get("venue_name"),
            "city": current.get("city"),
            "event_date": current.get("event_date"),
            "source_url": current.get("source_url"),
            "current": {
                "observed_at": current.get("observed_at"),
                "retrieved_at": current.get("retrieved_at"),
                "knowledge_time": current.get("knowledge_time"),
                "price": current_price,
                "currency": current.get("currency"),
                "price_basis": current.get("price_basis") or "UNKNOWN",
                "evidence_status": current.get("evidence_status") or "UNKNOWN",
                "evidence_ref": current.get("evidence_ref") or current.get("raw_payload_hash"),
                "listing_count": current.get("listing_count"),
            },
            "prior_observations": [
                {
                    "observed_at": p.get("observed_at"),
                    "price": p.get("all_in_price") if p.get("all_in_price") is not None else (
                        p.get("resale_min_price") if p.get("resale_min_price") is not None else p.get("face_value")
                    ),
                    "currency": p.get("currency"),
                    "price_basis": p.get("price_basis") or "UNKNOWN",
                    "evidence_status": p.get("evidence_status") or "UNKNOWN",
                    "evidence_ref": p.get("evidence_ref") or p.get("raw_payload_hash"),
                }
                for p in reversed(priors[-12:])
            ],
            "change_1d": _delta(24),
            "change_7d": _delta(24 * 7),
            "observation_count": len(obs_sorted),
        })

    events_out.sort(key=lambda e: str(e.get("event_date") or ""), reverse=True)
    return {
        "status": "OBSERVED",
        "label": "PUBLIC TICKET MARKET",
        "items": rows,
        "events": events_out,
        "note": note,
    }


def _attention(conn, artist_key: str) -> dict[str, dict[str, Any]]:
    rows = _rows(conn, """
        SELECT * FROM attention_observations
        WHERE artist_key = ?
        ORDER BY COALESCE(period_end, period_start) DESC NULLS LAST,
                 knowledge_time DESC NULLS LAST
    """, [artist_key])
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        source = str(row.get("source_system") or "").lower()
        if "wiki" in source:
            grouped["wikimedia"].append(row)
        elif "listenbrainz" in source:
            grouped["listenbrainz"].append(row)
        elif "youtube" in source:
            grouped["youtube"].append(row)

    out: dict[str, dict[str, Any]] = {}
    notes = {
        "wikimedia": "No Wikimedia attention observation is present in this serving generation.",
        "listenbrainz": "No ListenBrainz consumption observation is present in this serving generation.",
        "youtube": "No YouTube channel observation is present in this serving generation.",
    }
    for source in ("wikimedia", "listenbrainz", "youtube"):
        items = grouped.get(source, [])
        if not items:
            out[source] = _unknown(source, notes[source])
            continue
        latest = items[0]
        # Delta vs the PRIOR observation of the same metric kind AND the same
        # window span (a weekly row must not be compared to an all-time row).
        # Prior = a strictly LATER row (older observation); a row is never its
        # own prior. Absence of a comparable prior stays UNKNOWN — never zero.
        def _span(row: dict[str, Any]):
            start, end = row.get("period_start"), row.get("period_end")
            if start is None or end is None:
                return None
            try:
                return (end - start).days
            except Exception:
                return None

        keys = [(str(row.get("metric_kind") or ""), _span(row)) for row in items]
        for i, row in enumerate(items):
            prior = None
            for j in range(i + 1, len(items)):
                if keys[j] == keys[i] and keys[i][1] is not None:
                    prior = items[j]
                    break
            value = row.get("value_sum") if row.get("value_sum") is not None else row.get("value")
            if prior is None or value is None:
                row["change"] = None
                continue
            prior_value = prior.get("value_sum") if prior.get("value_sum") is not None else prior.get("value")
            if prior_value is None:
                row["change"] = None
                continue
            row["prior_value"] = prior_value
            row["prior_observation"] = prior.get("period_end") or prior.get("period_start") or prior.get("retrieved_at")
            row["change"] = value - prior_value
            if prior_value not in (None, 0):
                row["change_pct"] = round(100.0 * (value - prior_value) / abs(prior_value), 2)
        # Chart series: only SAME metric kind AND SAME window span (weekly vs
        # weekly, daily vs daily) with >=2 dated points. Different windows are
        # never plotted on one axis.
        series: list[dict[str, Any]] = []
        by_key: dict[tuple[str, object], list[dict[str, Any]]] = defaultdict(list)
        for row in items:
            t = row.get("period_end") or row.get("period_start")
            if t is None:
                continue
            v = row.get("value_sum") if row.get("value_sum") is not None else row.get("value")
            if v is None:
                continue
            kind = str(row.get("metric_kind") or "metric")
            start, end = row.get("period_start"), row.get("period_end")
            span = (end - start).days if (start is not None and end is not None) else None
            by_key[(kind, span)].append({"t": str(t)[:10], "v": float(v), "kind": kind, "span": span})
        for (kind, span), points in by_key.items():
            if len(points) < 2:
                continue
            points.sort(key=lambda p: p["t"])
            label = kind.replace("LISTENBRAINZ_", "").replace("_", " ").title()
            if span == 7:
                label += " (weekly)"
            elif span == 1:
                label += " (daily)"
            elif span == 30 or span == 31:
                label += " (monthly)"
            elif span is None:
                label += " (cumulative snapshot)"
            series.append({"metric_kind": kind, "span": span, "label": label, "points": points[-400:]})
        out[source] = {
            "status": "OBSERVED",
            "source_system": latest.get("source_system") or source,
            "latest_observation": latest.get("period_end") or latest.get("period_start"),
            "latest_knowledge_time": latest.get("knowledge_time"),
            "metrics": items[:24],
            "items": items[:24],
            "series": series,
            "note": (
                "Descriptive attention/consumption evidence only; it is not local demand "
                "or ticket-purchase intent."
            ),
        }
    return out


def _peer_rows(conn, artist_key: str, limit: int = 20) -> list[dict[str, Any]]:
    return _rows(conn, """
        SELECT p.*, a.name AS resolved_peer_name, a.tier AS peer_tier,
               (SELECT COUNT(*) FROM artist_markets mine
                JOIN artist_markets theirs ON mine.market_key = theirs.market_key
                WHERE mine.artist_key = p.subject_key
                  AND theirs.artist_key = p.peer_key) AS shared_markets,
               (SELECT COUNT(DISTINCT mine.event_key)
                FROM festival_appearances mine
                JOIN festival_appearances theirs ON mine.event_key = theirs.event_key
                WHERE mine.artist_key = p.subject_key
                  AND theirs.artist_key = p.peer_key) AS shared_festival_bills
        FROM artist_peers p
        JOIN artists a ON a.artist_key = p.peer_key
        WHERE p.subject_key = ?
        ORDER BY p.rank, p.shared_listeners DESC, p.peer_key
        LIMIT ?
    """, [artist_key, limit])


def _alternatives(peers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    alternatives: list[dict[str, Any]] = []
    for peer in peers[:10]:
        reasons = [
            f"{peer.get('shared_listeners')} shared listeners in the 1% ListenBrainz pilot",
            f"Jaccard {peer.get('jaccard')}",
        ]
        if peer.get("shared_festival_bills"):
            reasons.append(f"{peer['shared_festival_bills']} shared festival bills")
        if peer.get("shared_markets"):
            reasons.append(f"{peer['shared_markets']} shared observed markets")
        alternatives.append({
            "artist_key": peer.get("peer_key"),
            "artist_name": peer.get("resolved_peer_name") or peer.get("peer_name"),
            "reasons": reasons,
            "differences": [
                "Audience affinity is descriptive pilot evidence, not interchangeability or availability.",
                "Compare market, festival, forward, and ticket evidence before underwriting.",
            ],
            "source_system": peer.get("source_system") or "listenbrainz",
            "source_scope": peer.get("source_scope") or "PILOT_1_PERCENT",
            "knowledge_time": peer.get("knowledge_time"),
        })
    return alternatives


def _evidence_items(
    artist: dict[str, Any],
    attention: dict[str, dict[str, Any]],
    peers: list[dict[str, Any]],
    markets: list[dict[str, Any]],
    history: list[dict[str, Any]],
    festivals: list[dict[str, Any]],
    future: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = [{
        "panel": "identity",
        "source_system": artist.get("source_system") or "musicbrainz",
        "observation_time": artist.get("source_observation_time"),
        "knowledge_time": artist.get("knowledge_time"),
        "status": artist.get("identity_status") or "OBSERVED",
    }]
    for key, block in attention.items():
        items.append({
            "panel": f"attention.{key}",
            "source_system": block["source_system"],
            "observation_time": block["latest_observation"],
            "knowledge_time": block["latest_knowledge_time"],
            "status": block["status"],
        })
    for panel, rows in (
        ("audience_peers", peers), ("markets", markets), ("live_history", history),
        ("festival_history", festivals), ("future_tickets", future),
    ):
        if rows:
            latest = rows[0]
            items.append({
                "panel": panel,
                "source_system": latest.get("source_system"),
                "observation_time": latest.get("event_date") or latest.get("last_play"),
                "knowledge_time": latest.get("knowledge_time"),
                "status": "OBSERVED",
            })
        else:
            items.append({
                "panel": panel, "source_system": None, "observation_time": None,
                "knowledge_time": None, "status": "UNKNOWN",
            })
    return items


def _market_city_form(slug: str) -> str:
    """Derive the display city from a slug market key.
    'chicago-il' -> 'chicago'; 'new-york-ny' -> 'new york';
    'las-vegas-nv-us' -> 'las vegas'; 'st-louis-mo' -> 'st louis'.
    Only forms derivable from the slug itself — no fabricated mapping."""
    parts = [p for p in str(slug).lower().split("-") if p]
    if len(parts) >= 4:
        return " ".join(parts[:-2])
    return " ".join(parts[:-1]) if len(parts) > 1 else str(slug).lower()


def _enrich_markets_from_future(
    markets: list[dict[str, Any]], future: list[dict[str, Any]]
) -> None:
    """Join the artist's own forward/ticket rows into market profile rows.
    Markets with no forward evidence keep UNKNOWN fields (no zeros)."""
    for m in markets:
        slug = m.get("market_key") or m.get("market") or m.get("market_name")
        if not slug:
            continue
        city = _market_city_form(str(slug))
        matches = [
            f for f in future
            if (str(f.get("city") or "").lower() == city
                or str(f.get("market_name") or "").lower().startswith(city))
        ]
        if not matches:
            continue
        dated = [f for f in matches if f.get("event_date")]
        m["future_events"] = len(matches)
        m["ticket_evidence_available"] = sum(
            1 for f in matches if f.get("price_min") is not None or f.get("price_max") is not None
        ) or None
        if dated:
            nxt = sorted(dated, key=lambda f: str(f.get("event_date")))[0]
            m["next_event"] = {
                "date": nxt.get("event_date"),
                "venue": nxt.get("venue_name") or nxt.get("venue"),
                "event": nxt.get("event_name"),
            }


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            import json
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _factor_comparability(previous: dict[str, Any], latest: dict[str, Any]) -> tuple[bool, str | None]:
    """Financial-grade delta gate: only like-for-like observations compare.

    Fields are read from first-class columns when present (migration 050) with
    an evidence_json fallback for older rows. Missing or mismatched context
    means NOT_COMPARABLE — no percentage change is produced.
    """
    fields = (
        "measurement_basis",
        "measurement_window",
        "population_scope",
        "geographic_scope",
        "methodology_version",
        "coverage_generation",
    )

    def field(row: dict[str, Any], name: str) -> Any:
        if row.get(name) is not None:
            return row.get(name)
        evidence = _json_object(row.get("evidence_json"))
        return evidence.get(name)

    for name in fields:
        old_v = field(previous, name)
        new_v = field(latest, name)
        if old_v is None or new_v is None:
            return False, f"{name} missing (old={old_v!r}, new={new_v!r})"
        if str(old_v) != str(new_v):
            return False, f"{name} differs (old={old_v!r}, new={new_v!r})"
    return True, None


def _artist_factor_tape(conn, artist_key: str) -> dict[str, Any]:
    """Read the compact append-only factor tape when the artifact has it."""
    if not _table_exists(conn, "artist_factor_observations"):
        return {
            "status": "PROVIDER_READY",
            "items": [],
            "series": [],
            "changes": [],
            "note": "Factor tape is not present in this serving generation; rebuild from an intelligence snapshot to enable it.",
        }
    rows = _rows(conn, """
        SELECT * FROM artist_factor_observations
        WHERE artist_key = ?
        ORDER BY observation_time DESC NULLS LAST, retrieved_at DESC NULLS LAST,
                 factor_name, platform
    """, [artist_key])
    for row in rows:
        row["sample_size"] = _json_object(row.get("evidence_json")).get("sample_size")
        row["freshness"] = row.get("retrieved_at") or row.get("knowledge_time")
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        value = row.get("value")
        observed = row.get("observation_time") or row.get("as_of")
        if value is None or observed is None:
            continue
        group = (
            str(row.get("factor_name") or ""),
            str(row.get("platform") or row.get("source") or ""),
            str(row.get("unit") or ""),
        )
        groups[group].append(row)
    series: list[dict[str, Any]] = []
    for (factor_name, platform, unit), points in groups.items():
        if len(points) < 2:
            continue
        points = sorted(points, key=lambda row: str(row.get("observation_time") or row.get("as_of")))
        series.append({
            "factor_name": factor_name,
            "platform": platform,
            "unit": unit,
            "label": f"{factor_name} · {platform}",
            "source": points[-1].get("source") or points[-1].get("source_system") or platform,
            "period": {
                "start": points[0].get("observation_time") or points[0].get("as_of"),
                "end": points[-1].get("observation_time") or points[-1].get("as_of"),
            },
            "sample_size": points[-1].get("sample_size"),
            "freshness": points[-1].get("freshness"),
            "points": [
                {
                    "t": str(row.get("observation_time") or row.get("as_of"))[:19],
                    "v": float(row["value"]),
                    "source": row.get("source") or row.get("source_system") or platform,
                    "sample_size": row.get("sample_size"),
                }
                for row in points[-400:]
            ],
        })
    changes: list[dict[str, Any]] = []
    for (factor_name, platform, _unit), points in groups.items():
        points = sorted(points, key=lambda row: str(row.get("observation_time") or row.get("as_of")))
        if len(points) < 2:
            continue
        previous, latest = points[-2], points[-1]
        old = previous.get("value")
        new = latest.get("value")
        if old is None or new is None:
            continue
        if (previous.get("unit") or previous.get("value_unit")) != (latest.get("unit") or latest.get("value_unit")):
            continue
        change = float(new) - float(old)
        item = {
            "factor_name": factor_name,
            "platform": platform,
            "old_value": float(old),
            "new_value": float(new),
            "delta": change,
            "unit": latest.get("unit") or latest.get("value_unit"),
            "period": {
                "from": previous.get("observation_time") or previous.get("as_of"),
                "to": latest.get("observation_time") or latest.get("as_of"),
            },
            "source": latest.get("source") or latest.get("source_system") or platform,
            "generation": latest.get("generation") or latest.get("source_version"),
            "sample_size": latest.get("sample_size"),
            "freshness": latest.get("freshness"),
        }
        comparable, incomparable_reason = _factor_comparability(previous, latest)
        if not comparable:
            item["comparability"] = "NOT_COMPARABLE"
            item["comparability_reason"] = incomparable_reason or "measurement context differs"
            changes.append(item)
            continue
        item["comparability"] = "COMPARABLE"
        if float(old) != 0:
            item["delta_pct"] = change / abs(float(old)) * 100.0
        changes.append(item)
    return {
        "status": "OBSERVED" if rows else "PROVIDER_READY",
        "items": rows[:250],
        "series": series,
        "changes": sorted(changes, key=lambda item: (item["factor_name"], item["platform"])),
        "note": "Append-only temporal observations. UNKNOWN is distinct from zero; no current snapshot is used to reconstruct history.",
    }


def _artist_sentiment(conn, artist_key: str) -> dict[str, Any]:
    """Read daily aggregate sentiment without exposing raw social identities."""
    if not _table_exists(conn, "artist_sentiment_observations"):
        return {
            "status": "PROVIDER_READY",
            "items": [],
            "series": [],
            "note": "No daily sentiment aggregate is present in this serving generation.",
        }
    rows = _rows(conn, """
        SELECT * FROM artist_sentiment_observations
        WHERE artist_key = ?
        ORDER BY date DESC, platform
    """, [artist_key])
    series: list[dict[str, Any]] = []
    by_platform: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        row["sample_size"] = row.get("analyzed_count")
        row["freshness"] = row.get("retrieved_at") or row.get("knowledge_time")
        by_platform[str(row.get("platform") or "unknown")].append(row)
    for platform, platform_rows in by_platform.items():
        dated = [row for row in platform_rows if row.get("date") is not None and row.get("sentiment_mean") is not None]
        if len(dated) < 2:
            continue
        dated.sort(key=lambda row: str(row["date"]))
        latest = dated[-1]
        series.append({
            "platform": platform,
            "label": f"Sentiment · {platform}",
            "source": latest.get("source") or platform,
            "period": {"start": dated[0]["date"], "end": latest["date"]},
            "sample_size": latest.get("analyzed_count"),
            "freshness": latest.get("freshness"),
            "points": [
                {
                    "t": str(row["date"])[:10],
                    "v": float(row["sentiment_mean"]),
                    "sample_size": row.get("analyzed_count"),
                }
                for row in dated[-400:]
            ],
        })
    return {
        "status": "OBSERVED" if rows else "PROVIDER_READY",
        "items": rows[:250],
        "series": series,
        "note": "Daily aggregate only. Usernames, user IDs, post IDs, and raw text are excluded; sample size, language, and model generation remain visible.",
    }


def _provider_readiness(factor_tape: dict[str, Any], sentiment: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Expose legal/access boundaries instead of presenting absent values as zero."""
    factor_platforms = {str(row.get("platform") or "").lower() for row in factor_tape.get("items", [])}
    sentiment_platforms = {str(row.get("platform") or "").lower() for row in sentiment.get("items", [])}
    spotify_observed = "spotify" in factor_platforms
    social_observed = bool({"tiktok", "instagram", "youtube"} & (factor_platforms | sentiment_platforms))
    return {
        "spotify": {
            "status": "OBSERVED" if spotify_observed else "AUTH_REQUIRED",
            "historical_strategy": "LICENSED_HISTORICAL / SELF_OBSERVED_FORWARD",
            "commercial_use_status": "TERMS_REVIEW_REQUIRED",
            "note": "Unofficial Spotify scraping is not a canonical source; licensed history requires Soundcharts or Chartmetric authorization.",
        },
        "soundcharts": {
            "status": "OBSERVED" if spotify_observed else "PROVIDER_READY / AUTH_REQUIRED",
            "historical_strategy": "LICENSED_HISTORICAL",
            "commercial_use_status": "LICENSE_REQUIRED",
            "note": "Licensed audience and streaming history; real backfill is unavailable until account authorization is configured.",
        },
        "chartmetric": {
            "status": "PROVIDER_READY / AUTH_REQUIRED",
            "historical_strategy": "LICENSED_HISTORICAL",
            "commercial_use_status": "LICENSE_REQUIRED",
            "note": "Alternate licensed historical audience and streaming source; no account data is assumed.",
        },
        "google_trends": {
            "status": "OBSERVED" if "google_trends" in factor_platforms else "WAITLIST / AUTH_REQUIRED",
            "historical_strategy": "SELF_OBSERVED_FORWARD",
            "commercial_use_status": "LICENSE_REQUIRED",
            "note": "Official Trends API alpha contract only; UI scraping is disabled.",
        },
        "social": {
            "status": "OBSERVED" if social_observed else "PROVIDER_READY / AUTH_REQUIRED",
            "commercial_use_status": "TERMS_REVIEW_REQUIRED",
            "note": "Platform-specific rights and provider lineage remain attached to each observation.",
        },
    }


def get_artist_security(conn, artist_key: str) -> dict[str, Any] | None:
    """Return the full buyer-facing Artist Security contract."""
    artist = _one(conn, "SELECT * FROM artists WHERE artist_key = ?", [artist_key])
    if artist is None and not artist_key.startswith("mbid::"):
        artist = _one(conn, "SELECT * FROM artists WHERE musicbrainz_id = ?", [artist_key])
    if artist is None:
        return None
    artist_key = artist["artist_key"]
    external_ids = _rows(conn, """
        SELECT * FROM artist_external_ids WHERE artist_key = ?
        ORDER BY id_type, source_system, id_value
    """, [artist_key])
    attention = _attention(conn, artist_key)
    factor_tape = _artist_factor_tape(conn, artist_key)
    sentiment = _artist_sentiment(conn, artist_key)
    provider_readiness = _provider_readiness(factor_tape, sentiment)
    peers = _peer_rows(conn, artist_key)
    markets = _rows(conn, """
        SELECT *, observed_shows AS historical_shows,
               last_play_date AS last_play,
               market_key AS market_name,
               ticket_evidence_count AS ticket_evidence
        FROM artist_markets WHERE artist_key = ?
        ORDER BY observed_shows DESC NULLS LAST, last_play_date DESC NULLS LAST, market_key
        LIMIT 24
    """, [artist_key])
    for peer in peers:
        peer["source_artist_key"] = peer.get("subject_key")
        peer["artist_key"] = peer.get("peer_key")
        peer["artist_name"] = peer.get("resolved_peer_name") or peer.get("peer_name")
        # Row-specific explanation composed from the peer edge's own evidence.
        # No boilerplate: only fields this edge actually supports.
        parts: list[str] = []
        if peer.get("shared_listeners") is not None:
            parts.append(f"{peer['shared_listeners']} shared listeners")
        if peer.get("jaccard") is not None:
            parts.append(f"Jaccard {peer['jaccard']}")
        if peer.get("shared_markets"):
            parts.append(f"{peer['shared_markets']} shared markets")
        if peer.get("shared_festival_bills"):
            parts.append(f"{peer['shared_festival_bills']} shared festival bills")
        peer["why_related"] = " · ".join(parts) if parts else peer.get("explanation")
        peer["evidence_parts"] = parts
        peer["differences"] = (
            f"Shared observed markets: {peer.get('shared_markets') or 0}; "
            f"shared festival bills: {peer.get('shared_festival_bills') or 0}. "
            "Availability, deal terms, and local ticket intent remain UNKNOWN."
        )
    for market in markets:
        market["market"] = market.get("market_name") or market.get("market_key")
        market["last_played"] = market.get("last_play_date")
    history = _rows(conn, """
        SELECT * FROM event_history WHERE artist_key = ?
        ORDER BY event_date DESC NULLS LAST, event_name, event_key
        LIMIT 250
    """, [artist_key])
    festivals = _rows(conn, """
        SELECT f.*,
               (SELECT COUNT(DISTINCT other.artist_key)
                FROM festival_appearances other
                WHERE other.event_key = f.event_key AND other.artist_key <> f.artist_key) AS co_billed_artist_count
        FROM festival_appearances f WHERE f.artist_key = ?
        ORDER BY COALESCE(f.event_date, f.performance_date) DESC NULLS LAST,
                 f.festival_name, f.event_key
        LIMIT 150
    """, [artist_key])
    future = _rows(conn, """
        SELECT *, future_event_key AS event_key,
               ticket_price_min AS price_min,
               ticket_price_max AS price_max,
               ticket_price_currency AS currency,
               source_system AS provider,
               retrieved_at AS latest_observation
        FROM future_events WHERE artist_key = ?
        ORDER BY event_date, event_name, future_event_key
        LIMIT 150
    """, [artist_key])
    public_ticket_market = _public_ticket_market(conn, artist_key)
    alternatives = _alternatives(peers)
    _enrich_markets_from_future(markets, future)
    ticket_evidence_count = sum(has_advertised_structured_range(event) for event in future)
    market_status = "OBSERVED" if markets else "UNKNOWN"
    festival_status = "OBSERVED" if festivals else "UNKNOWN"
    future_status = "OBSERVED" if future else "UNKNOWN"
    peer_status = "OBSERVED" if peers else "UNKNOWN"
    quick_facts = {
        "historical_events": artist.get("historical_event_count") if artist.get("historical_event_count") is not None else len(history),
        "festival_appearances": artist.get("festival_appearance_count") if artist.get("festival_appearance_count") is not None else len(festivals),
        "markets": artist.get("market_count") if artist.get("market_count") is not None else len(markets),
        "venues_played": artist.get("venues_played"),
        "future_events": len(future),
        "current_ticket_ranges": ticket_evidence_count,
        "audience_peers": len(peers),
    }
    # Stable V1 contract names plus compatibility aliases used by the existing
    # SPA while the product replaces the legacy artist page.
    quick_facts.update({
        "historical_live_events": quick_facts["historical_events"],
        "markets_played": quick_facts["markets"],
        "active_ticket_evidence": quick_facts["current_ticket_ranges"],
        "audience_affinity_available": "OBSERVED" if peers else "UNKNOWN",
    })
    artist["external_ids"] = external_ids
    artist["identity"] = {
        "name": artist.get("name"),
        "type": artist.get("artist_type") or artist.get("type"),
        "area": artist.get("area"),
        "mbid": artist.get("musicbrainz_id"),
    }
    artist["coverage_state"] = {
        "identity": "OBSERVED",
        "attention_sources": sum(1 for block in attention.values() if block["status"] == "OBSERVED"),
        "audience_peers": peer_status,
        "markets": market_status,
        "live_history": "OBSERVED" if history else "UNKNOWN",
        "festival_history": festival_status,
        "future": future_status,
        "factor_tape": factor_tape["status"],
        "sentiment": sentiment["status"],
    }
    evidence_items = _evidence_items(
        artist, attention, peers, markets, history, festivals, future
    )
    latest_knowledge = max(
        (item["knowledge_time"] for item in evidence_items if item.get("knowledge_time") is not None),
        default=None,
        key=str,
    )
    observed_panels = (
        1
        + artist["coverage_state"]["attention_sources"]
        + sum(
            1 for key in ("audience_peers", "markets", "live_history", "festival_history", "future")
            if artist["coverage_state"][key] == "OBSERVED"
        )
    )
    artist["freshness"] = (
        f"latest knowledge {latest_knowledge}" if latest_knowledge is not None else "UNKNOWN"
    )
    artist["evidence_coverage"] = f"{observed_panels}/9 panels observed"
    return {
        "contract_version": CONTRACT_VERSION,
        "release_label": RELEASE_LABEL,
        "artist": artist,
        "quick_facts": quick_facts,
        "attention": attention,
        "peers": {
            "status": peer_status,
            "label": "PILOT AUDIENCE DATA — 1% ListenBrainz sample",
            "items": peers,
            "note": "Shared listening is not local demand, ticket intent, or a booking recommendation.",
        },
        "markets": {
            "status": market_status,
            "items": markets,
            "note": "Sorted by transparent observed historical show count; absent measures remain UNKNOWN.",
        },
        "history": {"status": "OBSERVED" if history else "UNKNOWN", "items": history},
        "festivals": {"status": festival_status, "items": festivals},
        "future": {
            "status": future_status,
            "ticket_evidence_status": (
                "ADVERTISED_STRUCTURED_RANGE" if ticket_evidence_count
                else "NO_CURRENT_TICKET_EVIDENCE"
            ),
            "items": future,
            "note": (
                "Ticketmaster prices are provider-advertised structured ranges only; "
                "they are not transactions, resale prices, attendance, sell-through, or sales."
            ),
        },
        "public_ticket_market": public_ticket_market,
        "factor_tape": factor_tape,
        "what_changed": factor_tape.get("changes", []),
        "sentiment": sentiment,
        "provider_readiness": provider_readiness,
        "alternatives": {
            "status": "OBSERVED" if alternatives else "UNKNOWN",
            "items": alternatives,
            "note": "Alternatives are evidence explanations, never a weighted ranking.",
        },
        "evidence": {
            "items": evidence_items
        },
    }


def _compare_summary(payload: dict[str, Any]) -> dict[str, Any]:
    artist = payload["artist"]
    facts = payload["quick_facts"]
    attention = payload["attention"]
    markets = payload["markets"]["items"][:5]
    return {
        "artist_key": artist["artist_key"],
        "artist_name": artist["name"],
        "market_count": facts.get("markets"),
        "identity": {
            "type": artist.get("artist_type") or artist.get("type"),
            "area": artist.get("area"),
            "tier": artist.get("tier"),
        },
        "attention": {key: value["status"] for key, value in attention.items()},
        "historical_events": facts.get("historical_events"),
        "festival_appearances": facts.get("festival_appearances"),
        "future_events": facts.get("future_events"),
        "current_ticket_ranges": facts.get("current_ticket_ranges"),
        "audience_peers": facts.get("audience_peers"),
        "strongest_markets": [{
            "market_key": row.get("market_key"),
            "historical_shows": row.get("observed_shows") or row.get("historical_shows"),
            "last_play": row.get("last_play_date") or row.get("last_play"),
        } for row in markets],
        "coverage_state": artist.get("coverage_state"),
        "evidence": payload["evidence"]["items"],
    }


def compare_artists(conn, artist_a: str, artist_b: str) -> dict[str, Any] | None:
    """Return a side-by-side evidence comparison with no ranking or winner."""
    left = get_artist_security(conn, artist_a)
    right = get_artist_security(conn, artist_b)
    if left is None or right is None:
        return None
    left_summary = _compare_summary(left)
    right_summary = _compare_summary(right)
    shared = _shared_audience_edge(conn, left_summary["artist_key"], right_summary["artist_key"])
    shared_dim = {
        "label": "Audience overlap",
        "left": shared["summary"],
        "right": shared["summary"],
        "explanation": "Shared-listener evidence from the 1% ListenBrainz pilot, if an observed edge exists. Shared listening is not local demand or ticket intent.",
    }
    dimensions = [
        {"label": "Identity", "left": left_summary["identity"], "right": right_summary["identity"],
         "explanation": "Public reference identity; no identity quality score."},
        {"label": "Attention sources", "left": left_summary["attention"], "right": right_summary["attention"],
         "explanation": "Source-separated attention states; attention is not local demand."},
        {"label": "Audience peers", "left": left_summary["audience_peers"], "right": right_summary["audience_peers"],
         "explanation": "Count of available 1% ListenBrainz pilot peer edges."},
        {"label": "Strongest observed markets", "left": left_summary["strongest_markets"], "right": right_summary["strongest_markets"],
         "explanation": "Ordered by observed historical shows; UNKNOWN dates stay unknown."},
        {"label": "Live history", "left": left_summary["historical_events"], "right": right_summary["historical_events"],
         "explanation": "Descriptive observed event counts, not attendance."},
        {"label": "Festival history", "left": left_summary["festival_appearances"], "right": right_summary["festival_appearances"],
         "explanation": "Observed festival/series appearances and co-bills."},
        {"label": "Future events", "left": left_summary["future_events"], "right": right_summary["future_events"],
         "explanation": "Latest retained Ticketmaster Discovery observations."},
        {"label": "Current ticket ranges", "left": left_summary["current_ticket_ranges"], "right": right_summary["current_ticket_ranges"],
         "explanation": "Provider-advertised structured ranges only; not transactions or sales."},
        {"label": "Evidence coverage", "left": left_summary["coverage_state"], "right": right_summary["coverage_state"],
         "explanation": "Explicit observed/unknown states; no composite score."},
    ]
    if shared["has_edge"]:
        dimensions.insert(1, shared_dim)
    return {
        "contract_version": CONTRACT_VERSION,
        "release_label": RELEASE_LABEL,
        "left": left_summary,
        "right": right_summary,
        "dimensions": dimensions,
        "no_winner": True,
        "note": "No fixed weights, artist score, ranking, or booking recommendation is produced.",
    }


def _shared_audience_edge(conn, left_key: str, right_key: str) -> dict[str, Any]:
    """Look up the observed audience edge between two artists, either direction."""
    row = _one(conn, """
        SELECT p.shared_listeners, p.jaccard,
               (SELECT COUNT(*) FROM artist_markets mine
                JOIN artist_markets theirs ON mine.market_key = theirs.market_key
                WHERE mine.artist_key = p.subject_key
                  AND theirs.artist_key = p.peer_key) AS shared_markets,
               (SELECT COUNT(DISTINCT mine.event_key)
                FROM festival_appearances mine
                JOIN festival_appearances theirs ON mine.event_key = theirs.event_key
                WHERE mine.artist_key = p.subject_key
                  AND theirs.artist_key = p.peer_key) AS shared_festival_bills
        FROM artist_peers p
        WHERE (p.subject_key = ? AND p.peer_key = ?) OR (p.subject_key = ? AND p.peer_key = ?)
        ORDER BY p.shared_listeners DESC NULLS LAST
        LIMIT 1
    """, [left_key, right_key, right_key, left_key])
    if row is None:
        return {"has_edge": False, "summary": "No observed audience edge in the 1% ListenBrainz pilot."}
    parts: list[str] = []
    if row.get("shared_listeners") is not None:
        parts.append(f"{row['shared_listeners']} shared listeners")
    if row.get("jaccard") is not None:
        parts.append(f"Jaccard {row['jaccard']}")
    if row.get("shared_markets"):
        parts.append(f"{row['shared_markets']} shared markets")
    if row.get("shared_festival_bills"):
        parts.append(f"{row['shared_festival_bills']} shared festival bills")
    summary = " · ".join(parts) if parts else "Observed edge without detail"
    return {"has_edge": True, "summary": summary}
