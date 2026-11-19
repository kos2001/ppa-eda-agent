"""Join final antenna report rows to netlist loads; missing geometry stays unknown.

This is a read-only diagnostic. A diode count alone does not establish where
along a route protection is effective. Use the exact same run's routed DEF/ODB
before choosing a diode location or layer change.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path

from fanout_loads import parse_netlist, is_diode


def normalize(name):
    return name.replace("\\", "").strip()


def report(netlist, antenna):
    netlist, antenna = Path(netlist), Path(antenna)
    opener = gzip.open if netlist.suffix == ".gz" else open
    with opener(netlist, "rt", encoding="utf-8") as stream:
        drivers, loads = parse_netlist(stream.read())
    drivers = {normalize(k): v for k, v in drivers.items()}
    loads = {normalize(k): v for k, v in loads.items()}
    rows = []
    for line in antenna.read_text(encoding="utf-8").splitlines():
        parts = [x.strip() for x in line.strip().strip("│").split("│")]
        if len(parts) != 6:
            continue
        try:
            ratio, partial, required = map(float, parts[:3])
        except ValueError:
            continue
        net, pin, layer = parts[3:]
        name = normalize(net)
        sinks = loads.get(name)
        driver = drivers.get(name)
        rows.append({"net": name, "pin": normalize(pin), "layer": layer,
                     "ratio": ratio, "partial": partial, "required": required,
                     "driver": ({"instance": driver[0], "cell": driver[1]} if driver else None),
                     "matched_net": sinks is not None,
                     "diodes": ([n for n, c in sinks if is_diode(c, n)] if sinks is not None else None),
                     "logic_loads": ([{"instance": n, "cell": c} for n, c in sinks
                                      if not is_diode(c, n)] if sinks is not None else None),
                     "route_geometry": None})
    if not rows:
        raise ValueError("No antenna violation rows parsed; empty/unknown is not a clean result")
    return {"schema": 1, "violation_pin_rows": len(rows),
            "violating_nets": len({r["net"] for r in rows}), "rows": rows,
            "sources": [{"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                        for p in (netlist, antenna)],
            "limitations": ["No route geometry supplied; layer lengths and diode locations are unknown",
                            "Flat SKY130 netlist parsing does not qualify arbitrary cell pin directions"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--netlist", type=Path, required=True)
    parser.add_argument("--antenna", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    result = report(args.netlist, args.antenna)
    text = json.dumps(result, indent=2) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text)
