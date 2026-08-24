# Venue Configuration & Capacity Evidence V1

## Verdict

```
VENUE_CONFIGURATION_AND_CAPACITY_EVIDENCE_V1 = PARTIAL
```

Real claims acquired from Wikidata, but all are `MAX_PERSONS` (upper bound
only). No configuration-specific (seated/standing/concert) capacity was found.
Workbench-safe auto-prefill = 0.

This is an honest negative result, not an engineering failure.

---

## Audited architecture

The existing capacity architecture (`python/festival_bloomberg/economics/capacity.py`,
`enrichment.py`, `repository.py`) is adequate for this problem:

- `CapacityClaim` dataclass with claim_id, canonical_venue_id, capacity_value,
  capacity_kind, configuration_description, provider, source, source_url,
  knowledge_time, claim_status, usage_label
- `EconomicsRepository.insert_capacity_claim` with idempotent dedup
- `EconomicsRepository.upsert_venue_mapping` for venue-source resolution links
- Providers: WikipediaProvider, WikidataProvider, OpenStreetMapProvider
- Claim construction: `claim_from_wikidata`, `claim_from_wikipedia_infobox`,
  `claims_from_osm`
- Conflict handling: `mark_conflicts` (never averages)
- Applicable capacity selection: `select_applicable_capacity`
  (configuration-compatible only; MAX_PERSONS flagged as UPPER_BOUND)

### Key finding

The enrichment pipeline was designed but never executed against the general
venue population. `economics.venue_capacity_claims` and
`economics.venue_source_ids` were both 0 rows before this milestone.

---

## Capacity semantics

| Kind | Meaning | Found in this milestone |
|---|---|---|
| `MAX_PERSONS` | Maximum building capacity (any configuration) | YES — all 9 claims |
| `SEATED` | Seated-event capacity | NO |
| `STANDING` | Standing/GA event capacity | NO |
| `CONCERT` | Concert-specific capacity | NO |
| `SPORTS` | Sports-specific capacity | NO |

`MAX_PERSONS` is correctly labeled `MAXIMUM_CAPACITY_UPPER_BOUND` and never
auto-filled into the workbench's `usable_capacity` / `sellable_capacity` fields.

Venue maximum ≠ event sellable capacity. That invariant is preserved.

---

## Target populations (frozen before acquisition)

| Class | Count | Example venues |
|---|---|---|
| WORKBENCH | 5 | United Center, Madison Square Garden, Hollywood Bowl, Red Rocks, The Roxy |
| HIGH_ACTIVITY | 7 | Blue Note, Ryman Auditorium, 9:30 Club, Mercury Lounge |
| FESTIVAL | 5 | Grant Park, Zilker Park, Empire Polo Club, Great Stage Park, Randall's Island |
| **Total targets** | **17** | |
| In canonical | 16 | |
| Not in canonical | 1 | Randall's Island |

---

## Acquisition results

### Claims persisted: 9 across 5 venues

| Venue | Wikidata QID | Capacity | Kind | Status |
|---|---|---|---|---|
| United Center | Q639975 | 23,500 | MAX_PERSONS | OBSERVED |
| Madison Square Garden | Q186125 | 18,006-20,789 | MAX_PERSONS | CONFLICTING (5 claims) |
| Hollywood Bowl | Q976218 | 17,500 | MAX_PERSONS | OBSERVED |
| Red Rocks Amphitheatre | Q2182648 | 9,525 | MAX_PERSONS | OBSERVED |
| 9:30 Club | Q4646354 | 1,200 | MAX_PERSONS | OBSERVED |

### Venues with Wikidata QID but no P1083 capacity claim

The Roxy, Ryman Auditorium, Grand Ole Opry House, Blue Note Jazz Club,
Mercury Lounge, Valley Bar, Grant Park, Zilker Park, Empire Polo Club,
Great Stage Park.

These 9 venues resolved to Wikidata entities but have no `P1083` (capacity)
statement. This is the primary structural limitation of Wikidata as a
capacity source for music venues.

### Sources attempted

| Source | Status | Results |
|---|---|---|
| Wikidata P1083 | SUCCESS | 9 claims (5 venues). Rate-limited after ~3 rapid requests; 2-3s delay needed |
| Wikipedia infobox | NO_RESULTS | 0 claims. Page-matching gap: phrase-anchored search does not reliably find venue pages |
| OSM Overpass | DEFERRED | Not attempted in V1 |

---

## Coverage

