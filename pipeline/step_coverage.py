r"""Notices when a flow silently runs fewer steps than it declares.

A "successful" Classic run of counter4 produces 74 step directories. The
Classic flow declares 78. The four that did not run are:

    OpenROAD.RepairDesignPostGRT     RUN_POST_GRT_DESIGN_REPAIR = False
    OpenROAD.ResizerTimingPostGRT    RUN_POST_GRT_RESIZER_TIMING = False
    Odb.HeuristicDiodeInsertion      RUN_HEURISTIC_DIODE_INSERTION = False
    Yosys.EQY                        RUN_EQY = False

Nothing in the log says so. There is no "skipping" line for any of them,
the flow reports success, and metrics.json contains no key for a check
that never happened — so the absence is invisible in exactly the way an
absent DRC metric was before score() started tracking `unverified`.

One of the four is a formal equivalence check. A pipeline that reports a
candidate as signed off while its equivalence check was switched off is
making a claim it did not verify.

This does not decide whether a given step *should* run. Three of the four
are off for defensible reasons and turning them on has real costs — and
`RUN_EQY=true` fails outright in this version (EQY aborts with its own
"This should not happen. Please report this bug."). The job here is only
to make the gap visible and attributable, so a decision is possible.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# Run directories are "NN-tool-stepname"; the id in the flow is
# "Tool.StepName". Comparison is on a normalized form because the
# directory spelling is lowercased and dot-free.
_STEP_DIR = re.compile(r"^(\d+)-(.+)$")

# Which config variable governs each step OpenLane can silently drop, so
# a missing step names the switch that dropped it rather than leaving a
# reader to grep for it. Read from a real resolved.json, not guessed.
GATING_VARS = {
    "Yosys.EQY": "RUN_EQY",
    "OpenROAD.RepairDesignPostGRT": "RUN_POST_GRT_DESIGN_REPAIR",
    "OpenROAD.ResizerTimingPostGRT": "RUN_POST_GRT_RESIZER_TIMING",
    "Odb.HeuristicDiodeInsertion": "RUN_HEURISTIC_DIODE_INSERTION",
    "OpenROAD.CheckAntennas": "RUN_ANTENNA_REPAIR",
    "KLayout.DRC": "RUN_KLAYOUT_DRC",
    "Magic.DRC": "RUN_MAGIC_DRC",
    "Netgen.LVS": "RUN_LVS",
    "KLayout.XOR": "RUN_KLAYOUT_XOR",
    "OpenROAD.CTS": "RUN_CTS",
    "OpenROAD.DetailedRouting": "RUN_DRT",
    "OpenROAD.IRDropReport": "RUN_IRDROP_REPORT",
}

# Steps whose absence is a signoff gap rather than an optimisation
# choice: each one is a correctness check, not a quality improvement.
SIGNOFF_STEPS = frozenset({
    "Yosys.EQY", "KLayout.DRC", "Magic.DRC", "Netgen.LVS", "KLayout.XOR",
    "OpenROAD.CheckAntennas",
})


def normalize(step_id: str) -> str:
    return step_id.lower().replace(".", "").replace("_", "").replace("-", "")


def executed_steps(run_dir: Path | str) -> list[tuple[int, str]]:
    """(number, name) for every step directory the run actually created."""
    out = []
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        return out
    for child in run_dir.iterdir():
        if not child.is_dir():
            continue
        m = _STEP_DIR.match(child.name)
        if m:
            out.append((int(m.group(1)), m.group(2)))
    return sorted(out)


def read_gating(run_dir: Path | str) -> dict:
    """The RUN_* switches this run resolved to, from its own
    resolved.json — the run's actual configuration, not the defaults."""
    path = Path(run_dir) / "resolved.json"
    if not path.is_file():
        return {}
    try:
        resolved = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return {k: v for k, v in resolved.items() if k.startswith("RUN_")}


def check(run_dir: Path | str, declared_steps: list[str]) -> dict:
    """Which declared steps did not run, and what turned each one off.

    `declared_steps` comes from the flow itself (see toolchain / the
    Classic step list); it is passed in rather than queried here so this
    stays a pure function over a finished run.
    """
    ran = {normalize(name) for _, name in executed_steps(run_dir)}
    gating = read_gating(run_dir)

    missing = []
    for step_id in declared_steps:
        if normalize(step_id) in ran:
            continue
        var = GATING_VARS.get(step_id)
        missing.append({
            "step": step_id,
            "gated_by": var,
            # None when we cannot attribute it: better an honest blank
            # than a guess at which switch was responsible.
            "value": gating.get(var) if var else None,
            "is_signoff": step_id in SIGNOFF_STEPS,
        })

    return {
        "declared": len(declared_steps),
        "executed": len(ran),
        "missing": missing,
        "missing_signoff": [m["step"] for m in missing if m["is_signoff"]],
    }


def unverified_steps(result: dict) -> list[str]:
    """Phrases for score()'s `unverified` list — signoff steps only.

    Optimisation steps that did not run make a design slower, not
    unverified, and reporting them the same way would bury the checks
    that matter under noise nobody acts on.
    """
    out = []
    for m in result.get("missing", []):
        if not m["is_signoff"]:
            continue
        why = f" ({m['gated_by']}={m['value']})" if m["gated_by"] else ""
        out.append(f"{m['step']} did not run{why}, so its check was never performed")
    return out
