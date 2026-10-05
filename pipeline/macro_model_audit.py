"""Audit actual macro timing axes and declared PVT without qualifying a model."""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import math
import re
from pathlib import Path


def groups(text):
    """Balanced Liberty groups; quoted braces do not affect nesting."""
    cleaned, i, quoted, escaped = [], 0, False, False
    while i < len(text):
        c = text[i]
        if not quoted and text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end < 0:
                raise ValueError("unterminated Liberty comment")
            cleaned.append(" ")
            i = end + 2
            continue
        if not quoted and text.startswith("//", i):
            end = text.find("\n", i + 2)
            cleaned.append("\n")
            i = len(text) if end < 0 else end + 1
            continue
        cleaned.append(c)
        if escaped:
            escaped = False
        elif c == "\\" and quoted:
            escaped = True
        elif c == '"':
            quoted = not quoted
        i += 1
    text = "".join(cleaned)
    pattern = re.compile(r"(\w+)\s*\(([^)]*)\)\s*\{")
    offset = 0
    while match := pattern.search(text, offset):
        depth, quoted, escaped = 1, False, False
        start = match.end()
        end = start
        while end < len(text) and depth:
            c = text[end]
            if escaped:
                escaped = False
            elif c == "\\" and quoted:
                escaped = True
            elif c == '"':
                quoted = not quoted
            elif not quoted:
                depth += (c == "{") - (c == "}")
            end += 1
        if depth:
            raise ValueError("unterminated Liberty group")
        yield match[1], match[2].strip().strip('"'), text[start:end - 1]
        offset = end


def attribute(body, name):
    match = re.search(r"\b" + re.escape(name) + r'\s*:\s*("[^"]*"|[^;]+)\s*;', body)
    return match[1].strip().strip('"') if match else None


def axis(body, index):
    match = re.search(rf'index_{index}\s*\(\s*"([^"]+)"\s*\)', body)
    if not match:
        return None
    values = [float(x.strip()) for x in match[1].split(",")]
    if (not values or any(not math.isfinite(v) for v in values)
            or any(b <= a for a, b in zip(values, values[1:]))):
        raise ValueError("Liberty timing axes must be increasing")
    return values


def expand_pin(name):
    match = re.fullmatch(r"(.+)\[(-?\d+):(-?\d+)\]", name)
    if not match:
        return [name]
    a, b = int(match[2]), int(match[3])
    if abs(a - b) > 4096:
        raise ValueError("unsupported bus width")
    return [f"{match[1]}[{i}]" for i in range(min(a, b), max(a, b) + 1)]


