import { useEffect, useState } from "react";
import { API_BASE_URL as BACKEND } from "../api/config";
import { useLang } from "../i18n";
import SchematicViewer from "./SchematicViewer";
import { LABEL_TYPES, type SheetInspection, type SheetSelection } from "./schematicModel";
import "./SchematicWorkspace.css";

function saveFile(text: string, name: string, type: string) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a"); a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function SchematicWorkspace({ cell, onOpen, parents, onBack, onStandardCell, opening }: {
  cell: string; onOpen: (id: string) => void; parents: string[]; onBack: () => void;
  onStandardCell: (master: string) => void; opening: boolean;
}) {
  const { lang } = useLang();
  const say = (en: string, ko: string) => lang === "ko" ? ko : en;
  const [loadedSheet, setSheet] = useState<SheetInspection | null>(null);
  const sheet = loadedSheet?.cell === cell ? loadedSheet : null;
  const [error, setError] = useState<string | null>(null);
  const [selection, setSelection] = useState<SheetSelection>(null);
  const [query, setQuery] = useState("");
  const [list, setList] = useState<"instance" | "net" | "checks">("instance");
  const [full, setFull] = useState(false);
  const [sourceOpen, setSourceOpen] = useState(false);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setSheet(null); setError(null); setSelection(null); setQuery(""); setSourceOpen(false);
    fetch(`${BACKEND}/analog/inspect?cell=${encodeURIComponent(cell)}`, { signal: controller.signal }).then(async r => {
      const data = await r.json(); if (!r.ok || data.error) throw new Error(data.error ?? `Inspection failed (${r.status})`);
      if (controller.signal.aborted) return;
      setSheet(data);
      const first = (data as SheetInspection).components.find(c => !LABEL_TYPES.has(c.type) && c.pins.length);
      if (first) setSelection({ kind: "instance", name: first.name });
    }).catch(e => { if (!controller.signal.aborted) setError(String(e.message ?? e)); });
    return () => controller.abort();
  }, [cell, refresh]);
  const devices = sheet?.components.filter(c => !LABEL_TYPES.has(c.type)) ?? [];
  const active = selection?.kind === "instance" ? sheet?.components.find(c => c.name === selection.name) : null;
  const activeNet = selection?.kind === "net" ? sheet?.nets.find(n => n.name === selection.name) : null;
  const needle = query.toLowerCase().trim();
  const matches = list === "instance"
    ? devices.filter(c => `${c.name} ${c.symbol} ${c.type}`.toLowerCase().includes(needle))
    : (sheet?.nets ?? []).filter(n => `${n.name} ${n.aliases.join(" ")}`.toLowerCase().includes(needle));
  const src = `${BACKEND}/analog/svg?cell=${encodeURIComponent(cell)}&v=${sheet?.sha256 ?? refresh}`;
  const select = (value: SheetSelection) => { setSelection(value); setSourceOpen(false); };
  const params = active ? { ...active.defaults, ...active.attributes } : {};
  const mainParams = ["model", "W", "L", "nf", "mult", "m", "value", "spiceprefix"].filter(k => k in params);
  const supplyParams = ["VPWR", "VGND", "VPB", "VNB"].filter(k => k in params);
  const sourceLine = active?.line ?? (selection?.kind === "net" ? sheet?.components.find(c => c.attributes.lab === selection.name)?.line : null);
  const selectedSymbol = active ? sheet?.symbols.find(s => s.symbol === active.symbol) : null;

  return <section className={`sheet-workspace${full ? " sheet-workspace--full" : ""}`} aria-label={say("Circuit review workspace", "회로 검토 작업공간")}>
    <div className="sheet-workspace__heading">
      <div className="sheet-workspace__title"><small>{say("CIRCUIT REVIEW", "회로 검토")}</small><strong>{cell.split("/").slice(1).join(" / ")}</strong></div>
      <div className="sheet-workspace__actions">
        {!!parents.length && <button type="button" onClick={onBack}>← {say("Up one level", "상위 회로")}</button>}
        <button type="button" onClick={() => setRefresh(v => v + 1)}>{say("Reload sheet", "회로 새로고침")}</button>
        <button type="button" onClick={() => setFull(v => !v)}>{full ? say("Close full screen", "전체 화면 닫기") : say("Full screen", "전체 화면")}</button>
      </div>
    </div>
    <div className="sheet-workspace__summary">
      {sheet ? <><span><b>{sheet.counts.devices}</b> {say("devices", "소자")}</span><span><b>{sheet.counts.nets}</b> {say("nets", "넷")}</span><span><b>{sheet.counts.ports}</b> {say("ports", "포트")}</span><span className={sheet.issues.length ? "has-issues" : ""}>{sheet.issues.length} {say("review notes", "검토 항목")}</span><code title={sheet.sha256}>SHA {sheet.sha256.slice(0, 10)}</code></> : <span>{error ?? say("Reading source and pin interfaces…", "원본과 핀 정보를 읽는 중…")}</span>}
    </div>
    {!!parents.length && <p className="sheet-workspace__breadcrumb">{[...parents, cell].map(p => p.split("/").slice(1).join("/")).join(" → ")}</p>}
    <div className="sheet-workspace__body">
      <div className="sheet-workspace__canvas"><SchematicViewer src={src} alt={`schematic of ${cell}`} inspection={sheet} selection={selection} onSelect={select} onClose={full ? () => setFull(false) : undefined} /></div>
      <aside className="sheet-workspace__inspector" aria-label={say("Circuit inspector", "회로 검사 패널")}>
        {error && <p role="alert">{error}</p>}
        <div className="sheet-workspace__tabs" role="group" aria-label={say("Inspection lists", "검사 목록")}>
          <button type="button" aria-pressed={list === "instance"} onClick={() => setList("instance")}>{say("Devices", "소자")}</button>
          <button type="button" aria-pressed={list === "net"} onClick={() => setList("net")}>{say("Nets", "넷")}</button>
          <button type="button" aria-pressed={list === "checks"} onClick={() => setList("checks")}>{say("Review", "검토")}{sheet && sheet.issues.length > 0 ? ` (${sheet.issues.length})` : ""}</button>
        </div>
        {list === "checks" ? <div className="sheet-workspace__notes">
          <p>{say("Source geometry review. Native ERC, LVS and simulation are separate checks.", "원본 좌표를 기준으로 한 검토입니다. 실제 ERC·LVS·시뮬레이션 판정은 별도입니다.")}</p>
          {sheet?.issues.length === 0 && <p>{say("No supported geometry checks flagged an issue.", "지원하는 좌표 검사에서 검토 항목이 발견되지 않았습니다.")}</p>}
          {sheet?.issues.map((note, i) => <button type="button" key={i} onClick={() => { if (note.instance) select({ kind: "instance", name: note.instance }); setSourceOpen(true); }}><b>{note.kind}</b>{note.message}</button>)}
        </div> : <>
          <input type="search" aria-label={say("Find device or net", "소자 또는 넷 검색")} placeholder={list === "instance" ? "M1, x1, nfet…" : "VDD, GND, clk…"} value={query} onChange={e => setQuery(e.target.value)} />
          <div className="sheet-workspace__list" aria-label={say("Search results", "검색 결과")}>
            {matches.slice(0, 100).map(item => <button type="button" key={item.name} aria-pressed={selection?.kind === list && selection.name === item.name}
              onClick={() => select({ kind: list as "instance" | "net", name: item.name })}>
              <b>{item.name}</b><small>{"symbol" in item ? item.symbol.replace("sky130_fd_pr/", "").replace(".sym", "") : `${item.connections.filter(c => !LABEL_TYPES.has(c.type)).length} ${say("terminals", "단자")}`}</small>
            </button>)}
            {!matches.length && <p>{say("No matches", "검색 결과 없음")}</p>}
          </div>
          {matches.length > 100 && <p>{say("Showing first 100; narrow the search.", "100개까지 표시합니다. 검색어를 좁혀 주세요.")}</p>}
        </>}
        <div className="sheet-workspace__properties">
          {active && <>
            <div className="sheet-workspace__property-title"><strong>{active.name}</strong><small>{active.type || say("annotation", "주석")}</small></div>
            <code className="sheet-workspace__symbol">{active.symbol}</code>
            {active.child && <button type="button" className="sheet-workspace__descend" onClick={() => onOpen(active.child!)}>↓ {say("Open underlying circuit", "하위 회로 열기")}</button>}
            {!active.child && active.master && <button type="button" className="sheet-workspace__descend" disabled={opening} onClick={() => onStandardCell(active.master!)}>↓ {opening ? say("Opening…", "여는 중…") : say("Open cell transistors", "셀 트랜지스터 열기")}</button>}
            <dl>{mainParams.map(key => <div key={key}><dt>{key}</dt><dd title={params[key]}>{params[key]}{!(key in active.attributes) && <small> ({say("symbol default", "심볼 기본값")})</small>}</dd></div>)}</dl>
            {!!supplyParams.length && <><h4>{say("Implicit supply / well bindings", "숨겨진 전원 / 웰 바인딩")}</h4><dl>{supplyParams.map(key => <div key={key}><dt>{key}</dt><dd>{params[key]}{!(key in active.attributes) && <small> ({say("symbol default", "심볼 기본값")})</small>}</dd></div>)}</dl></>}
            <h4>{say("Pin → net", "핀 → 넷")}</h4>
            <table aria-label={say("Pin connections", "핀 연결")}><thead><tr><th>{say("Pin", "핀")}</th><th>{say("Dir", "방향")}</th><th>{say("Net", "넷")}</th></tr></thead><tbody>
              {active.pins.map((p, i) => <tr key={`${p.name}-${i}`}><th scope="row">{p.name}</th><td>{p.direction || "?"}</td><td><button type="button" onClick={() => { select({ kind: "net", name: p.net }); setList("net"); setQuery(""); }}>{p.net}</button></td></tr>)}
            </tbody></table>
            {!active.pins.length && <p>{say("No resolved pin interface on this element.", "확인된 핀 정보가 없는 요소입니다.")}</p>}
            <details><summary>{say("All source attributes", "모든 원본 속성")}</summary><pre>{Object.entries(active.attributes).map(([k, v]) => `${k}=${v}`).join("\n")}</pre></details>
            {selectedSymbol && <details><summary>{say("Pin interface source", "핀 정보 출처")}</summary><code>{selectedSymbol.source}</code><small>SHA256 {selectedSymbol.sha256}</small></details>}
          </>}
          {activeNet && <>
            <div className="sheet-workspace__property-title"><strong>{activeNet.name}</strong><small>{activeNet.named ? say("labelled net", "라벨 넷") : say("unnamed geometry net", "이름 없는 좌표 넷")}</small></div>
            {activeNet.aliases.length > 1 && <p className="has-issues">{say("Connected aliases", "연결된 라벨")}: {activeNet.aliases.join(", ")}</p>}
            <h4>{say("Connected terminals", "연결 단자")}</h4>
            <div className="sheet-workspace__terminals">{activeNet.connections.filter(c => !LABEL_TYPES.has(c.type)).map((c, i) => <button type="button" key={i} onClick={() => { select({ kind: "instance", name: c.instance }); setList("instance"); setQuery(""); }}><b>{c.instance}.{c.pin}</b><small>{c.direction}</small></button>)}</div>
            <p>{say("Matching net labels and connected device names are highlighted in the source drawing.", "원본 회로도에서 해당 넷 라벨과 연결 소자 이름을 강조합니다.")}</p>
          </>}
          {!selection && <p>{say("Select a device or net name in the drawing or list.", "회로도나 목록에서 소자·넷 이름을 선택하세요.")}</p>}
        </div>
      </aside>
    </div>
    {sheet && <footer className="sheet-workspace__footer">
      <code title={sheet.source}>{sheet.source}</code>
      <div><button type="button" onClick={() => setSourceOpen(v => !v)}>{say("View source", "원본 보기")}</button>
        <button type="button" onClick={() => saveFile(sheet.source_text, `${cell.split("/").at(-1)}.sch`, "text/plain")}>↓ .sch</button>
        <a href={src} target="_blank" rel="noreferrer">↗ SVG</a>
        <button type="button" onClick={() => saveFile(JSON.stringify(sheet, null, 2), `${cell.split("/").at(-1)}-review.json`, "application/json")}>↓ {say("Review JSON", "검토 JSON")}</button></div>
      <p>{say("Pin mapping follows sheet geometry and symbol interfaces; buses are not expanded. Parameters stay symbolic until a testbench binds them.", "핀 연결은 원본 좌표와 심볼 정보를 따르며 버스는 비트로 확장하지 않습니다. 파라미터는 테스트벤치에서 지정되기 전까지 기호 값으로 유지합니다.")}</p>
    </footer>}
    {sheet && sourceOpen && <pre className="sheet-workspace__source" aria-label={say("Schematic source", "회로도 원본")}>{sheet.source_text.split("\n").map((line, i) => <code key={i} data-selected={sourceLine === i + 1 || undefined}><span>{i + 1}</span>{line}{"\n"}</code>)}</pre>}
  </section>;
}
