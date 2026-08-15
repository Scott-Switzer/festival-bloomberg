"""TRACK A — FORWARD_WATCH activation.

The forward watchlist is where evidence that can never be reconstructed later
is preserved. With keyed providers (Ticketmaster Discovery, SeatGeek) unset,
the deterministic, key-free universe for V1 is:

    MusicBrainz events (CC0) with begin in [today, today + horizon]
    +
    real future events already persisted in the event-history warehouse
    (Ticketmaster events acquired by the earlier recurring collector)

Keyed providers remain registered (KEY_REQUIRED) and are never bypassed; the
US-market×Ticketmaster universe is documented as the next expansion step once
a key is configured.

Every enrolled event keeps provider id, name, venue/place, begin date, first
seen, knowledge_time, source URL and rights. Observations are append-only and
mapped onto the accepted milestone ladder.

ARTIST SEMANTICS (PR #21 closure): ``artist_name`` is REAL performer evidence
ONLY. A MusicBrainz event name or a Ticketmaster/SETLIST event title is never
substituted for a main-performer relation; when no performer relation exists
``artist_name`` stays NULL and the event is reported as partially resolved,
never counted as high-quality.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone, time as dt_time
from typing import Any
from urllib.parse import urlencode

from ..acquisition.contracts import content_hash_of, utc_now
from ..acquisition.transport import HttpResponse, TransportError, UrllibTransport
from .event_graph import MusicBrainzError, MusicBrainzRateLimited
from .forward_watch import register_event_row

FORWARD_OBJECTIVE_VERSION = "data_acquisition_activation_v1"
MUSICBRAINZ_API = "https://musicbrainz.org/ws/2"
MB_LICENSE = "CC0 (MusicBrainz data)"


class MusicBrainzFutureEventsClient:
    """Keyless, rate-limited MusicBrainz future-event discovery (CC0)."""

    name = "musicbrainz_events"

    def __init__(
        self,
        transport: UrllibTransport | None = None,
        *,
        user_agent: str | None = None,
        rate_limit_seconds: float = 1.0,
    ) -> None:
        self.transport = transport or UrllibTransport(user_agent=user_agent)
        self.rate_limit_seconds = rate_limit_seconds
        self._last_request_at = 0.0
        #: REAL request telemetry — never inferred from returned row counts.
        self._telemetry: dict[str, Any] = {
            "request_count": 0,
            "successful_responses": 0,
            "rate_limits": 0,
            "http_failures": 0,
            "records_returned": 0,
            "latency_ms_total": 0,
        }

    def telemetry(self) -> dict[str, Any]:
        """Measured request telemetry accumulated across this client's calls.

        ``request_count`` is the number of real HTTP interactions, never a
        row-count estimate. A client that made no requests reports zeroes;
        callers store request_count = NULL / request_count_status = UNKNOWN
        when no live requests happened.
        """
        return dict(self._telemetry)

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.rate_limit_seconds:
            time.sleep(self.rate_limit_seconds - elapsed)
        self._last_request_at = time.monotonic()

    def search_events(
        self, *, begin_from: str, begin_to: str, offset: int = 0, limit: int = 100
    ) -> dict[str, Any]:
        """Search future events; returns the raw payload dict.

        Telemetry (request count, success/rate-limit/failure counts, latency)
        is measured per real HTTP interaction and accumulated on the client.
        """
        self._throttle()
        query = urlencode(
            {
                "query": f"begin:[{begin_from} TO {begin_to}]",
                "fmt": "json",
                "limit": str(limit),
                "offset": str(offset),
            }
        )
        url = f"{MUSICBRAINZ_API}/event?{query}"
        started = time.monotonic()
        try:
            response = self.transport.request("GET", url, timeout_seconds=30.0)
        except TransportError as exc:
            self._record(1, http_failures=1, started=started)
            raise MusicBrainzError(f"network failure: {exc}") from None
        self._record(1, started=started)
        if response.status in (503, 429):
            self._telemetry["rate_limits"] += 1
            raise MusicBrainzRateLimited(f"MusicBrainz rate limit ({response.status})")
        if response.status != 200:
            self._telemetry["http_failures"] += 1
            raise MusicBrainzError(f"MusicBrainz http {response.status}")
        self._telemetry["successful_responses"] += 1
        payload = _safe_json(response)
        self._telemetry["records_returned"] += len(payload.get("events") or [])
        return payload

    def _record(self, count: int, *, started: float, http_failures: int = 0) -> None:
        self._telemetry["request_count"] += count
        self._telemetry["http_failures"] += http_failures
        self._telemetry["latency_ms_total"] += int((time.monotonic() - started) * 1000)

    def future_events(
        self,
        *,
        horizon_days: int = 365,
        max_events: int = 500,
        as_of: date | None = None,
    ) -> list[dict[str, Any]]:
        """Paginate future events into a flat list of parsed event dicts."""
        today = as_of or date.today()
        begin_from = today.isoformat()
        begin_to = (today + timedelta(days=horizon_days)).isoformat()
        events: list[dict[str, Any]] = []
        offset = 0
        limit = 100
        while len(events) < max_events:
            payload = self.search_events(
                begin_from=begin_from, begin_to=begin_to, offset=offset, limit=limit
            )
            batch = payload.get("events") or []
            if not batch:
                break
            for raw in batch:
                parsed = parse_mb_event(raw)
                if parsed:
                    events.append(parsed)
                    if len(events) >= max_events:
                        break
            offset += limit
            if len(batch) < limit:
                break
        return events


def parse_mb_event(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize a MusicBrainz event search result (pure).

    Requires a real ``life-span.begin`` date; events without one are dropped
    (a forward watch cannot track a date-less event).
    """
    life_span = raw.get("life-span") or {}
    begin = life_span.get("begin")
    if not begin:
        return None
    try:
        begin_date = date.fromisoformat(str(begin)[:10])
    except ValueError:
        return None
    relations = raw.get("relations") or []
    performer = None
    place = None
    for relation in relations:
        rtype = relation.get("type")
        if rtype == "main performer" and not performer:
            artist = relation.get("artist") or {}
            performer = artist.get("name")
        elif rtype == "held at" and not place:
            place_node = relation.get("place") or {}
            place = place_node.get("name")
    return {
        "provider": "musicbrainz",
        "provider_event_id": raw.get("id"),
        "name": raw.get("name"),
        "event_type": raw.get("type"),
        "begin_date": begin_date,
        "main_performer": performer,
        "place": place,
        "license": MB_LICENSE,
        "source_url": f"{MUSICBRAINZ_API}/event/{raw.get('id')}",
    }


