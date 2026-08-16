"""Professional product workflows: watchlists, saved monitors, alerts, TODAY.

Single-user local product (no auth/multi-tenant complexity):

- ``core.watchlists`` / ``core.watchlist_items`` — named entity lists
  (ARTIST/FESTIVAL/TOUR/EVENT/VENUE/PROMOTER/MARKET/COMPANY).
- ``terminal.saved_monitors`` — persisted monitor view configuration.
- ``core.alerts`` — deterministic, idempotent, traceable alerts. One logical
  change creates one logical alert via a dedupe_key; re-running never spams.

Alerts are DERIVED from objective persisted changes (new events, status
changes, presales/onsales, lineup diffs, provider staleness). No AI urgency
scores and no invented facts.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

SOFTWARE_VERSION = "product_workflow_v1"

VALID_ENTITY_TYPES = (
    "ARTIST", "FESTIVAL", "TOUR", "EVENT", "VENUE", "PROMOTER", "MARKET", "COMPANY",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def watchlist_key(name: str) -> str:
    return hashlib.sha256(f"watchlist::{name.lower()}".encode("utf-8")).hexdigest()[:24]


def item_key(watchlist_key_value: str, entity_type: str, entity_key_value: str) -> str:
    material = "|".join([watchlist_key_value, entity_type, entity_key_value])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def alert_key(alert_type: str, entity_type: str, entity_key_value: str,
              provider: str | None, dedupe: str) -> str:
    material = "|".join([alert_type, entity_type, entity_key_value, provider or "", dedupe])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Watchlists
# ---------------------------------------------------------------------------
def create_watchlist(
    conn, *, name: str, description: str | None = None,
    entity_type: str | None = None, is_system: bool = False,
) -> dict[str, Any]:
    key = watchlist_key(name)
    conn.execute(
        """
        INSERT INTO core.watchlists
            (watchlist_key, name, description, entity_type, is_system, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, now(), now())
        ON CONFLICT (watchlist_key) DO UPDATE SET
            description = excluded.description, entity_type = excluded.entity_type,
            is_system = excluded.is_system, updated_at = now()
        """,
        [key, name, description, entity_type, is_system],
    )
    return {"watchlist_key": key, "name": name, "description": description,
            "entity_type": entity_type, "is_system": is_system}


def add_watchlist_item(
    conn, *, watchlist_key_value: str, entity_type: str, entity_key_value: str,
    entity_name: str | None = None, notes: str | None = None, tags: list[str] | None = None,
) -> int:
    """Add an item to a watchlist (idempotent). Returns 1 if newly added."""
    entity_type = entity_type.upper()
    key = item_key(watchlist_key_value, entity_type, entity_key_value)
    exists = conn.execute(
        "SELECT 1 FROM core.watchlist_items WHERE item_key = ?", [key]
    ).fetchone()
    if exists:
        conn.execute(
            "UPDATE core.watchlist_items SET removed_at = NULL, notes = COALESCE(?, notes) WHERE item_key = ?",
            [notes, key],
        )
        return 0
    conn.execute(
        """
        INSERT INTO core.watchlist_items
            (item_key, watchlist_key, entity_type, entity_key, entity_name,
             notes, tags, added_at, source_system)
        VALUES (?, ?, ?, ?, ?, ?, ?, now(), 'product_workflow')
        """,
        [key, watchlist_key_value, entity_type, entity_key_value, entity_name,
         notes, json.dumps(tags) if tags else None],
    )
    return 1


def remove_watchlist_item(conn, *, watchlist_key_value: str, entity_type: str,
                          entity_key_value: str) -> int:
    key = item_key(watchlist_key_value, entity_type.upper(), entity_key_value)
    conn.execute(
        "UPDATE core.watchlist_items SET removed_at = now() WHERE item_key = ?",
        [key],
    )
    return conn.execute("SELECT changes()").fetchone()[0]


def list_watchlists(conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT w.watchlist_key, w.name, w.description, w.entity_type, w.is_system,
               w.created_at, w.updated_at,
               (SELECT COUNT(*) FROM core.watchlist_items i
                WHERE i.watchlist_key = w.watchlist_key AND i.removed_at IS NULL) AS item_count
        FROM core.watchlists w ORDER BY w.is_system DESC, w.name
        """
    ).fetchall()
    return [dict(zip([c[0] for c in conn.description], r)) for r in rows]


