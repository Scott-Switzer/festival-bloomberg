# Market Competitive Calendar V1

## User problem

A talent buyer underwriting a proposed show — ARTIST × MARKET × DATE × VENUE —
needs to know what else is happening around that date that could compete for
audience, discretionary entertainment spend, transportation/parking capacity,
venue demand or market attention — and which of those events were actually
**knowable** when the buyer was deciding.

This milestone is a product move: it turns the existing (heavily music-only)
forward event rail into a **full public live-event calendar** and exposes an
explainable competitive calendar in the talent-buyer workspace. It deliberately
does **not** produce a competition score, does not claim every nearby event is
equally competitive, and keeps PIT tri-state knowability non-negotiable.

## Architecture / reuse map

Nothing was rebuilt. The milestone generalizes and reuses:

| Capability | Existing surface | Change |
|---|---|---|
| Ticketmaster Discovery API provider | `acquisition/providers/ticketmaster.py` | now preserves raw classification **IDs** (`segment_id`/`genre_id`/`subgenre_id`) + `family` flag on every event |
| Recursive date-window sweep + partition manifest | `oa/data_fabric.py::_sweep_window` / `_persist_partition` / `terminal.acquisition_partitions` | `classification_name` + `software_version` are now parameters; one sweep runs per segment |
| Event-level dedup + PIT tri-state windows | `planning/competition.py` (`known_before_cutoff` / `observed_post_cutoff` / `unknown_knowledge_time`, ±0/3/7/14) | reused as the classification core |
| Append-only event snapshots | `events.provider_event_snapshots` (migration 024) | migration 036 adds the classification-ID columns |
| Workbench (BUILD) workspace | `terminal/server.py` + `apps/terminal/static/app.js` | new `competitive-calendar` route + panel |

## Source contract

The provider's raw classification structure is preserved per snapshot:

- segment ID + name (e.g. `KZFzniwnSyZfZ7v7nJ` / `Music`)
- genre ID + name, subgenre ID + name (when supplied; the provider rarely
  returns subgenre — raw NULL is preserved, never invented)
- `family` flag when supplied
- venue coords, city/state/country, local date/time/timezone, onsale dates,
  promoter, price ranges, status — all already captured by the provider

Event date is `EVENT_TIME`, never `knowledge_time`; `retrieved_at` is the
system retrieval time and is **never** historical availability.

## Acquisition completeness

Every partition terminates in an explicit state persisted to
`terminal.acquisition_partitions`: `COMPLETE`, `SPLIT` (reported total exceeds
the provider's 1000-item deep-paging ceiling → date-window bisection down to
7-day minimum), `TRUNCATED_BY_CAP`, `RATE_LIMITED`, `ERROR`. No silent
truncation.

## Geographic competition context

For a target with coordinates, every competitor's exact haversine distance is
computed and bucketed: same venue, same city, ≤5 / ≤10 / ≤25 / ≤50 miles,
beyond 50. Missing coordinates ⇒ `UNKNOWN`, never assumed. Same-city equality
is never treated as a distance.

## Point-in-time semantics

For a research cutoff, each competitor is classified by its **earliest
`knowledge_time`** across snapshots into exactly one bucket:

- `known_before_cutoff` — earliest < cutoff (visible at the decision time)
- `observed_post_cutoff` — earliest ≥ cutoff (NOT visible at cutoff, but NOT
  missing data)
- `unknown_knowledge_time` — no valid knowledge time

Without a `research_cutoff` the result is a NON-PIT current-warehouse view and
is labeled `NON_PIT`, never historical evidence.

## Real pilot (bounded)

Six geographically diverse markets (Los Angeles, New York, Chicago, Las Vegas,
Nashville, Dallas) × all six Ticketmaster segments × a 30-day forward window ×
**two passes** (the second pass makes PIT knowability demonstrable: events first
observed in pass 2 are `observed_post_cutoff` for a pass-1 decision cutoff).

| Sweep metric | Value |
|---|---|
| Partitions | 108 (90 COMPLETE, 18 SPLIT, 0 truncated, 0 failed, 0 rate-limited) |
| API requests | 872 |
| Snapshots persisted | 18,818 |
| Distinct events (pilot markets) | ~27,000 across segments |

Events by segment (distinct): **Arts & Theatre 14,070 · Music 8,109 ·
Miscellaneous 4,520 · Sports 435 · Film 119 · Undefined 85**.

Coverage: coordinates 99.96%, knowledge-time 100%, 61 distinct genres,
subgenres 0 (provider rarely supplies them — raw NULL preserved).

## Information-lift measurement (the value test)

For 300 target MUSIC events (frozen sample, earliest dates in window), using the
**full** calendar vs music-only:

| Metric | Result |
|---|---|
| % with ≥1 same-day MUSIC event (music-only baseline) | 99.3% |
| % with ≥1 same-day NON-MUSIC event (**new information**) | **18.3%** |
| % whose ±3-day competitive context changes when non-music is added | **55.0%** |
| % with ≥1 competitor within 5 / 10 / 25 / 50 miles | 99.3% / 99.3% / 99.3% / 99.3% |
| % with defensible PIT classification | 100% |

PIT tri-state vs the pass-1 decision cutoff (2026-08-25 00:45 UTC): all 300
targets have competitors `known_before_cutoff` (the pre-existing estate) **and**
competitors `observed_after_cutoff` (this pilot's acquisitions) — the two
buckets never mix. Example: a Las Vegas music target on 2026-08-15 sees 218
competitors known at cutoff (including same-day Arts & Theatre + Music) and 613
events that existed but were **not** knowable at the decision time.

**Verdict on the value test: PASS.** The full public calendar materially
improves the decision context vs music-only: ~1 in 5 music shows has a
same-day non-music competitor, and over half see their surrounding context
change within ±3 days once non-music events are included. Non-music data is
information, not noise.

## Buyer workspace

The BUILD view of the talent-buyer workbench now has a **Competitive calendar**
panel: pick a date (defaults to the project start), and see

- same-day / ±3 / ±7 / ±14 counts split by segment, with the PIT mode labeled;
- distance summary (same venue, ≤5/10/25/50 miles);
- the actual nearest/relevant event rows: DATE | EVENT | TYPE | VENUE |
  DISTANCE | WINDOW | KNOWLEDGE STATUS;
- **Observed after cutoff** and **Unknown knowledge time** shown separately,
  never mixed into the known-at-cutoff count.

A buyer can compare two proposed dates (e.g. Chicago Oct 10 vs Oct 17) and see
immediately that their surrounding calendars differ. No score, no
recommendation — defensible context.

## Do-not-cross line

No demand prediction, no attendance forecast, no ML, no opaque competition
score, no invented weights (`sports=0.4` etc.). "You should book this show" is
not produced anywhere.

## Limitations

- The pilot window is 30 days forward; a 90-day sweep splits heavily (Music
  windows exceed the deep-paging ceiling) and needs more wall-clock/requests.
- Subgenre is almost never supplied by the provider (NULL preserved).
- The pilot's target sample is the earliest 300 music events in the window;
  density metrics (e.g. within-5-mile) reflect dense-metro venue clustering.
- `retrieved_at`-based knowledge time means "known at cutoff" is bounded by
  when we started observing the calendar; earlier knowability (announcement vs
  onsale) is not yet separated.

## Next milestone recommendation

See the PR handoff. The measured lift and the fact that acquisition ran clean
suggest `MARKET_COMPETITIVE_CALENDAR_V1` is a solid base; the next highest-value
move is the one the handoff justifies from these numbers.
