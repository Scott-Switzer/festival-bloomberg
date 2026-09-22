#!/usr/bin/env python3
"""Cutoff-evidence triage benchmark: deterministic pass vs Jev-assisted.

  baseline : deterministic_pass + verify_candidate per dossier (target-only
             identity resolution — the honest current capability)
  jev      : Jev triage_dossier per dossier (needs TYPESAFE_API_KEY)
  report   : metrics from result files (no key needed)

Two-stage policy: det-accepted documents are PRESERVED (Jev cannot veto);
residual documents get a Jev SKIP/ESCALATE. Final admission stays with the
deterministic verifier (unchanged code).

Results go to artifacts/jev_triage/ (gitignored). Rows store dossier_id,
class, split, gold flags, det outcome, Jev action/confidences, latency and
tokens only — never document text or keys.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "python"))
sys.path.insert(0, str(REPO_ROOT / "tests" / "python"))

ARTIFACT_DIR = REPO_ROOT / "artifacts" / "jev_triage"
#: Frozen operating points (set on the calibration split; see report).
SKIP_THRESHOLD = 0.15
IDENTITY_FLAG_THRESHOLD = 0.8
ANCHOR_THRESHOLD = 0.4
INPUT_DOLLARS_PER_M = 0.042

#: Fixed article-chrome boilerplate for snippet-size experiments.
BOILERPLATE = (
    "\n\nRelated: more concerts in your area. Sign up for our newsletter for "
    "presale codes and onsale reminders. Advertisement: cheap tickets, best "
    "seats, verified orders. Footer: about us, contact, privacy policy, terms "
    "of service, cookie preferences, accessibility statement. Navigation: "
    "home, music, concerts, festivals, venues, reviews, photos. Ticket vendor "
    "boilerplate: all sales final, service fees apply, prices may vary, "
    "delivery options include mobile transfer and will call. "
) * 40


def load_corpus():
    from fixtures.triage_corpus import build
    return build()


def _det_outcome(d):
    from festival_bloomberg.flywheel.evidence_extraction import deterministic_pass
    from festival_bloomberg.flywheel.evidence_verification import verify_candidate
    from fixtures.triage_corpus import _norm, deterministic_resolved
    html = d["html"]
    pub = None
    if d["source"]["published_at"]:
        from datetime import datetime
        pub = datetime.fromisoformat(d["source"]["published_at"])
    cands = deterministic_pass(html, canonical_event_id="eval-event",
                               source_document_id="eval-doc",
                               source_url="https://eval.local/doc",
                               publication_time=pub)
    resolved = deterministic_resolved(d["text"], d["target"])
    accepted = [c for c in cands if verify_candidate(
        c, target_event=d["target"], resolved=resolved,
        rights_status="RESEARCH_ONLY")["verification_status"] == "ACCEPTED"]
    return cands, accepted


def cmd_baseline(args):
    dossiers = load_corpus()
    rows = []
    for d in dossiers:
        cands, accepted = _det_outcome(d)
        rows.append({"dossier_id": d["dossier_id"], "class": d["class"], "split": d["split"],
                     "n_candidates": len(cands), "n_accepted": len(accepted),
                     "cutoffs": sorted({c["cutoff_type"] for c in cands})})
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    path = ARTIFACT_DIR / "baseline.json"
    path.write_text(json.dumps({"rows": rows}, indent=1))
    print(f"baseline: {len(rows)} rows -> {path}")


def cmd_jev(args):
    from festival_bloomberg.intelligence.jev import JevClient
    from festival_bloomberg.intelligence.jev_triage import (
        decide_triage, triage_dossier)
    dossiers = load_corpus()
    if args.only:
        dossiers = [d for d in dossiers if d["dossier_id"] in set(args.only.split(","))]
    if args.pad_to:
        dossiers = [dict(d, text=(d["text"] + BOILERPLATE)[: args.pad_to * 4]) for d in dossiers]
    client = JevClient(model=args.model)
    if not client.is_configured:
        print("TYPESAFE_ACCESS=BLOCKED (no TYPESAFE_API_KEY); no Jev calls made")
        return 2
    rows = []
    for d in dossiers:
        pred = triage_dossier(client, d, max_chars=args.max_chars)
        blob = json.dumps(pred, default=str)
        rows.append({
            "dossier_id": d["dossier_id"], "class": d["class"], "split": d["split"],
            "status": pred.get("status"), "action": decide_triage(pred, skip_threshold=SKIP_THRESHOLD),
            "worth": pred.get("worth_further_extraction"),
            "identity_consistent": pred.get("identity_consistent"),
            "contradiction": pred.get("contains_contradiction"),
            "date_semantics": pred.get("date_semantics"),
            "anchor": pred.get("has_usable_date_anchor"),
            "model": pred.get("model"), "latency_ms": pred.get("latency_ms"),
            "input_tokens": (pred.get("usage") or {}).get("input_tokens"),
            "output_tokens": (pred.get("usage") or {}).get("output_tokens"),
            "attempts": pred.get("attempts"),
            "leak": ("sk-" in blob or "apikey_" in blob),
        })
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    tag = f"{args.model.replace('/', '_')}"
    if args.pad_to:
        tag += f"_pad{args.pad_to}"
    if args.only:
        tag += "_subset"
    path = ARTIFACT_DIR / f"jev_{tag}.json"
    path.write_text(json.dumps({"rows": rows, "model_requested": args.model,
                                "skip_threshold": SKIP_THRESHOLD}, indent=1))
    print(f"jev: {len(rows)} rows -> {path}")


def _pct(xs):
    xs = sorted(xs)
    if not xs:
        return {"p50": None, "p95": None, "p99": None, "n": 0}
    def q(p):
        return xs[min(len(xs) - 1, int(p * len(xs)))]
    return {"p50": q(0.5), "p95": q(0.95), "p99": q(0.99), "n": len(xs)}


def cmd_report(args):
    dossiers = {d["dossier_id"]: d for d in load_corpus()}
    if args.split != "all":
        dossiers = {k: v for k, v in dossiers.items() if v["split"] == args.split}
    base = {r["dossier_id"]: r for r in json.loads(Path(args.baseline).read_text())["rows"]} if args.baseline else {}
    jev = {r["dossier_id"]: r for r in json.loads(Path(args.jev).read_text())["rows"]} if args.jev else {}
    in_scope = [d for d in dossiers.values() if d["dossier_id"] in base or d["dossier_id"] in jev]

    def is_adm_worth(d):
        return d["verifier_admissible"] and d["worth_full_extraction"]

    m: dict = {"n": len(in_scope)}
    if base:
        det_acc = [d for d in in_scope if d["dossier_id"] in base and base[d["dossier_id"]]["n_accepted"] >= 1]
        adm = [d for d in in_scope if d["dossier_id"] in base and is_adm_worth(d)]
        hit = [d for d in adm if base[d["dossier_id"]]["n_accepted"] >= 1]
        neg = [d for d in in_scope if d["dossier_id"] in base and d["class"] == "C"]
        neg_pass = [d for d in neg if base[d["dossier_id"]]["n_accepted"] == 0]
        m["baseline"] = {
            "admissible_recall": len(hit) / len(adm) if adm else None,
            "admissible_n": len(adm),
            "hard_negative_pass_rate": len(neg_pass) / len(neg) if neg else None,
            "escalated_n": sum(1 for d in in_scope if d["dossier_id"] in base and base[d["dossier_id"]]["n_candidates"] > 0),
        }
    if jev:
        adm = [d for d in in_scope if d["dossier_id"] in jev and is_adm_worth(d)]

        def _action(r):
            # Recompute from stored fields with frozen thresholds so reports
            # never depend on collection-time defaults.
            if r.get("status") != "OK":
                return "FALLBACK_ESCALATE"
            if (r.get("worth") or 1.0) <= SKIP_THRESHOLD:
                return "SKIP"
            return "ESCALATE"

        assisted, skipped, disc = 0, 0, 0
        for d in adm:
            r = jev[d["dossier_id"]]
            det_ok = d["dossier_id"] in base and base[d["dossier_id"]]["n_accepted"] >= 1
            if det_ok:
                assisted += 1
            elif _action(r) != "SKIP":
                assisted += 1
            else:
                skipped += 1
            if not det_ok and _action(r) != "SKIP":
                disc += 1
        res = [d for d in adm if d["dossier_id"] in jev and not (
            d["dossier_id"] in base and base[d["dossier_id"]]["n_accepted"] >= 1)]
        j = {
            "assisted_admissible_recall": assisted / len(adm) if adm else None,
            "false_skip_rate": skipped / len(adm) if adm else None,
            "discovery_rescued": disc,
            "discovery_rate": disc / len(adm) if adm else None,
            "residual_n": len(res),
        }
        mm = [d for d in in_scope if d["dossier_id"] in jev and (
            not d["identity_matches"] or (
                d["class"] == "C" and d["dossier_id"].split("-")[1] in
                ("wrong_venue", "wrong_artist", "other_event")))]
        j["identity_mismatch_recall"] = (
            sum(1 for d in mm if (jev[d["dossier_id"]].get("identity_consistent") or 1) < IDENTITY_FLAG_THRESHOLD) / len(mm) if mm else None)
        j["identity_mismatch_n"] = len(mm)
        j["identity_flag_threshold"] = IDENTITY_FLAG_THRESHOLD
        an = [d for d in in_scope if d["dossier_id"] in jev and d["usable_anchor_present"]]
        j["anchor_recall"] = (
            sum(1 for d in an if (jev[d["dossier_id"]].get("anchor") or 0) >= ANCHOR_THRESHOLD) / len(an) if an else None)
        j["anchor_n"] = len(an)
        lat = [jev[d["dossier_id"]].get("latency_ms") for d in in_scope
               if d["dossier_id"] in jev and jev[d["dossier_id"]].get("latency_ms") is not None]
        j["latency_ms"] = _pct(lat)
        toks = sum((jev[d["dossier_id"]].get("input_tokens") or 0) for d in in_scope if d["dossier_id"] in jev)
        n_jev = sum(1 for d in in_scope if d["dossier_id"] in jev)
        j["input_tokens_total"] = toks
        j["cost_usd_total"] = round(toks / 1e6 * INPUT_DOLLARS_PER_M, 6)
        j["cost_per_1000_usd"] = round(toks / max(1, n_jev) * 1000 / 1e6 * INPUT_DOLLARS_PER_M, 4)
        j["invented_or_leak"] = sum(1 for d in in_scope if d["dossier_id"] in jev and jev[d["dossier_id"]].get("leak"))
        j["provider_failures"] = sum(1 for d in in_scope if d["dossier_id"] in jev and jev[d["dossier_id"]].get("status") != "OK")
        j["contradiction_hits"] = sum(
            1 for d in in_scope if d["dossier_id"] in jev and "contradictory" in d["dossier_id"]
            and (jev[d["dossier_id"]].get("contradiction") or 0) >= 0.5)
        by_type: dict[str, dict] = {}
        for d in adm:
            t = d["dossier_id"].split("-")[0] + ":" + d["dossier_id"].split("-")[1]
            e = by_type.setdefault(t, {"n": 0, "assisted": 0, "skipped": 0})
            e["n"] += 1
            r = jev[d["dossier_id"]]
            det_ok = d["dossier_id"] in base and base[d["dossier_id"]]["n_accepted"] >= 1
            if det_ok or _action(r) != "SKIP":
                e["assisted"] += 1
            else:
                e["skipped"] += 1
        j["by_template"] = {k: {"n": v["n"], "recall": round(v["assisted"] / v["n"], 3)} for k, v in sorted(by_type.items())}
        m["jev"] = j
    if m.get("baseline") and m.get("jev"):
        b, j = m["baseline"]["admissible_recall"], m["jev"]["assisted_admissible_recall"]
        if b is not None and j is not None:
            m["admissible_recall_delta"] = j - b
    print(json.dumps(m, indent=1))
    if args.out:
        Path(args.out).write_text(json.dumps(m, indent=1))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("baseline")
    j = sub.add_parser("jev")
    j.add_argument("--model", default="jev-latest")
    j.add_argument("--only", default="")
    j.add_argument("--max-chars", type=int, default=6000)
    j.add_argument("--pad-to", type=int, default=0)
    r = sub.add_parser("report")
    r.add_argument("--split", default="all", choices=["all", "cal", "held"])
    r.add_argument("--baseline", default="")
    r.add_argument("--jev", default="")
    r.add_argument("--out", default="")
    args = ap.parse_args()
    if args.cmd == "baseline":
        return cmd_baseline(args) or 0
    if args.cmd == "jev":
        return cmd_jev(args)
    cmd_report(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
