r"""Derive a design's topology.json from what a real run already produced.

topology.json is what the topology fallback in case_retrieval compares,
and it was hand-written by the circuit-layout-extractor pass for the
first designs only. aes, gcd and riscv32i never got one, so their 19
cases carried `topology: null` and the fallback had nothing to compare:
that is one of the two reasons those cases found no precedent (the
other, gate rejections carrying no signature, is fixed in
case_retrieval). Measured on the store 2026-09-10: counter4 13/13 cases
with topology, aes 0/8, gcd 0/8, riscv32i 0/3.

Every field is read from an artefact, not estimated:

  module_count               `module` declarations in the design's own
                             Verilog sources
  has_macros                 MACROS / EXTRA_LEFS in config.json
  clock_domain_count         CLOCK_PORT entries in config.json
  port_count                 ports of the top module in Yosys' JSON
                             netlist from a completed run
  sequential_element_estimate  flip-flop and latch cells in that same
                             netlist, by cell-name pattern
  power_domain_count         VDD_NETS entries, 1 when unset

The Yosys JSON is read whole, not through netlist_graph.build_graph:
that graph is capped for storage, and on aes the cap sampled 400 of
11,616 cells and found 0 flops where the netlist has 562.

Usage:
    topology_derive.py --design aes                 # print what it derives
    topology_derive.py --design aes --write         # write designs/aes/topology.json
    topology_derive.py --design aes --backfill      # add it to cases that have none

--write refuses to overwrite a hand-written file. --backfill touches
only the `topology` field of cases where it is null, and only for the
named design, through the same compact case writer as orchestrator.py.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from case_storage import write_case_json

REPO_ROOT = Path(__file__).resolve().parent.parent
DESIGNS = REPO_ROOT / "pipeline" / "designs"
CASES = REPO_ROOT / "reference-db" / "cases"

_MODULE = re.compile(r"^\s*module\s+\w+", re.M)
# sky130 (dfxtp, dfrtp, dfstp, dfbbn, sdf*, dlxtp, dlrtp) and gf180mcu
# (dffq, dffrnq, dffsnq, sdff*, latq) sequential cell name stems.
_SEQ = re.compile(r"__(s?df[a-z]*_|dff[a-z]*_|sdff[a-z]*_|dl[a-z]*_|lat[a-z]*_)")


def netlist_json(run_dir: Path) -> Path | None:
    hits = sorted(Path(run_dir).glob("*yosys-synthesis/*.nl.v.json"))
    return hits[0] if hits else None


def runs_with_netlist(design_dir: Path) -> list[Path]:
    runs = design_dir / "runs"
    if not runs.exists():
        return []
    return sorted(r for r in runs.iterdir() if netlist_json(r))


def count_sources(design_dir: Path, config: dict) -> int:
    """`module` declarations across the design's own sources."""
    files: list[Path] = []
    spec = config.get("VERILOG_FILES") or []
    if isinstance(spec, str):
        spec = [spec]
    for entry in spec:
        rel = entry[len("dir::"):] if entry.startswith("dir::") else entry
        files.extend(design_dir.glob(rel))
    if not files:
        files = list((design_dir / "src").glob("*.v"))
    return sum(len(_MODULE.findall(p.read_text(errors="replace"))) for p in files)


def derive(design_dir: Path, run_dir: Path | None = None) -> dict:
    design_dir = Path(design_dir)
    config = json.loads((design_dir / "config.json").read_text(encoding="utf-8"))
    if run_dir is None:
        runs = runs_with_netlist(design_dir)
        if not runs:
            raise FileNotFoundError(
                f"{design_dir.name}: no run with a Yosys JSON netlist under runs/")
        run_dir = runs[-1]
    nl_path = netlist_json(run_dir)
    if nl_path is None:
        raise FileNotFoundError(f"{run_dir}: no *yosys-synthesis/*.nl.v.json")
    modules = json.loads(nl_path.read_text(encoding="utf-8"))["modules"]
    top_name = config.get("DESIGN_NAME")
    top = modules.get(top_name) or next(iter(modules.values()))

    cells = top.get("cells") or {}
    seq = sum(1 for c in cells.values() if _SEQ.search(c.get("type", "")))

    clocks = config.get("CLOCK_PORT")
    if isinstance(clocks, list):
        clock_domains = len(clocks)
    else:
        clock_domains = 1 if clocks else 0
    vdd = config.get("VDD_NETS")
    power_domains = len(vdd) if isinstance(vdd, list) and vdd else 1

    return {
        "module_count": count_sources(design_dir, config),
        "has_macros": bool(config.get("MACROS") or config.get("EXTRA_LEFS")),
        "clock_domain_count": clock_domains,
        "port_count": len(top.get("ports") or {}),
        "sequential_element_estimate": seq,
        "power_domain_count": power_domains,
        "notes": (
            f"Derived by topology_derive.py from {design_dir.name}'s config.json, "
            f"its Verilog sources and the Yosys netlist of run {Path(run_dir).name} "
            f"({len(cells)} cells, {seq} sequential by cell name). Not "
            f"hand-written; no judgement of datapath/control character is made."
        ),
    }


def write_topology(design_dir: Path, topo: dict, force: bool = False) -> Path:
    path = Path(design_dir) / "topology.json"
    if path.exists() and not force:
        raise FileExistsError(f"{path} exists; refusing to overwrite a recorded topology")
    path.write_text(json.dumps(topo, indent=2) + "\n", encoding="utf-8")
    return path


def backfill(design: str, topo: dict, cases_dir: Path = CASES) -> list[str]:
    """Set `topology` on this design's cases that have none. Returns the
    files changed. Cases that already carry a topology are left alone
    even if it differs — a recorded value outranks a derived one."""
    changed = []
    for path in sorted(cases_dir.glob(f"{design}__*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        if case.get("design") != design or case.get("topology"):
            continue
        case["topology"] = topo
        write_case_json(path, case)
        changed.append(path.name)
    return changed


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--design", required=True)
    ap.add_argument("--run-dir", type=Path, help="run to read the netlist from (default: latest with one)")
    ap.add_argument("--write", action="store_true", help="write designs/<design>/topology.json")
    ap.add_argument("--force", action="store_true", help="with --write, overwrite an existing file")
    ap.add_argument("--backfill", action="store_true",
                    help="add the topology to this design's cases that have none")
    args = ap.parse_args(argv)

    design_dir = DESIGNS / args.design
    topo = derive(design_dir, args.run_dir)
    print(json.dumps(topo, indent=2))
    if args.write:
        print(f"wrote {write_topology(design_dir, topo, force=args.force)}", file=sys.stderr)
    if args.backfill:
        changed = backfill(args.design, topo)
        print(f"backfilled {len(changed)} case(s): {', '.join(changed) or '-'}", file=sys.stderr)


if __name__ == "__main__":
    main()