def list_watchlist_items(conn, watchlist_key_value: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT item_key, entity_type, entity_key, entity_name, notes, tags,
               added_at, removed_at, source_system
        FROM core.watchlist_items
        WHERE watchlist_key = ? AND removed_at IS NULL
        ORDER BY entity_type, entity_name
        """,
        [watchlist_key_value],
    ).fetchall()
    return [dict(zip([c[0] for c in conn.description], r)) for r in rows]


# ---------------------------------------------------------------------------
# Saved monitors
# ---------------------------------------------------------------------------
def save_monitor(
    conn, *, name: str, entity_type: str, watchlist_key_value: str | None = None,
    filters: list[dict[str, Any]] | None = None,
    visible_columns: list[str] | None = None,
    sort: list[dict[str, str]] | None = None,
    time_horizon: str = "30D",
) -> dict[str, Any]:
    monitor_key = hashlib.sha256(f"monitor::{name.lower()}".encode("utf-8")).hexdigest()[:24]
    conn.execute(
        """
        INSERT INTO terminal.saved_monitors
            (monitor_key, name, entity_type, watchlist_key, filters, visible_columns,
             sort, time_horizon, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, now(), now())
        ON CONFLICT (monitor_key) DO UPDATE SET
            entity_type = excluded.entity_type, watchlist_key = excluded.watchlist_key,
            filters = excluded.filters, visible_columns = excluded.visible_columns,
            sort = excluded.sort, time_horizon = excluded.time_horizon,
            updated_at = now()
        """,
        [monitor_key, name, entity_type, watchlist_key_value,
         json.dumps(filters) if filters else None,
         json.dumps(visible_columns) if visible_columns else None,
         json.dumps(sort) if sort else None, time_horizon],
    )
    return {"monitor_key": monitor_key, "name": name, "entity_type": entity_type}


def list_monitors(conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT monitor_key, name, entity_type, watchlist_key, filters,
               visible_columns, sort, time_horizon, created_at, updated_at
        FROM terminal.saved_monitors ORDER BY name
        """
    ).fetchall()
    return [dict(zip([c[0] for c in conn.description], r)) for r in rows]


# ---------------------------------------------------------------------------
# Deterministic alert engine
# ---------------------------------------------------------------------------
def _persist_alert(conn, *, alert_type: str, entity_type: str, entity_key_value: str,
                   entity_name: str | None, provider: str | None, dedupe: str,
                   observed_at: str, detail: dict[str, Any],
                   source_record_id: str | None = None) -> int:
    key = alert_key(alert_type, entity_type, entity_key_value, provider, dedupe)
    exists = conn.execute(
        "SELECT 1 FROM core.alerts WHERE alert_key = ?", [key]
    ).fetchone()
    if exists:
        return 0
    conn.execute(
        """
        INSERT INTO core.alerts
            (alert_key, alert_type, entity_type, entity_key, entity_name,
             provider, observed_at, detail, dedupe_key, source_record_id,
             status, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', now())
        """,
        [key, alert_type, entity_type, entity_key_value, entity_name, provider,
         observed_at, json.dumps(detail, default=str), dedupe, source_record_id],
    )
    return 1


