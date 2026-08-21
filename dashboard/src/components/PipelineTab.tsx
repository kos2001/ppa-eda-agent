import { useEffect, useState } from "react";
import {
  fetchReferenceDb,
  type CandidateResult,
  type PipelineCase,
} from "../api/referenceDb";
import { useLang } from "../i18n";
import "./Tabs.css";
import "./PipelineTab.css";

function CandidateRow({ candidate }: { candidate: CandidateResult }) {
  if (candidate.error) {
    return (
      <tr>
        <td>{candidate.tag}</td>
        <td>
          <span className="pill pill--critical">FAIL TO RUN</span>
        </td>
        <td colSpan={3} className="pipeline__error-cell">
          {candidate.error}
        </td>
      </tr>
    );
  }
  const v = candidate.verdict;
  return (
    <tr>
      <td>{candidate.tag}</td>
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
    </tr>
  );
}

function CaseCard({ pipelineCase }: { pipelineCase: PipelineCase }) {
  return (
    <div className="panel">
      <span className="panel__title">
        {pipelineCase.design} — {pipelineCase.date}
      </span>
      <div className="panel__body">
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
                </tr>
              </thead>
              <tbody>
                {iter.results.map((c) => (
                  <CandidateRow key={c.tag} candidate={c} />
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
      </div>
    </div>
  );
}

export default function PipelineTab() {
  const { t } = useLang();
  const [cases, setCases] = useState<PipelineCase[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

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

  return (
    <div className="tab">
      <div className="panel">
        <span className="panel__title">{t("pipeline_panel_title")}</span>
        <div className="panel__body">
          <p>{t("pipeline_intro")}</p>
        </div>
      </div>

      {loading && <p>{t("pipeline_loading")}</p>}
      {error && (
        <p className="tab__error">
          {error} — {t("pipeline_error_hint")}
        </p>
      )}
      {!loading && !error && cases?.length === 0 && (
        <p>{t("pipeline_empty")}</p>
      )}

      {cases?.map((c) => (
        <CaseCard key={`${c.design}__${c.date}`} pipelineCase={c} />
      ))}
    </div>
  );
}
