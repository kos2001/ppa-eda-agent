import { memo, useMemo, useRef, useState, type MouseEvent } from "react";
import type { LayoutSummary } from "../api/referenceDb";
import "./LayoutView.css";

// Real placement (COMPONENTS) + real routed-wire geometry (NETS'
// ROUTED/NEW segments), parsed by pipeline/def_layout.py straight from
// OpenLane's own DEF output — nothing here is synthesized or
// approximated. Rendering approach (layer-tinted rects/lines on a dark
// canvas, metal-stack color coding) adapted from
// ~/gitspace/ip-dev-fde/strongarm_sim's webapp/src/components/
// LayoutView.tsx + virtuoso.ts, a working "Virtuoso Layout XL"-style
// viewer built for that sibling project's synthesized GDS — same visual
// language, applied here to real DEF/LEF placement+routing instead.
const METAL_COLORS: Record<string, string> = {
  li1: "#8a8f98",
  met1: "#e0574a",
  met2: "#4bbf73",
  met3: "#e8b339",
  met4: "#4a90d9",
  met5: "#c76bd6",
};

// Drawn as one <path> for every cell and one per metal layer, not one
// element per shape. The largest aes candidate has 13,195 cells and
// 123,741 routed segments: as individual <rect>/<line> elements, each
// with its own <title>, that was ~274k DOM nodes, and opening the row
// froze the page — then froze it again on every parent re-render (the
// 15 s poll, a streaming diagnosis). Paths are built once per layout.
// What the per-element <title>s told you is now one hover readout,
// found by hit-testing the geometry under the pointer.
function LayoutView({ layout }: { layout: LayoutSummary }) {
  const { die, cells, nets } = layout;
  const drawn = useMemo(() => {
    if (!die) return null;
    const [, , , y1] = die;
    // DEF y is bottom-up; SVG y is top-down — flip so origin reads bottom-left.
    const Y = (cy: number) => y1 - cy;
    const cellPath = cells
      .map((c) => `M${c.x} ${Y(c.y + c.h)}h${c.w}v${c.h}h${-c.w}Z`)
      .join("");
    const byLayer = new Map<string, string[]>();
    for (const net of nets) {
      for (const seg of net.segments) {
        let parts = byLayer.get(seg.layer);
        if (!parts) byLayer.set(seg.layer, (parts = []));
        parts.push(`M${seg.x1} ${Y(seg.y1)}L${seg.x2} ${Y(seg.y2)}`);
      }
    }
    const layers = [...byLayer.keys()].sort();
    return {
      cellPath,
      layers: layers.map((layer) => ({ layer, d: byLayer.get(layer)!.join("") })),
    };
  }, [die, cells, nets]);

  const svgRef = useRef<SVGSVGElement>(null);
  const frame = useRef<number | null>(null);
  const [hover, setHover] = useState<string | null>(null);

  if (!die || !drawn) return <p className="layout-view__empty">no die area in this run's DEF</p>;

  const [x0, y0, x1, y1] = die;
  const W = x1 - x0;
  const H = y1 - y0;
  const strokeWidth = Math.max(W, H) * 0.0025;

  const onMove = (e: MouseEvent<SVGSVGElement>) => {
    const { clientX, clientY } = e;
    if (frame.current !== null) return;
    frame.current = requestAnimationFrame(() => {
      frame.current = null;
      const svg = svgRef.current;
      const ctm = svg?.getScreenCTM();
      if (!svg || !ctm) return;
      const pt = new DOMPoint(clientX, clientY).matrixTransform(ctm.inverse());
      // Back to DEF coordinates.
      const x = pt.x;
      const y = y1 - pt.y;
      const cell = cells.find((c) => x >= c.x && x <= c.x + c.w && y >= c.y && y <= c.y + c.h);
      if (cell) {
        setHover(`${cell.inst} · ${cell.master} · (${cell.x.toFixed(2)}, ${cell.y.toFixed(2)}) ${cell.orient}`);
        return;
      }
      let best: string | null = null;
      let bestD = (strokeWidth * 2) ** 2;
      for (const net of nets) {
        for (const seg of net.segments) {
          const dx = seg.x2 - seg.x1;
          const dy = seg.y2 - seg.y1;
          const len2 = dx * dx + dy * dy;
          const t = len2 ? Math.max(0, Math.min(1, ((x - seg.x1) * dx + (y - seg.y1) * dy) / len2)) : 0;
          const ex = seg.x1 + t * dx - x;
          const ey = seg.y1 + t * dy - y;
          const d = ex * ex + ey * ey;
          if (d < bestD) {
            bestD = d;
            best = `${net.name} (${seg.layer})`;
          }
        }
      }
      setHover(best);
    });
  };

  return (
    <div className="layout-view">
      <svg
        ref={svgRef}
        viewBox={`${x0 - W * 0.03} ${-H * 0.03} ${W * 1.06} ${H * 1.06}`}
        width="100%"
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label="Real placement and routing from OpenLane's DEF output"
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
      >
        <rect x={x0} y={0} width={W} height={H} className="layout-view__die" />
        <path d={drawn.cellPath} className="layout-view__cell" />
        {drawn.layers.map(({ layer, d }) => (
          <path
            key={layer}
            d={d}
            fill="none"
            stroke={METAL_COLORS[layer] ?? "#888"}
            strokeWidth={strokeWidth}
            strokeLinecap="round"
          />
        ))}
      </svg>
      <div className="layout-view__legend">
        <span className="layout-view__legend-item">
          <span className="layout-view__legend-swatch layout-view__legend-swatch--cell" />
          {cells.length} standard cell{cells.length === 1 ? "" : "s"}
        </span>
        {drawn.layers.map(({ layer }) => (
          <span key={layer} className="layout-view__legend-item">
            <span
              className="layout-view__legend-swatch"
              style={{ background: METAL_COLORS[layer] ?? "#888" }}
            />
            {layer}
          </span>
        ))}
        {hover && <span className="layout-view__hover">{hover}</span>}
      </div>
    </div>
  );
}

export default memo(LayoutView);
