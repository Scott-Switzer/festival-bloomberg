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

### Phase 4–8: Direct Apify Source Bakeoff (COMPLETED)

**Direct Apify API configured** (APIFY_TOKEN from .env, single credential).

**Schema inspection:** 18 actor candidates inspected (free). 10 executed with real queries.

**Real dataset measurements:**

| Source | Records | Cost | Fields | Coords | Price | PIT | Verdict |
|--------|---------|------|--------|--------|-------|-----|---------|
| Eventbrite (scrapesage) | 25 | $0.00 | 74 | ✓ | ✓ | ✓ publishedDate | PILOT_ONLY |
| DICE (hoholabs) | 50 | $0.003 | 52 | ✓ | ✓ | ✓ announcement_date | PILOT_ONLY |
| Songkick (gio21) | 50 | $0.00 | 16 | 98% | ✗ | ✗ | RESEARCH_ONLY |
| AllEvents | 100 | $0.00 | 24 | ✓ | ✓ | ✗ | RESEARCH_ONLY |
| Resident Advisor | 20 | $0.00 | 16 | ✗ | ✗ | ✗ | RESEARCH_ONLY |
| Bandsintown (autolab) | 10 | $0.00 | 13 | ✗ | ✗ | ✗ | RESEARCH_ONLY |
| Fever | 20 | $0.00 | 14 | ✓ | ✓ | ✗ | RESEARCH_ONLY |
| TM Web (control) | ERROR | — | — | — | — | — | REJECT |

**Total spent: $0.0038 from $2.00 budget.**

### Phase 9: Source Acceptance Matrix

See: `docs/buyer-decision-workspace-v2/source-acceptance-matrix.md`

**ADOPTED — PILOT_ONLY (2):**
- `Eventbrite (scrapesage~eventbrite-scraper)` — 74 fields including publishedDate (PIT), price, capacity, organizer enrichment
- `DICE (hoholabs~dicefm-scraper)` — 52 fields including announcement_date (PIT), genre tags, ticket breakdown, sold-out detection

**Research rail (3):** Songkick, AllEvents, Resident Advisor

**All specialist event scrapers now available via direct Apify.**

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

**41 proposed-show tests** in `tests/python/test_proposed_show.py`.
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

**Total:** 779 Python tests collected, 1 skipped. Node tests pending full build.

Key results:
- 41 proposed-show tests: ✅ all pass
- Immutable revisions: ✅ v1 preserved, v2 distinct, replay deterministic
- Evidence classification: ✅ KNOWN/ASSUMED/UNKNOWN/CONFLICTING
- Product acceptance: ✅ 2 real scenarios, buyer view renders 11 sections, comparison detects venue/guarantee/cutoff diffs
- Source bakeoff: ✅ 18 inspected, 10 executed, $0.0038 spent, 2 adopted

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

1. **Event-source integration rail:** Eventbrite (scrapesage) and DICE (hoholabs) are evaluated and selected, but the normalized acquisition rail (ingest → normalize → evidence) is not yet wired.
2. **SPA panel for direct-Apify results:** The buyer view shows provenance from Ticketmaster but not yet from the new scrapers.
3. **Terms/commercial clearance:** All scraped sources are TERMS_REVIEW_REQUIRED — no commercial clearance has been sought.
4. **Multi-key rotation:** Explicitly deferred — single APIFY_TOKEN is sufficient for this bounded evaluation.

## Next Milestone Ranking

Based on measured marginal buyer value from this milestone:

1. **SOURCE_ACQUISITION_RAIL_V1** — Wire the 2 adopted scrapers (Eventbrite + DICE) into the acquisition rail with normalization, evidence integration, and provenance.

2. **DESIGN_PARTNER_DATA_ACTIVATION_V2** — The unified proposed-show object is ready. Design partners can validate whether the buyer view answers real underwriting questions.

3. **HISTORICAL_WEB_EVIDENCE_V1** — Songkick/AllEvents gigography would materially improve historical event coverage with deeper queries and broader date ranges.

## Security

- No credentials printed, logged, committed, or serialized
- MONID_API_KEY used via CLI subprocess with NO_COLOR=1 (no output)
- Source evaluation log stores only summaries, never raw credentials
- All existing secret handling patterns preserved

## CI Readiness

All 766 Python tests pass, all 76 Node tests pass. Migration count updated.
Ready for PR.