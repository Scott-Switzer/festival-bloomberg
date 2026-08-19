# Comparable Event Engine V1 — research framework (leakage-repaired)

Branch: `feat/comparable-event-engine-v1`
Base: `main @ 1188828` (post-merge of PR #32, CI green)

Goal: turn the `COMPS_SIGNAL_ONLY` finding into a **product object** — a
transparent, decomposable comparable-event distance — and measure whether it
beats the existing hierarchical champion. It must do so **without leakage**.

## Methodological repair (this revision)

The first increment used `price_min` as a target-event similarity feature. That
was invalid: the baseline's `LEAKAGE_BLACKLIST` already includes `price_min` /
`price_max`, and the frozen box-office corpus has no separate pre-event price
observation with `knowledge_time < cutoff`. The same audit applied to
`number_of_shows` (also only known from the published box-office record).

Fixed:

1. **Removed `price` and `shows`** from the fingerprint. The remaining
   leakage-safe components are `artist`, `venue`, `market`, `calendar`
   (circular month-of-year).
2. **Canonical admissibility contract.** `FINGERPRINT_SOURCE_FIELDS` is
   asserted disjoint from `LEAKAGE_BLACKLIST` at runtime, and a regression test
   proves that changing `price_min`/`price_max`/`ticket_gross_total`/
   `number_of_shows`/`headcount_total` does not change the distance.
3. **Missingness is coverage, not similarity.** The old `missing -> 0.5`
   neutral prior is gone. A comparable now returns `observed_distance`
   (weighted over observed dimensions only), `coverage_score` (observed
   weight / total weight), and a `ranking_distance = observed_distance +
   penalty × missing_fraction`. A minimum coverage gate drops low-coverage
   rows.
4. **Hierarchical strata.** Candidates are grouped into
   `SAME_ARTIST_VENUE → SAME_ARTIST_MARKET → SAME_ARTIST → SAME_VENUE →
   SAME_MARKET → BROAD_FALLBACK`; the most-specific stratum with ≥
   `min_stratum_size` usable outcomes is used, and the soft distance only
   ranks/weights *within* that stratum (engine C). This preserves the identity
   structure the champion already proved.
5. **Target-specific weights** (economically pre-specified, not tuned on test
   holds): gross weights venue higher, attendance/paid-tickets weight
   artist/venue equally.

Three engines are now compared: **A** hierarchical median champion,
**B** global soft-distance comparable, **C** hierarchical-stratum + soft
distance.

## Leakage-repaired backtest (MAE, lower is better)

| Target | Hold | n | hierarchical (A) | global (B) | stratum (C) | Δ(C−A) | winner |
|---|---|---|---:|---:|---:|---:|---:|---|
| REPORTED_ATTENDANCE | TIME | 49 | **16,920** | 20,452 | 20,549 | +3,629 | HIER |
| REPORTED_ATTENDANCE | ARTIST | 164 | **6,810** | 7,289 | 7,265 | +455 | HIER |
| REPORTED_ATTENDANCE | VENUE | 196 | **8,412** | 9,511 | 9,608 | +1,197 | HIER |
| REPORTED_ATTENDANCE | MARKET | 162 | **10,585** | 10,880 | 10,880 | +294 | HIER |
| REPORTED_ATTENDANCE | TOUR | 116 | **4,531** | 4,530 | 4,625 | +94 | HIER |
| TICKET_GROSS | TIME | 49 | 2,726,324 | 2,549,733 | **2,525,960** | −200,364 | COMP |
| TICKET_GROSS | ARTIST | 208 | **917,426** | 912,852 | 918,707 | +1,281 | HIER |
| TICKET_GROSS | VENUE | 236 | **1,132,053** | 1,179,373 | 1,196,344 | +64,291 | HIER |
| TICKET_GROSS | MARKET | 190 | 1,025,139 | 1,026,850 | **1,007,276** | −17,862 | COMP |
| TICKET_GROSS | TOUR | 170 | **964,315** | 973,359 | 970,276 | +5,961 | HIER |
| PAID_TICKETS | ARTIST | 44 | 5,767 | 5,110 | **4,959** | −807 | COMP |
| PAID_TICKETS | VENUE | 40 | **8,845** | 9,567 | 9,568 | +724 | HIER |
| PAID_TICKETS | MARKET | 28 | **4,640** | 4,765 | 4,746 | +105 | HIER |
| PAID_TICKETS | TOUR | 54 | 7,936 | 7,703 | **7,663** | −273 | COMP |

(PAID_TICKETS has no TIME fold in this corpus.)

## Bootstrap uncertainty (TIME hold, cluster-bootstrap by artist)

| Target | point Δ(C−A) | 90% CI | 95% CI | P(C improves) |
|---|---|---|---:|---:|---:|
| REPORTED_ATTENDANCE | +3,728 | [+1,410, +5,133] | [+1,314, +5,505] | 0.005 |
| TICKET_GROSS | −208,912 | [−333,734, −105,964] | [−375,566, −80,989] | 1.0 |

The gross improvement is real and leakage-free; the attendance deficit is real
and large. They pull in opposite directions, so no single "promote the engine"
conclusion is warranted.

## Negative controls

- **Shuffled outcomes** (TIME): corrupting candidate outcomes degrades engine C
  to MAE 21,485 (attendance, vs 20,549 unshuffled) and 2,913,587 (gross, vs
  2,525,960) — the gross signal is not an artifact of the candidate pool.
- **K sensitivity**: attendance best at K=3 (16,163) and degrades at K=10;
  gross is noisy across K (2,290k–3,023k). No single K is uniformly best.
- **Weight / penalty sensitivity**: flat at this corpus size — the hard
  identity strata dominate ordering and the calendar/penalty knobs barely move
  the weighted median. That is itself a finding: the engine's value is the
  stratum, not the weight vector.

## Honest verdict

`COMPARABLE_EVENT_ENGINE_V1 = PASS_RESEARCH_FRAMEWORK_CHAMPION_UNCHANGED`

The **hierarchical median champion remains champion** for the primary headcount
targets (attendance and, on grouped holds, paid-tickets and gross). The
leakage-repaired stratum engine earns exactly one defensible win: **gross on
the honest chronological hold**, with bootstrap support. That is worth keeping
— gross is the economically decisive target — but it is not "consistently beat
the champion across TIME + grouped holds with uncertainty," so the engine is
not promoted.

The architecture is correct and explainable (strata + soft reranking + coverage
accounting); the binding constraint is data density, not algorithm. The next
lever is `DENSE_PRE_EVENT_DATA_PANEL_V1`: venue capacity band, artist attention
at cutoff, market economics, H3 geography, competition, and tour lead-time —
dimensions the 657-row corpus does not have.

## Decision

- Keep the engine as the research/evaluation object; do **not** replace the
  hierarchical champion in production surfaces.
- Bar remains: "consistently beat the champion under TIME + grouped holds with
  uncertainty."
- SELL_OUT is deferred: it needs a classification harness (probability), not
  weighted-median regression, and is out of scope for this increment.
