r"""Power from a real workload, instead of an assumed toggle rate.

score() has carried this caveat since it was written:

    "OpenLane's default/vectorless estimate — no VCD/SAIF activity
     annotation configured in this pipeline yet, so these are OpenSTA's
     default-activity numbers, not switching-activity-accurate ones"

Honest, and a declared gap. Everything needed to close it was already in
the pinned image: Icarus Verilog to simulate the synthesized netlist
against a testbench, and OpenSTA's `read_vcd` to annotate the resulting
switching activity onto the design before `report_power`. No new
dependency, same container.

Measured on spm, whose testbench ships with OpenLane's own example:

    group           vectorless   annotated
    combinational     3.39e-04    4.31e-04   +27%
    sequential        4.24e-04    4.22e-04     -0.5%
    clock             2.81e-04    2.81e-04      0%
    total             1.04e-03    1.13e-03    +8.7%

The default estimate understates combinational power by more than a
quarter, because a fixed toggle rate has no idea a multiplier's datapath
is busy. Sequential and clock barely move — those are clock-driven, so
their activity was never in doubt.

The simulation is also a functional check the pipeline did not have.
equiv_check proves the netlist matches the RTL formally; this runs the
vendor testbench against the placed-and-routed netlist and reports
whether it passed, which is different evidence about a different claim.

Needs a testbench, which most designs here do not have. Absent one this
returns None rather than a number — a vectorless figure relabelled as
measured would be worse than no figure.
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

# OpenSTA's report_power table: group name then four powers and a share.
_POWER_ROW = re.compile(
    r"^(Sequential|Combinational|Clock|Macro|Pad|Total)\s+"
    r"([\d.e+-]+)\s+([\d.e+-]+)\s+([\d.e+-]+)\s+([\d.e+-]+)",
    re.M,
)
_ANNOTATED = re.compile(r"Annotated (\d+) pin activities")


class PowerActivityError(RuntimeError):
    pass


def find_pdk_root() -> Path:
    versions = PDK_ROOT / "volare" / "sky130" / "versions"
    if not versions.is_dir():
        raise PowerActivityError(f"no PDK at {versions}")
    for v in sorted(versions.iterdir()):
        if (v / "sky130A").is_dir():
            return v
    raise PowerActivityError(f"no sky130A under {versions}")


def find_testbench(design_dir: Path) -> Path | None:
    """A design's testbench, if it has one.

    Only spm does today — it came with OpenLane's example. Returning None
    is the common case and not an error.
    """
    for candidate in sorted((design_dir / "verify").glob("*_tb.v")):
        return candidate
    return None


def find_netlist(run_dir: Path) -> Path:
    """The placed-and-routed netlist, which is what should be simulated —
    the point is to measure the circuit that will be fabricated."""
    final = run_dir / "final" / "nl"
    hits = sorted(final.glob("*.nl.v")) if final.is_dir() else []
    if not hits:
        raise PowerActivityError(
            f"no final netlist under {run_dir} — the run did not reach signoff")
    return hits[0]


def parse_power(text: str) -> dict:
    """One report_power table into named numbers."""
    out = {}
    for m in _POWER_ROW.finditer(text):
        out[m.group(1).lower()] = {
            "internal_w": float(m.group(2)),
            "switching_w": float(m.group(3)),
            "leakage_w": float(m.group(4)),
            "total_w": float(m.group(5)),
        }
    return out


_SCRIPT = r"""
set -e
cd /tmp
iverilog -g2012 -DFUNCTIONAL -DUNIT_DELAY=#1 -o sim.vvp \
  /pdkv/sky130A/libs.ref/{scl}/verilog/primitives.v \
  /pdkv/sky130A/libs.ref/{scl}/verilog/{scl}.v \
  /work/netlist.v /work/tb.v > /tmp/compile.log 2>&1 || {{
    echo "###COMPILE_FAILED###"; tail -20 /tmp/compile.log; exit 0; }}
