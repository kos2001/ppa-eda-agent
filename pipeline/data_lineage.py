r"""Where the data comes from, what it becomes, and who reads it.

The console could already show whether the pipeline is healthy —
SystemHealth reports dataset size, retrieval coverage and whether a
surrogate is trainable. What it could not show is the *path*: a run
produces a case, cases become rows, rows become features, and two
separate consumers read the result for two different purposes. Someone
asking "is this actually learning from what it runs?" had to read four
modules to find out.

This assembles that path in one call, in the order the data moves:

    1. collected   what was actually run — designs, technologies, knobs
    2. stored      cases on disk, and what deduplication does to them
    3. featurized  which columns a model can see, and which are empty
    4. retrieved   the two RAG paths: precedent by case, guidance by
                   measurement
    5. learned     what the surrogate can predict, with its interval

Every number is read from the real store rather than tracked separately,
so this cannot drift from what the pipeline actually holds. It is also
read-only and runs no flows.

WHAT IT IS NOT. Not a health check — SystemHealth stays the place that
says whether something needs attention. This says what exists and where
it goes, which is a different question and was the one with no answer.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import case_retrieval
import surrogate
import tool_retrieval

REPO_ROOT = Path(__file__).resolve().parent.parent
REFDB = REPO_ROOT / "reference-db"


def collected(rows: list[dict]) -> dict:
    """What was run: designs against technologies, and which knobs moved."""
    by_design: dict[str, Counter] = defaultdict(Counter)
    knobs: Counter = Counter()
    pdks: Counter = Counter()
    for row in rows:
        scl = row.get("scl") or surrogate.DEFAULT_SCL
        by_design[row["design"]][scl] += 1
        pdks[row.get("pdk") or surrogate.DEFAULT_PDK] += 1
        for key in (row.get("overrides") or {}):
            knobs[key] += 1

    designs = []
    for name in sorted(by_design):
        libs = by_design[name]
        completed = sum(1 for r in rows
                        if r["design"] == name and r.get("area_um2") is not None)
        designs.append({
            "design": name,
            "rows": sum(libs.values()),
            "completed": completed,
            "libraries": dict(sorted(libs.items())),
        })
    return {
        "designs": designs,
        "technologies": dict(sorted(
            Counter((r.get("scl") or surrogate.DEFAULT_SCL) for r in rows).items())),
        "pdks": dict(sorted(pdks.items())),
        "knobs_swept": dict(knobs.most_common()),
    }


def stored() -> dict:
    """Cases on disk, and what deduplication removes.

    The gap between the two is the point: a re-run records the same
    configuration again, and counting those as independent samples would
    inflate any accuracy figure.
    """
    cases_dir = REFDB / "cases"
    files = sorted(cases_dir.glob("*.json")) if cases_dir.is_dir() else []
    raw = 0
    for path in files:
        try:
            case = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        raw += sum(len(it.get("results", []))
                   for it in case.get("iterations", []))
    deduped = len(surrogate.load_dataset())
    return {
        "case_files": len(files),
        "recorded_runs": raw,
        "distinct_samples": deduped,
        "collapsed_by_dedup": raw - deduped,
        "dedup_key": ["design", "overrides", "scl", "pdk"],
        "layouts": len(list((REFDB / "layouts").glob("*.png")))
        if (REFDB / "layouts").is_dir() else 0,
    }


def featurized(rows: list[dict]) -> dict:
    """Which columns a model can see, and how many rows actually fill them.

    A feature with no rows is not hypothetical here: PL_TARGET_DENSITY_PCT
    sat in the feature list from the start with zero data, and scl
    arrived as data before it was a feature. Both are visible here.
    """
    if not rows:
        return {"features": [], "targets": []}
    # Featurized once per row, not once per (feature, row). The obvious
    # nested loop recomputed the whole feature dict for every column and
    # made this page's own request the slowest thing on the server.
    computed = [surrogate.featurize(r) for r in rows]
    names = sorted(computed[0])
    features = []
    for name in names:
        filled = sum(1 for f in computed if f.get(name) is not None)
        features.append({
            "feature": name,
            "rows_with_a_value": filled,
            "coverage_pct": round(100 * filled / len(rows), 1),
        })
    targets = []
    for name in surrogate.TARGETS:
        have = sum(1 for r in rows if r.get(name) is not None)
        targets.append({"target": name, "rows_with_a_value": have})
    return {"features": features, "targets": targets, "total_rows": len(rows)}


def retrieved() -> dict:
    """The two RAG paths, and what each can currently answer.

    They are different corpora answering different questions, which is
    why both exist: case_retrieval finds what happened before,
    tool_retrieval finds what to run about it.
    """
    try:
        corpus = case_retrieval.load_cases()
    except Exception:  # noqa: BLE001 - reported, never fatal
        corpus = []
    signatures: Counter = Counter()
    for case in corpus:
        for sig in case.get("shared_signatures", []) or []:
            signatures[sig] += 1

    entries = tool_retrieval.MEASUREMENTS
    sources = Counter(e["design"] for e in entries)
    return {
        "precedent": {
            "cases_indexed": len(corpus),
            "keyed_by": "shared EDA error codes, then topology distance",
        },
        "guidance": {
            "measurements_indexed": len(entries),
            "source_designs": dict(sorted(sources.items())),
            "signatures": sorted({w for e in entries for w in e["when"]}),
            # The property that decides whether retrieval is worth
            # anything: an index whose every entry came from one design
            # has nothing to offer that design.
            "single_source": len(sources) <= 1,
        },
    }


def learned(rows: list[dict]) -> dict:
    """What the surrogate can predict, with the interval on each claim."""
    out = []
    for target in surrogate.TARGETS:
        try:
            result = surrogate.evaluate(rows, target)
        except Exception as e:  # noqa: BLE001
            out.append({"target": target, "error": str(e)})
            continue
        interval = surrogate.win_rate_interval(result.get("fold_wins") or [])
        out.append({
            "target": target,
            "win_rate": result.get("win_rate"),
            "folds": result.get("n_scored"),
            "interval": {k: interval[k] for k in ("lo", "hi", "clears_threshold")}
            if interval else None,
            "k": surrogate.DEFAULT_K_BY_TARGET.get(target),
            "verdict": result.get("verdict"),
        })
    return {"targets": out, "threshold": surrogate.MIN_WIN_RATE}


def report() -> dict:
    """The whole path, in the order the data moves through it."""
    rows = surrogate.load_dataset()
    return {
        "collected": collected(rows),
        "stored": stored(),
        "featurized": featurized(rows),
        "retrieved": retrieved(),
        "learned": learned(rows),
    }


def main() -> None:
    print(json.dumps(report(), indent=2, default=str))


if __name__ == "__main__":
    main()
