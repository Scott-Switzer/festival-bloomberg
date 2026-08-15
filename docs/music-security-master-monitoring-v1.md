# MUSIC_SECURITY_MASTER_AND_MONITORING_V1

**Verdict: PARTIAL (the identity architecture + bulk MusicBrainz series spine
are real; MBID resolution for the 14k event universe, catalog objects, and
the monitoring layer remain).**

## What changed

### 1. Music entity master (migration 027)

Extends the SAME canonical graph (no parallel identity system) with the
object families a music security master needs:

- **CATALOG** — `core.release_groups`, `core.releases`, `core.recordings`,
  `core.works` (recording ≠ work; ISRC lives on recordings, ISWC on works).
- **LIVE** — `core.event_series` (TOUR | FESTIVAL | RESIDENCY | RUN |
  EVENT_SERIES; a series ≠ an event).
- **INDUSTRY** — `core.labels`, `core.companies` (companies distinct from
  labels and from promoters; CIK/ticker for SEC linkage).
- **GRAPH** — `core.entity_relationships` (typed, source-backed,
  knowledge-timed edges).
- **ID MASTER** — `core.entity_external_ids` gains `namespace`,
  `resolution_status`, `resolution_method`, `first_seen_at`, `last_seen_at`,
  `knowledge_time`. External IDs are MAPPINGS, never primary keys.
- **RAW** — `raw.musicbrainz_dump_source` + `raw.musicbrainz_series` for CC0
  bulk-dump lineage.

### 2. MusicBrainz bulk ingest (no API hammering)

The public web service is ~1 req/sec, so bulk identity comes from the CC0
database dumps. `python/festival_bloomberg/musicbrainz/dumps.py` downloads a
`.tar.xz` dump, verifies its SHA-256, and streams the NDJSON into the
warehouse with full source lineage (snapshot date, URL, compressed size,
checksum, CC0). Dump files live in a gitignored `data/musicbrainz_dumps/`
directory and are never committed.

### 3. Live ingest of the series dump (festival/tour spine)

Authoritative bounded run against snapshot `20260815-001001`
(`series.tar.xz`, 31.8 MB, SHA-256 `de881fa5…`):

| Result | Count |
| --- | --- |
| series parsed & persisted (raw) | 37,283 |
| **canonical event series** | **6,228** |
| ├ FESTIVAL | 1,919 |
| ├ TOUR | 2,875 |
| ├ RESIDENCY | 47 |
| ├ RUN | 257 |
| └ EVENT_SERIES | 1,130 |

Non-event series (release-group/release/recording/work/label/award/podcast
series, ~31k) are preserved in `raw.musicbrainz_series` with their exact
MusicBrainz type and do **not** pollute `core.event_series`.

## Negative results (not hidden)

- **MBID resolution for the 14k Ticketmaster attractions not started** — the
  artist dump is ~1.7 GB and was deliberately deferred; the downloader is
  in place but the artist ingest + resolution pass is the next step.
- **Catalog objects (release-group/release/recording/work) are schema-only.**
- **Monitoring (watchlists/saved views/alerts/TODAY) not built yet.**

## Tests / CI

- Python **497 passed, 1 skipped** (14 new music-security-master regressions)
- Node 76/76, typecheck clean, gitleaks clean.

## Next binding edge

Ingest the MusicBrainz `artist` dump (~1.7 GB, CC0) and resolve the ~14k
Ticketmaster attractions + box-office artists to MBIDs locally; that turns
ListenBrainz live and makes the entity master a real linkage graph rather
than schema. After that: event + place dumps, then monitors/alerts.
