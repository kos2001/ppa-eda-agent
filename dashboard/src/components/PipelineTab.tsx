import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchPipelineRunStatus,
  fetchReferenceDb,
  triggerPipelineRun,
  type CandidateDataPointers,
  type CandidateResult,
  type CandidateVerdict,
  type PipelineCase,
  type PipelineRunState,
  type ProcessStageId,
} from "../api/referenceDb";
import { translateStream, translateViaServer } from "../api/gateway";
import { useAgent } from "../agentContext";
import { useLang } from "../i18n";
import LayoutView from "./LayoutView";
import SlackChart from "./SlackChart";
import "./Tabs.css";
import "./PipelineTab.css";

// Lazy: recharts' internal chunk is the largest in the app (~88KB
// gzipped) — deferring it keeps it off the critical path for
// PipelineTab's initial render (Pipeline is the default tab every
// session loads). See CandidateAreaChart.tsx.
const CandidateAreaChart = lazy(() => import("./CandidateAreaChart"));

const DATA_CATEGORY_ORDER: (keyof CandidateDataPointers)[] = [
  "circuit",
  "layout",
  "constraint_pdk",
  "verification",
];

// Fallback for cases written before orchestrator.py started including
// process_stages — keeps older reference-db entries renderable instead
// of crashing on a missing field.
const FALLBACK_PROCESS_STAGES: { id: ProcessStageId; name: string }[] = [
  { id: "extraction", name: "Circuit & Layout Extraction" },
  { id: "topology", name: "Topology Understanding" },
  { id: "placement_strategy", name: "Placement Strategy / Candidate Generation" },
  { id: "physical_constraint", name: "Physical Constraint Evaluation" },
  { id: "routing_generation", name: "Routing Generation Evaluation" },
  { id: "routing_candidate", name: "Routing Candidate Generation" },
  { id: "verification_ppa", name: "Verification & PPA Evaluation" },
  { id: "feedback", name: "AI Feedback / Repair / Optimization" },
];

// Which real subagent (.claude/agents/*.md) owns each of the 8 pipeline
// stages, and what it actually does — paraphrased from each agent's own
// description/system-prompt file, not invented. Used both for the small
// "owner" badge on each ProcessStages card and the AgentRoles legend
// panel, so the two never drift (one source of truth).
const STAGE_AGENT: Record<
  ProcessStageId,
  { agent: string; role: { en: string; ko: string } }
> = {
  extraction: {
    agent: "circuit-layout-extractor",
    role: {
      en: "Extracts a structural summary and topology signature from RTL (and any existing LEF/DEF layout) for the rest of the pipeline to reason about.",
      ko: "RTL(및 기존 LEF/DEF 레이아웃이 있다면 그것까지)에서 구조 요약과 토폴로지 시그니처를 추출해 나머지 파이프라인이 참고할 수 있게 합니다.",
    },
  },
  topology: {
    agent: "topology-analyst",
    role: {
      en: "Classifies the design's topology and surfaces similar past cases from reference-db/ before placement is proposed cold.",
      ko: "설계의 토폴로지를 분류하고, reference-db/에서 비슷한 과거 사례를 찾아 배치 전략가가 무(無)에서 시작하지 않도록 제공합니다.",
    },
  },
  placement_strategy: {
    agent: "placement-strategist",
    role: {
      en: "Proposes N real OpenLane config-override candidates (utilization, die sizing, macro hints) — every candidate gets a real run, no scoring without one.",
      ko: "실제 OpenLane 설정 오버라이드 후보(utilization, 다이 크기, 매크로 배치 힌트 등) N개를 제안합니다 — 모든 후보는 실제로 실행되며, 실행 없이 점수만 매기지 않습니다.",
    },
  },
  physical_constraint: {
    agent: "physical-constraint-evaluator",
    role: {
      en: "Flags real physical-constraint problems (density, legalization, PDN, congestion) before the expensive routing stage runs.",
      ko: "비용이 큰 라우팅 단계로 넘어가기 전에 실제 물리적 제약 문제(밀도, legalization, PDN, congestion)를 점검해 걸러냅니다.",
    },
  },
  routing_generation: {
    agent: "routing-candidate-evaluator",
    role: {
      en: "Reads TritonRoute's real routing output (DRC violations, wirelength, via count) for candidates that survived the placement check.",
      ko: "배치 검사를 통과한 후보의 실제 TritonRoute 라우팅 결과(DRC 위반, 배선 길이, via 개수)를 평가합니다.",
    },
  },
  routing_candidate: {
    agent: "routing-candidate-evaluator",
    role: {
      en: "Same agent as Routing Generation Evaluation — evaluates the detailed-routing result once a run reaches that step.",
      ko: "Routing Generation Evaluation과 동일 에이전트 — 실행이 detailed routing 단계에 도달하면 그 결과를 평가합니다.",
    },
  },
  verification_ppa: {
    agent: "verification-ppa-evaluator",
    role: {
      en: "Produces the final correctness + PPA verdict from a completed run's real metrics.json (area, all timing corners, power, DRC, LVS).",
      ko: "완료된 실행의 실제 metrics.json(면적, 전체 타이밍 코너, 파워, DRC, LVS)을 바탕으로 최종 정합성 + PPA 판정을 내립니다.",
    },
  },
  feedback: {
    agent: "feedback-optimizer",
    role: {
      en: "Closes the loop orchestrator.py's mechanical propose_repairs() can't: decides winner vs. next-iteration candidates, interprets each case.",
      ko: "orchestrator.py의 기계적 propose_repairs()가 처리하지 못하는 부분을 마무리합니다 — 승자 확정 또는 다음 iteration 후보 결정, 각 케이스에 대한 해석을 담당합니다.",
    },
  },
};

