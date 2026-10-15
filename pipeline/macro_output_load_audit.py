"""Fail-closed coverage audit for the pinned SRAM output-load STA export.

Table lookup load, effective capacitance and reduced pi-network total remain
separate observations. Completing this audit never qualifies a macro model.
"""
import math
import re

PINS = {f"u_sram/dout{port}[{bit}]" for port in range(2) for bit in range(32)}
EXPECTED = {(pin, analysis, edge) for pin in PINS
            for analysis in ("min", "max") for edge in ("rise", "fall")}
FIELDS = ("table_load_pf", "ceff_pf", "pi_total_pf")


def audit_log(text):
    issues, observations = [], []
    text = text.replace("\r\n", "\n")
    units = re.findall(r"(?m)^\s*capacitance\s+(\S+)\s*$", text)
    if units != ["1pF"]:
        issues.append("expected exactly one explicit capacitance 1pF unit declaration")
    done = re.findall(r"(?m)^LOAD_EXPORT_COMPLETE\s+(\S+)\s*$", text)
    if done != ["64"] or not text.rstrip().endswith("LOAD_EXPORT_COMPLETE 64"):
        issues.append("missing, duplicate or invalid final LOAD_EXPORT_COMPLETE 64 marker")
    if re.search(r"(?im)^\s*(?:error:|error\s|fatal:)", text):
        issues.append("STA error appears in export log")

    annotation = re.findall(r"(?m)^Found (\d+) unannotated drivers\.\s*$", text)
    partial = re.findall(r"(?m)^Found (\d+) partially unannotated drivers\.\s*$", text)
    unannotated = []
    if len(annotation) != 1 or partial != ["0"]:
        issues.append("missing/ambiguous parasitic annotation report or partially unannotated drivers")
    else:
        region = re.search(r"Found \d+ unannotated drivers\.\n(.*?)^Found 0 partially unannotated drivers\.", text, re.M | re.S)
        if region is None:
            issues.append("cannot delimit unannotated-driver list")
        else:
            unannotated = [line.strip() for line in region[1].splitlines() if line.strip()]
            if len(unannotated) != int(annotation[0]):
                issues.append("unannotated-driver list does not match declared count")
            if any(pin.startswith("u_sram/dout") for pin in unannotated):
                issues.append("SRAM output driver lacks parasitic annotation")

    starts = re.findall(r"(?m)^LOAD_DCALC_BEGIN\s+(\S+)\s+(\S+)\s*$", text)
    ends = re.findall(r"(?m)^LOAD_DCALC_END\s+(\S+)\s+(\S+)\s*$", text)
    blocks = re.findall(r"(?m)^LOAD_DCALC_BEGIN (\S+) (\S+)\n(.*?)^LOAD_DCALC_END (\S+) (\S+)\s*$", text, re.S)
    if len(starts) != 128 or len(ends) != 128 or len(blocks) != 128:
        issues.append("expected exactly 128 complete pin/analysis blocks")
    seen_blocks, seen = set(), set()
    for pin, analysis, body, end_pin, end_analysis in blocks:
        key = pin, analysis
        if key in seen_blocks:
            issues.append(f"duplicate block: {pin} {analysis}")
        seen_blocks.add(key)
        if (pin, analysis) != (end_pin, end_analysis) or pin not in PINS or analysis not in ("min", "max"):
            issues.append(f"invalid block identity: {pin} {analysis}")
            continue
        arcs = list(re.finditer(r"(?m)^(\S+) ([v^]) -> (\S+) ([v^])\s*$", body))
        if len(arcs) != 2:
            issues.append(f"expected two output-edge arcs: {pin} {analysis}")
        for index, arc in enumerate(arcs):
            edge = "rise" if arc[4] == "^" else "fall"
            identity = pin, analysis, edge
            if identity in seen:
                issues.append(f"duplicate observation: {pin} {analysis} {edge}")
            seen.add(identity)
            port = pin[len("u_sram/dout")]
            if arc[1] != "clk" + port or arc[2] != "v" or "u_sram/" + arc[3] != pin:
                issues.append(f"arc clock/output mismatch: {pin} {analysis} {edge}")
                continue
            data = body[arc.end():arcs[index + 1].start() if index + 1 < len(arcs) else len(body)]
            pi = re.findall(r"Pi model C2=(\S+) Rpi=(\S+) C1=(\S+), Ceff=(\S+)", data)
            tables = re.findall(r"total_output_net_capacitance\s*=\s*(\S+)", data)
            axes = re.findall(r"total_output_net_capacitance[^\n]*\n\s*\|([^\n]+)", data)
            if (len(pi) != 1 or len(tables) != 2 or len(axes) != 2
                    or len(re.findall(r"(?m)^Delay =", data)) != 1
                    or len(re.findall(r"(?m)^Slew =", data)) != 1):
                issues.append(f"missing/ambiguous pi or table-load values: {pin} {analysis} {edge}")
                continue
            try:
                c2, resistance, c1, ceff = map(float, pi[0])
                delay_load, slew_load = map(float, tables)
                values = (c2, resistance, c1, ceff, delay_load, slew_load)
                if not all(math.isfinite(value) and value >= 0 for value in (*values, c1 + c2)):
                    raise ValueError("nonfinite or negative")
                if delay_load != slew_load:
                    raise ValueError("delay/slew table loads disagree")
                grids = [list(map(float, axis.split())) for axis in axes]
                for grid in grids:
                    if (len(grid) < 2 or not all(math.isfinite(value) and value >= 0 for value in grid)
                            or any(a >= b for a, b in zip(grid, grid[1:]))):
                        raise ValueError("invalid capacitance table axis")
            except ValueError as exc:
                issues.append(f"invalid capacitance values: {pin} {analysis} {edge}: {exc}")
                continue
            observations.append({"pin": pin, "analysis": analysis, "edge": edge,
                                 "table_load_pf": delay_load, "ceff_pf": ceff,
                                 "pi_total_pf": c1 + c2, "pi_c1_pf": c1, "pi_c2_pf": c2,
                                 "pi_resistance_kohm": resistance,
                                 "delay_interpolation_bracket_pf": [grids[0][0], grids[0][-1]],
                                 "slew_interpolation_bracket_pf": [grids[1][0], grids[1][-1]],
                                 "within_interpolation_brackets": all(grid[0] <= delay_load <= grid[-1] for grid in grids)})
    valid_keys = {(row["pin"], row["analysis"], row["edge"]) for row in observations}
    missing = sorted(EXPECTED - valid_keys)
    if missing:
        issues.append(f"missing {len(missing)} valid pin/analysis/edge observations")
    ranges = {field: {"min": min((row[field] for row in observations), default=None),
                      "max": max((row[field] for row in observations), default=None)} for field in FIELDS}
    return {"complete": not issues and len(observations) == 256 and valid_keys == EXPECTED,
            "model_qualified": False, "issues": issues, "observations": observations,
            "ranges": ranges, "units": "pF" if units == ["1pF"] else None,
            "expected_observations": 256, "observed_observations": len(observations),
            "all_within_interpolation_brackets": bool(observations) and all(row["within_interpolation_brackets"] for row in observations),
            "table_axis_scope": "Printed adjacent interpolation brackets only; full Liberty load-axis extent is not reconstructed here",
            "missing_observations": [dict(zip(("pin", "analysis", "edge"), key)) for key in missing],
            "unannotated_drivers": unannotated,
            "scope": "all SRAM output table loads from the loaded STA model; model measurement/PVT qualification remains separate"}
