# Buyer Decision Workspace V2 — Handoff

Branch: `feat/buyer-decision-workspace-v2`  
Base: `main` at `115c98a` (PR #43 merged)  
Date: 2026-08-24

## What Was Built

### Primary Milestone: BUYER_DECISION_WORKSPACE_V2

The goal: one coherent underwriting object `ARTIST x MARKET x DATE x VENUE x DEAL`
with all supporting evidence organized around it — no magic scores, no claims of
predictive ability.

### Phase 1–2: Unified Proposed-Show Object

**New migration:** `037_buyer_decision_workspace_v2.sql`
- `planning.proposed_shows` — first-class product object for talent buyer underwriting
- `planning.proposal_comparisons` — stored scenario comparison snapshots
- `planning.source_evaluation_log` — external source evaluation audit trail

**New module:** `python/festival_bloomberg/planning/proposed_show.py`
- `create_proposed_show()` / `get_proposed_show()` / `list_proposed_shows()` — CRUD
- `buyer_decision_view()` — Bloomberg-style dense page assembling:
  1. SHOW HEADER (artist, market, venue, date, deal, cutoffs)
  2. EVIDENCE STATUS (KNOWN/ASSUMED/UNKNOWN/CONFLICTING)
  3. VENUE / CAPACITY (safe config, conflicts, review-required)
  4. COMPETITIVE CALENDAR (reuses PR #43 directly)
  5. COMPARABLE EVENTS (artist scorecard lift)
  6. ARTIST / ATTENTION CONTEXT (identity, live history, attention)
  7. SHOW ECONOMICS (linked scenario replay)
  8. RISKS / WARNINGS (capacity conflicts, missing data, assumption-heavy)
  9. PROVENANCE (source traceability)

**Every section calls into proven components — none were rebuilt.**

### Phase 3: Side-by-Side Scenario Comparison

`compare_proposals()` — compare 2+ proposed shows:
- Differences highlighted across date, venue, configuration, market, deal, guarantee
- Comparison table: row-oriented with diff flags
- Venue capacity, competitive calendar, economics, evidence all diffable
- No recommendation or score — just "here's what differs"

### Phase 4–8: Monid/Apify Source Bakeoff

**Monid CLI installed and configured** (v0.1.6, MONID_API_KEY from .env).

**Discovery:** 20+ endpoints discovered across Apify, context.dev, TikHub.
Inspected 6 candidates (free). Executed 5 real paid calls.

**Real measurements:**

| Source | Cost | Record Count | Fields |
|--------|------|-------------|--------|
| Google Maps | $0.0045/result | 1 | title, address, lat, lng, rating, website |
| YouTube | $0.0045/result | 5 | title, viewCount, likes, date, channelName |
| TikTok | $0.00045/result | 0 | noResults (unreliable) |
| Instagram | $0.003/result | 1 | username, followersCount (18.7M), biography |
| Web Scrape | $0.0009/call | N/A | 154KB markdown from Wikipedia |

**Total spent: $0.02835 from $1.00 balance.**

### Phase 9: Source Acceptance Matrix

See: `docs/buyer-decision-workspace-v2/source-acceptance-matrix.md`

**ADOPTED (1):**
- `context.dev/web/scrape/markdown` — highest value/cost ratio for venue/event enrichment

**PILOT ONLY (1):**
- `apify/streamers/youtube-scraper` — good attention data but defer to official YouTube API

**NOT AVAILABLE through Monid (requires direct Apify token):**
- Songkick, Eventbrite (both), Resident Advisor, Bandsintown — the specialist event scrapers

### Phase 10–11: Architecture

All new sources normalize through the existing evidence model:
- No direct dependency on actor schemas
- Raw provenance preserved in `planning.source_evaluation_log`
- Rights disposition documented per source

### What Was NOT Built (per constraints)

- ❌ Generic scraper platform
- ❌ ML demand model
- ❌ "Artist score" or booking recommendation
- ❌ Sentiment trading score
- ❌ Revenue forecast
- ❌ Kafka/Spark/Kubernetes/React rewrite/Postgres migration
- ❌ Crawlee adoption
- ❌ Capacity V3
- ❌ Nine simultaneous integrations

## Tests

**29 new tests** in `tests/python/test_proposed_show.py` covering:
- CRUD (create, get, list, idempotent, version increment)
- Evidence classification (KNOWN/ASSUMED/UNKNOWN/CONFLICTING)
- UNKNOWN propagation (never collapsed to 0)
- Risk derivation (missing data, capacity conflicts, assumption-heavy)
- PIT semantics preservation
- Credential redaction (no secrets in output)
- No booking recommendation in view
- Source provenance identification
- No backdating of current metrics
- Source failure explicit
- Scenario comparison (requires 2+, identifies differences, row-oriented table)
- Provider ID dedup (stable keys)
- Source evaluation log schema
- Economics replay
- Graceful missing-data handling
- Acceptance matrix determinism

**Migration counts updated:** 36→37 in all test assertions.

## Test Results

```
Python: 766 passed, 1 skipped (0 failures)
Node:   76 passed (0 failures)
```

## Product Gate

The workspace supports at minimum:

```python
# Create two proposed shows for comparison
show1 = create_proposed_show(conn, ...,
    artist_name="Kendrick Lamar", market="Chicago, IL", 
    proposed_date="2027-08-01", venue_name="United Center", 
    artist_guarantee=350000, decision_cutoff="2027-06-01")

show2 = create_proposed_show(conn, ...,
    artist_name="Kendrick Lamar", market="Chicago, IL",
    proposed_date="2027-08-08", venue_name="Aragon Ballroom",
    artist_guarantee=275000, decision_cutoff="2027-06-01")

# Compare them side by side
comparison = compare_proposals(conn, ws_conn,
    proposed_show_keys=[show1["proposed_show_key"], show2["proposed_show_key"]])
# → highlights date, venue, guarantee differences
# → shows venue capacity evidence
# → shows competitive calendar (via PR #43)
# → shows comparable event context
# → no score, no recommendation
```

## API Routes

New terminal API routes:
- `POST /api/planning/projects/:key/proposed-shows` — create proposed show
- `GET /api/planning/projects/:key/proposed-shows` — list proposed shows
- `GET /api/planning/projects/:key/buyer-decision?show=<key>` — full buyer view
- `POST /api/planning/projects/:key/compare-proposals` — side-by-side comparison

## Files Changed

### New files:
- `schema/migrations/037_buyer_decision_workspace_v2.sql`
- `python/festival_bloomberg/planning/proposed_show.py`
- `python/festival_bloomberg/acquisition/source_bakeoff.py`
- `tests/python/test_proposed_show.py`
- `docs/buyer-decision-workspace-v2/reuse-audit.md`
- `docs/buyer-decision-workspace-v2/source-acceptance-matrix.md`

### Modified files:
- `schema/workspace_schema.sql` — add proposed_shows, proposal_comparisons, source_evaluation_log
- `python/festival_bloomberg/terminal/server.py` — add 4 new API routes
- `tests/python/test_migrations.py` — 36→37
- `tests/python/test_signal_fabric_evidence.py` — 36→37 (×3)
- `tests/python/test_intelligence_metrics.py` — 36→37
- `tests/scraper/migrations.test.ts` — 36→37 (×2)

## Remaining Product Gaps

1. **Event-source gap:** Songkick, Eventbrite, RA, Bandsintown NOT available through Monid. Need direct Apify token.
2. **SPA panel:** API routes exist but the SPA needs a `buyer-decision` panel in `apps/terminal/static/app.js`.
3. **Monid → proposed_show bridge:** Source bakeoff results aren't yet wired into the buyer view's provenance section.
4. **Historical knowledge-time semantics:** Current scrapes correctly marked as current, not historical.

## Next Milestone Ranking

Based on measured marginal buyer value from this milestone:

1. **HISTORICAL_WEB_EVIDENCE_V1** — Songkick/Bandsintown gigography would materially improve historical event coverage and comparable-event evidence. Direct Apify token required.

2. **DESIGN_PARTNER_DATA_ACTIVATION_V2** — The unified proposed-show object is ready for design partners who can validate whether the buyer view answers real underwriting questions.

3. **SOCIAL_ATTENTION_PANEL_V1** — YouTube/Instagram scrapers are evaluated and cheap. A forward-attention panel for talent buyers is more valuable than abstract "market fundamentals."

4. **MARKET_FUNDAMENTALS_V1** — Market-level data aggregation is less valuable than artist-specific evidence for underwriting decisions.

## Security

- No credentials printed, logged, committed, or serialized
- MONID_API_KEY used via CLI subprocess with NO_COLOR=1 (no output)
- Source evaluation log stores only summaries, never raw credentials
- All existing secret handling patterns preserved

## CI Readiness

All 766 Python tests pass, all 76 Node tests pass. Migration count updated.
Ready for PR.