# Production Red Team + Competitive Product Audit — 2026-09-22

Milestone: `PRODUCTION_RED_TEAM_AND_PRODUCT_GAP_V1`
Live commit audited: `7063f142a9339df251656b1ca71e4788177b2d91` (origin/main, PR #78)
Production: `festival-bloomberg-terminal-production` @ `...scswitzer.workers.dev`
Serving generation at audit: `terminal_v1_20260921T135111Z`
Fix branch: `feat/production-red-team-product-gap-v1`

No secrets in this document. Authenticated URLs redacted throughout.

## 1. Executive findings

* Naked production is fail-closed (401 `TERMINAL_AUTH_REQUIRED` on `/`, `/health`,
  `/api/*`, `/_bootstrap`, `/static`; 404 on denied prefixes). No secret in client
  bundle. **Boundary PASS.**
* CI production UAT on main passes with zero console/page errors.
* Hostile browser QA (71 interactions, local harness on production code + serving
  artifact): found **P1 stale-render race** — rapid artist A→B and search A→B
  displayed the wrong entity's data, and in-flight loads from prior routes threw
  `Cannot set properties of null` on the replaced view. PR #78 had fixed only
  home→search. **Fixed on the fix branch; regression battery now 0 page errors.**
* XSS battery: all abuse strings escaped (`esc()` covers `&<>"'`), no alert fired. **PASS.**
* Data semantics: UNKNOWN≠0 respected, timestamps present
  (observation/knowledge/freshness), attention≠demand notes present, peers present,
  no `pilot sample` language. **PASS with noted gaps** (duplicate Alice Cooper
  identities undisambiguated; `last_play` nulls; weak heuristic comparables).
* Buyer workflow: evidence supports history/markets/peers/attention questions;
  cannot yet answer demand, routing, contacts, or offer certainty. **PARTIAL** —
  reduces evidence-gathering work, does not replace Pollstar/Prism/ROSTR.
* Strategic answer: a buyer who already has Chartmetric/Soundcharts + Pollstar +
  ROSTR + Prism opens Festival Bloomberg for one job — **verifying what was
  knowable when, with evidence and explicit unknowns** (PIT-cutoff buyer briefs).
  That job is real but narrow; the shortest path to a compelling answer is
  last-played dates + ticket-demand evidence + contact graph + MCP/API access.

## 2. Production bugs (found → fixed)

| # | Severity | Bug | Repro | Status |
|---|----------|-----|-------|--------|
| 1 | P1 | Stale artist render: rapid A→B shows A's data on B's route (`renderArtist` had no route token) | goto Nightwish → immediately Imagine Dragons; final view showed Nightwish | FIXED (token capture + post-await guard, all 20 async renders) |
| 2 | P1 | Stale search render: rapid Alice→Metallica ends showing "Alice Cooper 2 hits" (`doSearch` no post-await guard) | 6 rapid search navigations, read header | FIXED |
| 3 | P1 | `TypeError: Cannot set properties of null` ×5 distinct sites (demoGrid, monBox, paceList, lpList, pfExposure) from in-flight loads completing after route change | full 71-interaction battery, route-tagged stacks | FIXED (same guards) |
| 4 | P2 | Broken template download: backtest links `/static/design_partner_show_history_template.csv`, file did not exist (console 404) | visit #/backtest, click template link | FIXED (shipped canonical-header template; preview maps 27/27, 0 PII) |
| 5 | P3 | Missing favicon → console 404 on every load (pollutes console-error monitoring) | any page load | FIXED (`data:` favicon) |
| 6 | P3 | `view.addEventListener("click")` re-wired on every underwrite visit (handler stacking; #view persists across routes) | code inspection | FIXED (once-guard via dataset flag) |
| 7 | P3 | Unknown market key renders synthesized pretty name ("Nope Not A, MARKET", 0 artists) instead of not-found | GET /api/market/nope-not-a-market | OPEN (deferred; honest counts, low harm) |

Fix verification: triage script (`RACE-B-SHOWS: true`, no leak; search header = latest
query) + full 71-interaction battery: **0 page errors**, no alert, only benign
resource-404 console entry (expected 404 JSON for nonexistent-artist probe).

Regression protection: `tests/python/test_terminal_stale_render_guards.py` (4 tests:
token capture on all async renders, doSearch bump ordering, every await followed by
guard, no access-path leakage + escaped search header). `node --check` clean.

## 3. Data-quality findings

* **PASS**: UNKNOWN chips render as UNKNOWN (never 0); tape shows UNKNOWN for null
  values; `statusChip`/`prov()` label OBSERVED/DERIVED/USER ASSUMPTION/UNKNOWN.
* **PASS**: observation_time / knowledge_time / freshness visible on tape, attention
  panels, evidence table; ListenBrainz note explicitly "not local demand".
* **PASS**: Alice Cooper (HOT_1000 `ee58c59f`): ListenBrainz affinity OBSERVED,
  12 peers (AC/DC et al.), 1450 historical events, 57 markets, 11 festivals,
  4 forward events; zero `pilot sample` hits across payload.
* **P2 gap**: two "Alice Cooper" identities coexist (`ee58c59f` HOT_1000/1450 events
  vs `4d7928cd` COVERAGE_25000/8 events, likely person-vs-band MBIDs). Search shows
  two identical names distinguished only by tier badge. Buyer cannot tell which to
  open. Recommend disambiguation line (type/area/event count) in search results.
* **P2 gap**: `last_play`/`last_play_date` null across strongest-markets and markets
  endpoints → "Last played" columns empty; underwrite for Alice Cooper × chicago-il
  returns `observed_shows: null`. Market-timing questions (B/C) weakened.
* **P2 gap**: comparables heuristic weak (e.g. "13th Floor Elevators" for Alice Cooper
  via 1 shared market, `HEURISTIC_ORDERING_V1`). Consistent with
  `BASELINE_RESEARCH_V1 = COMPS_SIGNAL_ONLY`; do not over-invest before data
  expansion.

## 4. UX findings

* Escaping discipline strong across ~200 `innerHTML` sites (audited); `toast` uses
  `textContent`. No unescaped reflection found.
* Loading/empty/UNKNOWN/error states present on all routes; provenance chips visible.
* Widths 390/768/1440 render content (spot-checked body text; full visual polish not
  graded — no cosmetic redesign requested).
* Buyer frictions: duplicate-identity ambiguity (above); unknown-market pretty-name
  fabrication; search form does not update the hash (back button skips searches).
* Verdict: information-dense decision terminal, not a demo — but buyer workflows
  dead-end where evidence is thin (by design: UNKNOWN stays UNKNOWN).

## 5. Buyer workflow findings (C3/Live Nation/AEG/indie lens)

| Workflow | Answered? | Clicks | Notes |
|----------|-----------|--------|-------|
| A booking evidence for/against | Partial | 2–3 | history/festivals/attention/peers present; no demand signal |
| B strongest market | Partial | 2 | strongest_markets listed but last-played null; Chicago brief nulls |
| C venue size/config | Weak | 2–3 | capacity claims separate (good); no sell-through evidence |
| D comparables | Weak | 1 | heuristic, 1-shared-market links; not actionable |
| E recent/festival history | Yes | 1–2 | 1450 events, 11 festivals, forward 4; solid |
| F known vs UNKNOWN | Yes | 1 | best-in-class explicitness |
| G still needed pre-offer | Yes | 1 | brief lists unknowns; buyer still needs Pollstar/Prism/ROSTR |

Reduces evidence-gathering; does not reduce underwriting judgment. External tools
still needed: real ticket sales/gross (Pollstar/Prism Insights), audience
geo/demo + playlists + radio (Chartmetric/Soundcharts/Viberate), contacts
(ROSTR), holds/offers/settlements (Prism).

## 6. Performance findings

Local (same code path): shell 3ms/1.3KB, demo 29ms, search 17ms, artist-security
37ms, markets 2ms. Production: container cold start ~28s (known, CI-tolerated),
warm snappy, zero console/page errors in CI UAT. No excessive requests, no huge
assets (app.js 107KB). **PASS.** Rapid-navigation aborts surface one benign
`ERR_EMPTY_RESPONSE` in harness only.

## 7. Competitive matrix

Legend: STRONG / PRESENT / LIMITED / ABSENT / UNKNOWN. Claims from primary sources
(checked Sep 2026): chartmetric.com, soundcharts.com + developers.soundcharts.com,
viberate.com (+ MBW 2026-07-01 MCP story), pollstar.com/data + /Subscribe,
rostr.cc + hq.rostr.cc, prism.fm (+ /why-prism-for-venues-and-promoters,
insights.live), bandsintown.pro + artists.bandsintown.com, songkick.com, dice.fm.

| Capability | FB | Chartmetric | Soundcharts | Viberate | Pollstar | ROSTR | Prism | BIT/Songkick/DICE |
|---|---|---|---|---|---|---|---|---|
| Artist identity | PRESENT | STRONG | STRONG | STRONG | PRESENT | PRESENT | LIMITED | PRESENT |
| Artist metrics (streams/social) | LIMITED | STRONG | STRONG | STRONG | ABSENT | LIMITED | ABSENT | LIMITED |
| Historical metrics | PRESENT | STRONG | STRONG | STRONG | STRONG | LIMITED | STRONG | LIMITED |
| Audience geography | LIMITED | STRONG | STRONG | STRONG | LIMITED | ABSENT | LIMITED | PRESENT |
| Audience demographics | ABSENT | STRONG | PRESENT | STRONG | ABSENT | ABSENT | ABSENT | LIMITED |
| Playlist intelligence | ABSENT | STRONG | STRONG | STRONG | ABSENT | ABSENT | ABSENT | ABSENT |
| Social monitoring | LIMITED | STRONG | STRONG | STRONG | ABSENT | ABSENT | ABSENT | LIMITED |
| Radio | ABSENT | PRESENT | STRONG | PRESENT | ABSENT | ABSENT | ABSENT | ABSENT |
| Concert history | PRESENT | PRESENT | PRESENT | PRESENT | STRONG | PRESENT | LIMITED | PRESENT |
| Festival intelligence | PRESENT | LIMITED | LIMITED | STRONG | LIMITED | PRESENT | ABSENT | PRESENT |
| Venue intelligence | PRESENT | ABSENT | LIMITED | LIMITED | STRONG | UNKNOWN | STRONG | PRESENT |
| Market intelligence | PRESENT | PRESENT | PRESENT | PRESENT | PRESENT | ABSENT | STRONG | PRESENT |
| Ticket sales | ABSENT | ABSENT | ABSENT | ABSENT | STRONG | ABSENT | STRONG | PRESENT |
| Box office/gross | ABSENT | ABSENT | ABSENT | ABSENT | STRONG | ABSENT | STRONG | LIMITED |
| Attendance/capacity config | LIMITED | ABSENT | PRESENT | ABSENT | STRONG | ABSENT | PRESENT | LIMITED |
| Routing | LIMITED | ABSENT | ABSENT | ABSENT | PRESENT | PRESENT | LIMITED | LIMITED |
| Artist representation/contacts | ABSENT | ABSENT | ABSENT | ABSENT | STRONG | STRONG | LIMITED | ABSENT |
| Comparables | LIMITED | STRONG | PRESENT | PRESENT | LIMITED | ABSENT | LIMITED | ABSENT |
| Decision support (offer econ) | PRESENT | LIMITED | LIMITED | LIMITED | LIMITED | ABSENT | STRONG | ABSENT |
| Risk/uncertainty | PRESENT | ABSENT | ABSENT | ABSENT | ABSENT | ABSENT | ABSENT | ABSENT |
| Evidence provenance | STRONG | LIMITED | LIMITED | LIMITED | LIMITED | LIMITED | LIMITED | ABSENT |
| PIT correctness | STRONG | ABSENT | ABSENT | ABSENT | ABSENT | ABSENT | ABSENT | ABSENT |
| UNKNOWN semantics | STRONG | ABSENT | ABSENT | ABSENT | ABSENT | ABSENT | UNKNOWN | ABSENT |
| Alerts | PRESENT | STRONG | STRONG | PRESENT | PRESENT | PRESENT | LIMITED | STRONG |
| Shortlists | PRESENT | STRONG | PRESENT | PRESENT | ABSENT | PRESENT | ABSENT | ABSENT |
| Collaboration/CRM/workflow | LIMITED | LIMITED | LIMITED | LIMITED | LIMITED | LIMITED | STRONG | LIMITED |
| Reporting/exports | LIMITED | STRONG | STRONG | STRONG | STRONG | LIMITED | STRONG | PRESENT |
| API | LIMITED | STRONG | STRONG | STRONG | PRESENT | UNKNOWN | UNKNOWN | PRESENT |
| AI/NL interaction | LIMITED | PRESENT | UNKNOWN | PRESENT | ABSENT | ABSENT | ABSENT | ABSENT |

Notes: Soundcharts API exposes per-event venue/capacity/ticket-price + MCP
`get_artist_concerts`; Viberate launched an MCP server July 2026 (AI-first pivot);
Chartmetric ships Flow AI briefs + Data Assistant; Pollstar Data Cloud covers
319k artists 1999–present + contact DB ($648–$1,869/yr); Prism Insights
(insights.live) pools real shared box-office for demand prediction + holds/offers/
settlements/advancing at 10k+ venues; ROSTR tracks 100ks of artist↔agent/manager/
label/publisher relationships, free tier + Pro.

## 8. Differentiators (evidence-backed, not manufactured)

1. **PIT correctness**: decision-cutoff reconstruction; no competitor claims it.
2. **Explicit UNKNOWN semantics**: NULL stays NULL end-to-end (UI → economics).
3. **Evidence provenance**: every figure carries source + observation/knowledge time.
4. **ARTIST × MARKET × DATE × VENUE × DEAL in one brief** with frozen generation for
   audit. Prism has the deal workflow but not PIT/uncertainty; Pollstar has truth
   but not uncertainty; analytics trio has attention but not deals.
5. **Read-only verifiable generation** (sha-pinned serving artifact).

## 9. Table-stakes missing capabilities

Audience geography/demographics, playlist intelligence, radio/airplay, real ticket
sales/gross/attendance, artist-team contact graph, availability/routing tools,
settlements-grade offer workflow, CSV/API/MCP access (competitors ship MCP in
2026), mobile polish, alerting depth.

## 10. Moat-building opportunities (ranked by buyer value × moat ÷ cost)

1. Last-played/market-timing materialization (data exists upstream; unlocks B/C).
2. Ticket-demand evidence lane (even sparse: onsale/price-range/availability
   observations with provenance; never presented as sales).
3. Contact-graph lane (team/agent/manager linkage; ROSTR-shaped, read-only).
4. MCP/API for the terminal (meet buyers inside Claude/ChatGPT where Viberate
   already plays; read-only, generation-pinned).
5. Identity disambiguation in search (person-vs-band, event counts).
6. Playlist + radio lanes (feed comparables quality; attacks COMPS_SIGNAL_ONLY).
7. Routing/availability view from forward events + festival bills.
8. Unknown-market 404 state; search-hash deep links (back-button correctness).

## 11. Top 20 prioritized improvements

P1: (done) stale artist/search render guards; (done) null-write crashes.
P2: (done) backtest template CSV; last-played dates; demand-evidence lane;
comparables quality; duplicate-identity disambiguation; MCP/API; contact graph;
playlist/radio lanes; availability/routing; unknown-market 404.
P3: (done) favicon, handler once-wiring; search-hash links; mobile polish; alert
depth; exports; break-even copy; onboarding for first-time users (fresh-user test:
purpose discoverable in ~2 min; metric semantics need the provenance chips, which
exist but are subtle).

## 12. Implemented fixes (this cycle)

`apps/terminal/mvp/app.js`: route-token capture + post-await stale guards on all
20 async renders/loaders (49 await sites), `renderBrief` token threading,
underwrite template-handler once-wiring. `index.html`: favicon. New
`design_partner_show_history_template.csv` (canonical headers, 27/27 automap).
New `tests/python/test_terminal_stale_render_guards.py`.

## 13. Deferred roadmap

Data expansion (demand, playlists, radio, contacts), MCP/API, routing view,
market-timing materialization, mobile polish — tracked as P2 backlog above; no
speculative ML, no new vendors, no redesign in this cycle.

## 14. Final production verification

* Fix branch verified locally on production code path: 71-interaction battery,
  0 page errors, races eliminated, XSS escaped, template + favicon 200.
* Merged as PR #79 (`e327320` on main); all CI checks green
  (browser-uat, node, python, cloud-runtime, security).
* Production deploy via `.github/workflows/terminal-production.yml` from main:
  Actions run `35673104505` → **success**. New serving generation
  `terminal_v1_20260921T195313Z` (health matches CURRENT pin).
* Post-deploy authorized CI UAT on the new build: **PASS** — all 14 browser
  checks true, zero console/page errors.
* Post-deploy naked re-probe: `/`, `/health`, `/api/status`, `/api/search`,
  `/_bootstrap` all 401; denied prefixes 404. Fail-closed confirmed.
