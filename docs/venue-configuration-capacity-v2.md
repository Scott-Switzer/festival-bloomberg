# Venue Configuration & Capacity Evidence V2

## Verdict

```
VENUE_CONFIGURATION_CAPACITY_V2 = PASS
```

V1 had **0 configuration-specific** claims and **0 workbench-safe prefills**.
V2 delivered **10 venues** with configuration-specific capacity evidence and
**9 workbench-safe prefills** — a material, real improvement from free/open
sources (Wikipedia + Wikidata). OSM contributed 0 capacity claims in this
bounded pass but was exercised.

This is a genuine advance over V1's `MAX_PERSONS`-only result.

---

## Where the information came from

The central defect in V1 was **Wikipedia page resolution** via fuzzy search.
V2 replaces that with **ID-based resolution**:

```
canonical venue
  → Wikidata search/batch wbgetentities (P1083 claims + enwiki sitelink)
  → enwiki sitelink = EXACT Wikipedia page title
  → MediaWiki Action API wikitext
  → mwparserfromhell infobox parser
  → capacity evidence (with raw_value + parser_version preserved)
```

This happy path (QID → exact sitelink → exact page → wikitext → parser) is
what unlocked configuration-specific evidence. Search is used only as a
fallback for venues without a resolved QID.

## Parser

`earwig/mwparserfromhell` (MIT) is the real MediaWiki template parser. It is
used to read venue infoboxes, extracting `capacity`, `seating_capacity`,
`concert_capacity`, `hockey_capacity`, and list-valued `{{ubl|...}}`
configuration matrices.

Conservative semantics (matching the non-negotiable rules):

- `capacity = 20,000` (no config) stays `MAX_PERSONS` / upper bound
- `seating_capacity = 20,917` maps to `SEATED`
- `23,500 (concert)` / `18,006 (hockey)` → configuration claim from the text
- `{{ubl|Basketball: 19,812|Concert: 22,000|...}}` → per-row config claims
- `2,000–2,500` (range) → no numeric claim (raw preserved)
- `18,000<br>21,032` → **never concatenated** (a real parser defect was found
  and fixed — see below)
- No non-capacity numeric field (e.g. `year_built`) is ever parsed

## Schema / migration fix (found a real defect)

Adding `raw_value` and `parser_version` columns to
`economics.venue_capacity_claims` invalidates DuckDB's existing
`idx_econ_capacity_venue` ART index (a real DuckDB limitation). This caused
`DELETE` to fail with `FATAL: Failed to delete all rows from index`.

**Fix (migration 035):** drop the index before the `ALTER`, add the columns,
recreate the index. This keeps deletes working on both fresh (CI) and
existing databases. A live-database reconciliation removed one fabricated
claim produced by an earlier parser defect (see below).

## Coverage (frozen 100-venue universe; persisted state)

| Metric | V1 | V2 |
|---|---|---|
| Total claims | 9 | **52** |
| Venues with any claim | 5 | **28** |
| MAX_PERSONS claims | 9 | 37 |
| SEATED claims | 0 | **8** (8 venues) |
| CONCERT claims | 0 | **1** |
| SPORTS claims | 0 | **6** (2 venues) |
| Configuration-specific venues | 0 | **10** |
| Conflicting claims (coexist) | 5 | 5 |
| Workbench-safe prefill | 0 | **9** |
| Wikipedia claims | 0 | **28** |
| Wikidata claims | 9 | 24 |

## Real acceptance set

| Venue | Source | Kind | Value | Workbench-safe |
|---|---|---|---|---|
| United Center | Wikidata | MAX_PERSONS | 23,500 | Upper bound only |
| Madison Square Garden | Wikipedia `ubl` | **CONCERT** | **22,000** | ✓ safe (CONCERT) |
| Madison Square Garden | Wikipedia `ubl` | SPORTS | 18,006/19,812/20,789 | separate claims |
| Hollywood Bowl | Wikipedia | **SEATED** | **17,500** | ✓ safe (SEATED) |
| Mercury Lounge | Wikipedia | **SEATED** | **250** | ✓ safe (SEATED) |
| Red Rocks Amphitheatre | Wikidata+Wiki | MAX_PERSONS | 9,525 | Upper bound only |
| The Roxy | — | — | — | None |
| Ryman Auditorium | — | — | — | None |
| Blue Note Jazz Club | — | — | — | None |
| 9:30 Club | Wikidata | MAX_PERSONS | 1,200 | Upper bound only |
| Grant Park / Zilker / Empire Polo | — | — | — | None |