def generate_event_alerts(conn, *, knowledge_time: str | None = None) -> dict[str, Any]:
    """Derive deterministic change alerts from the live event snapshot corpus.

    Compares each event's LATEST snapshot against its FIRST snapshot; a
    material change in status/price/presales/onsale creates one logical alert
    (dedupe_key = event id + dimension + value, so re-runs never duplicate).
    """
    knowledge_time = knowledge_time or _now()
    summary = {"status": "RUNNING", "alerts_written": 0, "events_compared": 0}
    rows = conn.execute(
        """
        SELECT platform_object_id, event_name, artist_name, event_status,
               onsale_start, price_min, price_max, promoter, retrieved_at
        FROM events.provider_event_snapshots
        WHERE provider = 'ticketmaster'
        """
    ).fetchall()
    first: dict[str, dict[str, Any]] = {}
    latest: dict[str, dict[str, Any]] = {}
    for r in rows:
        eid = r[0]
        rec = dict(zip([c[0] for c in conn.description], r))
        if eid not in first or (r[8] or "") < (first[eid]["retrieved_at"] or ""):
            first[eid] = rec
        if eid not in latest or (r[8] or "") > (latest[eid]["retrieved_at"] or ""):
            latest[eid] = rec
    for eid in first:
        f, l = first[eid], latest[eid]
        summary["events_compared"] += 1
        entity_key_value = f"tm::{eid}"
        # Status changes.
        f_status = (f.get("event_status") or "").upper()
        l_status = (l.get("event_status") or "").upper()
        if f_status and l_status and f_status != l_status:
            mapping = {
                "CANCELLED": "EVENT_CANCELLED",
                "POSTPONED": "EVENT_POSTPONED",
                "RESCHEDULED": "EVENT_RESCHEDULED",
            }
            alert_type = mapping.get(l_status, "EVENT_STATUS_CHANGED")
            summary["alerts_written"] += _persist_alert(
                conn, alert_type=alert_type, entity_type="EVENT",
                entity_key_value=entity_key_value,
                entity_name=l.get("event_name") or l.get("artist_name"),
                provider="ticketmaster", dedupe=f"status:{l_status}",
                observed_at=knowledge_time,
                detail={"old_status": f_status, "new_status": l_status,
                        "event_name": l.get("event_name"),
                        "artist_name": l.get("artist_name")},
                source_record_id=eid,
            )
        # Onsale discovered.
        if f.get("onsale_start") is None and l.get("onsale_start"):
            summary["alerts_written"] += _persist_alert(
                conn, alert_type="ONSALE_DISCOVERED", entity_type="EVENT",
                entity_key_value=entity_key_value,
                entity_name=l.get("event_name") or l.get("artist_name"),
                provider="ticketmaster", dedupe=f"onsale:{l['onsale_start']}",
                observed_at=knowledge_time,
                detail={"onsale_start": l["onsale_start"],
                        "event_name": l.get("event_name"),
                        "artist_name": l.get("artist_name")},
                source_record_id=eid,
            )
        # Price range changed.
        f_price = (f.get("price_min"), f.get("price_max"))
        l_price = (l.get("price_min"), l.get("price_max"))
        if f_price != l_price and (f_price[0] is not None or f_price[1] is not None) \
                and (l_price[0] is not None or l_price[1] is not None):
            summary["alerts_written"] += _persist_alert(
                conn, alert_type="PRICE_RANGE_CHANGED", entity_type="EVENT",
                entity_key_value=entity_key_value,
                entity_name=l.get("event_name") or l.get("artist_name"),
                provider="ticketmaster",
                dedupe=f"price:{l_price[0]}:{l_price[1]}",
                observed_at=knowledge_time,
                detail={"old_price_min": f_price[0], "old_price_max": f_price[1],
                        "new_price_min": l_price[0], "new_price_max": l_price[1],
                        "event_name": l.get("event_name"),
                        "artist_name": l.get("artist_name")},
                source_record_id=eid,
            )
        # Promoter identified.
        if not f.get("promoter") and l.get("promoter"):
            summary["alerts_written"] += _persist_alert(
                conn, alert_type="PROMOTER_IDENTIFIED", entity_type="EVENT",
                entity_key_value=entity_key_value,
                entity_name=l.get("event_name") or l.get("artist_name"),
                provider="ticketmaster", dedupe=f"promoter:{l['promoter']}",
                observed_at=knowledge_time,
                detail={"promoter": l["promoter"], "event_name": l.get("event_name"),
                        "artist_name": l.get("artist_name")},
                source_record_id=eid,
            )
    summary["status"] = "COMPLETE"
    return summary


