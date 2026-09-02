#!/usr/bin/env python3
"""Technology comparison: hold the design fixed, vary the technology,
report the real PPA delta.

Pattern borrowed from github.com/kos2001/analog-layout-optimizer's
`layout_opt/process_change.py` (a sibling analog project), whose useful
idea is a framing rather than an algorithm: when the *process* changes,
the schematic is fixed — same topology, same nets — while the physical
implementation must be rebuilt against the new rules, and what you want
out of it is a `before`/`after` pair plus an explicit record of what
stayed invariant. That module re-optimizes a differential pair's device
geometry; none of its analog machinery transfers, but the shape of the
question does.

Why this repo needed it: the dashboard calls itself a DTCO
(design-technology co-optimization) console, but every case in
reference-db so far varies only *design* knobs — FP_CORE_UTIL, DIE_AREA,
SYNTH_STRATEGY — against one fixed technology (sky130_fd_sc_hd). The
technology half of "co-optimization" was branding, not something the
pipeline had ever actually done. This closes that gap with the real
variants installed in this repo's own PDK.

The comparison is real end to end: each variant is a full OpenLane2 run
via run_stage.py and is judged by the same score() the rest of the
pipeline uses, so a technology's numbers are exactly as trustworthy as
any other case's. A variant that fails to build is reported as a failure
with its real error, never dropped — "this technology does not work for
this design yet" is a real DTCO finding, not an inconvenience to hide.

Usage:
    python3 tech_compare.py --design designs/counter4 \\
        --variants sky130_fd_sc_hd sky130_fd_sc_hs
"""
import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

from run_stage import run_stage, read_metrics
import orchestrator

REPO_ROOT = Path(__file__).resolve().parent.parent
REFDB = REPO_ROOT / "reference-db"

# The OpenLane variable that selects a standard-cell library, i.e. the
# technology offering itself — not a design choice.
TECH_VAR = "STD_CELL_LIBRARY"

# Config keys that describe the *design*, which a technology comparison
# must hold constant for the result to mean anything. Recorded in the
# report so a reader can confirm the comparison was fair rather than
# taking it on trust.
DESIGN_INVARIANT_KEYS = ("DESIGN_NAME", "VERILOG_FILES", "CLOCK_PORT",
                          "CLOCK_PERIOD", "FP_CORE_UTIL", "DIE_AREA")


def design_invariants(design_dir: Path) -> dict:
    config = json.loads((design_dir / "config.json").read_text(encoding="utf-8"))
    return {k: config[k] for k in DESIGN_INVARIANT_KEYS if k in config}


def cells_used(run_dir: Path) -> dict:
    """Which standard-cell libraries the *actual* netlist instantiates,
    counted from the real gate-level Verilog OpenLane wrote.

    This exists because of a real, silent failure hit while building this
    module: selecting the library with `--override-config
    STD_CELL_LIBRARY=<x>` is accepted, appears correctly in the run's
    resolved.json, and changes nothing — the netlist still contained only
    the default library's cells. The comparison then reported a perfect
    0.00% delta on area, utilization and power across two supposedly
    different technologies, which reads as a finding rather than a bug.
    Trusting the requested config was the mistake; this reads what was
    actually built."""
    counts: dict[str, int] = {}
    for netlist in (run_dir / "final" / "nl").glob("*.v"):
        for match in re.finditer(r"\bsky130_fd_sc_([a-z]+)__", netlist.read_text(encoding="utf-8")):
            lib = f"sky130_fd_sc_{match.group(1)}"
            counts[lib] = counts.get(lib, 0) + 1
    return counts


def run_variant(design_dir: Path, variant: str, targets: dict) -> dict:
    """One real OpenLane run of this design on one technology."""
    tag = f"tech-{variant}"
    print(f"\n=== technology '{variant}' ===", file=sys.stderr)
    try:
        run_dir = run_stage(design_dir, tag, to_step=None, overrides=[],
                             scl=variant)
        metrics = read_metrics(run_dir)
        used = cells_used(run_dir)
        result = {"variant": variant, "tag": tag,
                   "cells_used": used,
                   "verdict": orchestrator.score(metrics, targets)}
        # Guard the comparison itself, not just the run: if the netlist
        # is built from a library other than the one requested, every
        # delta computed against it is meaningless, and silently emitting
        # it would be worse than failing.
        if used and variant not in used:
            result["technology_not_applied"] = (
                f"requested {variant} but the netlist instantiates "
                f"{sorted(used)} — this run's numbers are not this "
                f"technology's")
        return result
    except Exception as e:  # noqa: BLE001 — a failed technology is a finding
        return {"variant": variant, "tag": tag, "error": str(e)}


