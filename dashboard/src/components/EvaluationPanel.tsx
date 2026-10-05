import { useEffect, useState } from "react";
import { API_BASE_URL } from "../api/config";
import { useLang } from "../i18n";

interface Report {
  candidate_runs: number;
  timed_runs: number;
  timing_missing_runs: number;
  excluded: Record<string, number>;
  archives: { compatibility_key: string; eligible_candidates: number; axes: string[]; frontier: { id: string }[] }[];
  stage_costs: Record<string, { samples: number; median_seconds: number }>;
}

export default function EvaluationPanel({ refresh }: { refresh: number }) {
  const { lang } = useLang();
  const say = (en: string, ko: string) => lang === "ko" ? ko : en;
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    fetch(`${API_BASE_URL}/evaluation-report`, { signal: AbortSignal.timeout(30_000) })
      .then(async response => { if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.json() as Promise<Report>; })
      .then(data => { if (!cancelled) { setReport(data); setError(null); } })
      .catch(error => { if (!cancelled) setError(String(error)); });
    return () => { cancelled = true; };
  }, [refresh]);
  return <section className="overview__evidence" aria-label={say("Evaluation cost and Pareto memory", "평가 비용과 파레토 메모리")}>
    <div className="overview__section-heading"><h3>{say("Evaluation cost & Pareto memory", "평가 비용 · 파레토 메모리")}</h3></div>
    {error ? <p role="alert">{say("Evaluation report unavailable", "평가 보고서 확인 불가")} · {error}</p> : !report ? <p>{say("Reading measured history…", "측정 기록 확인 중…")}</p> : <>
      <ul className="overview__legend">
        <li>{say("Timed runs", "시간 기록")} <b>{report.timed_runs} / {report.candidate_runs}</b></li>
        <li>{say("Timing unknown", "시간 미확인")} <b>{report.timing_missing_runs}</b></li>
        <li>{say("Source-compatible archives", "출처 확인된 비교 그룹")} <b>{report.archives.length}</b></li>
        <li>{say("Passing records with unknown sources", "출처 미확인 통과 기록")} <b>{report.excluded.provenance_unknown ?? 0}</b></li>
      </ul>
      <p>{say("Only compatible RTL, effective SDC, PDK/library and tool versions enter the persistent frontier. Earlier stage results remain separate from signoff.", "RTL, 실제 SDC, PDK·라이브러리와 도구 버전이 호환되는 기록만 파레토 아카이브에 포함합니다. 초기 단계 결과는 최종 검증과 구분합니다.")}</p>
      <details><summary>{say("Inspect stage timings and archive groups", "단계별 시간과 비교 그룹 확인")}</summary>
        {Object.keys(report.stage_costs).length === 0 ? <p>{say("No stage-level timing evidence yet. New evaluations record it; old values are not inferred.", "단계별 시간 기록이 아직 없습니다. 새 평가부터 기록하며 과거 값은 추정하지 않습니다.")}</p> : <ul>{Object.entries(report.stage_costs).map(([name, cost]) => <li key={name}>{name} · {cost.median_seconds.toFixed(2)} s · {cost.samples} {say("samples", "측정")}</li>)}</ul>}
        {report.archives.length > 0 && <ul>{report.archives.map(group => <li key={group.compatibility_key}><code>{group.compatibility_key.slice(0, 12)}</code> · {group.frontier.length} / {group.eligible_candidates} · {group.axes.join(", ")}</li>)}</ul>}
      </details>
    </>}
  </section>;
}