// Reverse index of STAGE_AGENT, keyed by agent name — lets any place
// that shows a bare agent name (e.g. a human-in-the-loop review pill)
// look up its role for a tooltip without a second copy of the text.
const AGENT_ROLE_BY_NAME: Record<string, { en: string; ko: string }> = Object.fromEntries(
  Object.values(STAGE_AGENT).map((entry) => [entry.agent, entry.role])
);

// Agent legend — every subagent that touches this pipeline, in pipeline
// order, plus ppa-eda-analyst (not one of the 8 stages; it's the
// separate report-paste/live-simulation diagnosis agent behind the
// sidebar's "ppa-eda-analyst" tab). Deduplicates routing-candidate-
// evaluator (owns 2 stages) automatically via the Map below.
function AgentRolesLegend() {
  const { lang, t } = useLang();
  const seen = new Set<string>();
  const rows = (Object.values(STAGE_AGENT) as { agent: string; role: { en: string; ko: string } }[])
    .filter((entry) => (seen.has(entry.agent) ? false : (seen.add(entry.agent), true)));

  return (
    <details className="pipeline__agent-legend">
      <summary>{t("pipeline_agent_legend_title")}</summary>
      <ul>
        {rows.map((entry) => (
          <li key={entry.agent}>
            <code>{entry.agent}</code>
            <span>{entry.role[lang]}</span>
          </li>
        ))}
        <li className="pipeline__agent-legend-note">
          <code>ppa-eda-analyst</code>
          <span>{t("pipeline_agent_legend_diagnosis_note")}</span>
        </li>
      </ul>
    </details>
  );
}

function ProcessStages({ pipelineCase }: { pipelineCase: PipelineCase }) {
  const stages = pipelineCase.process_stages ?? FALLBACK_PROCESS_STAGES;
  const candidates = pipelineCase.iterations.flatMap((it) => it.results);
  const stageCounts: Partial<Record<ProcessStageId, number>> = {};
  for (const c of candidates) {
    if (c.stage) stageCounts[c.stage] = (stageCounts[c.stage] ?? 0) + 1;
  }
  const feedbackCount = candidates.filter((c) => c.produced_by_feedback).length;

  function countFor(id: ProcessStageId): { count: number; total: number; note: string } {
    switch (id) {
      case "extraction":
        return { count: candidates.length, total: candidates.length, note: "circuit/layout data per candidate" };
      case "topology":
        return pipelineCase.topology
          ? { count: 1, total: 1, note: `${pipelineCase.topology.has_macros ? "macro-heavy" : "std-cell only"}, ${pipelineCase.topology.sequential_element_estimate} flops` }
          : { count: 0, total: 1, note: "no topology.json for this design" };
      case "placement_strategy":
        return { count: candidates.length, total: candidates.length, note: `${candidates.length} candidate(s) proposed` };
      case "feedback":
        return { count: feedbackCount, total: candidates.length, note: feedbackCount > 0 ? `${feedbackCount} candidate(s) from auto-repair` : "no repair needed" };
      default:
        return { count: stageCounts[id] ?? 0, total: candidates.length, note: `${stageCounts[id] ?? 0}/${candidates.length} candidate run(s) reached this stage` };
    }
  }

  return (
    <div className="pipeline__process" aria-label="8-step layout agent process">
      {stages.map((stage, index) => {
        const { count, note } = countFor(stage.id);
        const reached = count > 0;
        const owner = STAGE_AGENT[stage.id];
        return (
          <div
            key={stage.id}
            className={`pipeline__process-stage ${reached ? "pipeline__process-stage--reached" : "pipeline__process-stage--empty"}`}
            title={owner ? `${owner.agent} — ${owner.role.en}` : note}
          >
            <span className="pipeline__process-stage-index">{String(index + 1).padStart(2, "0")}</span>
            <strong>{stage.name}</strong>
            {owner && <span className="pipeline__process-stage-agent">{owner.agent}</span>}
            <span className="pipeline__process-stage-note">{note}</span>
          </div>
        );
      })}
    </div>
  );
}

