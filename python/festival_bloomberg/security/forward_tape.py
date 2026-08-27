"""MARKET_LIQUIDITY_TAPE_V1 — P7: forward artist tape.

Turns ARTIST_SECURITY_1000 into a LIVING tape. Daily (bootstrap now, cron
later):

* Wikimedia latest complete daily pageview observation per artist
* ListenBrainz current update per artist
* YouTube channel credential state (BLOCKED_INVALID_KEY when the key is invalid
  or absent — never fabricates historical subscriber counts)

Records the latest observation per (artist, feed) in
``metrics.artist_forward_tape`` with freshness so a cron runner can append the
next day forward without re-querying history.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any

from ..attention.youtube_forward import classify_youtube_api_key

SOFTWARE_VERSION = "market_liquidity_tape_v1"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tape_key(*, artist_key: str, feed: str, period_date: date) -> str:
    m = f"{artist_key}|{feed}|{period_date.isoformat()}"
    return hashlib.sha256(m.encode("utf-8")).hexdigest()[:32]


def _upsert(conn, *, artist_key, artist_name, feed, period_date, **fields) -> None:
    key = _tape_key(artist_key=artist_key, feed=feed, period_date=period_date)
    exists = conn.execute(
        "SELECT 1 FROM metrics.artist_forward_tape WHERE tape_key = ?", [key]
    ).fetchone()
    if not exists:
        conn.execute(
            """
            INSERT INTO metrics.artist_forward_tape
                (tape_key, artist_key, artist_name, feed, period_date,
                 period_start, period_end, value, value_unit, metric_kind,
                 retrieved_at, freshness_days, status, detail, software_version,
                 rights_status, commercial_use_status, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    'TERMS_REVIEW_REQUIRED', 'PROTOTYPE_ONLY', CURRENT_TIMESTAMP)
            """,
            [
                key, artist_key, artist_name, feed, period_date.isoformat(),
                fields.get("period_start"), fields.get("period_end"),
                fields.get("value"), fields.get("value_unit"),
                fields.get("metric_kind"), fields.get("retrieved_at"),
                fields.get("freshness_days"), fields.get("status", "OBSERVED"),
                fields.get("detail"), SOFTWARE_VERSION,
            ],
        )
    else:
        conn.execute(
            """
            UPDATE metrics.artist_forward_tape
            SET value = COALESCE(?, value), period_start = COALESCE(?, period_start),
                period_end = COALESCE(?, period_end),
                retrieved_at = COALESCE(?, retrieved_at),
                freshness_days = COALESCE(?, freshness_days),
                status = ?, detail = COALESCE(?, detail)
            WHERE tape_key = ?
            """,
            [
                fields.get("value"), fields.get("period_start"),
                fields.get("period_end"), fields.get("retrieved_at"),
                fields.get("freshness_days"), fields.get("status", "OBSERVED"),
                fields.get("detail"), key,
            ],
        )


def ingest_wiki_latest_daily(conn, *, universe: list[dict[str, Any]], as_of: date | None = None) -> dict[str, Any]:
    """Record the latest complete daily wikipedia pageview observation per artist.

    Uses the real daily rows already in the warehouse — the forward tape is a
    pointer to the most recent committed daily observation, not a re-derivation.
    """
    as_of = as_of or date.today()
    keys = [a["artist_key"] for a in universe]
    if not keys:
        return {"status": "EMPTY_UNIVERSE", "rows": 0, "artists_with_latest": 0}
    rows = conn.execute(
        """
        WITH latest AS (
            SELECT artist_key,
                   MAX(period_start) AS max_start
            FROM metrics.artist_attention_observations
            WHERE metric_kind = 'pageviews'
              AND granularity = 'daily'
              AND status = 'ok'
              AND period_start <= ?
              AND artist_key IN (SELECT unnest(?))
            GROUP BY artist_key
        )
        SELECT o.artist_key, o.period_start, o.period_end, o.value,
               o.retrieved_at, o.provenance_json
        FROM metrics.artist_attention_observations o
        JOIN latest l
          ON o.artist_key = l.artist_key AND o.period_start = l.max_start
        """,
        [as_of.isoformat(), keys],
    ).fetchall()
    # some artists have multiple rows per day (batched collector artifacts); keep max value
    best: dict[str, dict] = {}
    for artist_key, start, end, value, retrieved_at, provenance in rows:
        start_dt = str(start)[:10]
        cur = best.get(artist_key)
        v = float(value) if value is not None else None
        if cur is None or (v is not None and (cur.get("value") is None or v > cur.get("value", 0))):
            best[artist_key] = {
                "period_date": date.fromisoformat(start_dt),
                "period_start": start, "period_end": end,
                "value": v, "retrieved_at": retrieved_at,
            }
    name_by_key = {}
    for a in universe:
        name_by_key[a["artist_key"]] = a.get("artist_display_name") or a.get("artist_name")
    written = 0
    for artist_key, b in best.items():
        period_date = b["period_date"]
        freshness = max(0, (as_of - period_date).days)
        _upsert(
            conn, artist_key=artist_key, artist_name=name_by_key.get(artist_key),
            feed="wiki_daily", period_date=period_date,
            period_start=b["period_start"], period_end=b["period_end"],
            value=b["value"], value_unit="pageviews", metric_kind="pageviews",
            retrieved_at=b["retrieved_at"], freshness_days=freshness,
            status="OBSERVED", detail="latest complete daily wikipedia pageviews",
        )
        written += 1
    return {
        "status": "COMPLETE",
        "rows": written,
        "artists_with_latest_daily": len(best),
        "as_of": as_of.isoformat(),
        "note": "pointers to committed daily rows; freshness tracked per artist",
    }


def ingest_listenbrainz_current(conn, *, universe: list[dict[str, Any]], as_of: date | None = None) -> dict[str, Any]:
    """Record the latest ListenBrainz aggregate per artist (all-time listens)."""
    as_of = as_of or date.today()
    keys = [a["artist_key"] for a in universe]
    if not keys:
        return {"status": "EMPTY_UNIVERSE", "rows": 0}
    rows = conn.execute(
        """
        SELECT artist_key, metric_kind, granularity, value, retrieved_at
        FROM metrics.artist_attention_observations
        WHERE metric_kind = 'LISTENBRAINZ_TOTAL_LISTEN_COUNT'
          AND granularity = 'all_time'
          AND status = 'ok'
          AND artist_key IN (SELECT unnest(?))
        """,
        [keys],
    ).fetchall()
    name_by_key = {}
    for a in universe:
        name_by_key[a["artist_key"]] = a.get("artist_display_name") or a.get("artist_name")
    written = 0
    with_value = 0
    for artist_key, _kind, _gran, value, retrieved_at in rows:
        if value is None:
            continue
        v = float(value)
        with_value += 1
        period_date = as_of  # snapshot date; LB all_time has no period range
        freshest = max(0, (as_of - retrieved_at.date()).days) if hasattr(retrieved_at, "date") else 0
        _upsert(
            conn, artist_key=artist_key, artist_name=name_by_key.get(artist_key),
            feed="listenbrainz", period_date=period_date,
            period_start=None, period_end=None,
            value=v, value_unit="listens", metric_kind="LISTENBRAINZ_TOTAL_LISTEN_COUNT",
            retrieved_at=retrieved_at, freshness_days=freshest,
            status="OBSERVED" if v else "NO_DATA",
            detail="latest all-time ListenBrainz listen count",
        )
        written += 1
    return {"status": "COMPLETE", "rows": written, "artists_with_listens": with_value, "as_of": as_of.isoformat()}


def ingest_youtube_credential(conn, *, universe: list[dict[str, Any]], api_key: str | None, as_of: date | None = None) -> dict[str, Any]:
    """Record YouTube forward-tape credential state honestly. When the key is
    invalid/absent, mark each artist channel tape entry BLOCKED_INVALID_KEY and
    do NOT fabricate subscriber counts."""
    as_of = as_of or date.today()
    state = classify_youtube_api_key(api_key)
    status = "OBSERVED" if state == "VALID" else "BLOCKED"
    detail = (
        "valid key — forward snapshots pending" if state == "VALID"
        else "no valid YOUTUBE_API_KEY; FAIL_CLOSED (no fabricated subscriber counts)"
    )
    rows = 0
    for a in universe:
        _upsert(
            conn, artist_key=a["artist_key"],
            artist_name=a.get("artist_display_name") or a.get("artist_name"),
            feed="youtube_channel", period_date=as_of,
            value=None, value_unit=None, metric_kind=None,
            retrieved_at=_utcnow(), freshness_days=0,
            status=status, detail=detail + f" state={state}",
        )
        rows += 1
    return {
        "status": "COMPLETE",
        "artists_logged": rows,
        "credential_state": state,
        "forward_tape_feed": "youtube_channel",
        "note": "BLOCKED_INVALID_KEY — no snapshots fenced until a valid key exists",
        "as_of": as_of.isoformat(),
    }


def run_forward_tape(
    conn,
    *,
    universe: list[dict[str, Any]],
    api_key: str | None,
    as_of: date | None = None,
) -> dict[str, Any]:
    as_of = as_of or date.today()
    wiki = ingest_wiki_latest_daily(conn, universe=universe, as_of=as_of)
    lb = ingest_listenbrainz_current(conn, universe=universe, as_of=as_of)
    yt = ingest_youtube_credential(conn, universe=universe, api_key=api_key, as_of=as_of)
    return {"status": "COMPLETE", "as_of": as_of.isoformat(), "feeds": {"wiki_daily": wiki, "listenbrainz": lb, "youtube": yt}}