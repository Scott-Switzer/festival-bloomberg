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
    exists = conn.execute(
        "SELECT 1 FROM core.watchlist_items WHERE item_key = ? AND removed_at IS NULL",
        [key],
    ).fetchone()
    conn.execute(
        "UPDATE core.watchlist_items SET removed_at = now() WHERE item_key = ?",
        [key],
    )
    return 1 if exists else 0


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
# Explicit provider acquisition runs (migration 030)
# ---------------------------------------------------------------------------
def start_acquisition_run(conn, *, provider: str, operation: str,
                          run_id: str | None = None) -> dict[str, Any]:
    """Open a logical acquisition run; every snapshot gets this run_id.

    Returns the run_id. Idempotent: re-opening the same run_id updates
    started_at only if the run is still RUNNING.
    """
    run_id = run_id or f"{provider}::{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')}"
    conn.execute(
        """
        INSERT INTO audit.provider_acquisition_runs
            (run_id, provider, operation, started_at, status)
        VALUES (?, ?, ?, now(), 'RUNNING')
        ON CONFLICT (run_id) DO NOTHING
        """,
        [run_id, provider, operation],
    )
    return run_id


def complete_acquisition_run(conn, *, run_id: str, status: str = "COMPLETE",
                             request_count: int = 0, record_count: int = 0,
                             error_count: int = 0, note: str | None = None) -> None:
    """Close a run; the alert engine compares latest COMPLETE vs prior."""
    conn.execute(
        """
        UPDATE audit.provider_acquisition_runs
        SET completed_at = now(), status = ?, request_count = ?,
            record_count = ?, error_count = ?, note = ?
        WHERE run_id = ?
        """,
        [status, request_count, record_count, error_count, note, run_id],
    )


# ---------------------------------------------------------------------------
# Deterministic alert engine
# ---------------------------------------------------------------------------
def _persist_alert(conn, *, alert_type: str, entity_type: str, entity_key_value: str,
                   entity_name: str | None, provider: str | None, dedupe: str,
                   observed_at: str, detail: dict[str, Any],
                   source_record_id: str | None = None,
                   related: list[dict[str, Any]] | None = None) -> int:
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
    # Related-entity graph: every alert links to everything it touches, so a
    # watchlist holding ARTIST mbid::X can surface an EVENT tm::123 alert.
    if related:
        _persist_alert_related(conn, alert_key_value=key, related=related)
    return 1


def _persist_alert_related(conn, *, alert_key_value: str,
                           related: list[dict[str, Any]]) -> int:
    """Persist related-entity edges for an alert (idempotent, source-backed)."""
    written = 0
    for r in related:
        etype = r.get("entity_type")
        ekey = r.get("entity_key")
        rel = r.get("relationship")
        if not etype or not ekey or not rel:
            continue
        rel_key = hashlib.sha256(
            "|".join([alert_key_value, etype, ekey, rel]).encode("utf-8")
        ).hexdigest()[:32]
        conn.execute(
            """
            INSERT OR IGNORE INTO core.alert_related_entities
                (relation_key, alert_key, entity_type, entity_key, relationship,
                 entity_name, source, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, now())
            """,
            [rel_key, alert_key_value, etype, ekey, rel, r.get("entity_name"),
             r.get("source") or "ticketmaster"],
        )
        written += 1
    return written


