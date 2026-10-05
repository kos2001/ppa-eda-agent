import { memo, useEffect, useMemo, useRef, useState, type PointerEvent } from "react";
import type { LayoutSummary } from "../api/referenceDb";
import { useLang } from "../i18n";
import { cellCoverage } from "./layoutGeometry";
import "./LayoutView.css";

const METAL_COLORS: Record<string, string> = {
  li1: "#8a8f98", met1: "#e0574a", met2: "#4bbf73", met3: "#e8b339", met4: "#4a90d9", met5: "#c76bd6",
};

// Batched paths retain the large-design performance of the original DEF
// viewer. Controls operate on existing geometry; they launch no EDA tools.
function LayoutView({ layout }: { layout: LayoutSummary }) {
  const { lang } = useLang();
  const say = (en: string, ko: string) => lang === "ko" ? ko : en;
  const { die, cells, nets } = layout;
  const drawn = useMemo(() => {
    if (!die || !die.every(Number.isFinite) || die[2] <= die[0] || die[3] <= die[1]) return null;
    const Y = (y: number) => die[3] - y;
    const cellPath = cells.map(c => `M${c.x} ${Y(c.y + c.h)}h${c.w}v${c.h}h${-c.w}Z`).join("");
    const byLayer = new Map<string, string[]>();
    for (const net of nets) for (const seg of net.segments) {
      if (!byLayer.has(seg.layer)) byLayer.set(seg.layer, []);
      byLayer.get(seg.layer)!.push(`M${seg.x1} ${Y(seg.y1)}L${seg.x2} ${Y(seg.y2)}`);
    }
    return { cellPath, layers: [...byLayer].sort(([a], [b]) => a.localeCompare(b)).map(([layer, parts]) => ({ layer, d: parts.join("") })) };
  }, [die, cells, nets]);
  const coverage = useMemo(() => cellCoverage(layout), [layout]);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [showCells, setShowCells] = useState(true);
  const [showCoverage, setShowCoverage] = useState(false);
  const [query, setQuery] = useState("");
  const [selection, setSelection] = useState<{ kind: "cell" | "net"; name: string } | null>(null);
  const [camera, setCamera] = useState({ scale: 1, dx: 0, dy: 0 });
  const [hover, setHover] = useState<string | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const frame = useRef<number | null>(null);
  const drag = useRef<{ x: number; y: number; dx: number; dy: number } | null>(null);
  useEffect(() => () => { if (frame.current !== null) cancelAnimationFrame(frame.current); }, []);
  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return [];
    return [...cells.filter(c => `${c.inst} ${c.master}`.toLowerCase().includes(q)).slice(0, 12).map(c => ({ kind: "cell" as const, name: c.inst })),
      ...nets.filter(n => n.name.toLowerCase().includes(q)).slice(0, 12).map(n => ({ kind: "net" as const, name: n.name }))].slice(0, 20);
  }, [query, cells, nets]);
  const selectedCell = selection?.kind === "cell" ? cells.find(c => c.inst === selection.name) : null;
  const selectedNet = selection?.kind === "net" ? nets.find(n => n.name === selection.name) : null;
  if (!die || !drawn) return <p className="layout-view__empty">{say("No valid die area in this run's DEF", "이 실행의 DEF에 유효한 다이 영역이 없습니다")}</p>;
  const [x0, , x1, y1] = die;
  const W = x1 - x0, H = y1 - die[1];
  const vw = W * 1.06 / camera.scale, vh = H * 1.06 / camera.scale;
  const strokeWidth = Math.max(W, H) * 0.0025 / camera.scale;
  const point = (clientX: number, clientY: number) => {
    const ctm = svgRef.current?.getScreenCTM();
    return ctm ? new DOMPoint(clientX, clientY).matrixTransform(ctm.inverse()) : null;
  };
  function zoom(factor: number) { setCamera(c => ({ ...c, scale: Math.max(1, Math.min(16, c.scale * factor)) })); }
  function move(event: PointerEvent<SVGSVGElement>) {
    const { clientX, clientY } = event;
    const p = point(clientX, clientY);
    if (!p) return;
    if (drag.current) {
      const origin = drag.current;
      const box = svgRef.current!.getBoundingClientRect();
      const ratio = Math.min(box.width / vw, box.height / vh);
      if (ratio <= 0) return;
      setCamera(c => ({ ...c, dx: origin.dx - (clientX - origin.x) / ratio,
        dy: origin.dy - (clientY - origin.y) / ratio }));
      return;
    }
    if (frame.current !== null) return;
    frame.current = requestAnimationFrame(() => {
      frame.current = null;
      const x = p.x, y = y1 - p.y;
      const cell = showCells && cells.find(c => x >= c.x && x <= c.x + c.w && y >= c.y && y <= c.y + c.h);
      if (cell) { setHover(`${cell.inst} · ${cell.master} · (${cell.x.toFixed(2)}, ${cell.y.toFixed(2)}) µm`); return; }
      let nearest: string | null = null, distance = (strokeWidth * 2) ** 2;
      for (const net of nets) for (const seg of net.segments) {
        if (hidden.has(seg.layer)) continue;
        const dx = seg.x2 - seg.x1, dy = seg.y2 - seg.y1, len = dx * dx + dy * dy;
        const t = len ? Math.max(0, Math.min(1, ((x - seg.x1) * dx + (y - seg.y1) * dy) / len)) : 0;
        const d = (seg.x1 + t * dx - x) ** 2 + (seg.y1 + t * dy - y) ** 2;
        if (d < distance) { nearest = `${net.name} (${seg.layer})`; distance = d; }
      }
      setHover(nearest);
    });
  }
  return <div className="layout-view">
    <div className="layout-view__toolbar">
      <button type="button" onClick={() => zoom(1.5)} aria-label={say("Zoom in layout", "레이아웃 확대")}>＋</button>
      <button type="button" onClick={() => zoom(1 / 1.5)} aria-label={say("Zoom out layout", "레이아웃 축소")}>−</button>
      <button type="button" onClick={() => setCamera({ scale: 1, dx: 0, dy: 0 })}>{say("Fit die", "다이 맞춤")}</button>
      <span>{camera.scale.toFixed(1)}× · {say("Drag to pan · arrow keys to move", "드래그로 이동 · 방향키 이동")}</span>
    </div>
    <div className="layout-view__controls">
      <label><input type="checkbox" checked={showCells} onChange={e => setShowCells(e.target.checked)} />{say("Instances", "인스턴스")}</label>
      {drawn.layers.map(({ layer }) => <label key={layer}><input type="checkbox" checked={!hidden.has(layer)} onChange={() => setHidden(prev => {
        const next = new Set(prev); if (next.has(layer)) next.delete(layer); else next.add(layer); return next;
      })} /><i style={{ background: METAL_COLORS[layer] ?? "#888" }} />{layer}</label>)}
      <label><input type="checkbox" checked={showCoverage} onChange={e => setShowCoverage(e.target.checked)} />{say("Cell footprint map", "셀 면적 분포")}</label>
    </div>
    <svg ref={svgRef} viewBox={`${(x0 + x1) / 2 + camera.dx - vw / 2} ${H / 2 + camera.dy - vh / 2} ${vw} ${vh}`}
      width="100%" preserveAspectRatio="xMidYMid meet" role="img" tabIndex={0}
      aria-label={say("Interactive recorded DEF layout", "기록된 DEF 인터랙티브 레이아웃")}
      onPointerMove={move} onPointerDown={e => {
        if (e.button !== 0) return;
        drag.current = { x: e.clientX, y: e.clientY, dx: camera.dx, dy: camera.dy };
        e.currentTarget.setPointerCapture(e.pointerId);
      }} onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }}
      onPointerLeave={() => setHover(null)} onKeyDown={e => {
        if (e.key === "+" || e.key === "=") { e.preventDefault(); zoom(1.5); }
        else if (e.key === "-") { e.preventDefault(); zoom(1 / 1.5); }
        else if (e.key.startsWith("Arrow")) { e.preventDefault(); setCamera(c => ({ ...c,
          dx: c.dx + (e.key === "ArrowRight" ? vw / 10 : e.key === "ArrowLeft" ? -vw / 10 : 0),
          dy: c.dy + (e.key === "ArrowDown" ? vh / 10 : e.key === "ArrowUp" ? -vh / 10 : 0) })); }
      }}>
      <rect x={x0} y={0} width={W} height={H} className="layout-view__die" />
      {showCells && <path d={drawn.cellPath} className="layout-view__cell" />}
      {drawn.layers.filter(({ layer }) => !hidden.has(layer)).map(({ layer, d }) => <path key={layer} data-layer={layer} d={d} fill="none"
        stroke={METAL_COLORS[layer] ?? "#888"} strokeWidth={strokeWidth} strokeLinecap="round" />)}
      {showCoverage && coverage.filter(tile => tile.coverage > 0).map((tile, i) => <rect key={i} x={tile.x} y={y1 - tile.y - tile.h} width={tile.w} height={tile.h}
        fill={`hsl(${200 - Math.min(1, tile.coverage) * 170} 80% 55%)`} opacity={0.65} data-coverage={tile.coverage}>
        <title>{`${(tile.coverage * 100).toFixed(1)}% · (${tile.x.toFixed(1)}, ${tile.y.toFixed(1)}) µm`}</title></rect>)}
      {selectedCell && showCells && <rect x={selectedCell.x} y={y1 - selectedCell.y - selectedCell.h} width={selectedCell.w} height={selectedCell.h}
        fill="none" stroke="var(--text)" strokeWidth={strokeWidth * 2} />}
      {selectedNet && <path d={selectedNet.segments.filter(s => !hidden.has(s.layer)).map(s => `M${s.x1} ${y1 - s.y1}L${s.x2} ${y1 - s.y2}`).join("")}
        fill="none" stroke="var(--text)" strokeWidth={strokeWidth * 2} />}
    </svg>
    <div className="layout-view__legend"><span>{cells.length} {say("instances", "인스턴스")} · {nets.length} {say("nets", "넷")}</span><span className="layout-view__hover">{hover}</span></div>
    {showCoverage && <p className="layout-view__note">{say("16×16 grid · summed recorded cell footprints / tile area. Blue → orange: 0 → 100%+. Geometric coverage; routing congestion and IR drop are not measured here.", "16×16 격자 · 기록된 셀 면적 합 / 격자 면적. 파랑 → 주황: 0 → 100% 이상. 기하학적 면적 분포이며 라우팅 혼잡도와 IR drop 측정값은 아닙니다.")}</p>}
    <div className="layout-view__search"><input type="search" value={query} onChange={e => setQuery(e.target.value)} aria-label={say("Find instance or net", "인스턴스 또는 넷 검색")}
      placeholder={say("Find an instance, master or net…", "인스턴스, 셀 종류, 넷 검색…")} />
      {selection && <button onClick={() => setSelection(null)}>{say("Clear highlight", "강조 해제")}</button>}
      {query.trim() && <div className="layout-view__matches">{matches.length ? matches.map(match => <button key={`${match.kind}:${match.name}`} onClick={() => setSelection(match)}>{match.kind} · {match.name}</button>) : <span>{say("No matching recorded geometry", "일치하는 기록된 도형 없음")}</span>}</div>}
      {selection && <p>{selection.kind} · <code>{selection.name}</code>{selectedCell && ` · ${selectedCell.master} · ${selectedCell.w} × ${selectedCell.h} µm`}{selectedNet && ` · ${selectedNet.segments.length} segments`}</p>}
    </div>
  </div>;
}
export default memo(LayoutView);