def build_forward_event_row(
    *,
    provider: str,
    provider_event_id: str,
    artist_name: str | None,
    venue_name: str | None,
    market: str | None,
    event_date: date,
    first_seen_at: datetime | None = None,
    source_url: str | None = None,
    rights_status: str = "TERMS_REVIEW_REQUIRED",
    commercial_use_status: str = "TERMS_REVIEW_REQUIRED",
    observation_class: str = "OBSERVED_PUBLIC",
) -> dict[str, Any]:
    """Build a ``flywheel.forward_watch_events`` row for a discovered event."""
    return register_event_row(
        provider=provider,
        provider_event_id=provider_event_id,
        artist_name=artist_name,
        venue_name=venue_name,
        market=market,
        event_date=event_date,
        first_seen_at=first_seen_at,
        source_url=source_url,
        rights_status=rights_status,
        commercial_use_status=commercial_use_status,
        observation_class=observation_class,
        software_version=FORWARD_OBJECTIVE_VERSION,
    )


# ---------------------------------------------------------------------------
# Milestone mapping for observations
# ---------------------------------------------------------------------------
def milestone_for_observation(
    *,
    event_date: date | None,
    observed_at: datetime,
    onsale_date: date | None = None,
) -> str:
    """Map an observation time to the accepted ladder milestone (pure).

    D+N is only used when a known onsale date anchors it; without one, the
    observation is never labeled D+N (UNKNOWN_ONSALE != EVENT_DATE). Falls
    back to the event-relative ladder.
    """
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    if onsale_date:
        onsale = datetime.combine(onsale_date, dt_time.min, tzinfo=timezone.utc)
        if onsale <= observed_at < onsale + timedelta(days=14):
            days = (observed_at - onsale).days
            if days <= 0:
                return "ONSALE"
            return f"D+{min(days, 14)}"
    if event_date:
        event_start = datetime.combine(event_date, dt_time.min, tzinfo=timezone.utc)
        if observed_at >= event_start + timedelta(days=30):
            return "SETTLEMENT"
        if observed_at >= event_start:
            return "SHOW"
        days_to = (event_start - observed_at).days
        # The next scheduled event-relative capture whose countdown has been
        # reached (e.g. an observation 16 days out lands in the T-14 window).
        for offset in (30, 14, 7, 3, 1):
            if days_to >= offset:
                return f"T-{offset}"
        return "WEEKLY"
    return "WEEKLY"


def build_observation_row(
    *,
    watch_event_id: str,
    observed_at: datetime,
    event_date: date | None,
    onsale_date: date | None = None,
    provider: str = "event_history",
    event_status: str | None = None,
    price_min: float | None = None,
    price_max: float | None = None,
    currency: str | None = None,
    source_url: str | None = None,
    raw_payload_hash: str | None = None,
    rights_status: str = "TERMS_REVIEW_REQUIRED",
    commercial_use_status: str = "TERMS_REVIEW_REQUIRED",
) -> dict[str, Any]:
    """Build a ``flywheel.forward_watch_observations`` row (pure)."""
    from .forward_watch import build_observation_row as build_fw_observation

    milestone = milestone_for_observation(
        event_date=event_date, observed_at=observed_at, onsale_date=onsale_date
    )
    return build_fw_observation(
        watch_event_id=watch_event_id,
        observed_at=observed_at,
        milestone=milestone,
        event_status=event_status,
        price_min=price_min,
        price_max=price_max,
        currency=currency,
        source_provider=provider,
        source_url=source_url,
        rights_status=rights_status,
        commercial_use_status=commercial_use_status,
        observation_class="OBSERVED_PUBLIC",
        raw_payload_hash=raw_payload_hash,
        software_version=FORWARD_OBJECTIVE_VERSION,
    )