def _related_entities_for_event(conn, *, eid: str) -> list[dict[str, Any]]:
    """Source-backed related entities for a Ticketmaster event alert.

    Attaches EVENT (self), resolved ARTISTs (from the resolution ledger),
    VENUE, MARKET (city), and PROMOTER — only from columns/ledgers that
    actually exist. No guessed relationships.
    """
    row = conn.execute(
        """
        SELECT attractions, venue_id, venue_name, city, promoter
        FROM events.provider_event_snapshots
        WHERE platform_object_id = ? AND provider = 'ticketmaster'
        ORDER BY (attractions IS NOT NULL) DESC, retrieved_at DESC LIMIT 1
        """,
        [eid],
    ).fetchone()
    if not row:
        return []
    attractions, venue_id, venue_name, city, promoter = row
    related: list[dict[str, Any]] = [{
        "entity_type": "EVENT", "entity_key": f"tm::{eid}",
        "relationship": "EVENT_SELF", "entity_name": None,
    }]
    # Resolved artists: attraction id -> canonical artist via resolution ledger.
    artist_related: list[dict[str, Any]] = []
    try:
        items = json.loads(attractions) if isinstance(attractions, str) else attractions
    except (ValueError, TypeError):
        items = []
    if isinstance(items, list):
        for it in items:
            aid = (it or {}).get("ticketmaster_attraction_id")
            if not aid:
                continue
            resolved = conn.execute(
                """
                SELECT artist_key, artist_mbid FROM identity.ticketmaster_artist_resolutions
                WHERE attraction_id = ? AND resolution_status = 'MATCHED_ARTIST'
                  AND artist_key IS NOT NULL
                ORDER BY knowledge_time DESC LIMIT 1
                """,
                [aid],
            ).fetchone()
            if not resolved:
                continue
            artist_key, artist_mbid = resolved
            artist_related.append({
                "entity_type": "ARTIST", "entity_key": artist_key,
                "relationship": "EVENT_PRIMARY_ARTIST" if len(artist_related) == 0 else "EVENT_ARTIST",
                "entity_name": None,
            })
    related.extend(artist_related)
    if venue_id or venue_name:
        related.append({
            "entity_type": "VENUE",
            "entity_key": f"tm-venue::{venue_id}" if venue_id else f"venue-name::{venue_name}",
            "relationship": "EVENT_VENUE", "entity_name": venue_name,
        })
    if city:
        related.append({
            "entity_type": "MARKET", "entity_key": f"market::{city}",
            "relationship": "EVENT_MARKET", "entity_name": city,
        })
    if promoter:
        related.append({
            "entity_type": "PROMOTER", "entity_key": f"promoter::{promoter}",
            "relationship": "EVENT_PROMOTER", "entity_name": promoter,
        })
    return related


def _presale_signature(presales: Any) -> frozenset[tuple[str, str, str]]:
    """Canonical order-independent set of (name, start, end) presale entries."""
    if not presales:
        return frozenset()
    try:
        items = json.loads(presales) if isinstance(presales, str) else presales
    except (ValueError, TypeError):
        return frozenset()
    entries = []
    if isinstance(items, list):
        for it in items:
            if isinstance(it, dict):
                entries.append((it.get("name") or "", it.get("start") or "", it.get("end") or ""))
    return frozenset(entries)


def _compare_consecutive(conn, *, eid: str, prev: dict[str, Any],
                         cur: dict[str, Any], knowledge_time: str,
                         related: list[dict[str, Any]] | None = None) -> int:
    """Emit alerts for the transition prev -> cur (one logical change each)."""
    written = 0
    entity_key_value = f"tm::{eid}"
    name = cur.get("event_name") or cur.get("artist_name")
    base = {"event_name": cur.get("event_name"), "artist_name": cur.get("artist_name")}

    f_status = (prev.get("event_status") or "").upper()
    l_status = (cur.get("event_status") or "").upper()
    if f_status and l_status and f_status != l_status:
        mapping = {"CANCELLED": "EVENT_CANCELLED", "POSTPONED": "EVENT_POSTPONED",
                   "RESCHEDULED": "EVENT_RESCHEDULED"}
        alert_type = mapping.get(l_status, "EVENT_STATUS_CHANGED")
        written += _persist_alert(
            conn, alert_type=alert_type, entity_type="EVENT", entity_key_value=entity_key_value,
            entity_name=name, provider="ticketmaster", dedupe=f"status:{f_status}->{l_status}",
            observed_at=knowledge_time,
            detail={**base, "old_status": f_status, "new_status": l_status}, source_record_id=eid,
            related=related)

    if not prev.get("onsale_start") and cur.get("onsale_start"):
        written += _persist_alert(
            conn, alert_type="ONSALE_DISCOVERED", entity_type="EVENT", entity_key_value=entity_key_value,
            entity_name=name, provider="ticketmaster", dedupe=f"onsale:{cur['onsale_start']}",
            observed_at=knowledge_time,
            detail={**base, "onsale_start": cur["onsale_start"]}, source_record_id=eid,
            related=related)

    new_presales = _presale_signature(cur.get("presales")) - _presale_signature(prev.get("presales"))
    for pname, pstart, pend in sorted(new_presales):
        written += _persist_alert(
            conn, alert_type="PRESALE_DISCOVERED", entity_type="EVENT", entity_key_value=entity_key_value,
            entity_name=name, provider="ticketmaster", dedupe=f"presale:{pname}:{pstart}",
            observed_at=knowledge_time,
            detail={**base, "presale_name": pname, "presale_start": pstart, "presale_end": pend},
            source_record_id=eid, related=related)

    f_price = (prev.get("price_min"), prev.get("price_max"))
    l_price = (cur.get("price_min"), cur.get("price_max"))
    if f_price != l_price and (f_price[0] is not None or f_price[1] is not None) \
            and (l_price[0] is not None or l_price[1] is not None):
        written += _persist_alert(
            conn, alert_type="PRICE_RANGE_CHANGED", entity_type="EVENT", entity_key_value=entity_key_value,
            entity_name=name, provider="ticketmaster", dedupe=f"price:{l_price[0]}:{l_price[1]}",
            observed_at=knowledge_time,
            detail={**base, "old_price_min": f_price[0], "old_price_max": f_price[1],
                    "new_price_min": l_price[0], "new_price_max": l_price[1]}, source_record_id=eid,
            related=related)

    if not prev.get("promoter") and cur.get("promoter"):
        written += _persist_alert(
            conn, alert_type="PROMOTER_IDENTIFIED", entity_type="EVENT", entity_key_value=entity_key_value,
            entity_name=name, provider="ticketmaster", dedupe=f"promoter:{cur['promoter']}",
            observed_at=knowledge_time, detail={**base, "promoter": cur["promoter"]}, source_record_id=eid,
            related=related)
    return written