def _metric(result: dict, key: str):
    return (result.get("verdict") or {}).get(key)


def _power_total(result: dict):
    power = (result.get("verdict") or {}).get("power") or {}
    return power.get("total_w")


def delta_vs_baseline(baseline: dict, other: dict) -> dict | None:
    """Real PPA delta between two technologies, or None when either side
    didn't produce metrics or didn't actually build on the technology it
    claimed. Percentages are only computed from two real measured values
    — never from a missing one treated as zero."""
    if baseline.get("error") or other.get("error"):
        return None
    if baseline.get("technology_not_applied") or other.get("technology_not_applied"):
        return None
    out = {}
    for name, getter in (("area_um2", lambda r: _metric(r, "area_um2")),
                          ("utilization", lambda r: _metric(r, "utilization")),
                          ("worst_setup_wns", lambda r: _metric(r, "worst_setup_wns")),
                          ("power_total_w", _power_total)):
        b, o = getter(baseline), getter(other)
        if b is None or o is None:
            continue
        entry = {"baseline": b, "variant": o, "abs_delta": round(o - b, 6)}
        if b:
            entry["pct_delta"] = round(100.0 * (o - b) / abs(b), 2)
        out[name] = entry
    return out


def compare(design_dir: Path, variants: list[str], targets: dict) -> dict:
    results = [run_variant(design_dir, v, targets) for v in variants]
    baseline = results[0]
    return {
        "design": design_dir.name,
        "date": date.today().isoformat(),
        "technology_variable": TECH_VAR,
        "baseline_variant": baseline["variant"],
        # What was deliberately held constant. Without this the deltas
        # below are uninterpretable — the point of the comparison is that
        # only the technology moved.
        "design_invariants": design_invariants(design_dir),
        "results": results,
        "deltas": {r["variant"]: delta_vs_baseline(baseline, r)
                    for r in results[1:]},
    }


def print_report(report: dict) -> None:
    print(f"\n=== technology comparison: {report['design']} ===")
    print(f"held fixed: {report['design_invariants']}")
    print(f"varied: {report['technology_variable']}\n")
    for r in report["results"]:
        if r.get("error"):
            first_line = r["error"].splitlines()[0]
            print(f"  {r['variant']}: FAILED TO BUILD — {first_line}")
            continue
        v = r["verdict"]
        status = "PASS" if v["passed"] else f"FAIL ({'; '.join(v['violations'])})"
        print(f"  {r['variant']}: {status} — area={v['area_um2']} um^2, "
              f"util={v['utilization']}, worst_setup_wns={v['worst_setup_wns']}")
        print(f"    cells actually used: {r.get('cells_used')}")
        if r.get("technology_not_applied"):
            print(f"    !! {r['technology_not_applied']}")
    for variant, delta in report["deltas"].items():
        if delta is None:
            print(f"\n  no delta for {variant} — one side did not produce metrics")
            continue
        print(f"\n  {variant} vs {report['baseline_variant']}:")
        for name, d in delta.items():
            pct = f" ({d['pct_delta']:+.2f}%)" if "pct_delta" in d else ""
            print(f"    {name}: {d['baseline']} -> {d['variant']}{pct}")


def write_report(report: dict) -> Path:
    out_dir = REFDB / "tech"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{report['design']}__{report['date']}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--design", required=True, type=Path)
    ap.add_argument("--variants", nargs="+", required=True,
                     help="standard-cell libraries; the first is the baseline")
    args = ap.parse_args()

    run_spec_path = args.design / "run_spec.json"
    targets = {}
    if run_spec_path.exists():
        targets = json.loads(run_spec_path.read_text(encoding="utf-8")).get("targets", {})

    report = compare(args.design.resolve(), args.variants, targets)
    print_report(report)
    print(f"\nreport written to: {write_report(report)}")


if __name__ == "__main__":
    main()
