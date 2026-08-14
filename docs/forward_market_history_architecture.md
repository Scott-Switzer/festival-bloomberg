# Forward Market History V1 Architecture

## Overview

Forward Market History V1 is a recurring read-only economics collector that accumulates real market evidence over time. The system is optimized for finance-grade underwriting evidence and the durable moat of the Point-In-Time (PIT) dataset.

## 6-Hour Cadence Rationale

The initial operating cadence is set to 6 hours for the following reasons:

1. **Price Volatility**: Ticket market prices typically show meaningful changes on 4-12 hour timescales. A 6-hour cadence captures most price movements without excessive noise.

2. **Provider Rate Limits**: Ticketmaster API rate limits are designed for reasonable polling intervals. 6 hours (4 runs/day) is well within acceptable limits for read-only access.

3. **Storage Efficiency**: Each run creates new snapshot records. 6-hour cadence balances temporal resolution with storage growth (4 snapshots/day per event vs 24 for hourly).

4. **Operational Overhead**: Less frequent runs reduce operational overhead and allow for manual intervention if needed.

5. **Event Lifecycle**: For events with weeks/months until showtime, 6-hour resolution provides sufficient temporal density for trend analysis.

## Configuration

The cadence is stored as configuration, not schema truth. It can be overridden without migration changes:

- **Environment Variable**: `FESTIVAL_BLOOMBERG_ECON_CADENCE` (default: `6h`)
- **Supported Values**: `24h`, `12h`, `6h`, `3h`, `1h`
- **LaunchAgent**: `StartInterval` in plist (21600 seconds for 6h)

## System Components

### 1. Tracked Event Registry (`economics/tracking.py`)

Manages the lifecycle of events being tracked for recurring collection:

- **Statuses**: `ACTIVE`, `COMPLETED`, `EXPIRED`, `CANCELED`, `PAUSED`
- **Post-Event Window**: 48 hours after event time for final observations
- **Lifecycle**: Events are tracked → ACTIVE → snapshots collected → post-event window → COMPLETED/EXPIRED

### 2. Collector Lock (`economics/collector.py`)

Non-blocking file lock to prevent concurrent collector runs:

- **Non-Blocking**: Fails fast if lock is already held (`LockHeldError`)
- **Lock Path**: Configurable via `FESTIVAL_BLOOMBERG_ECON_LOCK` (default: `data/warehouse/economics.lock`)
- **Restart-Safe**: Lock is released on process exit or exception

### 3. Run Logger (`economics/runlog.py`)

Structured logging with secret redaction:

- **Safe Metadata**: run_id, timestamps, event counts, provider status
- **Redacted**: API keys, auth headers, secret URLs, full .env contents
- **Log Location**: `~/.local/state/festival-bloomberg/economics_collector.log`
- **Log Rotation**: 5MB max size, archived to `.log.1`
- **DB Persistence**: Runs are also stored in `economics.collector_runs` table

### 4. Venue Identity Resolution (`economics/venues.py`)

Deterministic venue resolution hierarchy:

1. **EXACT_EXTERNAL_ID**: Exact match on ticketmaster_venue_id, setlistfm_venue_id, wikidata_qid
2. **EXACT_CANONICAL_MAPPING**: Known canonical mappings for major venues
3. **EXACT_NAME_CITY**: Exact normalized name + city/state/country match
4. **COORDINATE_MATCH**: Coordinate proximity + name compatibility (~150m tolerance)
5. **ALIAS_MATCH**: Sponsor prefix stripping (e.g., "Huntington Bank" → "United Center")
6. **FUZZY_REVIEW_REQUIRED**: Manual review required
7. **UNRESOLVED**: No match found

**No fuzzy auto-merging** - all fuzzy candidates require manual review.

### 5. Capacity Enrichment (`economics/enrichment.py`)

Multi-source capacity claim extraction:

- **Priority**: Wikipedia > Wikidata > OSM
- **Source Rights**: All sources are rights-decided before use
  - Wikipedia: CC BY-SA 3.0 (OPEN_WITH_ATTRIBUTION)
  - Wikidata: CC0 (OPEN_COMMERCIAL_OK)
  - OSM: ODbL (OPEN_WITH_ATTRIBUTION)
- **No AveragingConflicting claims are preserved, not averaged
- **Capacity Kinds**: MAX_PERSONS, CONCERT, SEATED, STANDING, SPORTS, UNKNOWN

### 6. Snapshot Delta Semantics (`economics/snapshots.py`)

PIT-safe snapshot comparison with UNKNOWN handling:

- **NO_OBSERVED_PRICE_CHANGE_INFORMATION**: UNKNOWN → UNKNOWN
- **PRICE_BECAME_OBSERVABLE**: UNKNOWN → known value
- **PRICE_BECAME_UNOBSERVABLE**: known value → UNKNOWN
- **OBSERVED_DELTA**: known → known with numeric delta

**UNKNOWN is never coerced to zero** - it represents missing information.

### 7. LaunchAgent Integration

macOS user-level scheduler for recurring execution:

- **Label**: `com.festival-bloomberg.economics-snapshot`
- **Interval**: 21600 seconds (6 hours)
- **Wrapper**: `scripts/economics_snapshot_wrapper.sh`
- **Install**: `scripts/install_economics_launchagent.sh`
- **No Secrets**: Plist contains no secret values, only environment variable references

## CLI Commands

### Event Tracking
- `festival economics tracked-events` - List tracked events
- `festival economics track-event --event-id ...` - Add event to tracking
- `festival economics untrack-event --event-id ...` - Remove event from tracking
- `festival economics snapshot-tracked` - Snapshot all tracked events (scheduled command)

### Venue Management
- `festival economics venue-audit` - Audit venue master for duplicates
- `festival economics merge-united-center` - Merge United Center duplicates

### Discovery
- `festival economics discover-upcoming` - Discover upcoming events for tracked artists (placeholder)

## Operational Acceptance

Run with:
```bash
festival operational-acceptance-forward-history \
    --db data/warehouse/artist_market_event_history.duckdb \
    --manifest reports/forward_market_history_v1.json
```

### Gates

- **FORWARD_COLLECTOR**: Collector runs successfully with lock
- **LAUNCHAGENT**: LaunchAgent installed and loads correctly
- **TWO_SNAPSHOT_PIT**: Two-snapshot cycle demonstrates PIT replay
- **VENUE_MASTER**: Venue audit passes, United Center merged
- **VENUE_GRAPH_PARITY**: 48 vs 28 discrepancy explained (per-artist counting)
- **CAPACITY_ENRICHMENT**: ≥15 venues with capacity claims
- **FORWARD_MARKET_HISTORY_V1**: All gates pass

## Fail-Closed Principles

1. **UNKNOWN Source Rights**: Fails closed, no capacity enrichment
2. **NOT_CONFIGURED Providers**: Logged but don't block other providers
3. **Lock Contention**: Fail fast, don't queue
4. **Missing .env**: Fail with clear error
5. **Auth Failure**: Fail with structured error, preserve DB

## Cost

- **Monetary**: $0.00 (no paid providers)
- **Storage**: ~4 snapshots/day per event (6-hour cadence)
- **API Calls**: ~4 calls/day per event (Ticketmaster only if configured)

## Future Enhancements

- **Cadence Adjustment**: Support per-event cadence based on proximity to event time
- **Upcoming Discovery**: Automated discovery of new events for tracked artists
- **Event Completion Labeling**: Setlist.fm integration for post-event status
- **Price-History DQ**: Per-event coverage ratio reporting
- **Temporal Feature Table**: PIT-safe feature extraction for modeling
