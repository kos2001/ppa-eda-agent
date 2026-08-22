import { useCallback, useState } from "react";
import type { TimingCorner } from "../api/referenceDb";
import "./SlackChart.css";

// Diverging bars around a zero baseline for real per-corner setup slack —
// adapted from ~/gitspace/kos2001/sign-off's frontend/src/components/
// charts.tsx SlackBars (same "endpoint slack around zero" chart shape,
// built for its own RTL/timing sign-off dashboard). Ported with this
// app's own theme tokens instead of that repo's --div-pos/--div-neg/
// --baseline set, and generalized from single-run endpoint slacks to
// this pipeline's real multi-corner setup WNS (9 PVT corners per
// candidate, from OpenLane's actual metrics.json).
function useTooltip() {
  const [tip, setTip] = useState<{ x: number; y: number; content: string } | null>(null);
  const show = useCallback(
    (e: { clientX: number; clientY: number }, content: string) =>
      setTip({ x: e.clientX + 12, y: e.clientY + 12, content }),
    []
  );
  const hide = useCallback(() => setTip(null), []);
  const node = tip ? (
    <div className="slack-chart__tooltip" style={{ left: tip.x, top: tip.y }}>
      {tip.content}
    </div>
  ) : null;
  return { show, hide, node };
}

export default function SlackChart({ corners }: { corners: TimingCorner[] }) {
  const { show, hide, node } = useTooltip();
  if (corners.length === 0) return null;

  const width = 560;
  const barH = 15;
  const gap = 6;
  const labelW = 130;
  const height = corners.length * (barH + gap);
  const plotW = width - labelW - 70;
  const maxAbs = Math.max(...corners.map((c) => Math.abs(c.setup_wns)), 0.01);
  const zeroX = labelW + plotW / 2;
  const scale = plotW / 2 / maxAbs;
  const worstIdx = corners.reduce(
    (worst, c, i) => (c.setup_wns < corners[worst].setup_wns ? i : worst),
    0
  );

  return (
    <div className="slack-chart">
      <svg width={width} height={height + 18} role="img" aria-label="setup slack per PVT corner">
        <line x1={zeroX} y1={0} x2={zeroX} y2={height} className="slack-chart__baseline" />
        {corners.map((c, i) => {
          const w = Math.abs(c.setup_wns) * scale;
          const x = c.setup_wns < 0 ? zeroX - w : zeroX;
          const y = i * (barH + gap);
          const isWorst = i === worstIdx;
          return (
            <g key={c.corner}>
              <text x={labelW - 8} y={y + barH - 3} textAnchor="end" className="slack-chart__label">
                {c.corner}
              </text>
              <rect
                x={x}
                y={y}
                width={Math.max(w, 1)}
                height={barH}
                rx={3}
                className={c.setup_wns < 0 ? "slack-chart__bar--neg" : "slack-chart__bar--pos"}
                onMouseMove={(e) =>
                  show(e, `${c.corner} · setup WNS ${c.setup_wns.toFixed(3)}ns${isWorst ? " · worst" : ""}`)
                }
                onMouseLeave={hide}
              />
              {isWorst && (
                <text
                  x={x - 6}
                  y={y + barH - 3}
                  textAnchor="end"
                  className="slack-chart__worst-label"
                >
                  {c.setup_wns.toFixed(3)}ns
                </text>
              )}
            </g>
          );
        })}
        <text x={zeroX} y={height + 14} textAnchor="middle" className="slack-chart__label">
          0 ns
        </text>
      </svg>
      {node}
    </div>
  );
}
