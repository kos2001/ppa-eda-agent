#!/usr/bin/env python3
"""Proves that a run's synthesized gate netlist is functionally equivalent
to the RTL it came from, using Yosys's own SAT-based equivalence checker.

Fills a real hole in this pipeline's verification story. `score()` checks
DRC, LVS, utilization and timing — all real, none of them functional. LVS
compares the *layout* against the *netlist*, confirming the physical
implementation matches what was handed to placement; nothing has ever
compared that netlist against the *RTL*. So a synthesis result that is
clean, legal, fast and wrong would be reported as PASS.

That is not a hypothetical risk here: this pipeline deliberately varies
`SYNTH_STRATEGY` (AREA 0 / AREA 2 / DELAY 1 / DELAY 4 are real candidates
in counter4's run_spec) and `STD_CELL_LIBRARY`, both of which change what
Yosys actually emits. Judging those candidates on area and timing while
never checking they still compute the same function is the gap this
closes.

Yosys is already in the OpenLane image — this adds no dependency, it
uses a capability the pipeline was shipping and not calling.

Method: read the RTL as `gold` and the gate netlist (with the real
liberty for cell functions) as `gate`, build an equivalence miter, and
discharge it with `equiv_simple` + `equiv_induct` for sequential logic.
`equiv_status -assert` exits non-zero on any unproven point, which is
what makes the result trustworthy rather than decorative — verified by
negative control (see the module tests).

Usage:
    equiv_check.py --design designs/counter4 --run-dir designs/counter4/runs/<tag>
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from toolchain import OPENLANE_IMAGE as IMAGE, platform_args

REPO_ROOT = Path(__file__).resolve().parent.parent
PDK_ROOT = REPO_ROOT / "pdk"

# `-ignore_miss_func` is required, not incidental: sky130's liberty has
# cells whose `function` attribute Yosys can't parse (sequential cells
# with complex next-state expressions). Without it the read aborts and
# nothing gets compared; with it those cells become blackboxes, which is
# correct — their equivalence points are handled by equiv_induct.
_YS = """
{gold_models}
read_verilog {rtl_args}
prep -top {top} -flatten
{normalization}
design -stash gold

read_liberty -ignore_miss_func /work/cells.lib
{physical_models}
read_verilog /work/gate.v
prep -top {top} -flatten
{normalization}
design -stash gate

