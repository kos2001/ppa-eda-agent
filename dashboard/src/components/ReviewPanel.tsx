import { useState } from "react";
import { applyReview, requestReview, type PipelineCase } from "../api/referenceDb";
import { askReview, cachedReview } from "../api/gateway";
import { useAgent } from "../agentContext";
import { useLang } from "../i18n";
import MarkdownDoc from "./Markdown";
import TranslateBlock from "./TranslateBlock";
import { AGENT_ROLE_BY_NAME, formatCachedAt } from "./pipelineModel";

// Surfaces the real human-in-the-loop workflow (pipeline/request_review.py):
// an OPEN case (no winner, and propose_repairs() found nothing auto-
// repairable) is flagged for review; once a subagent's verdict has been
// applied via `request_review.py apply`, it shows up here as real history,
// not just buried in the diagnosis text.
// Stage 8, carried out in the console. Three real steps, each backed by
// the same pipeline/request_review.py workflow a terminal would run:
// generate the request from the case's real diagnosis, get a second
// opinion on it, then write that verdict back into the case. Until this
// existed the UI printed a shell command here and the end-to-end process
// left the tool at precisely the point that needs judgment.
function ReviewWorkflow({
  design,
  onApplied,
}: {
  design: string;
  onApplied: () => void;
}) {
  const { lang, t } = useLang();
  const { serverConfigured } = useAgent();
  const [requestText, setRequestText] = useState<string | null>(null);
  const [review, setReview] = useState<string | null>(null);
  const [busy, setBusy] = useState<null | "request" | "ask" | "apply">(null);
  const [error, setError] = useState<string | null>(null);
  const [showRequest, setShowRequest] = useState(false);
  // Whether the text in the box came from the model, and whether a
  // person has since touched it. This is the whole point of the panel:
  // it was called human-in-the-loop while offering the human three
  // buttons and no way to say anything.
  const [aiDrafted, setAiDrafted] = useState(false);
  const [humanEdited, setHumanEdited] = useState(false);
  // When the shown draft came off disk rather than from a call just
  // now. Surfaced because a reader deciding whether to trust a verdict
  // should know whether the model saw this case a minute ago or an hour
  // ago — and because "regenerate" only makes sense once they know they
  // are looking at a stored answer.
  const [cachedAt, setCachedAt] = useState<string | null>(null);

  const reviewAuthor = !aiDrafted
    ? "human-review"
    : humanEdited
      ? "hermes-review+human"
      : "hermes-review";

  async function handleRequest() {
    setBusy("request");
    setError(null);
    try {
      const { content } = await requestReview(design);
      setRequestText(content);
      // The usual order of work is: read what the model said, then write
      // your own verdict. So if a draft for this exact request already
      // exists, it is put in the box now rather than behind a button —
      // pressing "ask" to retrieve an answer that is already on disk is
      // a step that exists only because the panel forgot.
      // Only when there is a request to match a draft against. With no
      // request text there is nothing to compare a stored draft to, and
      // showing one anyway is the bug this check exists to prevent.
      if (content) {
        const cached = await cachedReview(design, content, lang);
        if (cached.text) {
          setReview(cached.text);
          setAiDrafted(true);
          setHumanEdited(false);
          setCachedAt(cached.written_at);
        }
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  function handleAsk(refresh = false) {
    if (!requestText) return;
    setBusy("ask");
    setReview("");
    setError(null);
    setAiDrafted(true);
    setHumanEdited(false);
    setCachedAt(null);
    // The agent answers in the language the console is set to, rather
    // than answering in English and leaving the user to press translate.
    askReview(requestText, {
      onToken: (d) => setReview((prev) => (prev ?? "") + d),
      onDone: () => setBusy(null),
      onError: (e) => {
        setBusy(null);
        setError(e.message);
      },
    }, lang, { design, refresh });
  }

  async function handleApply() {
    if (!review?.trim()) return;
    setBusy("apply");
    setError(null);
    try {
      // Attribution follows who actually wrote the text. A human
      // verdict recorded as "hermes-review" would misstate the record in
      // exactly the way this project refuses to elsewhere — and the
      // reverse, an untouched model answer filed as human judgement, is
      // worse.
      await applyReview(design, reviewAuthor, review);
      setReview(null);
      setRequestText(null);
      setAiDrafted(false);
      setHumanEdited(false);
      setCachedAt(null);
      onApplied();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="pipeline__review">
      <ol className="pipeline__review-steps">
        <li>
          <button onClick={handleRequest} disabled={busy !== null}>
            {busy === "request" ? "…" : t("review_step_request")}
          </button>
          {requestText && (
            <button
              className="pipeline__review-link"
              onClick={() => setShowRequest((v) => !v)}
            >
              {showRequest ? t("review_hide_request") : t("review_show_request")}
            </button>
          )}
        </li>
        <li>
          {/* Optional. The workflow used to dead-end here without a
              gateway key: the button was disabled and there was no other
              way to produce review text, so the entire panel was unusable
              on a machine with no key. */}
          <button
            onClick={() => handleAsk(cachedAt !== null)}
            disabled={busy !== null || !requestText || !serverConfigured}
            title={!serverConfigured ? t("pipeline_translate_needs_key") : undefined}
          >
            {busy === "ask"
              ? t("review_asking")
              : cachedAt
                ? t("review_step_reask")
                : t("review_step_ask")}
          </button>
          {/* Once a draft is already in the box, this button's job
              changes from "get one" to "get another" — and it says so,
              because pressing it then costs a fresh multi-minute call
              and overwrites what is there. */}
          <span className="pipeline__review-optional">
            {cachedAt ? t("review_cached_at").replace("{t}", formatCachedAt(cachedAt))
                      : t("review_ai_optional")}
          </span>
        </li>
        <li>
          <button onClick={handleApply} disabled={busy !== null || !review?.trim()}>
            {busy === "apply" ? "…" : t("review_step_apply")}
          </button>
        </li>
      </ol>

      {showRequest && requestText && (
        <div className="pipeline__review-doc">
          <MarkdownDoc source={requestText} />
        </div>
      )}
      <div className="pipeline__review-result">
        <span className="tab__meta-label">
          {t("review_your_verdict")}
          <span className="pipeline__review-author">{reviewAuthor}</span>
        </span>
        {/* Editable, and usable with nothing in it but what a person
            types. The model's answer is a draft to correct, not a
            result to accept. */}
        <textarea
          className="pipeline__review-input"
          value={review ?? ""}
          placeholder={t("review_placeholder")}
          rows={6}
          disabled={busy === "ask"}
          onChange={(e) => {
            setReview(e.target.value);
            if (aiDrafted) setHumanEdited(true);
          }}
        />
        <p className="pipeline__review-hint">{t("review_hint")}</p>
      </div>
      {error && <p className="tab__error">{error}</p>}
    </div>
  );
}

export default function HumanInTheLoopPanel({
  pipelineCase,
  onApplied,
}: {
  pipelineCase: PipelineCase;
  onApplied: () => void;
}) {
  const { lang, t } = useLang();
  const isOpen = !pipelineCase.winner_tag;
  const reviews = pipelineCase.human_in_the_loop ?? [];
  if (!isOpen && reviews.length === 0) return null;

  return (
    <div className={`pipeline__hitl ${isOpen ? "pipeline__hitl--action" : ""}`}>
      <span className="tab__meta-label">
        human-in-the-loop
        {isOpen && (
          <span className="pipeline__hitl-badge">{t("hitl_needs_you")}</span>
        )}
      </span>
      {isOpen && <ReviewWorkflow design={pipelineCase.design} onApplied={onApplied} />}
      {reviews.length > 0 && (
        <ul className="pipeline__hitl-log">
          {reviews.map((r, i) => (
            <li key={i}>
              <span className="pill pill--good" title={AGENT_ROLE_BY_NAME[r.agent]?.[lang]}>
                {r.agent}
              </span>
              <span className="pipeline__hitl-time">{r.reviewed_at}</span>
              <span className="pipeline__hitl-summary">
                {r.summary}
                <TranslateBlock text={r.summary} />
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
