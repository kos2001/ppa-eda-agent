r"""Uses OpenLane's own SynthesisExploration flow to pick synthesis strategies.

This pipeline explores SYNTH_STRATEGY the expensive way: it proposes one
candidate per strategy and runs each through the *full* Classic flow —
78 steps, 60-100 s apiece. counter4's nine candidates cost about nine
minutes to produce what is, in the end, an area-versus-slack table.

OpenLane ships a flow that produces that table directly. Its own
description: "tries multiple synthesis strategies (in the form of
different scripts for the ABC utility) to try and find which strategy is
better by either minimizing area or maximizing slack (and thus
frequency.)" It runs synthesis and pre-PnR STA only. Measured on
counter4: **8 seconds for all nine strategies**.

That does not replace the full runs — synthesis area is not post-route
area, and a strategy that wins here can still lose after placement. What
it replaces is running nine full flows to find out which two or three
are worth running full flows on.

Results are read from each strategy's own state_out.json (76 real
metrics each) rather than scraped from the terminal table the flow
prints, so a change to that table's formatting cannot silently produce
wrong numbers.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from toolchain import OPENLANE_IMAGE as IMAGE, platform_args
import operating_point

REPO_ROOT = Path(__file__).resolve().parent.parent
PDK_ROOT = REPO_ROOT / "pdk"

# The flow writes one directory per strategy per step:
#   1-sta-area-0, 1-sta-delay-3, ...
# The STA ones carry the timing metrics; the SDC ones are inputs.
_STA_DIR = re.compile(r"^\d+-sta-(area|delay)-(\d+)$")


class SynthExploreError(RuntimeError):
    pass


def strategy_name(dir_name: str) -> str | None:
    """"1-sta-delay-3" -> "DELAY 3", matching SYNTH_STRATEGY's own spelling."""
    m = _STA_DIR.match(dir_name)
    return f"{m.group(1).upper()} {m.group(2)}" if m else None


def read_results(run_dir: Path | str, clock_period_ns: float | None = None) -> list[dict]:
    """Per-strategy synthesis results from a completed exploration run.

    Fmax is derived with the same operating_point code the full pipeline
    uses, so the two cannot disagree about what a slack means.
    """
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        raise SynthExploreError(f"no such run directory: {run_dir}")

    out = []
    for child in sorted(run_dir.iterdir()):
        if not child.is_dir():
            continue
        name = strategy_name(child.name)
        if name is None:
            continue
        state_file = child / "state_out.json"
        if not state_file.is_file():
            continue
        metrics = (json.loads(state_file.read_text(encoding="utf-8")).get("metrics") or {})
        if not metrics:
            continue
        ws = metrics.get("timing__setup__ws")
        row = {
            "strategy": name,
            "gates": metrics.get("design__instance__count"),
            "area_um2": metrics.get("design__instance__area"),
            "setup_ws_ns": ws,
            "setup_tns_ns": metrics.get("timing__setup__tns"),
            "fmax_mhz": None,
        }
        op = operating_point.operating_point(metrics, clock_period_ns)
        if op:
            row["fmax_mhz"] = op["fmax_mhz"]
        elif clock_period_ns and isinstance(ws, (int, float)):
            # Pre-PnR STA may report only the aggregate, with no
            # per-corner keys for operating_point() to work from.
            period = clock_period_ns - ws
            row["fmax_mhz"] = (1000.0 / period) if period > 0 else None
        out.append(row)

    if not out:
        raise SynthExploreError(
            f"{run_dir}: no per-strategy STA results found — did the "
            f"SynthesisExploration flow actually run?"
        )
    return out