def generate_new_event_alerts(conn, *, knowledge_time: str | None = None) -> dict[str, Any]:
    """NEW_EVENT alerts: first-seen events in the live snapshot corpus.

    A platform_object_id seen for the FIRST time in the corpus (earliest
    retrieved_at is the corpus minimum) is a NEW_EVENT. Dedupe by event id.
    """
    knowledge_time = knowledge_time or _now()
    summary = {"status": "RUNNING", "alerts_written": 0}
    rows = conn.execute(
        """
        SELECT platform_object_id, MIN(retrieved_at) AS first_seen
        FROM events.provider_event_snapshots
        WHERE provider = 'ticketmaster'
        GROUP BY platform_object_id
        """
    ).fetchall()
    # Corpus-level earliest observation (approximate epoch start of the corpus).
    corpus_start = conn.execute(
        "SELECT MIN(retrieved_at) FROM events.provider_event_snapshots WHERE provider='ticketmaster'"
    ).fetchone()[0]
    new_ids = [r[0] for r in rows if str(r[1]) == str(corpus_start)]
    for eid in new_ids:
        info = conn.execute(
            """
            SELECT event_name, artist_name, venue_name, city, local_date, canonical_url
            FROM events.provider_event_snapshots
            WHERE platform_object_id = ? ORDER BY retrieved_at DESC LIMIT 1
            """,
            [eid],
        ).fetchone()
        if not info:
            continue
        detail = {"event_name": info[0], "artist_name": info[1],
                  "venue_name": info[2], "city": info[3],
                  "local_date": str(info[4]) if info[4] else None,
                  "canonical_url": info[5]}
        summary["alerts_written"] += _persist_alert(
            conn, alert_type="NEW_EVENT", entity_type="EVENT",
            entity_key_value=f"tm::{eid}", entity_name=info[0] or info[1],
            provider="ticketmaster", dedupe="first_seen", observed_at=knowledge_time,
            detail=detail, source_record_id=eid,
        )
    summary["status"] = "COMPLETE"
    return summary


def generate_data_provider_stale_alerts(conn, *, knowledge_time: str | None = None) -> dict[str, Any]:
    """DATA_PROVIDER_STALE alerts for providers whose latest knowledge is old."""
    knowledge_time = knowledge_time or _now()
    summary = {"status": "RUNNING", "alerts_written": 0, "stale_providers": []}
    rows = conn.execute(
        """
        SELECT source_system AS provider, MAX(retrieved_at) AS latest
        FROM metrics.artist_attention_observations
        GROUP BY source_system
        """
    ).fetchall()
    for provider, latest in rows:
        if latest is None:
            continue
        age_days = (datetime.now(timezone.utc) - latest.replace(tzinfo=timezone.utc)).days \
            if latest.tzinfo is None else (datetime.now(timezone.utc) - latest).days
        if age_days > 14:
            summary["stale_providers"].append({"provider": provider, "age_days": int(age_days)})
            summary["alerts_written"] += _persist_alert(
                conn, alert_type="DATA_PROVIDER_STALE", entity_type="PROVIDER",
                entity_key_value=f"provider::{provider}", entity_name=provider,
                provider=provider, dedupe=f"age:{int(age_days)}", observed_at=knowledge_time,
                detail={"age_days": int(age_days), "latest_knowledge": str(latest)},
            )
    summary["status"] = "COMPLETE"
    return summary


def list_alerts(conn, *, limit: int = 100, entity_key_value: str | None = None) -> list[dict[str, Any]]:
    sql = """
        SELECT alert_key, alert_type, entity_type, entity_key, entity_name,
               provider, observed_at, detail, status, ingested_at
        FROM core.alerts WHERE 1=1
    """
    params: list[Any] = []
    if entity_key_value:
        sql += " AND entity_key = ?"
        params.append(entity_key_value)
    sql += " ORDER BY observed_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [dict(zip([c[0] for c in conn.description], r)) for r in rows]


# ---------------------------------------------------------------------------
# TODAY home view — WHAT CHANGED IN MY MUSIC UNIVERSE
# ---------------------------------------------------------------------------
def _identity_conflicts(conn, limit: int) -> list[dict[str, Any]]:
    """Identity conflicts/disagreements, if the table exists (defensive)."""
    try:
        rows = conn.execute(
            "SELECT artist_key, issue FROM core.identity_conflicts "
            "ORDER BY observed_at DESC LIMIT ?", [limit],
        ).fetchall()
        return [dict(zip([c[0] for c in conn.description], r)) for r in rows]
    except Exception:
        return []


