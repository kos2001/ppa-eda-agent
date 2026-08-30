import { useCallback, useRef, useState } from "react";
import {
  askSources,
  askViaServer,
  type AskSource,
} from "../api/gateway";
import { useLang, type DictKey } from "../i18n";
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

interface Turn {
  question: string;
  sources: AskSource[];
  facts: string | null;
  answer: string;
  /** Retrieval ran and found nothing — not an error, an honest miss. */
  empty: boolean;
  /** Sources found, but no model could write them up. */
  modelError: string | null;
}

const EXAMPLES: DictKey[] = ["ask_ex1", "ask_ex2", "ask_ex3", "ask_ex4"];

function SourceCard({ source }: { source: AskSource }) {
  const { t } = useLang();
  const [open, setOpen] = useState(false);
  return (
    <li className="ask__source">
      <button className="ask__source-head" onClick={() => setOpen((v) => !v)}>
        <span className="ask__source-name">
          {open ? "▾" : "▸"} {source.source}
        </span>
        <span className="ask__source-title">{source.title}</span>
      </button>
      {open && (
        <>
          <pre className="ask__excerpt">{source.excerpt}</pre>
          {/* Why this passage was returned. When a wrong one comes back,
              the matched terms say why — which turns "the search is bad"
              into a term list someone can fix. */}
          <p className="ask__matched">
            {t("ask_matched")}: {source.matched.join(", ")}
          </p>
        </>
      )}
    </li>
  );
}

function TurnView({ turn }: { turn: Turn }) {
  const { t } = useLang();
  return (
    <article className="ask__turn">
      <p className="ask__question">{turn.question}</p>

      {turn.empty ? (
        <p className="ask__nothing">{t("ask_nothing")}</p>
      ) : (
        <>
          {turn.facts && (
            <div className="ask__facts">
              <span className="ask__facts-label">{t("ask_facts")}</span>
              <pre>{turn.facts}</pre>
            </div>
          )}
          {turn.answer && <p className="ask__answer">{turn.answer}</p>}
          {turn.modelError && !turn.answer && (
            <p className="ask__no-model">{t("ask_no_model")}</p>
          )}
          {turn.sources.length > 0 && (
            <>
              <span className="ask__sources-label">{t("ask_sources")}</span>
              <ul className="ask__sources">
                {turn.sources.map((s) => (
                  <SourceCard key={`${s.source}::${s.title}`} source={s} />
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

    const turn: Turn = {
      question: asked, sources: [], facts: null,
      answer: "", empty: false, modelError: null,
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
      patch({ modelError: String(e) });
      setPhase("done");
      return;
    }

    patch({ sources: grounding.sources, facts: grounding.facts });
    endRef.current?.scrollIntoView({ behavior: "smooth" });

    // Nothing was retrieved. The model is not called: there is nothing
    // for it to be grounded in, and asking anyway is asking it to make
    // something up.
    if (grounding.sources.length === 0 && !grounding.facts) {
      patch({ empty: true });
      setPhase("done");
      return;
    }

    setPhase("answering");
    await askViaServer(asked, {
      onToken: (delta) =>
        setTurns((prev) => prev.map((row, i) =>
          i === index ? { ...row, answer: row.answer + delta } : row)),
      onDone: () => setPhase("done"),
      // Not a failure of the page. The sources are already on screen and
      // remain the answer; only the prose is missing.
      onError: (err) => { patch({ modelError: String(err) }); setPhase("done"); },
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
      {busy && (
        <p className="ask__busy">
          {phase === "searching" ? t("ask_searching") : t("ask_asking")}
        </p>
      )}
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
