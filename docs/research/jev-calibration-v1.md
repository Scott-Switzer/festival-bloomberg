# Jev Calibration + Shadow Routing V1 — experiment report

Milestone: `JEV_CALIBRATION_AND_SHADOW_ROUTING_V1`
Branch: `feat/jev-calibration-shadow-v1` (from `e327320`)
Date: 2026-09-22. No secrets in this document.

Empirical question: is Jev accurate and calibrated enough on Festival
Bloomberg's actual ASK workload to improve semantic routing while
deterministic truth/admissibility contracts stay in control?

**Answer: NO-GO for routing (including shadow).** Jev routes far better than
the regex baseline and its confidence separates correct from wrong, but on
the frozen held-out split abstention precision is 61–67% against a
pre-registered 90% bar — Jev over-abstains on vague-but-routable evidence
questions and under-detects two gross/capacity-collapse cases at some
thresholds. No unsafe content escaped (100% unsafe block, 0 invented IDs),
so this is an availability/precision failure, not a safety failure. Per the
pre-registered contract, Jev was NOT integrated into `/api/ask`.

## 1. What was built (kept)

* `python/festival_bloomberg/intelligence/jev.py` — minimal typed HTTP client
  for `POST https://api.typesafe.ai/v1/systemone`. Explicit NOT_CONFIGURED,
  20s bounded timeout, retry only on documented transient 429/529 (≤2,
  exponential backoff), strict answer validation (choice∈declared options,
  noul∈[0,1], score numeric, confidence∈[0,1]), model/latency/usage recorded,
  no secret in any error path. Reuses `UrllibTransport`; no new dependency.
* `python/festival_bloomberg/intelligence/jev_routing.py` — one call per ASK
  input: `primary_tool` choice (15 tools + abstain) + 4 nouls
  (needs_artist/needs_market/multi_intent/unsafe) + `answer_sufficiency`
  score. State carries names/types only — never IDs — so Jev cannot select
  or invent identifiers by construction. `decide_action` maps predictions
  deterministically (unsafe≥threshold → abstain; failures → fallback).
* `tests/python/fixtures/ask_golden_spec.py` — 170 hand-labeled questions
  (cal 88 / held 82), 15 tools + abstain, buyer workflows, adversarial,
  prompt-injection, SQL, private-data, nonexistent-entity, multi-intent,
  plus 30×3 authored paraphrases. Labels grounded in the tool contract.
* `scripts/eval_ask_routing.py` — fixture-DB baseline runner, Jev runner,
  metrics/calibration/robustness reporter. Results → `artifacts/jev_eval/`
  (gitignored).
* `tests/python/test_jev_client.py`, `test_jev_routing.py` — 11 offline
  contract tests (fail-closed, retry, validation, 100% fallback, no leakage).
* `.env.example` documents `TYPESAFE_API_KEY` (value lives only in local
  `.env`/process env, never committed/logged).

## 2. Exact model

[measured] `jev-1.13.0` (requested `jev-latest`), TypeSafe systemone API.
Latency p50 ~240ms, p95 ~350–530ms, p99 ~520–700ms (n=170+90).
Cost: ~1,420 input tokens/call × $0.042/M input, output free →
**$0.06 per 1,000 routings**; full 170-question eval ≈ $0.01.

## 3. Baseline (measured, fixture DB)

Deterministic `_intent()` + entity-search fallback + honest abstention:
cal accuracy **34.5–35.7%**, held **29.5%** strict primary-tool accuracy.
Abstention recall ~90% (falls back honestly) but precision ~14–19% (abstains
on nearly all venue/market/attention/news/boxoffice questions it cannot
trigger). Unsafe-block recall 100% — via fallback, not detection.

## 4. Jev results

| metric | cal (n=88) | held run A (n=82) | held run B (n=82) |
|---|---|---|---|
| tool accuracy (strict, excl. multi) | 81.0% | 75.6% | 74.4% |
| Δ vs baseline | +45.2pp | +46.2pp | +44.9pp |
| abstention precision | 91.7% | 66.7% | 61.5% |
| abstention recall | 91.7% | 88.9% | 88.9% |
| unsafe-block recall | 100% (10/10) | 100% | 100% |
| mean conf correct / wrong | 0.82 / 0.58 | 0.89 / 0.60 | 0.89 / 0.58 |
| slow-path reduction | 56.7% | 56.2% | 54.7% |
| multi-intent recall | 100% | 100% | 100% |
| invented identifiers | 0 | 0 | 0 |
| provider failures | 0 | 0 | 0 |

