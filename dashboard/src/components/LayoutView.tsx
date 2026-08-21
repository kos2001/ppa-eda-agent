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

export default function LayoutView({ layout }: { layout: LayoutSummary }) {
  const { die, cells, nets } = layout;
  if (!die) return <p className="layout-view__empty">no die area in this run's DEF</p>;

  const [x0, y0, x1, y1] = die;
  const W = x1 - x0;
  const H = y1 - y0;
  // DEF y is bottom-up; SVG y is top-down — flip so origin reads bottom-left.
  const Y = (cy: number) => y1 - cy;

  const usedLayers = new Set<string>();
  for (const net of nets) for (const seg of net.segments) usedLayers.add(seg.layer);

  return (
    <div className="layout-view">
      <svg
        viewBox={`${x0 - W * 0.03} ${-H * 0.03} ${W * 1.06} ${H * 1.06}`}
        width="100%"
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label="Real placement and routing from OpenLane's DEF output"
      >
        <rect x={x0} y={0} width={W} height={H} className="layout-view__die" />

        {cells.map((c) => (
          <rect
            key={c.inst}
            x={c.x}
            y={Y(c.y + c.h)}
            width={c.w}
            height={c.h}
            className="layout-view__cell"
          >
            <title>{`${c.inst}\n${c.master}\n(${c.x.toFixed(2)}, ${c.y.toFixed(2)}) ${c.orient}`}</title>
          </rect>
        ))}

        {nets.map((net) =>
          net.segments.map((seg, i) => (
            <line
              key={`${net.name}-${i}`}
              x1={seg.x1}
              y1={Y(seg.y1)}
              x2={seg.x2}
              y2={Y(seg.y2)}
              stroke={METAL_COLORS[seg.layer] ?? "#888"}
              strokeWidth={Math.max(W, H) * 0.0025}
              strokeLinecap="round"
            >
              <title>{`${net.name} (${seg.layer})`}</title>
            </line>
          ))
        )}
      </svg>
      <div className="layout-view__legend">
        <span className="layout-view__legend-item">
          <span className="layout-view__legend-swatch layout-view__legend-swatch--cell" />
          {cells.length} standard cell{cells.length === 1 ? "" : "s"}
        </span>
        {[...usedLayers].sort().map((layer) => (
          <span key={layer} className="layout-view__legend-item">
            <span
              className="layout-view__legend-swatch"
              style={{ background: METAL_COLORS[layer] ?? "#888" }}
            />
            {layer}
          </span>
        ))}
      </div>
    </div>
  );
}
