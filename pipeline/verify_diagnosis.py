#!/usr/bin/env python3
"""Cross-checks a case's diagnosis prose against that case's own real
recorded data, flagging references that aren't grounded in it.

Idea borrowed from github.com/kos2001/strongarm-sizing-console's
`scripts/agent_selftest.py`, whose grading principle is the useful part:
an agent's answer is judged by *independent cross-validation against what
the backend actually measured*, not by string similarity to an expected
answer. That repo re-runs golden tasks through a live agent endpoint and
checks the numbers it claimed against a real simulation. This pipeline
has no always-on agent endpoint and its "answers" are the diagnoses
already stored in reference-db, so the same principle applies to the
artifacts directly: does this diagnosis reference things the run really
produced?

This matters here because the failure it guards has already happened for
real: sram_wrapper's first diagnosis blamed the macro's clk0/clk1 pins
without ever opening the .lib, and had to be rewritten after the actual
liberty file was read (the corrected text, and a note about the mistake,
are still in that case). A confident, plausible, ungrounded diagnosis is
the failure mode worth automating a check for.

DELIBERATELY NARROW — what this does NOT do:

It cannot tell you a diagnosis is *correct*. Reasoning about a real
physical root cause is exactly the judgment `request_review.py` escalates
to a human/subagent, and a regex claiming to settle it would be worse
than no check at all. What it checks is far weaker and actually
decidable: every EDA error code the prose cites must appear in the
case's own recorded errors, and every candidate tag it cites must have
been run by this design (by this case, or reported separately as from
another of its cases). That catches invented references and stale
copy-paste from another design — not wrong physics.

Usage:
    python3 verify_diagnosis.py                 # all designs
    python3 verify_diagnosis.py --design X
    python3 verify_diagnosis.py --strict        # exit 1 if anything ungrounded
"""
import argparse
import json
import re
import sys
from pathlib import Path

import case_store

REPO_ROOT = Path(__file__).resolve().parent.parent
REFDB = REPO_ROOT / "reference-db"

# Tool error codes as OpenLane/OpenROAD/Magic actually emit them
# (PDN-0185, RSZ-0090, GRT-0097, DRT-0001...). Four-digit suffix is the
# real shape of every code seen in this repo's cases so far.
ERROR_CODE_RE = re.compile(r"\b[A-Z]{2,4}-\d{4}\b")

# Candidate tags as this pipeline really generates them: a prefix from
# run_spec.json ("cand", "sweep", ...) then a hyphen. The hyphen is load
# bearing — without it the pattern also matches the ordinary English word
# "candidate", which is a false positive this checker hit on its first
# real run against sram_wrapper's diagnosis.
TAG_RE = re.compile(r"\b(?:cand|sweep)-[A-Za-z0-9_.]+(?:-[A-Za-z0-9_.]+)*\b")

# A hyphenated token as tags are written. The boundaries are ASCII on
# purpose: `\w` matches Hangul, so `aes-...-r9는` (a tag with a Korean
# particle attached, common in this store's prose) was never a token.
# A preceding `/` is allowed so `runs/<tag>/final` cites its tag.
TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])[A-Za-z0-9_.]+(?:-[A-Za-z0-9_.]+)+(?![A-Za-z0-9_-])")


def case_files() -> dict:
    index_file = REFDB / "index.json"
    if not index_file.exists():
        return {}
    return json.loads(index_file.read_text(encoding="utf-8"))


def diagnosis_text(case: dict) -> str:
    """All prose a human/subagent wrote about this case: the diagnosis
    field plus every human_in_the_loop review summary."""
    parts = [case.get("diagnosis") or ""]
    parts += [r.get("summary") or "" for r in case.get("human_in_the_loop", [])]
    return "\n".join(parts)


def recorded_evidence(case: dict) -> tuple[str, set]:
    """(all real recorded error text, all real candidate tags) for a case."""
    errors = []
    tags = set()
    for iteration in case.get("iterations", []):
        for result in iteration.get("results", []):
            tags.add(result.get("tag"))
            if result.get("error"):
                errors.append(result["error"])
    tags.discard(None)
    return "\n".join(errors), tags


_RECORDED_CACHE: dict = {}


def recorded_tags(refdb: Path | str | None = None) -> dict[str, set[str]]:
    """Every candidate tag reference-db has recorded, by design.

    TAG_RE alone recognises only `cand-` and `sweep-`, and most real tags
    look nothing like that: `c-hd-clock_period6`, `gf180-...`,
    `aes-closure-20261004-driver-r3-headroom12`. Over every committed
    case, a diagnosis citing one of those was never checked at all, while
    the report still said `checked: true`. The store already knows which
    tags are real. Cached on the index's bytes and each case file's mtime
    and size, so a long-running server sees a new or rewritten case.
    Unreadable state (an index caught mid-write) yields no tags rather
    than an exception: grounding is recorded beside a review, and must
    not stop one from being applied.
    """
    root = Path(refdb) if refdb is not None else REFDB
    try:
        raw = (root / "index.json").read_bytes()
        index = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError):
        return {}
    stamps = []
    for names in index.values():
        for name in names:
            try:
                st = (root / "cases" / name).stat()
            except OSError:
                continue
            stamps.append((name, st.st_mtime_ns, st.st_size))
    key = (str(root), raw, tuple(sorted(stamps)))
    if _RECORDED_CACHE.get("key") == key:
        return _RECORDED_CACHE["tags"]
    tags: dict[str, set[str]] = {}
    for design, names in index.items():
        for name in names:
            try:
                case = case_store.load_light(root / "cases" / name)
            except (OSError, json.JSONDecodeError):
                continue
            tags.setdefault(design, set()).update(recorded_evidence(case)[1])
    _RECORDED_CACHE.update(key=key, tags=tags)
    return tags


