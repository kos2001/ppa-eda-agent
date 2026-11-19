"""Bounded CMOS review layout using real symbol interfaces and native verification.

Group transistors by conduction topology, place PMOS above NMOS and draw safe
orthogonal connections. Where a route would collide with other pins or junctions,
retain explicit same-net labels. This is a presentation transform, not synthesis.
Every activation requires identical native xschem netlists before and after.
"""
from __future__ import annotations

import argparse
from collections import defaultdict, deque
import hashlib
import json
from pathlib import Path
import re
import tempfile

import custom_bridge
import schematic_inspect as si

VERSION = "cmos-review-v3"
MAX_DEVICES = 80
SUPPLY = re.compile(r"^(VDD|VSS|VPWR|VGND|GND|VCC|VEE|VNB|VPB)$", re.I)
POSITIVE = re.compile(r"^(VDD|VPWR|VCC)$", re.I)
NEGATIVE = re.compile(r"^(VSS|VGND|GND|VEE)$", re.I)


def on_segment(p, a, b):
    return (a[0] == b[0] == p[0] and min(a[1], b[1]) <= p[1] <= max(a[1], b[1])) or (a[1] == b[1] == p[1] and min(a[0], b[0]) <= p[0] <= max(a[0], b[0]))


def topology(snapshot):
    """Independent source comparison ignores placement and generated labels."""
    return [(c["name"], c["symbol"].removesuffix(".sym"), c["attributes"], [(p["name"], p["net"]) for p in c["pins"]])
            for c in snapshot["components"] if c["type"] not in si.LABEL_TYPES]


