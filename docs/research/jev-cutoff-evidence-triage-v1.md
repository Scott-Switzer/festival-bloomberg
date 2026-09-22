# Jev Cutoff-Evidence Triage Eval V1 — experiment report

Milestone: `JEV_CUTOFF_EVIDENCE_TRIAGE_EVAL_V1`
Branch: `feat/jev-cutoff-evidence-triage-eval-v1` (from `e0f86fa`)
Date: 2026-09-22. No secrets in this document.

Empirical question: does Jev high-recall semantic triage of public
historical event evidence earn a place BEFORE expensive extraction/verifier
work, without weakening deterministic admissibility contracts?

**Answer: GO for shadow triage; ACTIVE skipping explicitly out of scope.**
On the frozen held-out split, Jev-assisted admissible recall is 100% vs a
45.5%→11.9% deterministic baseline (held Δ +88.1pp), false-skip rate 0%,
identity-mismatch recall 100%, anchor recall 100%, 0 leaks, 100% fail-closed
fallback. All pre-registered GO criteria pass. Per the milestone contract,
this authorizes SHADOW_TRIAGE only — production acquisition behavior stays
authoritative, and ACTIVE skipping needs a separate future milestone after
shadow observations transfer.

## 1. Architecture (unchanged, enforced)

```text
PUBLIC DOCUMENT -> DETERMINISTIC PASS -> JEV TRIAGE (residual only)
  -> DETERMINISTIC DATE/PIT RESOLUTION and/or GENERATIVE PROPOSAL
  -> DETERMINISTIC EVIDENCE VERIFIER -> WAREHOUSE
```

Jev never suppresses an already-positive deterministic extraction, never
generates dates/IDs/prices/evidence, never sees private settlement data.
`verify_candidate` code is untouched (existing suites green, §9).

## 2. Corpus construction [measured]

340 dossiers from **real repository entities** (artist/venue/date sampled
from the serving warehouse into `tests/python/fixtures/triage_anchors.json`,
501 anchors / 170 artists): A 80 / B 110 / C 100 / D 50; cal 215 / held 125
(36.8% held). Split by anchor artist — no near-duplicates cross splits.

- Class A (verifier-admissible): 8 templates (onsale phrase/now, announced,
  presale, price, JSON-LD, OG+phrase, anchored weekday). Gold = ACTUAL
  pipeline outcome: builder runs `deterministic_pass` + `verify_candidate`
  and asserts ACCEPT.
- Class B (regex-missed semantic, the key class): 11 paraphrase/synonym/
  multi-sentence/implicit variants (`going on sale`, `priced at`, `presale
  opens`→`early access`, `@type Concert`, reversed OG attrs, anchorless
  weekday with metadata anchor…). Builder asserts zero admissible;
  admissibility-in-principle verified by running the verifier on the ideal
  correct candidate (asserts ACCEPT).
- Class C (hard negatives): wrong venue/artist/date, recap, bio, capacity,
  other-event, retrospective, `soon`, marketing. Builder asserts zero
  accepted (identity gazetteer drives `wrong_artist_venue_city_or_date`).
- Class D (adversarial): prompt injection, fake system prompt, contradictory
  dates, multi-event, tribute collision, anchorless relative, vague
  `now available`, stale late report, duplicate, Spanish snippet.

Real-vs-synthetic ledger: entities 100% real (warehouse-observed); prose is
hand-authored templates (wild-text fetch was GDELT-rate-limited; retried,
still 429). Labeled "hand-authored on real anchors", not wild text.

## 3. Gold labels [method]

Deterministic/manual, frozen before held-out eval, never model-generated:
`verifier_admissible` (pipeline-accepted 105 + ideal-would-accept 120),
`worth_full_extraction`, `expected_bound_semantics`, `usable_anchor_present`,
`identity_matches`, verifier rejection reasons. Skip denominator =
worth ∩ admissible = 220 (stale late-reports accepted-but-worthless are a
correct-skip class, reported separately).

## 4. Baseline [measured]

`deterministic_pass` + verifier with target-only identity resolution:
admissible recall **45.5% all / 60.1% cal / 11.9% held** (held is B-heavy by
split luck); hard-negative pass **79%** — i.e. the baseline ACCEPTS 21% of
hard negatives (wrong-identity docs with matching trigger words), because
target-substring resolution cannot contradict. Pre-existing verifier-input
limitation, documented, unchanged by this milestone.

## 5. Jev triage [measured, model jev-1.13.0]

One call/dossier, 12 atomic Nouls + 1 date-semantics Choice, state bounded
(~950 input tokens base). Operating point frozen on cal: SKIP iff
`worth_further_extraction ≤ 0.15`; identity flag `< 0.8`; anchor flag
`≥ 0.4`.

