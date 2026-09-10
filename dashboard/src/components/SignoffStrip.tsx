import type { SignoffCheck } from "../api/referenceDb";
import { useLang } from "../i18n";
import "./SignoffStrip.css";

// One cell per signoff check the verdict was built from, in the order
// score() reads them, so a reader sees all of them at once — clean,
// violated, or never run — instead of reconstructing the clean ones
// as "whatever is in neither list". The three states keep the
// verdict's own distinction: a nonzero count is a real tool rejection
// (red, the only red here); an absent count is a step that did not
// run (outlined, not filled — a decision nobody has made yet, per the
// colour language in ActionCenter.css); zero is clean.
//
// Cases written before signoff_checks was recorded have none, and the
// callers fall back to the text lists; this never invents a row.

type State = "clean" | "violated" | "never";

function stateOf(c: SignoffCheck): State {
  if (c.count == null) return "never";
  return c.count > 0 ? "violated" : "clean";
}

export default function SignoffStrip({
  checks,
  source,
  compact = false,
}: {
  checks: SignoffCheck[];
  source?: string;
  compact?: boolean;
}) {
  const { t } = useLang();
  const counts = { clean: 0, violated: 0, never: 0 };
  for (const c of checks) counts[stateOf(c)] += 1;
  const summary = `${counts.clean} ${t("signoff_clean")} · ${counts.violated} ${t("signoff_violated")} · ${counts.never} ${t("signoff_never")}`;

  return (
    <div className={`signoff ${compact ? "signoff--compact" : ""}`}>
      <div className="signoff__cells" role="img" aria-label={`${t("signoff_title")}: ${summary}`}>
        {checks.map((c) => {
          const state = stateOf(c);
          const detail =
            state === "never"
              ? t("verdict_never_ran")
              : state === "violated"
                ? `${c.count}`
                : "0";
          return (
            <span
              key={c.key}
              className={`signoff__cell signoff__cell--${state}`}
              title={`${c.label}: ${detail}`}
            />
          );
        })}
      </div>
      <div className="signoff__legend">
        <span className="signoff__count signoff__count--clean">{counts.clean} {t("signoff_clean")}</span>
        <span className={`signoff__count ${counts.violated > 0 ? "signoff__count--violated" : ""}`}>
          {counts.violated} {t("signoff_violated")}
        </span>
        <span className={`signoff__count ${counts.never > 0 ? "signoff__count--never" : ""}`}>
          {counts.never} {t("signoff_never")}
        </span>
        {source && !compact && <span className="signoff__source">{source}</span>}
      </div>
    </div>
  );
}