echo "###SIM###"
vvp sim.vvp 2>&1 | tail -40
echo "###SIM_END###"
cat > sta.tcl <<'TCL'
read_liberty /pdkv/sky130A/libs.ref/{scl}/lib/{scl}__tt_025C_1v80.lib
read_verilog /work/netlist.v
link_design {top}
create_clock -name {clk} -period {period} [get_ports {clk}]
puts "###VECTORLESS###"
report_power
puts "###ANNOTATE###"
read_vcd -scope {scope} /tmp/dump.vcd
puts "###ANNOTATED###"
report_power
exit
TCL
sta -no_init -exit sta.tcl 2>&1
"""


def measure(design_dir: Path | str, run_dir: Path | str,
            clock_port: str = "clk", clock_period: float = 10.0,
            scl: str = "sky130_fd_sc_hd") -> dict | None:
    """Simulate the netlist and report power both ways, or None.

    None when the design has no testbench — the honest answer, since a
    vectorless number relabelled as measured is worse than no number.
    """
    design_dir = Path(design_dir).resolve()
    run_dir = Path(run_dir).resolve()
    tb = find_testbench(design_dir)
    if tb is None:
        return None

    netlist = find_netlist(run_dir)
    top = netlist.name.replace(".nl.v", "")
    work = run_dir / "_power_activity"
    work.mkdir(exist_ok=True)
    (work / "netlist.v").write_bytes(netlist.read_bytes())
    (work / "tb.v").write_bytes(tb.read_bytes())

    script = _SCRIPT.format(
        scl=scl, top=top, clk=clock_port, period=clock_period,
        # The testbench instantiates the design as `dut`; OpenSTA needs
        # the VCD scope that corresponds to the linked design.
        scope=f"{tb.stem}/dut",
    )
    cmd = [
        "docker", "run", "--rm", *platform_args(),
        "-v", f"{find_pdk_root()}:/pdkv",
        "-v", f"{work}:/work",
        IMAGE, "sh", "-c", script,
    ]
    print(f"$ docker run … {top} power activity", file=sys.stderr)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    out = proc.stdout

    if "###COMPILE_FAILED###" in out:
        return {"error": "gate-level simulation did not compile",
                "detail": out.split("###COMPILE_FAILED###", 1)[1][:800]}

    # Bounded at both ends. Splitting only on the next marker let
    # OpenSTA's own warnings — it emits one per fill cell, which have no
    # function to link — land in what is labelled simulation output, so
    # the last line of a passing testbench read as a warning.
    sim_text = ""
    if "###SIM###" in out and "###SIM_END###" in out:
        sim_text = out.split("###SIM###", 1)[1].split("###SIM_END###", 1)[0]

    vectorless = parse_power(
        out.split("###VECTORLESS###", 1)[1].split("###ANNOTATE###")[0]
    ) if "###VECTORLESS###" in out else {}
    annotated = parse_power(
        out.split("###ANNOTATED###", 1)[1]
    ) if "###ANNOTATED###" in out else {}

    pins = _ANNOTATED.search(out)
    return {
        "testbench": str(tb.relative_to(REPO_ROOT)),
        "netlist": str(netlist.relative_to(REPO_ROOT)),
        "annotated_pins": int(pins.group(1)) if pins else 0,
        # The testbench's own verdict on the placed netlist. Reported
        # rather than judged here: what counts as a pass is the
        # testbench's business, and its raw output is the evidence.
        "simulation_tail": sim_text.strip()[-1200:],
        "vectorless": vectorless,
        "annotated": annotated,
        "delta": compare(vectorless, annotated),
    }


def compare(vectorless: dict, annotated: dict) -> dict:
    """Per-group change from assuming activity to measuring it."""
    out = {}
    for group in sorted(set(vectorless) | set(annotated)):
        a = (vectorless.get(group) or {}).get("total_w")
        b = (annotated.get(group) or {}).get("total_w")
        if not isinstance(a, float) or not isinstance(b, float) or a == 0:
            continue
        out[group] = {
            "vectorless_w": a,
            "annotated_w": b,
            "change_pct": round(100 * (b - a) / a, 1),
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--design", required=True, type=Path)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--clock-port", default="clk")
    ap.add_argument("--clock-period", type=float, default=10.0)
    args = ap.parse_args()

    got = measure(args.design, args.run_dir, args.clock_port, args.clock_period)
    if got is None:
        raise SystemExit(
            f"{args.design.name} has no testbench under verify/ — "
            f"activity-annotated power needs a real workload to measure")
    print(json.dumps(got, indent=2))


if __name__ == "__main__":
    main()
