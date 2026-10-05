import { useEffect, useState } from "react";
import { fetchArtifacts, type ArtifactInventory, type ArtifactText } from "../api/referenceDb";
import { useLang } from "../i18n";
import type { InspectionRow } from "./runInspection";

export default function EvidenceBrowser({ row, metric = "" }: { row: InspectionRow; metric?: string }) {
  const { lang } = useLang();
  const say = (en: string, ko: string) => lang === "ko" ? ko : en;
  const [inventory, setInventory] = useState<ArtifactInventory | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [step, setStep] = useState("final");
  const [file, setFile] = useState("final/metrics.json");
  const [text, setText] = useState<ArtifactText | null>(null);
  const [textError, setTextError] = useState<string | null>(null);
  const [query, setQuery] = useState(metric);
  const [matchesOnly, setMatchesOnly] = useState(false);
  const [allSteps, setAllSteps] = useState(false);
  useEffect(() => { setQuery(metric); if (metric) { setStep("final"); setFile("final/metrics.json"); } }, [metric]);
  useEffect(() => {
    let cancelled = false;
    if (!row.pipelineCase.file) return;
    fetchArtifacts(row.pipelineCase.file, row.candidate.tag).then(data => {
      if (cancelled) return;
      const inv = data as ArtifactInventory;
      setInventory(inv); setError(null);
      if (!inv.files.some(f => f.id === "final/metrics.json") && inv.files.length) {
        setStep(inv.files[0].step); setFile(inv.files[0].id);
      }
    }).catch(e => { if (!cancelled) setError(String(e)); });
    return () => { cancelled = true; };
  }, [row.id, row.pipelineCase.file, row.candidate.tag]);
  const available = inventory?.files.filter(f => f.step === step) ?? [];
  const selectedFile = available.find(f => f.id === file)?.id ?? available[0]?.id;
  useEffect(() => {
    let cancelled = false;
    if (!row.pipelineCase.file || !selectedFile) return;
    fetchArtifacts(row.pipelineCase.file, row.candidate.tag, selectedFile).then(data => {
      if (!cancelled) { setText(data as ArtifactText); setTextError(null); }
    }).catch(e => { if (!cancelled) { setText(null); setTextError(String(e)); } });
    return () => { cancelled = true; };
  }, [row.pipelineCase.file, row.candidate.tag, selectedFile]);
  const currentText = text?.id === selectedFile ? text : null;
  const lines = currentText?.content.split("\n") ?? [];
  const visibleLines = lines.map((line, index) => ({ line, number: index + 1, match: Boolean(query && line.toLowerCase().includes(query.toLowerCase())) }))
    .filter(line => !matchesOnly || line.match).slice(0, 1500);
  if (!row.pipelineCase.file) return <p>{say("Case filename was not recorded; source files cannot be located.", "케이스 파일명이 기록되지 않아 원본 파일을 찾을 수 없습니다.")}</p>;
  return <section className="evidence-browser" aria-label={say("Native tool evidence", "도구 원본 근거")}>
    <h4>{say("Flow snapshots & native evidence", "흐름 스냅샷 · 도구 원본 근거")}</h4>
    <p className="run-inspector__note">{say("A step folder or saved state is an execution artifact, not a signoff verdict. Recorded metrics and model qualification remain separate.", "단계 폴더와 저장 상태는 실행 산출물이며 최종 검증 판정이 아닙니다. 기록된 검사 결과와 모델 적합성을 별도로 확인하세요.")}</p>
    {error ? <p role="alert">{error}</p> : !inventory ? <p>{say("Reading available reports…", "확인 가능한 보고서를 읽는 중…")}</p> : !inventory.run_available ?
      <p>{say("Original run files are unavailable. Archived case measurements remain visible above.", "원본 실행 파일이 없어 열 수 없습니다. 저장된 케이스 측정값은 위에서 확인할 수 있습니다.")}</p> : <>
      <div className="evidence-flow" aria-label={say("Recorded flow steps", "기록된 실행 단계")}>
        <button type="button" aria-pressed={step === "run"} onClick={() => { setStep("run"); setQuery(""); }}>{say("Run inputs", "실행 입력")}</button>
        {(allSteps ? inventory.steps : inventory.steps.slice(-12)).map(node => <button type="button" key={node.id} aria-pressed={step === node.id}
          onClick={() => { setStep(node.id); setQuery(""); }} title={`${node.files} files · ${node.snapshot_recorded ? "state_out.json recorded" : "state snapshot absent"}`}>
          <i className={node.snapshot_recorded ? "evidence-flow__snapshot" : "evidence-flow__unknown"} />{node.id}
        </button>)}
      </div>
      <p className="run-inspector__note">{say("● Saved state_out snapshot · ○ Snapshot absent · step order follows recorded directory numbers.", "● state_out 스냅샷 있음 · ○ 스냅샷 없음 · 기록된 디렉터리 번호 순서입니다.")}</p>
      <button type="button" onClick={() => setAllSteps(v => !v)}>{allSteps ? say("Show last steps", "마지막 단계 보기") : `${say("Show all recorded steps", "기록된 모든 단계 보기")} (${inventory.steps.filter(node => node.id !== "final").length}${inventory.steps.some(node => node.id === "final") ? " + final" : ""})`}</button>
      {inventory.limited && <p>{say("File inventory limited to 2,000 text artifacts.", "텍스트 산출물 목록은 2,000개까지 표시합니다.")}</p>}
      <div className="evidence-browser__filters">
        <label>{say("Source file", "원본 파일")}<select aria-label={say("Source file", "원본 파일")} value={selectedFile ?? ""} onChange={e => setFile(e.target.value)}>
          {available.map(f => <option key={f.id} value={f.id}>{f.id} ({(f.bytes / 1024).toFixed(1)} KB)</option>)}
          {!available.length && <option value="">{say("No readable reports in this step", "이 단계에 읽을 수 있는 보고서가 없습니다")}</option>}
        </select></label>
        <label>{say("Find metric or text", "지표 또는 텍스트 검색")}<input type="search" aria-label={say("Find in source report", "원본 보고서 내 검색")} value={query} onChange={e => setQuery(e.target.value)} /></label>
        <label><input type="checkbox" checked={matchesOnly} onChange={e => setMatchesOnly(e.target.checked)} />{say("Matching lines only", "일치하는 줄만")}</label>
      </div>
      {textError && <p role="alert">{textError}</p>}
      {selectedFile && !currentText && !textError && <p>{say("Reading source…", "원본 읽는 중…")}</p>}
      {currentText && <>
        <p className="run-inspector__note"><code>{row.pipelineCase.file} :: {row.candidate.tag} :: {currentText.id}</code>
          {" · "}{say("Source modified", "원본 수정 시각")} {currentText.modified_at}
          {currentText.truncated && ` · ${say("First 200 KB; source is larger", "첫 200 KB 표시 · 원본은 더 큽니다")}`}</p>
        <pre className="evidence-browser__text" aria-label={say("Source report content", "원본 보고서 내용")}>
          {visibleLines.map(line => <code key={line.number} data-match={line.match || undefined}><span>{line.number}</span>{line.line || " "}</code>)}
          {!visibleLines.length && say("No matching source lines", "일치하는 원본 줄 없음")}
        </pre>
        {lines.length > 1500 && <p>{say("At most 1,500 lines shown. Search filters the returned source excerpt.", "최대 1,500줄을 표시합니다. 검색은 반환된 원본 일부에 적용됩니다.")}</p>}
      </>}
    </>}
  </section>;
}
