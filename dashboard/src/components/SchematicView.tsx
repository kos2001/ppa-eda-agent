import { useMemo, useState } from "react";
import type { NetlistGraph, NetlistCell } from "../api/referenceDb";
import { useLang } from "../i18n";
import "./SchematicView.css";

// The gate-level circuit, drawn from Yosys' own netlist.
//
// The console has rendered the physical layout since early on and never
// showed the circuit that layout implements. The data was there: Yosys
// writes a JSON netlist during synthesis, and the pipeline recorded its
// path into runs/ — a directory that gets deleted.
//
// Laid out by dependency depth rather than by a real schematic router:
// inputs on the left, each gate one level right of whatever drives it,
// outputs last. That is honest about what it is — a connectivity view,
// not a draughted schematic — and it makes the thing worth looking for
// visible, which is how deep the logic actually is between registers.
//
// Sequential cells are treated as depth boundaries: a flop's output
// starts a new path rather than continuing the one that fed it, which is
// what stops a counter's feedback loop from making the graph infinite.

const SEQ = /__(df|sdf|dl|edf|sedf)/; // sky130 sequential cell families

interface Node {
  id: string;
  label: string;
  sub: string;
  kind: "input" | "output" | "cell" | "seq";
  depth: number;
  drivers: string[];
}

function buildNodes(graph: NetlistGraph): Node[] {
  // net bit -> id of whatever drives it
  const driverOf = new Map<number, string>();
  for (const p of graph.ports) {
    if (p.direction === "input") p.bits.forEach((b) => driverOf.set(b, `port:${p.name}`));
  }
  for (const c of graph.cells) {
    for (const bits of Object.values(c.outputs)) {
      bits.forEach((b) => driverOf.set(b, `cell:${c.name}`));
    }
  }

  const nodes = new Map<string, Node>();
  for (const p of graph.ports) {
    if (p.direction !== "input") continue;
    nodes.set(`port:${p.name}`, {
      id: `port:${p.name}`, label: p.name, sub: `input${p.bits.length > 1 ? `[${p.bits.length}]` : ""}`,
      kind: "input", depth: 0, drivers: [],
    });
  }
  const cellDrivers = (c: NetlistCell) => {
    const ids = new Set<string>();
    for (const bits of Object.values(c.inputs)) {
      for (const b of bits) {
        const d = driverOf.get(b);
        if (d) ids.add(d);
      }
    }
    return [...ids];
  };
  for (const c of graph.cells) {
    nodes.set(`cell:${c.name}`, {
      id: `cell:${c.name}`, label: c.label,
      sub: c.type.replace(/^sky130_fd_sc_\w+__/, ""),
      kind: SEQ.test(c.type) ? "seq" : "cell",
      depth: 0, drivers: cellDrivers(c),
    });
  }
  for (const p of graph.ports) {
    if (p.direction !== "output") continue;
    const ids = new Set<string>();
    p.bits.forEach((b) => { const d = driverOf.get(b); if (d) ids.add(d); });
    nodes.set(`out:${p.name}`, {
      id: `out:${p.name}`, label: p.name, sub: `output${p.bits.length > 1 ? `[${p.bits.length}]` : ""}`,
      kind: "output", depth: 0, drivers: [...ids],
    });
  }

  // Longest-path depth, with sequential cells terminating a path. The
  // visited set is what keeps a combinational loop (or a mis-derived
  // edge) from recursing forever.
  const depth = (id: string, seen: Set<string>): number => {
    const n = nodes.get(id);
    if (!n || seen.has(id)) return 0;
    seen.add(id);
    if (n.kind === "input") return 0;
    let d = 0;
    for (const p of n.drivers) {
      const parent = nodes.get(p);
      if (!parent) continue;
      // A flop's Q starts a fresh path — that is the register boundary.
      d = Math.max(d, parent.kind === "seq" ? 1 : depth(p, seen) + 1);
    }
    seen.delete(id);
    return d;
  };
  for (const n of nodes.values()) n.depth = depth(n.id, new Set());

  // Outputs always sit last so the drawing reads left to right.
  const maxDepth = Math.max(0, ...[...nodes.values()].map((n) => n.depth));
  for (const n of nodes.values()) if (n.kind === "output") n.depth = maxDepth + 1;

  return [...nodes.values()];
}

