import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { PipelineCase } from "../api/referenceDb";
import { useLang } from "../i18n";
import MacroModelCoverage from "./MacroModelCoverage";
import CandidateAreaChart from "./CandidateAreaChart";
import ObjectivesChart from "./ObjectivesChart";
import TranslateBlock from "./TranslateBlock";
import HumanInTheLoopPanel from "./ReviewPanel";
import { AgentRolesLegend, ProcessStages } from "./PipelineProcess";
import { AxisLabel, CaseLayoutImage, TopologySummary } from "./CaseParts";
import CandidateRow from "./CandidateRow";
import {
  areaChartData,
  caseStamp,
  countCandidates,
  flattenCandidates,
  winnerFieldCandidates,
} from "./pipelineModel";

function CaseCard({
  pipelineCase,
  defaultOpen,
  onApplied,
  focusDesign,
  focusCase,
}: {
  pipelineCase: PipelineCase;
  defaultOpen: boolean;
  onApplied: () => void;
  focusDesign: string | null;
  focusCase: string | null;
}) {
  const { t } = useLang();
  // Collapsed by default for all but the newest case. Measured problem
  // this fixes: with every case fully expanded the page was 22 screens
  // tall, so there was no way to see what cases exist without scrolling
  // through all of their contents. The newest stays open so the page is
  // never just a list of closed boxes.
  const [open, setOpen] = useState(defaultOpen);
  const cardRef = useRef<HTMLDivElement | null>(null);

  // The Action Center's "open the review workflow" jumps here rather
  // than telling the reader to go find the right card themselves —
  // pointing at work without taking you to it is the scattering this
  // whole redesign is meant to remove.
  useEffect(() => {
    if ((focusDesign && focusDesign === pipelineCase.design) || (focusCase && focusCase === pipelineCase.file)) {
      setOpen(true);
      cardRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [focusDesign, focusCase, pipelineCase.design, pipelineCase.file]);
  const [expandedTags, setExpandedTags] = useState<Set<string>>(new Set());
  const candidates = useMemo(() => flattenCandidates(pipelineCase), [pipelineCase]);
  const { passed, deferred, screenedOnly, failed, unverified } = useMemo(
    () => countCandidates(candidates),
    [candidates]
  );
  const chartData = useMemo(() => areaChartData(pipelineCase), [pipelineCase]);
  const winnerField = useMemo(() => winnerFieldCandidates(pipelineCase), [pipelineCase]);

  const toggle = useCallback((tag: string) => {
    setExpandedTags((prev) => {
      const next = new Set(prev);
      if (next.has(tag)) next.delete(tag);
      else next.add(tag);
      return next;
    });
  }, []);

  if (!open) {
    return (
      <div className="panel pipeline__case--collapsed" ref={cardRef}>
        <button className="pipeline__case-toggle" onClick={() => setOpen(true)}>
          <span className="pipeline__case-toggle-name">
            {/* The design name is the group header above; what this row
                has to answer is when, and what it changed. */}
            ▸ {caseStamp(pipelineCase)}
          </span>
          <AxisLabel pipelineCase={pipelineCase} />
          <span className={`pill ${pipelineCase.winner_tag ? "pill--good" : "pill--critical"}`}>
            {pipelineCase.winner_tag ? "CLOSED" : "OPEN"}
          </span>
          <span className="pipeline__case-toggle-meta">
            {candidates.length - deferred + screenedOnly} {t("pipeline_case_candidates")} · {passed} PASS · {failed} FAIL{deferred > 0 ? ` · ${deferred} NOT EVALUATED${screenedOnly ? ` (${screenedOnly} screen only)` : ""}` : ""}
          </span>
        </button>
      </div>
    );
  }

  return (
    <div className="panel" ref={cardRef}>
      {pipelineCase.evaluation_budget && <p className="pipeline__note">
        {t("evaluation_budget_label")} · {pipelineCase.evaluation_budget.started_evaluations} / {pipelineCase.evaluation_budget.limits.max_evaluations ?? "∞"}
        {" · "}{pipelineCase.evaluation_budget.elapsed_seconds.toFixed(1)} s
        {" · "}{pipelineCase.evaluation_budget.not_evaluated.length} NOT EVALUATED
      </p>}
      <span className="panel__title">
        <button className="pipeline__case-collapse" onClick={() => setOpen(false)}>
          ▾
        </button>
        {pipelineCase.design} — {caseStamp(pipelineCase)}
        <AxisLabel pipelineCase={pipelineCase} />
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
          <div className="metric-card"><span className="metric-card__label">search depth</span><strong className="metric-card__value">{pipelineCase.iterations.length}</strong><span className="metric-card__note">iterations · {candidates.length - deferred + screenedOnly} measured candidates{deferred > 0 ? ` · ${deferred} NOT EVALUATED` : ""}</span></div>
          <div className="metric-card metric-card--good"><span className="metric-card__label">passed</span><strong className="metric-card__value">{passed}</strong><span className="metric-card__note">verified candidates</span></div>
          <div className={`metric-card ${failed > 0 ? "metric-card--critical" : ""}`}><span className="metric-card__label">{unverified ? "not passed" : "rejected"}</span><strong className="metric-card__value">{failed}</strong><span className="metric-card__note">{unverified ? `${failed - unverified} rejected · ${unverified} never checked` : "failed or violated guardrails"}</span></div>
        </div>

        {/* Directly under the status metrics, not at the bottom of the
            card. This is the only part of a case that asks the operator
            to *do* something; it used to sit last, after an 8,700-character
            diagnosis, which is past the point anyone scrolls. */}
        <HumanInTheLoopPanel pipelineCase={pipelineCase} onApplied={onApplied} />

        <ProcessStages pipelineCase={pipelineCase} />
        <AgentRolesLegend />

        {candidates.map(c => c.verdict?.model_validity?.macro_arc_audit && (
          <MacroModelCoverage key={c.tag} tag={c.tag} audit={c.verdict.model_validity.macro_arc_audit} />
        ))}

        {pipelineCase.topology && <TopologySummary topology={pipelineCase.topology} />}

        <CaseLayoutImage pipelineCase={pipelineCase} />

        {chartData.length > 0 && (
          <div className="pipeline__chart">
            <div className="pipeline__chart-head">
              <div>
                <div className="tab__meta-label">candidate area comparison</div>
                <strong>What size did each attempt achieve?</strong>
              </div>
              <span>Measured cell area · lower is better only after signoff passes</span>
            </div>
            <CandidateAreaChart data={chartData} />
          </div>
        )}

        <ObjectivesChart
          candidates={winnerField}
          winnerTag={pipelineCase.winner_tag}
        />

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
            <div className="tab__meta-label">
              iteration {iter.iteration}
              {iter.polish ? " · polish — moves tried on the winner, kept only if signoff still passes and the result is cheaper" : ""}
            </div>
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
                    allCandidates={candidates}
                    caseFile={pipelineCase.file}
                    expanded={expandedTags.has(c.tag)}
                    onToggle={toggle}
                  />
                ))}
              </tbody>
            </table>
          </div>
        ))}

        {/* Collapsed. The diagnosis is reference material — real, kept,
            and worth reading when you need it — but sram_wrapper's runs
            to 8,700 characters, and left open it visually outweighed
            every actionable thing on the card. The summary line carries
            enough to decide whether to open it. */}
        {pipelineCase.diagnosis && (
          <details className="pipeline__diagnosis">
            <summary>
              <span className="tab__meta-label">diagnosis</span>
              <span className="pipeline__diagnosis-teaser">
                {pipelineCase.diagnosis.slice(0, 110)}…
                {" "}({pipelineCase.diagnosis.length.toLocaleString()} chars)
              </span>
            </summary>
            <p>{pipelineCase.diagnosis}</p>
            <TranslateBlock text={pipelineCase.diagnosis} />
          </details>
        )}
      </div>
    </div>
  );
}

// Memoized: props are stable across the poll tick (see DesignGroupSection).
export default memo(CaseCard);
