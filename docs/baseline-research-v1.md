# Baseline Research V1

The first falsifiable quantitative question the company needed answered was
**not** "can we predict whether to book an artist?" It was narrower and
answerable with the data we have:

> Given only box-office results genuinely published *before* a future
> engagement, how predictable are future reported attendance and gross using
> simple historical comparables?

This milestone builds no ML. It runs a strict model ladder — global/venue/
artist/market/artist×market/artist×venue medians, last/recent-3 artist, a
hierarchical comparable fallback, then log-linear / Ridge / Poisson (or
logistic for sell-out), then a shrunken partial-pooling estimator — under
TIME, ARTIST, VENUE, MARKET and TOUR holds, with cluster bootstrap, negative
controls, ablations, and error segmentation.

> Status: **research only.** The corpus is `RESEARCH_ONLY` /
> `TERMS_REVIEW_REQUIRED`; the commercial-eligible corpus is **0**. The models
> here are prohibited from production booking, guarantee, forecast or API use.

## Frozen corpus

| Field | Value |
| --- | --- |
| Corpus version | `public_boxscore_research_corpus_v2` |
| Software version | `baseline_research_v1` |
| Source DB | `data/warehouse/boxoffice_research_v2.duckdb` (gitignored) |
| Rows frozen | 657 |
| Checksum | `4a45197fed073b0f6dfe7e7762a06b3d49badccecd3b212a8e6a656c84f606ee` |
| Billboard publication-time estimate | `2013-11-11` (latest event + 7-day chart lag) |

The manifest (`reports/baseline_research_v1/corpus_v1_manifest.json`,
gitignored) contains every canonical engagement with its target values,
definitions, rights, source publication time, and fold assignment. New scrapes
cannot silently change a score — the checksum changes first.

### A date bug fixed before freezing

Pollstar's `Dates:` field omits the year. The year is now inferred from the
page's publication year (a defensible inference for a current-week chart), and
the TIME fold is recomputed from corrected dates in the freeze. Before this,
Pollstar's `PAID_TICKETS` rows would have all landed in TEST with NULL dates.

## Eligibility waterfall (657 raw → eligible)

Every target has its own population. Multi-show aggregates and estimated rows
are never event-level labels; gross is USD-only (no fake FX).

| Step | REPORTED_ATTENDANCE | PAID_TICKETS | TICKET_GROSS | SELL_OUT |
| --- | ---: | ---: | ---: | ---: |
| raw | 657 | 657 | 657 | 657 |
| multi-show aggregates | −214 | −214 | −214 | −214 |
| estimated / unreported | −0 | −0 | −0 | −0 |
| no event date | −2 | −2 | −2 | −2 |
| target unavailable | −84 | −357 | −0 | −215 |
| non-USD gross | −0 | −0 | −0 | −0 |
| **eligible** | **357** | **84** | **441** | **226** |

Targets are never coerced: `REPORTED_ATTENDANCE` and `PAID_TICKETS` are
separate populations, and a row with `headcount_definition=REPORTED_ATTENDANCE`
is simply *not* a `PAID_TICKETS` observation.

## Research cutoff and leakage

The study cutoff is **historical result availability**, not booking time:

- A prior engagement may only contribute to a target's features if its
  box-office result was published (`publication_time`) before the target's
  event start.
- Billboard is a single archived year-end compilation; its publication time is
  estimated as the latest event date + 7 days.
- The report therefore answers "could prior published history have predicted
  this later engagement?" — **not** "could a promoter have known this at offer
  time?" That distinction is stated everywhere.

The leakage blacklist forbids, for the same target event, any use of its own
attendance / paid tickets / gross / sell-out / sell-through / chart rank /
post-event source metadata / realized capacity utilization. `chart rank` in
particular is an outcome-derived selection artifact and is blacklisted.
Missing history is `None` + a flag, never imputed to zero.

## Headline result (chronological TIME holdout)

`REPORTED_ATTENDANCE`, test N = 49 (MAE, lower is better):

| Model | MAE | WAPE |
| --- | ---: | ---: |
| global median | 19,621 | 0.850 |
| venue median | 18,926 | 0.820 |
| **market median** | **17,064** | **0.739** |
| **hierarchical comparable fallback** | **16,920** | **0.733** |
| partial pooling | 19,076 | 0.827 |
| log-linear | 19,292 | 0.836 |
| Ridge | 19,272 | 0.835 |
| Poisson | 23,077 | 1.000 |

`TICKET_GROSS`, test N = 49 (MAE):

| Model | MAE |
| --- | ---: |
| global median | 2,900,303 |
| **market median** | **2,686,745** |
| hierarchical fallback | 2,726,324 |
| log-linear | 2,858,171 |
| Ridge | 2,861,082 |

