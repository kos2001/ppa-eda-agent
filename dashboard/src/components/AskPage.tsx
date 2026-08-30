import { useCallback, useRef, useState } from "react";
import {
  askSources,
  askViaServer,
  type AskSource,
} from "../api/gateway";
import { useLang, type DictKey } from "../i18n";
import MarkdownDoc from "./Markdown";
import "./AskPage.css";

// Free-form questions about this service.
//
// The console could answer exactly one kind of question — paste a
// report, get a diagnosis — and "what is this for?", "why gf180 as well
// as sky130?", "what has counter4 actually passed?" had no surface at
// all. All three are answerable from what the repo already holds.
//
// RETRIEVE FIRST, THEN WRITE. Sources are fetched and shown before any
// model is called, and they stay on screen next to the answer. Two
// reasons, and the second is the important one:
//
//   - With no hermes-gateway key there is still an answer of a kind:
//     the passages themselves. A chat that went blank without a key
//     would be useless on exactly the checkout that has not set one up.
//
//   - An answer you cannot check is worth less than one you can. The
//     sources are not a footnote to be collapsed; they are shown at the
//     same size as the prose, because the prose is the part that can be
//     wrong.
//
// When retrieval finds nothing, the model is never called. Handing it a
// question with no grounding is asking it to invent, which is the
// failure this whole path exists to prevent.

type Phase = "idle" | "searching" | "answering" | "done";

/** Where an answer is, and how each step it has already passed turned out.
 *
 * A spinner tells the reader to wait. This tells them what is being
 * waited on, and — because retrieval finishes long before the prose —
 * which half of the answer is already available. The steps are the two
 * real ones: the repo is searched, then a model writes from what was
 * found. That is the design, so it is what the progress shows.
 */
type StepState = "running" | "ok" | "empty" | "skipped" | "failed";

interface Step {
  state: StepState;
  /** Wall time for the step, so a slow one is visibly slow. */
  ms: number | null;
}

interface Turn {
  question: string;
  sources: AskSource[];
  facts: string | null;
  answer: string;
  /** Retrieval ran and found nothing — not an error, an honest miss. */
  empty: boolean;
  /** Sources found, but no model could write them up. */
  modelError: string | null;
  search: Step;
  write: Step;
}

function StepRow({
  step,
  label,
  detail,
}: {
  step: Step;
  label: string;
  detail: string | null;
}) {
  const mark = {
    running: "◌", ok: "✓", empty: "○", skipped: "—", failed: "×",
  }[step.state];
  return (
    <li className={`ask__step ask__step--${step.state}`}>
      <span className="ask__step-mark">{mark}</span>
      <span className="ask__step-label">{label}</span>
      {detail && <span className="ask__step-detail">{detail}</span>}
      {step.ms != null && (
        <span className="ask__step-ms">{(step.ms / 1000).toFixed(1)}s</span>
      )}
    </li>
  );
}

function Progress({ turn }: { turn: Turn }) {
  const { t } = useLang();
  const searchDetail =
    turn.search.state === "running"
      ? null
      : turn.sources.length > 0
        ? t("ask_step_found").replace("{n}", String(turn.sources.length))
        : t("ask_step_none");
  const writeDetail =
    turn.write.state === "skipped" ? t("ask_step_skipped")
      : turn.write.state === "failed" ? t("ask_step_failed")
        : turn.write.state === "ok" ? t("ask_step_done")
          : null;

  return (
    <ul className="ask__steps">
      <StepRow step={turn.search} label={t("ask_step_search")} detail={searchDetail} />
      <StepRow step={turn.write} label={t("ask_step_write")} detail={writeDetail} />
    </ul>
  );
}

const EXAMPLES: DictKey[] = ["ask_ex1", "ask_ex2", "ask_ex3", "ask_ex4"];

function SourceCard({ source, best }: { source: AskSource; best: number }) {
  const { t } = useLang();
  const [open, setOpen] = useState(false);
  const isModule = source.source.endsWith(".py");
  // Relative to the best hit in this answer, not to an absolute scale.
  // The score has no natural ceiling — a title match adds a flat 0.35 —
  // so drawing it against a made-up maximum would be decoration. Against
  // the top hit it says the one true thing a reader needs: how much
  // weaker than the best match this one is.
  const width = Math.max(6, Math.round((source.score / (best || 1)) * 100));

  return (
    <li className="ask__source">
      <button className="ask__source-head" onClick={() => setOpen((v) => !v)}>
        <span className="ask__source-name">
          {open ? "▾" : "▸"} {source.source}
        </span>
        <span className="ask__source-kind">
          {isModule ? t("ask_kind_module") : t("ask_kind_doc")}
        </span>
        <span className="ask__source-title">{source.title}</span>
        <span
          className="ask__score"
          title={`${t("ask_relevance")} ${source.score.toFixed(2)}`}
        >
          <span className="ask__score-bar" style={{ width: `${width}%` }} />
        </span>
      </button>
      {/* Why this passage was returned, shown whether or not it is
          expanded. When a wrong source comes back these say why, which
          turns "the search is bad" into a term list someone can fix. */}
      <p className="ask__matched">
        {t("ask_matched")}:{" "}
        {source.matched.map((term) => (
          <span className="ask__term" key={term}>{term}</span>
        ))}
      </p>
      {open && <pre className="ask__excerpt">{source.excerpt}</pre>}
    </li>
  );
}

