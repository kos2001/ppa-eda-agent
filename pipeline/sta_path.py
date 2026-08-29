r"""The slew and delay stage by stage along the path to one chosen pin.

sta_report answers "which pins violate" by reading checks.rpt, which the
run already wrote. It cannot answer "why does this one violate", because
no report in the run directory contains that: it needs `report_checks
-to <pin>`, a fresh query against a chosen endpoint.

That gap is what this closes, and the case for closing it is specific.
sram_wrapper's address pins had been violating their slew limit by up to
22x for several sessions. Five config variables were tried and all were
null; the recorded diagnoses blamed macro placement, then wire length,
then the clock. One `report_checks` settled it:

    _093_/Q     (dfxtp_1)          slew 0.0539   <- the flop is fine
    load_slew85 (dlymetal6s2s_1)   slew 0.1576
    load_slew84 (dlymetal6s2s_1)   slew 0.2350
    load_slew83 (clkbuf_2)         slew 0.2585
    u_sram/addr0[3]                slew 0.3453   <- violation

repair_design was fixing a slew violation with delay cells — cells whose
entire purpose is to be slow. Nothing in metrics.json, checks.rpt or the
critical-path report says that. Only the chain does.

Note what the chain is NOT. It is not the critical path: setup slack on
this one was +18.57 ns, so max.rpt would never show it. The endpoint has
to be chosen from the violator list and then asked about directly.

Read-only and re-runs nothing — it links the netlist the run already
produced against its own SPEF and SDC. Works on runs that failed before
signoff, which is when this question is usually worth asking.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from toolchain import OPENLANE_IMAGE as IMAGE, platform_args

REPO_ROOT = Path(__file__).resolve().parent.parent
PDK_ROOT = REPO_ROOT / "pdk"

# A path row: some leading numbers, a rise/fall arrow, a pin, and
# usually a cell. The columns are Cap, Slew, Delay, Time — but Cap and
# Slew are blank on rows for a pin that is only a load, so a row can
# carry two, three or four numbers.
#
# They are assigned from the RIGHT, not the left. Matching optional
# leading groups greedily put the first number in Cap on a three-number
# row, so the endpoint's slew — the number the whole trace exists to
# report — was read as its capacitance and returned as None.
_STAGE = re.compile(
    r"^\s*((?:[\d.]+\s+){2,4})[\^v]\s+(\S+)(?:\s+\(([^)]+)\))?\s*$", re.M)
_COLUMNS = ("time_ns", "delay_ns", "slew_ns", "cap_pf")


class StaPathError(RuntimeError):
    pass


def find_pdk_root() -> Path:
    versions = PDK_ROOT / "volare" / "sky130" / "versions"
    if not versions.is_dir():
        raise StaPathError(f"no PDK at {versions}")
    for v in sorted(versions.iterdir()):
        if (v / "sky130A").is_dir():
            return v
    raise StaPathError(f"no sky130A under {versions}")


def _newest(run_dir: Path, *patterns: str) -> Path | None:
    """The last-written match, preferring final/ when a run reached it.

    Both layouts matter: a completed run has final/, and a run that died
    in signoff has only the numbered step directories — which is exactly
    the run someone is trying to debug.
    """
    for pattern in patterns:
        hits = sorted(run_dir.glob(pattern))
        if hits:
            return max(hits, key=lambda p: p.stat().st_mtime)
    return None


def inputs_for(run_dir: Path) -> dict:
    """Netlist, parasitics and constraints this run actually produced."""
    run_dir = Path(run_dir)
    netlist = _newest(run_dir, "final/nl/*.nl.v", "*/[!_]*.nl.v")
    if netlist is None:
        raise StaPathError(
            f"{run_dir}: no netlist — the run did not reach placement")
    spef = _newest(run_dir, "final/spef/nom/*.spef", "*/nom/*.spef", "*/*.spef")
    # Not a character-class glob: `*/[!s]*.sdc` was meant to skip the
    # synthesis SDC and silently skipped sram_wrapper.sdc too, leaving
    # the design with no clock and OpenSTA reporting "No paths found" —
    # a wrong answer that looks like a real one.
    sdc = None
    for cand in (run_dir.glob("final/sdc/*.sdc"), run_dir.glob("*/*.sdc")):
        hits = [p for p in cand if "abc" not in p.name]
        if hits:
            sdc = max(hits, key=lambda p: p.stat().st_mtime)
            break
    return {"netlist": netlist, "spef": spef, "sdc": sdc}


def macro_libs(design_dir: Path) -> list[Path]:
    """Any macro liberty the design declares, resolved to this checkout."""
    cfg = Path(design_dir) / "config.json"
    if not cfg.is_file():
        return []
    out = []
    for spec in (json.loads(cfg.read_text()).get("MACROS") or {}).values():
        for entry in (spec.get("lib") or {}).values():
            for raw in (entry if isinstance(entry, list) else [entry]):
                raw = str(raw)
                if raw.startswith("dir::"):
                    p = Path(design_dir) / raw[len("dir::"):]
                else:
                    hits = sorted((REPO_ROOT / "pdk").rglob(Path(raw).name))
                    p = hits[0] if hits else None
                if p and p.is_file():
                    out.append(p)
    return out


def parse_path(text: str) -> list[dict]:
    """One report_checks table into stages, in order."""
    out = []
    for m in _STAGE.finditer(text):
        numbers, pin, cell = m.groups()
        values = [float(v) for v in numbers.split()]
        row = {"pin": pin, "cell": cell,
               "cap_pf": None, "slew_ns": None, "delay_ns": None, "time_ns": None}
        for name, value in zip(_COLUMNS, reversed(values)):
            row[name] = value
        out.append(row)
    return out


_TCL = """
{liberties}
read_verilog /work/nl.v
link_design {top}
{spef_cmd}
{sdc_cmd}
puts "###PATH###"
report_checks -to {{{pin}}} -path_delay max -fields {{slew capacitance}} -digits 4
exit
"""


def trace(design_dir: Path | str, run_dir: Path | str, pin: str,
          top: str | None = None,
          scl: str = "sky130_fd_sc_hd",
          corner: str = "tt_025C_1v80") -> dict:
    """The stage-by-stage chain arriving at `pin`.

    `pin` is an endpoint from the max-slew violator list, not a guess —
    sta_report names them, and this says why each one is what it is.
    """
    design_dir = Path(design_dir).resolve()
    run_dir = Path(run_dir).resolve()
    got = inputs_for(run_dir)
    top = top or got["netlist"].name.replace(".nl.v", "").replace(".pnl", "")

    work = run_dir / "_sta_path"
    work.mkdir(exist_ok=True)
    (work / "nl.v").write_bytes(got["netlist"].read_bytes())
    if got["spef"]:
        (work / "p.spef").write_bytes(got["spef"].read_bytes())
    if got["sdc"]:
        (work / "c.sdc").write_bytes(got["sdc"].read_bytes())

    liberties = [f"read_liberty /pdkv/sky130A/libs.ref/{scl}/lib/{scl}__{corner}.lib"]
    for i, lib in enumerate(macro_libs(design_dir)):
        (work / f"macro{i}.lib").write_bytes(lib.read_bytes())
        liberties.append(f"read_liberty /work/macro{i}.lib")

    script = _TCL.format(
        liberties="\n".join(liberties), top=top, pin=pin,
        spef_cmd="read_spef /work/p.spef" if got["spef"] else 'puts "###NO_SPEF###"',
        sdc_cmd="read_sdc /work/c.sdc" if got["sdc"] else 'puts "###NO_SDC###"',
    )
    (work / "probe.tcl").write_text(script)

    cmd = ["docker", "run", "--rm", *platform_args(),
           "-v", f"{find_pdk_root()}:/pdkv", "-v", f"{work}:/work",
           IMAGE, "sta", "-no_init", "-exit", "/work/probe.tcl"]
    print(f"$ docker run … report_checks -to {pin}", file=sys.stderr)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    out = proc.stdout

    if "###PATH###" not in out:
        raise StaPathError(f"OpenSTA produced no path for {pin}:\n{out[-800:]}")
    body = out.split("###PATH###", 1)[1]
    if "Error:" in body:
        raise StaPathError(body[body.index("Error:"):][:300])

    # "No paths found" is how OpenSTA says the question was malformed —
    # a pin that is not an endpoint, or a design with no clock because
    # the SDC was missed. It printed the marker and then nothing, so an
    # empty table read as "the path is fine" until this raised instead.
    if "No paths found" in body or not (stages := parse_path(body)):
        raise StaPathError(
            f"OpenSTA found no path to {pin}. Either it is not a timing "
            f"endpoint, or this run has no usable constraints "
            f"(sdc={got['sdc']}).")
    return {
        "pin": pin,
        "netlist": str(got["netlist"].relative_to(REPO_ROOT)),
        "parasitics": str(got["spef"].relative_to(REPO_ROOT)) if got["spef"] else None,
        "constraints": str(got["sdc"].relative_to(REPO_ROOT)) if got["sdc"] else None,
        "corner": corner,
        "stages": stages,
        "arrival_slew_ns": stages[-1]["slew_ns"] if stages else None,
        "worst_degrader": worst_degrader(stages),
    }


def worst_degrader(stages: list[dict]) -> dict | None:
    """The stage that adds the most slew, which is the one to look at.

    Named rather than left for the reader to subtract: on sram_wrapper
    the answer was a delay cell, and it is only obvious once the
    differences are taken.
    """
    worst = None
    for prev, cur in zip(stages, stages[1:]):
        if prev.get("slew_ns") is None or cur.get("slew_ns") is None:
            continue
        added = cur["slew_ns"] - prev["slew_ns"]
        if worst is None or added > worst["added_slew_ns"]:
            worst = {"pin": cur["pin"], "cell": cur["cell"],
                     "added_slew_ns": round(added, 4),
                     "from_ns": prev["slew_ns"], "to_ns": cur["slew_ns"]}
    return worst


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--design", required=True, type=Path)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--pin", required=True,
                    help="endpoint, e.g. 'u_sram/addr0[3]' — take it from "
                         "sta_report's max-slew violator list")
    ap.add_argument("--corner", default="tt_025C_1v80")
    args = ap.parse_args()

    got = trace(args.design, args.run_dir, args.pin, corner=args.corner)
    print(f"{'pin':38s} {'cell':32s} {'slew':>8s} {'delay':>8s}")
    for s in got["stages"]:
        print(f"{s['pin']:38s} {(s['cell'] or ''):32s} "
              f"{(f'{s['slew_ns']:.4f}' if s['slew_ns'] is not None else '-'):>8s} "
              f"{s['delay_ns']:8.4f}")
    w = got["worst_degrader"]
    if w:
        print(f"\nworst degrader: {w['pin']} ({w['cell']}) "
              f"adds {w['added_slew_ns']} ns ({w['from_ns']} -> {w['to_ns']})")


if __name__ == "__main__":
    main()
