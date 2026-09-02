# BLOOMBERG_TERMINAL_PRODUCT_HARDENING_V1 — REPORT

Generated: 2026-08-30 · branch `feat/artist-security-25000-database-v1` · PR #57 (draft)
Method: live browser automation against `http://127.0.0.1:8931` (launchd KeepAlive).
Constraint honored: machine under Wikidata full-corpus scan — no latency claims made.

## What changed in this pass

### 1. TODAY became the terminal home
Replaced the static tape listing with a buyer monitor (`/api/monitor` + `/api/today` + `/api/planning/projects`):
- **WATCHLIST (40)** and **SHORTLIST (1)** rows with live links to Artist Security
- **PROJECTS** quick access
- **WHAT CHANGED**: attention movement table built ONLY from observed deltas (same metric + same window span + different values). With the current single-sweep serving generation it honestly renders "NO OBSERVED MOVEMENT" instead of fake movers. A prior version paired weekly-vs-all-time rows and produced nonsense deltas (+99.75%); that SQL was fixed.
- **TICKETING** changes, **UPCOMING** forward events for watched/shortlisted artists, **RECENT** (local workflow memory), compact **DATA HEALTH** below the fold.

### 2. Artist Security is decision-dense above the fold
New structure in order:
1. Security header (identity, tier, freshness, coverage, actions)
2. Sticky function sub-nav: OVERVIEW · ATTENTION · MARKETS · LIVE · FESTIVALS · AUDIENCE · TICKETS · EVIDENCE (anchor scroll)
3. Decision band: **ATTENTION NOW** (per-source latest value + delta chip) · **WHAT IS NEXT** (next event date, venue link, market link, advertised price or explicit NONE) · **TOP MARKETS** (top 5 with show counts + upcoming counts, click-through with context)
4. Second band: **TOP AUDIENCE PEERS** (top 5 with row-specific reasons + one-click COMPARE) · **FESTIVAL EXPOSURE** (count + named series)
5. Detail panels below (source-separated attention with window selector + chart, peers table, market profile, live history, festivals, forward/tickets, alternatives, evidence).

Example — Metallica (HOT_1000): decision band answers all four questions in one viewport: attention now (ListenBrainz 3.69M cumulative, deltas UNKNOWN-honest), next event (2026-10-01 · Sphere · Las Vegas, NV, US · ticket evidence NONE), top 5 markets with show counts, top 5 peers with reasons, festival exposure (7 appearances: Sick New World, Lollapalooza, Reading Festival, Sonisphere Switzerland).

### 3. Attention deltas + chart (evidence-honest)
- Server (`artist_security._attention`): per-row `change` vs the PRIOR observation **of the same metric_kind AND same window span** (weekly never compared to all-time; a row is never its own prior). Cumulative TOTAL_* snapshots and single-observation windows stay UNKNOWN.
- Chart series built only from same (metric_kind, span) groups with ≥2 dated points; SVG line chart with 30D/90D/1Y/MAX selector, hover tooltips (date · source · value), window delta in the meta line.
- Current generation honestly renders "Insufficient observations — chart appears when at least two comparable observations of the same window land." The machinery activates automatically as repeat sweeps land.

### 4. Artist × Market V2
- Market rows now carry real forward evidence where it exists: `future_events`, `next_event` (date · venue · event), `ticket_evidence_available` — joined from the artist's own `future_events` rows by derived city form (`chicago-il` → Chicago). Verified: Death Cab for Cutie chicago-il → 1 upcoming (Beyond Hunger Benefit, The Salt Shed, 2026-11-21). Markets with no forward evidence keep UNKNOWN (never fabricated zeros).
- Every market click carries artist context (sessionStorage) — Market Security renders "**Viewing in context of METALLICA**" with **BACK TO METALLICA** + **COMPARE**.
- Market table simplified to decision columns: Market · Historical shows · Venues · Last played · Future events · Next event · Ticket evidence.

### 5. Compare V2
Flat 9-row table replaced with grouped sections: **AUDIENCE · ATTENTION · LIVE · MARKETS · FESTIVALS · FORWARD · TICKETS**, each showing both artists' values side-by-side with amber-highlighted differences. MARKETS section computes overlap client-side: "0 shared · 5 only-Linkin Park / 5 only-Metallica" plus per-side only-market lists. Actions: **SWAP**, **CHANGE COMPARATOR** (type + search), **ADD A/B TO SHORTLIST** (project picker). No winner, no score.

### 6. Peer explanations
`why_related` is now composed per row from the edge's own evidence: "350 shared listeners · Jaccard 0.0368 · 10 shared markets · 1 shared festival bills". Structured COMPARE buttons per peer (artist page + peers table).

### 7. Sparse states collapsed
Empty sections render as one compact status line ("NO CURRENT TICKET EVIDENCE · last checked …") instead of giant empty cards/tables. Verified on Metallica's Wikimedia/YouTube panels.

### 8. Terminal workflow memory + keyboard
- localStorage recents (artists/markets/venues/festivals/compares) → TODAY RECENT module; verified live.
- `/` and Cmd/Ctrl+K focus search; Escape clears; **↑/↓ select search results, Enter opens selected**.

## Buyer scenario acceptance (live browser, timed)

| Scenario | Result | Time | Clicks/actions |
|---|---|---|---|
| A — investigate artist for Chicago (search → artist → Chicago market → context bar → back) | **PASS** (context preserved both ways) | 8.4s | 3 |
| B — replacement artist (peer COMPARE → grouped compare → SWAP) | **PASS** (7 highlighted differences; swap works) | 5.5s | 2 |
| C — what's happening now (decision band + subnav) | **PASS** | 3.8s | 1 |

All three are well inside targets (A<60s, B<90s, C<45s).

## Test evidence
- `tests/python/test_intelligence_terminal.py`: 21/21 passed.
- `tests/python/test_artist_security_25000.py`: passed.
- `node --check` on app.js; `py_compile` on server/artist_security/readmodels.

## Remaining P2/P3 (next pass)
1. Attention chart/CHANGE activate only when repeat observations land — needs the LB sweep cadence or the full-corpus run (deliberately not started this pass).
2. LIVE HISTORY venue cells UNKNOWN — blocked on venue materialization wave (data-side).
3. Compare MARKETS overlap uses only top-5 strongest markets from the compare summary; full 47-market overlap would need the full market lists in the summary.
4. Sub-nav uses smooth scroll; consider instant jump + active-section highlight.
5. Market page "Context series" panel is empty (no provider-gated context acquired) — renders honest empty state.
6. Sparse artist full sweep (10 HOT/10 CORE/10 COVERAGE visual audit) still pending.
7. RECENT rows show raw ISO timestamps; format to relative/short.

## Incident note
Disk hit 100% (116MiB free) mid-pass, breaking pytest temp-dir creation. Root cause: large Chrome browser caches (1.4GB) + node-gyp/Homebrew caches on a 228GB disk shared with two 5GB DuckDB serving/warehouse files and a 7GB unrelated project. Freed ~2GB of pure regenerable caches (no data touched). Recommend periodic cache hygiene and monitoring the two DuckDB files' growth.