def generate_event_alerts(conn, *, knowledge_time: str | None = None) -> dict[str, Any]:
    """Derive change alerts by comparing each event's CONSECUTIVE snapshots.

    Compares each snapshot against the PREVIOUS distinct snapshot (ordered by
    retrieved_at), not first-vs-latest, so a value that changes and then
    reverts still produces two alerts. dedupe_key = event + dimension + value,
    so re-runs are idempotent.
    """
    knowledge_time = knowledge_time or _now()
    summary = {"status": "RUNNING", "alerts_written": 0, "events_compared": 0}
    rows = conn.execute(
        """
        SELECT platform_object_id, event_name, artist_name, event_status,
               onsale_start, price_min, price_max, promoter, presales, retrieved_at
        FROM events.provider_event_snapshots
        WHERE provider = 'ticketmaster'
        ORDER BY platform_object_id, retrieved_at
        """
    ).fetchall()
    cols = [c[0] for c in conn.description]
    prev: dict[str, dict[str, Any]] = {}
    related_cache: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        rec = dict(zip(cols, r))
        eid = rec["platform_object_id"]
        if eid in prev:
            summary["events_compared"] += 1
            if eid not in related_cache:
                related_cache[eid] = _related_entities_for_event(conn, eid=eid)
            summary["alerts_written"] += _compare_consecutive(
                conn, eid=eid, prev=prev[eid], cur=rec, knowledge_time=knowledge_time,
                related=related_cache[eid])
        prev[eid] = rec
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
    # NEW_EVENT requires a PRIOR run to compare against. Prefer explicit
    # acquisition runs (migration 030); fall back to distinct retrieved_at
    # batches for historical snapshots without acquisition_run_id.
    runs = conn.execute(
        """
        SELECT run_id FROM audit.provider_acquisition_runs
        WHERE provider = 'ticketmaster' AND status = 'COMPLETE'
        ORDER BY completed_at
        """
    ).fetchall()
    if runs:
        latest_run = runs[-1][0]
        # New = present in the latest run AND absent from ALL prior runs.
        new_ids = [r[0] for r in conn.execute(
            """
            WITH latest AS (
                SELECT DISTINCT platform_object_id
                FROM events.provider_event_snapshots
                WHERE provider = 'ticketmaster' AND acquisition_run_id = ?
            ),
            prior AS (
                SELECT DISTINCT platform_object_id
                FROM events.provider_event_snapshots
                WHERE provider = 'ticketmaster'
                  AND acquisition_run_id IS NOT NULL
                  AND acquisition_run_id != ?
            )
            SELECT l.platform_object_id FROM latest l
            ANTI JOIN prior p USING (platform_object_id)
            """,
            [latest_run, latest_run],
        ).fetchall()]
    else:
        batches = conn.execute(
            "SELECT DISTINCT retrieved_at FROM events.provider_event_snapshots "
            "WHERE provider='ticketmaster' ORDER BY retrieved_at"
        ).fetchall()
        batch_times = [r[0] for r in batches]
        if len(batch_times) < 2:
            summary["status"] = "COMPLETE"
            summary["note"] = "single snapshot corpus: NEW_EVENT requires a prior run"
            return summary
        latest_batch = batch_times[-1]
        new_ids = [r[0] for r in rows if str(r[1]) == str(latest_batch)]
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
            related=_related_entities_for_event(conn, eid=eid),
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
    """Identity conflicts/disagreements between providers.

    Reads the real ``core.identity_conflicts`` columns (entity_key, not
    artist_key); no exception suppression — a schema mismatch must surface,
    not silently report zero conflicts.
    """
    rows = conn.execute(
        "SELECT conflict_key, entity_type, entity_key, provider_a, provider_b, "
        "value_a, value_b, issue, resolution_status, observed_at "
        "FROM core.identity_conflicts ORDER BY observed_at DESC LIMIT ?", [limit],
    ).fetchall()
    return [dict(zip([c[0] for c in conn.description], r)) for r in rows]


