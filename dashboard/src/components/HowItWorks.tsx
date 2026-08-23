import { useLang, type DictKey } from "../i18n";
import type { PipelineCase } from "../api/referenceDb";
import "./HowItWorks.css";

// Orientation panel: what this service actually does, before any data.
//
// Measured problem it fixes: opening the dashboard cold, the first prose
// on screen explained where the data came from ("each case below is a
// real orchestrator.py run") — the data *source*, never the *service*. A
// newcomer could not tell what goes in (RTL + targets), what comes out
// (a signed-off layout), or what the agent does in between. Everything
// else on the page is evidence for a process the page never stated.
//
// Deliberately grounded in the real numbers on screen rather than being
// abstract marketing: the counts below are computed from the same
// reference-db cases rendered underneath, so the explanation and the
// evidence can never drift apart.

export interface PipelineStats {
  designs: number;
  cases: number;
  candidateRuns: number;
  closed: number;
  repaired: number;
}

export function computeStats(cases: PipelineCase[]): PipelineStats {
  const results = cases.flatMap((c) => c.iterations.flatMap((it) => it.results));
  return {
    designs: new Set(cases.map((c) => c.design)).size,
    cases: cases.length,
    candidateRuns: results.length,
    closed: cases.filter((c) => c.winner_tag).length,
    repaired: results.filter((r) => r.produced_by_feedback).length,
  };
}

// The loop, as it really runs in orchestrator.orchestrate(). Step 4's
// three branches are that function's three STOP_REASONS — the same total
// guard the pipeline itself is built on, so this diagram is a view of
// real control flow, not an idealised marketing funnel.
const STEPS: { n: string; key: DictKey }[] = [
  { n: "1", key: "hiw_step_input" },
  { n: "2", key: "hiw_step_propose" },
  { n: "3", key: "hiw_step_run" },
  { n: "4", key: "hiw_step_score" },
];

export default function HowItWorks({ cases }: { cases: PipelineCase[] }) {
  const { t } = useLang();
  const s = computeStats(cases);

  return (
    <details className="hiw" open>
      <summary className="hiw__summary">{t("hiw_title")}</summary>
      <div className="hiw__body">
        <p className="hiw__lede">{t("hiw_lede")}</p>

        <ol className="hiw__flow">
          {STEPS.map((step) => (
            <li key={step.key} className="hiw__step">
              <span className="hiw__step-n">{step.n}</span>
              <span className="hiw__step-text">{t(step.key)}</span>
            </li>
          ))}
        </ol>

        <ul className="hiw__outcomes">
          <li className="hiw__outcome hiw__outcome--good">
            <strong>PASS</strong>
            <span>{t("hiw_outcome_pass")}</span>
          </li>
          <li className="hiw__outcome hiw__outcome--loop">
            <strong>FAIL ↺</strong>
            <span>{t("hiw_outcome_repair")}</span>
          </li>
          <li className="hiw__outcome hiw__outcome--human">
            <strong>FAIL ⚑</strong>
            <span>{t("hiw_outcome_escalate")}</span>
          </li>
        </ul>

        <p className="hiw__real">{t("hiw_real")}</p>

        <div className="hiw__stats">
          <span><strong>{s.designs}</strong>{t("hiw_stat_designs")}</span>
          <span><strong>{s.cases}</strong>{t("hiw_stat_cases")}</span>
          <span><strong>{s.candidateRuns}</strong>{t("hiw_stat_runs")}</span>
          <span><strong>{s.closed}</strong>{t("hiw_stat_closed")}</span>
          <span><strong>{s.repaired}</strong>{t("hiw_stat_repaired")}</span>
        </div>
      </div>
    </details>
  );
}
