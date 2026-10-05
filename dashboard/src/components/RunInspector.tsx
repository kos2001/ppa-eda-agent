import { useEffect, useMemo, useState } from "react";
import { fetchCandidateDetail, type LayoutSummary, type PipelineCase } from "../api/referenceDb";
import { useLang } from "../i18n";
import { candidateState } from "./overviewData";
import { comparisonEvidence, finiteMetric, inspectionRows, checkState, type InspectionRow } from "./runInspection";
import EvidenceBrowser from "./EvidenceBrowser";
import LayoutView from "./LayoutView";
import "./RunInspector.css";

function RecordedLayout({ row }: { row: InspectionRow }) {
  const { lang } = useLang();
  const [layout, setLayout] = useState<LayoutSummary | null>(row.candidate.layout ?? null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(Boolean(row.candidate.layout_deferred && row.pipelineCase.file && !row.candidate.layout));
  useEffect(() => {
    let cancelled = false;
    if (row.candidate.layout || !row.candidate.layout_deferred || !row.pipelineCase.file) return;
    // This is the only tab which requests the large, recorded DEF geometry.
    fetchCandidateDetail(row.pipelineCase.file, row.candidate.tag).then(data => {
      if (!cancelled) { setLayout(data.layout); setLoading(false); }
    }).catch(e => { if (!cancelled) { setError(String(e)); setLoading(false); } });
    return () => { cancelled = true; };
  }, [row.pipelineCase.file, row.candidate.tag, row.candidate.layout, row.candidate.layout_deferred]);
  return <div>{error ? <p role="alert">{error}</p> : layout ? <LayoutView layout={layout} /> : loading ?
    <p>{lang === "ko" ? "저장된 DEF 도형을 불러오는 중…" : "Loading recorded DEF geometry…"}</p> :
    <p>{lang === "ko" ? "이 후보의 저장된 DEF 도형이 없습니다." : "No stored DEF geometry for this candidate."}</p>}</div>;
}

const show = (value: unknown, digits = 2) => finiteMetric(value)?.toLocaleString(undefined, { maximumFractionDigits: digits }) ?? "—";

export default function RunInspector({ cases, onOpenCase }: { cases: PipelineCase[]; onOpenCase: (design: string, file: string) => void }) {
  const { lang } = useLang();
  const say = (en: string, ko: string) => lang === "ko" ? ko : en;
  const designs = [...new Set(cases.map(c => c.design))].sort();
  const [chosenDesign, setDesign] = useState("aes");
  const design = designs.includes(chosenDesign) ? chosenDesign : designs[0];
  const rows = useMemo(() => inspectionRows(cases.filter(c => c.design === design)), [cases, design]);
  const technologies = [...new Set(rows.map(row => row.technology))];
  const [chosenTechnology, setTechnology] = useState<string | null>(null);
  const technology = chosenTechnology && technologies.includes(chosenTechnology) ? chosenTechnology : technologies[0];
  const filtered = rows.filter(row => row.technology === technology);
  const [chosenIds, setIds] = useState<string[] | null>(null);
  const ids = chosenIds ?? filtered.slice(0, 4).map(row => row.id);
  const selected = filtered.filter(row => ids.includes(row.id));
  const [activeId, setActive] = useState<string | null>(null);
  const active = filtered.find(row => row.id === activeId) ?? selected[0] ?? filtered[0];
  const [tab, setTab] = useState<"evidence" | "layout" | "settings">("evidence");
  const [metric, setMetric] = useState("");
  const evidence = comparisonEvidence(selected);
  const checkKeys = [...new Set(selected.flatMap(row => row.candidate.verdict?.signoff_checks?.map(check => check.key) ?? []))];
  const plotted = selected.flatMap(row => {
    const area = finiteMetric(row.candidate.verdict?.area_um2), power = finiteMetric(row.candidate.verdict?.power?.total_w);
    return area === null || power === null ? [] : [{ row, area, power: power * 1e6 }];
  });
  const position = (value: number, values: number[], start: number, span: number) => {
    const lo = Math.min(...values), hi = Math.max(...values);
    return start + (hi === lo ? 0.5 : (value - lo) / (hi - lo)) * span;
  };
  function exportSelection() {
    const blob = new Blob([JSON.stringify({ source: "recorded reference-db cases; raw observations, no ranking", comparison_evidence: evidence,
      rows: selected.map(row => ({ file: row.pipelineCase.file, tag: row.candidate.tag, at: row.at, technology: row.technology,
        verdict: row.candidate.verdict, overrides: row.candidate.overrides, seconds: row.candidate.seconds })) }, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob), link = document.createElement("a");
    link.href = url; link.download = `${design}-recorded-comparison.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  if (!rows.length) return <section className="run-inspector"><h3>{say("Run comparison & evidence", "실행 비교 · 검증 근거")}</h3><p>{say("No measured cases available to inspect.", "확인할 측정 케이스가 없습니다.")}</p></section>;
  return <section className="run-inspector" aria-label={say("Run comparison and evidence", "실행 비교와 검증 근거")}>
    <div className="run-inspector__heading"><div><span className="run-inspector__eyebrow">RUN WORKSPACE</span><h3>{say("Compare runs. Inspect the evidence.", "실행을 비교하고 근거를 확인하세요.")}</h3></div>
      <button type="button" disabled={!selected.length} onClick={exportSelection}>{say("Export selected JSON", "선택 기록 JSON 저장")}</button></div>
    <div className="run-inspector__filters">
      <label>{say("Design", "설계")}<select aria-label={say("Comparison design", "비교할 설계")} value={design} onChange={e => { setDesign(e.target.value); setTechnology(null); setIds(null); setActive(null); setMetric(""); }}>
        {designs.map(name => <option key={name}>{name}</option>)}</select></label>
      <label>{say("Recorded technology & tool", "기록된 기술 · 도구")}<select aria-label={say("Comparison technology", "비교할 기술")} value={technology} onChange={e => { setTechnology(e.target.value); setIds(null); setActive(null); setMetric(""); }}>
        {technologies.map(name => <option key={name}>{name}</option>)}</select></label>
    </div>
    <p className={`run-inspector__provenance run-inspector__provenance--${evidence}`}>
      {evidence === "compatible" ? say("Recorded provenance keys match. Check physical, timing and model gates before making a design decision.", "기록된 출처 키가 일치합니다. 설계 판단 전에 물리·타이밍 검사와 모델 적합성을 확인하세요.") : evidence === "different" ?
        say("Sources or constraints differ. Raw observations only; no improvement or Pareto claim.", "소스나 제약이 다릅니다. 원시 관측값만 표시하며 개선이나 파레토 비교를 주장하지 않습니다.") :
        say("Source compatibility unknown. Same recorded technology does not establish identical RTL or constraints. Raw observations only.", "출처 호환성 미확인. 같은 기록된 기술도 RTL·제약의 일치를 보장하지 않습니다. 원시 관측값을 표시합니다.")}
    </p>
    <details className="run-inspector__selection"><summary>{say("Choose candidates", "후보 선택")} · {selected.length} / 6 · {filtered.length} {say("recorded", "기록")}</summary>
      <div>{filtered.map(row => <label key={row.id}><input type="checkbox" checked={ids.includes(row.id)} disabled={!ids.includes(row.id) && ids.length >= 6}
        onChange={e => setIds(e.target.checked ? [...ids, row.id] : ids.filter(id => id !== row.id))} /><span>{row.at.replace("T", " ")} · {row.candidate.tag}</span></label>)}</div>
    </details>
    <div className="run-inspector__comparison">
      <div className="run-inspector__table-scroll"><table><caption>{say("Recorded candidate metrics", "기록된 후보 지표")}</caption><thead><tr>
        <th>{say("Candidate", "후보")}</th><th>{say("State", "상태")}</th><th>Area µm²</th><th>Power µW</th><th>Setup ns</th><th>Time s</th>
      </tr></thead><tbody>{selected.map(row => <tr key={row.id} className={active?.id === row.id ? "run-inspector__active" : ""}>
        <th><button type="button" onClick={() => { setActive(row.id); setMetric(""); }}><span>{row.candidate.tag}</span><small>{row.at.replace("T", " ")}</small></button></th>
        <td><span className={`run-inspector__state run-inspector__state--${candidateState(row.candidate)}`}>{row.candidate.not_evaluated ? "NOT EVALUATED" : candidateState(row.candidate)}</span></td>
        <td>{show(row.candidate.verdict?.area_um2)}</td><td>{show(finiteMetric(row.candidate.verdict?.power?.total_w) === null ? null : row.candidate.verdict!.power!.total_w! * 1e6)}</td>
        <td>{show(row.candidate.verdict?.worst_setup_slack, 3)}</td><td>{show(row.candidate.seconds, 1)}</td>
      </tr>)}</tbody></table>{!selected.length && <p>{say("Select candidates to compare their recorded metrics.", "기록된 지표를 볼 후보를 선택하세요.")}</p>}</div>
      <div className="run-inspector__scatter"><svg viewBox="0 0 440 260" role="img" aria-label={say("Recorded area and vectorless power observations", "기록된 면적과 vectorless 전력 관측값")}>
        <line x1="65" y1="215" x2="415" y2="215" /><line x1="65" y1="25" x2="65" y2="215" />
        <text x="220" y="251" textAnchor="middle">Area µm²</text><text x="16" y="120" textAnchor="middle" transform="rotate(-90 16 120)">Power µW</text>
        {plotted.length > 0 && <><text x="65" y="233">{show(Math.min(...plotted.map(p => p.area)))}</text><text x="415" y="233" textAnchor="end">{show(Math.max(...plotted.map(p => p.area)))}</text>
          <text x="58" y="215" textAnchor="end">{show(Math.min(...plotted.map(p => p.power)))}</text><text x="58" y="32" textAnchor="end">{show(Math.max(...plotted.map(p => p.power)))}</text></>}
        {plotted.map(({ row, area, power }, index) => <g key={row.id} role="button" tabIndex={0} aria-label={`${say("Inspect", "확인")} ${row.candidate.tag}`} onClick={() => setActive(row.id)}
          onKeyDown={e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setActive(row.id); } }}>
          <circle cx={position(area, plotted.map(p => p.area), 75, 330)} cy={position(power, plotted.map(p => p.power), 205, -170)} r={active?.id === row.id ? 9 : 6}
            className={`run-inspector__point--${candidateState(row.candidate)}`} /><title>{row.candidate.tag} · {area} µm² · {power} µW</title>
          <text x={position(area, plotted.map(p => p.area), 75, 330) + 11} y={position(power, plotted.map(p => p.power), 205, -170) + 4}>{index + 1}</text>
        </g>)}
        {!plotted.length && <text x="235" y="120" textAnchor="middle">{say("No complete area/power pairs", "면적·전력 측정 쌍 없음")}</text>}
      </svg><p>{say("Vectorless power only · absent values omitted · click a point to inspect its source. No ranking across unknown sources.", "Vectorless 전력만 · 누락값 제외 · 점을 눌러 근거 확인. 출처 미확인 기록의 순위를 정하지 않습니다.")}</p></div>
    </div>
    <div className="run-inspector__checks"><h4>{say("Verification evidence matrix", "검증 근거 매트릭스")}</h4><p className="run-inspector__note">{say("0 = recorded clean count · positive = recorded violation · — = unknown. Click a check to locate its metric in the native report.", "0 = 기록된 위반 없음 · 양수 = 기록된 위반 · — = 미확인. 검사를 누르면 원본 보고서에서 해당 지표를 찾습니다.")}</p>
      <div className="run-inspector__table-scroll"><table><thead><tr><th>{say("Check", "검사")}</th>{selected.map(row => <th key={row.id} title={row.candidate.tag}>{row.candidate.tag}</th>)}</tr></thead><tbody>
        {checkKeys.map(key => <tr key={key}><th>{selected.flatMap(row => row.candidate.verdict?.signoff_checks ?? []).find(c => c.key === key)?.label ?? key}</th>
          {selected.map(row => { const count = row.candidate.verdict?.signoff_checks?.find(c => c.key === key)?.count; return <td key={row.id}><button type="button" className={`run-inspector__check--${checkState(count)}`}
            title={`${row.candidate.tag} · ${key}: ${count ?? "unknown"}`} onClick={() => { setActive(row.id); setMetric(key); setTab("evidence"); }}>{checkState(count) === "unknown" ? "—" : show(count, 0)}</button></td>; })}</tr>)}
      </tbody></table>{!checkKeys.length && <p>{say("No per-check evidence recorded for this selection.", "선택한 후보의 검사별 근거가 기록되지 않았습니다.")}</p>}</div>
    </div>
    {active && <div className="run-inspector__detail"><div className="run-inspector__detail-heading"><div><h4>{active.candidate.tag}</h4><small>{active.pipelineCase.file ?? active.at}</small></div>
      <button type="button" disabled={!active.pipelineCase.file} onClick={() => onOpenCase(active.pipelineCase.design, active.pipelineCase.file!)}>{say("Open this case →", "이 케이스 열기 →")}</button></div>
      {active.candidate.verdict?.model_validity?.macro_arc_audit?.model_qualified === false && <p className="run-inspector__provenance">{say("Model qualification open. Clean physical-check counts do not qualify this macro model.", "모델 적합성 미확정. 물리 검증 위반 수가 0이어도 매크로 모델이 검증된 것은 아닙니다.")}</p>}
      <div className="run-inspector__tabs" role="tablist" aria-label={say("Candidate inspection", "후보 상세 확인")}>{(["evidence", "layout", "settings"] as const).map(name => <button type="button" key={name} role="tab" aria-selected={tab === name} onClick={() => setTab(name)}>
        {name === "evidence" ? say("Flow & source reports", "흐름 · 원본 보고서") : name === "layout" ? say("Interactive layout", "인터랙티브 레이아웃") : say("Recorded settings", "기록된 설정")}</button>)}</div>
      {tab === "evidence" && <EvidenceBrowser key={active.id} row={active} metric={metric} />}
      {tab === "layout" && <RecordedLayout key={active.id} row={active} />}
      {tab === "settings" && <pre className="evidence-browser__text">{JSON.stringify({ file: active.pipelineCase.file, tag: active.candidate.tag,
        overrides: active.candidate.overrides, constraints: active.pipelineCase.constraints ?? "not recorded", toolchain: active.pipelineCase.toolchain ?? "not recorded",
        evaluation_provenance: active.candidate.evaluation_provenance ?? "not recorded", model_validity: active.candidate.verdict?.model_validity ?? "not recorded" }, null, 2)}</pre>}
    </div>}
  </section>;
}
