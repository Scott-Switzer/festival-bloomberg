# Festival Intelligence Terminal MVP V1

The first **information product** layer. Not a model, not a forecast — a
read-only, source-backed intelligence terminal over the canonical warehouse.

The product answers, for `ARTIST / EVENT / VENUE / MARKET / FESTIVAL`:

> What exists? What happened historically? What is upcoming? What just
> changed? What are the sources? What do we know? What is still unknown?

It deliberately does **not** answer "should we book?", "what guarantee?", or
"what will attendance be?" — those remain future validated research questions.

## What was built (migration 022)

- **Activity tape** (`terminal.activity_tape`) — the append-only "what
  changed" ledger. Every meaningful transition becomes one row
  (`EVENT_DISCOVERED`, `OUTCOME_PUBLISHED`, `EVENT_ANNOUNCEMENT_OBSERVED`,
  `PRICE_CHANGED`, `EVENT_STATUS_CHANGED`, …). UNCHANGED polls never produce a
  row; re-derivation is idempotent via a stable `dedupe_key`; rows are
  append-only and never rewritten.
- **Provider health** (`terminal.provider_health`) — operational freshness for
  the DATA page, joined to `flywheel.source_registry` rights/commercial status.
- **News-mention store** (`terminal.news_mentions`) — metadata-only GDELT tape
  (URL/domain/title/timestamp). Full article text is never persisted.
- **Read models** (`intelligence/readmodels.py`) — search + artist/event/venue/
  market/festival intelligence + tape + sources, all read-only over the
  warehouse, with source/evidence lineage on every empirical field.
- **ASK** (`intelligence/ask.py`) — a grounded, read-only Q&A layer. The tool
  surface is a closed set of read-model calls (no SQL primitive, no write
  primitive); factual answers carry their evidence rows. DeepSeek composes
  answers from tool results only when configured, and never invents a value.
- **New provider scaffolds** (`intelligence/providers.py`) — ListenBrainz,
  GDELT, NWS, Census ACS, JamBase (optional/bounded), Ticketmaster. All
  fail-closed: without a key they report `NOT_CONFIGURED` and make zero calls.
- **Terminal server** (`terminal/server.py`) — a stdlib, read-only HTTP layer
  exposing `/api/*` and the static SPA in `apps/terminal/static`.

## The semantic guardrails preserved

- `UNKNOWN != 0`; absent facts render as "no data", never a fabricated zero.
- Attention (pageviews/listens/views) is an attention sample, never local
  ticket demand. News mentions are metadata, never sentiment/demand.
- Venue capacity is returned as a **list of claims**, never collapsed to one
  exact number.
- A provider without a key makes zero requests; the terminal read path never
  breaks when an optional provider (JamBase, Census, Ticketmaster) is absent.
- ASK cannot execute arbitrary SQL, cannot persist evidence, and cannot create
  attendance/price/capacity/booking facts that are not already stored.

## Running it

```bash
# derive the tape + provider health from the persisted warehouse
PYTHONPATH=python python3 -m festival_bloomberg.oa.intelligence_terminal

# start the terminal (read-only server + SPA)
PYTHONPATH=python python3 -m festival_bloomberg.terminal.server --port 8931
```

Then open `http://127.0.0.1:8931`.

## Live operational acceptance (authoritative run `terminal_20260815T132649`)

| Measure | Value |
| --- | ---: |
| Activity tape rows derived (from real warehouse) | **2,162** |
| … EVENT_DISCOVERED (forward events) | 562 |
| … OUTCOME_PUBLISHED (PIT result evidence) | 474 |
| … EVENT_ANNOUNCEMENT_OBSERVED (forward bounds) | 1,124 |
| … EVENT_STATUS_CHANGED | 2 |
| Historical engagements | 657 |
| Distinct artists / venues / markets | 356 / 384 / 261 |
| Forward events under surveillance | 562 |
| Forward observations / events with 2+ observations | 15 / 2 |
| Outcome claims / cutoff evidence / PIT evidence | 1,716 / 1,955 / 474 |
| Festivals (canonical corpus) | 0 (honest gap) |

## Milestone verdict: **PARTIAL**

The information product is real and runs end-to-end on genuine warehouse data
(search → artist → boxoffice history → event → venue → market → tape →
sources → ASK). But the live-data expansion is gated on provider keys:
Ticketmaster / Census / JamBase / YouTube are `NOT_CONFIGURED` in this run, and
there is no canonical festival corpus. The binding next step is **keyed
ingestion** (Ticketmaster Discovery DMA-partitioned US music, ListenBrainz
attention, GDELT news tape, NWS weather), which will materially deepen the
same read models without any product-layer change.