def build_today(conn, *, limit: int = 50) -> dict[str, Any]:
    """TODAY: sections for watchlist, ticketing, catalysts, attention, live
    market, and data health. Channels stay separate; no urgency scores."""
    now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d")
    sections: dict[str, Any] = {}

    watched = conn.execute(
        """
        SELECT entity_key, entity_name, entity_type FROM core.watchlist_items
        WHERE removed_at IS NULL AND entity_type IN ('ARTIST', 'FESTIVAL', 'TOUR')
        ORDER BY entity_name
        """
    ).fetchall()
    watched_keys = {r[0] for r in watched}
    # NEW_EVENT / NEW_TOUR / NEW_FESTIVAL_APPEARANCE alerts touching
    # watched entities since the ledger began.
    new_events = conn.execute(
        """
        SELECT alert_key, alert_type, entity_type, entity_key, entity_name,
               observed_at, detail
        FROM core.alerts
        WHERE alert_type IN ('NEW_EVENT', 'NEW_TOUR', 'NEW_FESTIVAL_APPEARANCE')
          AND status = 'ACTIVE'
          AND (entity_key IN (SELECT entity_key FROM core.watchlist_items
                              WHERE removed_at IS NULL)
               OR entity_type IN ('FESTIVAL', 'TOUR'))
        ORDER BY observed_at DESC LIMIT ?
        """,
        [limit],
    ).fetchall()
    new_event_cols = [c[0] for c in conn.description]
    sections["watchlist"] = {
        "watched_entities": len(watched),
        "watched_names": [r[1] for r in watched],
        "new_events": [dict(zip(new_event_cols, r)) for r in new_events],
    }

    # 2. Ticketing — recent presales/onsales/price changes from the alert ledger.
    ticketing_types = ("PRESALE_DISCOVERED", "ONSALE_DISCOVERED", "PRICE_RANGE_CHANGED",
                       "EVENT_CANCELLED", "EVENT_POSTPONED", "EVENT_RESCHEDULED")
    placeholders = ",".join("?" for _ in ticketing_types)
    ticketing = conn.execute(
        f"""
        SELECT alert_key, alert_type, entity_name, provider, observed_at, detail
        FROM core.alerts
        WHERE alert_type IN ({placeholders}) AND status = 'ACTIVE'
        ORDER BY observed_at DESC LIMIT ?
        """,
        [*ticketing_types, limit],
    ).fetchall()
    ticketing_rows = [dict(zip([c[0] for c in conn.description], r)) for r in ticketing]
    sections["ticketing"] = {
        "new_onsales": [r for r in ticketing_rows if r["alert_type"] == "ONSALE_DISCOVERED"],
        "new_presales": [r for r in ticketing_rows if r["alert_type"] == "PRESALE_DISCOVERED"],
        "status_changes": [r for r in ticketing_rows
                           if r["alert_type"] in ("EVENT_CANCELLED", "EVENT_POSTPONED",
                                                   "EVENT_RESCHEDULED", "PRICE_RANGE_CHANGED")],
    }

    # 3. Catalysts — recent news mentions (metadata only).
    news = conn.execute(
        """
        SELECT mention_id, entity_name, article_url, title, publication_time, domain
        FROM terminal.news_mentions
        ORDER BY publication_time DESC LIMIT ?
        """,
        [limit],
    ).fetchall()
    sections["catalysts"] = {
        "news": [dict(zip([c[0] for c in conn.description], r)) for r in news],
    }

    # 4. Attention — most recently updated attention observations.
    attention = conn.execute(
        """
        SELECT artist_key, article_title, metric_kind, source_system,
               value_sum, value_unit, retrieved_at, period_start
        FROM metrics.artist_attention_observations
        WHERE status = 'ok'
        ORDER BY retrieved_at DESC LIMIT ?
        """,
        [limit],
    ).fetchall()
    attention_rows = [dict(zip([c[0] for c in conn.description], r)) for r in attention]
    for r in attention_rows:
        r["artist_name"] = str(r["artist_key"]).replace("name::", "")
    sections["attention"] = {"movers": attention_rows}

    # 5. Live market — upcoming events by market density.
    market = conn.execute(
        """
        SELECT city AS market, COUNT(DISTINCT platform_object_id) AS event_count
        FROM events.provider_event_snapshots
        WHERE provider = 'ticketmaster' AND local_date >= ?
        GROUP BY city ORDER BY event_count DESC LIMIT 12
        """,
        [today_str],
    ).fetchall()
    sections["live_market"] = {
        "busy_markets": [dict(zip([c[0] for c in conn.description], r)) for r in market],
    }

    # 6. Data health — provider staleness + identity conflicts.
    health = conn.execute(
        """
        SELECT provider, operational_status, failure_count, rate_limit_count,
               last_success_at AS last_seen
        FROM terminal.provider_health ORDER BY provider
        """
    ).fetchall()
    health_rows = [dict(zip([c[0] for c in conn.description], r)) for r in health]
    sections["data_health"] = {
        "providers": health_rows,
        "provider_failures": [
            {**h, "issue": h["operational_status"]}
            for h in health_rows if h.get("operational_status") not in (None, "OPERATIONAL", "NOT_CONFIGURED")
        ],
        "identity_conflicts": _identity_conflicts(conn, limit),
        "alerts_recent": len(list_alerts(conn, limit=10)),
    }
    return {
        "generated_at": now.isoformat(),
        "sections": sections,
    }


