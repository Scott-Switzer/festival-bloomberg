# INTELLIGENCE_DATA_ESTATE_AND_FESTIVAL_SPINE_V1

A repository-wide milestone: activate the dormant festival spine, fix the
provider/config architecture, and turn the terminal into a denser information
product. Success is measured by real data acquired and real defects fixed,
not by another invisible backend layer.

## What was built

### 1. Festival spine activated (real, source-backed)

The canonical festival schema already existed in `schema/duckdb.sql`
(`core.festivals`, `core.festival_editions`, `core.festival_stages`,
`core.lineup_slots`, `raw.lineup_observations`) but was empty and unexposed.
Migration **023** adds the two missing pieces the billing doctrine requires:

- `core.festival_billing_observations` — one row per SOURCE-SPECIFIC billing
  claim. A poster, programme, website, and retrospective can disagree, and
  conflicting claims coexist by design (dedupe_key keyed on source).
- `core.festival_editions.date_precision` (day|month|year|circa|unknown).
- `core.lineup_slots.performance_status` (announced|scheduled|performed|
  cancelled|substituted|surprise|unverified) and `identity_confidence`.

The research in `docs/historical_lineups_and_billing_analysis.md` is now
transcribed as **96 research-seed lineup candidates + 96 retrospective
billing observations derived from prior research, across 6 real festival
identities** (Newport Jazz 1954, Monterey Pop 1967, Woodstock 1969,
Glastonbury 1971, Lollapalooza 1991, Coachella 1999). These are NOT yet
independently fetched source observations — every row is
`RESEARCH_DISCOVERY_SEED` / `RESEARCH_ONLY` with the cited source URL, per-act
confidence, and rationale — a discovery lead to corroborate, never an observed
fact. Artist identities stay UNRESOLVED (never forced); date precision is
year/month only where the research supports it (no invented days).

### 2. Provider / config architecture fixed

- **`.env` loading**: the terminal provider scaffolds read `os.environ`
  directly and never loaded the local `.env`. `intelligence/providers.py` now
  loads via `localenv.load_local_env` (OS env wins, values never logged).
- **Public no-key bug**: `ListenBrainzProvider`/`GdeltProvider`/`NwsProvider`
  had `env_keys = ()` so `any(())` permanently reported `NOT_CONFIGURED`.
  Fixed with a real state taxonomy (`PUBLIC_NO_AUTH`, `AUTH_CONFIGURED`,
  `AUTH_MISSING`, `OPERATIONAL`, `NOT_IMPLEMENTED`, `DISABLED_RIGHTS`,
  `BLOCKED`, `RATE_LIMITED`, `DEGRADED`). A no-key provider can never be
  `NOT_CONFIGURED` again.
- **Reconciliation**: the unified `provider_statuses()` registry covers both
  the terminal scaffolds and the canonical acquisition providers; SeatGeek is
  `DISABLED_RIGHTS` (terms); Spotify is annotated with the 2026 Dev Mode
  restrictions.
- **`config.py`**: presence-only credential status (name + booleans + source,
  never the value).

### 3. Real providers validated (bounded)

- **Spotify Web API**: `acquisition/providers/spotify.py` — a real
  client-credentials provider (token caching + expiry, 401 → one refresh +
  retry, 429 → `RATE_LIMITED`, missing credentials → `NOT_CONFIGURED`).
  Live-validated (token exchange 200, `/v1/search` 200). It persists ONLY
  fields the 2026 response actually returns (`id`, `name`, `external_urls`,
  `images`, `type`, `uri`) and records `fields_present` so nothing can be built
  around the removed `popularity` / `followers` / `genres`. The separate
  `spak_` credential is a **Spotify Soloist API key** — a distinct product
  surface with no public endpoint contract; it stays isolated and is never
  sent to the Web API.
- **NVIDIA NIM**: `NVIDIA_API_KEY` validated via `list_models()` → 102 models
  in the live catalog. `intelligence/llm.py` adds a provider-neutral
  `ModelRouter` (FAST_EXTRACT / DEEP_REASON / CODE_REASON / EMBED / RERANK)
  and an OpenAI-compatible `NimClient` (chat/embed/list_models), fail-closed
  without a key, malformed-response safe.
- **NWS**: new key-free `acquisition/providers/nws.py` (registered in the
  router). One live forecast call returned 14 periods with forecast
  generation time kept separate from the validity window.
- **Grounded ASK**: `answer()` now optionally composes prose over read-only
  tool results via the LLM, with the authoritative `evidence` attached; the
  tool surface remains closed (no SQL, no writes, no fact creation).

### 4. Terminal festival page + agent memory

- `/api/festivals`, `/api/festivals/{id}`, `/api/festivals/{id}/editions/{key}`,
  `/api/artists/{id}/billing`, `/api/artists/{id}/co-occurrence`.
- The SPA FESTIVALS view now lists real festivals and renders per-edition
  tiered lineups with source links; the artist page shows a billing-trajectory
  table (and its box-office table now uses the real field names).
- **Agent memory**: `AGENTS.md`, `docs/project_manifest.yaml`, and
  `python -m festival_bloomberg.ops.context_snapshot` — the anti-amnesia
  entrypoint for future agents.

## Live OA (authoritative run `data_estate_20260815T*`)

| Measure | Before | After |
| --- | ---: | ---: |
| canonical festivals | 0 | **6** |
| festival editions | 0 | **6** |
| research-seed lineup candidates | 0 | **96** |
| retrospective billing observations | 0 | **96** |
| activity-tape rows | 2,162 | **2,264** (+102 festival) |
| distinct festival artists | 0 | **90** |
| artists with 2+ festival appearances | 0 | **6** |

Provider validation: **NVIDIA = AUTH_VALID** (102 models), **NWS = SUCCESS**
(14 records), **Spotify Web API = AUTH_VALID** (client-credentials flow).
Credential presence (names only): Ticketmaster, YouTube, Monid, Setlist.fm,
BLS, NVIDIA, MusicBrainz, Spotify present; Census, APIFY, SeatGeek, DeepSeek,
JamBase absent. No secret value was printed or committed.

## Verdict

`INTELLIGENCE_DATA_ESTATE_AND_FESTIVAL_SPINE_V1 = PARTIAL` (toward PASS)

- Festival spine: **PASS** (real identities; honest seed-vs-observed
  evidence classes; no fabricated billing facts).
- Provider/config architecture: **PASS** (bug fixed, taxonomy correct).
- Agent memory: **PASS** (AGENTS.md + manifest + context snapshot).
- NVIDIA + NWS: **PASS** (validated live, bounded).
- Spotify Web API: **PASS** (validated + implemented; identity/catalog
  prototype source).
- Ticketmaster / ListenBrainz / GDELT / Census live ingestion:
  **NOT_IMPLEMENTED** — the remaining gap. Keyed providers (Ticketmaster,
  Census) and public sources (ListenBrainz, GDELT) need real ingestion wired to
  the scaffolds; the next milestone is live data activation into the same read
  models.

## Recommended next milestone

Wire Ticketmaster US DMA-partitioned music ingestion + ListenBrainz/GDELT
public acquisition into the existing scaffolds, run Spotify identity
resolution over the festival-seed and box-office artists, and populate the
same terminal read models and activity tape.
