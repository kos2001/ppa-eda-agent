"""Read existing full STA path reports only; never invoke EDA tools."""
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[5]
RUN = ROOT / "pipeline/designs/srio_r15_clock_fanout16_revalidate_20261015/runs/io"
STAGE = RUN / "54-openroad-stapostpnr"
ARCHIVE = ROOT / "pipeline/designs/sram_wrapper/experiments/evidence-20261015-physical"
ROW = re.compile(r"^\s*\d+\s+(?P<cap>[\d.]+)\s+[\d.]+\s+[\d.-]+\s+[\d.-]+\s+(?P<edge>[v^])\s+u_sram/(?P<pin>dout[01]\[\d+\])(?:\s|$)")
PINS = [f"dout{port}[{bit}]" for port in range(2) for bit in range(32)]
COMBINATIONS = [f"{mode}_{edge}" for mode in ("max", "min") for edge in ("rise", "fall")]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    sources = {}
    def retain(path):
        sources[str(path.relative_to(ROOT))] = sha(path)
    resolved = json.loads((ARCHIVE / "resolved.json").read_text())
    records = []
    for corner in resolved["STA_CORNERS"]:
        observed = {pin: {key: set() for key in COMBINATIONS} for pin in PINS}
        for mode in ("max", "min"):
            path = STAGE / corner / (mode + ".rpt")
            retain(path)
            for line in path.read_text().splitlines():
                match = ROW.match(line)
                if match:
                    key = mode + ("_rise" if match["edge"] == "^" else "_fall")
                    observed[match["pin"]][key].add(float(match["cap"]))
        full_check = STAGE / corner / "checks.rpt"
        archive_check = ARCHIVE / corner / "checks.rpt"
        retain(full_check)
        retain(archive_check)
        normalized_match = full_check.read_text() == archive_check.read_text()
        observed_count = sum(bool(values) for pin in observed.values() for values in pin.values())
        pins_present = sum(any(pin.values()) for pin in observed.values())
        records.append({
            "corner": corner, "all_64_output_pins_present": pins_present == 64,
            "pins_present": pins_present, "expected_analysis_edge_combinations": 256,
            "observed_analysis_edge_combinations": observed_count,
            "missing_analysis_edge_combinations": 256 - observed_count,
            "compact_checks_match_full_run_after_newline_normalization": normalized_match,
            "columns": COMBINATIONS,
            "path_cap_values_report_units": {
                pin: [next(iter(observed[pin][key])) if len(observed[pin][key]) == 1 else None
                      for key in COMBINATIONS] for pin in PINS},
            "ambiguous_values": {pin + ":" + key: sorted(values)
                                 for pin, data in observed.items() for key, values in data.items()
                                 if len(values) > 1},
        })
    for path in (ARCHIVE / "resolved.json", STAGE / "state_in.json", STAGE / "COMMANDS",
                 ROOT / "pipeline/flows/macro_input_audit.py", ROOT / "pipeline/flows/macro_signoff.py"):
        retain(path)
    liberty = ROOT / "pipeline/designs/sram_wrapper/lib/sky130_sram_1kbyte_1rw1r_32x256_8_TT_1p8V_25C.relaxed.lib"
    retain(liberty)
    result = {
        "status": "partial_path_report_coverage", "model_qualified": False,
        "output_load_coverage_complete": False,
        "scope": "Observed Cap columns at u_sram/dout driver path rows from existing min/max reports; no fresh STA or inferred testbench loads",
        "units": {"reported_cap_unit": "unknown", "liberty_cap_unit": "1 pF",
                  "note": "Local macro Liberty declares capacitive_load_unit(1,pF); retained path report headers do not declare units, and this audit did not verify the pinned report unit override contract. Values remain in report units."},
        "null_meaning": "unobserved analysis/edge combination, or ambiguous if listed separately; never zero",
        "corners": records, "sources_sha256": sources,
        "available_reextraction_state": str((STAGE / "state_in.json").relative_to(ROOT)),
        "existing_sta_command": "sta -no_splash -exit /design/runs/io/54-openroad-stapostpnr/corner_macro_slews.tcl",
        "next_minimal_step": "Use same pinned OpenLane image and per-corner _env_*.tcl, map /design to this run's design and /pdk to original PDK, and load the state_in netlist/SDC/Liberty and matching 53-openroad-rcx SPEF. Add a separate report-only Tcl wrapper after the original corner.tcl, enumerating all 64 output pins and explicitly querying min/max rise/fall load capacitance with units, net names and annotation status. Verify the installed OpenSTA query API first. Preserve original reports and emit macro_outputs.csv per corner. No placement, routing or extraction recomputation is required.",
    }
    Path(__file__).with_name("output-load-coverage.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"corners": len(records), "pins_per_corner": [r["pins_present"] for r in records],
                      "observed_combinations": [r["observed_analysis_edge_combinations"] for r in records],
                      "all_source_runs_match": all(r["compact_checks_match_full_run_after_newline_normalization"] for r in records)}))


if __name__ == "__main__":
    main()
