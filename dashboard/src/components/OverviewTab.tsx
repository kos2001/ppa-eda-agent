import { useEffect, useMemo, useState } from "react";
import { API_BASE_URL } from "../api/config";
import { fetchReferenceDb, layoutImageUrl, type ReferenceDb } from "../api/referenceDb";
import { useLang } from "../i18n";
import { groupByDesign, recordedAt } from "./caseGrouping";
import { candidateSummary, CANDIDATE_STATES } from "./overviewData";
import "./OverviewTab.css";
import EvaluationPanel from "./EvaluationPanel";
import RunInspector from "./RunInspector";

interface Example {
  design: string;
  top: string | null;
  clock_period_ns: number | null;
  objective: string | null;
  expected_outcome: string | null;
  runnable: boolean;
  config_path: string;
}

const DESCRIPTIONS: Record<string, [string, string]> = {
  counter4: ["4-bit counter · baseline flow", "4비트 카운터 · 기본 흐름"],
  counter4_tinydie: ["Small-die floorplan repair example", "작은 다이의 플로어플랜 복구 예제"],
  gcd: ["Greatest common divisor · clock sweep", "최대공약수 연산 · 클록 주기 탐색"],
  spm: ["Unsigned serial/parallel multiplier", "부호 없는 직렬·병렬 곱셈기"],
  cdc_twoclock: ["Two clock domains · constraint coverage", "두 클록 도메인 · 제약 적용 범위"],
  riscv32i: ["RISC-V processor · larger RTL design", "RISC-V 프로세서 · 대규모 RTL 설계"],
  aes: ["AES cipher · coupled physical repair", "AES 암호화 · 복합 물리 위반 복구"],
  sram_wrapper: ["SRAM macro · timing-model coverage", "SRAM 매크로 · 타이밍 모델 적용 범위"],
  sram_wrapper_autoplace: ["SRAM macro · automatic placement", "SRAM 매크로 · 자동 배치"],
};
const REFERENCES = [
  { name: "SiliconCompiler", url: "https://docs.siliconcompiler.com/en/v0.38.3/user_guide/tutorials/dashboard_tutorial.html",
    detail: ["Applied: selected-run metrics, area/power observations and flow snapshots", "적용: 선택 실행 지표, 면적·전력 관측값, 흐름 스냅샷"] },
  { name: "OpenROAD Web Viewer", url: "https://openroad.readthedocs.io/en/latest/main/src/web/README.html",
    detail: ["Applied: zoom/pan, layer visibility, geometry search and cell footprint map", "적용: 확대·이동, 레이어 선택, 도형 검색, 셀 면적 분포"] },
  { name: "LanEx", url: "https://github.com/AkshatIsWired/lanex",
    detail: ["Applied: per-check source reports, highlighted metrics and unknown states", "적용: 검사별 원본 보고서, 지표 강조, 미확인 상태 표시"] },
];