def explore(design_dir: Path | str, tag: str = "synth-explore",
            clock_period_ns: float | None = None) -> list[dict]:
    """Runs the real SynthesisExploration flow and returns its results."""
    design_dir = Path(design_dir).resolve()
    if not (design_dir / "config.json").exists():
        raise SynthExploreError(f"no config.json in {design_dir}")

    cmd = [
        "docker", "run", "--rm", *platform_args(),
        "-v", f"{PDK_ROOT}:/pdk",
        "-v", f"{design_dir}:/design",
        IMAGE,
        "openlane", "--pdk-root", "/pdk",
        "--flow", "SynthesisExploration",
        "--run-tag", tag, "--overwrite",
        "/design/config.json",
    ]
    print(f"$ {' '.join(cmd)}", file=sys.stderr)
    proc = subprocess.Popen(cmd, cwd=design_dir, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", bufsize=1)
    assert proc.stdout is not None
    captured = []
    for line in proc.stdout:
        captured.append(line)
        sys.stderr.write(line)
        sys.stderr.flush()
    if proc.wait() != 0:
        raise SynthExploreError(
            "SynthesisExploration failed. Tail:\n" + "".join(captured)[-1500:]
        )
    return read_results(design_dir / "runs" / tag, clock_period_ns)


def rank(results: list[dict], objective: str = "area") -> list[dict]:
    """Strategies best-first for one objective.

    "area" and "fmax" genuinely disagree — on counter4, AREA 0 is the
    smallest at 171 um^2 while DELAY 3 has the most slack at 195 um^2 —
    which is the whole reason to look rather than assume.
    """
    if objective == "area":
        keyed = [r for r in results if isinstance(r.get("area_um2"), (int, float))]
        return sorted(keyed, key=lambda r: r["area_um2"])
    if objective in ("fmax", "slack"):
        keyed = [r for r in results if isinstance(r.get("setup_ws_ns"), (int, float))]
        return sorted(keyed, key=lambda r: -r["setup_ws_ns"])
    raise SynthExploreError(f"unknown objective {objective!r}")


def suggest_candidates(results: list[dict], count: int = 3) -> list[dict]:
    """Candidate specs for run_spec.json, chosen rather than swept.

    Takes the best for area and the best for slack, then fills up to
    `count` with whatever else scores well — so the expensive full-flow
    runs go to the ends of the tradeoff plus a middle, instead of to all
    nine strategies indiscriminately.
    """
    picks: list[dict] = []
    seen: set[str] = set()

    def take(row, why):
        if row and row["strategy"] not in seen:
            seen.add(row["strategy"])
            picks.append({
                "tag": f"synth-{row['strategy'].lower().replace(' ', '')}",
                "overrides": {"SYNTH_STRATEGY": row["strategy"]},
                "why": why,
                "explored": row,
            })

    by_area = rank(results, "area")
    by_slack = rank(results, "fmax")
    take(by_area[0] if by_area else None, "smallest area in exploration")
    take(by_slack[0] if by_slack else None, "most setup slack in exploration")
    for row in by_slack[1:]:
        if len(picks) >= count:
            break
        take(row, "next-best slack in exploration")
    return picks[:count]


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--design", required=True, type=Path)
    ap.add_argument("--tag", default="synth-explore")
    ap.add_argument("--clock-period", type=float, default=None,
                    help="ns; enables Fmax in the output")
    ap.add_argument("--read-only", action="store_true",
                    help="parse an existing run instead of running the flow")
    args = ap.parse_args()

    period = args.clock_period
    if period is None:
        cfg = args.design / "config.json"
        if cfg.exists():
            value = json.loads(cfg.read_text(encoding="utf-8")).get("CLOCK_PERIOD")
            period = float(value) if isinstance(value, (int, float)) else None

    if args.read_only:
        results = read_results(args.design / "runs" / args.tag, period)
    else:
        results = explore(args.design, args.tag, period)

    print(json.dumps({
        "results": results,
        "best_area": rank(results, "area")[0]["strategy"],
        "best_slack": rank(results, "fmax")[0]["strategy"],
        "suggested_candidates": suggest_candidates(results),
    }, indent=2))


if __name__ == "__main__":
    main()
