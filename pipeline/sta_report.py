#!/usr/bin/env python3
"""Reads the OpenSTA analysis OpenLane already ran, instead of throwing
it away and keeping one number per corner.

The gap this closes. Every completed run writes a full set of real
OpenSTA reports per timing corner — `max.rpt`/`min.rpt` (the actual
critical paths, stage by stage, with fanout, capacitance, slew and delay
at every point), `checks.rpt` (`report_check_types -max_slew -max_cap
-max_fanout -violators`, i.e. the DRV checks), `clock.rpt`, `power.rpt`.
Nothing in this pipeline has ever read a single one of them: `score()`
takes `timing__setup__wns__corner:*` from metrics.json and stops.

So a failing candidate reported "worst setup WNS -0.05" and nothing else.
That number says a path is late; it does not say which path, which cells
are on it, or where the delay accumulated — which is everything you need
to decide what to change. Same shape as the .odb gap: an aggregate exists,
the detail that makes it actionable was being discarded.

This is deliberately a *reader*, not a re-run. OpenSTA has already done
the analysis with the run's real parasitics and liberty; re-running it
would cost minutes to recompute what is sitting on disk, and any
discrepancy between the two would be a bug rather than a feature.

Usage:
    sta_report.py --run-dir designs/counter4/runs/<tag> [--corner max_ss_100C_1v60]
"""
import argparse
import json
import re
import sys
from pathlib import Path

# "     6    0.016266    0.253733    0.755894    1.225143 ^ _20_/Q (sky130_fd_sc_hd__dfxtp_1)"
_STAGE_RE = re.compile(
    r"^\s*(?P<fanout>\d+)?\s+(?P<cap>\d+\.\d+)\s+(?P<slew>\d+\.\d+)\s+"
    r"(?P<delay>\d+\.\d+)\s+(?P<time>\d+\.\d+)\s+[\^v]\s+(?P<pin>\S+)"
    r"(?:\s+\((?P<cell>[^)]+)\))?"
)
_SLACK_RE = re.compile(r"^\s*(-?\d+\.\d+)\s+slack\s+\((MET|VIOLATED)\)")
# The final port/pin line of a path has no fanout/cap columns, so the
# last matched stage under-reports arrival by that last hop (1.653849 vs
# the 1.655644 STA states, on counter4). Take the number STA itself
# prints rather than the last row that happened to parse.
_ARRIVAL_RE = re.compile(r"^\s*(-?\d+\.\d+)\s+data arrival time\s*$")


def latest_sta_dir(run_dir: Path) -> Path:
    """The most advanced STA step in the run. Post-PnR when it exists —
    that is the one with real extracted parasitics; otherwise the latest
    STA that did run, which for a failed run is the only timing view
    available and is still worth reading."""
    sta_dirs = sorted(
        (p for p in run_dir.glob("*-openroad-sta*") if p.is_dir()),
        key=lambda p: p.name,
    )
    if not sta_dirs:
        raise FileNotFoundError(f"no OpenSTA step directory under {run_dir}")
    for p in reversed(sta_dirs):
        if "postpnr" in p.name:
            return p
    return sta_dirs[-1]


def parse_path(rpt: Path) -> dict | None:
    """The first (worst) path in a max/min report, with its per-stage
    breakdown — which is the point: it turns one slack number into a list
    of where the time actually went."""
    if not rpt.exists():
        return None
    startpoint = endpoint = None
    stages: list[dict] = []
    slack = met = None
    arrival = None
    for line in rpt.read_text(errors="replace").splitlines():
        if startpoint is None and line.startswith("Startpoint:"):
            startpoint = line.split(":", 1)[1].strip()
        elif endpoint is None and line.startswith("Endpoint:"):
            endpoint = line.split(":", 1)[1].strip()
        m = _STAGE_RE.match(line)
        if m and slack is None:
            stages.append({
                "pin": m.group("pin"),
                "cell": m.group("cell"),
                "fanout": int(m.group("fanout")) if m.group("fanout") else None,
                "cap_pf": float(m.group("cap")),
                "slew_ns": float(m.group("slew")),
                "delay_ns": float(m.group("delay")),
                "arrival_ns": float(m.group("time")),
            })
        a = _ARRIVAL_RE.match(line)
        if a and arrival is None:
            arrival = float(a.group(1))
        s = _SLACK_RE.match(line)
        if s and slack is None:
            slack, met = float(s.group(1)), s.group(2) == "MET"
    if startpoint is None or not stages:
        return None
    worst = max(stages, key=lambda st: st["delay_ns"])
    if arrival is None:                      # no explicit line: fall back
        arrival = stages[-1]["arrival_ns"]
    return {
        "startpoint": startpoint,
        "endpoint": endpoint,
        "slack_ns": slack,
        "met": met,
        "arrival_ns": arrival,
        "stages": stages,
        # The single stage that dominates the path — the first thing
        # anyone asks after "why is it late".
        "worst_stage": {
            "pin": worst["pin"], "cell": worst["cell"],
            "delay_ns": worst["delay_ns"],
            "share_of_arrival": (round(worst["delay_ns"] / arrival, 4)
                                  if arrival else None),
        },
    }


