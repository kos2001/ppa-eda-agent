r"""Tells a Magic DRC count on abstracts apart from one on geometry.

With `MAGIC_DRC_USE_GDS=false` — OpenLane's own documented setting for
OpenRAM sky130 macros — Magic checks DEF+LEF instead of the GDS. A
standard cell's LEF is an abstract: the N+ tap inside a tapvpwrvgnd
cell is not in it, so every row's nwell looks untapped and rule nwell.4
("All nwells must contain metal-connected N+ taps") fires once per row
segment. Measured on sram_wrapper (I-klayoutgds, 2026-09-10): 382 Magic
DRC errors, all nwell.4, none inside the macro's bbox, in strips one
row pitch apart in the standard-cell rows beside it — while KLayout DRC
on the same run's real GDS, macro included, reported zero.

Counting those 382 as violations would say the layout is bad. Counting
them as nothing would say Magic checked the geometry, which it did not.
They are the third thing this verdict already has a word for: a check
that could not be run on the geometry — *unverified* by Magic, with
KLayout DRC on the GDS named as the check that did see it.

Deliberately narrow. This reclassifies only when every reported rule is
nwell.4, Magic was told not to read the GDS, and not one box lies inside
a placed macro. A single box inside a macro, or any other rule, leaves
the count a violation: that is a claim nobody has measured a benign
cause for.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

NWELL_TAP_RULE = "nwell.4"

_BOX = re.compile(r"^\s*(-?[\d.]+)um\s+(-?[\d.]+)um\s+(-?[\d.]+)um\s+(-?[\d.]+)um\s*$")
_RULE_ID = re.compile(r"\(([\w.]+)\)\s*$")
_LEF_MACRO = re.compile(r"^MACRO\s+(\S+)(.*?)^END\s+\1", re.S | re.M)
_LEF_SIZE = re.compile(r"SIZE\s+([\d.]+)\s+BY\s+([\d.]+)")


def parse_report(text: str) -> dict[str, list[tuple[float, float, float, float]]]:
    """Rule id -> boxes, from Magic's drc_violations.magic.rpt.

    The report is the rule's full text, a dashed line, then one box per
    line in microns; the rule id is the parenthesised tail of the text.
    """
    out: dict[str, list] = {}
    rule = None
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("---") or s.startswith("[INFO]"):
            continue
        m = _BOX.match(line)
        if m:
            if rule is not None:
                out.setdefault(rule, []).append(tuple(float(v) for v in m.groups()))
            continue
        rid = _RULE_ID.search(s)
        rule = rid.group(1) if rid else s
        out.setdefault(rule, [])
    return {k: v for k, v in out.items() if v}


def lef_size(lef_text: str, macro: str) -> tuple[float, float] | None:
    for m in _LEF_MACRO.finditer(lef_text):
        if m.group(1) == macro:
            s = _LEF_SIZE.search(m.group(2))
            if s:
                return float(s.group(1)), float(s.group(2))
    return None


def macro_bboxes(resolved: dict, pdk_root: Path) -> list[tuple[str, tuple[float, float, float, float]]]:
    """(instance, bbox) for every placed macro in the run's resolved.json.

    Orientation is ignored on purpose: the macro's bbox for N and its
    rotations differ only in which of width/height is which, and the
    check below tolerates that by testing both. A box that is inside
    under either reading is treated as inside — erring towards leaving
    the count a violation.
    """
    out = []
    for name, spec in (resolved.get("MACROS") or {}).items():
        size = None
        for lef in spec.get("lef") or []:
            p = Path(str(lef).replace("/pdk/", str(pdk_root) + "/", 1)) \
                if str(lef).startswith("/pdk/") else Path(lef)
            if p.is_file():
                size = lef_size(p.read_text(encoding="utf-8", errors="replace"), name)
                if size:
                    break
        if not size:
            continue
        w, h = size
        for inst, place in (spec.get("instances") or {}).items():
            loc = place.get("location") or [None, None]
            if not (isinstance(loc[0], (int, float)) and isinstance(loc[1], (int, float))):
                continue
            x, y = float(loc[0]), float(loc[1])
            out.append((inst, (x, y, x + max(w, h), y + max(w, h))))
    return out


def _inside(box, bbox, tol: float = 1.0) -> bool:
    x0, y0, x1, y1 = box
    bx0, by0, bx1, by1 = bbox
    return bx0 - tol <= x0 and x1 <= bx1 + tol and by0 - tol <= y0 and y1 <= by1 + tol


def classify(rules: dict[str, list], use_gds: bool,
             macros: list[tuple[str, tuple]]) -> dict:
    """Whether a Magic DRC count is the abstract artefact and nothing else."""
    total = sum(len(v) for v in rules.values())
    if total == 0:
        return {"abstract_artefact": False, "reason": "no Magic DRC errors", "count": 0}
    if use_gds:
        return {"abstract_artefact": False, "count": total,
                "reason": "Magic read the GDS; these are geometry errors"}
    other = sorted(r for r in rules if r != NWELL_TAP_RULE)
    if other:
        return {"abstract_artefact": False, "count": total,
                "reason": f"rules other than {NWELL_TAP_RULE} reported: {', '.join(other)}"}
    inside = [(inst, box) for box in rules[NWELL_TAP_RULE]
              for inst, bbox in macros if _inside(box, bbox)]
    if inside:
        return {"abstract_artefact": False, "count": total,
                "reason": f"{len(inside)} {NWELL_TAP_RULE} box(es) inside a macro "
                          f"({inside[0][0]}) — not the standard-cell-row artefact"}
    return {
        "abstract_artefact": True,
        "count": total,
        "rule": NWELL_TAP_RULE,
        "macros_checked": [inst for inst, _ in macros],
        "reason": (f"MAGIC_DRC_USE_GDS=false: Magic checked DEF/LEF abstracts, and "
                   f"all {total} errors are {NWELL_TAP_RULE} on standard-cell rows "
                   f"outside every placed macro — the taps live inside cell "
                   f"abstracts Magic could not see. KLayout DRC on the GDS is the "
                   f"geometry check for this run."),
    }


def check(run_dir: Path | str, pdk_root: Path | str) -> dict | None:
    """Classification for a completed run, or None when Magic DRC left no
    report to read (nothing to reclassify)."""
    run_dir = Path(run_dir)
    reports = sorted(run_dir.glob("*-magic-drc/reports/drc_violations.magic.rpt"))
    if not reports:
        return None
    try:
        resolved = json.loads((run_dir / "resolved.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        resolved = {}
    use_gds = resolved.get("MAGIC_DRC_USE_GDS", True)
    rules = parse_report(reports[-1].read_text(encoding="utf-8", errors="replace"))
    result = classify(rules, bool(use_gds), macro_bboxes(resolved, Path(pdk_root)))
    result["report"] = str(reports[-1])
    result["per_rule"] = {k: len(v) for k, v in rules.items()}
    return result


def apply_to_verdict(verdict: dict, result: dict | None) -> dict:
    """Moves an abstract-artefact Magic count from violations to unverified."""
    if not result or not result.get("abstract_artefact"):
        return verdict
    label = "Magic DRC error(s)"
    kept = [v for v in verdict.get("violations", []) if not v.endswith(label)]
    if len(kept) == len(verdict.get("violations", [])):
        return verdict  # nothing to move
    verdict["violations"] = kept
    verdict.setdefault("unverified", []).append(
        f"Magic DRC on abstracts: {result['count']} {result['rule']} on standard-cell "
        f"rows, none inside a macro (KLayout DRC on the GDS is the geometry check)")
    verdict["passed"] = not verdict["violations"] and not verdict["unverified"]
    verdict["magic_abstract_drc"] = result
    return verdict
