"""Offline Dagster parity experiment for Ticketmaster forward acquisition.

Proves (or disproves) that wrapping the existing acquisition workflow in a
Dagster asset produces IDENTICAL semantics to the legacy runner — with NO
network and NO live warehouse.

Method:
  1. Freeze a realistic Ticketmaster Discovery API search response.
  2. Create two fresh temporary DuckDB warehouses from identical migrations.
  3. Legacy: call `oa.live_data_activation._run_ticketmaster` directly with a
     scripted FakeTransport against DB A.
  4. Dagster: materialize a single asset that calls the SAME
     `_run_ticketmaster` with an identical scripted FakeTransport against DB B.
  5. Compare semantic content (event fields, acquisition-run fields) with
     deterministic digests, plus idempotency on a second run.

Timestamps (`retrieved_at`, `knowledge_time`, `run_id`, `snapshot_key`) are
wall-clock by design (retrieval time IS when you fetched it) and are excluded
from the comparison; only semantic content must be identical.

Run:  PYTHONPATH=python .venv/bin/python scripts/oss_dagster_parity.py
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

os.environ.setdefault("FESTIVAL_BLOOMBERG_SKIP_ENV_FILE", "1")
os.environ["TICKETMASTER_API_KEY"] = "parity-test-key"

import duckdb  # noqa: E402

from festival_bloomberg.oa.live_data_activation import _run_ticketmaster  # noqa: E402
from festival_bloomberg.warehouse.repository import FestivalRepository  # noqa: E402
from festival_bloomberg.events.repository import EventRepository  # noqa: E402


MARKETS: tuple[tuple[str, str], ...] = (("Chicago", "IL"), ("Austin", "TX"))


def _event(tm_id: str, name: str, artist: str, attraction_id: str, venue: dict, date: str, status: str, price_min: float, price_max: float, promoter: str) -> dict:
    return {
        "id": tm_id,
        "name": name,
        "url": f"https://www.ticketmaster.com/event/{tm_id}",
        "source": "universe",
        "dates": {
            "start": {"localDate": date, "localTime": "20:00:00", "dateTime": f"{date}T01:00:00Z"},
            "status": {"code": status},
            "timezone": "America/Chicago",
        },
        "sales": {
            "public": {"startDateTime": "2026-05-01T10:00:00Z", "endDateTime": f"{date}T00:00:00Z"},
            "presales": [{"name": "Fan Presale", "startDateTime": "2026-04-28T10:00:00Z", "endDateTime": "2026-04-30T10:00:00Z"}],
        },
        "priceRanges": [{"type": "standard", "currency": "USD", "min": price_min, "max": price_max}],
        "promoter": {"name": promoter},
        "classifications": [{"primary": True, "segment": {"name": "Music"}, "genre": {"name": "Rock"}, "subgenre": {"name": "Pop"}, "type": {"name": "Concert"}}],
        "_embedded": {"venues": [venue], "attractions": [{"id": attraction_id, "name": artist}]},
    }


def build_frozen_responses() -> list[tuple[int, dict]]:
    """Two markets, one page each (totalPages=1), one event per market."""
    chicago = {
        "page": {"totalElements": 1, "totalPages": 1, "size": 50, "number": 0},
        "_embedded": {"events": [_event(
            "tm-chi-1", "Taylor Swift | The Eras Tour", "Taylor Swift", "a-taylor",
            {"id": "v-chi", "name": "United Center", "city": {"name": "Chicago"},
             "state": {"name": "Illinois", "stateCode": "IL"},
             "country": {"name": "United States", "countryCode": "US"},
             "location": {"latitude": "41.8807", "longitude": "-87.6742"}},
            "2026-11-01", "onsale", 49.5, 499.5, "Live Nation",
        )]},
    }
    austin = {
        "page": {"totalElements": 1, "totalPages": 1, "size": 50, "number": 0},
        "_embedded": {"events": [_event(
            "tm-aus-1", "Bad Bunny | World Tour", "Bad Bunny", "a-bad",
            {"id": "v-aus", "name": "Moody Center", "city": {"name": "Austin"},
             "state": {"name": "Texas", "stateCode": "TX"},
             "country": {"name": "United States", "countryCode": "US"},
             "location": {"latitude": "30.2807", "longitude": "-97.7325"}},
            "2026-11-15", "onsale", 65.0, 399.0, "C3 Presents",
        )]},
    }
    return [(200, chicago), (200, austin)]


class FakeTransport:
    """Same contract as tests/python/conftest.py FakeTransport (scripted, offline)."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []

    def request(self, method, url, *, headers=None, params=None, body=None, timeout_seconds=30.0):
        self.requests.append({"method": method, "url": url, "params": params})
        if self._responses:
            status, payload = self._responses.pop(0)
        else:
            status, payload = 500, {"error": "no scripted response"}
        body_bytes = json.dumps(payload).encode("utf-8") if not isinstance(payload, bytes) else payload
        from festival_bloomberg.acquisition.transport import HttpResponse
        return HttpResponse(status, body_bytes, {})


