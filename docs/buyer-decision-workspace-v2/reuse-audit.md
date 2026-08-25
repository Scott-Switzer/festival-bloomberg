# Phase 0 — Reuse Audit: Buyer Decision Workspace V2

## Existing Components → Reuse Decisions

| Component | Path | Decision | Notes |
|-----------|------|----------|-------|
| **Talent Buyer Workbench** | `planning/repository.py`, `planning/candidates.py`, `planning/scenario.py` | REUSE | Projects, shortlists, scenarios, candidate universe — the planning schema is the correct foundation for proposed-show objects. Extend, don't replace. |
| **Competitive Calendar** | `planning/competitive_calendar.py` | REUSE DIRECTLY | PIT tri-state, distance buckets, segment windows — exactly what the buyer view needs. No reason to fork. |
| **Show Economics** | `economics/show_economics.py`, `economics/show_economics_product.py` | REUSE DIRECTLY | Deterministic engine with TypedInput provenance, sensitivity, boundary, compare_spared_scenarios. |
| **Venue Capacity** | `economics/capacity.py`, `economics/wikipedia_capacity.py` | REUSE DIRECTLY | assess_venue_claims + capacity_prefill already handles conflicting claims, safe prefill, evidence classification. |
| **Artist Scorecard** | `planning/candidates.py` → `artist_scorecard()` | REUSE SECTIONS | Identity, live history, attention, market history, comparables — lift into buyer view sections. |
| **Evidence Repository** | `evidence/repository.py` | REUSE | Provenance chain: raw → evidence → canonical → read model. |
| **Terminal Server** | `terminal/server.py` + `apps/terminal/static/app.js` | EXTEND | Add /api/planning/projects/:key/buyer-decision + /api/planning/projects/:key/compare-proposals |
| **Serving Snapshot** | `terminal/storage.py` | REUSE | Read-only serving snapshot + mutable workspace DB — unchanged. |
| **Workspace Schema** | `schema/workspace_schema.sql` | EXTEND | Add `planning.proposed_shows` table. |
| **Secret Handling** | `localenv.py` + `.env` | REUSE | `load_local_env()` reads APIFY_TOKEN and MONID_API_KEY from `.env`. |
| **Ticketmaster Provider** | `acquisition/providers/ticketmaster.py` | REUSE | Already used by market_calendar sweep. |
| **OA Data Fabric** | `oa/data_fabric.py`, `oa/market_calendar.py` | REFERENCE ONLY | Partition model, sweep infrastructure — reference pattern but don't reuse for new providers. |

## What Does NOT Need Rebuilding

- ✅ Competitive calendar engine (PR #43)
- ✅ Show economics engine
- ✅ Venue capacity reconciliation
- ✅ Artist scorecard sections (identity, live history, attention)
- ✅ Planning workspace (projects, shortlists, candidates)
- ✅ Evidence/provenance chain
- ✅ Serving snapshot + workspace DB architecture
- ✅ Secret handling pattern
- ✅ Terminal SPA framework

## What IS Being Built

1. **Unified proposed-show object** — new migration + Python module that wraps existing components
2. **Buyer decision view** — new terminal API + new SPA panel
3. **Proposal comparison** — extends existing scenario comparison
4. **Monid/Apify source bakeoff** — new acquisition modules + acceptance matrix
5. **Source rights disposition** — governance documentation