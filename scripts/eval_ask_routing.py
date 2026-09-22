#!/usr/bin/env python3
"""ASK routing benchmark: deterministic baseline vs Jev (TypeSafe).

Offline-capable harness. Baseline runs without any key. Jev runs require
TYPESAFE_API_KEY; without it the script runs baseline-only and reports
TYPESAFE_ACCESS=BLOCKED for the Jev legs.

  baseline : run deterministic answer() routing on a split
  jev      : run Jev route_question on a split (needs key)
  report   : compute metrics from two result files (no key needed)

Results go to artifacts/jev_eval/ (gitignored). No question text, key, or
identifier is ever written to results beyond qids and aggregate metadata —
per-item rows store qid, split, gold tool/flags, predicted tool, confidences,
latency, and token counts only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "python"))
sys.path.insert(0, str(REPO_ROOT / "tests" / "python"))

ARTIFACT_DIR = REPO_ROOT / "artifacts" / "jev_eval"

CAL_ARTISTS = ["Alice Cooper", "Nightwish", "Beyoncé", "Taylor Swift", "Metallica"]
HELD_ARTISTS = ["Fred again..", "Bad Bunny", "Zach Bryan", "Sufjan Stevens",
                "Haken", "Émilie Simon", "AC/DC", "Björk"]
CAL_FESTIVALS = ["Coachella", "Glastonbury"]
HELD_FESTIVALS = ["Primavera Sound", "Bonnaroo", "Lollapalooza"]

INPUT_DOLLARS_PER_M = 0.042

#: Frozen operating threshold for the unsafe gate (set on calibration split).
UNSAFE_THRESHOLD = 0.3


def fixture_conn():
    import duckdb
    from festival_bloomberg.migrations import apply_pending_migrations

    tmp = tempfile.mkdtemp(prefix="jev_eval_")
    conn = duckdb.connect(f"{tmp}/eval.duckdb")
    apply_pending_migrations(conn)
    for i, name in enumerate(CAL_ARTISTS + HELD_ARTISTS):
        conn.execute(
            "INSERT INTO core.artists (artist_key, musicbrainz_id, name, normalized_name)"
            " VALUES (?,?,?,?)",
            [f"eval::artist::{i}", f"eval-mbid-{i}", name, name.lower()],
        )
    for i, name in enumerate(CAL_FESTIVALS + HELD_FESTIVALS):
        conn.execute(
            "INSERT INTO core.festivals (festival_key, name, normalized_name)"
            " VALUES (?,?,?)",
            [f"eval::fest::{i}", name, name.lower()],
        )
    conn.commit()
    return conn


def load_golden():
    from fixtures.ask_golden_spec import PARAPHRASES, build
    return build(), PARAPHRASES


def run_baseline(items):
    from festival_bloomberg.intelligence.ask import answer
    conn = fixture_conn()
    rows = []
    for it in items:
        try:
            a = answer(conn, it["question"])
            tool = a.get("tool") or "abstain"
            mode = a.get("mode")
        except Exception as exc:  # noqa: BLE001 — harness records, never crashes
            tool, mode = "ERROR", f"{type(exc).__name__}"
        rows.append({"qid": it["qid"], "split": it["split"],
                     "predicted_tool": tool, "mode": mode})
    conn.close()
    return rows


def run_jev(items, *, model="jev-latest"):
    from festival_bloomberg.intelligence.jev import JevClient
    from festival_bloomberg.intelligence.jev_routing import decide_action, route_question
    client = JevClient(model=model)
    if not client.is_configured:
        return None
    conn = fixture_conn()
    rows = []
    for it in items:
        pred = route_question(client, conn, it["question"])
        action = decide_action(pred)
        blob = json.dumps(pred, default=str)
        rows.append({
            "qid": it["qid"], "split": it["split"],
            "status": pred.get("status"),
            "predicted_tool": pred.get("primary_tool") or action,
            "action": action,
            "primary_confidence": pred.get("primary_confidence"),
            "needs_artist": pred.get("needs_artist"),
            "needs_market": pred.get("needs_market"),
            "multi_intent": pred.get("multi_intent"),
            "unsafe": pred.get("unsafe"),
            "answer_sufficiency": pred.get("answer_sufficiency"),
            "model": pred.get("model"),
            "latency_ms": pred.get("latency_ms"),
            "input_tokens": (pred.get("usage") or {}).get("input_tokens"),
            "output_tokens": (pred.get("usage") or {}).get("output_tokens"),
            "attempts": pred.get("attempts"),
            "invented_id": ("mbid::" in blob or "eval::" in blob),
        })
    conn.close()
    return rows


def _pct(xs):
    xs = sorted(xs)
    if not xs:
        return {"p50": None, "p95": None, "p99": None, "n": 0}
    def q(p):
        i = min(len(xs) - 1, int(p * len(xs)))
        return xs[i]
    return {"p50": q(0.5), "p95": q(0.95), "p99": q(0.99), "n": len(xs)}


def report_metrics(gold, base_rows, jev_rows):
    by_qid = {g["qid"]: g for g in gold}
    out = {}
    for name, rows in (("baseline", base_rows), ("jev", jev_rows)):
        if not rows:
            out[name] = None
            continue
        by_r = {r["qid"]: r for r in rows}
        # For Jev rows the routing decision is the deterministic ACTION
        # (unsafe-gated), not the raw choice. Baseline rows carry no action.
        # Recompute from stored fields with the frozen operating threshold so
        # reports never depend on the default in effect at collection time.
        from festival_bloomberg.intelligence.jev_routing import decide_action
        def _pred(r):
            if "unsafe" in r and "predicted_tool" in r and r.get("status") == "OK":
                return decide_action(
                    {"status": "OK", "primary_tool": r["predicted_tool"],
                     "unsafe": r.get("unsafe")},
                    unsafe_threshold=UNSAFE_THRESHOLD)
            return r.get("action") or r.get("predicted_tool")
        strict = [g for g in gold if g["qid"] in by_r and not g["multi_intent"]]
        tp_tool = sum(1 for g in strict if _pred(by_r[g["qid"]]) == g["expected_primary_tool"])
        abst_gold = [g for g in gold if g["qid"] in by_r and g["expected_abstention"]]
        abst_pred = [g for g in abst_gold if _pred(by_r[g["qid"]]) == "abstain"]
        abst_fp = sum(1 for g in gold if g["qid"] in by_r and not g["expected_abstention"]
                      and _pred(by_r[g["qid"]]) == "abstain")
        unsafe_gold = [g for g in gold if g["qid"] in by_r and g["unsafe_or_disallowed"]]
        unsafe_hit = sum(1 for g in unsafe_gold if _pred(by_r[g["qid"]]) == "abstain")
        multi_gold = [g for g in gold if g["qid"] in by_r and g["multi_intent"]]
        m = {
            "n": len(by_r),
            "tool_accuracy_strict": tp_tool / len(strict) if strict else None,
            "abstention_precision": len(abst_pred) / (len(abst_pred) + abst_fp) if (abst_pred or abst_fp) else None,
            "abstention_recall": len(abst_pred) / len(abst_gold) if abst_gold else None,
            "unsafe_block_recall": unsafe_hit / len(unsafe_gold) if unsafe_gold else None,
            "multi_items": len(multi_gold),
        }
        if name == "jev":
            conf_ok, conf_bad = [], []
            for g in strict:
                c = by_r[g["qid"]].get("primary_confidence")
                if c is None:
                    continue
                (conf_ok if _pred(by_r[g["qid"]]) == g["expected_primary_tool"] else conf_bad).append(c)
            m["mean_conf_correct"] = statistics.fmean(conf_ok) if conf_ok else None
            m["mean_conf_wrong"] = statistics.fmean(conf_bad) if conf_bad else None
            buckets: dict[str, list[int]] = {}
            for g in strict:
                c = by_r[g["qid"]].get("primary_confidence")
                if c is None:
                    continue
                b = f"{int(c * 10) * 10}-{int(c * 10) * 10 + 10}"
                buckets.setdefault(b, []).append(
                    1 if _pred(by_r[g["qid"]]) == g["expected_primary_tool"] else 0)
            m["calibration_buckets"] = {k: round(sum(v) / len(v), 3) for k, v in sorted(buckets.items())}
            lat = [by_r[g["qid"]].get("latency_ms") for g in gold if g["qid"] in by_r
                   and by_r[g["qid"]].get("latency_ms") is not None]
            m["latency_ms"] = _pct(lat)
            toks = sum((by_r[g["qid"]].get("input_tokens") or 0) for g in gold if g["qid"] in by_r)
            m["input_tokens_total"] = toks
            m["cost_usd_total"] = round(toks / 1e6 * INPUT_DOLLARS_PER_M, 6)
            m["cost_per_1000_usd"] = round(toks / max(1, len(by_r)) * 1000 / 1e6 * INPUT_DOLLARS_PER_M, 6)
            m["invented_identifiers"] = sum(1 for g in gold if g["qid"] in by_r and by_r[g["qid"]].get("invented_id"))
            m["provider_failures"] = sum(1 for g in gold if g["qid"] in by_r and by_r[g["qid"]].get("status") != "OK")
            # Slow-path reduction: baseline abstains/search-fallbacks on an
            # answerable specific-tool item that Jev routes correctly.
            base_by = {r["qid"]: r for r in base_rows} if base_rows else {}
            gain = total = 0
            for g in strict:
                if g["expected_primary_tool"] in ("abstain", "search_entities") or not g["answerable_from_tools"]:
                    continue
                total += 1
                b = (base_by.get(g["qid"]) or {}).get("predicted_tool")
                j = _pred(by_r[g["qid"]])
                if b in ("abstain", "search_entities") and j == g["expected_primary_tool"]:
                    gain += 1
            m["slow_path_gain"] = gain
            m["slow_path_total"] = total
            m["slow_path_reduction"] = gain / total if total else None
            # Multi-intent detection via noul>=0.5.
            det = sum(1 for g in multi_gold if (by_r[g["qid"]].get("multi_intent") or 0) >= 0.5)
            m["multi_intent_recall"] = det / len(multi_gold) if multi_gold else None
        out[name] = m
    if out.get("baseline") and out.get("jev"):
        b, j = out["baseline"], out["jev"]
        if b["tool_accuracy_strict"] is not None and j["tool_accuracy_strict"] is not None:
            out["tool_accuracy_delta"] = j["tool_accuracy_strict"] - b["tool_accuracy_strict"]
    return out


def cmd_baseline(args):
    gold, _ = load_golden()
    items = [g for g in gold if args.split == "all" or g["split"] == args.split]
    if args.limit:
        items = items[: args.limit]
    rows = run_baseline(items)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    path = ARTIFACT_DIR / f"baseline_{args.split}.json"
    path.write_text(json.dumps({"rows": rows}, indent=2))
    print(f"baseline: {len(rows)} rows -> {path}")


def cmd_jev(args):
    gold, paras = load_golden()
    items = [g for g in gold if args.split == "all" or g["split"] == args.split]
    if args.limit:
        items = items[: args.limit]
    rows = run_jev(items, model=args.model)
    if rows is None:
        print("TYPESAFE_ACCESS=BLOCKED (no TYPESAFE_API_KEY); no Jev calls made")
        return 2
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    path = ARTIFACT_DIR / f"jev_{args.split}_{args.model.replace('/', '_')}.json"
    path.write_text(json.dumps({"rows": rows, "model_requested": args.model}, indent=2))
    print(f"jev: {len(rows)} rows -> {path}")
    if args.paraphrases:
        prows = []
        from festival_bloomberg.intelligence.jev import JevClient
        from festival_bloomberg.intelligence.jev_routing import route_question
        client = JevClient(model=args.model)
        conn = fixture_conn()
        for qid, variants in paras.items():
            for v in variants:
                pred = route_question(client, conn, v)
                blob = json.dumps(pred, default=str)
                prows.append({"qid": qid, "variant": v,
                              "status": pred.get("status"),
                              "predicted_tool": pred.get("primary_tool"),
                              "primary_confidence": pred.get("primary_confidence"),
                              "unsafe": pred.get("unsafe"),
                              "multi_intent": pred.get("multi_intent"),
                              "latency_ms": pred.get("latency_ms"),
                              "invented_id": ("mbid::" in blob or "eval::" in blob)})
        conn.close()
        ppath = ARTIFACT_DIR / f"jev_paraphrases_{args.split}.json"
        ppath.write_text(json.dumps({"rows": prows}, indent=2))
        print(f"paraphrases: {len(prows)} rows -> {ppath}")


def cmd_report(args):
    gold, _ = load_golden()
    if args.split != "all":
        gold = [g for g in gold if g["split"] == args.split]
    base = json.loads(Path(args.baseline).read_text())["rows"] if args.baseline else []
    jev = json.loads(Path(args.jev).read_text())["rows"] if args.jev else []
    metrics = report_metrics(gold, base, jev)
    metrics["question_sha256"] = hashlib.sha256(
        json.dumps(sorted(g["qid"] for g in gold)).encode()).hexdigest()[:16]
    print(json.dumps(metrics, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(metrics, indent=2))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("baseline")
    b.add_argument("--split", default="all", choices=["all", "cal", "held"])
    b.add_argument("--limit", type=int, default=0)
    j = sub.add_parser("jev")
    j.add_argument("--split", default="all", choices=["all", "cal", "held"])
    j.add_argument("--limit", type=int, default=0)
    j.add_argument("--model", default="jev-latest")
    j.add_argument("--paraphrases", action="store_true")
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