def read_model(path, macro):
    path = Path(path)
    libraries = [g for g in groups(path.read_text()) if g[0] == "library"]
    if len(libraries) != 1:
        raise ValueError("expected one complete Liberty library")
    _, name, body = libraries[0]
    tm = re.fullmatch(r"([\d.]+)(ns|ps|us)", attribute(body, "time_unit") or "")
    if not tm:
        raise ValueError("missing or unsupported Liberty time unit")
    scale = float(tm[1]) * {"ns": 1, "ps": .001, "us": 1000}[tm[2]]
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("invalid Liberty time unit")
    op = [g for g in groups(body) if g[0] == "operating_conditions"]
    default = attribute(body, "default_operating_conditions")
    selected = [g for g in op if g[1] == default] if default else op
    if (default or op) and len(selected) != 1:
        raise ValueError("ambiguous or missing default operating condition")
    conditions = selected[0][2] if len(selected) == 1 else ""
    voltage = attribute(conditions, "voltage") or attribute(body, "nom_voltage")
    temperature = attribute(conditions, "temperature") or attribute(body, "nom_temperature")
    if ((voltage is not None and (not math.isfinite(float(voltage)) or float(voltage) <= 0))
            or (temperature is not None and not math.isfinite(float(temperature)))):
        raise ValueError("invalid operating conditions")
    process = re.search(r"(?:^|_)(TT|SS|FF)(?:_|$)", name, re.I)
    templates = {n: b for k, n, b in groups(body) if k == "lu_table_template"}
    cells = [g for g in groups(body) if g[0] == "cell" and g[1] == macro]
    if len(cells) != 1:
        raise ValueError(f"missing or duplicate macro cell {macro}")
    inputs, arcs = set(), []

    def pins(container, inherited=None):
        for kind, pin, pin_body in groups(container):
            if kind == "bus":
                pins(pin_body, attribute(pin_body, "direction"))
            elif kind == "pin":
                direction = attribute(pin_body, "direction") or inherited
                if direction == "input":
                    inputs.update(expand_pin(pin))
                for tk, _, timing in groups(pin_body):
                    if tk != "timing":
                        continue
                    related = attribute(timing, "related_pin")
                    for table, template, data in groups(timing):
                        if table not in ("cell_rise", "cell_fall", "rise_transition", "fall_transition",
                                         "rise_constraint", "fall_constraint"):
                            continue
                        if template == "scalar":
                            vm = re.search(r'values\s*\(\s*"([^"\n]+)"\s*\)\s*;', data)
                            if (not vm or "," in vm[1] or not math.isfinite(float(vm[1]))
                                    or axis(data, 1) is not None or axis(data, 2) is not None):
                                raise ValueError("invalid scalar timing table")
                            arcs.append({"pin": pin, "related_pin": related,
                                         "timing_type": attribute(timing, "timing_type"),
                                         "table": table, "axes": [], "scalar": True})
                            continue
                        base = templates.get(template)
                        if base is None:
                            raise ValueError(f"unknown timing template {template}")
                        axes = []
                        for i in (1, 2):
                            values = axis(data, i) or axis(base, i)
                            variable = attribute(base, f"variable_{i}")
                            if values is None or variable is None:
                                raise ValueError("missing timing axis or variable")
                            axes.append({"variable": variable, "values": values,
                                         "min_ns": values[0] * scale if "transition" in variable else None,
                                         "max_ns": values[-1] * scale if "transition" in variable else None})
                        vm = re.search(r"values\s*\((.*?)\)\s*;", data, re.S)
                        rows = re.findall(r'"([^"]*)"', vm[1]) if vm else []
                        if len(rows) != len(axes[0]["values"]) or any(
                                len(row.split(",")) != len(axes[1]["values"]) for row in rows):
                            raise ValueError("timing table dimensions do not match axes")
                        for row in rows:
                            if any(not math.isfinite(float(x.strip())) for x in row.split(",")):
                                raise ValueError("nonfinite timing value")
                        arcs.append({"pin": pin, "related_pin": related,
                                     "timing_type": attribute(timing, "timing_type"),
                                     "table": table, "axes": axes})

    pins(cells[0][2])
    if not inputs or not arcs:
        raise ValueError("missing macro inputs or timing tables")
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "library": name, "process": process[1].upper() if process else None,
            "voltage_V": float(voltage) if voltage else None,
            "temperature_C": float(temperature) if temperature else None,
            "inputs": sorted(inputs), "arcs": arcs}


def corner_pvt(corner):
    match = re.fullmatch(r"(?:max|min|nom)_(tt|ss|ff)_(n?\d+)C_(\d+)v(\d+)", corner)
    if not match:
        return None
    return {"process": match[1].upper(),
            "voltage_V": float(match[3] + "." + match[4]),
            "temperature_C": -int(match[2][1:]) if match[2].startswith("n") else int(match[2])}


