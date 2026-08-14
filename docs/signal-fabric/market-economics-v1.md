# Market Economics Evidence V1

Stacked on PR #11 (which is stacked on PR #10). Do not merge out of order.

This layer stores **source-backed claims**, not booking economics:

- Venue capacity is a claim (configuration-specific, possibly conflicting).
- Ticketmaster price ranges and statuses are **current** primary-market snapshots.
- SeatGeek official API v2 stats are **event-level** secondary-market snapshots.
- Historical prices, sold-out, and attendance default to UNKNOWN.

Do not interpret OFFSALE or zero resale listings as sold out. Do not average
conflicting capacities. Do not consume legacy `arbitrage_candidate`.

## PIT

Snapshot and claim `knowledge_time` is retrieval time. A claim fetched in 2026
is invisible at a 2020 cutoff. Event date is not knowledge time.

## Collector

```text
PYTHONPATH=python python3.12 -m festival_bloomberg.cli economics snapshot-upcoming \
  --market "Chicago, IL"
```

`scripts/economics_snapshot.sh` is LaunchAgent-suitable: append-only, lock-safe,
$0 paid providers. Cadence is not stored in the schema.

Authenticated SeatGeek tests require `SEATGEEK_CLIENT_ID` locally. CI stays
fixture-only.
