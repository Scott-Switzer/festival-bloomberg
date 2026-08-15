# NATIONAL_COVERAGE_ENTITY_MASTER_AND_FESTIVAL_HISTORY_V1

**Verdict: PARTIAL (the forward-event fabric + cap fix are real; the entity
master resolution scale and festival-history scale remain).**

## What this milestone delivers

The three P0 objectives were: (1) complete the national Ticketmaster
forward-event universe without silent truncation, (2) build the canonical
cross-provider entity master, (3) massively expand the historical
festival/lineup graph. This run delivers (1) fully, (2) as schema + a real
ListenBrainz provider (resolution scale still pending), and (3) not yet.

## 1. Ticketmaster artificial cap removed — recursive date-window subdivision

The prior collector capped every partition at `DEFAULT_PAGE_SIZE=20 ×
MAX_PAGES=5 = 100` records and then labeled the rest "truncated". That was an
internal cap, not Ticketmaster's limit. The provider now:

- pages to the provider's own deep-paging ceiling (`RETRIEVAL_CEILING = 1000`,
  page size 50), and
- a partition whose `reported_total` exceeds the ceiling is `SPLIT` in half and
  each half is re-queried, recursing down to `MAX_SPLIT_DEPTH=4` /
  `MIN_SPLIT_WINDOW_DAYS=7`.

Every leaf partition is `COMPLETE`, `TRUNCATED_BY_CAP`, `RATE_LIMITED`, or an
explicit error — never silently truncated. The pagination metadata now carries
an explicit `truncated` flag (previously it was computed but not surfaced, so
the split logic never fired).

**Authoritative bounded national run (17 US markets, 472 requests):**

| Metric | Value |
| --- | --- |
| partitions attempted | 35 |
| complete leaves | 26 |
| truncated leaves | **0** |
| split (internal) | 9 |
| rate limited | 0 |
| provider errors | 0 |
| events persisted this run | 14,020 |
| distinct US music events | **14,023** (up from 1,692) |
| events with >=2 snapshots | 1,690 (real cross-run repeat observations) |

One snapshot per (event, run): the parent/child re-fetch that the split causes
shares the run's `retrieved_at` and dedupes to a single observation, so the
"2+ observations" metric counts genuine repeat observations, not pagination
mechanics. The prior (buggy) run's 23,022 per-partition snapshots were removed
as local bug artifacts before the clean re-run.

## 2. Canonical entity master (schema foundation)

Migration `026_national_coverage_entity_master_v1` adds:

- a partition-tree manifest (`parent_partition_id`, `depth`, `split_reason`) so
  the recursive subdivision is auditable;
- `core.promoters` (canonical promoter/company spine with explicit-parent
  ownership evidence only);
- `core.entity_resolution_ledger` (append-only `MATCHED`/`AMBIGUOUS`/`UNMATCHED`
  resolution ledger keyed by `(entity_type, entity_key, id_type, id_value)`).

Never auto-merges ambiguous identities.

## 3. ListenBrainz — real no-auth provider (P14)

`acquisition/providers/listenbrainz.py` implements the documented
`GET /1/stats/artist/{artist_mbid}/listeners` endpoint and
`attention/listenbrainz.py` persists two metrics per resolved artist:

- `LISTENBRAINZ_LISTEN_COUNT`   (provider `total_listen_count`)
- `LISTENBRAINZ_LISTENER_COUNT` (top-N listener sample, provenance-tagged)

Semantics: MBID-keyed (an artist without an MBID is skipped, never a zero);
204/404 → `missing`; 429 → `RATE_LIMITED`; labeled
`ATTENTION_CONSUMPTION_SAMPLE` (never local demand). The terminal registry now
reports ListenBrainz as `OPERATIONAL`.

**Live result: `NO_MBID_RESOLVED`** — the warehouse has Spotify external IDs
(118) but zero MusicBrainz IDs, so there are no MBIDs to query yet. The
provider is implemented and fully unit-tested; populating the MBID spine is
the next binding edge (entity-master P5).

## Negative results (not hidden)

- **ListenBrainz live = 0 rows** pending MBID resolution (above).
- **Festival history / MusicBrainz festival-event acquisition (P8–P13) not
  started this run** — the six seed editions remain the corpus.
- **Entity resolution scale (P5–P7)** remains schema-only; no probabilistic
  linkage pass yet.

## Git / CI

| Field | Value |
| --- | --- |
| branch | `feat/national-coverage-entity-master-festival-history-v1` |
| migration | 026 |
| Python | 483 passed, 1 skipped |
| Node | 76/76 |
| typecheck | clean |

## Next binding edge

Resolve MusicBrainz IDs for the ~14k Ticketmaster attractions + box-office
artists (deterministic exact match first, then candidate ranking), then
ListenBrainz becomes live and the entity master moves from schema to real
linkage. After that, scale festival history via MusicBrainz event/series
relationships.
