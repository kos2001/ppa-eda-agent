import { useCallback, useEffect, useState } from "react";
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
      <span className="sh__label">{label}</span>
      <span className="sh__value">{value}</span>
      {note && <span className="sh__note">{note}</span>}
    </li>
  );
}

export default function SystemHealth() {
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

  if (error) {
    return (
      <details className="sh">
        <summary className="sh__summary">{t("sh_title")}</summary>
        <p className="tab__error">{error}</p>
      </details>
    );
  }

  const learning = report?.learning_data;
  const retrieval = report?.retrieval;
  const short = Object.entries(learning?.needs_more_runs ?? {});

  return (
    <details className="sh">
      <summary className="sh__summary">
        {t("sh_title")}
        {busy && <span className="sh__busy"> · {t("sh_scanning")}</span>}
      </summary>

      {!report ? (
        <p className="sh__empty">{t("sh_scanning")}</p>
      ) : (
        <div className="sh__body">
          <p className="sh__lede">{t("sh_lede")}</p>

          <section className="sh__block">
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

          <section className="sh__block">
            <h4>{t("sh_retrieval")}</h4>
            <ul className="sh__list">
              <Row
                label={t("sh_corpus")}
                value={`${retrieval?.cases_with_failure_signature ?? 0} / ${retrieval?.cases ?? 0}`}
                note={t("sh_corpus_note")}
              />
              <Row
                label={t("sh_signatures")}
                value={String(retrieval?.distinct_signatures?.length ?? 0)}
                note={(retrieval?.distinct_signatures ?? []).join(", ")}
              />
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

          <section className="sh__block">
            <h4>{t("sh_learning")}</h4>
            <ul className="sh__list">
              <Row
                label={t("sh_configs")}
                value={String(learning?.distinct_configs ?? 0)}
                note={t("sh_configs_note").replace(
                  "{n}", String(learning?.evaluable_at ?? "?"))}
              />
              {learning?.model_mae != null && learning?.baseline_mae != null && (
                <Row
                  label={t("sh_mae")}
                  value={`${learning.model_mae.toFixed(2)} vs ${learning.baseline_mae.toFixed(2)}`}
                  note={t("sh_mae_note")}
                />
              )}
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
            {/* The verdict in full, because its wording carries the
                caveat: a small average edge is not a usable model. */}
            {learning?.verdict && (
              <p className="sh__verdict">{learning.verdict}</p>
            )}
          </section>

          <button className="sh__refresh" onClick={() => void load()} disabled={busy}>
            {busy ? t("sh_scanning") : t("sh_rescan")}
          </button>
        </div>
      )}
    </details>
  );
}