def build_today(conn, workspace_conn=None, *, limit: int = 50) -> dict[str, Any]:
    """TODAY: sections for watchlist, ticketing, catalysts, attention, live
    market, and data health. Channels stay separate; no urgency scores.

    ``conn`` is the serving snapshot (alerts, news, evidence);
    ``workspace_conn`` holds the watchlist. When omitted, both roles share
    ``conn`` (single-DB tests/backwards compatibility).
    """
    if workspace_conn is None:
        workspace_conn = conn
    now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d")
    sections: dict[str, Any] = {}

    watched = workspace_conn.execute(
        """
        SELECT entity_key, entity_name, entity_type FROM core.watchlist_items
        WHERE removed_at IS NULL AND entity_type IN ('ARTIST', 'FESTIVAL', 'TOUR')
        ORDER BY entity_name
        """
    ).fetchall()
    watched_keys = {
        r[0] for r in workspace_conn.execute(
            "SELECT entity_key FROM core.watchlist_items WHERE removed_at IS NULL"
        ).fetchall()
    }
    # Personalized: alerts whose RELATED-ENTITY graph touches a watched
    # entity (an EVENT tm::123 alert surfaces for a watched ARTIST mbid::X
    # via the alert_related_entities edge), plus alerts whose own entity is
    # watched directly. Global FESTIVAL/TOUR alerts are NOT mixed in here.
    # The watchlist and the alert ledger live in different storage roles, so
    # combine in application code rather than one cross-DB join.
    candidate_alerts = conn.execute(
        """
        SELECT DISTINCT a.alert_key, a.alert_type, a.entity_type, a.entity_key,
               a.entity_name, a.observed_at, a.detail,
               re.entity_type AS watched_via_type, re.entity_key AS watched_via_key,
               re.relationship AS watched_via_relationship
        FROM core.alerts a
        LEFT JOIN core.alert_related_entities re ON re.alert_key = a.alert_key
        WHERE a.alert_type IN ('NEW_EVENT', 'NEW_TOUR', 'NEW_FESTIVAL_APPEARANCE')
          AND a.status = 'ACTIVE'
        ORDER BY a.observed_at DESC LIMIT ?
        """,
        [max(limit * 20, 500)],
    ).fetchall()
    new_event_cols = [c[0] for c in conn.description]
    new_event_rows = []
    for r in candidate_alerts:
        rec = dict(zip(new_event_cols, r))
        if rec.get("entity_key") not in watched_keys and rec.get("watched_via_key") not in watched_keys:
            continue
        detail = rec.get("detail") or {}
        new_event_rows.append({
            "alert_key": rec.get("alert_key"),
            "alert_type": rec.get("alert_type"),
            "entity_type": rec.get("entity_type"),
            "entity_key": rec.get("entity_key"),
            "entity_name": rec.get("entity_name") or detail.get("event_name") or detail.get("artist_name"),
            "event_name": rec.get("entity_name") or detail.get("event_name") or detail.get("artist_name"),
            "observed_at": rec.get("observed_at"),
            "first_seen_at": rec.get("observed_at"),
            "provider": "ticketmaster",
            "watched_via_type": rec.get("watched_via_type"),
            "watched_via_key": rec.get("watched_via_key"),
            "watched_via_relationship": rec.get("watched_via_relationship"),
        })
        if len(new_event_rows) >= limit:
            break
    sections["watchlist"] = {
        "watched_entities": len(watched),
        "watched_names": [r[1] for r in watched],
        "new_events": new_event_rows,
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

    def _ticketing_contract(rec: dict[str, Any]) -> dict[str, Any]:
        """Normalized TODAY contract: flat fields, never nested detail JSON.

        The SPA renders ``event_name / onsale_start / presale_start / status``
        at top level; every ticketing alert is flattened here once.
        """
        detail = rec.get("detail") or {}
        if isinstance(detail, str):
            try:
                detail = json.loads(detail)
            except (ValueError, TypeError):
                detail = {}
        name = rec.get("entity_name") or detail.get("event_name") or detail.get("artist_name")
        return {
            "alert_key": rec.get("alert_key"),
            "alert_type": rec.get("alert_type"),
            "event_key": rec.get("entity_key"),
            "event_name": name,
            "artist_name": detail.get("artist_name"),
            "observed_at": rec.get("observed_at"),
            "provider": rec.get("provider"),
            "onsale_start": detail.get("onsale_start"),
            "presale_start": detail.get("presale_start"),
            "presale_name": detail.get("presale_name"),
            "status": detail.get("new_status") or detail.get("old_status"),
            "old_status": detail.get("old_status"),
            "new_status": detail.get("new_status"),
            "old_price_min": detail.get("old_price_min"),
            "old_price_max": detail.get("old_price_max"),
            "new_price_min": detail.get("new_price_min"),
            "new_price_max": detail.get("new_price_max"),
            "promoter": detail.get("promoter"),
            "source_record_id": rec.get("source_record_id"),
        }

    sections["ticketing"] = {
        "new_onsales": [_ticketing_contract(r) for r in ticketing_rows
                         if r["alert_type"] == "ONSALE_DISCOVERED"],
        "new_presales": [_ticketing_contract(r) for r in ticketing_rows
                          if r["alert_type"] == "PRESALE_DISCOVERED"],
        "status_changes": [_ticketing_contract(r) for r in ticketing_rows
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
def create_default_watchlists(conn, workspace_conn=None) -> dict[str, Any]:
    """Ship clearly-marked USER-EDITABLE system watchlists derived from REAL
    data (never hard-coded subjective talent recommendations).

    Evidence is read from ``conn`` (serving/canonical); watchlists are written
    to ``workspace_conn`` (defaults to ``conn`` for single-DB callers).
    """
    if workspace_conn is None:
        workspace_conn = conn
    created = []

    # Major festivals (from the festival spine). The list name does NOT claim
    # US scope because no reliable country filter exists on the spine yet
    # (raw.musicbrainz_place.area is a city/market, not a country).
    fest_wl = create_watchlist(workspace_conn, name="Major Festivals",
                               description="Festival series from the MusicBrainz festival spine (global; geography not yet filtered)",
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
        add_watchlist_item(workspace_conn, watchlist_key_value=fest_wl["watchlist_key"],
                           entity_type="FESTIVAL", entity_key_value=key, entity_name=name)

    # Active tours (TOUR series with events in 2024+).
    tour_wl = create_watchlist(workspace_conn, name="Active Tours",
                               description="Tour series with events dated 2024 or later",
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
        add_watchlist_item(workspace_conn, watchlist_key_value=tour_wl["watchlist_key"],
                           entity_type="TOUR", entity_key_value=key, entity_name=name)

    # High-activity artists (most event-performer relations in the graph).
    artist_wl = create_watchlist(workspace_conn, name="High-Activity Artists",
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
        add_watchlist_item(workspace_conn, watchlist_key_value=artist_wl["watchlist_key"],
                           entity_type="ARTIST",
                           entity_key_value=f"mbid::{mbid}", entity_name=name)

    return {"created": created}
