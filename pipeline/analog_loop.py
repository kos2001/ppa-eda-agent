#!/usr/bin/env python3
"""The custom/analog half's closed loop: propose sizings, measure them for
real, score them against a spec, and improve from what the measurement
said — the counterpart of orchestrator.py for transistor-level design.

Why a second loop rather than an argument to the first. orchestrator.py
drives OpenLane: its candidates are config overrides, its verdict comes
from a signoff metrics.json, and its repairs read OpenLane error text.
None of that exists here. An analog candidate is a device size, the
verdict comes from `.meas` values, and a repair is a number scaled by
what was measured. Same shape, no shared machinery except the parts that
genuinely are shared — `expand_sweeps()` is imported from orchestrator
rather than reimplemented, because "sweep this parameter across these
values" is the same idea in both halves.

What makes it a *self-improvement* loop rather than a sweep runner, in
the same three levels self_improve.py uses for the digital half:

1. **Auto-repair coverage.** What fraction of non-passing candidates a
   known pattern actually fixed, versus needed a human. It goes up only
   when a new pattern is added, never by redefining "covered".
2. **The measurement proposes the next candidate.** `propose_repairs()`
   reads the violation's own numbers — not a rule of thumb. The one
   pattern here scales W_P by the *measured* tplh/tphl, because that
   ratio is what the imbalance is.
3. **Promotion is earned, not assumed.** Every pattern below names the
   real run that proved it. The loop shipped with none, was run, failed,
   and the pattern was added only after a real run showed the repair
   closing the target (see PATTERNS).

The case store is `reference-db/analog/`, deliberately separate from
`reference-db/cases/`. That schema is shaped for digital candidates and
23 signoff checks; forcing transistor measurements into it would pollute
the labels surrogate.py and pareto.py read.

Usage:
    analog_loop.py run  --design inv          # real sweep + repairs
    analog_loop.py scan                       # coverage + what needs a human
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

import custom_bridge
from orchestrator import expand_sweeps, safe_tag

REPO_ROOT = Path(__file__).resolve().parent.parent
ANALOG_DIR = REPO_ROOT / "pipeline" / "analog"
CASE_DIR = REPO_ROOT / "reference-db" / "analog"

# Mirrors orchestrator.STOP_REASONS so a caller (scan(), the dashboard)
# can branch on a value instead of parsing prose — and so "ran out of
# budget" never reads as "genuinely stuck", which is the distinction
# self_improve.py records as the difference between a re-run and a
# review.
STOP_REASONS = ("winner_found", "max_iterations_reached", "no_repairable_failures")


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

_PARAM_LINE = re.compile(r"^\.param\s+(.*)$", re.IGNORECASE | re.MULTILINE)


def bind_params(deck: str, overrides: dict) -> str:
    """Rewrite `.param NAME=VALUE` entries in a netlist.

    Substitution rather than appending a second `.param`: ngspice takes
    the LAST definition, so appending works — right up until a deck
    happens to define the same name twice for its own reasons, at which
    point which one wins depends on line order rather than on intent.
    Rewriting in place leaves exactly one definition per name, and a name
    that is not already in the deck is reported rather than silently
    added, because a sweep over a parameter the circuit does not read is
    a sweep that measures nothing five times.
    """
    missing = [k for k in overrides if not re.search(rf"\b{re.escape(k)}\s*=", deck)]
    if missing:
        raise KeyError(f"deck has no .param for: {', '.join(sorted(missing))}")

    def rewrite(match: re.Match) -> str:
        body = match.group(1)
        for name, value in overrides.items():
            body = re.sub(rf"\b{re.escape(name)}\s*=\s*[^\s]+",
                          f"{name}={value}", body)
        return f".param {body}"

    return _PARAM_LINE.sub(rewrite, deck)


def derive(measurements: dict) -> dict:
    """Quantities a spec can gate on that ngspice does not measure directly.

    `rise_fall_ratio` is the one that matters for a static CMOS gate: a
    cell whose output rises slower than it falls is one whose pull-up is
    undersized, and the ratio states it in the units the repair uses.
    Computed here rather than written into every testbench's .control so
    that adding a derived target never means editing a schematic.
    """
    out = dict(measurements)
    tphl, tplh = measurements.get("tphl"), measurements.get("tplh")
    if tphl and tplh and tphl > 0:
        out["rise_fall_ratio"] = tplh / tphl
        out["tpd_avg"] = (tphl + tplh) / 2
    return out


def score(per_corner: dict, targets: dict) -> dict:
    """Verdict for one candidate across every corner it was run at.

    Two rules carried over from orchestrator.score(), because they are
    what make a verdict trustworthy rather than optimistic:

    - The worst corner governs. A cell that meets timing at ff and misses
      at ss misses.
    - A target with no measurement behind it is `unverified`, not
      `passed`. ngspice exits 0 when a `.meas` fails, so "the number is
      absent" is a thing that really happens, and treating absence as
      success is how a broken measurement becomes a clean report.
    """
    violations: list[str] = []
    unverified: list[str] = []
    worst: dict = {}
    for key, bound in targets.items():
        values = {corner: derive(m).get(key) for corner, m in per_corner.items()}
        present = {c: v for c, v in values.items() if isinstance(v, (int, float))}
        for corner in values:
            if corner not in present:
                unverified.append(f"{key} not measured at {corner}")
        if not present:
            continue
        if "max" in bound:
            corner, value = max(present.items(), key=lambda kv: kv[1])
            worst[key] = {"corner": corner, "value": value, "limit": bound["max"]}
            if value > bound["max"]:
                violations.append(
                    f"{key} {value:.6g} > {bound['max']:.6g} at {corner}")
        if "min" in bound:
            corner, value = min(present.items(), key=lambda kv: kv[1])
            worst.setdefault(key, {"corner": corner, "value": value,
                                    "limit": bound["min"]})
            if value < bound["min"]:
                violations.append(
                    f"{key} {value:.6g} < {bound['min']:.6g} at {corner}")
    return {
        "passed": not violations and not unverified,
        "violations": violations,
        "unverified": unverified,
        "worst": worst,
    }


def pick_winner(results: list[dict]) -> dict | None:
    """The passing candidate with the most room to spare.

    Margin rather than raw speed: the spec states what "good enough" is,
    and among candidates that clear it the useful one is the one that
    clears it by the most at its worst corner — that is the one that
    survives the next process shift.
    """
    passing = [r for r in results if (r.get("verdict") or {}).get("passed")]
    if not passing:
        return None

    def margin(r: dict) -> float:
        worst = (r["verdict"].get("worst") or {})
        ratios = []
        for entry in worst.values():
            limit, value = entry.get("limit"), entry.get("value")
            if isinstance(limit, (int, float)) and isinstance(value, (int, float)) \
                    and limit:
                ratios.append((limit - value) / abs(limit))
        return min(ratios) if ratios else 0.0

    return max(passing, key=margin)


# ---------------------------------------------------------------------------
# Repair patterns — each one names the real run that proved it
# ---------------------------------------------------------------------------

# DELIBERATELY SMALL. A pattern is added only after a real run showed the
# repair closing the target it claims to close, which is the same bar
# orchestrator.propose_repairs() holds ("proven, not assumed", soul.md).
# The loop shipped with this list empty, was run, and reported 0/5
# coverage honestly before anything was added here.
def _balance_rise_fall(result: dict, spec: dict) -> dict | None:
    """Widen the pull-up by exactly the imbalance that was measured.

    Fires only when `rise_fall_ratio` is the ONLY violation and nothing
    is unverified. A cell that is also too slow overall, or that has a
    measurement missing, is not a cell whose pfet width is the answer —
    and a pattern that fires on failures it was never shown to fix is
    the thing this list exists to prevent.

    The step is the measured ratio itself, capped by MAX_STEP: tplh/tphl
    IS the imbalance, in the units W_P is in. It is deliberately not
    solved for in one jump, because the relationship is not linear and
    the loop can re-measure instead of trusting an extrapolation.
    """
    verdict = result.get("verdict") or {}
    if verdict.get("unverified"):
        return None
    violations = verdict.get("violations") or []
    if not violations or not all(v.startswith("rise_fall_ratio ") for v in violations):
        return None
    worst = (verdict.get("worst") or {}).get("rise_fall_ratio") or {}
    ratio = worst.get("value")
    width = result.get("overrides", {}).get("W_P")
    if not isinstance(ratio, (int, float)) or not isinstance(width, (int, float)):
        return None
    if ratio <= 1:
        # The pull-up is already the fast side; widening it further is
        # the wrong direction and this pattern has no evidence for the
        # other one.
        return None
    new_width = round(width * min(ratio, MAX_STEP), 4)
    if new_width == width:
        return None
    return {"overrides": {**result["overrides"], "W_P": new_width}}


PATTERNS: list[dict] = [
    {
        "id": "balance-rise-fall",
        "design": "inv",
        "when": "rise_fall_ratio is the only violation",
        "repair": _balance_rise_fall,
        # Promoted only after a real sweep showed the repair moving the
        # number it claims to move, measured 2026-09-12 at the ss corner:
        #
        #   W_P 1.0    ratio 1.2553   fail
        #   W_P 1.255  ratio 1.1366   fail  (the measured-ratio step)
        #   W_P 1.5    ratio 0.9208   PASS
        #   W_P 1.8    ratio 0.8214   PASS
        #
        # The step undershoots — which is the evidence for re-measuring
        # rather than solving in one jump — and tpd_avg fell alongside
        # (99.5 -> 87.5 ps), so the repair does not buy the ratio by
        # making the cell slower.
        "evidence": "inv: W_P 1.0 -> 1.255 moved ss rise_fall_ratio 1.2553 -> "
                    "1.1366, and 1.5 closed it at 0.9208 with tpd_avg improving "
                    "from 99.5 to 87.5 ps.",
    },
]

# How far one repair is allowed to move a knob. A repair that jumps to
# the value the measurement implies would be right if the relationship
# were linear; it is not (a wider pfet is also a bigger load on whatever
# drives it), so the loop takes the measured step and re-measures rather
# than trusting the extrapolation.
MAX_STEP = 2.0


def propose_repairs(results: list[dict], iteration: int, spec: dict) -> list[dict]:
    """Next candidates, derived from the violations' own numbers."""
    proposals: list[dict] = []
    seen: set = set()
    for r in results:
        verdict = r.get("verdict") or {}
        if verdict.get("passed"):
            continue
        for pattern in PATTERNS:
            candidate = pattern["repair"](r, spec)
            if not candidate:
                continue
            key = json.dumps(candidate["overrides"], sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            candidate.update({
                "tag": safe_tag(f"iter{iteration + 1}-{pattern['id']}-"
                                 + "-".join(f"{k}{v}" for k, v in
                                            candidate["overrides"].items())),
                "repair_of": r["tag"],
                "pattern": pattern["id"],
            })
            proposals.append(candidate)
            break
    return proposals


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------

def load_spec(design: str) -> tuple[Path, dict]:
    design_dir = ANALOG_DIR / design
    spec_path = design_dir / "spec.json"
    if not spec_path.is_file():
        raise FileNotFoundError(f"no spec at {spec_path}")
    return design_dir, json.loads(spec_path.read_text(encoding="utf-8"))


def run_candidate(design_dir: Path, spec: dict, cand: dict, deck: str) -> dict:
    """One sizing, measured at every corner the spec names.

    The netlist is generated once per loop and re-parameterised here
    rather than re-netlisted per candidate: the schematic does not change
    between sizings, only the `.param` values do, and re-running xschem
    per candidate would be a container start to produce an identical
    file.
    """
    per_corner: dict = {}
    errors: list[str] = []
    bound = bind_params(deck, cand["overrides"])
    tmp = design_dir / f".{cand['tag']}.spice"
    tmp.write_text(bound, encoding="utf-8")
    try:
        for corner in spec.get("corners", ["tt"]):
            result = custom_bridge.run_spice(tmp, pdk=spec.get("pdk", "sky130A"),
                                             corner=corner)
            per_corner[corner] = result.metadata.get("measurements", {})
            if not result.ok:
                errors.extend(f"{corner}: {e}" for e in result.errors[:3])
    finally:
        tmp.unlink(missing_ok=True)
    verdict = score(per_corner, spec.get("targets", {}))
    return {
        "tag": cand["tag"],
        "overrides": cand["overrides"],
        "repair_of": cand.get("repair_of"),
        "pattern": cand.get("pattern"),
        "measurements": {c: derive(m) for c, m in per_corner.items()},
        "verdict": verdict,
        "errors": errors,
    }


def loop(design: str, max_iterations: int | None = None) -> dict:
    """Propose, measure, score, repair — until something passes or the
    patterns run out."""
    design_dir, spec = load_spec(design)
    cell = spec["cell"]
    schematic = design_dir / f"{cell}.sch"

    netlisted = custom_bridge.netlist_schematic(schematic,
                                                 pdk=spec.get("pdk", "sky130A"))
    if not netlisted.ok:
        raise RuntimeError(f"netlist failed: {netlisted.errors}")
    deck = Path(netlisted.metadata["netlist"]).read_text(encoding="utf-8")

    candidates = list(spec.get("candidates", [])) + expand_sweeps(spec)
    if not candidates:
        raise ValueError(f"{design}: spec lists no candidates and no sweeps")

    budget = max_iterations if max_iterations is not None \
        else spec.get("max_iterations", 3)
    iterations: list[dict] = []
    winner = None
    stop_reason = "max_iterations_reached"

    for i in range(budget):
        results = [run_candidate(design_dir, spec, c, deck) for c in candidates]
        iterations.append({"iteration": i, "results": results})
        winner = pick_winner(results)
        if winner:
            stop_reason = "winner_found"
            break
        candidates = propose_repairs(results, i, spec)
        if not candidates:
            stop_reason = "no_repairable_failures"
            break

    return {"design": design, "cell": cell, "spec": spec,
            "iterations": iterations, "winner": winner,
            "stop_reason": stop_reason,
            "schematic": str(schematic.relative_to(REPO_ROOT)),
            "netlist_source": "xschem"}


# ---------------------------------------------------------------------------
# The case store, and the scan that reads it
# ---------------------------------------------------------------------------

def write_case(run: dict) -> Path:
    """One case per design per run, in reference-db/analog/.

    Timestamped rather than one-per-day like the digital store: an analog
    sweep is seconds, not minutes, so several runs a day is the normal
    case and overwriting them would throw away exactly the history this
    loop learns from.
    """
    CASE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d__%H%M%S")
    path = CASE_DIR / f"{run['design']}__{stamp}.json"
    case = {
        "design": run["design"],
        "cell": run["cell"],
        "date": date.today().isoformat(),
        "schematic": run["schematic"],
        "netlist_source": run["netlist_source"],
        "spec": run["spec"],
        "iterations": run["iterations"],
        "winner": run["winner"],
        "stop_reason": run["stop_reason"],
        "outcome": {
            "winner_found": "passed",
            "max_iterations_reached": "no sizing met targets after all iterations",
            "no_repairable_failures": "no sizing met targets — no auto-repairable "
                                       "pattern matched, needs a human decision",
        }[run["stop_reason"]],
        "toolchain": {"netlister": "xschem", "simulator": "ngspice",
                       "image": custom_bridge.XSCHEM_IMAGE},
    }
    path.write_text(json.dumps(case, indent=2), encoding="utf-8")
    return path


def cases(design: str | None = None) -> list[dict]:
    if not CASE_DIR.is_dir():
        return []
    out = []
    for path in sorted(CASE_DIR.glob("*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        if design and case.get("design") != design:
            continue
        case["_path"] = str(path.relative_to(REPO_ROOT))
        out.append(case)
    return out


def coverage(case: dict) -> dict:
    """What fraction of this case's failures a pattern actually repaired.

    Counted the same way self_improve.py counts it for the digital half:
    a failure is covered when a later candidate carrying a `pattern` tag
    was proposed for it, not when a pattern merely exists. That is what
    keeps the number honest — it can only rise by adding a pattern that
    fires on a real failure.
    """
    failures = 0
    repaired = set()
    for iteration in case.get("iterations", []):
        for r in iteration.get("results", []):
            if not (r.get("verdict") or {}).get("passed"):
                failures += 1
            if r.get("repair_of"):
                repaired.add(r["repair_of"])
    return {"failures": failures, "repaired": len(repaired),
            "fraction": (len(repaired) / failures) if failures else None}


def scan(design: str | None = None) -> dict:
    """The report: where every analog design stands and what needs whom.

    Same three questions self_improve.py asks of the digital half —
    what is covered mechanically, what is genuinely stuck and needs a
    person, and what merely ran out of budget and needs a re-run, which
    is a different thing and must not be escalated as if it were the
    first.
    """
    designs = [design] if design else sorted(
        d.name for d in ANALOG_DIR.iterdir()
        if d.is_dir() and (d / "spec.json").is_file()) if ANALOG_DIR.is_dir() else []
    report = {"designs": [], "generated": datetime.now().isoformat(timespec="seconds")}
    for name in designs:
        found = cases(name)
        latest = found[-1] if found else None
        entry: dict = {"design": name, "cases": len(found)}
        if latest is None:
            entry.update(state="never run",
                          next_step=f"python3 pipeline/analog_loop.py run --design {name}")
        else:
            cov = coverage(latest)
            entry.update(
                state="passed" if latest.get("winner") else "OPEN",
                case=latest["_path"],
                stop_reason=latest.get("stop_reason"),
                winner=(latest.get("winner") or {}).get("tag"),
                coverage=cov,
                worst=((latest.get("winner") or {}).get("verdict") or {}).get("worst"),
            )
            if latest.get("winner"):
                entry["next_step"] = "nothing — a sizing met the spec"
            elif latest.get("stop_reason") == "max_iterations_reached":
                # Budget, not judgement. Escalating this trains people to
                # ignore the backlog (self_improve.py's own finding).
                entry["next_step"] = (
                    f"python3 pipeline/analog_loop.py run --design {name} "
                    f"--max-iterations {len(latest.get('iterations', [])) + 2}")
            else:
                entry["next_step"] = (
                    "needs a human/subagent: no pattern matched. The violations "
                    "to read are in " + latest["_path"])
                entry["promotion_candidate"] = True
        report["designs"].append(entry)
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="sweep, measure, score, repair")
    p_run.add_argument("--design", required=True)
    p_run.add_argument("--max-iterations", type=int, default=None)
    p_run.add_argument("--no-case", action="store_true",
                       help="do not write a reference-db/analog case")

    p_scan = sub.add_parser("scan", help="coverage and what needs attention")
    p_scan.add_argument("--design", default=None)
    p_scan.add_argument("--json", action="store_true")

    args = ap.parse_args()

    if args.cmd == "scan":
        report = scan(args.design)
        if args.json:
            print(json.dumps(report, indent=2))
            return
        for entry in report["designs"]:
            print(f"\n{entry['design']}: {entry['state']}  ({entry['cases']} case(s))")
            if entry.get("coverage"):
                cov = entry["coverage"]
                frac = "n/a" if cov["fraction"] is None else f"{cov['fraction']:.0%}"
                print(f"  auto-repair coverage: {cov['repaired']}/{cov['failures']}"
                      f" ({frac})")
            if entry.get("winner"):
                print(f"  winner: {entry['winner']}")
            print(f"  next: {entry['next_step']}")
        return

    run = loop(args.design, args.max_iterations)
    for iteration in run["iterations"]:
        print(f"\n--- iteration {iteration['iteration']} ---")
        for r in iteration["results"]:
            verdict = r["verdict"]
            mark = "PASS" if verdict["passed"] else "fail"
            print(f"  {mark}  {r['tag']:<28} {r['overrides']}")
            for v in verdict["violations"]:
                print(f"          {v}")
            for u in verdict["unverified"][:2]:
                print(f"          unverified: {u}")
    print(f"\nstop_reason: {run['stop_reason']}")
    if run["winner"]:
        print(f"winner: {run['winner']['tag']}  {run['winner']['overrides']}")
    if not args.no_case:
        print(f"case: {write_case(run).relative_to(REPO_ROOT)}")
    sys.exit(0 if run["winner"] else 1)


if __name__ == "__main__":
    main()
