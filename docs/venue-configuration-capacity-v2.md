# Venue Configuration & Capacity Evidence V2

## Verdict

```
VENUE_CONFIGURATION_CAPACITY_V2 = PASS
```

V1 had **0 configuration-specific** claims and **0 workbench-safe prefills**.
V2 delivered **10 venues** with configuration-specific capacity evidence and
**8 workbench-safe venue-configuration prefills** — a material, real
improvement from free/open sources (Wikipedia + Wikidata). OSM contributed 0
capacity claims in this bounded pass but was exercised.

This is a genuine advance over V1's `MAX_PERSONS`-only result.

**Correction vs the initial V2 report:** the earlier claim of *9* safe
prefills included Madison Square Garden CONCERT 22,000 as safe. Under the
production reconciliation contract (below) MSG is **review-required**:
CONCERT 22,000 exceeds the venue's claimed MAX_PERSONS 20,789, which is a
`CROSS_KIND_CONTRADICTION` and blocks automatic prefill. The corrected safe
set is the 8 SEATED venues listed in the acceptance table.

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
| Blocked claims (conflicts + contradictions) | 5 | **20** |
| Workbench-safe prefill | 0 | **8** |
| Wikipedia claims | 0 | **28** |
| Wikidata claims | 9 | 24 |

## Real acceptance set

| Venue | Source | Kind | Value | Workbench-safe |
|---|---|---|---|---|
| United Center | Wikidata | MAX_PERSONS | 23,500 | Upper bound only |
| Madison Square Garden | Wikipedia `ubl` | CONCERT | 22,000 | Review required (`CROSS_KIND_CONTRADICTION`) |
| Madison Square Garden | Wikipedia `ubl` | SPORTS | 18,006/19,812/20,789 | separate claims |
| Hollywood Bowl | Wikipedia | **SEATED** | **17,500** | ✓ safe (SEATED) |
| Mercury Lounge | Wikipedia | **SEATED** | **250** | ✓ safe (SEATED) |
| Red Rocks Amphitheatre | Wikidata+Wiki | MAX_PERSONS | 9,525 | Upper bound only |
| The Roxy | — | — | — | None |
| Ryman Auditorium | — | — | — | None |
| Blue Note Jazz Club | — | — | — | None |
| 9:30 Club | Wikidata | MAX_PERSONS | 1,200 | Upper bound only |
| Grant Park / Zilker / Empire Polo | — | — | — | None |

The outmost value: Hollywood Bowl + Mercury Lounge + the other SEATED venues
now let a buyer apply a **configuration-compatible** capacity that is a real
`OBSERVED_PUBLIC` claim rather than a bare `USER_ASSUMPTION`.

## One semantic reconciliation / prefill contract

The V1-style `mark_conflicts` treated *any* two differing `(value, kind)`
pairs as a conflict, which collapsed legitimate configurations (SEATED 17,500
+ CONCERT 18,000), collapsed SPORTS subtypes, and never caught a
configuration value that contradicts a claimed maximum. V2 replaces it with
one deterministic contract — `assess_venue_claims` — used identically by the
production workbench `capacity_prefill`, acquisition acceptance/reporting
(`repo.reconcile_capacity_claims`) and tests:

1. Same configuration + same value from different sources -> `CORROBORATED`
   (rows stay separate; never collapsed).
2. Same configuration + different values -> `SAME_CONFIGURATION_CONFLICT` /
   review required; no automatic prefill.
3. Different explicit configurations (SEATED vs CONCERT) -> **no** conflict
   solely because values differ.
4. `MAX_PERSONS` remains upper-bound evidence only; never usable/sellable.
5. Explicit configuration value above a MAX_PERSONS claim ->
   `CROSS_KIND_CONTRADICTION` on both claims; automatic prefill blocked.
6. SPORTS subtypes (basketball, hockey, boxing, ...) are assessed by a
   normalized subtype key, never collapsed by the broad kind.

Reconciliation mutates only `claim_status`; raw source claims are never
overwritten or deleted, and the report is regenerated from the persisted
state (idempotent). The acquisition script's duplicated safe-prefill logic
was removed — there is no second implementation.

### Real result over the persisted estate (52 claims, 28 venues)

- **8 workbench-safe venue-configuration pairs** (all SEATED): Hollywood Bowl
  17,500; Mercury Lounge 250; Orpheum Theatre 2,672; Empty Bottle 400;
  David Geffen Hall 2,200; The Van Buren 1,800; Bowery Ballroom 575;
  The Fillmore 1,315.
- **MSG under generic rules**: CONCERT 22,000 -> `CROSS_KIND_CONTRADICTION`
  (contradicted max 20,789); its 6 Wikidata MAX claims are internally
  conflicting and (being below CONCERT 22,000) contradicted; SPORTS claims
  share one `{{ubl}}` description string so their subtypes are not
  structurally distinguishable in the current parse — the raw text preserves
  them (`[[Basketball]]: 19,812` etc.), but the structured rows collapse to a
  same-configuration conflict. MSG = **review required**, nothing prefilled.
- 6 same-configuration conflict groups (MSG SPORTS, Ball Arena MAX, CFG Bank
  SPORTS, Auditorio Nacional MAX, Stage AE MAX) + 10 blocked claims;
  10 cross-kind contradiction claims; 12 corroborated MAX claims
  (e.g. Red Rocks 9,525 x2 sources).
- 14 upper-bound-only venues (United Center, Chase Center, ...); 0 unknown.

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

**Yes for Wikipedia + Wikidata.** We now have 8 workbench-safe
configuration-specific capacity pairs from real infoboxes (Hollywood Bowl
seated 17,500, Mercury Lounge 250, and six more SEATED venues). MSG concert
22,000 is real evidence but is correctly **not** prefilled because it
contradicts the venue's claimed maximum — that is the contract working, not a
missed claim. OSM contributed nothing in this bounded pass. Public structured data is **not** the full
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
python/festival_bloomberg/economics/capacity.py             (raw_value + parser_version; semantic mark_conflicts + assess_venue_claims contract)
python/festival_bloomberg/economics/repository.py           (persist new columns; reconcile_capacity_claims)
python/festival_bloomberg/economics/show_economics_product.py (capacity_prefill delegates to the shared contract)
schema/migrations/035_venue_capacity_claim_metadata_v1.sql  (index-safe ADD COLUMN)
requirements.txt                                            (mwparserfromhell)
tests/python/test_venue_capacity_v2.py                      (33 regression tests incl. semantic rules A-J + one-contract test G)
scripts/venue_capacity_v2_acquisition.py                    (bounded V2 acquisition; reports via shared contract)
docs/venue-configuration-capacity-v2.md                     (this file)
docs/project_manifest.yaml                                  (milestone status)
reports/venue_capacity_v2_report.json                       (machine-readable)
```