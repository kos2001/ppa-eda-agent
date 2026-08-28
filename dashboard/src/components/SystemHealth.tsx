import { useCallback, useEffect, useState, type ReactNode } from "react";
import { fetchSelfImprove, type SelfImproveReport } from "../api/referenceDb";
import { useLang } from "../i18n";
import "./SystemHealth.css";

// The state of the system that produces the cases, rather than the cases.
//
// Everything else in this console answers "what happened in this run".
// Nothing answered "where should this be improved next" — even though
// self_improve.py computes exactly that and has always thrown it away as
// terminal output. Three things it knows and the console did not show:
//
//   the loop      which designs are stuck for a reason a machine can act
//                 on (just more budget) versus one needing judgment
//   retrieval     whether the case store can still find precedent, which
//                 degrades quietly — a corpus where cases share no
//                 failure signature answers "this appears to be new"
//                 every time and nobody notices
//   learning data whether enough distinct configurations exist to
//                 evaluate a surrogate at all, and what the current
//                 verdict is against a predict-the-mean baseline
//
// Deliberately not a scoreboard. Every number here is a lever with a
// stated next action, because a health panel that only reports is one
// more thing to read.

// A metric with its number given the weight the number deserves. As a
// collapsed block every element was 10-11px and the value was the same
// size as its label, so nothing could be read at a glance — which is
// the only thing a health page is for.
function Row({
  label,
  value,
  tone = "",
  note,
}: {
  label: string;
  value: string;
  tone?: string;
  note?: string;
}) {
  return (
    <li className={`sh__row ${tone ? `sh__row--${tone}` : ""}`}>
      <div className="sh__row-head">
        <span className="sh__label">{label}</span>
        <span className="sh__value">{value}</span>
      </div>
      {note && <span className="sh__note">{note}</span>}
    </li>
  );
}

// One line at the top answering "is there anything for me right now",
// so the page can be closed without reading it when the answer is no.
function Headline({ report }: { report: SelfImproveReport }) {
  const { t } = useLang();
  const act = report.budget_retries.length;
  const judge = report.pattern_promotion_candidates.length;
  const warn = report.ungrounded_reviews.length;
  const total = act + judge + warn;

  const parts: string[] = [];
  if (act) parts.push(t("sh_hl_act").replace("{n}", String(act)));
  if (judge) parts.push(t("sh_hl_judge").replace("{n}", String(judge)));
  if (warn) parts.push(t("sh_hl_warn").replace("{n}", String(warn)));

  return (
    <div className={`sh__headline ${total ? "sh__headline--work" : "sh__headline--clear"}`}>
      <span className="sh__headline-count">{total}</span>
      <span className="sh__headline-text">
        {total ? parts.join(" · ") : t("sh_hl_clear")}
      </span>
    </div>
  );
}