def plan(source: Path, root: Path = si.ROOT) -> str:
    root = root.resolve()
    snap = si.inspect_source(source, root)
    if snap["issues"]:
        raise ValueError("source geometry has unresolved review notes")
    devices = [c for c in snap["components"] if c["type"] not in si.LABEL_TYPES]
    if not devices or len(devices) > MAX_DEVICES or any(c["type"] not in {"nmos", "pmos"} for c in devices):
        raise ValueError("connected CMOS layout requires 1–80 resolved MOS devices")
    raw = {si.attributes(f[-1]).get("name"): f for _, f in si.records(snap["source_text"]) if f[0] == "C"}
    pinmap = {c["name"]: {p["name"]: p["net"] for p in c["pins"]} for c in devices}
    if any(set(p) != {"D", "G", "S", "B"} for p in pinmap.values()):
        raise ValueError("layout requires exact D/G/S/B symbol interfaces")
    # Separate logic stages through channel connectivity, not shared global rails
    # or gate inputs (a buffer's two inverters remain separate stages).
    owners = defaultdict(list)
    for i, c in enumerate(devices):
        for pin in ["D", "S"]:
            net = pinmap[c["name"]][pin]
            if not SUPPLY.fullmatch(net):
                owners[net].append(i)
    adjacency = [set() for _ in devices]
    for ids in owners.values():
        for i in ids:
            adjacency[i].update(ids)
    seen, groups = set(), []
    for i in range(len(devices)):
        if i in seen:
            continue
        pending, group = [i], []
        while pending:
            j = pending.pop()
            if j in seen:
                continue
            seen.add(j); group.append(j); pending.extend(sorted(adjacency[j], reverse=True))
        groups.append(sorted(group))

    def ranks(ids, typ):
        graph = defaultdict(set)
        for i in ids:
            c = devices[i]
            if c["type"] != typ:
                continue
            p = pinmap[c["name"]]; graph[p["D"]].add(p["S"]); graph[p["S"]].add(p["D"])
        rail_pattern = POSITIVE if typ == "pmos" else NEGATIVE
        distance = {n: 0 for n in graph if rail_pattern.fullmatch(n)}
        queue = deque(distance)
        while queue:
            n = queue.popleft()
            for other in sorted(graph[n]):
                if other not in distance:
                    distance[other] = distance[n] + 1; queue.append(other)
        rank = {}
        for i in ids:
            c = devices[i]
            if c["type"] == typ:
                p = pinmap[c["name"]]; rank[i] = min(distance.get(p["D"], 0), distance.get(p["S"], 0))
        return rank

    placements, rails, obstacles = {}, [], []
    gx, gy, row_height = 320, 120, 0
    for group_no, ids in enumerate(groups):
        rp, rn = ranks(ids, "pmos"), ranks(ids, "nmos")
        p_rows = max(rp.values(), default=-1) + 1
        n_rows = max(rn.values(), default=-1) + 1
        columns = {}
        width_cols = 1
        for rank in [rp, rn]:
            for level in sorted(set(rank.values())):
                used = set()
                for i in ids:
                    if rank.get(i) != level:
                        continue
                    pn = pinmap[devices[i]["name"]]
                    neighbors = [columns[j] for j in ids if j in columns and j in rank and rank[j] == level - 1
                                 and {pn["D"], pn["S"]}.intersection({pinmap[devices[j]["name"]]["D"], pinmap[devices[j]["name"]]["S"]})]
                    col = next((n for n in neighbors if n not in used), None)
                    if col is None:
                        col = next(k for k in range(len(ids) + 1) if k not in used)
                    columns[i] = col; used.add(col); width_cols = max(width_cols, col + 1)
        width = width_cols * 420 + 220
        height = (p_rows + n_rows) * 180 + 220
        if group_no and group_no % 2 == 0:
            gx = 320; gy += row_height + 180; row_height = 0
        row_height = max(row_height, height)
        middle = gy + 120 + max(0, p_rows - 1) * 180 + 100
        for i in ids:
            y = gy + 120 + rp[i] * 180 if i in rp else middle + 100 + (n_rows - rn[i] - 1) * 180
            x = gx + columns[i] * 420
            placements[devices[i]["name"]] = (x, y)
            obstacles.append((x - 60, y - 50, x + 145, y + 50))
        for typ, rank, rail_y, pattern in [("pmos", rp, gy, POSITIVE), ("nmos", rn, middle + 100 + max(0, n_rows - 1) * 180 + 120, NEGATIVE)]:
            names = sorted({pinmap[devices[i]["name"]][k] for i in rank for k in ["D", "S"] if pattern.fullmatch(pinmap[devices[i]["name"]][k])})
            # Separate distinct supply domains rather than shorting them.
            if len(names) == 1:
                rails.append((names[0], (gx - 120, rail_y), (gx + (width_cols - 1) * 420 + 200, rail_y)))
        gx += width + 100

    wires, ends, all_pins = [], defaultdict(list), []
    contracts = json.loads(Path(si.__file__).with_name("schematic_symbols.json").read_text())["symbols"]
    for c in devices:
        x, y = placements[c["name"]]
        info = si.symbol_info(c["symbol"], source, root, contracts)
        if not info:
            raise ValueError("symbol interface changed during layout")
        for p in info["pins"]:
            pos = (x + p["x"], y + p["y"])
            net = pinmap[c["name"]][p["name"]]
            if p["name"] == "G":
                end = (pos[0] - 70, pos[1])
            elif p["name"] == "B":
                end = (pos[0] + 150, pos[1])
            else:
                end = (pos[0], pos[1] + (45 if p["y"] > 0 else -45))
            wires.append((pos, end, net)); ends[net].append(end); all_pins.append((pos, net))
    for net, a, b in rails:
        # Connect each supply terminal to the nearest point on its rail rather
        # than routing a detour to a distant rail endpoint.
        ends[net].extend((pos[0], a[1]) for pos in list(ends[net])
                         if min(a[0], b[0]) <= pos[0] <= max(a[0], b[0]))
        wires.append((a, b, net)); ends[net].extend([a, b])

    def clear_segment(a, b, net):
        if a == b:
            return True
        if a[0] != b[0] and a[1] != b[1]:
            return False
        # Routes may not pass through a symbol body/parameter annotation.
        for x0, y0, x1, y1 in obstacles:
            if a[0] == b[0]:
                if x0 < a[0] < x1 and max(min(a[1], b[1]), y0) < min(max(a[1], b[1]), y1):
                    return False
            elif y0 < a[1] < y1 and max(min(a[0], b[0]), x0) < min(max(a[0], b[0]), x1):
                return False
        if any(other != net and on_segment(p, a, b) for p, other in all_pins):
            return False
        for u, v, other in wires:
            if other == net:
                continue
            # Bare interior crossings are allowed; a T or endpoint intersection
            # is an electrical junction and must never connect different nets.
            if any(on_segment(p, u, v) for p in [a, b]) or any(on_segment(p, a, b) for p in [u, v]):
                return False
        return True

    labels = []
    for net, points in ends.items():
        points = list(dict.fromkeys(points))
        parent = list(range(len(points)))
        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]; i = parent[i]
            return i
        # A drawn rail already connects its ends and any stubs on it. Do not
        # route a second rectangle around that existing connection.
        for a, b, existing_net in wires:
            if existing_net != net:
                continue
            touched = [i for i, pos in enumerate(points) if on_segment(pos, a, b)]
            for i in touched[1:]:
                parent[find(i)] = find(touched[0])
        pairs = sorted((abs(a[0] - b[0]) + abs(a[1] - b[1]), i, j) for i, a in enumerate(points) for j, b in enumerate(points) if i < j)
        for _, i, j in pairs:
            if find(i) == find(j):
                continue
            a, b = points[i], points[j]
            candidates = [[a, b], [a, (b[0], a[1]), b], [a, (a[0], b[1]), b]]
            for dx in [-100, 100]:
                candidates.append([a, (a[0] + dx, a[1]), (a[0] + dx, b[1]), b])
            for dy in [-90, 90]:
                candidates.append([a, (a[0], a[1] + dy), (b[0], a[1] + dy), b])
            route = next((p for p in candidates if all(clear_segment(u, v, net) for u, v in zip(p, p[1:]))), None)
            if route:
                wires.extend((u, v, net) for u, v in zip(route, route[1:]) if u != v)
                parent[find(i)] = find(j)
        labelled = set()
        for i, pos in enumerate(points):
            key = find(i)
            if key not in labelled:
                labelled.add(key); labels.append((pos, net))

    out = [f'v {{xschem version=3.4.4 file_version=1.2\n* {VERSION}: topology-grouped CMOS review layout.\n* Device and port attributes retained; native netlist equality required.\n}}']
    for _, f in si.records(snap["source_text"]):
        if f[0] in {"G", "K", "V", "S", "E", "F"}:
            out.append(f'{f[0]} {{{f[1]}}}')
    out.extend(f'N {a[0]:g} {a[1]:g} {b[0]:g} {b[1]:g} {{lab={net}}}' for a, b, net in wires)
    # Keep port declaration order exactly as the imported source uses it.
    for i, c in enumerate(c for c in snap["components"] if c["type"] in {"ipin", "opin", "iopin"}):
        f = raw[c["name"]]
        symbol = f[1] if f[1].endswith('.sym') else f[1] + '.sym'
        out.append(f'C {{{symbol}}} 40 {120 + i * 35} 0 0 {{{f[-1]}}}')
    taken = {c["name"] for c in snap["components"]}
    for i, (pos, net) in enumerate(labels):
        name = f'__review_net_{i}'
        while name in taken:
            name += '_'
        out.append(f'C {{devices/lab_pin.sym}} {pos[0]:g} {pos[1]:g} 0 1 {{name={name} lab={net}}}')
    for c in devices:
        f = raw[c["name"]]; x, y = placements[c["name"]]
        symbol = f[1] if f[1].endswith('.sym') else f[1] + '.sym'
        out.append(f'C {{{symbol}}} {x:g} {y:g} 0 0 {{{f[-1]}}}')
    return '\n'.join(out) + '\n'


