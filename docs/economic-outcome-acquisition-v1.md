# Economic Outcome Acquisition V1

The Historical Laboratory built the *ledger*. This milestone tried to put
**real economic numbers** into it — attendance, tickets sold, sold-out,
gross, price, event capacity — from legitimately obtainable public sources.

The headline result is deliberately unglamorous: **free public data is not a
box-office feed.** After a genuine, bounded acquisition pass, the corpus has
**1 real attendance label** (Lollapalooza Chicago), **3 sold-out
assertions**, and **0 tickets-sold / gross / guarantee / promoter-contribution
labels**. The model-readiness gate therefore reports `NOT_READY`, and this
document explains exactly why, with the numbers.

## What was built

| Module | Purpose |
| --- | --- |
| `economics/document_ingestion.py` | HTML/wikitext/TXT → evidence + strict semantic extraction |
| `economics/outcome_acquisition.py` | Curated public sources → outcome claims (Wikipedia first) |
| `economics/readiness.py` | Model-readiness gate (no model trained) |
| `acquisition/providers/commoncrawl.py` | WARC capture offset lookup + record retrieval |
| `oa/economic_outcome.py` | Live OA driver + festival event seeding |

## Claim semantics (unchanged, now exercised)

- `PAID_ATTENDANCE` ≠ `SCANNED_ATTENDANCE` ≠ `REPORTED_ATTENDANCE`.
- `expected/planned` attendance is **not** actual attendance (rejected).
- `capacity of N` is capacity, never attendance.
- `N tickets sold` is `TICKETS_SOLD`, never attendance.
- `sold out` is `EXPLICIT_SOLD_OUT_ASSERTION`, never inferred from offsale.
- `grossed $N` is `TICKET_GROSS`, never reconstructed from tickets × price.
- Multi-edition festival body text (e.g. "70,000 attended" for Lollapalooza
  *Stockholm*) is **not** auto-attributed to the Chicago event — the acquirer
  only trusts infobox fields for attendance/capacity.

## Source-quality hierarchy

`A_PRIMARY_GOVERNMENT/PROMOTER/VENUE/SETTLEMENT` → `B_REPUTABLE_*` →
`C_OTHER_PUBLIC_REPORT` → `D_INFERRED/WEAK` → `UNKNOWN`.

The current pass is entirely `C_OTHER_PUBLIC_REPORT` (Wikipedia, an
aggregator). A-tier government/promoter/venue documents remain the top target
and are not yet acquired — that is the honest gap, not a reason to inflate
grades.

## Rights separation

- Setlist.fm → `RESEARCH_ONLY` (non-commercial terms).
- Wikidata → `OPEN_COMMERCIAL_OK` (CC0).
- Wikipedia → `OPEN_WITH_ATTRIBUTION` (CC BY-SA).
- OSM → `OPEN_WITH_ATTRIBUTION` (ODbL).
- Common Crawl → `UNKNOWN` (rights belong to the underlying publisher).

Every report carries a **research corpus vs commercial-eligible corpus**
split. Research-only evidence is never silently promoted to a commercial
feature.

## Live OA result (real public data, $0)

- **99 events searched** (95 concerts + 4 seeded festival editions).
- **155 claims total**; 5 new economic claims this pass.
- Attendance: **1 event** (Lollapalooza Chicago, 400,000 reported aggregate).
- Event capacity: **1 event** (Lollapalooza, 115,000 daily).
- Sold-out: **3 events** (Lollapalooza, Pitchfork, North Coast).
- Tickets sold / gross / guarantee / promoter contribution: **0**.
- Cost: **$0.00**; Monid/Apify: none.

## Common Crawl live result

`lookup_capture_offset` found a real July 2026 capture of
`https://www.lollapalooza.com/` (`CC-MAIN-2026-30`, capture
`20260716141918`, WARC offset `720554185`, length `30811`).
`pitchforkmusicfestival.com` has no capture in that crawl. The WARC retrieval
contract (`fetch_warc_record_bytes`, `extract_warc_payload_text`) is tested
offline; full WARC content download is intentionally deferred to keep the
milestone bounded.

## Model-readiness verdict

**NOT_READY** — 1 attendance label vs a conservative floor of 50, and zero
onsale/announcement cutoffs for the concert corpus. `venue_count=28`,
`year_count=20` show breadth, but breadth without targets is still not
training data.

## Selection bias (unchanged, still material)

United Center (35) and Soldier Field (16) dominate; major-artist bias is
structural. Adding festival editions does not fix this.

## What this proves

The acquisition *machinery* is correct and honest: it extracts with strict
semantics, preserves provenance and rights, and refuses to fabricate. What it
also proves is that **free public sources cannot reach a model-ready corpus**.
The marginal value of a licensed box-office feed, a design-partner settlement
file, or an authorized Eventbrite/customer import is now quantified: it is the
entire remaining gap between 1 attendance label and 50+.

## Next milestone gate

A licensed box-office / settlement feed or authorized customer import is the
only path that meaningfully advances the readiness verdict. That is the next
acquisition milestone — not another scrape pass, and not ML.
