import { useMemo, useState } from "react";
import type { CandidateResult } from "../api/referenceDb";
import { OBJECTIVES, axisPosition, objectivePoints } from "./objectives";
import "./ObjectivesChart.css";

const W = 720;
const H = 252;
const PAD = { top: 42, right: 34, bottom: 32, left: 52 };

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
  const [selected, setSelected] = useState<string | null>(null);
  if (points.length < 2 || used.length < 2) return null;

  const axes = OBJECTIVES.filter((o) => used.includes(o.name));
  const x = (i: number) => PAD.left + (i * (W - PAD.left - PAD.right)) / (axes.length - 1);
  const y = (pos: number) => PAD.top + pos * (H - PAD.top - PAD.bottom);
  const colValues = (name: (typeof axes)[number]["name"]) =>
    points.map((p) => p.values[name] as number);
  const position = (tag: string, axis: (typeof axes)[number]) => {
    const p = points.find((q) => q.tag === tag)!;
    return axisPosition(colValues(axis.name), p.values[axis.name] as number);
  };
  const path = (tag: string) =>
    axes.map((a, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(position(tag, a)).toFixed(1)}`).join(" ");

  const order = [...points].sort((a, b) => {
    const rank = (p: (typeof points)[number]) => (p.tag === winnerTag ? 2 : p.onFront ? 1 : 0);
    return rank(a) - rank(b);
  });
  const shown = hover ?? selected ?? winnerTag ?? points[0].tag;
  const shownPoint = points.find((p) => p.tag === shown) ?? points[0];
  const frontCount = points.filter((p) => p.onFront).length;

  return (
    <section className="objchart" aria-label="Winner trade-off comparison">
      <div className="objchart__header">
        <div>
          <span className="tab__meta-label">winner decision field</span>
          <strong>Where did each passing candidate trade one objective for another?</strong>
        </div>
        <span>{points.length} signoff-clean · {frontCount} on Pareto front</span>
      </div>
      <div className="objchart__scroll-hint" aria-hidden="true">scroll across objectives →</div>
      <div className="objchart__plot-wrap">
        <svg viewBox={`0 0 ${W} ${H}`} className="objchart__svg" role="img"
             aria-label="Parallel coordinates: the top of every axis is best within this candidate field">
          <rect x={PAD.left} y={y(0)} width={W - PAD.left - PAD.right} height={y(0.25) - y(0)}
                className="objchart__band objchart__band--best" />
          <rect x={PAD.left} y={y(0.75)} width={W - PAD.left - PAD.right} height={y(1) - y(0.75)}
                className="objchart__band objchart__band--worst" />
          {[0, 0.25, 0.5, 0.75, 1].map((p) => (
            <line key={p} x1={PAD.left} x2={W - PAD.right} y1={y(p)} y2={y(p)}
                  className="objchart__guide" />
          ))}
          <text x={7} y={y(0.12)} className="objchart__rank-label">BEST</text>
          <text x={7} y={y(0.88)} className="objchart__rank-label">WORST</text>
          {axes.map((a, i) => {
            const vals = colValues(a.name);
            const best = a.name === "margin" ? -Math.min(...vals) : Math.min(...vals);
            const worst = a.name === "margin" ? -Math.max(...vals) : Math.max(...vals);
            return (
              <g key={a.name}>
                <line x1={x(i)} x2={x(i)} y1={y(0)} y2={y(1)} className="objchart__axis" />
                <text x={x(i)} y={PAD.top - 23} textAnchor="middle" className="objchart__axis-label">{a.label}</text>
                <text x={x(i)} y={PAD.top - 9} textAnchor="middle" className="objchart__unit">{a.unit}</text>
                <text x={x(i)} y={y(0) - 5} textAnchor="middle" className="objchart__tick">{fmt(best, a.unit)}</text>
                <text x={x(i)} y={y(1) + 14} textAnchor="middle" className="objchart__tick">{fmt(worst, a.unit)}</text>
              </g>
            );
          })}
          {order.map((p) => {
            const active = shown === p.tag;
            const cls = [
              "objchart__line",
              p.onFront ? "objchart__line--front" : "",
              p.tag === winnerTag ? "objchart__line--winner" : "",
              shown && !active ? "objchart__line--dim" : "",
              active ? "objchart__line--active" : "",
            ].filter(Boolean).join(" ");
            return (
              <g key={p.tag} className="objchart__series"
                 onMouseEnter={() => setHover(p.tag)} onMouseLeave={() => setHover(null)}
                 onClick={() => setSelected((current) => current === p.tag ? null : p.tag)}>
                <path d={path(p.tag)} className={cls} />
                <path d={path(p.tag)} className="objchart__hit" />
                {axes.map((a, i) => (
                  <circle key={a.name} cx={x(i)} cy={y(position(p.tag, a))}
                          r={active ? 4.2 : 2.8} className={cls.replaceAll("objchart__line", "objchart__point")} />
                ))}
                <title>{`${p.tag}${p.onFront ? " · Pareto front" : " · dominated"}`}</title>
              </g>
            );
          })}
        </svg>
      </div>
      <div className="objchart__candidates" aria-label="Select a candidate to inspect">
        {points.map((p) => (
          <button key={p.tag} type="button"
                  className={`objchart__candidate ${shown === p.tag ? "objchart__candidate--active" : ""} ${p.tag === winnerTag ? "objchart__candidate--winner" : ""}`}
                  aria-pressed={selected === p.tag}
                  onMouseEnter={() => setHover(p.tag)} onMouseLeave={() => setHover(null)}
                  onFocus={() => setHover(p.tag)} onBlur={() => setHover(null)}
                  onClick={() => setSelected((current) => current === p.tag ? null : p.tag)}>
            <i className={p.tag === winnerTag ? "objchart__dot objchart__dot--winner" : p.onFront ? "objchart__dot objchart__dot--front" : "objchart__dot"} />
            {p.tag}
          </button>
        ))}
      </div>
      <div className="objchart__readout" aria-live="polite">
        <div className="objchart__readout-title">
          <strong>{shownPoint.tag}</strong>
          <span>{shownPoint.tag === winnerTag ? "winner" : shownPoint.onFront ? "Pareto front" : "dominated"}</span>
        </div>
        <div className="objchart__metric-grid">
          {axes.map((a) => {
            const vals = colValues(a.name);
            const flat = vals.every((v) => v === vals[0]);
            const pos = axisPosition(vals, shownPoint.values[a.name] as number);
            return (
              <div className="objchart__metric" key={a.name}>
                <span>{a.label}</span>
                <strong>{fmt(shownPoint.display[a.name], a.unit)}</strong>
                <small>{flat ? "same across field" : pos < 0.01 ? "best in field" : `${Math.round(pos * 100)}% down field range`}</small>
              </div>
            );
          })}
        </div>
      </div>
      <div className="objchart__legend">
        <span><i className="objchart__swatch objchart__swatch--winner" />winner</span>
        <span><i className="objchart__swatch objchart__swatch--front" />Pareto front</span>
        <span><i className="objchart__swatch" />dominated</span>
        <span>Top means best only within this comparable field · click a candidate to pin it</span>
      </div>
    </section>
  );
}
