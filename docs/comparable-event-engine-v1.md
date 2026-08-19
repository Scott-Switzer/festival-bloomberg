# Comparable Event Engine V1 — first increment

Branch: `feat/comparable-event-engine-v1`
Base: `main @ 1188828` (post-merge of PR #32, CI green)

Goal: turn the `COMPS_SIGNAL_ONLY` finding into a **product object** — a
transparent, decomposable comparable-event distance — and measure whether it
beats the existing hierarchical champion. It does not (yet). That is the honest
result, and it re-confirms the baseline's core conclusion.

## What was built

`python/festival_bloomberg/research/comparable.py` — pure computation, no I/O,
no model fitting:

- `comparable_distance(target, candidate)` — a decomposable weighted distance
  over six components: `artist`, `venue`, `market`, `calendar` (circular
  month), `price` (log price band), `shows`. Missing components use a neutral
  0.5 prior and are reported as `missing` (never imputed zero).
- `retrieve_comparables(...)` — top-K retrieval with per-comp distance
  decomposition and a distance-weighted valuation (weighted median, p10/p90,
  p25/p75, effective sample size).
- `point_in_time_candidates(...)` — PIT admissibility: a candidate may only
  contribute if its result was published strictly before the target's event
  start; self and unknown-publication rows are excluded.

`scripts/comparable_backtest.py` — loads the frozen corpus and runs the engine
against the hierarchical fallback under every leakage-safe hold.

`tests/python/test_comparable_engine.py` — 7 offline tests locking the distance
decomposition, calendar circularity, price scaling, PIT exclusion, and
weighted-quantile semantics.

## Backtest result (MAE, lower is better)

| Target | Hold | n | hierarchical | comparable | winner |
|---|---|---|---:|---:|---:|---|
| REPORTED_ATTENDANCE | TIME | 49 | **16,920** | 20,427 | HIER |
| REPORTED_ATTENDANCE | ARTIST | 164 | **6,810** | 7,289 | HIER |
| REPORTED_ATTENDANCE | VENUE | 196 | **8,412** | 9,511 | HIER |
| REPORTED_ATTENDANCE | MARKET | 162 | **10,585** | 10,880 | HIER |
| REPORTED_ATTENDANCE | TOUR | 116 | **4,531** | 4,586 | HIER |
| TICKET_GROSS | TIME | 49 | 2,726,324 | **2,549,733** | COMP |
| TICKET_GROSS | ARTIST | 208 | 917,426 | **907,211** | COMP |
| TICKET_GROSS | VENUE | 236 | **1,132,053** | 1,168,477 | HIER |
| TICKET_GROSS | MARKET | 190 | **1,025,139** | 1,043,405 | HIER |
| TICKET_GROSS | TOUR | 170 | 964,315 | **959,165** | COMP |
| PAID_TICKETS | ARTIST | 44 | 5,767 | **5,179** | COMP |
| PAID_TICKETS | VENUE | 40 | **8,845** | 9,537 | HIER |
| PAID_TICKETS | MARKET | 28 | **4,640** | 4,920 | HIER |
| PAID_TICKETS | TOUR | 54 | 7,936 | **7,722** | COMP |

## Honest verdict

**The comparable engine does not dethrone the hierarchical champion.** On the
only honest chronological hold (TIME), it is *worse* for attendance (+3,507
MAE) and *better* for gross (−176,590). Across all holds it loses attendance
5/5, wins gross 3/5, and splits paid-tickets 2/4.

Why: the champion's hard identity ladder (artist×venue → artist×market →
artist → venue → market) concentrates on the two signals that actually carry
structure in this thin corpus — venue and market medians. The engine's soft
blend gives ~35% of its weight to calendar/price/shows, which dilute that
signal for attendance (the price component is the one that helps gross, hence
the gross win).

This is a **confirmation of `COMPS_SIGNAL_ONLY`**, not a failure. The distance
decomposition is the right *architecture* — it is explainable, PIT-safe, and
ready to absorb richer features — but it cannot beat the champion with only
name/date/price columns.

## The next lever is data, not algorithm

To make the engine beat the champion, the candidate feature panel needs
dimensions the 657-row corpus does not have:

- **venue capacity band** (the champion proxies venue scale only via prior
  outcomes, which is circular for cold venues)
- **artist attention at cutoff** (Wikimedia / ListenBrainz / YouTube history)
- **market economics** (population, income, college density — correctly
  vintage-stamped)
- **geography** (H3 cells, distance between venue and candidate venue)
- **competition** (nearby same-day / same-week events)
- **tour position / lead time**

Each of these maps to a `DENSE_PRE_EVENT_DATA_PANEL_V1` acquisition target
(Overture Maps, Census ACS, FRED/ALFRED, NOAA/NCEI, plus the existing
Wikimedia/ListenBrainz/YouTube under current rights policies).

## Decision

- **Do not replace the hierarchical champion** with the comparable engine in
  this state.
- Keep the engine: it is the correct evaluation object once the feature panel
  gains the dimensions above. The bar remains "consistently beat the champion
  under TIME + grouped holds with uncertainty."
- The next milestone is `DENSE_PRE_EVENT_DATA_PANEL_V1`, feeding this engine.