| metric | cal (n=215) | held (n=125) | GO bar | verdict |
|---|---|---|---|---|
| assisted admissible recall | 100% | 100% | — | — |
| recall Δ vs baseline | +39.9pp | **+88.1pp** | ≥+15pp | PASS |
| false-skip on admissible | 0% | **0%** | ≤5% | PASS |
| identity-mismatch recall | 100% (18/18) | **100% (17/17)** | ≥95% | PASS |
| anchor-detection recall | 100% (111/111) | **100% (24/24)** | ≥95% | PASS |
| regex-missed discovery | 39.9% | **88.1% (59/67)** | ≥15% | PASS |
| verifier precision change | none (untouched) | none | unchanged | PASS |
| secret/private leaks | 0 | 0 | 0 | PASS |
| fail-closed fallback | 100% (offline) | 100% | 100% | PASS |
| contradiction hits | 3/3 | 2/2 (scores 0.50–0.51, weak) | — | noted |

Per-template held recall is 1.0 across all 17 worth-admissible templates.
Latency: p50 333ms / p95 460ms / p99 1387ms. Cost: ~920 base tokens →
**$0.039/1,000 dossiers** (actual usage; output free).

## 6. False sends & adversarial

Worth-False docs: 32 escalated / 26 skipped on held (55% false-send).
Per the pre-registered asymmetry this is economically acceptable (cheap
deterministic rejection downstream; no generative stage exists to waste).
Adversarial: injection docs escalate with identical typed shapes
(EXACT_DATE, contradiction ~0.05 — no policy alteration, output stays
typed); fake-system and tribute SKIP (identity 0.24–0.25); late-report and
anchorless-relative escalate (false sends, documented); Spanish snippet
escalates correctly; multi-event escalates. Typed-output-only enforced by
the client validator.

## 7. Robustness & snippet size [measured]

Deterministic perturbations (lowercase, punctuation-strip, unicode
whitespace) on 40 held B/D dossiers: **120/120 action-stable (100%)**.
Snippet padding with realistic article chrome to ~2k/4k/8k tokens (30 B
dossiers): recall 30/30, 29/30, 30/30; p50 latency 334→372→416ms; tokens
scale linearly. **Smallest state preserves quality** — keep ~2k bound.

## 8. Cost/throughput scenarios [measured rates, inferred totals]

At ~950 input tokens/dossier and $0.042/M: 1k/day ≈ $0.04; 10k ≈ $0.39;
100k ≈ $3.90; 1M ≈ $39/day. Sequential wall-clock ~3–5 dossiers/s single
thread; Jev-side rate limit 250k tok/s is not binding at these volumes.
Generative-extraction savings N/A (DeepSeek stage NOT_CONFIGURED — hence
the discovery-path GO criterion, which passed at 88.1%).

## 9. Deterministic contracts unchanged [measured]

`test_historical_decision_evidence.py` + `test_pre_event_cutoffs.py` green
(35 passed with new triage tests). UNKNOWN≠0, bound-not-exact,
anchor-required, identity-mismatch-reject, rights fail-closed all preserved;
`retrieved_at` untouched; no private data in any Jev state (states carry
names/dates/domains only — verified by construction + leak scan 0).

## 10. GO/NO-GO

**GO for SHADOW_TRIAGE.** All pre-registered criteria pass on frozen held-out
data at cal-frozen thresholds. ACTIVE skipping is NOT authorized in this
milestone and requires future shadow-transfer evidence.

## 11. Shadow status

Not implemented in this experiment (GO arrived at report time; shadow wiring
is the prescribed next step, not this PR). Prescribed data contract when
built: provider/model/state-hash/questions-hash/triage-version/answers/
probabilities/confidence/latency/tokens/evaluated_at — separate from
verified evidence, never collapsed into truth.

## 12. Limitations

* Hand-authored prose on real anchors (not wild text); template-bound
  diversity; ideal-candidate admissibility is constructed, not observed.
* Small held anchor set (24) for the anchor metric; identity threshold 0.8
  has ~25% false-flag on consistent docs (measurement-only use).
* Contradiction scores weak (0.5); date_semantics misfires on contradictory
  inputs (NO_DATE_EVIDENCE).
* Baseline identity resolution (target-only) is weaker than production
  dossier resolution — C false-accepts partly reflect harness fidelity.
* Single model version; vendor may shift `jev-latest`.

## 13. Recommended next workload

Per GO: build SHADOW_TRIAGE on residual documents, then re-run this exact
corpus + shadow-transfer analysis before any ACTIVE milestone. Beyond that:
monitor/material-change scoring (score primitive), then tape/search rerank.

## 14. Non-Jev product priority (unchanged)

Next milestone stays `MARKET_TIMING_AND_IDENTITY_V1`: last_play
materialization, observed-shows null diagnosis, identity disambiguation,
market 404/UNKNOWN, search deep-links. Buyer-visible work does not wait for
Jev rollout.

## 15. Claims ledger

* [vendor] 70–500ms, $0.042/M in, calibrated confidence — consistent.
* [measured] §§2–9, model jev-1.13.0, 340 dossiers + 120 perturbations + 90
  sized runs, fixture-DB + live-API protocol above.
* [inferred] Cost/throughput extrapolations (§8), production transfer (§11).
