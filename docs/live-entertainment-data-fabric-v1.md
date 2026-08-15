# LIVE_ENTERTAINMENT_DATA_FABRIC_V1

Turn the working ingestion proof into a denser, cross-linked information
estate. The metric is **new correct queryable information**, not request count.

## Verdict

**PARTIAL** — Wikimedia attention, the extended Ticketmaster sweep, the
partition manifest, the news-mention store, and the NEWS/ATTN terminal views
are real and verified. GDELT is honestly `RATE_LIMITED` (the live API is
currently throttling at >=1 req/5s and refused every bounded probe), so the
news tape remains empty until the throttle clears. The Ticketmaster national
sweep is real but the partition manifest honestly reports **0 complete
non-truncated partitions** — the deep-paging cap (100 records / 5 pages per
market partition) means large markets need date-window subdivision, which is
the next binding edge.

## What was built

| Layer | Result |
| --- | --- |
| GDELT provider (`acquisition/providers/gdelt.py`) | Key-free DOC 2.0 artlist; metadata-only (`content_role=news_metadata`, no article body); 429 → `RATE_LIMITED`; 5s minimum request spacing honored |
| Wikimedia pageviews (`attention/wikimedia_pageviews.py`) | Key-free per-article pageviews persisted into `metrics.artist_attention_observations`; 404 → `missing` (never zero); idempotent observation keys |
| Partition manifest (migration 025) | `terminal.acquisition_partitions` records per-partition `total_expected` / received / `truncated` / status so completeness is never faked |
| News-mention tape | `derive_news_tape_entries` emits `NEWS_MENTION` rows from `terminal.news_mentions`; append-only, idempotent |
| Terminal | NEW **NEWS** and **ATTN** views + `/api/news` + `/api/attention`; fixed the broken `get_attention_series` read model (was reading non-existent columns) |

## Live OA (authoritative run `data_fabric_20260815T155515`)

| Provider | Outcome |
| --- | --- |
| Wikimedia | **30/30** artist pageviews acquired, 30 rows persisted (Ariana Grande 814,128; Billy Joel 200,218; …) |
| Ticketmaster | **17 US market partitions**, 85 requests, **1,691 events** persisted (1,692 distinct), 0 rate-limited |
| GDELT | `RATE_LIMITED_STOPPED` — 1 attempt, 429 (honest; no fabricated success) |

**Ticketmaster partition honesty:** every market except Miami exceeds the
100-record/5-page retrieval cap (Las Vegas 2,578; New York 2,044; Chicago
1,142 future music events …). All 16 large partitions are recorded as
`truncated = TRUE` rather than silently "complete". This proves the
date-window subdivision requirement the milestone called out.

**Activity tape:** 8,762 total rows after the run (+1,835 event-derived rows
in this milestone).

## Semantics preserved

- UNKNOWN is never encoded as zero (missing pageviews → `missing`, not `0`).
- Archive/retrieval time is never collapsed into publication time (news
  mentions keep `publication_time` separate from `retrieved_at`).
- No article text is stored or redistributed (GDELT is metadata-only).
- Provider failures are explicit statuses, never empty success.
- Pageviews are an attention channel, never labeled demand.

## Tests

- Python: **473 passed, 1 skipped** (8 new `test_data_fabric.py` regressions).
- Node: **76/76**. Typecheck: clean. Gitleaks: clean.

## Next binding edge

Date-window subdivision of Ticketmaster market partitions (so large markets
stop truncating at the 100-record cap), then retry GDELT after its throttle
clears. ListenBrainz remains the honest `NOT_IMPLEMENTED` scaffold.