function TopologySummary({ topology }: { topology: NonNullable<PipelineCase["topology"]> }) {
  return (
    <div className="tab__meta pipeline__topology">
      <span>
        <span className="tab__meta-label">macros</span>
        {topology.has_macros ? "yes" : "no"}
      </span>
      <span>
        <span className="tab__meta-label">modules</span>
        {topology.module_count}
      </span>
      <span>
        <span className="tab__meta-label">clock domains</span>
        {topology.clock_domain_count}
      </span>
      <span>
        <span className="tab__meta-label">power domains</span>
        {topology.power_domain_count}
      </span>
      <span>
        <span className="tab__meta-label">sequential elements (est.)</span>
        {topology.sequential_element_estimate}
      </span>
    </div>
  );
}

const STAGE_SHORT_LABEL: Record<ProcessStageId, { short: string; full: string }> = {
  extraction: { short: "extract", full: "Circuit & Layout Extraction" },
  topology: { short: "topology", full: "Topology Understanding" },
  placement_strategy: { short: "placement", full: "Placement Strategy / Candidate Generation" },
  physical_constraint: { short: "phys. constraint", full: "Physical Constraint Evaluation" },
  routing_generation: { short: "routing gen.", full: "Routing Generation Evaluation" },
  routing_candidate: { short: "routing cand.", full: "Routing Candidate Generation" },
  verification_ppa: { short: "verify/PPA", full: "Verification & PPA Evaluation" },
  feedback: { short: "feedback", full: "AI Feedback / Repair / Optimization" },
};

function DataPointers({ data }: { data: CandidateDataPointers }) {
  return (
    <div className="pipeline__data-pointers">
      {DATA_CATEGORY_ORDER.map((category) => {
        const fields = Object.entries(data[category] ?? {});
        const present = fields.filter(([, v]) => v != null);
        return (
          <div key={category} className="pipeline__data-category">
            <span className="tab__meta-label">{category.replace("_", " / ")}</span>
            {present.length === 0 ? (
              <span className="pipeline__data-missing">—</span>
            ) : (
              <ul>
                {present.map(([field, path]) => (
                  <li key={field} title={path ?? undefined}>
                    {field}
                  </li>
                ))}
              </ul>
            )}
          </div>
        );
      })}
    </div>
  );
}