| Metric | Before | After |
|---|---|---|
| Claims total | 0 | 9 |
| Venues with any claim | 0 | 5 (0.006% of 82,547) |
| MAXIMUM_CAPACITY | 0 | 5 |
| SEATED_CAPACITY | 0 | 0 |
| STANDING_CAPACITY | 0 | 0 |
| CONCERT_SPECIFIC_CAPACITY | 0 | 0 |
| CONFIGURATION_SPECIFIC_CAPACITY | 0 | 0 |
| WORKBENCH_SAFE_PREFILL | 0 | 0 |

---

## Conflict handling

Madison Square Garden has 5 conflicting MAX_PERSONS claims from Wikidata:
18,006, 18,500, 19,812, 20,000, 20,789.

These correspond to different configurations (basketball, hockey, concert,
boxing, end-stage concert). All are marked `CONFLICTING`. None is
auto-filled. All are surfaced to the workbench for user inspection.

Conflicting claims coexist — never averaged.

---

## Workbench integration

The existing `capacity_prefill` function in `show_economics_product.py`
correctly handles this result:

- United Center (single MAX_PERSONS claim) → `UPPER_BOUND_OR_INCOMPATIBLE_ONLY`
- Madison Square Garden (conflicting) → `CONFLICTING_COMPATIBLE_CLAIMS`
- Hollywood Bowl → `UPPER_BOUND_OR_INCOMPATIBLE_ONLY`
- Red Rocks → `UPPER_BOUND_OR_INCOMPATIBLE_ONLY`

No claim meets the `CONFIGURATION_COMPATIBLE` threshold (requires SEATED,
STANDING, or CONCERT with single distinct integral value).

This is correct behavior. The workbench never auto-fills an upper-bound
maximum as event sellable capacity.

---

## Real venue acceptance

### United Center

- Wikidata Q639975: 23,500 MAX_PERSONS
- The infobox notes "capacity = 23,500 (concert)" but Wikidata P1083 lacks
  qualifiers to distinguish concert from hockey/basketball
- Workbench shows UPPER_BOUND_ONLY — correct

### Madison Square Garden

- Wikidata Q186125: 5 conflicting MAX_PERSONS (18,006-20,789)
- Different sports/theater configurations produce different capacities
- No concert-specific claim exists
- Workbench shows all 5 claims with CONFLICTING status

### Hollywood Bowl

- Wikidata Q976218: 17,500 MAX_PERSONS
- Known as an amphitheater; the actual sellable capacity is 17,500 for concerts
- Wikidata P1083 has no configuration qualifiers
- Workbench shows UPPER_BOUND_ONLY despite the value being de facto correct

### Red Rocks Amphitheatre

- Wikidata Q2182648: 9,525 MAX_PERSONS
- Actual concert capacity is 9,525
- Wikidata P1083 has no configuration qualifiers
- Workbench shows UPPER_BOUND_ONLY

---

## Known gaps

1. **Wikidata P1083 is sparse.** Most music venues lack this property entirely.
   Only 5 of 16 target venues had capacity statements.

2. **Wikipedia infoboxes are untapped.** The search mechanism doesn't reliably
   find venue pages. Many Wikipedia venue articles have `capacity` and
   `seating_capacity` fields the API could return.

3. **OSM not queried.** OpenStreetMap `capacity=*` and `capacity:*` tags are a
   complementary source for concert/standing/seated capacity.

4. **All claims are MAX_PERSONS.** No configuration-specific capacity found.
   This means the workbench cannot auto-fill capacity — users must still
   enter `USER_ASSUMPTION` for `usable_capacity` / `sellable_capacity`.

5. **82,547 venues, 5 enriched.** The existing `core.venues` table is
   entirely from MusicBrainz (no capacity data). Only a tiny fraction has
   been enriched.

---

## Next steps

1. **Batch Wikidata for top-100 venues** (3-second delays, proper rate-limit
   backoff). The P1083 coverage will remain sparse — that's Wikidata reality.

2. **Fix Wikipedia infobox matching.** The current phrase-anchored query
   should include city context or fall back to an exact-title lookup.

3. **Add OSM Overpass.** `capacity=*` tags frequently have concert/standing
   values with configuration labels.

4. **Official venue pages.** Where TOS permits, venue websites often publish
   capacity charts — these are the best source for configuration-specific
   capacity claims.

---

## Tests

All existing tests pass. No new test regressions introduced.
The acquisition script (`scripts/venue_capacity_acquisition.py`) is
research-only and not imported by any package code.

---

## Files changed

```
docs/venue-configuration-capacity-evidence-v1.md  (new)
docs/project_manifest.yaml                         (updated)
scripts/venue_capacity_acquisition.py              (new)
reports/venue_capacity_evidence_v1.json            (machine-readable report)
```