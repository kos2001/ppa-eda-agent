import { useState } from "react";
import {
  triggerPipelineRun,
  type PipelineCase,
} from "../api/referenceDb";
import { useLang, type DictKey } from "../i18n";
import LiveRun from "./LiveRun";
import "./ActionCenter.css";

// What the agent needs from a human, in one place.
//
// The problem this fixes: the console was organised by *case* — a
// historical record — so "what needs me right now" was scattered across
// N collapsed cards and could only be found by opening each one. The
// live strip could say "1 case waiting" but not what to do or where.
//
// The intervention points are not invented for the UI; they are exactly
// orchestrate()'s three STOP_REASONS plus "never run", and each maps to
// one concrete action:
//
//   never run                  -> run the agent
//   winner_found               -> nothing; the agent finished
//   max_iterations_reached     -> the loop was still making progress and
//                                 ran out of budget. A human decision,
//                                 but not a judgement call: give it more.
//   no_repairable_failures     -> the agent has no pattern for this
//                                 failure. This is the one that genuinely
//                                 needs human judgement, and it routes to
//                                 the review workflow inside the case.
//
// Designs are ordered by how much they need attention, so the top of the
// list is always the next thing to do.

export type ActionKind = "review" | "budget" | "run" | "done";

export interface DesignAction {
  design: string;
  kind: ActionKind;
  latest: PipelineCase | null;
  iterationsRun: number;
  reviewed: boolean;
}

const ORDER: Record<ActionKind, number> = { review: 0, budget: 1, run: 2, done: 3 };

const COPY: Record<ActionKind, { state: DictKey; ask: DictKey }> = {
  review: { state: "ac_state_review", ask: "ac_ask_review" },
  budget: { state: "ac_state_budget", ask: "ac_ask_budget" },
  run: { state: "ac_state_run", ask: "ac_ask_run" },
  done: { state: "ac_state_done", ask: "ac_ask_done" },
};

export function deriveActions(
  designs: string[],
  cases: PipelineCase[]
): DesignAction[] {
  const byDesign = new Map<string, PipelineCase[]>();
  for (const c of cases) {
    const list = byDesign.get(c.design) ?? [];
    list.push(c);
    byDesign.set(c.design, list);
  }
  const all = new Set([...designs, ...byDesign.keys()]);

  const actions: DesignAction[] = [...all].map((design) => {
    const list = (byDesign.get(design) ?? [])
      .slice()
      .sort((a, b) => b.date.localeCompare(a.date));
    const latest = list[0] ?? null;
    const reviewed = Boolean(latest?.human_in_the_loop?.length);

    let kind: ActionKind;
    if (!latest) kind = "run";
    else if (latest.winner_tag) kind = "done";
    else if (latest.stop_reason === "max_iterations_reached") kind = "budget";
    else kind = "review";

    return {
      design,
      kind,
      latest,
      iterationsRun: latest?.iterations.length ?? 0,
      reviewed,
    };
  });

  return actions.sort(
    (a, b) => ORDER[a.kind] - ORDER[b.kind] || a.design.localeCompare(b.design)
  );
}

function ActionRow({
  action,
  onRun,
  onOpenCase,
}: {
  action: DesignAction;
  onRun: (design: string, maxIterations?: number) => void;
  onOpenCase: (design: string) => void;
}) {
  const { t } = useLang();
  const copy = COPY[action.kind];
  // Double the budget that already proved insufficient — the same rule
  // self_improve.py applies, kept consistent so the console and the CLI
  // suggest the same thing.
  const nextBudget = Math.max(action.iterationsRun, 1) * 2;

  return (
    <li className={`ac__row ac__row--${action.kind}`}>
      <div className="ac__what">
        <span className="ac__design">{action.design}</span>
        <span className="ac__state">{t(copy.state)}</span>
      </div>
      <p className="ac__ask">
        {t(copy.ask).replace("{n}", String(nextBudget))}
        {action.kind === "review" && action.reviewed && ` ${t("ac_already_reviewed")}`}
      </p>
      <div className="ac__do">
        {action.kind === "review" && (
          <button onClick={() => onOpenCase(action.design)}>
            {t("ac_btn_review")}
          </button>
        )}
        {action.kind === "budget" && (
          <button onClick={() => onRun(action.design, nextBudget)}>
            {t("ac_btn_budget").replace("{n}", String(nextBudget))}
          </button>
        )}
        {action.kind === "run" && (
          <button onClick={() => onRun(action.design)}>{t("ac_btn_run")}</button>
        )}
        {action.kind === "done" && (
          <>
            <span className="ac__winner">{action.latest?.winner_tag}</span>
            <button className="ac__secondary" onClick={() => onRun(action.design)}>
              {t("ac_btn_rerun")}
            </button>
          </>
        )}
      </div>
    </li>
  );
}

export default function ActionCenter({
  designs,
  cases,
  lastRefresh,
  onOpenCase,
  onRunStarted,
}: {
  designs: string[];
  cases: PipelineCase[];
  lastRefresh: Date | null;
  onOpenCase: (design: string) => void;
  onRunStarted: () => void;
}) {
  const { t } = useLang();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  // The design whose run to follow. Set when we start one here, so the
  // step-by-step view appears exactly where the button was pressed.
  const [watching, setWatching] = useState<string | null>(null);

  const actions = deriveActions(designs, cases);
  const needing = actions.filter((a) => a.kind !== "done").length;

  async function handleRun(design: string, maxIterations?: number) {
    setBusy(design);
    setError(null);
    try {
      await triggerPipelineRun(design, maxIterations);
      setWatching(design);
      onRunStarted();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="ac">
      <header className="ac__head">
        <h2>{t("ac_title")}</h2>
        <span className="ac__meta">
          <span className={needing ? "ac__count ac__count--needing" : "ac__count"}>
            {needing
              ? t("ac_needing").replace("{n}", String(needing))
              : t("ac_all_clear")}
          </span>
          {/* Liveness lives here, not in a separate strip below: a second
              band repeating this same count was one of three places the
              page said the same thing. */}
          <span className="ac__live">
            <i className="ac__dot" />
            {lastRefresh
              ? t("live_refreshed").replace("{t}", lastRefresh.toLocaleTimeString())
              : "—"}
          </span>
        </span>
      </header>
      <ul className="ac__list">
        {actions.map((a) => (
          <ActionRow
            key={a.design}
            action={a}
            onRun={handleRun}
            onOpenCase={onOpenCase}
          />
        ))}
      </ul>
      {busy && <p className="ac__busy">{t("ac_starting").replace("{d}", busy)}</p>}
      <LiveRun design={watching} onFinished={onRunStarted} />
      {error && <p className="tab__error">{error}</p>}
    </section>
  );
}