def parse_drv(rpt: Path) -> dict | None:
    """The design-rule (max slew / max cap / max fanout) violation counts
    and any listed violators. This is the same family of check that
    produces RSZ-0090 (a max_transition violation), so having it as data
    rather than as a substring of an error message is the difference
    between reporting a failure and being able to reason about one."""
    if not rpt.exists():
        return None
    text = rpt.read_text(errors="replace")
    counts = {}
    for kind in ("slew", "fanout", "cap"):
        m = re.search(rf"max {kind} violation count (\d+)", text)
        if m:
            counts[f"max_{kind}_violations"] = int(m.group(1))
    section = re.search(
        r"report_check_types[^\n]*\n=+\n(.*?)(?=\n=====|\Z)", text, re.S)
    violators = []
    if section:
        for line in section.group(1).splitlines():
            line = line.strip()
            if line and not line.startswith("=") and "Corner" not in line:
                violators.append(line)
    return {**counts, "violator_lines": violators}


def read_run(run_dir: Path, corner: str | None = None) -> dict:
    """Reads whichever STA step ran last.

    Two real directory layouts, both produced by OpenLane: pre- and
    post-PnR STA write one subdirectory per timing corner, while mid-PnR
    STA writes max.rpt/min.rpt/checks.rpt flat in the step directory
    itself. Only the first was handled at first, so a run that failed
    before post-PnR (sram_wrapper is exactly that case) returned an empty
    result — and returned it silently, which is the failure mode worth
    avoiding above all: no corners must not read as no problems.
    """
    sta_dir = latest_sta_dir(run_dir)
    corner_dirs = sorted(p for p in sta_dir.iterdir() if p.is_dir())

    out = {}
    if corner_dirs:
        for cdir in corner_dirs:
            if corner and cdir.name != corner:
                continue
            out[cdir.name] = {
                "setup_path": parse_path(cdir / "max.rpt"),
                "hold_path": parse_path(cdir / "min.rpt"),
                "drv": parse_drv(cdir / "checks.rpt"),
            }
    elif (sta_dir / "max.rpt").exists():
        # Flat layout: the corner is not in the path, so name it after
        # the step rather than inventing a corner label.
        out[sta_dir.name] = {
            "setup_path": parse_path(sta_dir / "max.rpt"),
            "hold_path": parse_path(sta_dir / "min.rpt"),
            "drv": parse_drv(sta_dir / "checks.rpt"),
        }

    if not out:
        raise FileNotFoundError(
            f"{sta_dir} has no readable STA reports (neither per-corner "
            f"subdirectories nor a flat max.rpt) — refusing to report an "
            f"empty result as a clean one")
    return {"sta_step": sta_dir.name, "corners": out}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--corner", default=None)
    ap.add_argument("--stages", type=int, default=0,
                     help="also print this many worst path stages")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    data = read_run(args.run_dir.resolve(), args.corner)
    if args.json:
        print(json.dumps(data, indent=2))
        return

    print(f"\nSTA step: {data['sta_step']}")
    for name, c in data["corners"].items():
        p = c["setup_path"]
        drv = c["drv"] or {}
        print(f"\n=== {name} ===")
        if p:
            state = "MET" if p["met"] else "VIOLATED"
            w = p["worst_stage"]
            print(f"  setup slack {p['slack_ns']} ns ({state}), arrival {p['arrival_ns']} ns")
            print(f"  path        {p['startpoint']}  ->  {p['endpoint']}")
            print(f"  worst stage {w['pin']} ({w['cell']}) "
                  f"{w['delay_ns']} ns = {(w['share_of_arrival'] or 0) * 100:.1f}% of arrival")
        else:
            print("  no setup path reported")
        viol = {k: v for k, v in drv.items() if k.endswith("violations")}
        if viol:
            total = sum(viol.values())
            print(f"  DRV         {viol}" + ("" if total else "  (clean)"))
        for line in (drv.get("violator_lines") or [])[:5]:
            print(f"    ! {line}")
        if args.stages and p:
            print(f"  {'pin':<34} {'cell':<28} {'delay':>9} {'slew':>9}")
            for st in sorted(p["stages"], key=lambda s: -s["delay_ns"])[:args.stages]:
                print(f"  {st['pin'][:34]:<34} {str(st['cell'])[:28]:<28} "
                      f"{st['delay_ns']:>9.4f} {st['slew_ns']:>9.4f}")


if __name__ == "__main__":
    main()
