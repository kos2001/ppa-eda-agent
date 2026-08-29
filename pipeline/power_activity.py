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
    combinational     4.64e-04    6.69e-04   +44.2%
    sequential        5.03e-04    4.99e-04    -0.8%
    clock             3.65e-04    3.65e-04     0.0%
    total             1.33e-03    1.53e-03   +15.0%

The default estimate understates combinational power by nearly half,
because a fixed toggle rate has no idea a multiplier's datapath is busy.
Sequential and clock barely move — those are clock-driven, so their
activity was never in doubt. The two groups that should move did, and
the two that shouldn't didn't.

The vectorless column is a check on the whole setup, not decoration. It
reproduces OpenLane's own power__total for the same corner to within
0.1% (1.330e-03 against 1.331575e-03), so the annotated column differs
from it because of the activity data and nothing else. Getting there
took three corrections, each of which looked fine until it was checked
against that number:

  - no SPEF, so every net had zero wire capacitance and switching power
    came out 1.8x low;
  - a hand-written `create_clock` instead of the run's own SDC, so
    inputs had ideal zero transition and no output load;
  - the tt corner, while OpenLane's unsuffixed `power__total` is
    actually max_ff_n40C_1v95 — the fast-fast 1.95 V corner.

The last one presented as a 14% error in this module and was not one.

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


def find_sdc(run_dir: Path) -> Path | None:
    """The run's own signoff constraints, if it reached them.

    Preferred over a hand-written `create_clock`. A bare clock leaves
    input ports with an ideal zero transition and no output load, which
    understates internal power — measured on spm, using the real SDC
    moved the total from 1.13e-03 W to within a few percent of
    OpenLane's own 1.332e-03 W. Reconstructing constraints that the run
    already wrote down is a way to disagree with the run for no reason.

    Its clock comes from here too, which is why the clock_port and
    clock_period arguments only matter for the fallback.
    """
    hits = sorted((run_dir / "final" / "sdc").glob("*.sdc")) \
        if (run_dir / "final" / "sdc").is_dir() else []
    return hits[0] if hits else None


# The corner score() reports. OpenLane's `power__total` metric carries
# no corner suffix, which reads as "the" power — it is in fact
# max_ff_n40C_1v95, the fast-fast 1.95 V corner, the highest-power one.
#
# Found by measuring: at tt this module reported 1.140e-03 W against
# score()'s 1.332e-03 W, a 14% gap that looked like a defect here. It
# was not. OpenLane's own tt number is 1.152e-03 W — a ~1% agreement.
# The whole discrepancy was a corner mismatch, so this matches the
# corner rather than leaving two numbers that cannot be compared.
POWER_CORNER = "ff_n40C_1v95"
POWER_RC_CORNER = "max"


def find_spef(run_dir: Path, rc_corner: str = POWER_RC_CORNER) -> Path | None:
    """The extracted parasitics for the power corner, if OpenRCX ran.

    Without these, every net has zero wire capacitance and switching
    power is badly understated — measured on spm, 2.07e-04 W against
    OpenLane's own 3.72e-04 W, a factor of 1.8. The activity annotation
    fixes *how often* a net toggles; the SPEF fixes *what it costs* to
    toggle it. Both are needed, and the second was easy to miss because
    the numbers without it look perfectly reasonable.
    """
    for corner in (rc_corner, "nom", "typ"):
        hits = sorted((run_dir / "final" / "spef" / corner).glob("*.spef")) \
            if (run_dir / "final" / "spef" / corner).is_dir() else []
        if hits:
            return hits[0]
    return None


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
read_liberty /pdkv/sky130A/libs.ref/{scl}/lib/{scl}__{corner}.lib
read_verilog /work/netlist.v
link_design {top}
{spef_cmd}
{sdc_cmd}
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
            scl: str = "sky130_fd_sc_hd",
            corner: str = POWER_CORNER) -> dict | None:
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

    # Reported rather than silently skipped: a run without parasitics
    # gives switching power roughly half of what the wires actually
    # cost, and that should be visible in the result, not inferred.
    spef = find_spef(run_dir)
    if spef is not None:
        (work / "parasitics.spef").write_bytes(spef.read_bytes())

    # The run's own constraints when it has them, a bare clock only as a
    # fallback — the fallback is measurably worse, so it is recorded in
    # the result rather than applied quietly.
    sdc = find_sdc(run_dir)
    if sdc is not None:
        (work / "constraints.sdc").write_bytes(sdc.read_bytes())

    script = _SCRIPT.format(
        scl=scl, top=top, corner=corner,
        spef_cmd=("read_spef /work/parasitics.spef" if spef is not None
                  else 'puts "###NO_SPEF###"'),
        sdc_cmd=("read_sdc /work/constraints.sdc" if sdc is not None else
                 f"create_clock -name {clock_port} -period {clock_period} "
                 f"[get_ports {clock_port}]"),
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
        "parasitics": str(spef.relative_to(REPO_ROOT)) if spef else None,
        "constraints": str(sdc.relative_to(REPO_ROOT)) if sdc else "bare clock",
        # Recorded because it is the difference between a number that
        # can be compared with score()'s and one that cannot.
        "corner": corner,
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
