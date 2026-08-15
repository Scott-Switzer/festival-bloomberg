# AGENTS.md — Festival Intelligence orientation for coding/research agents

Before planning ANY change, run the context snapshot:

```bash
PYTHONPATH=python .venv/bin/python -m festival_bloomberg.ops.context_snapshot
```

Then read `docs/project_manifest.yaml` for the current capability manifest.
This repository is large and accumulated over many PRs; forgetting an existing
asset is itself an engineering defect. Before concluding "we don't have X",
search: (1) current code, (2) the DuckDB warehouse, (3) `docs/`, (4) git
history/branches, (5) the legacy `~/CascadeProjects/festival-intelligence`
checkout, (6) the provider registry.

## Mission

Festival Intelligence is the quantitative information infrastructure for live
entertainment. The near-term product is a **read-only information terminal**
(`apps/terminal/` + `python/festival_bloomberg/terminal/`) that answers: who,
where, when, what changed, what are the sources, what is known/unknown. It is
NOT yet a booking/recommendation product.

## Non-negotiable semantics (do not violate)

- UNKNOWN != 0 (NULL stays NULL; missing is never encoded as zero)
- event_time != knowledge_time != source_publication_time != retrieved_at
- archive capture time != publication time
- announcement != booking; onsale != announcement; presale != onsale
- OFFSALE != SOLD_OUT; zero listings != SOLD_OUT
- capacity != attendance; reported attendance != paid tickets != scans
- estimated != observed; multi-show aggregate != individual result
- fan attention != local demand; news sentiment != ticket purchase intent
- current API response != historical API response
- conflicting source claims COEXIST (never silently reconciled)
- capacity claims are CLAIMS (never collapse to one exact number)
- DeepSeek/NVIDIA models PROPOSE evidence; deterministic code decides admissibility

## Architecture (current)

- **Warehouse**: DuckDB. Base DDL in `schema/duckdb.sql` (schemas: raw, core,
  metrics, model, audit) + versioned migrations in `schema/migrations/`
  (auto-applied by `python/festival_bloomberg/migrations.py`). The TS side
  mirrors migrations in `src/scraper/migrations.ts`.
- **Canonical research DB**: `data/warehouse/boxoffice_research_v2.duckdb`
  (terminal + flywheel + research/economics schemas live here).
- **Acquisition**: `python/festival_bloomberg/acquisition/` — router, policy,
  transport, health, costs, and provider implementations in
  `acquisition/providers/` (ticketmaster, setlistfm, seatgeek, youtube, monid,
  apify, commoncrawl, openstreetmap, wikidata, wikimedia, wikipedia, http,
  nws, scrapling, eventbrite). All fail-closed; credentials via `localenv.py`.
- **Flywheel**: `python/festival_bloomberg/flywheel/` — event graph, forward
  watch, outcome hunter, PIT reconstruction, pre-event cutoffs, acquisition
  priority (value-of-information), evidence documents/claims/verification.
- **Festival spine**: `schema/duckdb.sql` core.festivals /
  core.festival_editions / core.festival_stages / core.lineup_slots +
  migration 023 core.festival_billing_observations. Repository + seed in
  `python/festival_bloomberg/festivals/`.
- **Intelligence terminal**: `python/festival_bloomberg/intelligence/`
  (readmodels, tape, providers, ask, llm) + `terminal/server.py` (read-only
  HTTP + static SPA) + `apps/terminal/static/`.
- **Config**: `python/festival_bloomberg/localenv.py` (canonical .env loader,
  OS env wins, never logs values) + `python/festival_bloomberg/config.py`
  (presence-only credential status). `.env` is gitignored; never commit it.

## Accepted milestones and known negative results

- `BASELINE_RESEARCH_V1 = COMPS_SIGNAL_ONLY` — historical comparables have
  signal but not enough temporal continuity/repeat history to justify
  sophisticated underwriting models. DO NOT revive advanced ML because the
  terminal looks better; data expansion and predictive validation are
  separate tracks.
- Pre-event cutoffs (ANNOUNCEMENT / ONSALE / BOOKING) are still ~0 for the
  public historical corpus; result availability is NOT pre-event knowability.
- `HISTORICAL_DECISION_EVIDENCE_ENGINE_V1` and earlier acquisition milestones:
  see `docs/*.md` for exact findings.

## Key commands

```bash
# full python suite (offline; hermetic, skips .env)
.venv/bin/python -m pytest tests/python -q
# node suite + typecheck
npm test && npm run typecheck  # (verify exact script names in package.json)
# apply migrations / run OA
PYTHONPATH=python .venv/bin/python -m festival_bloomberg.oa.data_estate
# terminal
PYTHONPATH=python .venv/bin/python -m festival_bloomberg.terminal.server --port 8931
```

## Source/rights discipline

SeatGeek: DISABLED for automated corpus/LLM ingestion (terms). Setlist.fm:
noncommercial unless arranged. JamBase: bounded trial benchmark only, not a
permanent dependency. Spotify Dev Mode (2026): no followers/popularity, no
top-track endpoint; the Web API client-credentials provider is operational
(identity/catalog only — `id/name/external_urls/images/type/uri`). The `spak_`
credential is a separate **Spotify Soloist** key with no public contract —
keep it isolated, never send it to the Web API. NVIDIA NIM:
prototyping/research via developer program.
Never send private promoter/customer settlement data to hosted LLMs.
