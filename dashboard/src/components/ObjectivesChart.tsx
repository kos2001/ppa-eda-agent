import { useMemo, useState } from "react";
import type { CandidateResult } from "../api/referenceDb";
import { OBJECTIVES, axisPosition, objectivePoints } from "./objectives";
import "./ObjectivesChart.css";

// Parallel coordinates over the objectives a winner is chosen on. Each
// passing candidate is one line; the top of every axis is the best value on
// that axis, so a line that stays near the top is good at everything and a
// line that dives is paying for something. Lines that no other candidate
// beats on every axis (the Pareto front) are drawn solid; the rest are
// faint; the winner is thick and labelled.
//
// Plain SVG rather than recharts: the chart is a few dozen elements, and
// recharts is the largest chunk in the dashboard.

const W = 640;
const H = 220;
const PAD = { top: 34, right: 96, bottom: 26, left: 30 };

function fmt(n: number | undefined, unit: string): string {
  if (n == null) return "—";
  const abs = Math.abs(n);
  const s = abs >= 1000 ? n.toFixed(0) : abs >= 10 ? n.toFixed(1) : n.toFixed(2);
  return `${s} ${unit}`;
}

export default function ObjectivesChart({
  candidates,
  winnerTag,
}: {
  candidates: CandidateResult[];
  winnerTag: string | null | undefined;
}) {
  const { points, used } = useMemo(() => objectivePoints(candidates), [candidates]);
  const [hover, setHover] = useState<string | null>(null);
  if (points.length < 2 || used.length < 2) return null;

  const axes = OBJECTIVES.filter((o) => used.includes(o.name));
  const x = (i: number) => PAD.left + (i * (W - PAD.left - PAD.right)) / (axes.length - 1);
  const y = (pos: number) => PAD.top + pos * (H - PAD.top - PAD.bottom);
  const colValues = (name: (typeof axes)[number]["name"]) =>
    points.map((p) => p.values[name] as number);

  const path = (tag: string) => {
    const p = points.find((q) => q.tag === tag)!;
    return axes
      .map((a, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(axisPosition(colValues(a.name), p.values[a.name] as number)).toFixed(1)}`)
      .join(" ");
  };

  // Faint lines first, then the front, then the winner, so what matters
  // is never hidden under what does not.
  const order = [...points].sort((a, b) => {
    const rank = (t: (typeof points)[number]) => (t.tag === winnerTag ? 2 : t.onFront ? 1 : 0);
    return rank(a) - rank(b);
  });
  const shown = hover ?? winnerTag ?? null;
  const shownPoint = points.find((p) => p.tag === shown);

  return (
    <div className="objchart">
      <div className="tab__meta-label">
        trade-off across the objectives the winner is chosen on · top of an axis is best
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="objchart__svg" role="img"
           aria-label="Parallel-coordinates chart of passing candidates">
        {axes.map((a, i) => {
          const vals = colValues(a.name);
          const best = a.name === "margin" ? -Math.min(...vals) : Math.min(...vals);
          const worst = a.name === "margin" ? -Math.max(...vals) : Math.max(...vals);
          return (
            <g key={a.name}>
              <line x1={x(i)} x2={x(i)} y1={y(0)} y2={y(1)} className="objchart__axis" />
              <text x={x(i)} y={PAD.top - 18} textAnchor="middle" className="objchart__axis-label">
                {a.label}
              </text>
              <text x={x(i)} y={y(0) - 5} textAnchor="middle" className="objchart__tick">
                {fmt(best, a.unit)}
              </text>
              <text x={x(i)} y={y(1) + 13} textAnchor="middle" className="objchart__tick">
                {fmt(worst, a.unit)}
              </text>
            </g>
          );
        })}
        {order.map((p) => {
          const cls = [
            "objchart__line",
            p.onFront ? "objchart__line--front" : "",
            p.tag === winnerTag ? "objchart__line--winner" : "",
            hover && hover !== p.tag ? "objchart__line--dim" : "",
          ].filter(Boolean).join(" ");
          return (
            <path key={p.tag} d={path(p.tag)} className={cls}
                  onMouseEnter={() => setHover(p.tag)} onMouseLeave={() => setHover(null)}>
              <title>{[
                `${p.tag}${p.onFront ? " · on the Pareto front" : " · dominated"}`,
                ...axes.map((a) => `${a.label}: ${fmt(p.display[a.name], a.unit)}`),
              ].join("\n")}</title>
            </path>
          );
        })}
        {winnerTag && points.some((p) => p.tag === winnerTag) && (
          <text x={W - PAD.right + 8} y={y(axisPosition(colValues(axes[axes.length - 1].name),
                points.find((p) => p.tag === winnerTag)!.values[axes[axes.length - 1].name] as number)) + 4}
                className="objchart__winner-label">
            ◉ winner
          </text>
        )}
      </svg>
      <div className="objchart__legend">
        <span><i className="objchart__swatch objchart__swatch--winner" /> winner</span>
        <span><i className="objchart__swatch objchart__swatch--front" /> Pareto front</span>
        <span><i className="objchart__swatch" /> dominated</span>
        {shownPoint && (
          <span className="objchart__readout">
            <b>{shownPoint.tag}</b>
            {axes.map((a) => ` · ${a.label} ${fmt(shownPoint.display[a.name], a.unit)}`).join("")}
          </span>
        )}
      </div>
    </div>
  );
}