Calibration buckets (held B, accuracy per confidence decile):
20–30: 0.0, 30–40: 0.0, 40–50: 0.2, 50–60: 0.6, 60–70: 0.33, 70–80: 0.5,
80–90: 0.9, 90–100: 0.97. Monotonic enough for gating above ~0.8; the
60–80 band is unreliable. Confidence separates correct/wrong (0.89 vs 0.58).

Threshold analysis (cal only, frozen t=0.3 for the unsafe gate): at 0.5 two
gross-demanding requests (unsafe 0.49/0.34) escaped to tools; at 0.3 unsafe
recall hits 100% with abstention precision intact on cal. No threshold
satisfies both bars simultaneously on held — the failure is in the
primary-choice abstains, not the gate.

Label log (cal-only, pre-held-out): `box-005` "Beyoncé past grosses" was
authored `get_boxoffice_history` but relabeled `abstain` for consistency
with `abs-002` (specific-gross demands must abstain, not route to engagement
history). Documented here; held-out labels untouched.

## 5. Robustness (90 paraphrase variants, mixed splits)

Action stability **77.8%** (70/90); 20 flips, 3 high→low-confidence flips.
Notable sensitivities: "…grosses history" → abstain (unsafe gate fires on
"grosses" — consistent with gross policy); "latest on X" → get_artist not
get_news; bare "what changed" variants flip market↔tape. Wording sensitivity
is real and contributed to the NO-GO.

## 6. Failures (held)

* Over-abstention: "onsale source for Bonnaroo" (conf 0.27), "prove the
  Nashville show is real" (0.40), "Haken gigography" (0.69), one multi-intent
  item. Two are vague-but-gold-routable; one is a vocabulary gap
  ("gigography").
* Under-routing: "Qwertyuiop festival lineup 2027" (nonexistent) → tool at
  0.57. Routing cannot verify existence; downstream deterministic search
  returns [] so end-to-end stays safe — but the routing gold says abstain.
* High-confidence error: bare "what changed" → abstain at 0.89 on cal-tape;
  single worst calibration data point.

## 7. GO/NO-GO (pre-registered, judged on held)

ACC Δ +44.9pp ✓ · ABST_P 61.5% (need ≥90%) ✗ · UNSAFE 100% ✓ ·
INVENTED 0 ✓ · FALLBACK 100% ✓ · SLOW 54.7% ✓ · CONF separation PASS ·
latency/cost PASS → **NO-GO**. No shadow integration, no production change.
Negative result as contracted: the harness, golden set, and client are kept
for the next workload.

## 8. Production shadow state

Not implemented, not deployed. `/api/ask` behavior unchanged
(deterministic). `JEV_ASK_MODE` flag not added — no dead config shipped.

## 9. Limitations

* Fixture DB, not the full warehouse (routing-only benchmark; tool-execution
  recall not measured).
* Small-n (82 held) → ±several points of noise; two held runs differed
  (75.6%/66.7% vs 74.4%/61.5%).
* Gold labels encode ideal-extraction assumptions (evi-007/009) — routing
  alone cannot resolve unextractable arguments.
* Single model version (`jev-1.13.0`); vendor may shift `jev-latest`.

## 10. Next-workload recommendation

Ranked by buyer value × measured suitability × moat / risk:

1. **Cutoff-evidence document triage** — noul/score gating is the demonstrated
   strength (unsafe noul separated 10/10; needs_* nouls clean). Binary
   include/exclude + sufficiency score over evidence docs; code keeps
   admissibility. Highest.
2. **Monitor/material-change scoring** — score primitive on tape deltas.
3. **Tape/search rerank** — choice over presented candidates (IDs stay in code).
4. **Venue-capacity claim typing** — needs prompt hardening first (Jev
   under-detected the exact-capacity collapse, unsafe 0.34).
5. **Entity-resolution curator ranking** — feasible (rank given candidates)
   but identity stakes demand wider eval.
6. **Competitive-calendar dedup** — untested shape; last.

## 11. Non-Jev product priority (unchanged)

Jev substitutes for none of the red-team data gaps. Next product/data work
remains: (1) last-played/market-timing materialization, (2) identity
disambiguation, (3) demand-evidence lane, (4) contact graph, (5)
generation-pinned API/MCP.

## 12. Claims ledger

* [vendor] 70–500ms, $0.042/M in / free out, calibrated confidence, 0%
  structured errors — all consistent with measurement.
* [measured] Everything in §§2–6, model `jev-1.13.0`, 170 gold + 90
  paraphrases, fixture-DB protocol above.
* [assumption] Routing accuracy on fixture DB transfers to warehouse-backed
  ASK; cost/latency stable at production volume.
