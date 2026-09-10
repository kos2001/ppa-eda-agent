import { useCallback, useEffect, useState } from "react";
import {
  fetchFeedback,
  fetchReferenceDb,
  sendFeedback,
  type FeedbackEntry,
  type PipelineCase,
} from "../api/referenceDb";
import { useLang, type DictKey } from "../i18n";
import { formatSeconds, runCosts } from "./runCost";
import "./ManualPage.css";

// How to operate this console, and somewhere to say it did not help.
//
// Every other page shows what the tools produced. None of them said what
// a person is supposed to *do* — the closest was the "how it works"
// block on the pipeline page, which explains the concept and not the
// operation. So a newcomer could see nine candidate runs and a FAIL pill
// with no idea which button starts a run, what an OPEN case is waiting
// for, or what to type into a review.
//
// Task-shaped rather than feature-shaped: the headings are questions
// someone actually arrives with, in the order they arrive in. A tour of
// the buttons would be easier to write and would answer none of them.
//
// The feedback form sits at the bottom on purpose. A reader who had to
// look something up has just found a gap, and that is the moment they
// can describe it — asking on a separate page would ask everyone except
// the people with something to say.

interface Step {
  q: DictKey;
  a: DictKey;
  where?: DictKey;
  // A live table under the answer, for the questions whose honest
  // answer is a number the store keeps changing.
  table?: "cost";
}