# ---------------------------------------------------------------------------
# Default (system, user-editable) watchlists from deterministic real data
# ---------------------------------------------------------------------------
def create_default_watchlists(conn) -> dict[str, Any]:
    """Ship clearly-marked USER-EDITABLE system watchlists derived from REAL
    data (never hard-coded subjective talent recommendations)."""
    created = []

    # Major US festivals (from the festival spine).
    fest_wl = create_watchlist(conn, name="Major US Festivals",
                               description="US festival series from the MusicBrainz festival spine",
                               entity_type="FESTIVAL", is_system=True)
    created.append(fest_wl["name"])
    fest_rows = conn.execute(
        """
        SELECT series_key, name FROM core.event_series
        WHERE series_type = 'FESTIVAL'
        ORDER BY name LIMIT 60
        """
    ).fetchall()
    for key, name in fest_rows:
        add_watchlist_item(conn, watchlist_key_value=fest_wl["watchlist_key"],
                           entity_type="FESTIVAL", entity_key_value=key, entity_name=name)

    # Active tours (TOUR series with events in 2024+).
    tour_wl = create_watchlist(conn, name="Active Tours",
                               description="Tour series with events in the reference graph",
                               entity_type="TOUR", is_system=True)
    created.append(tour_wl["name"])
    tour_rows = conn.execute(
        """
        SELECT s.series_key, s.name FROM core.event_series s
        JOIN core.series_events se ON se.series_key = s.series_key
        JOIN raw.musicbrainz_event e ON e.mbid = se.event_mbid
        WHERE s.series_type = 'TOUR' AND e.begin_date >= '2024-01-01'
        GROUP BY s.series_key, s.name ORDER BY s.name LIMIT 60
        """
    ).fetchall()
    for key, name in tour_rows:
        add_watchlist_item(conn, watchlist_key_value=tour_wl["watchlist_key"],
                           entity_type="TOUR", entity_key_value=key, entity_name=name)

    # High-activity artists (most event-performer relations in the graph).
    artist_wl = create_watchlist(conn, name="High-Activity Artists",
                                 description="Artists with the most reference-graph event appearances",
                                 entity_type="ARTIST", is_system=True)
    created.append(artist_wl["name"])
    artist_rows = conn.execute(
        """
        SELECT artist_mbid, artist_name, COUNT(*) AS n
        FROM core.event_performers
        GROUP BY artist_mbid, artist_name
        ORDER BY n DESC LIMIT 60
        """
    ).fetchall()
    for mbid, name, _n in artist_rows:
        add_watchlist_item(conn, watchlist_key_value=artist_wl["watchlist_key"],
                           entity_type="ARTIST",
                           entity_key_value=f"mbid::{mbid}", entity_name=name)

    return {"created": created}