// Every real PVT corner OpenLane actually analyzed (typically 9: {min,
// nom, max} x {ff_n40C_1v95, tt_025C_1v80, ss_100C_1v60}) — real setup/
// hold WNS per corner from metrics.json, not just the single worst value.
function TimingCorners({ corners }: { corners: CandidateVerdict["timing_corners"] }) {
  if (corners.length === 0) return null;
  return (
    <div className="pipeline__timing">
      <span className="tab__meta-label">timing — real WNS per PVT corner</span>
      <SlackChart corners={corners} />
      <table className="tab__summary pipeline__timing-table">
        <thead>
          <tr>
            <th>corner</th>
            <th>setup WNS</th>
            <th>hold WNS</th>
          </tr>
        </thead>
        <tbody>
          {corners.map((c) => (
            <tr key={c.corner}>
              <td>{c.corner}</td>
              <td className={c.setup_wns < 0 ? "pipeline__timing-bad" : undefined}>
                {c.setup_wns.toFixed(3)} ns
              </td>
              <td className={c.hold_wns != null && c.hold_wns < 0 ? "pipeline__timing-bad" : undefined}>
                {c.hold_wns != null ? `${c.hold_wns.toFixed(3)} ns` : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// Real power (OpenLane's default-activity estimate — no VCD/SAIF
// annotation configured in this pipeline yet) and real IR-drop / supply-
// voltage numbers from the actual power grid OpenROAD generated for
// this candidate's power domain.
function PowerSummary({ verdict }: { verdict: CandidateVerdict }) {
  const { power, power_domain: powerDomain } = verdict;
  if (!power && !powerDomain) return null;
  return (
    <div className="pipeline__power">
      <span className="tab__meta-label">power / power domain</span>
      <div className="tab__meta">
        {power && (
          <>
            <span>
              <span className="tab__meta-label">internal</span>
              {(power.internal_w! * 1000).toFixed(4)} mW
            </span>
            <span>
              <span className="tab__meta-label">switching</span>
              {(power.switching_w! * 1000).toFixed(4)} mW
            </span>
            <span>
              <span className="tab__meta-label">leakage</span>
              {(power.leakage_w! * 1e6).toFixed(4)} µW
            </span>
            <span>
              <span className="tab__meta-label">total</span>
              {(power.total_w! * 1000).toFixed(4)} mW
            </span>
          </>
        )}
        {powerDomain && (
          <>
            <span>
              <span className="tab__meta-label">supply voltage</span>
              {powerDomain.voltage_worst_v} V
            </span>
            <span>
              <span className="tab__meta-label">IR drop (worst)</span>
              {(powerDomain.ir_drop_worst_v! * 1000).toFixed(4)} mV
            </span>
            <span>
              <span className="tab__meta-label">IR drop (avg)</span>
              {(powerDomain.ir_drop_avg_v! * 1000).toFixed(4)} mV
            </span>
          </>
        )}
      </div>
    </div>
  );
}

function CandidateRow({
  candidate,
  expanded,
  onToggle,
}: {
  candidate: CandidateResult;
  expanded: boolean;
  onToggle: () => void;
}) {
  const stageBadge = candidate.stage && (
    <span className="pipeline__stage-badge" title={STAGE_SHORT_LABEL[candidate.stage].full}>
      {STAGE_SHORT_LABEL[candidate.stage].short}
    </span>
  );
  const feedbackBadge = candidate.produced_by_feedback && (
    <span className="pipeline__stage-badge pipeline__stage-badge--feedback" title="produced by AI feedback/repair from a prior iteration's failure">
      ↺ repaired
    </span>
  );

  if (candidate.error) {
    return (
      <tr>
        <td>{candidate.tag}</td>
        <td>
          <span className="pill pill--critical">FAIL TO RUN</span>
        </td>
        <td colSpan={2} className="pipeline__error-cell">
          {candidate.error}
        </td>
        <td>
          {stageBadge}
          {feedbackBadge}
        </td>
      </tr>
    );
  }
  const v = candidate.verdict;
  return (
    <>
      <tr
        className={candidate.data ? "pipeline__row--expandable" : undefined}
        onClick={candidate.data ? onToggle : undefined}
      >
        <td>
          {candidate.data && <span className="pipeline__disclosure">{expanded ? "▾" : "▸"}</span>}
          {candidate.tag}
        </td>
        <td>
          <span className={`pill ${v?.passed ? "pill--good" : "pill--critical"}`}>
            {v?.passed ? "PASS" : "FAIL"}
          </span>
        </td>
        <td>{v?.area_um2 != null ? `${v.area_um2} µm²` : "—"}</td>
        <td>{v?.utilization != null ? v.utilization.toFixed(3) : "—"}</td>
        <td>
          {v && !v.passed && v.violations.length > 0
            ? v.violations.join("; ")
            : v?.worst_setup_wns != null
              ? `WNS ${v.worst_setup_wns}`
              : "—"}
        </td>
        <td>
          {stageBadge}
          {feedbackBadge}
        </td>
      </tr>
      {expanded && candidate.data && (
        <tr className="pipeline__detail-row">
          <td colSpan={6}>
            {candidate.layout && (
              <>
                <span className="tab__meta-label">placement &amp; routing — real DEF output</span>
                <LayoutView layout={candidate.layout} />
              </>
            )}
            {v && <TimingCorners corners={v.timing_corners} />}
            {v && <PowerSummary verdict={v} />}
            <DataPointers data={candidate.data} />
          </td>
        </tr>
      )}
    </>
  );
}

// Surfaces the real human-in-the-loop workflow (pipeline/request_review.py):
// an OPEN case (no winner, and propose_repairs() found nothing auto-
// repairable) is flagged for review; once a subagent's verdict has been
// applied via `request_review.py apply`, it shows up here as real history,
// not just buried in the diagnosis text.
function HumanInTheLoopPanel({ pipelineCase }: { pipelineCase: PipelineCase }) {
  const { lang } = useLang();
  const isOpen = !pipelineCase.winner_tag;
  const reviews = pipelineCase.human_in_the_loop ?? [];
  if (!isOpen && reviews.length === 0) return null;

  return (
    <div className="pipeline__hitl">
      <span className="tab__meta-label">human-in-the-loop</span>
      {isOpen && reviews.length === 0 && (
        <p className="pipeline__hitl-needed">
          needs review — run{" "}
          <code>python3 pipeline/request_review.py request --design {pipelineCase.design}</code>{" "}
          to dispatch a subagent
        </p>
      )}
      {reviews.length > 0 && (
        <ul className="pipeline__hitl-log">
          {reviews.map((r, i) => (
            <li key={i}>
              <span className="pill pill--good" title={AGENT_ROLE_BY_NAME[r.agent]?.[lang]}>
                {r.agent}
              </span>
              <span className="pipeline__hitl-time">{r.reviewed_at}</span>
              <span className="pipeline__hitl-summary">
                {r.summary}
                <TranslateBlock text={r.summary} />
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// On-demand machine translation for real reference-db text (diagnosis,
// review summaries) — only offered when the UI is in Korean, since the
// dashboard's i18n never touches this real subagent-written content
// (precise numbers like transition times/capacitances must stay exactly
// as originally written). Reuses the same hermes-gateway key/server
// config the Diagnosis tab already manages via useAgent(), so there's
// no separate key-entry flow for this feature.
function TranslateBlock({ text }: { text: string }) {
  const { lang, t } = useLang();
  const { key, serverConfigured } = useAgent();
  const [translated, setTranslated] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [elapsedSec, setElapsedSec] = useState(0);
  const timerRef = useRef<number | null>(null);

  useEffect(() => () => {
    if (timerRef.current !== null) window.clearInterval(timerRef.current);
  }, []);

  if (lang !== "ko") return null;
  const canTranslate = Boolean(key) || serverConfigured;

  function handleTranslate() {
    setLoading(true);
    setTranslated("");
    setError(null);
    setElapsedSec(0);
    const startedAt = performance.now();
    timerRef.current = window.setInterval(() => {
      setElapsedSec(Math.round((performance.now() - startedAt) / 1000));
    }, 1000);
    function stopTimer() {
      if (timerRef.current !== null) {
        window.clearInterval(timerRef.current);
        timerRef.current = null;
      }
    }
    const callbacks = {
      // This gateway model doesn't stream token-by-token — it generates
      // the full response server-side and flushes it as one burst (every
      // chunk shares the same upstream timestamp), so onToken may not
      // fire at all until the whole translation is ready. Real long
      // diagnosis text took ~9,800 completion tokens and ~3-4 minutes
      // end to end with zero partial output in between — the elapsed
      // timer below exists so that wait doesn't read as broken.
      onToken: (delta: string) => setTranslated((prev) => (prev ?? "") + delta),
      onDone: () => {
        stopTimer();
        setLoading(false);
      },
      onError: (e: Error) => {
        stopTimer();
        setLoading(false);
        setError(e.message);
      },
    };
    if (serverConfigured) translateViaServer(text, callbacks);
    else if (key) translateStream(key, text, callbacks);
  }

  if (translated !== null) {
    return (
      <div className="pipeline__translation">
        <span className="tab__meta-label">{t("pipeline_translate_label")}</span>
        {loading && translated === "" && (
          <p className="pipeline__translate-waiting">
            {t("pipeline_translate_loading")} ({elapsedSec}s) — {t("pipeline_translate_long_wait_hint")}
          </p>
        )}
        <p>{translated}{loading && translated !== "" && "…"}</p>
        {error && <p className="tab__error">{error}</p>}
      </div>
    );
  }

  return (
    <button
      className="pipeline__translate-button"
      onClick={handleTranslate}
      disabled={!canTranslate || loading}
      title={!canTranslate ? t("pipeline_translate_needs_key") : undefined}
    >
      {loading ? t("pipeline_translate_loading") : t("pipeline_translate_button")}
    </button>
  );
}

function CaseCard({ pipelineCase }: { pipelineCase: PipelineCase }) {
  const [expandedTags, setExpandedTags] = useState<Set<string>>(new Set());
  const candidates = pipelineCase.iterations.flatMap((iteration) => iteration.results);
  const passed = candidates.filter((candidate) => candidate.verdict?.passed).length;
  const failed = candidates.length - passed;
  const chartData = candidates
    .filter((candidate) => candidate.verdict?.area_um2 != null)
    .map((candidate) => ({ name: candidate.tag, area: candidate.verdict?.area_um2 ?? 0, passed: Boolean(candidate.verdict?.passed) }));

  function toggle(tag: string) {
    setExpandedTags((prev) => {
      const next = new Set(prev);
      if (next.has(tag)) next.delete(tag);
      else next.add(tag);
      return next;
    });
  }

  return (
    <div className="panel">
      <span className="panel__title">
        {pipelineCase.design} — {pipelineCase.date}
      </span>
      <div className="panel__body">
        <div className="metric-grid pipeline__metrics">
          <div className={`metric-card ${pipelineCase.winner_tag ? "metric-card--good" : "metric-card--critical"}`}>
            <span className="metric-card__label">closure status</span>
            <strong className="metric-card__value">{pipelineCase.winner_tag ? "CLOSED" : "OPEN"}</strong>
            <span className="metric-card__note">winner · {pipelineCase.winner_tag ?? "not found"}</span>
            {pipelineCase.stop_reason && pipelineCase.stop_reason !== "winner_found" && (
              <span className="metric-card__note" title="why orchestrator.orchestrate()'s loop stopped">
                stop reason · {pipelineCase.stop_reason}
              </span>
            )}
          </div>
          <div className="metric-card"><span className="metric-card__label">search depth</span><strong className="metric-card__value">{pipelineCase.iterations.length}</strong><span className="metric-card__note">iterations · {candidates.length} candidates</span></div>
          <div className="metric-card metric-card--good"><span className="metric-card__label">passed</span><strong className="metric-card__value">{passed}</strong><span className="metric-card__note">verified candidates</span></div>
          <div className={`metric-card ${failed > 0 ? "metric-card--critical" : ""}`}><span className="metric-card__label">rejected</span><strong className="metric-card__value">{failed}</strong><span className="metric-card__note">failed or violated guardrails</span></div>
        </div>

        <ProcessStages pipelineCase={pipelineCase} />
        <AgentRolesLegend />

        {pipelineCase.topology && <TopologySummary topology={pipelineCase.topology} />}

        {chartData.length > 0 && (
          <div className="pipeline__chart">
            <div className="tab__meta-label">candidate area comparison · lower is better</div>
            <Suspense fallback={<div style={{ height: 220 }} />}>
              <CandidateAreaChart data={chartData} />
            </Suspense>
          </div>
        )}

        <div className="tab__meta">
          <span>
            <span className="tab__meta-label">outcome</span>
            {pipelineCase.outcome}
          </span>
          <span>
            <span className="tab__meta-label">winner</span>
            {pipelineCase.winner_tag ?? "none"}
          </span>
          <span>
            <span className="tab__meta-label">iterations run</span>
            {pipelineCase.iterations.length}
          </span>
        </div>

        {pipelineCase.iterations.map((iter) => (
          <div key={iter.iteration} className="pipeline__iteration">
            <div className="tab__meta-label">iteration {iter.iteration}</div>
            <table className="tab__summary">
              <thead>
                <tr>
                  <th>candidate</th>
                  <th>verdict</th>
                  <th>area</th>
                  <th>utilization</th>
                  <th>detail</th>
                  <th>process stage</th>
                </tr>
              </thead>
              <tbody>
                {iter.results.map((c) => (
                  <CandidateRow
                    key={c.tag}
                    candidate={c}
                    expanded={expandedTags.has(c.tag)}
                    onToggle={() => toggle(c.tag)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        ))}

        {pipelineCase.diagnosis && (
          <div className="pipeline__diagnosis">
            <span className="tab__meta-label">diagnosis</span>
            <p>{pipelineCase.diagnosis}</p>
            <TranslateBlock text={pipelineCase.diagnosis} />
          </div>
        )}

        <HumanInTheLoopPanel pipelineCase={pipelineCase} />
      </div>
    </div>
  );
}

// Runs the actual agent, not just displays what it did before — the
// concrete answer to "why is this a dashboard, not the DTCO agent
// itself": this panel IS the agent's control surface. Press "run", a
// real pipeline/orchestrator.py candidate-generation-and-auto-repair
// loop spawns server-side against real OpenLane, and the panel polls
// its live status until a new reference-db case shows up below.
function RunAgentPanel({
  design,
  onRunFinished,
}: {
  design: string | null;
  onRunFinished: () => void;
}) {
  const { t } = useLang();
  const [runState, setRunState] = useState<PipelineRunState | null>(null);
  const [launchError, setLaunchError] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  const stopPolling = useCallback(() => {
    if (pollRef.current != null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  useEffect(() => stopPolling, [stopPolling]);

  async function handleRun() {
    if (!design) return;
    setLaunchError(null);
    try {
      const state = await triggerPipelineRun(design);
      setRunState(state);
      if (state.status === "running") {
        stopPolling();
        pollRef.current = window.setInterval(async () => {
          try {
            const polled = await fetchPipelineRunStatus(design);
            setRunState(polled);
            if (polled.status !== "running") {
              stopPolling();
              onRunFinished();
            }
          } catch {
            // transient — next tick retries
          }
        }, 4000);
      }
    } catch (e) {
      setLaunchError(String(e));
    }
  }

  if (!design) return null;
  const running = runState?.status === "running";

  return (
    <div className="pipeline__run-agent">
      <button className="pipeline__run-button" disabled={running} onClick={handleRun}>
        {running ? t("pipeline_run_running") : t("pipeline_run_button")} — {design}
      </button>
      {runState?.status === "done" && (
        <span className="pill pill--good">{t("pipeline_run_done")}</span>
      )}
      {runState?.status === "error" && (
        <span className="pill pill--critical" title={runState.error ?? undefined}>
          {t("pipeline_run_failed")}
        </span>
      )}
      {launchError && <p className="tab__error">{launchError}</p>}
      {running && runState?.tail && runState.tail.length > 0 && (
        <pre className="pipeline__run-log">{runState.tail.slice(-12).join("\n")}</pre>
      )}
    </div>
  );
}

export default function PipelineTab() {
  const { t } = useLang();
  const [cases, setCases] = useState<PipelineCase[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [designFilter, setDesignFilter] = useState<string>("all");

  const loadCases = useCallback(() => {
    return fetchReferenceDb()
      .then((db) => {
        const flat = Object.values(db.designs).flat();
        flat.sort((a, b) => b.date.localeCompare(a.date));
        setCases(flat);
      })
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    let cancelled = false;
    loadCases().finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [loadCases]);

  const designNames = useMemo(
    () => Array.from(new Set((cases ?? []).map((c) => c.design))).sort(),
    [cases]
  );
  const visibleCases = useMemo(
    () =>
      designFilter === "all"
        ? cases
        : (cases ?? []).filter((c) => c.design === designFilter),
    [cases, designFilter]
  );

  return (
    <div className="tab">
      <div className="panel">
        <span className="panel__title">{t("pipeline_panel_title")}</span>
        <div className="panel__body">
          <p>{t("pipeline_intro")}</p>
          {designNames.length > 1 && (
            <label className="pipeline__filter">
              <span className="tab__meta-label">{t("pipeline_filter_design")}</span>
              <select
                value={designFilter}
                onChange={(e) => setDesignFilter(e.target.value)}
              >
                <option value="all">{t("pipeline_filter_all")}</option>
                {designNames.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <RunAgentPanel
            design={designFilter === "all" ? designNames[0] ?? null : designFilter}
            onRunFinished={loadCases}
          />
        </div>
      </div>

      {loading && <p>{t("pipeline_loading")}</p>}
      {error && (
        <p className="tab__error">
          {error} — {t("pipeline_error_hint")}
        </p>
      )}
      {!loading && !error && visibleCases?.length === 0 && (
        <p>{t("pipeline_empty")}</p>
      )}

      {visibleCases?.map((c) => (
        <CaseCard key={`${c.design}__${c.date}`} pipelineCase={c} />
      ))}
    </div>
  );
}
