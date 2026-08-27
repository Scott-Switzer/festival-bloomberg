"""ListenBrainz BULK collection for the ARTIST_SECURITY_1000 universe.

Milestone OPEN_ARTIST_MARKET_DATA_V1 — SOURCE 1.

Two acquisition paths are combined (both key-free, CC0):

* ``collect_artist_popularity``  — the official bulk endpoint: one POST of up
  to 1000 MBIDs per request returns cumulative totals for the whole universe.
  This is the primary path for the ~1,000-artist universe (1-2 requests).
* ``collect_priority_range_history`` — per-artist range statistics
  (week / month / all_time) for the DEMAND/MOMENTUM windows
  (LB_LISTENS_7D / LB_LISTENS_28D / LB_LISTEN_VELOCITY). Bounded and
  rate-limit-aware; used for the range-based factors.

Both persist into ``metrics.artist_attention_observations`` with the existing
observation keys (idempotent re-runs) and the ATTENTION_CONSUMPTION_SAMPLE
semantics — never local ticket demand. Missing MBIDs stay missing (never zero).

The full raw exports (listenbrainz-spark-dumps) are a future bulk path; this
module documents that boundary and keeps the API paths honest and bounded.
"""

from __future__ import annotations

from typing import Any

from .listenbrainz import (
    collect_artist_popularity,
    collect_priority_range_history,
)

LB_BULK_BATCH_SIZE = 1000


def collect_security_universe_listenbrainz(
    conn,
    transport,
    *,
    universe: list[dict[str, Any]],
    ranges: tuple[str, ...] = ("week", "month", "all_time"),
    artist_keys: dict[str, str] | None = None,
    min_interval_seconds: float = 0.4,
    include_range_history: bool = True,
) -> dict[str, Any]:
    """Bulk + range ListenBrainz collection for the security universe.

    ``universe`` rows come from ``security.artist_security_master`` selection
    (select_security_universe): each has ``artist_key``, ``mbid``,
    ``artist_name``.

    Returns a combined summary. Rate limiting on the per-artist range path
    stops only that path (RATE_LIMITED_STOPPED); the bulk totals path already
    completed.
    """
    pairs: list[tuple[str, str]] = []
    key_by_mbid: dict[str, str] = {}
    for artist in universe:
        mbid = artist.get("mbid")
        name = artist.get("artist_name") or artist.get("artist_key") or ""
        if not mbid:
            continue
        pairs.append((name, mbid))
        key_by_mbid[mbid] = artist["artist_key"]
    if artist_keys:
        key_by_mbid.update(artist_keys)

    bulk_summary = collect_artist_popularity(
        conn, transport, artists=pairs, artist_keys=key_by_mbid,
    )
    range_summary: dict[str, Any] = {"status": "SKIPPED", "detail": "range history disabled"}
    if include_range_history:
        range_summary = collect_priority_range_history(
            conn, transport, artists=pairs, ranges=ranges,
            min_interval_seconds=min_interval_seconds,
            artist_keys=key_by_mbid,
        )

    return {
        "status": "COMPLETE",
        "artists_eligible": len(pairs),
        "bulk_popularity": bulk_summary,
        "range_history": range_summary,
        "rows_persisted_total": (
            bulk_summary.get("rows_persisted", 0)
            + range_summary.get("rows_persisted", 0)
        ),
    }
