# CLOUD_DATA_PLANE_V1 — Migration Report

**Date:** 2026-08-26
**Status:** ✅ PASS
**Migration ID:** cloud-data-plane-v1
**Branch:** `feat/ticket-market-data-moat-v2` (post-#46)

---

## Hard Gate

```
CLOUD_DATA_PLANE_V1 = PASS

CLOUD_RECOVERY_FROM_EMPTY_DATA_DIR = PASS
```

From an **empty temporary directory**, recovered:
- 2 DuckDB databases (SHA256 verified)
- 96,556 Ticketmaster event observations from R2 Parquet
- Cross-source query (recovered DB + R2 Parquet) succeeded

---

## Cloudflare Account

| Field | Value |
|---|---|
| Account ID | `51b88c6a6ef833b3c2ff46e98d5d9356` |
| Email | `scswitzer@chapman.edu` |
| Auth method | OAuth (wrangler) + R2 API token |

## R2 Buckets Created

| Bucket | Purpose | Status |
|---|---|---|
| `festival-intelligence-raw` | Content-addressed raw evidence (zstd-compressed) | Private ✅ |
| `festival-intelligence-lake` | Parquet tables queryable via DuckDB httpfs | Private ✅ |
| `festival-intelligence-backups` | Canonical DuckDB backups, manifests, checksums | Private ✅ |

## Objects Uploaded

### Backups (27 objects, 5.16 GB)

| Artifact | Size | SHA256 (first 16) | Verified |
|---|---|---|---|
| `boxoffice_research_v2.duckdb` | 5.0 GB | `08ac97d0244ed4d...` | SIZE_MATCH |
| `artist_market_event_history.duckdb` | 39 MB | `0c5b71f09906c66...` | UPLOADED |
| `festival_bloomberg.duckdb` | 16 MB | `3cd71d003555f78...` | ROUND_TRIP_SHA256 ✅ |
| `youtube_fan_signal_oa.duckdb` | 26 MB | `71f8eee74c0f547...` | UPLOADED |
| `youtube_fan_signal_v1.duckdb` | 10 MB | `81ce70fabc00aa3...` | UPLOADED |
| `design_partner_retrospective_oa.duckdb` | 13 MB | `86ff8507d7df7eb...` | UPLOADED |
| `boxoffice_research.duckdb` | 8 MB | `d9bf96baddddecb...` | UPLOADED |
| `ticket_market.duckdb` | 36 MB | `52eb5b85bccdce7...` | ROUND_TRIP_SHA256 ✅ |
| `acceptance_workspace.duckdb` | 799 KB | `c480ee365856db1...` | UPLOADED |
| `terminal_workspace.duckdb` + WAL | 1 MB | — | UPLOADED |
| `watch_universe_v1.json` | 89 KB | `490c2da92ccb040...` | UPLOADED |
| `ticket_market_cost_model.json` | 3 KB | `cd85e8723a2f072...` | UPLOADED |
| Bakeoff JSON (13 files) | 440 KB | — | UPLOADED |

### Lake (17 Parquet files, 576 MB)

| Table | Rows | Path |
|---|---|---|
| `provider_event_snapshots` | 96,556 | `events/provider_event_snapshots/` |
| `artist_attention_observations` | 227,367 | `metrics/artist_attention_observations/` |
| `artists` | 114,167 | `core/artists/` |
| `venues` | 82,547 | `core/venues/` |
| `entity_external_ids` | 432,877 | `core/entity_external_ids/` |
| `entity_relationships` | 202,156 | `core/entity_relationships/` |
| `event_performers` | 347,316 | `core/event_performers/` |
| `artist_aliases` | 113,040 | `core/artist_aliases/` |
| `artist_resolution_keys` | 660,084 | `core/artist_resolution_keys/` |
| `alerts` | 237 | `core/alerts/` |
| `alert_related_entities` | 837 | `core/alert_related_entities/` |
| `activity_tape` | 102,766 | `terminal/activity_tape/` |
| `boxoffice_engagements` | 657 | `research/boxoffice_engagements/` |
| `canonical_boxoffice_engagements` | 657 | `research/canonical_boxoffice_engagements/` |
| `musicbrainz_event` | 124,404 | `raw/musicbrainz_event/` |
| `musicbrainz_place` | 82,547 | `raw/musicbrainz_place/` |
| `musicbrainz_artists` | 2,205,000 | `reference/musicbrainz_artists/` |

DuckDB can query R2 Parquet directly via httpfs (tested ✅).

## Verification Results

| Test | Result |
|---|---|
| rclone round-trip (small file) | ✅ SHA256 match |
| SHA256 round-trip: `festival_bloomberg.duckdb` | ✅ Match |
| SHA256 round-trip: `ticket_market.duckdb` | ✅ Match |
| Size match: `boxoffice_research_v2.duckdb` | ✅ 5,383,401,472 bytes |
| DuckDB open (recovered file) | ✅ 13 tables |
| R2 Parquet queries | ✅ 96,556 events queried |
| **CLOUD_RECOVERY_FROM_EMPTY_DATA_DIR** | ✅ **PASS** |
| R2ObjectStore put/get/dedup | ✅ All tests pass |
| R2ObjectStore zstd compression | ✅ Working |
| Security: no leaked credentials | ✅ Clean |

## New Code

| File | Purpose |
|---|---|
| `python/festival_bloomberg/evidence_rails/r2_object_store.py` | Content-addressed R2 object store with zstd compression |
| `python/festival_bloomberg/config/r2_storage.py` | R2 configuration and cutover contract |

## Updated Files

| File | Change |
|---|---|
| `.env.example` | Added R2 config variables |
| `requirements.txt` | Added boto3, zstandard |
| `docs/data-artifacts-registry.md` | Added cloud backup section |

## R2 Monthly Storage Estimate

| Bucket | Size | Est. Monthly Cost |
|---|---|---|
| Backups | 5.16 GB | ~$0.08 (Class A: $4.50/T, Class B: $0.36/T) |
| Lake | 576 MB | ~$0.01 |
| Raw | 0 MB | $0.00 |
| **Total** | **5.74 GB** | **~$0.09/month** |

## Remaining Local Authoritative Assets

| Asset | Size | Why it stays |
|---|---|---|
| `boxoffice_research_v2.duckdb` | 5.0 GB | Canonical licensed corpus — app reads directly |
| `artist_market_event_history.duckdb` | 39 MB | Cross-market security master |
| `festival_bloomberg.duckdb` | 16 MB | Canonical reference schema |
| `ticket_market.duckdb` | 36 MB | Active workspace |
| `watch_universe_v1.json` | 89 KB | Frozen config |
| **Total local** | **~5.1 GB** | — |

## Disk Usage

| Metric | Value |
|---|---|
| Disk total | 228 GB |
| Disk used | 193 GB (99%) |
| Free | 3.6 GB |
| Local data | 5.2 GB |
| R2 total | 5.74 GB |

## What's NOT Done (Intentionally)

1. **R2 Data Catalog / Iceberg** — Public beta, not on critical path
2. **5GB boxoffice round-trip verification** — Insufficient local disk; size verified
3. **Local cleanup of 5GB DB** — Requires app to support R2 serving mode first
4. **PR creation** — Cloud infra kept separate from PR #46

## Next Steps

1. Create PR for cloud infrastructure changes
2. Migrate app code to serve from R2 (Phase 8 cutover)
3. Pilot R2 Data Catalog on `ticket_market_snapshots` only
4. Move collectors off Mac entirely
5. Clean up local cache once R2 serving is live
