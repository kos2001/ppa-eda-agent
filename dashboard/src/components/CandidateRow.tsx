import { memo, useEffect, useRef, useState } from "react";
import { fetchCandidateDetail, type CandidateResult } from "../api/referenceDb";
import { useLang } from "../i18n";
import SignoffStrip from "./SignoffStrip";
import { ViolationChips } from "./ClosureLedger";
import LayoutView from "./LayoutView";
import {
  DataPointers,
  PowerSummary,
  PredictionLine,
  QualityLine,
  TimingCorners,
} from "./CandidateDetail";
import { STAGE_SHORT_LABEL, failureRecovery, verdictPill } from "./pipelineModel";

function CandidateRowImpl({
  candidate,
  allCandidates,
  caseFile,
  expanded,
  onToggle,
}: {
  candidate: CandidateResult;
  allCandidates: CandidateResult[];
  caseFile: string | null | undefined;
  expanded: boolean;
  onToggle: (tag: string) => void;
}) {
  const { t } = useLang();
  // The layout is fetched when the row is opened, not with the list:
  // it is 183 MB across the store and read only here. Held per row so
  // closing and reopening does not fetch it twice.
  const [layout, setLayout] = useState(candidate.layout ?? null);
  const [layoutState, setLayoutState] = useState<"idle" | "loading" | "error">("idle");
  // A ref, not the state, guards the request: putting the state in the
  // effect's dependencies made the effect re-run on its own
  // "loading" transition and its cleanup cancel the response.
  const layoutRequested = useRef(false);
  useEffect(() => {
    if (!expanded || layout || !candidate.layout_deferred || !caseFile || layoutRequested.current) return;
    layoutRequested.current = true;
    setLayoutState("loading");
    fetchCandidateDetail(caseFile, candidate.tag)
      .then((d) => { setLayout(d.layout); setLayoutState(d.layout ? "idle" : "error"); })
      .catch(() => setLayoutState("error"));
  }, [expanded, layout, candidate.layout_deferred, candidate.tag, caseFile]);
  const stageBadge = candidate.stage && (
    <span className="pipeline__stage-badge" title={STAGE_SHORT_LABEL[candidate.stage].full}>
      {STAGE_SHORT_LABEL[candidate.stage].short}
    </span>
  );
  const provenanceBadge = candidate.polish ? (
    <span className="pipeline__stage-badge pipeline__stage-badge--polish"
          title={`polish move: ${candidate.polish.why}`}>
      ✦ {candidate.polish.move}
    </span>
  ) : candidate.repair ? (
    <span className="pipeline__stage-badge pipeline__stage-badge--feedback"
          title={candidate.repair.why}>
      ↺ {candidate.repair.code}
    </span>
  ) : null;
  const feedbackBadge = !candidate.repair && !candidate.polish && candidate.produced_by_feedback && (
    <span className="pipeline__stage-badge pipeline__stage-badge--feedback" title="produced by AI feedback/repair from a prior iteration's failure">
      ↺ repaired
    </span>
  );

  if (candidate.not_evaluated) {
    return <tr><td>{candidate.tag}</td><td><span className="pill">NOT EVALUATED</span></td>
      <td colSpan={3}>{candidate.budget_exhausted} · {t("evaluation_not_run")}
        {candidate.screen_evaluation && <span> · screen {candidate.screen_evaluation.status} · {candidate.screen_evaluation.seconds.toFixed(2)} s</span>}
      </td></tr>;
  }
  if (candidate.error) {
    const recovery = failureRecovery(candidate, allCandidates);
    return (
      <tr>
        <td>{candidate.tag}</td>
        <td>
          <span className="pill pill--critical">FAIL TO RUN</span>
        </td>
        <td colSpan={3} className="pipeline__error-cell">
          {recovery ? (
            <>
              <div className={`pipeline__recovery pipeline__recovery--${recovery.kind}`}>
                <strong>{recovery.label}</strong>
                <span>{recovery.detail}</span>
              </div>
              <details className="pipeline__raw-error">
                <summary>original OpenLane error</summary>
                <pre>{candidate.error}</pre>
              </details>
            </>
          ) : candidate.error}
        </td>
        <td>
          {stageBadge}
          {provenanceBadge}
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
        onClick={candidate.data ? () => onToggle(candidate.tag) : undefined}
      >
        <td>
          {candidate.data && <span className="pipeline__disclosure">{expanded ? "▾" : "▸"}</span>}
          {candidate.tag}
        </td>
        <td>
          {/* Three states, not two. A candidate blocked only because a
              signoff step never ran is not the same as one the tools
              rejected, and calling both FAIL sends a reader looking for
              a design bug that isn't there. */}
          <span className={`pill ${verdictPill(v).cls}`}>{verdictPill(v).text}</span>
        </td>
        <td>{v?.area_um2 != null ? `${v.area_um2} µm²` : "—"}</td>
        <td>{v?.utilization != null ? v.utilization.toFixed(3) : "—"}</td>
        <td>
          {v && !v.passed && v.violations.length > 0
            ? <ViolationChips violations={v.violations} />
            : v && (v.unverified?.length ?? 0) > 0
              ? v.signoff_checks && v.signoff_checks.length > 0
                ? <SignoffStrip checks={v.signoff_checks} compact />
                : `${t("verdict_never_ran")}: ${v.unverified!.join("; ")}`
              : v?.worst_setup_slack != null
                ? `slack ${v.worst_setup_slack.toFixed(2)} ns`
                : v?.worst_setup_wns != null
                  ? `WNS ${v.worst_setup_wns}`
                  : "—"}
        </td>
        <td>
          {stageBadge}
          {provenanceBadge}
          {feedbackBadge}
        </td>
      </tr>
      {expanded && candidate.data && (
        <tr className="pipeline__detail-row">
          <td colSpan={6}>
            {layout && (
              <>
                <span className="tab__meta-label">placement &amp; routing — real DEF output</span>
                <LayoutView layout={layout} />
              </>
            )}
            {!layout && layoutState === "loading" && (
              <span className="tab__meta-label">{t("candidate_layout_loading")}</span>
            )}
            {!layout && layoutState === "error" && (
              <span className="tab__meta-label">{t("candidate_layout_error")}</span>
            )}
            {v && <QualityLine verdict={v} />}
            {candidate.repair && (
              <p className="pipeline__provenance">
                <span className="tab__meta-label">why this candidate exists</span>
                repair for {candidate.repair.code}: {candidate.repair.why}
              </p>
            )}
            {candidate.polish && (
              <p className="pipeline__provenance">
                <span className="tab__meta-label">why this candidate exists</span>
                polish move {candidate.polish.move}: {candidate.polish.why}
              </p>
            )}
            {v && <TimingCorners corners={v.timing_corners} />}
            {v && <PowerSummary verdict={v} />}
            <PredictionLine prediction={candidate.prediction} />
            <DataPointers data={candidate.data} />
          </td>
        </tr>
      )}
    </>
  );
}

// Memoized: opening one row used to re-render every row of every iteration
// table. Props are now stable (the parent passes one useCallback toggle and a
// memoized candidate list), so only the toggled row updates.
export default memo(CandidateRowImpl);