function TurnView({ turn }: { turn: Turn }) {
  const { t } = useLang();
  return (
    <article className="ask__turn">
      <p className="ask__question">{turn.question}</p>
      <Progress turn={turn} />

      {turn.empty ? (
        <p className="ask__nothing">{t("ask_nothing")}</p>
      ) : (
        <>
          {turn.facts && (
            <div className="ask__facts">
              <span className="ask__facts-label">{t("ask_facts")}</span>
              {/* Already markdown — service_qa.py writes a sentence and a
                  "- design: ..." list — so it goes through the same
                  parser rather than a <pre> that showed the hyphens. */}
              <MarkdownDoc source={turn.facts} className="ask__facts-body" railFrom={99} />
            </div>
          )}
          {turn.answer && (
            // The model writes markdown — headings, numbered steps,
            // backticked file paths and metric keys — and this rendered
            // it into a <pre>, so a reader met "**두 가지**" and
            // "`report_area`" as literal asterisks and backticks. The
            // console already has a parser for exactly this text, used
            // for review requests and covered by test_markdown_render.py;
            // there was no reason for a second, worse one.
            //
            // railFrom is out of reach on purpose: the contents rail
            // earns its width in a 10,000-character review document and
            // not in a chat answer, where it would put a table of
            // contents beside three paragraphs.
            <MarkdownDoc source={turn.answer} className="ask__answer" railFrom={99} />
          )}
          {turn.modelError && !turn.answer && (
            <p className="ask__no-model">{t("ask_no_model")}</p>
          )}
          {turn.sources.length > 0 && (
            <>
              <span className="ask__sources-label">{t("ask_sources")}</span>
              <ul className="ask__sources">
                {turn.sources.map((s) => (
                  <SourceCard
                    key={`${s.source}::${s.title}`}
                    source={s}
                    best={turn.sources[0]?.score ?? 1}
                  />
                ))}
              </ul>
            </>
          )}
        </>
      )}
    </article>
  );
}

export default function AskPage() {
  const { t } = useLang();
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [phase, setPhase] = useState<Phase>("idle");
  const endRef = useRef<HTMLDivElement | null>(null);

  const ask = useCallback(async (text: string) => {
    const asked = text.trim();
    if (!asked || phase === "searching" || phase === "answering") return;
    setQuestion("");
    setPhase("searching");

    const startedAt = Date.now();
    const turn: Turn = {
      question: asked, sources: [], facts: null,
      answer: "", empty: false, modelError: null,
      search: { state: "running", ms: null },
      write: { state: "running", ms: null },
    };
    setTurns((prev) => [...prev, turn]);
    const index = turns.length;
    const patch = (fields: Partial<Turn>) =>
      setTurns((prev) => prev.map((row, i) =>
        i === index ? { ...row, ...fields } : row));

    let grounding;
    try {
      grounding = await askSources(asked);
    } catch (e) {
      patch({
        modelError: String(e),
        search: { state: "failed", ms: Date.now() - startedAt },
        write: { state: "skipped", ms: null },
      });
      setPhase("done");
      return;
    }

    const searchedAt = Date.now();
    patch({
      sources: grounding.sources,
      facts: grounding.facts,
      search: {
        state: grounding.sources.length || grounding.facts ? "ok" : "empty",
        ms: searchedAt - startedAt,
      },
    });
    endRef.current?.scrollIntoView({ behavior: "smooth" });

    // Nothing was retrieved. The model is not called: there is nothing
    // for it to be grounded in, and asking anyway is asking it to make
    // something up.
    if (grounding.sources.length === 0 && !grounding.facts) {
      patch({ empty: true, write: { state: "skipped", ms: null } });
      setPhase("done");
      return;
    }

    setPhase("answering");
    await askViaServer(asked, {
      onToken: (delta) =>
        setTurns((prev) => prev.map((row, i) =>
          i === index ? { ...row, answer: row.answer + delta } : row)),
      onDone: () => {
        patch({ write: { state: "ok", ms: Date.now() - searchedAt } });
        setPhase("done");
      },
      // Not a failure of the page. The sources are already on screen and
      // remain the answer; only the prose is missing.
      onError: (err) => {
        patch({
          modelError: String(err),
          write: { state: "failed", ms: Date.now() - searchedAt },
        });
        setPhase("done");
      },
    });
  }, [phase, turns.length]);

  const busy = phase === "searching" || phase === "answering";

  return (
    <div className="tab ask">
      <p className="ask__intro">{t("ask_intro")}</p>

      {turns.length === 0 && (
        <div className="ask__examples">
          <span className="ask__examples-label">{t("ask_examples")}</span>
          {EXAMPLES.map((key) => (
            <button key={key} className="ask__example" onClick={() => ask(t(key))}>
              {t(key)}
            </button>
          ))}
        </div>
      )}

      {turns.map((turn, i) => <TurnView key={i} turn={turn} />)}
      <div ref={endRef} />

      <form
        className="ask__form"
        onSubmit={(e) => { e.preventDefault(); ask(question); }}
      >
        <input
          className="ask__input"
          value={question}
          placeholder={t("ask_placeholder")}
          onChange={(e) => setQuestion(e.target.value)}
          disabled={busy}
        />
        <button className="ask__send" type="submit" disabled={busy || !question.trim()}>
          {busy ? t("ask_asking") : t("ask_send")}
        </button>
      </form>
    </div>
  );
}
