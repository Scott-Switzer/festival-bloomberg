# HISTORICAL_DECISION_EVIDENCE_ENGINE_V1

The prior milestone proved the binding constraint is **pre-event knowability**:
historical `ANNOUNCEMENT` / `PRESALE` / `GENERAL_ONSALE` / `BOOKING_OR_OFFER`
coverage is 0, and the result-publication evidence is a retrospective batch.
The system was good at *storing* evidence once found, but not intelligent
about *finding* the missing evidence.

This milestone inserts the missing layer: an **autonomous evidence-research
engine** that knows which missing fact is worth acquiring next, searches
intelligently for it, and proves where it came from.

## Core principle

> **An LLM does not decide truth. An LLM proposes candidate claims.
> Deterministic code decides admissibility.**

Structured extraction always precedes an LLM; the LLM is only an ambiguity
resolver for pages that survive deterministic passes; and every claim — from
any extractor — carries an exact source document id and evidence span.

## What was built (migration 021)

- **`flywheel.evidence_documents`** — immutable, content-addressed document
  store. Content hash + crawl/publication metadata + rights, never rewritten.
- **`flywheel.evidence_claims`** — claim support graph. Every candidate claim
  points at the exact document + character span + extractor that produced it.
  `verification_status` (ACCEPTED / REJECTED) is set by deterministic code.
- **`flywheel/acquisition_priority.py`** — warm-start dependency graph +
  value-of-information priority. A documented lexicographic ordering
  (`unlock_count desc, repeat_frequency desc, has_known_outcome desc,
  source_path_count desc, event_date asc`), never an opaque score.
- **`flywheel/evidence_extraction.py`** — deterministic extractors (PASS 1:
  JSON-LD / Schema.org Event, OpenGraph; PASS 2: temporal date-language),
  each returning candidates with evidence spans.
- **`flywheel/evidence_verification.py`** — deterministic admissibility gate:
  wrong identity, announcement-as-booking, archive-as-publication, relative
  date without anchor, midpoint-of-interval, rights failure — all rejected.
- **`flywheel/deepseek_extractor.py`** — strict JSON-schema tool contract for
  the DeepSeek V4 Pro analyst. Candidate-claims-only, public material only,
  NOT_CONFIGURED without a key (never fabricated).

## Authoritative OA: `hdee_20260815T125259`

Deterministic core run against the real warehouse (no HTTP, no model tokens,
$0).

### Acquisition priority graph (real)

| Metric | Value |
| --- | ---: |
| Single-show targets | 357 |
| Warm-start-locked (>=3 potential priors, 0 known) | **126** |
| Targets with ALL decision cutoffs | 0 |

Top-ranked acquisition targets (value-of-information ordering):

| Rank | Artist | Date | Downstream | Repeat |
| ---: | --- | --- | ---: | ---: |
| 1 | Guns N' Roses | 2025-05-01 | 26 | 27 |
| 2–7 | Guns N' Roses (tour) | 2025-05-* | 25→20 | 27 |
| 8 | Future & Metro Boomin | 2024-07-30 | 20 | 21 |
| 9–15 | Guns N' Roses / Future & Metro Boomin | … | 19→16 | … |

Repeated artists with the most downstream leverage rank first — the priority
graph already beats uniform crawling because it knows that recovering the
earliest Guns N' Roses 2025 cutoff unlocks 26 downstream same-artist targets.

### Deterministic extractor + verifier

- JSON-LD / OpenGraph / date-language extractors return candidates with spans;
  "on sale now" is an upper bound (never an exact onsale); explicit
  "tickets go on sale March 3" is a DAY-granularity observation.
- DeepSeek extractor: **NOT_CONFIGURED** (no key) — zero requests, honest.

## Milestone verdict: **HISTORICAL_DECISION_EVIDENCE_ENGINE_V1 = PARTIAL**

The intelligent acquisition architecture (priority graph + immutable document
store + claim support graph + deterministic verifier + LLM contract) is
operational and measured. But the advance bar is not met:

- no historical decision cutoff moved UNKNOWN → observed/bounded (the
  deterministic-only run cannot fetch announcement/onsale pages without a
  keyed Ticketmaster source or a candidate-URL discovery channel);
- warm-start remains 0 because the result-publication evidence is still a
  single retrospective batch.

The binding edge is now precisely named: **the priority graph says acquire
Guns N' Roses 2025 tour announcement/onsale pages first; what is missing is a
keyed Ticketmaster probe or a candidate-URL archive source to actually fetch
them.** DeepSeek (or the deterministic extractors) can then turn those pages
into auditable claims.

## Next

1. Configure a Ticketmaster key (5,000/day, treat 2 req/s as the safe default)
   and probe the ranked events' structured onsale/presale/promoter fields.
2. Use Common Crawl's URL Index (not CDX) for bulk host/date discovery of the
   ranked artists' official announcement pages.
3. Enable the DeepSeek extractor for the residual ambiguous pages, with the
   deterministic verifier as the only admissibility gate.
4. Recompute the warm-start dependency graph after every accepted cutoff.