**Reading:** prior published box-office history contains real but modest
comparable structure. Market and venue medians beat the global baseline by
~8–14%; the hierarchical fallback (artist×venue → artist×market → artist →
venue → market → global) is the single best predictor. The log-linear /
Ridge / Poisson statistical models **do not beat the best comp**, and Poisson
is worse than the global median. This is the definition of `COMPS_SIGNAL_ONLY`.

## The decisive structural fact

The TIME test set is **entirely 2026 Touring Data with zero artist history**.
The corpus is temporally disjoint — Billboard (2012–13), Pollstar (Jan–May
2024), Touring Data (2024–26) — so a strict chronological holdout is, by
construction, a **cold-start artist test**. Under that holdout every
artist-derived feature collapses to the global median, and the only signal
that survives is market/venue history.

This is exactly why grouped holds matter. Under ARTIST/VENUE/MARKET/TOUR
holds (which mix time and therefore leak artist familiarity into training),
statistical models tie or slightly beat comps in the friendlier folds but
blow up in others (e.g. log-linear WAPE 2.33 under TOUR holdout). The group
holds mostly demonstrate entity memorization, not durable structure.

## Per-target verdict

| Target | Eligible | Verdict | Comp beats global |
| --- | ---: | --- | --- |
| `REPORTED_ATTENDANCE` | 357 | `COMPS_SIGNAL_ONLY` | 4 / 5 holds |
| `PAID_TICKETS` | 84 | `COMPS_SIGNAL_ONLY` | 2 / 4 holds (no TIME holdout possible) |
| `TICKET_GROSS` | 441 | `COMPS_SIGNAL_ONLY` | 5 / 5 holds |
| `SELL_OUT` | 226 | `NO_PREDICTABLE_SIGNAL` | 0 / 4 holds (no TIME holdout possible) |

`PAID_TICKETS` and `SELL_OUT` are entirely Pollstar (Jan–May 2024), which the
TIME split places in train — so those targets have **no chronological
out-of-sample** and are only scored under group holds. This is reported
honestly, not hidden.

**Overall verdict: `COMPS_SIGNAL_ONLY`.** Historical comparables carry signal;
regularized/statistical models do not consistently beat them, and sell-out is
not predictable in this corpus.

## Uncertainty

Cluster bootstrap (by artist) of the log-linear vs hierarchical-fallback MAE
delta on the TIME holdout: point delta ~2,497 (statistical *worse*), 90% CI
[1,060, 4,404], p(improve) = 0.0. The comparable advantage over the global
median is directionally stable but modest in absolute magnitude.

## Negative controls and ablations

- **Shuffled target.** Statistical models refit on permuted train targets are
  reported for every split; they do not outperform the real-target models,
  confirming the observed signal is a target relationship rather than an
  artifact of the design matrix.
- **Random-split comparison.** `NAIVE_RANDOM_SPLIT` (included only to show
  optimism) produces spuriously low MAE (≈9,329 for attendance) relative to
  the TIME holdout (≈19,621), demonstrating why random splits are not the
  headline.
- **Ablations.** artist-only / venue-only / market-only / artist+venue /
  artist+venue+market log-linear MAE is reported per split; venue and market
  history are the workhorses in the TIME fold, while artist history adds
  little because the test artists are cold-start.

## Error segmentation (TIME, attendance)

Every test row is `artist_history=0`, `source=touring_data`, `year=2026`,
`sellout=0` — a fully cold-start slice with MAE ≈ 16,920 under the best comp.
There is no within-corpus comparison for warm-start artists under a
chronological holdout.

## Selection bias

All three sources are chart/editorial selected (Billboard chart, Pollstar
top-5-per-tier chart, Touring Data reported tours). The strongest claim this
study can make is:

> "Model performance within the observed public box-office research
> population" — **not** "model predicts all concerts".

## Research verdict and the bar for advancing

- **`COMPS_SIGNAL_ONLY`** for attendance / paid-tickets / gross; **no signal**
  for sell-out.
- A statistical model beating a global median is **not** sufficient. The bar
  is: does a nontrivial model *consistently* beat strong historical-comparable
  baselines under TIME and grouped holds? It does **not** today.
- **Recommendation: do not proceed to ML.** The next lever is data, not
  algorithm: more artist-level history within a *dense* temporal span (so a
  chronological holdout stops being a pure cold-start test), a first-party /
  licensed feed, and a real booking/announcement cutoff so the study can move
  from "published-history availability" toward the actual offer-time question.

## Files

- `python/festival_bloomberg/research/freeze.py` — checksummed corpus freeze
- `python/festival_bloomberg/research/features.py` — point-in-time comparable
  features, leakage blacklist, missing-as-information
- `python/festival_bloomberg/research/baselines.py` — pure-numpy models +
  metrics (no sklearn)
- `python/festival_bloomberg/research/experiment.py` — split evaluation,
  bootstrap, controls, ablations, verdict
- `python/festival_bloomberg/oa/baseline_research.py` — live OA driver
- `tests/python/test_baseline_research.py` — 18 offline regressions