async function apiJson<T>(endpoint: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${endpoint}`, { signal: AbortSignal.timeout(10_000) });
  if (!response.ok) throw new Error(`${endpoint}: HTTP ${response.status}`);
  return response.json();
}

export default function OverviewTab({ onOpenDesign, onOpenCase, onOpenSchematic }: {
  onOpenDesign: (design: string) => void;
  onOpenCase: (design: string, file: string) => void;
  onOpenSchematic: () => void;
}) {
  const { lang } = useLang();
  const say = (en: string, ko: string) => lang === "ko" ? ko : en;
  const [db, setDb] = useState<ReferenceDb | null>(null);
  const [examples, setExamples] = useState<Example[] | null>(null);
  const [connected, setConnected] = useState<boolean | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [updated, setUpdated] = useState<Date | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");

  useEffect(() => {
    let cancelled = false;
    let running = false;
    async function load() {
      if (running || document.visibilityState === "hidden") return;
      running = true;
      const results = await Promise.allSettled([
        fetchReferenceDb(), apiJson<{ examples: Example[] }>("/examples"),
        apiJson<{ status: string; service: string }>("/health"),
      ]);
      running = false;
      if (cancelled) return;
      const [store, catalog, health] = results;
      if (store.status === "fulfilled") setDb(store.value);
      if (catalog.status === "fulfilled") setExamples(catalog.value.examples);
      setConnected(health.status === "fulfilled" && health.value.status === "ok" && health.value.service === "ppa-eda-api");
      setErrors(results.flatMap(result => result.status === "rejected" ? [String(result.reason)] : []));
      if (store.status === "fulfilled") setUpdated(new Date());
    }
    void load();
    const timer = window.setInterval(() => void load(), 30_000);
    const visible = () => void load();
    document.addEventListener("visibilitychange", visible);
    return () => { cancelled = true; window.clearInterval(timer); document.removeEventListener("visibilitychange", visible); };
  }, [refresh]);

  const groups = useMemo(() => groupByDesign(Object.values(db?.designs ?? {}).flat()), [db]);
  const counts = useMemo(() => candidateSummary(Object.values(db?.designs ?? {}).flat()), [db]);
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const labels = {
    clean: say("Verification passed", "후보 검증 통과"),
    violations: say("Measured violations", "측정된 위반"),
    unknown: say("Checks incomplete", "검증 미완료"),
    error: say("Flow failed to run", "도구 실행 실패"),
  };
  const catalog = useMemo(() => {
    const rows = new Map((examples ?? []).map(e => [e.design, e]));
    for (const group of groups) if (!rows.has(group.design)) rows.set(group.design, {
      design: group.design, top: null, clock_period_ns: null, objective: null,
      expected_outcome: null, runnable: false, config_path: "",
    });
    return [...rows.values()].sort((a, b) => a.design.localeCompare(b.design));
  }, [examples, groups]);
  const shown = catalog.filter(example => {
    const group = groups.find(g => g.design === example.design);
    return (filter === "all" || (filter === "recorded" ? Boolean(group) : !group)) &&
      `${example.design} ${example.top ?? ""} ${DESCRIPTIONS[example.design]?.join(" ") ?? ""}`.toLowerCase().includes(query.toLowerCase());
  });

  return <div className="overview">
    <header className="overview__heading">
      <div><span className="overview__eyebrow">PPA / DESIGN EXPLORER</span>
        <h2>{say("Designs, evidence & examples", "설계, 실행 근거와 예제")}</h2>
        <p>{say("Explore measured runs and open a design to inspect its layout and signoff reports.", "측정된 실행을 살펴보고, 설계를 열어 레이아웃과 검증 보고서를 확인하세요.")}</p></div>
      <button onClick={() => setRefresh(v => v + 1)}>{say("Refresh", "새로 고침")}</button>
    </header>
    {errors.length > 0 && <div className="overview__connection-error" role="alert">
      {say("Some API requests failed. Retained records may be stale.", "일부 API 요청이 실패했습니다. 표시된 기존 기록은 최신 상태가 아닐 수 있습니다.")}
      <small>{errors.join(" · ")}</small>
    </div>}
    <section className="overview__services" aria-label={say("Service architecture", "서비스 구성")}>
      <div><span>01 / FRONTEND</span><strong>React · Vite</strong><small>{window.location.origin}</small></div>
      <span className="overview__arrow" aria-hidden="true">→</span>
      <div><span>02 / BACKEND API</span><strong>{connected === null ? say("Connecting…", "연결 중…") : connected ? say("API connected", "API 연결됨") : say("API unavailable", "API 연결 실패")}</strong><small>{API_BASE_URL || window.location.origin}</small></div>
      <span className="overview__arrow" aria-hidden="true">→</span>
      <div><span>03 / EXECUTION & STORE</span><strong>Python · OpenLane · SPICE</strong><small>reference-db / {say("Measured history", "측정 기록")}</small></div>
    </section>
    <section className="overview__metrics" aria-label={say("Measured history summary", "측정 기록 요약")}>
      {[[say("Available designs", "등록된 설계"), examples ? catalog.length : "—"],
        [say("Recorded cases", "기록된 케이스"), db ? groups.reduce((n, g) => n + g.cases.length, 0) : "—"],
        [say("Candidate runs", "후보 실행"), db ? total : "—"],
        [say("Verification passed", "후보 검증 통과"), db ? counts.clean : "—"]].map(([label, value]) =>
        <div key={label}><strong>{typeof value === "number" ? value.toLocaleString() : value}</strong><span>{label}</span></div>)}
    </section>
    <section className="overview__evidence" aria-label={say("Candidate verification distribution", "후보 검증 분포")}>
      <div className="overview__section-heading"><h3>{say("Candidate verification", "후보 검증 현황")}</h3>
        <small>{updated ? `${say("Records refreshed", "기록 확인")} ${updated.toLocaleTimeString(lang === "ko" ? "ko-KR" : "en-US")}` : say("Loading measured records…", "측정 기록을 불러오는 중…")}</small></div>
      <div className="overview__bar" aria-hidden="true">{total > 0 && CANDIDATE_STATES.map(state => <span key={state} className={`overview__segment--${state}`} style={{ width: `${counts[state] / total * 100}%` }} />)}</div>
      <ul className="overview__legend">{CANDIDATE_STATES.map(state => <li key={state}><i className={`overview__segment--${state}`} /><span>{labels[state]}</span><b>{db ? counts[state] : "—"}</b></li>)}</ul>
      <p>{say("Physical checks do not establish SRAM model qualification or functional equivalence. Open the case for its constraints and model audit.", "물리 검증 결과만으로 SRAM 모델 적합성이나 기능 등가성이 확정되지 않습니다. 케이스에서 제약과 모델 감사를 확인하세요.")}</p>
    </section>
    <EvaluationPanel refresh={refresh} />
    {db && <RunInspector cases={Object.values(db.designs).flat()} onOpenCase={onOpenCase} />}
    <section aria-label={say("Circuit examples", "회로 예제")}>
      <div className="overview__section-heading"><h3>{say("Circuit examples", "회로 예제")}</h3><span>{shown.length} / {catalog.length}</span></div>
      <div className="overview__filters">
        <input type="search" aria-label={say("Search designs", "설계 검색")} placeholder={say("Search AES, SRAM, RISC-V…", "AES, SRAM, RISC-V 검색…")} value={query} onChange={e => setQuery(e.target.value)} />
        <select aria-label={say("Filter examples", "예제 필터")} value={filter} onChange={e => setFilter(e.target.value)}>
          <option value="all">{say("All examples", "모든 예제")}</option><option value="recorded">{say("With measured records", "측정 기록 있음")}</option><option value="unrecorded">{say("Without records", "측정 기록 없음")}</option>
        </select>
      </div>
      <div className="overview__grid">{shown.map(example => {
        const group = groups.find(g => g.design === example.design);
        const latest = group?.cases[0];
        const imageCase = group?.cases.find(c => c.layout_image);
        const summary = candidateSummary(group?.cases ?? []);
        const results = latest?.iterations.flatMap(i => i.results) ?? [];
        const modelOpen = results.some(r => r.verdict?.model_validity?.macro_arc_audit?.model_qualified === false);
        const latestChecks = results.length === 1 ? results[0].verdict?.signoff_checks : null;
        const latestZero = Boolean(latestChecks?.length && latestChecks.every(check => check.count === 0));
        const desc = DESCRIPTIONS[example.design];
        return <article className="overview__card" key={example.design}>
          <div className="overview__preview">{imageCase?.layout_image
            ? <><img src={layoutImageUrl(imageCase.layout_image)} alt={`${example.design} ${say("measured layout", "실측 레이아웃")}`} loading="lazy" /><span>{say("Recorded GDS render", "기록된 GDS 렌더")} · {recordedAt(imageCase).replace("T", " ")}</span></>
            : <div><span>{say("Layout not recorded", "레이아웃 기록 없음")}</span><code>{example.top ?? example.design}</code></div>}</div>
          <div className="overview__card-body"><h4>{example.design}</h4><p>{desc ? desc[lang === "ko" ? 1 : 0] : example.top ?? example.design}</p>
            <div className="overview__card-meta"><span>{example.clock_period_ns !== null ? `${example.clock_period_ns} ns · ${say("config clock", "설정 클록")}` : "—"}</span><span>{db ? group?.cases.length ?? 0 : "—"} {say("cases", "케이스")} · {db ? group?.candidates ?? 0 : "—"} {say("candidates", "후보")}</span></div>
            <div className="overview__card-status">{group ? `${summary.clean} ${say("verification passed", "후보 검증 통과")}` : db ? say("No measured cases yet", "측정된 케이스 없음") : say("Records unavailable", "기록 확인 전")}{modelOpen && <span>{say("Model qualification open", "모델 적합성 미확정")}</span>}</div>
            {modelOpen && latestZero && <small>{say("Latest candidate: recorded physical-check counts are all zero.", "최근 후보: 기록된 물리 검증 위반 수는 모두 0입니다.")}</small>}
            {latest && <small>{say("Latest case", "최근 케이스")} · {recordedAt(latest).replace("T", " ")} · {latest.outcome}</small>}
            {example.expected_outcome === "fail" && <small>{say("Declared negative control", "실패를 예상한 대조 실험")}</small>}
            <button disabled={!group} onClick={() => onOpenDesign(example.design)}>{group ? say("Inspect measured cases →", "측정 케이스 보기 →") : say("Configuration available · no records", "설정 있음 · 기록 없음")}</button>
          </div>
        </article>;
      })}</div>
      {shown.length === 0 && <p>{examples === null && !db ? say("Loading example catalog…", "예제 목록을 불러오는 중…") : say("No matching examples.", "조건에 맞는 예제가 없습니다.")}</p>}
      <div className="overview__analog"><div><strong>{say("Analog & custom cells", "아날로그·커스텀 셀")}</strong><p>{say("Browse checked-in schematics and testbenches in the Schematic workspace.", "회로도 작업 공간에서 저장된 회로와 테스트벤치를 확인하세요.")}</p></div><button onClick={onOpenSchematic}>{say("Open schematics →", "회로도 열기 →")}</button></div>
    </section>
    <section aria-label={say("External dashboard references", "외부 대시보드 참고 사례")}>
      <div className="overview__section-heading"><h3>{say("Other EDA visualization examples", "다른 EDA 시각화 예제")}</h3><small>{say("References reviewed", "참고 자료 확인")} · 2026-10-04</small></div>
      <div className="overview__references">{REFERENCES.map(reference => <a key={reference.name} href={reference.url} target="_blank" rel="noreferrer"><strong>{reference.name} ↗</strong><span>{reference.detail[lang === "ko" ? 1 : 0]}</span></a>)}</div>
    </section>
  </div>;
}
