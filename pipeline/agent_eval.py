r"""Whether swapping the language model would actually change anything.

The question this answers came up as "would a better model improve
performance?", and it cannot be settled by argument, because "better"
and "performance" both need a number attached before they mean anything
here.

WHAT A MODEL SWAP CANNOT MOVE. Nothing in the decision path is a
language model. score(), pick_winner(), pareto, propose_repairs(),
model_validity and surrogate are deterministic Python reading real
OpenLane/OpenROAD/OpenSTA/Yosys output. Area, slack, power, DRC and the
winner are what the tools measured. Swapping the model changes none of
them, and this file does not pretend to test them.

WHAT IT CAN MOVE is the three jobs a model actually does here — draft a
review, diagnose a pasted report, translate — and of those only the
first two involve judgment. So that is what this measures, on the cases
already in reference-db, where the real answer is recorded.

THE SCORE, AND ITS LIMITS. Each case is replayed with its diagnosis
removed: the model sees only what the run actually produced, and is
asked what went wrong. Two things are then checked.

  grounded   — every EDA error code and candidate tag the answer cites
               appears in that case's own recorded data. This is
               verify_diagnosis's check, reused rather than reinvented,
               and it catches the failure that has already happened
               here for real: sram_wrapper's first diagnosis blamed the
               macro's clk pins without opening the .lib.

  recall     — how many of the root-cause terms the recorded diagnosis
               settled on appear in the answer.

Neither is correctness. A diagnosis can be perfectly grounded and wrong
about the physics, and recall rewards using the same words rather than
reaching the same conclusion. What the pair does support is a
*comparison*: run it, change PPA_EDA_DIRECT_LLM_MODEL, run it again, and
the difference is measured rather than asserted.

The honest prior, from this session: the sram_wrapper breakthrough came
from running report_checks — opening a file the previous analysis had
not opened — and not from reasoning harder about the files already read.
A stronger model does not open more files. Treat a large score jump with
suspicion until it survives a case whose diagnosis it could not have
seen.
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

import tool_retrieval
import verify_diagnosis

REPO_ROOT = Path(__file__).resolve().parent.parent
CASES = REPO_ROOT / "reference-db" / "cases"
DEFAULT_SERVER = "http://localhost:8123"

# Terms a diagnosis has to reach to count as having found the cause.
# Taken from what each case's own recorded diagnosis concluded, written
# down here rather than extracted, so the target cannot drift when the
# prose is edited.
ROOT_CAUSE_TERMS: dict[str, list[str]] = {
    "sram_wrapper": ["max_transition", "addr", "liberty", "slew"],
    "counter4_tinydie": ["DIE_AREA", "utilization", "placement"],
    "cdc_twoclock": ["clock", "domain", "synchron"],
}


class EvalError(RuntimeError):
    pass


def latest_case(design: str) -> Path | None:
    hits = sorted(CASES.glob(f"{design}__*.json"))
    return hits[-1] if hits else None


def prompt_for(case: dict, guidance: bool = False) -> str:
    """What the model is given: the run's own output, and no answer.

    With `guidance`, the retrieved measurement block is prepended — the
    same text request_review.py now puts in front of a reviewer. That
    makes the A/B the point of the exercise: does supplying which tool
    answers this, and which trap wastes the attempt, change the answer?

    It measures context supply, not tool use. A single chat call cannot
    run report_checks, so the ceiling here is whatever the trap text
    itself is worth. Do not read a gain as evidence the agent could have
    done the measurement.

    Deliberately the same evidence verify_diagnosis grades against — the
    error text and candidate tags the run really produced — so a model
    that cites something ungrounded invented it rather than being misled
    by a prompt that mentioned it.
    """
    errors, tags = verify_diagnosis.recorded_evidence(case)
    # Leave-one-out: an entry grounded in this very design is that
    # design's answer, and returning it would measure reading rather
    # than reasoning.
    block = (tool_retrieval.guidance_block(case, exclude_design=case.get("design"))
             if guidance else None)
    lines = ([block, ""] if block else []) + [
        f"Design: {case.get('design')}",
        f"Outcome: {case.get('outcome')}",
        f"Candidate tags that were run: {', '.join(sorted(tags)) or '(none)'}",
        "",
        "Tool output from the failing candidates:",
        errors[:6000] or "(no error text recorded)",
        "",
        "State the root cause in at most 150 words. Cite only error codes "
        "and candidate tags that appear above. If the output does not "
        "support a conclusion, say what measurement is missing instead of "
        "guessing.",
    ]
    return "\n".join(lines)


def ask(server: str, prompt: str, timeout: float = 400.0) -> tuple[str, float]:
    """One call through the local proxy, returning (answer, seconds).

    Through the server so the credential stays where it lives. The
    /diagnose route is the one that carries the analyst persona, which
    is the configuration actually in use.
    """
    # The server's own field name; it wraps this in its analyst prompt.
    body = json.dumps({"reportText": prompt, "lang": "en"}).encode()
    req = urllib.request.Request(
        f"{server}/diagnose", data=body,
        headers={"Content-Type": "application/json"})
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise EvalError(f"{e.code}: {e.read()[:300].decode('utf-8','replace')}")
    except OSError as e:
        raise EvalError(f"{server} unreachable: {e}")
    return collect(raw), time.time() - started


def collect(raw: str) -> str:
    """The text out of a server-sent-event stream."""
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload in ("", "[DONE]"):
            continue
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        for choice in chunk.get("choices", []):
            out.append((choice.get("delta") or {}).get("content") or "")
    return "".join(out).strip()


def recall(answer: str, design: str) -> tuple[int, int, list[str]]:
    """How many of the recorded root-cause terms the answer reaches."""
    terms = ROOT_CAUSE_TERMS.get(design, [])
    low = answer.lower()
    hit = [t for t in terms if t.lower() in low]
    return len(hit), len(terms), hit


def score_case(case: dict, answer: str) -> dict:
    grounding = verify_diagnosis.verify_case({**case, "diagnosis": answer})
    bad = (grounding.get("ungrounded_error_codes", [])
           + grounding.get("ungrounded_candidate_tags", []))
    hit, total, terms = recall(answer, case.get("design", ""))
    return {
        "grounded": not bad,
        "ungrounded": bad,
        "cited_codes": grounding.get("cited_error_codes", []),
        "recall": f"{hit}/{total}",
        "recall_terms": terms,
        "words": len(answer.split()),
    }


def run(designs: list[str], server: str, guidance: bool = False) -> dict:
    rows = []
    for design in designs:
        path = latest_case(design)
        if path is None:
            rows.append({"design": design, "error": "no recorded case"})
            continue
        case = json.loads(path.read_text())
        answer, seconds = ask(server, prompt_for(case, guidance))
        row = {"design": design, "case": path.name, "guidance": guidance,
               "seconds": round(seconds, 1)}
        row.update(score_case(case, answer))
        row["answer"] = answer
        rows.append(row)

    scored = [r for r in rows if "error" not in r]
    return {
        "server": server,
        "guidance": guidance,
        "cases": len(scored),
        "grounded": sum(1 for r in scored if r["grounded"]),
        "mean_seconds": (round(sum(r["seconds"] for r in scored) / len(scored), 1)
                         if scored else None),
        "rows": rows,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--designs", nargs="*",
                    default=sorted(ROOT_CAUSE_TERMS),
                    help="designs to replay (default: those with recorded terms)")
    ap.add_argument("--server", default=DEFAULT_SERVER)
    ap.add_argument("--with-guidance", action="store_true",
                    help="prepend the retrieved measurement block")
    ap.add_argument("--quiet", action="store_true",
                    help="omit the answers, keep the scores")
    args = ap.parse_args()

    got = run(args.designs, args.server, args.with_guidance)
    if args.quiet:
        for row in got["rows"]:
            row.pop("answer", None)
    print(json.dumps(got, indent=2, ensure_ascii=False))
    print(f"\ngrounded {got['grounded']}/{got['cases']} | "
          f"mean {got['mean_seconds']}s", flush=True)


if __name__ == "__main__":
    main()
