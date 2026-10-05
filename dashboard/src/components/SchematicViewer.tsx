import { useCallback, useEffect, useRef, useState } from "react";
import { useLang } from "../i18n";
import { LABEL_TYPES, safeSchematicSvg, type SheetInspection, type SheetSelection } from "./schematicModel";
import "./SchematicViewer.css";

const STEP = 1.3;
type Transform = { scale: number; x: number; y: number };

export default function SchematicViewer({ src, alt, onClose, onFullScreen, expanded, inspection, selection, onSelect }: {
  src: string; alt: string; onClose?: () => void;
  onFullScreen?: () => void;
  expanded?: boolean;
  inspection?: SheetInspection | null; selection?: SheetSelection;
  onSelect?: (value: SheetSelection) => void;
}) {
  const { lang } = useLang();
  const say = (en: string, ko: string) => lang === "ko" ? ko : en;
  const [t, setT] = useState<Transform>({ scale: 1, x: 0, y: 0 });
  const [paper, setPaper] = useState(true);
  const [status, setStatus] = useState("loading");
  const [largeSheet, setLargeSheet] = useState(false);
  const frameRef = useRef<HTMLDivElement>(null);
  const sheetRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const drag = useRef<{ x: number; y: number; ox: number; oy: number; moved: boolean } | null>(null);
  const size = useRef({ width: 1000, height: 700, x: 0, y: 0 });
  const cameraMode = useRef<"fit" | "readable" | "manual">("fit");

  const fit = useCallback(() => {
    const f = frameRef.current, s = size.current;
    if (!f || !svgRef.current) return;
    cameraMode.current = "fit";
    const scale = Math.min((f.clientWidth - 32) / s.width, (f.clientHeight - 32) / s.height);
    setT({ scale, x: (f.clientWidth - s.width * scale) / 2, y: (f.clientHeight - s.height * scale) / 2 });
  }, []);
  const readable = useCallback(() => {
    const f = frameRef.current, svg = svgRef.current;
    if (!f || !svg) return;
    cameraMode.current = "readable";
    const fonts = [...svg.querySelectorAll("text")].map(n => Number(n.getAttribute("font-size"))).filter(n => n > 0).sort((a, b) => a - b);
    const font = fonts[Math.floor(fonts.length / 2)] || 14;
    const scale = Math.max(Math.min((f.clientWidth - 32) / size.current.width, (f.clientHeight - 32) / size.current.height), 12 / font);
    const label = svg.querySelector<SVGGraphicsElement>('text[data-kind="instance"]') ?? svg.querySelector<SVGGraphicsElement>("text");
    let cx = size.current.x + size.current.width / 2, cy = size.current.y + size.current.height / 2;
    if (label) {
      const b = label.getBBox(), matrix = label.transform.baseVal.consolidate()?.matrix;
      const p = new DOMPoint(b.x + b.width / 2, b.y + b.height / 2).matrixTransform(matrix ?? undefined);
      cx = p.x; cy = p.y;
    }
    setT({ scale, x: f.clientWidth / 2 - (cx - size.current.x) * scale, y: f.clientHeight / 2 - (cy - size.current.y) * scale });
  }, []);
  const zoom = useCallback((factor: number, cx?: number, cy?: number) => {
    const f = frameRef.current;
    if (!f) return;
    cameraMode.current = "manual";
    const x = cx ?? f.clientWidth / 2, y = cy ?? f.clientHeight / 2;
    setT(prev => {
      const scale = Math.min(40, Math.max(0.02, prev.scale * factor));
      const applied = scale / prev.scale;
      return { scale, x: x - (x - prev.x) * applied, y: y - (y - prev.y) * applied };
    });
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    svgRef.current = null;
    sheetRef.current?.replaceChildren();
    setStatus("loading");
    fetch(src, { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error(`Drawing unavailable (${response.status})`);
      const svg = safeSchematicSvg(await response.text());
      if (controller.signal.aborted || !sheetRef.current) return;
      const vb = svg.viewBox.baseVal;
      const width = vb.width || Number(svg.getAttribute("width")), height = vb.height || Number(svg.getAttribute("height"));
      if (!(width > 0 && height > 0 && Number.isFinite(width + height))) throw new Error("Invalid drawing dimensions");
      size.current = { width, height, x: vb.x, y: vb.y };
      svg.setAttribute("width", String(width)); svg.setAttribute("height", String(height));
      svg.classList.add("schview__drawing"); svg.setAttribute("role", "img"); svg.setAttribute("aria-label", alt);
      sheetRef.current.replaceChildren(svg);
      svgRef.current = svg;
      setStatus("ready"); fit();
    }).catch(e => { if (!controller.signal.aborted) setStatus(String(e.message ?? e)); });
    return () => controller.abort();
  }, [src, alt, fit]);
  // Change the vector viewport, never scale a composited screenshot of it.
  useEffect(() => {
    const svg = svgRef.current, frame = frameRef.current;
    if (!svg || !frame || status !== "ready") return;
    svg.setAttribute("width", String(frame.clientWidth));
    svg.setAttribute("height", String(frame.clientHeight));
    svg.setAttribute("viewBox", `${size.current.x - t.x / t.scale} ${size.current.y - t.y / t.scale} ${frame.clientWidth / t.scale} ${frame.clientHeight / t.scale}`);
  }, [t, status]);
  useEffect(() => {
    const frame = frameRef.current;
    if (!frame) return;
    const observer = new ResizeObserver(() => {
      if (cameraMode.current === "readable") readable();
      else if (cameraMode.current === "fit") fit();
      else setT(prev => ({ ...prev }));
    }); observer.observe(frame);
    const wheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = frame.getBoundingClientRect();
      zoom(e.deltaY < 0 ? STEP : 1 / STEP, e.clientX - r.left, e.clientY - r.top);
    };
    frame.addEventListener("wheel", wheel, { passive: false });
    return () => { observer.disconnect(); frame.removeEventListener("wheel", wheel); };
  }, [fit, readable, zoom]);
  useEffect(() => { fit(); }, [expanded, fit]);
  useEffect(() => {
    if (!onClose) return;
    const escape = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [onClose]);
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg || status !== "ready") return;
    const components = inspection?.components.filter(c => !LABEL_TYPES.has(c.type)) ?? [];
    const names = new Set(components.map(c => c.name));
    const netNames = new Map(inspection?.nets.flatMap(n => n.aliases.map(alias => [alias, n.name] as const)) ?? []);
    const selectedNet = selection?.kind === "net" ? inspection?.nets.find(n => n.name === selection.name) : null;
    const connected = new Set(selectedNet?.connections.map(c => c.instance) ?? []);
    for (const text of svg.querySelectorAll("text")) {
      const name = text.textContent?.trim() ?? "";
      const kind = names.has(name) ? "instance" : netNames.has(name) ? "net" : "";
      text.classList.toggle("schview__label", !!kind);
      if (kind) { text.dataset.kind = kind; text.dataset.name = kind === "net" ? netNames.get(name) ?? name : name; }
      else { delete text.dataset.kind; delete text.dataset.name; }
      text.classList.toggle("schview__selected", !!selection && (kind === selection.kind && text.dataset.name === selection.name));
      text.classList.toggle("schview__connected", connected.has(name) && kind === "instance");
    }
  }, [inspection, selection, status]);
  useEffect(() => {
    const svg = svgRef.current, frame = frameRef.current;
    if (!svg || !frame || status !== "ready") return;
    const fonts = [...svg.querySelectorAll("text")].map(n => Number(n.getAttribute("font-size"))).filter(n => n > 0).sort((a, b) => a - b);
    const fitted = Math.min((frame.clientWidth - 32) / size.current.width, (frame.clientHeight - 32) / size.current.height);
    const isLarge = (fonts[Math.floor(fonts.length / 2)] ?? 14) * fitted < 9;
    setLargeSheet(isLarge);
    if (isLarge) readable();
  }, [status, inspection?.cell, readable]);

  const focusSelection = () => {
    const svg = svgRef.current, frame = frameRef.current;
    const label = svg?.querySelector<SVGGraphicsElement>(".schview__selected");
    if (!svg || !frame || !label) return;
    cameraMode.current = "manual";
    const b = label.getBBox(), m = label.transform.baseVal.consolidate()?.matrix;
    const c = new DOMPoint(b.x + b.width / 2, b.y + b.height / 2).matrixTransform(m ?? undefined);
    const scale = Math.min(5, Math.max(t.scale, frame.clientWidth / 360));
    setT({ scale, x: frame.clientWidth / 2 - (c.x - size.current.x) * scale, y: frame.clientHeight / 2 - (c.y - size.current.y) * scale });
  };
  return <div className={`schview${paper ? " schview--paper" : ""}`}>
    <div className="schview__frame" ref={frameRef} tabIndex={0} aria-label={say("Schematic canvas", "회로도 캔버스")}
      onKeyDown={e => {
        if (e.key === "+" || e.key === "=") { e.preventDefault(); zoom(STEP); }
        else if (e.key === "-") { e.preventDefault(); zoom(1 / STEP); }
        else if (e.key === "0" || e.key.toLowerCase() === "f") { e.preventDefault(); fit(); }
        else if (e.key === "Escape") onSelect?.(null);
      }}
      onPointerDown={e => { if (e.button !== 0) return; e.currentTarget.focus(); e.currentTarget.setPointerCapture(e.pointerId); drag.current = { x: e.clientX, y: e.clientY, ox: t.x, oy: t.y, moved: false }; }}
      onPointerMove={e => { const d = drag.current; if (!d) return; if (Math.hypot(e.clientX - d.x, e.clientY - d.y) > 4) d.moved = true; if (d.moved) { cameraMode.current = "manual"; setT(prev => ({ ...prev, x: d.ox + e.clientX - d.x, y: d.oy + e.clientY - d.y })); } }}
      onPointerUp={e => {
        if (drag.current && !drag.current.moved) {
          const target = document.elementFromPoint(e.clientX, e.clientY)?.closest<SVGElement>("text[data-kind]");
          if (target && e.currentTarget.contains(target)) onSelect?.({ kind: target.dataset.kind as "instance" | "net", name: target.dataset.name! });
        }
        drag.current = null;
      }} onPointerCancel={() => { drag.current = null; }} onDoubleClick={fit}>
      <div ref={sheetRef} className="schview__sheet" />
      {status !== "ready" && <p className="schview__status" role={status === "loading" ? "status" : "alert"}>{status === "loading" ? say("Reading xschem drawing…", "xschem 회로도를 읽는 중…") : status}</p>}
      <span className="schview__badge">xschem · {say("source drawing", "원본 회로도")}</span>
    </div>
    <div className="schview__bar">
      <button type="button" onClick={() => zoom(1 / STEP)} aria-label={say("Zoom out", "축소")}>−</button>
      <span className="schview__zoom">{Math.round(t.scale * 100)}%</span>
      <button type="button" onClick={() => zoom(STEP)} aria-label={say("Zoom in", "확대")}>+</button>
      <button type="button" onClick={fit}>{say("Fit sheet", "전체 보기")}</button>
      <button type="button" onClick={readable}>{say("Readable view", "읽기 크기")}</button>
      {onFullScreen && <button type="button" onClick={onFullScreen}>{say("Expand canvas", "도면 전체화면")}</button>}
      <button type="button" onClick={focusSelection} disabled={!selection}>{say("Locate selection", "선택 위치")}</button>
      <button type="button" onClick={() => setPaper(v => !v)} aria-pressed={paper}>{say("Paper", "도면 배경")}</button>
      <span className="schview__hint">{say("scroll · zoom / drag · pan / click · inspect", "휠 · 확대 / 드래그 · 이동 / 이름 클릭 · 검사")}</span>
      {onClose && <button type="button" className="schview__close" onClick={onClose}>{say("Close", "닫기")}</button>}
      {largeSheet && <small className="schview__large-note">{say("Large sheet · readable section opens first; Fit sheet shows all.", "큰 회로는 읽을 수 있는 부분부터 열립니다. 전체 보기로 모두 확인하세요.")}</small>}
    </div>
  </div>;
}
