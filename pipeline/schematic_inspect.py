"""Read-only sheet review from xschem geometry and actual symbol pin interfaces.

This is a bounded connectivity preview, not a second netlister or ERC verdict.
No Tcl, SPICE, or EDA process is executed. Named labels join disconnected stubs;
wire endpoints and pins join segments, but bare crossing wires do not join.
Unknown symbols/buses remain explicit instead of inventing their connectivity.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
MAX_INSTANCES = 8000
LABEL_TYPES = {"ipin", "opin", "iopin", "label", "noconn"}


def records(text: str):
    """Tokenize brace-delimited multiline records without evaluating Tcl."""
    i, line = 0, 1
    while i < len(text):
        if text[i].isspace():
            line += text[i] == "\n"
            i += 1
            continue
        start_line = line
        fields = []
        while i < len(text) and text[i] != "\n":
            if text[i].isspace():
                i += 1
                continue
            if text[i] == "{":
                i += 1
                start, depth = i, 1
                while i < len(text) and depth:
                    if text[i] == "\\":
                        i += 2
                        continue
                    if text[i] == "{":
                        depth += 1
                    elif text[i] == "}":
                        depth -= 1
                    line += text[i] == "\n"
                    i += 1
                if depth:
                    raise ValueError(f"unterminated record at line {start_line}")
                fields.append(text[start:i - 1])
            else:
                start = i
                while i < len(text) and not text[i].isspace():
                    i += 1
                fields.append(text[start:i])
        if fields:
            yield start_line, fields


def attributes(text: str) -> dict[str, str]:
    pattern = r'([\w:]+)\s*=\s*("(?:\\.|[^"\\])*"|[^\s]+)'
    out = {}
    for key, value in re.findall(pattern, text):
        if value.startswith('"'):
            value = value[1:-1]
            value = re.sub(r'\\(["\\])', r'\1', value)
        out[key] = value
    return out


def safe_file(path: Path, roots: list[Path]) -> Path | None:
    resolved = path.resolve()
    if resolved.is_file() and any(resolved.is_relative_to(r.resolve()) for r in roots):
        return resolved
    return None


def sheet_path(cell: str, root: Path = ROOT) -> Path:
    if not re.fullmatch(r"(analog|gate)/[A-Za-z0-9_][A-Za-z0-9_.-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*", cell):
        raise ValueError("cell must be <analog|gate>/<design>/<cell>")
    kind, design, name = cell.split("/")
    base = root / "pipeline" / ("analog" if kind == "analog" else "designs")
    parent = base / design if kind == "analog" else base / design / "sch"
    found = safe_file(parent / f"{name}.sch", [base])
    if not found:
        raise FileNotFoundError("schematic not found inside the permitted source tree")
    return found


def symbol_info(symbol: str, sheet: Path, root: Path, contracts: dict) -> dict | None:
    # PDK importers omit .sym; hand-drawn project sheets include it.
    symbol = symbol if symbol.endswith(".sym") else symbol + ".sym"
    analog = root / "pipeline" / "analog"
    pdk = root / "pdk" / "sky130A" / "libs.tech" / "xschem"
    allowed = [analog, root / "pipeline" / "designs", pdk]
    # The same project-local and PDK roots used by pipeline/analog/xschemrc.
    candidates = [sheet.parent / symbol, analog / symbol, pdk / symbol]
    for candidate in candidates:
        found = safe_file(candidate, allowed)
        if not found:
            continue
        data = found.read_text(encoding="utf-8")
        props, pins = {}, []
        for _, fields in records(data):
            if fields[0] == "K":
                props = attributes(fields[1])
            if fields[0] == "B" and len(fields) >= 7 and fields[1] == "5":
                x1, y1, x2, y2 = map(float, fields[2:6])
                pin = attributes(fields[6])
                pins.append({"name": pin.get("name", ""), "direction": pin.get("dir", ""),
                             "x": (x1 + x2) / 2, "y": (y1 + y2) / 2})
        master = None
        defaults = attributes(props.get("template", ""))
        if symbol.startswith("sky130_stdcells/") and defaults.get("prefix") == "sky130_fd_sc_hd__":
            master = defaults["prefix"] + Path(symbol).stem
        return {"type": props.get("type", ""), "pins": pins, "master": master,
                "defaults": defaults,
                "source": str(found.relative_to(root)),
                "sha256": hashlib.sha256(data.encode()).hexdigest()}
    return contracts.get(symbol)


def point(x: float, y: float) -> tuple[float, float]:
    return round(x, 6), round(y, 6)


def transform(x: float, y: float, rot: int, flip: int, ox: float, oy: float):
    x = -x if flip else x
    for _ in range(rot):
        x, y = -y, x
    return point(x + ox, y + oy)


def inspect(cell: str, root: Path = ROOT) -> dict:
    root = root.resolve()
    sheet = sheet_path(cell, root)
    result = inspect_source(sheet, root, cell)
    provenance = safe_file(sheet.with_suffix('.layout.json'), [sheet.parent])
    if provenance and provenance.stat().st_size < 100_000:
        try:
            layout = json.loads(provenance.read_text())
            if layout.get('after_sha256') == result['sha256'] and layout.get('symbols') == result['symbols']:
                result['layout'] = layout
                for error in layout.get('native_erc_errors', []):
                    result['issues'].append({'kind': 'native_netlist_diagnostic',
                                            'message': 'Preserved from original native netlisting: ' + str(error)})
        except (ValueError, OSError, AttributeError):
            pass
    return result


def inspect_source(sheet: Path, root: Path = ROOT, cell: str | None = None) -> dict:
    """Internal source reader; HTTP callers must go through sheet_path first."""
    root = root.resolve()
    sheet = sheet.resolve()
    text = sheet.read_text(encoding="utf-8")
    parsed = list(records(text))
    if sum(f[0] == "C" for _, f in parsed) > MAX_INSTANCES:
        raise ValueError("sheet exceeds 8,000 elements; extract a bounded cone for review")
    contracts = json.loads((Path(__file__).with_name("schematic_symbols.json")).read_text())["symbols"]
    components, wires, labels, issues, definitions = [], [], [], [], {}
    positions = set()
    for line, f in parsed:
        if f[0] == "N" and len(f) >= 6:
            a, b = point(*map(float, f[1:3])), point(*map(float, f[3:5]))
            wires.append((a, b, attributes(f[5]).get("lab"), line))
            positions.update([a, b])
        elif f[0] == "C" and len(f) >= 7:
            symbol, x, y, rot, flip = f[1], float(f[2]), float(f[3]), int(f[4]), int(f[5])
            props = attributes(f[6])
            if symbol not in definitions:
                definitions[symbol] = symbol_info(symbol, sheet, root, contracts)
            info = definitions[symbol]
            name = props.get("name", f"@line:{line}")
            defaults = (info or {}).get("defaults", {})
            component = {"name": name, "symbol": symbol, "type": (info or {}).get("type", "unknown"),
                         "line": line, "attributes": props, "defaults": defaults, "pins": [], "child": None,
                         "master": (info or {}).get("master")
                         if props.get("prefix", defaults.get("prefix")) == "sky130_fd_sc_hd__" else None}
            # Navigation is confined to this same approved schematic inventory.
            if info and not str(info["source"]).startswith("https:"):
                child = root / info["source"]
                child = safe_file(child.with_suffix(".sch"), [root / "pipeline" / "analog", root / "pipeline" / "designs"])
                if child:
                    rel = child.relative_to(root / "pipeline").parts
                    if rel[0] == "analog" and len(rel) == 3:
                        component["child"] = f"analog/{rel[1]}/{child.stem}"
                    elif rel[0] == "designs" and len(rel) == 4 and rel[2] == "sch":
                        component["child"] = f"gate/{rel[1]}/{child.stem}"
            if not info:
                issues.append({"kind": "unresolved_symbol", "instance": name, "line": line,
                               "message": f"Pin interface unavailable: {symbol}"})
            for pin in (info or {}).get("pins", []):
                pos = transform(pin["x"], pin["y"], rot, flip, x, y)
                positions.add(pos)
                component["pins"].append({**pin, "x": pos[0], "y": pos[1], "net": None})
                lab = props.get("lab", defaults.get("lab"))
                if component["type"] in LABEL_TYPES and lab:
                    labels.append((pos, lab, line))
            components.append(component)

    parent = {p: p for p in positions}

    def find(p):
        while parent[p] != p:
            parent[p] = parent[parent[p]]
            p = parent[p]
        return p

    def join(a, b):
        parent[find(a)] = find(b)

    rows, columns = defaultdict(list), defaultdict(list)
    for x, y in positions:
        rows[y].append(x)
        columns[x].append(y)
    for values in [*rows.values(), *columns.values()]:
        values.sort()
    for a, b, lab, line in wires:
        if a[1] == b[1]:
            lo, hi = sorted((a[0], b[0]))
            values = rows[a[1]]
            touched = [(v, a[1]) for v in values[bisect_left(values, lo):bisect_right(values, hi)]]
        elif a[0] == b[0]:
            lo, hi = sorted((a[1], b[1]))
            values = columns[a[0]]
            touched = [(a[0], v) for v in values[bisect_left(values, lo):bisect_right(values, hi)]]
        else:
            touched = [a, b]
            issues.append({"kind": "unsupported_diagonal", "line": line,
                           "message": "Diagonal wire: interior connections were not inferred"})
        for p in touched:
            join(a, p)
        if lab:
            labels.append((a, lab, line))

    names = defaultdict(set)
    for pos, lab, _ in labels:
        names[find(pos)].add(lab)
    # Name equality is xschem's intended connection for labelled stubs.
    named_roots = {}
    for pos, lab, _ in labels:
        if lab in named_roots:
            join(pos, named_roots[lab])
        else:
            named_roots[lab] = pos
    names = defaultdict(set)
    for pos, lab, _ in labels:
        names[find(pos)].add(lab)
    net_ids, nets = {}, {}
    ports = []
    for c in components:
        if c["type"] in {"ipin", "opin", "iopin"}:
            ports.append({"name": c["attributes"].get("lab", ""), "direction": c["type"]})
        for pin in c["pins"]:
            p = find(point(pin["x"], pin["y"]))
            if p not in net_ids:
                aliases = sorted(names[p])
                net_id = aliases[0] if aliases else f"@unnamed:{len(net_ids) + 1}"
                net_ids[p] = net_id
                nets[net_id] = {"name": net_id, "aliases": aliases, "named": bool(aliases), "connections": []}
                if len(aliases) > 1:
                    issues.append({"kind": "label_conflict", "instance": c["name"], "line": c["line"],
                                   "message": "Connected labels disagree: " + ", ".join(aliases)})
                if any(re.search(r"\[[^\]]*:[^\]]*\]|,|^\d+\*", n) for n in aliases):
                    issues.append({"kind": "bus_unexpanded", "instance": c["name"], "line": c["line"],
                                   "message": f"Bus label is unexpanded: {net_id}"})
            pin["net"] = net_ids[p]
            nets[pin["net"]]["connections"].append({"instance": c["name"], "pin": pin["name"],
                                                   "type": c["type"], "direction": pin["direction"]})
    devices = [c for c in components if c["type"] not in LABEL_TYPES and c["pins"]]
    for net in nets.values():
        terminals = [p for p in net["connections"] if p["type"] not in LABEL_TYPES]
        intentionally_open = any(p["type"] == "noconn" for p in net["connections"])
        if not net["named"] and len(terminals) == 1 and not intentionally_open:
            issues.append({"kind": "unattached_pin", "instance": terminals[0]["instance"],
                           "message": f"Unattached pin: {terminals[0]['instance']}.{terminals[0]['pin']}"})
    symbol_sources = [{"symbol": name, "source": info["source"], "sha256": info["sha256"]}
                      for name, info in definitions.items() if info]
    return {"cell": cell or sheet.stem, "source": str(sheet.relative_to(root)) if sheet.is_relative_to(root) else str(sheet), "source_text": text,
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
            "modified_at": datetime.fromtimestamp(sheet.stat().st_mtime, timezone.utc).isoformat(),
            "basis": "xschem source geometry + symbol pin interfaces; review preview, not native ERC/LVS",
            "symbols": symbol_sources, "components": components, "nets": list(nets.values()),
            "ports": ports, "issues": issues,
            "counts": {"devices": len(devices), "elements": len(components), "nets": len(nets), "ports": len(ports)}}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cell", required=True)
    args = ap.parse_args()
    try:
        print(json.dumps(inspect(args.cell), ensure_ascii=False))
    except (ValueError, OSError) as exc:
        print(json.dumps({"error": str(exc)}))
        raise SystemExit(1)