design -copy-from gold -as gold {top}
design -copy-from gate -as gate {top}
equiv_make gold gate equiv
prep -top equiv
equiv_simple -seq 5
equiv_induct -seq 5
equiv_status -assert
"""


def find_netlist(run_dir: Path) -> Path:
    """The netlist synthesis produced, preferred over `final/nl`.

    Not a convenience choice — `final/nl` cannot be read this way. It
    contains cells inserted *after* synthesis for physical reasons
    (`sky130_fd_sc_hd__fill_1`, tap, decap, antenna diodes) which have no
    logic function, so `read_liberty -ignore_miss_func` skips them and
    Yosys then aborts: "Module `\\sky130_fd_sc_hd__fill_1' ... is not part
    of the design". Found by running this against a full flow after it
    had worked against a synthesis-only run.

    This answers only whether synthesis preserved the RTL's function.
    LVS/DRC do not independently prove that later rewiring preserved logic.
    Use check(..., final_netlist=True) for that separate proof; known physical
    cells are validated against Liberty/LEF before being modeled as no-ops.
    """
    for step in sorted(run_dir.glob("*yosys-synthesis"), key=lambda p: p.name):
        for f in step.glob("*.nl.v"):
            return f
    final = run_dir / "final" / "nl"
    if final.is_dir():
        for f in final.glob("*.nl.v"):
            return f
    candidates = sorted(run_dir.glob("*/*.nl.v"), key=lambda p: p.parent.name)
    if not candidates:
        raise FileNotFoundError(
            f"no gate netlist under {run_dir} — the run never completed synthesis")
    return candidates[-1]


def find_liberty(run_dir: Path) -> Path:
    """The liberty for the standard-cell library this run actually used.

    Read from the run's own resolved.json rather than assuming
    sky130_fd_sc_hd: tech_compare.py varies STD_CELL_LIBRARY, and
    checking a netlist against the wrong library's cell functions would
    either fail spuriously or prove nothing.
    """
    scl = "sky130_fd_sc_hd"
    resolved = run_dir / "resolved.json"
    if resolved.exists():
        scl = json.loads(resolved.read_text(encoding="utf-8")).get("STD_CELL_LIBRARY", scl)
    lib_dir = PDK_ROOT / "sky130A" / "libs.ref" / scl / "lib"
    for lib in sorted(lib_dir.glob("*tt_025C_1v80.lib")):
        return lib
    raise FileNotFoundError(f"no typical-corner liberty for {scl} under {lib_dir}")


def physical_models(netlist: str, liberty: str, lef: str = "") -> tuple[str, list[str]]:
    """Model only known SKY130 HD input-only physical cells as logical no-ops.

    Validate pins against the actual Liberty; never blackbox arbitrary logic
    to make a proof pass. Electrical behavior remains a separate signoff gate.
    """
    from macro_model_audit import groups, attribute
    names = sorted(set(re.findall(
        r"\bsky130_(?:fd|ef)_sc_hd__(?:fill|decap|tapvpwrvgnd|diode)_\d+\b", netlist)))
    libraries = [g for g in groups(liberty) if g[0] == "library"]
    if len(libraries) != 1:
        raise ValueError("expected one Liberty library")
    cells = {name: body for kind, name, body in groups(libraries[0][2]) if kind == "cell"}
    models = []
    for name in names:
        if name not in cells:
            # Fillers/taps may have no Liberty entry. Require the real LEF
            # to show exclusively power/ground pins, never infer from name alone.
            macro = re.search(r"(?ms)^\s*MACRO\s+" + re.escape(name)
                              + r"\s*$(.*?)^\s*END\s+" + re.escape(name) + r"\s*$", lef)
            if "__diode_" in name or macro is None:
                raise ValueError(f"physical cell absent from Liberty/LEF: {name}")
            pin_names = re.findall(r"(?m)^\s*PIN\s+(\S+)\s*$", macro[1])
            for pin in pin_names:
                block = re.search(r"(?ms)^\s*PIN\s+" + re.escape(pin)
                                  + r"\s*$(.*?)^\s*END\s+" + re.escape(pin) + r"\s*$", macro[1])
                if block is None or not re.search(r"\bUSE\s+(?:POWER|GROUND)\s*;", block[1]):
                    raise ValueError(f"physical LEF has signal/unknown pin: {name}/{pin}")
            models.append(f"module {name}(); endmodule")
            continue
        pins = []
        for kind, pin, body in groups(cells[name]):
            if kind in ("ff", "latch", "bus", "bundle"):
                raise ValueError(f"unexpected logic in physical cell: {name}")
            if kind == "pin":
                if (attribute(body, "direction") != "input"
                        or attribute(body, "function") is not None
                        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", pin)):
                    raise ValueError(f"physical cell has non-input-only pin: {name}/{pin}")
                pins.append("input " + pin)
        models.append(f"module {name}({', '.join(pins)}); endmodule")
    return "\n".join(models) + "\n", names


def check(design_dir: Path, run_dir: Path, top: str | None = None, *,
          final_netlist: bool = False, log_path: Path | None = None,
          reference_synthesis: bool = False) -> dict:
    """Returns a structured result. Never raises on non-equivalence —
    "these differ" is a finding to record, not a crash."""
    if reference_synthesis and not final_netlist:
        raise ValueError("synthesis reference is only supported with final-netlist proof")
    rtl_files = sorted(design_dir.glob("src/*.v"))
    # Blackbox stubs describe a macro's interface with no behaviour, so
    # including them as `gold` would compare against an empty function.
    rtl_files = [f for f in rtl_files if "blackbox" not in f.name]
    if not rtl_files:
        raise FileNotFoundError(f"no RTL under {design_dir}/src")
    reference_netlist = None
    if reference_synthesis:
        choices = sorted(run_dir.glob("*yosys-synthesis/*.nl.v"))
        if len(choices) != 1:
            raise ValueError("requires exactly one synthesis reference netlist")
        reference_netlist = choices[0]
        rtl_files = [reference_netlist]

    if final_netlist:
        choices = sorted((run_dir / "final/nl").glob("*.nl.v"))
        if len(choices) != 1:
            raise ValueError("final-netlist proof requires exactly one final/nl/*.nl.v")
        netlist = choices[0]
    else:
        netlist = find_netlist(run_dir)
    liberty = find_liberty(run_dir)
    lefs = sorted((liberty.parent.parent / "lef").glob("*.lef"))
    top = top or json.loads((design_dir / "config.json").read_text(encoding="utf-8"))["DESIGN_NAME"]

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        shutil.copy(netlist, work / "gate.v")
        shutil.copy(liberty, work / "cells.lib")
        physical_command, modeled_cells = "", []
        if final_netlist:
            models, modeled_cells = physical_models(
                netlist.read_text(encoding="utf-8"), liberty.read_text(encoding="utf-8"),
                "\n".join(lef.read_text(encoding="utf-8") for lef in lefs))
            (work / "physical.v").write_text(models, encoding="utf-8")
            physical_command = "read_verilog -overwrite /work/physical.v"
        rtl_args = []
        for i, f in enumerate(rtl_files):
            shutil.copy(f, work / f"rtl{i}.v")
            rtl_args.append(f"/work/rtl{i}.v")
        (work / "eq.ys").write_text(
            _YS.format(rtl_args=" ".join(rtl_args), top=top,
                       physical_models=physical_command,
                       gold_models=("read_liberty -ignore_miss_func /work/cells.lib\n"
                                    + physical_command if reference_synthesis else ""),
                       normalization="memory_map\nclk2fflogic\nopt_clean\nsplitnets" if final_netlist else ""),
            encoding="utf-8")

        container_name = "ppa-equiv-" + uuid.uuid4().hex
        cmd = ["docker", "run", "--rm", "--name", container_name, *platform_args(),
               "-v", f"{work}:/work", IMAGE, "yosys", "-s", "/work/eq.ys"]
        print(f"$ {' '.join(cmd)}", file=sys.stderr)
        timed_out = False
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                    timeout=600 if final_netlist else None)
        except subprocess.TimeoutExpired as exc:
            # Killing only the Docker client would leave the proof running.
            # Remove only the uniquely named container created by this call.
            subprocess.run(["docker", "rm", "-f", container_name],
                           capture_output=True, text=True, check=True)
            def decoded(value):
                return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
            result = subprocess.CompletedProcess(cmd, 124, decoded(exc.stdout),
                decoded(exc.stderr) + "\nProof deadline exceeded; equivalence remains unverified.\n")
            timed_out = True

    out = result.stdout + result.stderr
    if log_path is not None:
        log_path.write_text(out, encoding="utf-8")
    proven = unproven = None
    m = re.search(r"Of those cells (\d+) are proven and (\d+) are unproven", out)
    if m:
        proven, unproven = int(m.group(1)), int(m.group(2))
    else:
        failure = re.search(r"ERROR: Found (\d+) unproven \$equiv cells in 'equiv_status -assert'", out)
        if failure:
            unproven = int(failure[1])
    if timed_out:
        # Progress counts precede the proof and are not final results.
        proven = unproven = None

    return {
        "design": design_dir.name,
        "top": top,
        "netlist": str(netlist),
        "liberty": str(liberty),
        "scope": ("final routed logic vs synthesis; does not prove RTL-to-synthesis"
                  if reference_synthesis else "final routed logical netlist vs RTL"
                  if final_netlist else "synthesis netlist vs RTL"),
        "reference_sources_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in rtl_files},
        "formal_assumptions": (["clk2fflogic global clock and negative hold time for async FF inputs",
                                "No analog delay, clock skew or metastability model"] if final_netlist else []),
        "normalization": ["memory_map", "clk2fflogic", "opt_clean", "splitnets"] if final_netlist else [],
        "physical_input_only_models": modeled_cells,
        "netlist_sha256": hashlib.sha256(netlist.read_bytes()).hexdigest(),
        "liberty_sha256": hashlib.sha256(liberty.read_bytes()).hexdigest(),
        "physical_lefs_sha256": ({str(lef): hashlib.sha256(lef.read_bytes()).hexdigest()
                                  for lef in lefs} if final_netlist else {}),
        "log_sha256": hashlib.sha256(out.encode("utf-8")).hexdigest(),
        "equivalent": result.returncode == 0,
        "timed_out": timed_out,
        "proven_points": proven,
        "unproven_points": unproven,
        # A pass with zero compared points would be vacuous — surfaced so
        # a caller can tell "proved equivalent" from "compared nothing".
        "vacuous": result.returncode == 0 and not proven,
        "tail": out[-1200:] if result.returncode != 0 else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--design", required=True, type=Path)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--top", default=None)
    ap.add_argument("--final-netlist", action="store_true",
                    help="prove final routed logic, explicitly modeling Liberty-checked input-only physical cells")
    ap.add_argument("--reference-synthesis", action="store_true",
                    help="isolate physical transformation: use synthesis as gold, not RTL")
    ap.add_argument("--out", type=Path, help="write structured proof result")
    ap.add_argument("--log", type=Path, help="retain complete Yosys proof log")
    args = ap.parse_args()

    r = check(args.design.resolve(), args.run_dir.resolve(), args.top,
              final_netlist=args.final_netlist, log_path=args.log,
              reference_synthesis=args.reference_synthesis)
    if args.out:
        args.out.write_text(json.dumps(r, indent=2) + "\n", encoding="utf-8")
    verdict = ("EQUIVALENT" if r["equivalent"] else "NOT PROVEN EQUIVALENT")
    if r["vacuous"]:
        verdict = "INCONCLUSIVE (nothing was compared)"
    print(f"\n{r['design']} ({r['top']}): {verdict}")
    print(f"  netlist : {r['netlist']}")
    print(f"  liberty : {r['liberty']}")
    print(f"  points  : {r['proven_points']} proven, {r['unproven_points']} unproven")
    if r["tail"]:
        print(f"\n{r['tail']}")
    sys.exit(0 if r["equivalent"] and not r["vacuous"] else 1)


if __name__ == "__main__":
    main()
