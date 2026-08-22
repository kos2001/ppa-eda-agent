import { useEffect, useMemo, useState } from "react";
import {
  fetchReferenceDb,
  type CandidateDataPointers,
  type CandidateResult,
  type CandidateVerdict,
  type PipelineCase,
  type ProcessStageId,
} from "../api/referenceDb";
import { useLang } from "../i18n";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import LayoutView from "./LayoutView";
import "./Tabs.css";
import "./PipelineTab.css";

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
        return (
          <div
            key={stage.id}
            className={`pipeline__process-stage ${reached ? "pipeline__process-stage--reached" : "pipeline__process-stage--empty"}`}
            title={note}
          >
            <span className="pipeline__process-stage-index">{String(index + 1).padStart(2, "0")}</span>
            <strong>{stage.name}</strong>
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
              <span className="pill pill--good">{r.agent}</span>
              <span className="pipeline__hitl-time">{r.reviewed_at}</span>
              <span className="pipeline__hitl-summary">{r.summary}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
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
          </div>
          <div className="metric-card"><span className="metric-card__label">search depth</span><strong className="metric-card__value">{pipelineCase.iterations.length}</strong><span className="metric-card__note">iterations · {candidates.length} candidates</span></div>
          <div className="metric-card metric-card--good"><span className="metric-card__label">passed</span><strong className="metric-card__value">{passed}</strong><span className="metric-card__note">verified candidates</span></div>
          <div className={`metric-card ${failed > 0 ? "metric-card--critical" : ""}`}><span className="metric-card__label">rejected</span><strong className="metric-card__value">{failed}</strong><span className="metric-card__note">failed or violated guardrails</span></div>
        </div>

        <ProcessStages pipelineCase={pipelineCase} />

        {pipelineCase.topology && <TopologySummary topology={pipelineCase.topology} />}

        {chartData.length > 0 && (
          <div className="pipeline__chart">
            <div className="tab__meta-label">candidate area comparison · lower is better</div>
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={chartData} margin={{ top: 18, right: 12, left: 6, bottom: 30 }}>
                <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="name" angle={-18} textAnchor="end" tick={{ fill: "var(--text-dim)", fontSize: 10 }} stroke="var(--border)" />
                <YAxis tick={{ fill: "var(--text-dim)", fontSize: 10 }} stroke="var(--border)" />
                <Tooltip contentStyle={{ background: "var(--surface-raised)", border: "1px solid var(--border)", borderRadius: 8, fontFamily: "var(--mono)" }} />
                <Bar dataKey="area" radius={[5, 5, 0, 0]}>{chartData.map((entry) => <Cell key={entry.name} fill={entry.passed ? "var(--good)" : "var(--critical)"} />)}</Bar>
              </BarChart>
            </ResponsiveContainer>
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
          </div>
        )}

        <HumanInTheLoopPanel pipelineCase={pipelineCase} />
      </div>
    </div>
  );
}

export default function PipelineTab() {
  const { t } = useLang();
  const [cases, setCases] = useState<PipelineCase[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [designFilter, setDesignFilter] = useState<string>("all");

  useEffect(() => {
    let cancelled = false;
    fetchReferenceDb()
      .then((db) => {
        if (cancelled) return;
        const flat = Object.values(db.designs).flat();
        flat.sort((a, b) => b.date.localeCompare(a.date));
        setCases(flat);
      })
      .catch((e) => !cancelled && setError(String(e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

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
