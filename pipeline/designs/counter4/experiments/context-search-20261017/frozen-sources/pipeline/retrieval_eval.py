#!/usr/bin/env python3
r"""How well does retrieval find what is already written down?

Two retrieval layers feed what a model or a reviewer sees, and neither had a
number: "better" was an argument. This measures both on what the repo
actually holds.

ASK (service_qa.search). A gold set of questions, each with the files where
the answer genuinely lives (tests/retrieval_gold.json). Reported per split -
dev and test - because the changes were tuned on dev failures only, so test
says whether an improvement generalised or memorised. Metrics: hit@1, hit@3,
MRR, and for the negative questions the rate at which something is returned
anyway, which is the failure service_qa exists to prevent.

CASES (case_retrieval). Coverage rather than ranking quality, because there is
no ground truth for "the most useful precedent":
  - failing cases with no failure signature at all (retrieved by topology only),
  - failing cases with no earlier case sharing a signature - the cold start a
    reviewer actually faced, computed against only the cases dated before it.

Neither number is correctness of an answer. They say whether the right
passage or precedent is reachable, which is the precondition for a grounded
answer and nothing more.

Usage:
    retrieval_eval.py [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import case_retrieval
import service_qa

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLD = REPO_ROOT / "tests" / "retrieval_gold.json"
TOP = 4


def rank_of(hits: list[dict], accept: list[str]) -> int | None:
    """1-based rank of the first hit from an accepted file, or None."""
    for i, hit in enumerate(hits, 1):
        if any(hit["source"] == a or hit["source"].endswith(a) for a in accept):
            return i
    return None


def evaluate_ask(gold: dict | None = None, docs: list[dict] | None = None, top: int = TOP) -> dict:
    gold = gold or json.loads(GOLD.read_text(encoding="utf-8"))
    docs = docs if docs is not None else service_qa.load_corpus()
    out: dict = {"passages": len(docs), "sources": len({d["source"] for d in docs}), "splits": {}}
    for split in ("dev", "test", "all"):
        rows = [q for q in gold["questions"] if split == "all" or q["split"] == split]
        pos = [q for q in rows if not q.get("negative")]
        neg = [q for q in rows if q.get("negative")]
        ranks = [rank_of(service_qa.search(q["q"], docs, top), q["accept"]) for q in pos]
        misses = [q["q"] for q, r in zip(pos, ranks) if r is None]
        false_pos = [q["q"] for q in neg if service_qa.search(q["q"], docs, top)]
        n = len(pos) or 1
        out["splits"][split] = {
            "questions": len(pos), "negatives": len(neg),
            "hit@1": round(sum(1 for r in ranks if r == 1) / n, 3),
            "hit@3": round(sum(1 for r in ranks if r and r <= 3) / n, 3),
            f"hit@{top}": round(sum(1 for r in ranks if r) / n, 3),
            "mrr": round(sum(1 / r for r in ranks if r) / n, 3),
            "false_positive_rate": round(len(false_pos) / (len(neg) or 1), 3),
            "missed": misses, "answered_when_it_should_not": false_pos,
        }
    return out


def _date_key(case: dict) -> tuple:
    return (case.get("date") or "", case.get("_file") or "")


def evaluate_cases(corpus: list[dict] | None = None) -> dict:
    corpus = corpus if corpus is not None else case_retrieval.load_cases()
    failing = []
    for case in corpus:
        best = case_retrieval.best_candidate(case)
        if best is not None and not (best.get("verdict") or {}).get("passed"):
            failing.append(case)
    no_signature = [c["_file"] for c in failing if not case_retrieval.case_signatures(c)]
    cold = []
    for case in failing:
        sigs = case_retrieval.case_signatures(case)
        earlier = [c for c in corpus if _date_key(c) < _date_key(case)]
        if not any(sigs & case_retrieval.case_signatures(c) for c in earlier):
            cold.append(case["_file"])
    return {
        "cases": len(corpus), "failing": len(failing),
        "failing_with_no_signature": len(no_signature),
        "failing_cold_start": len(cold),
        "no_signature_cases": no_signature,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--misses", action="store_true", help="list the questions not found")
    args = ap.parse_args()
    ask, cases = evaluate_ask(), evaluate_cases()
    print(f"ASK: {ask['passages']} passages from {ask['sources']} sources")
    print(f"{'split':6s} {'n':>3s} {'hit@1':>6s} {'hit@3':>6s} {'hit@4':>6s} {'mrr':>6s} {'false+':>7s}")
    for name, s in ask["splits"].items():
        print(f"{name:6s} {s['questions']:>3d} {s['hit@1']:>6.2f} {s['hit@3']:>6.2f} "
              f"{s['hit@4']:>6.2f} {s['mrr']:>6.2f} {s['false_positive_rate']:>7.2f}")
    if args.misses:
        for q in ask["splits"]["all"]["missed"]:
            print("  not found:", q)
        for q in ask["splits"]["all"]["answered_when_it_should_not"]:
            print("  answered but should not have:", q)
    print(f"CASES: {cases['cases']} cases, {cases['failing']} failing; "
          f"{cases['failing_with_no_signature']} of them carry no failure signature, "
          f"{cases['failing_cold_start']} had no earlier case sharing one")
    if args.json:
        args.json.write_text(json.dumps({"ask": ask, "cases": cases}, indent=1, ensure_ascii=False),
                             encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
