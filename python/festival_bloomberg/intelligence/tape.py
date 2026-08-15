"""The canonical activity tape — append-only "what changed" ledger.

Every meaningful, externally observable transition becomes one row. The tape is
the homepage: *what changed in live entertainment?*

Invariants enforced here (and tested):

- UNCHANGED provider polls never become tape entries.
- Re-derivation is idempotent: a stable ``dedupe_key`` per (activity type,
  source provider, source record) means running the derivation twice never
  duplicates a row and never rewrites history.
- ``observed_at`` (when we saw it) is kept separate from ``effective_at``
  (when it happened); they are never collapsed.
- A derivation step only emits entries for evidence that actually exists in the
  warehouse. Missing evidence -> no row (UNKNOWN is never fabricated as a row).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any

#: Closed activity-type enum. Unknown types are rejected, not stored.
ACTIVITY_TYPES: frozenset[str] = frozenset(
    {
        "EVENT_DISCOVERED",
        "EVENT_ANNOUNCEMENT_OBSERVED",
        "PRESALE_OBSERVED",
        "ONSALE_OBSERVED",
        "PRICE_CHANGED",
        "EVENT_STATUS_CHANGED",
        "EVENT_CANCELLED",
        "EVENT_POSTPONED",
        "EVENT_RESCHEDULED",
        "VENUE_CHANGED",
        "PROMOTER_IDENTIFIED",
        "TOUR_DATE_ADDED",
        "NEW_ARTIST_MARKET_EVENT",
        "FESTIVAL_LINEUP_CHANGE",
        "WIKIMEDIA_ATTENTION_SPIKE",
        "LISTENBRAINZ_ACTIVITY_CHANGE",
        "YOUTUBE_ACTIVITY_CHANGE",
        "SOCIAL_MENTION_BURST",
        "NEWS_MENTION",
        "TOUR_ANNOUNCEMENT",
        "FESTIVAL_LINEUP_ANNOUNCEMENT",
        "WEATHER_ALERT",
        "FORECAST_RISK_CHANGED",
        "OUTCOME_PUBLISHED",
    }
)

#: entity_type values the tape accepts.
ENTITY_TYPES: frozenset[str] = frozenset(
    {"ARTIST", "EVENT", "VENUE", "MARKET", "FESTIVAL", "PROMOTER", "TOUR"}
)


def dedupe_key(*parts: object) -> str:
    """Stable, deterministic tape dedupe key from (type, provider, record)."""
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_tape_row(
    *,
    activity_type: str,
    entity_type: str,
    entity_id: str,
    observed_at: datetime,
    source_provider: str,
    source_record_id: str,
    knowledge_time: datetime,
    rights_status: str,
    evidence_class: str = "OBSERVED_PUBLIC",
    effective_at: datetime | None = None,
    artist_id: str | None = None,
    event_id: str | None = None,
    venue_id: str | None = None,
    market_id: str | None = None,
    festival_id: str | None = None,
    old_value_json: dict | None = None,
    new_value_json: dict | None = None,
    source_url: str | None = None,
    software_version: str | None = None,
    activity_id: str | None = None,
) -> dict[str, Any]:
    """Build one validated tape row. Rejects unknown activity/entity types."""
    if activity_type not in ACTIVITY_TYPES:
        raise ValueError(f"unknown activity_type: {activity_type}")
    if entity_type not in ENTITY_TYPES:
        raise ValueError(f"unknown entity_type: {entity_type}")
    key = dedupe_key(activity_type, entity_type, entity_id, source_provider, source_record_id)
    return {
        "activity_id": activity_id or str(uuid.uuid4()),
        "observed_at": observed_at,
        "effective_at": effective_at,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "activity_type": activity_type,
        "artist_id": artist_id,
        "event_id": event_id,
        "venue_id": venue_id,
        "market_id": market_id,
        "festival_id": festival_id,
        "source_provider": source_provider,
        "source_record_id": source_record_id,
        "old_value_json": json.dumps(old_value_json, default=str) if old_value_json is not None else None,
        "new_value_json": json.dumps(new_value_json, default=str) if new_value_json is not None else None,
        "evidence_class": evidence_class,
        "rights_status": rights_status,
        "source_url": source_url,
        "knowledge_time": knowledge_time,
        "dedupe_key": key,
        "software_version": software_version,
    }


def _as_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def derive_tape_entries(conn) -> list[dict[str, Any]]:
    """Derive tape entries from the persisted warehouse (never fabricates).

    Reads the real corpus and emits one row per genuinely new transition. A
    caller that inserts only these rows (deduplicated by ``dedupe_key``) keeps
    the tape an honest, idempotent projection of the warehouse.
    """
    rows: list[dict[str, Any]] = []

    # 1. EVENT_DISCOVERED from the forward watchlist (first-seen evidence).
    for r in conn.execute(
        "SELECT watch_event_id, provider, provider_event_id, artist_name, venue_name, "
        "       market, event_date, first_seen_at, rights_status, source_url, "
        "       software_version "
        "FROM flywheel.forward_watch_events "
        "ORDER BY first_seen_at"
    ).fetchall():
        (watch_id, provider, pe_id, artist, venue, market, event_date,
         first_seen, rights, url, sw) = r
        observed = _as_dt(first_seen)
        if observed is None:
            continue
        entity_id = watch_id
        eff = _as_dt(event_date)
        rows.append(build_tape_row(
            activity_type="EVENT_DISCOVERED",
            entity_type="EVENT",
            entity_id=entity_id,
            observed_at=observed,
            effective_at=eff,
            source_provider=provider or "unknown",
            source_record_id=pe_id or watch_id,
            knowledge_time=observed,
            rights_status=rights or "UNKNOWN",
            evidence_class="ARCHIVE_CAPTURE_UPPER_BOUND",
            artist_id=artist,
            venue_id=venue,
            market_id=market.strip().lower() if market else None,
            new_value_json={"event_date": str(event_date) if event_date else None,
                            "artist": artist, "venue": venue, "market": market},
            source_url=url,
            software_version=sw,
        ))

    # 2. OUTCOME_PUBLISHED from PIT result-availability evidence.
    for r in conn.execute(
        "SELECT evidence_id, canonical_event_id, source_publication_time, "
        "       archive_capture_time, source_provider, source_document_id, "
        "       rights_status, source_url, software_version "
        "FROM flywheel.pit_reconstruction_evidence "
        "ORDER BY knowledge_time"
    ).fetchall():
        (ev_id, canonical_id, pub, arch, provider, doc_id, rights, url, sw) = r
        observed = _as_dt(pub) or _as_dt(arch)
        if observed is None:
            continue
        rows.append(build_tape_row(
            activity_type="OUTCOME_PUBLISHED",
            entity_type="EVENT",
            entity_id=canonical_id,
            observed_at=observed,
            effective_at=observed,
            source_provider=provider or "unknown",
            source_record_id=doc_id or ev_id,
            knowledge_time=observed,
            rights_status=rights or "UNKNOWN",
            evidence_class="OBSERVED_PUBLIC" if pub else "ARCHIVE_CAPTURE_UPPER_BOUND",
            event_id=canonical_id,
            new_value_json={"result_available": str(observed)},
            source_url=url,
            software_version=sw,
        ))

    # 3. Decision-cutoff observations (announcement / presale / onsale / booking).
    _cutoff_activity = {
        "ANNOUNCEMENT": "EVENT_ANNOUNCEMENT_OBSERVED",
        "PRESALE": "PRESALE_OBSERVED",
        "GENERAL_ONSALE": "ONSALE_OBSERVED",
        "BOOKING_OR_OFFER": "EVENT_ANNOUNCEMENT_OBSERVED",  # upper bound -> announcement
    }
    for r in conn.execute(
        "SELECT cutoff_id, canonical_event_id, cutoff_type, cutoff_kind, "
        "       cutoff_timestamp, upper_bound, lower_bound, source_provider, "
        "       source_url, rights_status, knowledge_time, software_version "
        "FROM flywheel.pre_event_cutoff_evidence "
        "WHERE cutoff_type IN ('ANNOUNCEMENT','PRESALE','GENERAL_ONSALE','BOOKING_OR_OFFER') "
        "ORDER BY knowledge_time"
    ).fetchall():
        (cid, canonical_id, ctype, ckind, ts, ub, lb, provider, url,
         rights, kt, sw) = r
        activity = _cutoff_activity[ctype]
        observed = _as_dt(kt)
        if observed is None:
            continue
        # BOOKING_OR_OFFER upper bounds are re-expressed as announcement bounds;
        # an exact observed booking date is a distinct, rarer row type.
        if ctype == "BOOKING_OR_OFFER" and ts is not None:
            new_value = {"booking_observed": str(_as_dt(ts))}
        else:
            new_value = {
                "cutoff_type": ctype,
                "cutoff_kind": ckind,
                "upper_bound": str(_as_dt(ub)) if ub else None,
                "exact": str(_as_dt(ts)) if ts else None,
            }
        rows.append(build_tape_row(
            activity_type=activity,
            entity_type="EVENT",
            entity_id=canonical_id,
            observed_at=observed,
            effective_at=_as_dt(ts) or _as_dt(ub),
            source_provider=provider or "unknown",
            source_record_id=cid,
            knowledge_time=observed,
            rights_status=rights or "UNKNOWN",
            evidence_class="ARCHIVE_CAPTURE_UPPER_BOUND" if (ts is None) else "OBSERVED_PUBLIC",
            event_id=canonical_id,
            new_value_json=new_value,
            source_url=url,
            software_version=sw,
        ))

    # 4. Forward observation transitions: status / price changes only. An
    #    observation that merely repeats the prior state never becomes a row.
    prior: dict[str, dict[str, Any]] = {}
    for r in conn.execute(
        "SELECT watch_event_id, milestone, event_status, price_min, price_max, "
        "       currency, observed_at, knowledge_time, source_provider, "
        "       rights_status, source_url, software_version "
        "FROM flywheel.forward_watch_observations "
        "ORDER BY watch_event_id, knowledge_time, observed_at"
    ).fetchall():
        (watch_id, milestone, status, pmin, pmax, cur, observed, kt, provider,
         rights, url, sw) = r
        obs_dt = _as_dt(observed) or _as_dt(kt)
        if obs_dt is None:
            continue
        p = prior.get(watch_id)
        # Price change: a price observation differs from the prior observation.
        if (pmin is not None or pmax is not None) and (
            p is None
            or p.get("price_min") != pmin
            or p.get("price_max") != pmax
        ):
            new_value = {"min": pmin, "max": pmax}
            if cur is not None:
                new_value["currency"] = cur
            rows.append(build_tape_row(
                activity_type="PRICE_CHANGED",
                entity_type="EVENT",
                entity_id=watch_id,
                observed_at=obs_dt,
                effective_at=obs_dt,
                source_provider=provider or "unknown",
                source_record_id=f"{watch_id}:price",
                knowledge_time=_as_dt(kt) or obs_dt,
                rights_status=rights or "UNKNOWN",
                event_id=watch_id,
                old_value_json={"min": p.get("price_min"), "max": p.get("price_max")} if p else None,
                new_value_json=new_value,
                source_url=url,
                software_version=sw,
            ))
        # Status change: a status observation differs from the prior one.
        if status is not None and (p is None or p.get("event_status") != status):
            rows.append(build_tape_row(
                activity_type="EVENT_STATUS_CHANGED",
                entity_type="EVENT",
                entity_id=watch_id,
                observed_at=obs_dt,
                effective_at=obs_dt,
                source_provider=provider or "unknown",
                source_record_id=f"{watch_id}:status",
                knowledge_time=_as_dt(kt) or obs_dt,
                rights_status=rights or "UNKNOWN",
                event_id=watch_id,
                old_value_json={"status": p.get("event_status")} if p else None,
                new_value_json={"status": status, "milestone": milestone},
                source_url=url,
                software_version=sw,
            ))
        prior[watch_id] = {
            "event_status": status,
            "price_min": pmin,
            "price_max": pmax,
        }

    return rows


def insert_tape_entries(conn, rows: list[dict[str, Any]]) -> int:
    """Insert tape rows that are not already present (idempotent, append-only).

    Returns the number of NEW rows written. Existing dedupe_keys are skipped
    (never rewritten).
    """
    written = 0
    for row in rows:
        exists = conn.execute(
            "SELECT 1 FROM terminal.activity_tape WHERE dedupe_key = ?",
            [row["dedupe_key"]],
        ).fetchone()
        if exists:
            continue
        conn.execute(
            """
            INSERT INTO terminal.activity_tape
                (activity_id, observed_at, effective_at, entity_type, entity_id,
                 activity_type, artist_id, event_id, venue_id, market_id,
                 festival_id, source_provider, source_record_id, old_value_json,
                 new_value_json, evidence_class, rights_status, source_url,
                 knowledge_time, dedupe_key, software_version)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                row["activity_id"], row["observed_at"], row["effective_at"],
                row["entity_type"], row["entity_id"], row["activity_type"],
                row["artist_id"], row["event_id"], row["venue_id"], row["market_id"],
                row["festival_id"], row["source_provider"], row["source_record_id"],
                row["old_value_json"], row["new_value_json"], row["evidence_class"],
                row["rights_status"], row["source_url"], row["knowledge_time"],
                row["dedupe_key"], row["software_version"],
            ],
        )
        written += 1
    return written