// Per-design run cost, from the store, for the "how long" question.
//
// Fetches the cases itself rather than being handed them: the manual is
// lazy-loaded and mounted with no props, and threading the pipeline
// page's case list through App for one table would couple two pages
// that otherwise share nothing.
function RunCostTable() {
  const { t } = useLang();
  const [cases, setCases] = useState<PipelineCase[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    fetchReferenceDb()
      .then((db) => {
        if (live) setCases(Object.values(db.designs).flat());
      })
      .catch((e) => {
        if (live) setError(String(e));
      });
    return () => {
      live = false;
    };
  }, []);

  if (error) return <p className="man__cost-note">{t("man_cost_unavailable")}</p>;
  if (!cases) return <p className="man__cost-note">…</p>;

  const rows = Object.values(runCosts(cases)).sort(
    (a, b) => b.medianSeconds - a.medianSeconds,
  );
  if (rows.length === 0) {
    return <p className="man__cost-note">{t("man_cost_none")}</p>;
  }
  return (
    <table className="man__cost">
      <thead>
        <tr>
          <th>{t("man_cost_design")}</th>
          <th>{t("man_cost_median")}</th>
          <th>{t("man_cost_runs")}</th>
          <th>{t("man_cost_host")}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.design}>
            <td>{r.design}</td>
            <td className="man__cost-num">{formatSeconds(r.medianSeconds)}</td>
            <td className="man__cost-num">{r.runs}</td>
            <td>{r.hosts.join(", ") || "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// A section's kind picks its card's accent colour — the same
// understand/propose/evaluate/decide colour language PipelineTab uses
// for its four phases, reused here because this page is the same kind
// of four-stage journey (start -> read -> get stuck -> improve).
type SectionKind = "start" | "read" | "stuck" | "improve";

const SECTIONS: { title: DictKey; kind: SectionKind; steps: Step[] }[] = [
  {
    title: "man_s_start",
    kind: "start",
    steps: [
      { q: "man_q_what", a: "man_a_what" },
      { q: "man_q_run", a: "man_a_run", where: "man_w_run" },
      { q: "man_q_watch", a: "man_a_watch", where: "man_w_watch" },
      // Added because the first feedback entry this page ever received
      // asked for it: the manual said where every control was and never
      // what a run costs. The answer is a table computed from the store
      // (see RunCostTable) — the typed sentence it replaced said "about
      // 27 s for counter4" and could not say anything about aes.
      { q: "man_q_howlong", a: "man_a_howlong", table: "cost" },
    ],
  },
  {
    title: "man_s_read",
    kind: "read",
    steps: [
      { q: "man_q_verdict", a: "man_a_verdict", where: "man_w_verdict" },
      { q: "man_q_unverified", a: "man_a_unverified" },
      { q: "man_q_stage", a: "man_a_stage", where: "man_w_stage" },
    ],
  },
  {
    title: "man_s_stuck",
    kind: "stuck",
    steps: [
      { q: "man_q_open", a: "man_a_open", where: "man_w_open" },
      { q: "man_q_review", a: "man_a_review", where: "man_w_review" },
      { q: "man_q_budget", a: "man_a_budget", where: "man_w_budget" },
    ],
  },
  {
    title: "man_s_improve",
    kind: "improve",
    steps: [
      { q: "man_q_next", a: "man_a_next", where: "man_w_next" },
      { q: "man_q_data", a: "man_a_data" },
      { q: "man_q_lang", a: "man_a_lang", where: "man_w_lang" },
    ],
  },
];

const KINDS: { id: string; label: DictKey }[] = [
  { id: "bug", label: "man_k_bug" },
  { id: "confusing", label: "man_k_confusing" },
  { id: "missing", label: "man_k_missing" },
  { id: "note", label: "man_k_note" },
];

function Feedback() {
  const { t } = useLang();
  const [message, setMessage] = useState("");
  const [kind, setKind] = useState("confusing");
  const [busy, setBusy] = useState(false);
  const [sentAt, setSentAt] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [entries, setEntries] = useState<FeedbackEntry[]>([]);

  const load = useCallback(async () => {
    try {
      setEntries((await fetchFeedback()).entries);
    } catch {
      // A failed listing is not worth an error banner over the form —
      // it does not stop anyone from sending.
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function submit() {
    if (!message.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const res = await sendFeedback(message, kind, "manual");
      setSentAt(res.at);
      setMessage("");
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="man__feedback">
      <h3>{t("man_fb_title")}</h3>
      <p className="man__lede">{t("man_fb_lede")}</p>

      <div className="man__kinds">
        {KINDS.map((k) => (
          <button
            key={k.id}
            className={kind === k.id ? "man__kind man__kind--on" : "man__kind"}
            onClick={() => setKind(k.id)}
          >
            {t(k.label)}
          </button>
        ))}
      </div>

      <textarea
        className="man__input"
        rows={4}
        value={message}
        placeholder={t("man_fb_placeholder")}
        onChange={(e) => setMessage(e.target.value)}
      />

      <div className="man__send">
        <button onClick={() => void submit()} disabled={busy || !message.trim()}>
          {busy ? t("man_fb_sending") : t("man_fb_send")}
        </button>
        {/* Says where it went. "Thanks for your feedback" with no
            destination is how feedback forms earn their reputation. */}
        <span className="man__dest">{t("man_fb_dest")}</span>
      </div>

      {sentAt && <p className="man__ok">{t("man_fb_ok").replace("{t}", sentAt)}</p>}
      {error && <p className="tab__error">{error}</p>}

      {entries.length > 0 && (
        <details className="man__log">
          <summary>{t("man_fb_log").replace("{n}", String(entries.length))}</summary>
          <ul>
            {entries.slice(0, 20).map((e, i) => (
              <li key={`${e.at}-${i}`}>
                <span className="man__log-meta">
                  {e.at.replace("T", " ").slice(0, 16)} · {e.kind}
                </span>
                <span className="man__log-msg">{e.message}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

export default function ManualPage() {
  const { t } = useLang();
  return (
    <section className="man">
      <h2 className="man__title">{t("man_title")}</h2>
      <p className="man__lede">{t("man_lede")}</p>

      <div className="man__grid">
        {SECTIONS.map((section) => (
          <section
            key={section.title}
            className={`man__card man__card--${section.kind}`}
          >
            <h3 className="man__card-title">{t(section.title)}</h3>
            <dl className="man__list">
              {section.steps.map((step) => (
                <div key={step.q} className="man__item">
                  <dt>{t(step.q)}</dt>
                  <dd>
                    {t(step.a)}
                    {/* Named so the answer can be acted on without
                        hunting. A manual that explains a concept but not
                        where the control is has answered half a question. */}
                    {step.where && (
                      <span className="man__where">{t(step.where)}</span>
                    )}
                    {step.table === "cost" && <RunCostTable />}
                  </dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
      </div>

      <Feedback />
    </section>
  );
}
