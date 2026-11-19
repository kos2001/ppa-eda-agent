import { useMemo, useState } from "react";
import type { PipelineCase, ProcessStageId } from "../api/referenceDb";
import { useLang } from "../i18n";
import StageArtifacts from "./StageArtifacts";
import {
  FALLBACK_PROCESS_STAGES,
  PIPELINE_PHASES,
  STAGE_AGENT,
  stageCount,
  summarizeStages,
} from "./pipelineModel";

// Agent legend — every subagent that touches this pipeline, in pipeline
// order, plus ppa-eda-analyst (not one of the 8 stages; it's the
// separate on-demand EDA report diagnosis agent behind the
// sidebar's "ppa-eda-analyst" tab). Deduplicates routing-candidate-
// evaluator (owns 2 stages) automatically via the Map below.
const LEGEND_ROWS = (() => {
  const seen = new Set<string>();
  return (Object.values(STAGE_AGENT) as { agent: string; role: { en: string; ko: string } }[])
    .filter((entry) => (seen.has(entry.agent) ? false : (seen.add(entry.agent), true)));
})();

export function AgentRolesLegend() {
  const { lang, t } = useLang();
  const rows = LEGEND_ROWS;

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

export function ProcessStages({ pipelineCase }: { pipelineCase: PipelineCase }) {
  const { t } = useLang();
  // Which stage's artifacts are open. The stage cards used to be labels
  // with a count — you could see a stage happened but not what came out
  // of it, though every stage's real output was already in the case.
  const [openStage, setOpenStage] = useState<ProcessStageId | null>(null);
  const stages = pipelineCase.process_stages ?? FALLBACK_PROCESS_STAGES;
  const summary = useMemo(() => summarizeStages(pipelineCase), [pipelineCase]);
  const { candidates, feedbackCount, gateFlow } = summary;

  const stageIndex = new Map(stages.map((s, i) => [s.id, i + 1]));
  const byId = new Map(stages.map((s) => [s.id, s]));

  return (
    <div className="pipeline__process" aria-label="8-step layout agent process">
      {PIPELINE_PHASES.map((phase) => (
        <section key={phase.id} className={`pipeline__phase pipeline__phase--${phase.id}`}>
          <header className="pipeline__phase-head">
            <span className="pipeline__phase-label">{t(phase.labelKey)}</span>
            <span className="pipeline__phase-role">{t(phase.roleKey)}</span>
          </header>
          <div className="pipeline__phase-stages">
            {phase.stages.map((id) => {
              const stage = byId.get(id);
              if (!stage) return null;
              const { count, note } = stageCount(id, pipelineCase, summary);
              const reached = count > 0;
              const owner = STAGE_AGENT[id];
              return (
                <button
                  key={id}
                  type="button"
                  onClick={() => setOpenStage(openStage === id ? null : id)}
                  className={`pipeline__process-stage ${reached ? "pipeline__process-stage--reached" : "pipeline__process-stage--empty"}`
                    + (openStage === id ? " pipeline__process-stage--open" : "")}
                  title={owner ? `${owner.agent} — ${owner.role.en}` : note}
                >
                  <span className="pipeline__process-stage-index">
                    {String(stageIndex.get(id) ?? 0).padStart(2, "0")}
                  </span>
                  <strong>{stage.name}</strong>
                  {owner && <span className="pipeline__process-stage-agent">{owner.agent}</span>}
                  <span className="pipeline__process-stage-note">{note}</span>
                  {/* The attrition itself, drawn. A gate is only
                      understandable relative to how many candidates
                      entered it — a bare fraction hides whether the loss
                      was one candidate or all of them. */}
                  {gateFlow.has(id) && candidates.length > 0 && (() => {
                    const f = gateFlow.get(id)!;
                    const pct = (n: number) => `${(n / candidates.length) * 100}%`;
                    return (
                      <span className="pipeline__flow" aria-hidden="true">
                        <i className="pipeline__flow-survived"
                           style={{ width: pct(f.entered - f.lost) }} />
                        <i className="pipeline__flow-lost"
                           style={{ width: pct(f.lost) }} />
                      </span>
                    );
                  })()}
                  <span className="pipeline__process-stage-more">
                    {openStage === id ? t("sa_hide") : t("sa_show")}
                  </span>
                </button>
              );
            })}
          </div>
        </section>
      ))}

      {/* The repair loop, drawn because it is this agent's defining
          behaviour and was previously invisible: stage 8 does not end the
          run, it feeds a new candidate set back into stage 3. Labelled
          with the real count from this case so it reads as something
          that happened, not a diagram decoration. */}
      <div
        className={`pipeline__loop ${feedbackCount > 0 ? "pipeline__loop--fired" : "pipeline__loop--idle"}`}
      >
        ↺ {feedbackCount > 0
            ? t("phase_loop_fired").replace("{n}", String(feedbackCount))
            : t("phase_loop_idle")}
      </div>

      {openStage && (
        <StageArtifacts
          stage={openStage}
          stageName={byId.get(openStage)?.name ?? openStage}
          pipelineCase={pipelineCase}
        />
      )}
    </div>
  );
}
