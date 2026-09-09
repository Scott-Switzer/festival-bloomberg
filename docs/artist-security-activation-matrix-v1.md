# ARTIST_SECURITY Activation Matrix — v1
*As of 2026-09-08 · origin/main @ a9551ce*
*Audit method: serving artifact read-only + API/handler + static SPA + acquisition/policy code. LIVE requires real source → raw → Gold → serving → API → hosted page → measured coverage/freshness → browser.*

## Serving baseline
- Generation: `terminal_v1_20260908T194538Z` (built 2026-09-08T19:45:35.916Z)
- SHA `d6b38393…`
- Tables: artists 25k, attention 50.9k (listenbrainz-only, **knowledge_time 2026-08-15 — stale ~24d**), factor 89.3k (youtube-only), peers 126.9k (LB full-corpus, 13.2k artists), sentiment 111 (youtube VADER), markets 27.3k (estate-summary), events 134k, festivals 14.7k, future 6.4k, tickets 771.

## Matrix

| Feature | Source | Rows | Distinct artists / 25k cov | Latest obs | Freshness (avg) | Depth | Rights / Gold / Serving / API / Hosted | Verdict |
|---|---|---|---|---|---|---|---:|---|
| Identity + external IDs | Wikidata / MB | 47.6k ext IDs, 40.7k search terms — 25k artists | 25k 100% | estate 2026-08-28 | n/a | 25k universe | LICENSED OK / n/a / SERVING / API LIVE / Hosted LIVE | **LIVE_GOOD** |
| ListenBrainz peers | LB full corpus dump 2593 | 126,898 edges, 1,081,761 Gold edges | **13,220 52.9%** (929/1k HOT, only 44.9% of COVERAGE_25000) | Gold 2026-09-08 | fresh (Gold 2026-09-08) | peer graph | Provider terms OK / GOLD LIVE (b188…) / SERVING LIVE / API LIVE / Hosted LIVE | **LIVE_GOOD** (tail 11.8k artists peers=UNKNOWN) |
| Attention (consumption time series) | ListenBrainz weekly | **50,886** | **25,000 100%** but values frozen | **2026-08-15** period_end max | **~24 days stale** | 2002-01-01 → 2026-07-27 | OK / Gold SILVER partial / SERVING / API / Hosted | **STALE** — single source, no Live refresh in ~24d |
| Artist Factor Tape | **YouTube API only** | **89,388** | — distinct artists ≈ ? (see `SELECT count(DISTINCT artist_key)`) | max observation_time **2026-09-05** (avg 6.7d) | 6.7d avg | 2026-08-27 → 2026-09-05 | YOUTUBE_API terms-review / no Gold / SERVING / API / Hosted | **LIVE_SPARSE** — only youtube factor_family; Spotify/Trends/Social unobserved |
| Sentiment / fan | youtube comments → VADER_3.3.2 | **111** | **111 0.44%** | date **2026-09-03** single day | 5d | **1 day window** | YT terms-review / baseline only / SERVING / API / Hosted UNKNOWN for 99.5% | **LIVE_SPARSE** — baseline kept, richer model not LIVE |
| Live-event history | MB/SetlistFM/SeatGeek derived events | **134,449** | ~? distinct (much <25k) | event_dates back to **1715**, future to 2028 (dirty data: min 1715-12-22) | stale/gaps | unknown ingest freshness | Conditional / n/a / SERVING / API / Hosted | **STRUCTURAL_ONLY** — table exists, per-artist coverage unmeasured, dates include obvious bad data |
| Festival history | festival_appearances | **14,758** | very sparse | same window | — | — | — / — / SERVING / API | **LIVE_SPARSE** — <1 appearance/artist avg |
| Forward events | future_events | **6,459** | sparse | min 2026-09-08 (today window) | 48d *future* avg | through 2027-06-19 | — / — / SERVING / API / Hosted | **LIVE_SPARSE** — many artists 0 future events |
| Ticket-market history | marketplace listing pages | **771** | **<<1% artists** | 2026-09-05 (avg 3d) | 3d | 2 marketplaces | Terms-review / no Gold / SERVING / API / Hosted UNKNOWN for ~97% | **LIVE_SPARSE** |
| Markets / geography | **estate summary** (`artist_markets` = `PILOT_25K_ESTATE_SUMMARY`) + `artist_geography_observations` **MISSING from serving** | 27.3k markets (observed_shows=counts) | unknown distinct | estate 2026-08-28 | — | — | Structural / — / SERVING (markets as summary) / API | **STRUCTURAL_ONLY** — geography panel returns `UNKNOWN / No artist_geography_observations in this serving generation` for every artist; market footprint is estate summary, not observed market activity |
| Relationships / external IDs | Wikidata | part of ext IDs | 25k | n/a | — | — | — / — / API | **LIVE_SPARSE** (rendered only as identity aliases) |
| News / GDELT | gdelt provider + acquisition router | **0 rows in serving** | 0 0% | — | — | — | GDELT approved/conditional (content CC BY 4.0, legal review) / **NO Gold** / **NOT in serving** | **IMPLEMENTED_NOT_ACTIVATED** — provider class+policy exist, no pipeline writing to serving |
| YouTube acquisition | youtube provider (official comments API) | 111 sentiment + 89k factor rows downstream | single-digit % | 2026-09-03/2026-09-05 | 3–7d | — | YT terms-review / baseline / SERVING sparse | **LIVE_SPARSE** |
| Wikimedia / Wikipedia | wikimedia provider | **0 attention rows** beyond listenbrainz | 0% for wiki | — | — | — | Approved-with-conditions / not activated / not in serving | **NOT_AVAILABLE** in serving (attention = LB-only) |
| Spotify / Soundcharts / Chartmetric | spotify provider | **0 factor rows** | 0% | — | — | — | `PROVIDER_READY / AUTH_REQUIRED` or `LICENSE_REQUIRED` — provider classes exist, no credentials configured | **IMPLEMENTED_NOT_ACTIVATED** (server returns AUTH_REQUIRED, not UNKNOWN) |
| Google Trends | trends provider | **0** | 0% | — | — | — | `WAITLIST / AUTH_REQUIRED` | **NOT_AVAILABLE** |
| Alternative explainers | derived from `artist_peers` (shared markets/bills + LB) | derived | 52.9% wherever peers exist | Gold-dependent | fresh where peers fresh | — | — / — / API | **LIVE_GOOD** where peers exist, else UNKNOWN |
| Video/crawler lanes (Apify, Browser Run, Hetzner Crawlee) | apify/scrapling/commoncrawl providers + Hetzner env | **not provisioned** | 0% | — | — | — | Code exists, **no Cron/Queue/dedupe/budget governor wired to Artist Security refresh** | **NOT_AVAILABLE** — lanes exist as provider classes, not as a refresh fabric |
| R2 / serving build | lake GOLD/serving pipeline | listenbrainz artist_day **38/288 months** complete | — | 2026-09-08 serving | builds on demand, not scheduled | — | n/a / Gold partial / SERVING LIVE | **IMPLEMENTED_NOT_ACTIVATED** for continuous refresh |