# ---------------------------------------------------------------------------
# Migration of real persisted history into the flywheel watchlist
# ---------------------------------------------------------------------------
def migrate_history_watch(
    *,
    history_conn,
    flywheel,
    as_of: datetime | None = None,
    max_events: int = 100,
) -> dict[str, Any]:
    """Enroll real future events + their REAL snapshot rows from the
    event-history warehouse into the flywheel forward watchlist.

    Nothing is fabricated: events come from ``events.events`` (future dates
    only) and observations come from ``economics.primary_ticket_snapshots``
    (real retrievals with real timestamps).
    """
    now = as_of or utc_now()
    today = now.date()

    # REAL performer evidence comes from the event graph's artist relations
    # (artist_event_relations -> artist_identities.display_name), never from
    # the Ticketmaster/SETLIST event title. When no performer relation exists
    # artist_name stays NULL — an event name is never substituted for an
    # artist (PR #21 semantic closure, fix 1).
    events = history_conn.execute(
        "SELECT e.event_id, e.event_name, e.venue_name, e.market_id, e.local_date, "
        "e.event_status, a.display_name "
        "FROM events.events e "
        "LEFT JOIN events.artist_event_relations r ON r.event_id = e.event_id "
        "LEFT JOIN events.artist_identities a ON a.canonical_artist_id = r.artist_id "
        "WHERE e.local_date >= ? ORDER BY e.local_date LIMIT ?",
        [today.isoformat(), max_events],
    ).fetchall()

    enrolled = 0
    observations = 0
    events_with_2plus = 0
    watch_ids: dict[str, str] = {}
    for event_id, event_name, venue_name, market, local_date, event_status, performer_name in events:
        row = build_forward_event_row(
            provider="event_history",
            provider_event_id=event_id,
            artist_name=performer_name,
            venue_name=venue_name,
            market=market,
            event_date=date.fromisoformat(str(local_date)),
            first_seen_at=now,
            source_url=None,
            rights_status="TERMS_REVIEW_REQUIRED",
            commercial_use_status="TERMS_REVIEW_REQUIRED",
        )
        if flywheel.register_forward_event(row):
            enrolled += 1
            watch_ids[event_id] = row["watch_event_id"]
        else:
            # Idempotent re-run: reuse the EXISTING watch event id so new
            # observations always attach to the original watch row (never
            # create orphan observations under a freshly hashed id).
            existing = flywheel.find_forward_event(
                provider="event_history", provider_event_id=event_id
            )
            watch_ids[event_id] = (existing or {}).get("watch_event_id") or row["watch_event_id"]
            # PR #21 closure (fix 1): legacy rows could carry the Ticketmaster
            # event TITLE as artist_name; reconcile to the real performer.
            flywheel.reconcile_forward_event_artist(
                provider="event_history",
                provider_event_id=event_id,
                artist_name=performer_name,
            )

    # Migrate real snapshot rows as observations (append-only, PIT-safe).
    for event_id, watch_id in watch_ids.items():
        snaps = history_conn.execute(
            "SELECT snapshot_id, retrieved_at, provider, provider_event_id, "
            "minimum_price, maximum_price, currency, event_status, public_onsale_start, "
            "source_url, raw_payload_hash "
            "FROM economics.primary_ticket_snapshots "
            "WHERE canonical_event_id = ? ORDER BY retrieved_at",
            [event_id],
        ).fetchall()
        if len(snaps) >= 2:
            events_with_2plus += 1
        for (
            snapshot_id, retrieved_at, provider, provider_event_id, min_price,
            max_price, currency, event_status, onsale_str, source_url, raw_hash,
        ) in snaps:
            try:
                observed_at = (
                    retrieved_at
                    if isinstance(retrieved_at, datetime)
                    else datetime.fromisoformat(str(retrieved_at))
                )
            except ValueError:
                continue
            event_date = None
            for row in events:
                if row[0] == event_id:
                    event_date = date.fromisoformat(str(row[4]))
                    break
            onsale_date = None
            if onsale_str:
                try:
                    onsale_date = date.fromisoformat(str(onsale_str)[:10])
                except ValueError:
                    onsale_date = None
            obs = build_observation_row(
                watch_event_id=watch_id,
                observed_at=observed_at,
                event_date=event_date,
                onsale_date=onsale_date,
                provider=provider or "event_history",
                event_status=event_status,
                price_min=min_price,
                price_max=max_price,
                currency=currency,
                source_url=source_url,
                raw_payload_hash=raw_hash,
            )
            if flywheel.insert_forward_observation(obs):
                observations += 1

    return {
        "events_enrolled": enrolled,
        "observations_inserted": observations,
        "events_with_2plus_observations": events_with_2plus,
        "as_of": now.isoformat(),
    }


def _safe_json(response: HttpResponse) -> dict[str, Any]:
    try:
        payload = response.json()
    except (ValueError, TypeError) as exc:
        raise MusicBrainzError("response not JSON") from exc
    return payload if isinstance(payload, dict) else {}