def check(design_dir, run_dir):
    from model_validity import _resolve, find_reports, macro_configuration
    from macro_slew_audit import read_audit
    design_dir, run_dir = Path(design_dir), Path(run_dir)
    cfg, config_source = macro_configuration(design_dir, run_dir)
    macros = cfg.get("MACROS") or {}
    if not macros:
        return None
    reports = find_reports(run_dir)
    models, records, issues = {}, [], []
    for macro, spec in macros.items():
        for rpt in reports:
            corner = rpt.parent.name
            paths, missing = set(), []
            for pattern, entries in (spec.get("lib") or {}).items():
                if fnmatch.fnmatchcase(corner, pattern):
                    for entry in entries if isinstance(entries, list) else [entries]:
                        path = _resolve(entry, design_dir)
                        if path is not None and path.is_file():
                            paths.add(path)
                        else:
                            missing.append(str(entry))
            record = {"macro": macro, "corner": corner, "expected_pvt": corner_pvt(corner),
                      "instances": sorted((spec.get("instances") or {}).keys())}
            try:
                if missing or len(paths) != 1:
                    raise ValueError("macro corner must select one readable Liberty file")
                path = next(iter(paths))
                key = (path, macro)
                if key not in models:
                    models[key] = read_model(path, macro)
                model = models[key]
                declared = {k: model[k] for k in ("process", "voltage_V", "temperature_C")}
                expected = record["expected_pvt"]
                record.update(declared_pvt=declared, pvt_matches_declared=bool(expected and declared == expected),
                              model_sha256=model["sha256"], timing_tables=len(model["arcs"]))
                if not record["pvt_matches_declared"]:
                    issues.append(f"{macro} {corner}: declared PVT {declared} differs from {expected}")
                # Metadata matches are not proof that a table was measured.
                record["model_qualified"] = False
            except (OSError, ValueError) as exc:
                record.update(model_qualified=False, error=str(exc))
                issues.append(f"{macro} {corner}: {exc}")
            records.append(record)
    expected_inputs = {inst: len(model["inputs"]) for (path, macro), model in models.items()
                       for inst in (macros[macro].get("instances") or {})}
    try:
        audit = read_audit(run_dir, expected_inputs)
        rows = {(r["corner"], r["pin"]): r for r in audit["rows"]}
    except (OSError, ValueError, KeyError) as exc:
        audit, rows = {"coverage_complete": False, "error": str(exc)}, {}
    violations, unknown = [], []
    for record in records:
        if "model_sha256" not in record:
            continue
        model = next(m for m in models.values() if m["sha256"] == record["model_sha256"])
        for inst in record["instances"]:
            for arc in model["arcs"]:
                for index, item in enumerate(arc["axes"], 1):
                    variable = item["variable"]
                    if variable == "total_output_net_capacitance":
                        continue  # Output loads need a separate extracted report.
                    if variable in ("related_pin_transition", "input_net_transition"):
                        pins = [pin for name in (arc["related_pin"] or "").split()
                                for pin in expand_pin(name)]
                    elif variable == "constrained_pin_transition":
                        pins = expand_pin(arc["pin"])
                    else:
                        unknown.append(f"unsupported axis variable {variable}")
                        continue
                    if not pins:
                        unknown.append(f"missing related pin on {arc['pin']} {arc['table']}")
                    for pin in pins:
                        full = f"{inst}/{pin}"
                        row = rows.get((record["corner"], full))
                        if row is None:
                            unknown.append(f"missing input slew {record['corner']} {full}")
                            continue
                        for edge in ("max_rise_ns", "max_fall_ns", "min_rise_ns", "min_fall_ns"):
                            value = row[edge]
                            if value is None:
                                unknown.append(f"unknown input edge {record['corner']} {full} {edge}")
                            elif value > 0 and not item["min_ns"] <= value <= item["max_ns"]:
                                violations.append({"corner": record["corner"], "pin": full, "edge": edge,
                                                   "slew_ns": value, "arc_pin": arc["pin"],
                                                   "table": arc["table"], "axis": index, "variable": variable,
                                                   "range_ns": [item["min_ns"], item["max_ns"]]})
    return {"model_qualified": False, "corner_models": records, "issues": issues,
            "config_source": str(config_source),
            "config_sha256": hashlib.sha256(config_source.read_bytes()).hexdigest(),
            "input_coverage_complete": audit["coverage_complete"],
            "input_axis_extrapolations": violations, "unknown_input_checks": sorted(set(unknown)),
            "scope": "declared PVT, timing-table structure and input axes; output loads and measured functional/PVT provenance remain unqualified"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--design", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(check(args.design, args.run_dir), indent=2))


def summary(result):
    """Keep case records compact; full per-arc checks are a separate artifact."""
    if result is None:
        return None
    return {k: v for k, v in result.items()
            if k not in ("input_axis_extrapolations", "unknown_input_checks")} | {
                "input_axis_extrapolation_count": len(result["input_axis_extrapolations"]),
                "input_axis_examples": result["input_axis_extrapolations"][:6],
                "unknown_input_check_count": len(result["unknown_input_checks"]),
                "unknown_input_examples": result["unknown_input_checks"][:6],
            }


if __name__ == "__main__":
    main()