export default function SystemHealth({
  standalone = false,
}: {
  // As its own page the collapse is wrong — you navigated here to read
  // it. The prop exists because the component was first a foldable block
  // inside the pipeline page, and both framings are still reachable.
  standalone?: boolean;
} = {}) {
  const { t } = useLang();
  const [report, setReport] = useState<SelfImproveReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      setReport(await fetchSelfImprove());
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // A plain function, not a component defined during render: React
  // treats a freshly-created component type as a different type each
  // pass and remounts its children, so the panel would flash on every
  // busy-state change.
  const shell = (children: ReactNode) =>
    standalone ? (
      <section className="sh sh--page">
        <h2 className="sh__page-title">{t("sh_title")}</h2>
        {children}
      </section>
    ) : (
      <details className="sh">
        <summary className="sh__summary">
          {t("sh_title")}
          {busy && <span className="sh__busy"> · {t("sh_scanning")}</span>}
        </summary>
        {children}
      </details>
    );

  if (error) {
    return shell(<p className="tab__error">{error}</p>);
  }

  const learning = report?.learning_data;
  const retrieval = report?.retrieval;
  const short = Object.entries(learning?.needs_more_runs ?? {});

  return shell(
    <>
      {!report ? (
        <p className="sh__empty">{t("sh_scanning")}</p>
      ) : (
        <div className="sh__body">
          <Headline report={report} />
          <p className="sh__lede">{t("sh_lede")}</p>
          <div className="sh__cards">

          <section className="sh__block sh__block--loop">
            <h4>{t("sh_loop")}</h4>
            <ul className="sh__list">
              <Row
                label={t("sh_budget")}
                value={String(report.budget_retries.length)}
                tone={report.budget_retries.length ? "act" : ""}
                note={report.budget_retries.length
                  ? `${report.budget_retries.map((b) => b.design).join(", ")} — ${t("sh_budget_note")}`
                  : t("sh_none_pending")}
              />
              <Row
                label={t("sh_promotion")}
                value={String(report.pattern_promotion_candidates.length)}
                tone={report.pattern_promotion_candidates.length ? "judge" : ""}
                note={report.pattern_promotion_candidates.length
                  ? `${report.pattern_promotion_candidates.join(", ")} — ${t("sh_promotion_note")}`
                  : t("sh_none_pending")}
              />
              {/* A review citing something absent from its own case is
                  not a wrong conclusion — it is a reference nobody can
                  check, which is a different and fixable problem. */}
              <Row
                label={t("sh_ungrounded")}
                value={String(report.ungrounded_reviews.length)}
                tone={report.ungrounded_reviews.length ? "warn" : ""}
                note={report.ungrounded_reviews.length
                  ? report.ungrounded_reviews
                      .map((u) => `${u.design}/${u.agent}: ${u.ungrounded.join(", ")}`)
                      .join(" · ")
                  : t("sh_grounded_ok")}
              />
            </ul>
          </section>

          <section className="sh__block sh__block--rag">
            <h4>{t("sh_retrieval")}</h4>
            <ul className="sh__list">
              <Row
                label={t("sh_corpus")}
                value={`${retrieval?.cases_with_failure_signature ?? 0} / ${retrieval?.cases ?? 0}`}
                note={t("sh_corpus_note")}
              />
              <li className="sh__row">
                <div className="sh__row-head">
                  <span className="sh__label">{t("sh_signatures")}</span>
                  <span className="sh__value">
                    {retrieval?.distinct_signatures?.length ?? 0}
                  </span>
                </div>
                {/* Twelve error codes set as a paragraph read as a wall.
                    They are discrete identifiers — one chip each lets
                    the eye pick out the one it is looking for. */}
                <ul className="sh__chips">
                  {(retrieval?.distinct_signatures ?? []).map((sig) => (
                    <li key={sig}>{sig}</li>
                  ))}
                </ul>
              </li>
              <Row
                label={t("sh_no_precedent")}
                value={String(retrieval?.cases_without_precedent?.length ?? 0)}
                tone={(retrieval?.cases_without_precedent?.length ?? 0) ? "warn" : ""}
                note={(retrieval?.cases_without_precedent ?? [])
                  .map((c) => `${c.design} ${c.date}`).join(", ")
                  || t("sh_precedent_ok")}
              />
            </ul>
          </section>

          <section className="sh__block sh__block--learn">
            <h4>{t("sh_learning")}</h4>
            <ul className="sh__list">
              <Row
                label={t("sh_configs")}
                value={String(learning?.distinct_configs ?? 0)}
                note={t("sh_configs_note").replace(
                  "{n}", String(learning?.evaluable_at ?? "?"))}
              />
              {/* Two targets, because they need different amounts of
                  data and answer different questions. Predicting area
                  needs a run that reached signoff; predicting whether it
                  gets there uses every configuration attempted, which is
                  why it has more samples. */}
              {(learning?.targets ?? []).map((tgt) => (
                <Row
                  key={tgt.field}
                  label={t(tgt.field === "completed" ? "sh_t_completed" : "sh_t_area")}
                  value={
                    tgt.accuracy != null && tgt.baseline_accuracy != null
                      ? `${Math.round(tgt.accuracy * 100)}% vs ${Math.round(tgt.baseline_accuracy * 100)}%`
                      : tgt.model_mae != null && tgt.baseline_mae != null
                        ? `${tgt.model_mae.toFixed(2)} vs ${tgt.baseline_mae.toFixed(2)}`
                        : "—"
                  }
                  note={
                    // k is re-derived from the data every scan, so a
                    // default left behind by a smaller dataset shows up
                    // here rather than silently costing accuracy.
                    `${tgt.n_scored}/${tgt.n_total} ${t("sh_t_scored")}` +
                    (tgt.best_k != null && tgt.current_k != null
                      ? ` · k=${tgt.current_k}` +
                        (tgt.best_k !== tgt.current_k
                          ? ` ${t("sh_k_drift").replace("{n}", String(tgt.best_k))}`
                          : ` ${t("sh_k_ok")}`)
                      : "") +
                    ` · ${tgt.verdict}`
                  }
                />
              ))}
              {short.length > 0 && (
                <Row
                  label={t("sh_short")}
                  value={String(short.length)}
                  tone="act"
                  note={short.map(([d, n]) => `${d}: ${n}`).join(", ")
                    + ` — ${t("sh_short_note")}`}
                />
              )}
            </ul>
            {/* Kept only when no target could be evaluated at all —
                otherwise each target carries its own verdict above and
                repeating one of them here would imply it covered both. */}
            {(learning?.targets ?? []).every((x) => x.n_scored === 0) &&
              learning?.verdict && (
                <p className="sh__verdict">{learning.verdict}</p>
              )}
          </section>

          </div>

          <button className="sh__refresh" onClick={() => void load()} disabled={busy}>
            {busy ? t("sh_scanning") : t("sh_rescan")}
          </button>
        </div>
      )}
    </>
  );
}