const COL_W = 150;
const ROW_H = 46;
const BOX_W = 112;
const BOX_H = 30;

export default function SchematicView({ graph }: { graph: NetlistGraph | null | undefined }) {
  const { t } = useLang();
  const [selected, setSelected] = useState<string | null>(null);

  const nodes = useMemo(() => (graph?.cells ? buildNodes(graph) : []), [graph]);

  if (!graph) return null;
  if (graph.error) return <p className="sa__none">{t("nl_failed")}: {graph.error}</p>;
  if (!nodes.length) return <p className="sa__none">{t("nl_empty")}</p>;

  const columns = new Map<number, Node[]>();
  for (const n of nodes) {
    const col = columns.get(n.depth) ?? [];
    col.push(n);
    columns.set(n.depth, col);
  }
  const depths = [...columns.keys()].sort((a, b) => a - b);
  const pos = new Map<string, { x: number; y: number }>();
  depths.forEach((d, ci) => {
    columns.get(d)!.forEach((n, ri) => {
      pos.set(n.id, { x: ci * COL_W + 10, y: ri * ROW_H + 10 });
    });
  });

  const width = depths.length * COL_W + 20;
  const height = Math.max(...[...columns.values()].map((c) => c.length)) * ROW_H + 20;

  const edges: { from: string; to: string }[] = [];
  for (const n of nodes) for (const d of n.drivers) if (pos.has(d)) edges.push({ from: d, to: n.id });

  const active = (id: string) =>
    selected === null ||
    selected === id ||
    edges.some((e) => (e.from === selected && e.to === id) || (e.to === selected && e.from === id));

  return (
    <div className="nl">
      <div className="nl__head">
        <span className="tab__meta-label">
          {t("nl_title").replace("{top}", graph.top)}
        </span>
        <span className="nl__stats">
          {graph.cell_count} {t("nl_cells")} · {graph.ports.length} {t("nl_ports")}
          {graph.truncated && ` · ${t("nl_truncated")}`}
        </span>
      </div>

      {graph.cell_types && (
        <div className="nl__hist">
          {graph.cell_types.slice(0, 8).map((h) => (
            <span key={h.type}>
              <code>{h.type.replace(/^sky130_fd_sc_\w+__/, "")}</code>
              <b>{h.count}</b>
            </span>
          ))}
        </div>
      )}

      <div className="nl__scroll">
        <svg width={width} height={height} role="img" aria-label="gate-level netlist">
          {edges.map((e, i) => {
            const a = pos.get(e.from)!;
            const b = pos.get(e.to)!;
            const x1 = a.x + BOX_W;
            const y1 = a.y + BOX_H / 2;
            const x2 = b.x;
            const y2 = b.y + BOX_H / 2;
            const mid = (x1 + x2) / 2;
            return (
              <path
                key={i}
                className={`nl__edge ${active(e.from) && active(e.to) ? "" : "nl__edge--dim"}`}
                d={`M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`}
              />
            );
          })}
          {nodes.map((n) => {
            const p = pos.get(n.id)!;
            return (
              <g
                key={n.id}
                className={`nl__node nl__node--${n.kind} ${active(n.id) ? "" : "nl__node--dim"}`}
                transform={`translate(${p.x},${p.y})`}
                onMouseEnter={() => setSelected(n.id)}
                onMouseLeave={() => setSelected(null)}
              >
                <rect width={BOX_W} height={BOX_H} rx={n.kind === "cell" || n.kind === "seq" ? 4 : 14} />
                <text x={6} y={12}>{n.label.slice(0, 16)}</text>
                <text x={6} y={23} className="nl__sub">{n.sub.slice(0, 18)}</text>
              </g>
            );
          })}
        </svg>
      </div>
      <p className="nl__caveat">{t("nl_caveat")}</p>
    </div>
  );
}