The outmost value of the ASG example: MSG + Hollywood Bowl + Mercury Lounge
now let a buyer apply a **configuration-compatible** capacity that is a real
`OBSERVED_PUBLIC` claim rather than a bare `USER_ASSUMPTION`.

## Defects found and fixed during this milestone

1. **BR-concatenation**: `18,000<br>21,032 (with floor seats)` was parsed as
   `1,800,021,032`. Fixed in `_parse_number` — a `<br>`/newline/semicolon
   separating two comma-grouped figures is now detected as `UNPARSEABLE`
   (no invented number). Regression test added.
2. **DuckDB index invalidation from ADD COLUMN**: deletes on
   `venue_capacity_claims` failed after the migration. Fixed by dropping and
   recreating the index around the ALTER in migration 035.
3. **Wikipedia exact-page resolution**: the web route is now ID/sitelink
   based, not fuzzy phrase search.

## Source request/runtime statistics

The runtime partial claims report (from the bounded passes) shows Wikipedia
page fetch succeeded for every sitelink attempted, while OSM Overpass was
rate-limited / timing out (53 failures vs 5 successes at 1.2s delay) and
contributed no capacity claims. This matches the known public nature of the
three sources:

- **Wikipedia**: highest value (infoboxes encode configuration matrices)
- **Wikidata P1083**: good for major arenas, sparse for music venues
- **OSM**: capacity tags rarely present; exposed but no claims won

## The central question answered

> Did Wikipedia/Wikidata/OSM materially reduce the configuration-capacity
> UNKNOWN problem?

**Yes for Wikipedia + Wikidata.** We now have 9 workbench-safe
configuration-specific capacity claims from real infoboxes (e.g. MSG concert
22,000, Hollywood Bowl seated 17,500, Mercury Lounge 250). OSM contributed
nothing in this bounded pass. Public structured data is **not** the full
answer — the majority of the frozen universe still resolves only to an upper
bound (`MAX_PERSONS`) or nothing. But for the highest-value arena/theatre
venues it substantially reduces `USER_ASSUMPTION`.

Configuration-specific coverage is materially better than V1. OSM remains
low-yield for capacity. The next-probable path for the remaining gap is
official venue/tech-pack evidence and promoter/design-partner data.

## After V2 — recommended next milestones (not implemented in this PR)

1. `MARKET_COMPETITIVE_CALENDAR_V1` — Ticketmaster collector across Music +
   Sports + Arts/Theatre + Family + Film, explicit geo/time overlap.
2. `MARKET_FUNDAMENTALS_V1` — Census + BEA + BLS primary government data.
3. `HISTORICAL_WEB_EVIDENCE_V1` — Common Crawl URL Index / Parquet + DuckDB.
4. Design-partner / ticket-pace / settled-outcome activation.

Do not resume advanced ML until economically meaningful outcome density
improves.

## Files changed

```
python/festival_bloomberg/economics/wikipedia_capacity.py   (new — mwparserfromhell parser)
python/festival_bloomberg/economics/capacity.py             (raw_value + parser_version on claims)
python/festival_bloomberg/economics/repository.py           (persist new columns)
schema/migrations/035_venue_capacity_claim_metadata_v1.sql  (index-safe ADD COLUMN)
requirements.txt                                            (mwparserfromhell)
tests/python/test_venue_capacity_v2.py                      (20 regression tests)
scripts/venue_capacity_v2_acquisition.py                    (bounded V2 acquisition)
docs/venue-configuration-capacity-v2.md                     (this file)
docs/project_manifest.yaml                                  (milestone status)
reports/venue_capacity_v2_report.json                       (machine-readable)
```