def fresh_warehouse(path: str):
    repo = FestivalRepository(path)
    EventRepository(repo.conn)  # applies pending migrations
    return repo


SEMANTIC_COLS = [
    "platform_object_id", "event_name", "artist_name", "venue_id", "venue_name",
    "city", "state_code", "country_code", "latitude", "longitude", "local_date",
    "local_time", "event_time", "timezone", "event_status", "onsale_start",
    "onsale_end", "presales", "price_min", "price_max", "price_currency",
    "price_type", "promoter", "segment", "genre", "subgenre", "event_type",
    "canonical_url",
]


def digest(conn) -> str:
    """Deterministic digest of semantic acquisition state (timestamps excluded)."""
    snaps = conn.execute(
        f"SELECT {', '.join(SEMANTIC_COLS)} FROM events.provider_event_snapshots "
        "ORDER BY platform_object_id"
    ).fetchall()
    runs = conn.execute(
        "SELECT provider, operation, status, request_count, record_count, "
        "error_count, note FROM audit.provider_acquisition_runs ORDER BY provider, operation"
    ).fetchall()
    material = json.dumps({"snapshots": snaps, "runs": runs}, default=str, sort_keys=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def run_legacy(db_path: str, responses) -> dict:
    repo = fresh_warehouse(db_path)
    try:
        summary = _run_ticketmaster(repo.conn, MARKETS, validate=True, transport=FakeTransport(responses))
        repo.conn.commit()
        return {"summary": summary, "digest": digest(repo.conn),
                "snap_count": repo.conn.execute("SELECT COUNT(*) FROM events.provider_event_snapshots").fetchone()[0]}
    finally:
        repo.close()


def run_dagster(db_path: str, responses) -> dict:
    from dagster import Definitions, asset, materialize

    @asset
    def ticketmaster_acquisition():
        repo = fresh_warehouse(db_path)
        try:
            return _run_ticketmaster(repo.conn, MARKETS, validate=True, transport=FakeTransport(responses))
        finally:
            repo.close()

    result = materialize([ticketmaster_acquisition], resources={})
    assert result.success, f"Dagster materialization failed: {result}"
    repo = fresh_warehouse(db_path)
    try:
        return {"summary": result.output_for_node("ticketmaster_acquisition"),
                "digest": digest(repo.conn),
                "snap_count": repo.conn.execute("SELECT COUNT(*) FROM events.provider_event_snapshots").fetchone()[0]}
    finally:
        repo.close()


def main() -> None:
    print("=== Offline Dagster parity experiment (Ticketmaster forward acquisition) ===\n")

    tmp = Path(tempfile.mkdtemp(prefix="oss_parity_"))
    legacy_db = str(tmp / "legacy.duckdb")
    dagster_db = str(tmp / "dagster.duckdb")

    responses = build_frozen_responses()
    print(f"frozen fixture: {len(responses)} scripted HTTP responses, 2 markets, 1 event each")

    print("\n[1/3] legacy runner ...")
    legacy = run_legacy(legacy_db, responses)
    print(f"      events_persisted={legacy['summary']['events_persisted']} "
          f"requests={legacy['summary']['requests']} snap_count={legacy['snap_count']}")

    print("[2/3] dagster asset ...")
    dag = run_dagster(dagster_db, build_frozen_responses())
    print(f"      events_persisted={dag['summary']['events_persisted']} "
          f"requests={dag['summary']['requests']} snap_count={dag['snap_count']}")

    print("\n[3/3] comparison ...")
    print(f"      legacy  digest = {legacy['digest']}")
    print(f"      dagster digest = {dag['digest']}")
    equivalent = legacy["digest"] == dag["digest"] and legacy["snap_count"] == dag["snap_count"]
    print(f"      SEMANTIC_EQUIVALENCE = {'PASS' if equivalent else 'FAIL'}")

    # Idempotency: a second legacy run with the same fixture adds no NEW event
    # (it adds new SNAPSHOTS per retrieval, but no new distinct events).
    print("\n[idempotency] second legacy run ...")
    second = run_legacy(legacy_db, build_frozen_responses())
    distinct = second["snap_count"]  # 2 new snapshots (new retrieval) — but same distinct events
    conn = duckdb.connect(legacy_db, read_only=True)
    distinct_events = conn.execute(
        "SELECT COUNT(DISTINCT platform_object_id) FROM events.provider_event_snapshots"
    ).fetchone()[0]
    conn.close()
    print(f"      snap_count after 2nd run = {distinct}, distinct events = {distinct_events} "
          f"(expect 2 — no duplicate distinct events)")

    print(f"\nDAGSTER_PILOT = {'ADOPTED (semantic parity proven)' if equivalent else 'FAIL (semantic divergence)'}")
    if not equivalent:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
