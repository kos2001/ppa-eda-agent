import { useEffect, useState } from "react";
import {
  fetchPipelineRunStatus,
  type PipelineRunState,
} from "../api/referenceDb";
import { useLang } from "../i18n";
import "./LiveRun.css";

// What the agent is doing right now, step by step.
//
// The console could start a real OpenLane run and then show a scrolling
// tail of its output. That answers "is it alive" and nothing else: a
// full flow is 78 steps over several minutes, and a run can involve nine
// candidates in sequence. From a tail you cannot tell which candidate is
// running, whether it is at floorplan or at signoff, or where the
// previous one died.
//
// The server supplies it by watching the run directory: OpenLane
// materialises one NN-tool-step/ directory per step as it reaches them.
// Its progress bar cannot be used — Rich checks isatty() and draws
// nothing when the output is a pipe, so a piped run emits exactly one
// "Stage" line, at the end. Measured both from the server's stream and
// from a plain shell redirect.
//
// `fetchPipelineRunStatus` had existed in the API module the whole time
// and no component ever called it.

const POLL_MS = 1500;

function Bar({ step, total, failed }: { step: number; total: number | null; failed: boolean }) {
  // On a design's first run the total is genuinely unknown — no earlier
  // run has been observed reaching the end. A 0%-wide bar reads as
  // "stuck", so show a moving indeterminate bar instead of a number
  // nobody measured.
  if (!total) {
    return (
      <div className="live__bar" role="progressbar" aria-valuenow={step}>
        <i className="live__bar-fill live__bar-fill--unknown" />
      </div>
    );
  }
  const pct = Math.min(100, (step / total) * 100);
  return (
    <div className="live__bar" role="progressbar" aria-valuenow={step} aria-valuemax={total}>
      <i className={failed ? "live__bar-fill live__bar-fill--bad" : "live__bar-fill"}
         style={{ width: `${pct}%` }} />
    </div>
  );
}

export default function LiveRun({
  design,
  onFinished,
}: {
  design: string | null;
  onFinished?: () => void;
}) {
  const { t } = useLang();
  const [state, setState] = useState<PipelineRunState | null>(null);

  useEffect(() => {
    if (!design) {
      setState(null);
      return;
    }
    let alive = true;
    let timer: number | undefined;

    const poll = async () => {
      try {
        const s = await fetchPipelineRunStatus(design);
        if (!alive) return;
        setState(s);
        if (s.status === "running") {
          timer = window.setTimeout(poll, POLL_MS);
        } else if (s.status === "done" || s.status === "error") {
          // One refresh of the case list when the run lands, not a
          // standing poll — the reference-db only changes at that point.
          onFinished?.();
        }
      } catch {
        // A failed poll is not a failed run; keep trying rather than
        // reporting a state we did not observe.
        if (alive) timer = window.setTimeout(poll, POLL_MS * 2);
      }
    };
    poll();
    return () => {
      alive = false;
      if (timer) window.clearTimeout(timer);
    };
  }, [design, onFinished]);

  if (!design || !state || state.status === "idle") return null;

  const progress = state.progress ?? [];
  const running = progress.filter((p) => p.status === "running").length;
  const failed = progress.filter((p) => p.status === "failed").length;
  const done = progress.filter((p) => p.status === "done").length;

  return (
    <section className="live">
      <header className="live__head">
        <span className="live__title">
          <i className={state.status === "running" ? "live__pulse" : "live__pulse live__pulse--off"} />
          {t("live_running").replace("{d}", design)}
        </span>
        <span className="live__counts">
          {done > 0 && <span>{done} {t("live_done")}</span>}
          {running > 0 && <span className="live__counts--now">{running} {t("live_now")}</span>}
          {failed > 0 && <span className="live__counts--bad">{failed} {t("live_failed")}</span>}
        </span>
      </header>

      {progress.length === 0 ? (
        // The orchestrator is up but has not announced a candidate yet.
        // Saying "starting" is different from showing an empty list.
        <p className="live__starting">{t("live_starting")}</p>
      ) : (
        <ul className="live__list">
          {progress.map((p) => (
            <li key={p.tag + p.startedAt} className={`live__row live__row--${p.status}`}>
              <div className="live__row-head">
                <code className="live__tag">{p.tag}</code>
                <span className="live__step">
                  {/* The step number is real even when the total is not
                      yet known; hiding it hid the only live number. */}
                  {p.step > 0
                    ? `${t("live_step")} ${p.step}${p.total ? `/${p.total}` : ""}`
                    : "—"}
                  {p.elapsed && <em>{p.elapsed}</em>}
                </span>
              </div>
              <Bar step={p.step} total={p.total} failed={p.status === "failed"} />
              <div className="live__row-foot">
                {/* The real step name OpenLane is on, not a guess at
                    which of our 8 pipeline stages it maps to. */}
                <span className="live__stepname">{p.stepName ?? t("live_starting")}</span>
                {p.status === "failed" && p.error && (
                  <span className="live__err" title={p.error}>{p.error}</span>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      {state.status === "error" && state.error && (
        <p className="tab__error">{state.error}</p>
      )}
    </section>
  );
}