def tag_prefixes(tags: set[str]) -> set[str]:
    """The prefixes a new tag of this design would carry. A prefix that
    starts with a digit (a date: `2026-10-05-retry` is a legal tag) or
    is one letter (`c`, `G`) would make dates and `c-2` read as tags."""
    return {t.split("-", 1)[0] for t in tags if "-" in t
            if len(t.split("-", 1)[0]) > 1 and t[0].isalpha()}


# Tag prefixes that are also technology names (see cited_tags).
TECHNOLOGY_PREFIXES = {"sky130", "gf180", "tech"}


def cited_tags(prose: str, known: dict[str, set[str]], design: str | None,
               own_tags: set[str] = frozenset()) -> set[str]:
    """Tags the prose cites:

    - the `cand-`/`sweep-` shape (TAG_RE);
    - any hyphenated tag the store recorded (an exact match, so copy-
      paste such as `c-gf180mcu_7t-clock_period12` or `hs-util40` in aes
      is still caught), except another design's two-part tag on a
      technology prefix: `sky130-hd`, `gf180-7t` and `tech-hs` are also
      how people name a technology, and read as prose there;
    - a token starting with one of THIS design's prefixes with a digit
      after it (`aes-closure-20261004-r9`): how an invented tag looks.
      Other designs' prefixes are not used, so one new case elsewhere
      cannot turn this design's prose into citations, and the digit must
      follow the prefix, so `gf180-only` and `sky130-hd` stay prose.

    A file named after a tag (`<tag>.log`) cites the tag.
    """
    found = set(TAG_RE.findall(prose))
    # The case's own tags count even before the index lists its design.
    mine = (known.get(design, set()) if design else set()) | set(own_tags)
    every = {t for tags in known.values() for t in tags if "-" in t
             if not (t.count("-") == 1 and t.split("-")[0] in TECHNOLOGY_PREFIXES)}
    every |= {t for t in mine if "-" in t}
    own = tag_prefixes(mine)
    for token in TOKEN_RE.findall(prose):
        token = token.rstrip(".")
        stem = re.sub(r"\.[A-Za-z]{1,5}$", "", token)
        if token in every:
            found.add(token)
        elif stem in every:
            found.add(stem)
        else:
            prefix, _, rest = stem.partition("-")
            if prefix in own and any(ch.isdigit() for ch in rest):
                found.add(stem)
    return found


def verify_case(case: dict, known: dict[str, set[str]] | None = None) -> dict:
    prose = diagnosis_text(case)
    if not prose.strip():
        return {"checked": False, "reason": "no diagnosis or review text"}

    known = recorded_tags() if known is None else known
    error_text, real_tags = recorded_evidence(case)
    recorded_codes = set(ERROR_CODE_RE.findall(error_text))
    cited_codes = set(ERROR_CODE_RE.findall(prose))
    cited = cited_tags(prose, known, case.get("design"), real_tags)
    # Not in this case, but run by another case of the same design: a
    # verdict carried forward, or a proposal that was later executed.
    # Neither invented nor another design's, so shown and not flagged.
    same_design = known.get(case.get("design"), set())
    elsewhere = (cited - real_tags) & same_design

    return {
        "checked": True,
        "cited_error_codes": sorted(cited_codes),
        "ungrounded_error_codes": sorted(cited_codes - recorded_codes),
        "cited_candidate_tags": sorted(cited),
        "ungrounded_candidate_tags": sorted(cited - real_tags - same_design),
        "candidate_tags_from_other_cases": sorted(elsewhere),
        # False when the store's tags could not be read: only cand-/sweep-
        # and this case's own tags were recognised.
        "store_tags_available": bool(known),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--design", help="check only this design (default: all)")
    ap.add_argument("--strict", action="store_true",
                     help="exit 1 if any reference is ungrounded")
    args = ap.parse_args()

    index = case_files()
    designs = [args.design] if args.design else sorted(index)
    problems = 0

    print("=== diagnosis grounding check ===")
    print("(reference groundedness only — NOT a correctness check; see module docstring)\n")
    for design in designs:
        for name in index.get(design, []):
            case = case_store.load_light(REFDB / "cases" / name)
            report = verify_case(case)
            if not report["checked"]:
                print(f"{name}: skipped — {report['reason']}")
                continue
            bad_codes = report["ungrounded_error_codes"]
            bad_tags = report["ungrounded_candidate_tags"]
            status = "OK" if not (bad_codes or bad_tags) else "UNGROUNDED"
            print(f"{name}: {status}")
            print(f"  error codes cited: {report['cited_error_codes'] or '(none)'}")
            if bad_codes:
                problems += len(bad_codes)
                print(f"  !! cited but never recorded in this case's real errors: {bad_codes}")
            if report["cited_candidate_tags"]:
                print(f"  candidate tags cited: {report['cited_candidate_tags']}")
            if report["candidate_tags_from_other_cases"]:
                print(f"  tags run by another case of this design: "
                      f"{report['candidate_tags_from_other_cases']}")
            if bad_tags:
                problems += len(bad_tags)
                print(f"  !! cited tags that don't exist in this case: {bad_tags}")
            print()

    if problems:
        print(f"{problems} ungrounded reference(s) — read the diagnosis and either "
              f"correct it or confirm the reference came from a log the case "
              f"didn't capture.")
        return 1 if args.strict else 0
    print("all cited references are grounded in each case's own recorded data.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
