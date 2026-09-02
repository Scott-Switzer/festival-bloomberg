# Commodity music data — buy vs build (v1)

Date: 2026-09-02 · Status: DECISION INPUT (no purchase made)

## Principle

Festival Bloomberg's defensible edge is **auditable pre-offer underwriting,
buyer-owned private outcomes, point-in-time reconstruction, deterministic show
economics, and portfolio risk** — not "more public artist metrics." Public
artist/audience/streaming/social data is a commodity that Soundcharts (~20M
artists, 61 metric types) and Chartmetric (~9M artists, 25+ platforms) already
sell cheaply. Engineering time should go to outcomes, ticket trajectories,
PIT, economics, portfolio risk, and decision memory.

## Candidates

| Provider | What it offers | Indicative cost | Notes |
|---|---|---|---|
| Soundcharts API | streaming, charts, airplay, social, city demand | ~$250/mo (500K req) – $500/mo (4M req) | premium endpoints extra |
| Chartmetric | 25+ platforms, demographics, geography | subscription | closed API, heavier terms |
| Pollstar Data Cloud | box office/routing 1999–, 319K artists | enterprise | overlaps our own R2 event estate |
| TM1 / Eventbrite / DICE / Opendate | *customer-authorized first-party* ticket data | N/A (partner) | the real moat — not commodity |

## Category verdicts

| Category | Verdict | Rationale |
|---|---|---|
| Streaming counts/velocity | DEFER | commodity; buy later when a customer asks; not differentiator |
| Social/radio/charts | DEFER | commodity; Soundcharts API is the natural purchase when revenue exists |
| City-level audience | DEFER | buy later; Callboard/Prism both weaker here than they claim |
| YouTube/Wikimedia attention | BUILD (keep) | our own rails already exist; estate publication is the gap |
| Event/festival/lineup evidence | BUILD (keep) | already materialized from public corpora; differentiate on freshness |
| Ticket counts / sales curves | BUILD (partner) | customer-authorized first-party ingestion = the moat |
| Box office history | BUILD (partner) | same — never buy pooled history we cannot audit |

## Rule going forward

Once modest revenue exists, purchase licensed commodity layers rather than
reproducing Soundcharts/Chartmetric. Until then: no new public scrapers; keep
engineering on private outcomes, sales pace, PIT, and portfolio analytics.