## Provenance model (honest by construction)
- `UNKNOWN != 0` enforced by contract — panels return `UNKNOWN` with explicit `note`, not zero rows.
- `factor_tape` carries `source`, `source_scope`, `rights_status`, `evidence_ref` per observation; `attention` carries `source_system/source_scope/rights_status/knowledge_time`.
- No invented popularity score exists — no fake ranking fabricated for crawler priority (none exists).

## Coverage snapshot (extracted read-only)
```
artists 25000
attention 50886  (listenbrainz 50886)  distinct 25000 — period 2002-01-01..2026-07-27  knowledge max 2026-08-15
factor  89388    (youtube_api 89388)   factor families: presumably YT only
peers   126898   13220 artists 52.9%   (HOT 929/1000, COVERAGE_25000 8983/20000)
sentiment 111    111 artists 0.44%     date single day 2026-09-03  VADER_3.3.2
events  134449, festivals 14758, future 6459, tickets 771
```
- Freshness p50/p95 not derivable from single `knowledge_time` column alone; full distribution requires bucketing — report currently uses avg age above. Panel freshness instead uses per-artist `max(knowledge_time)` formatted as `latest knowledge <timestamp>` and `evidence_coverage`.

## Rights/commercial snapshot
- Wikidata CC0 — APPROVED.
- Wikimedia/Wikipedia/GDELT — APPROVED_WITH_CONDITIONS (attribution, CC BY variants).
- YouTube — TERMS_REVIEW_REQUIRED (provider lineage attached per row).
- Spotify/Soundcharts/Chartmetric/Trends — LICENSE_REQUIRED / WAITLIST, provider classes present, no authorization configured → returned as `PROVIDER_READY / AUTH_REQUIRED`.

## What is NOT LIVE
- `artist_geography_observations` missing from serving → Geography panel always UNKNOWN (STRUCTURAL_ONLY).
- Any news/catalyst tape, multi-view similarity, momentum baselines/ML, refresh scheduler, and observability panel — code paths for providers/planner exist, but nothing wires them to a continuously refreshed Artist Security record.

## Verdict prelude (Phase 0 only)
ARTIST_SECURITY is a **partially activated collection of independently built data products sharing one 25K terminal**, not a continuously refreshed live security. The TAM database is live; the intelligence layers are individually sparse or stale.

## Next step
Do not rebuild what exists. The fix sequence starts from the lowest-effort live-defects (stale attention, docs mislabel, sentiment single-day) and works outward toward the missing fabric (acquisition + source policy + evidence + refresh), before any peer/momentum research expansion.
