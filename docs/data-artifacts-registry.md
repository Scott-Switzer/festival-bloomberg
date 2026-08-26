# Data Artifacts Registry

Tracks the large (multi-GB) local data artifacts under `data/`, their
provenance, and how to get them back if they are ever removed. Kept up to
date because these files are git-ignored build artifacts — a full disk can
force their deletion, and they must remain recoverable.

## Registry

| Path | Size | Kind | Rebuildable? | How to regenerate / redownload | Status |
| --- | --- | --- | --- | --- | --- |
| `data/warehouse/boxoffice_research_v2.duckdb` | ~5.0 GB | canonical research warehouse (boxscore corpus + canonical entities) | Yes — rebuilt by acquisition/OA drivers (`oa/boxscore_v2.py`, `oa/baseline_research.py`, `scripts/comparable_v2_closure.py`); depends on licensed boxscore sources (Billboard / Touring Data) | re-run acquisition pipeline; **not** freely re-downloadable (licensed source data) | KEEP |
| `data/serving/terminal_prod_20260819_231500_UTC.duckdb` | ~4.8 GB | terminal serving snapshot (published 2026-08-19 23:15 UTC) | Yes | `PYTHONPATH=python .venv/bin/python -m festival_bloomberg.cli.main terminal publish-snapshot --canonical data/warehouse/boxoffice_research_v2.duckdb` (see `docs/terminal-serving-snapshot-v1.md`); a fresh snapshot gets a new `terminal_prod_<UTC>.duckdb` name — update `SERVING_DB` in `scripts/freeze_watch_universe.py`, `scripts/overlap_analysis.py`, `scripts/revalidate_lift.py` | DELETED 2026-08-25 (approved; disk full) |
| `data/musicbrainz_dumps/*.tar.xz` + `mbdump/` | ~2.2 GB | MusicBrainz JSON dumps (artist, place, event, area, series) | Yes — freely re-downloadable | `https://ftp.musicbrainz.org/pub/musicbrainz/data/json-dumps/` (see `python/festival_bloomberg/musicbrainz/dumps.py`, `JSON_DUMPS_INDEX`); already ingested into `raw.musicbrainz_*` / `reference.musicbrainz_artists` tables in `data/warehouse/boxoffice_research_v2.duckdb` and `data/warehouse/artist_market_event_history.duckdb` | DELETED 2026-08-25 (approved; re-downloadable + already ingested) |

## Cloud Backup (R2)

As of 2026-08-26, all irreplaceable data is backed up to Cloudflare R2:

| Bucket | Contents |
| --- | --- |
| `festival-intelligence-backups` | Canonical DuckDB backups, manifests, checksums |
| `festival-intelligence-raw` | Content-addressed raw evidence (zstd-compressed) |
| `festival-intelligence-lake` | Parquet tables queryable via DuckDB httpfs |

**Backup timestamp:** `2026-08-26T01-00-58Z`

Key objects:
```
backups/canonical/2026-08-26T01-00-58Z/
  boxoffice_research_v2.duckdb      (5.0 GB, SHA256: 08ac97d0...)
  boxoffice_research.duckdb          (8 MB)
  artist_market_event_history.duckdb (39 MB)
  festival_bloomberg.duckdb          (16 MB)
  youtube_fan_signal_oa.duckdb       (26 MB)
  youtube_fan_signal_v1.duckdb       (10 MB)
  design_partner_retrospective_oa.duckdb (13 MB)
  ticket_market.duckdb              (36 MB)
  watch_universe_v1.json            (89 KB)
  ... (26 total objects, 5.16 GB)

backups/manifests/migration_manifest.json
```

**Recovery command:**
```bash
rclone copy r2:festival-intelligence-backups/canonical/2026-08-26T01-00-58Z/<file> ./
```

See `python/festival_bloomberg/evidence_rails/r2_object_store.py` for programmatic R2 access.

## Notes

- `data/warehouse/boxoffice_research_v2.duckdb` is the **canonical** research
  warehouse. It is deliberately NOT deleted when disk pressure hits: it is the
  ingested licensed corpus itself (not a redundant copy) and cannot be freely
  re-downloaded. If it is ever removed, boxscore coverage is lost unless the
  licensed acquisition sources are re-run.
- All deleted entries above were git-ignored (confirmed with
  `git check-ignore`), so their removal does not affect the repository diff.
- The frozen 100-event watch universe (`data/workspace/watch_universe_v1.json`)
  is NOT affected by the serving-snapshot deletion — it is persisted JSON.
- R2 credentials are stored in `.env` (git-ignored) and `~/.config/rclone/rclone.conf`.
  Never commit them.