def native_statements(text):
    text = re.sub(r'\n\+\s*', ' ', text)
    return sorted(' '.join(line.split()) for line in text.splitlines() if line.strip() and not line.lstrip().startswith('*'))


def redraft(source: Path, root: Path = si.ROOT, verify_native: bool = True) -> dict:
    root = root.resolve()
    source = source.resolve()
    old = source.read_text()
    if VERSION in old:
        return {"ok": True, "changed": False, "layout": VERSION}
    draft = plan(source, root)
    with tempfile.TemporaryDirectory(prefix='ppa-schematic-layout-') as temp:
        work = Path(temp); new_dir = work / 'draft'; new_dir.mkdir()
        candidate = new_dir / source.name; candidate.write_text(draft)
        before = si.inspect_source(source, root)
        after = si.inspect_source(candidate, root)
        if topology(before) != topology(after) or after['issues']:
            raise ValueError('layout changed source pin connectivity or introduced review issues')
        if verify_native:
            baseline = custom_bridge.netlist_schematic(source, out_dir=work / 'baseline')
            revised = custom_bridge.netlist_schematic(candidate, out_dir=work / 'revised')
            # Existing imported cells have implicit, undriven well pins. Preserve
            # those exact native diagnostics; equality does not claim ERC clean.
            usable = lambda r: r.metadata.get('netlist') and Path(r.metadata['netlist']).is_file() and r.metadata.get('returncode') == 0
            if not usable(baseline) or not usable(revised):
                raise ValueError('native netlisting failed; original schematic retained')
            if baseline.errors != revised.errors or any(not e.startswith('Error: undriven node:') for e in baseline.errors):
                raise ValueError('native diagnostics changed; original schematic retained')
            if native_statements(Path(baseline.metadata['netlist']).read_text()) != native_statements(Path(revised.metadata['netlist']).read_text()):
                raise ValueError('native netlists disagree; original schematic retained')
        else:
            raise ValueError('activation requires native verification')
    # No source mutation occurs before both independent comparisons pass.
    source.write_text(draft)
    result = {"ok": True, "changed": True, "layout": VERSION, "native_netlist_identical": True,
              "before_sha256": hashlib.sha256(old.encode()).hexdigest(), "after_sha256": hashlib.sha256(draft.encode()).hexdigest(),
              "devices": before['counts']['devices'], "native_erc_errors": baseline.errors,
              "native_erc_clean": not baseline.errors,
              "symbols": after['symbols']}
    source.with_suffix('.layout.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--cell', required=True)
    args = ap.parse_args()
    print(json.dumps(redraft(si.sheet_path(args.cell)), indent=2))
