import type { CandidateResult, PipelineCase, ProcessStageId } from "../api/referenceDb";
import { useLang } from "../i18n";
import ConstraintsView from "./Constraints";
import "./StageArtifacts.css";

// What each pipeline stage actually produced.
//
// The stage cards named a stage, its owning agent and a count — "3/9
// candidate run(s) reached this stage" — and stopped there. You could
// see that a stage happened but not what came out of it, even though
// every stage's real output is already in the case: file pointers from
// data_pointers(), the topology signature, the proposed overrides, the
// real OpenLane error text for candidates that died, the signoff
// verdicts, the repaired candidates.
//
// Nothing here is computed or estimated. Each panel reads fields the
// pipeline really recorded; a stage with no artifact says so rather than
// rendering an empty shell.

function FileRow({ label, path }: { label: string; path: string | null }) {
  if (!path) return null;
  // Paths point into runs/, which is gitignored and routinely deleted,
  // so these are shown as a record of what the run produced rather than
  // as links that would often be dead.
  return (
    <li>
      <span className="sa__k">{label}</span>
      <code title={path}>{path.replace(/^.*\/runs\//, "runs/")}</code>
    </li>
  );
}

function Extraction({ candidates }: { candidates: CandidateResult[] }) {
  const { t } = useLang();
  const withData = candidates.find((c) => c.data);
  if (!withData?.data) return <p className="sa__none">{t("sa_none_extraction")}</p>;
  const d = withData.data;
  return (
    <>
      <p className="sa__lede">{t("sa_lede_extraction").replace("{tag}", withData.tag)}</p>
      <div className="sa__cols">
        {(["circuit", "layout", "constraint_pdk", "verification"] as const).map((cat) => (
          <div key={cat}>
            <span className="tab__meta-label">{cat.replace("_", " / ")}</span>
            <ul className="sa__files">
              {Object.entries(d[cat] ?? {}).map(([k, v]) => (
                <FileRow key={k} label={k} path={v} />
              ))}
            </ul>
          </div>
        ))}
      </div>
    </>
  );
}

function Topology({ pipelineCase }: { pipelineCase: PipelineCase }) {
  const { t } = useLang();
  const topo = pipelineCase.topology;
  if (!topo) return <p className="sa__none">{t("sa_none_topology")}</p>;
  return (
    <>
      <p className="sa__lede">{t("sa_lede_topology")}</p>
      <ul className="sa__kv">
        {Object.entries(topo).map(([k, v]) => (
          <li key={k}>
            <span className="sa__k">{k}</span>
            <span className="sa__v">{String(v)}</span>
          </li>
        ))}
      </ul>
    </>
  );
}

function Proposals({ candidates }: { candidates: CandidateResult[] }) {
  const { t } = useLang();
  return (
    <>
      <p className="sa__lede">{t("sa_lede_proposals")}</p>
      <table className="tab__summary sa__table">
        <thead>
          <tr>
            <th>candidate</th>
            <th>config overrides — what makes it different</th>
            <th>origin</th>
          </tr>
        </thead>
        <tbody>
          {candidates.map((c) => (
            <tr key={c.tag}>
              <td><code>{c.tag}</code></td>
              <td>
                {Object.keys(c.overrides ?? {}).length === 0
                  ? <span className="sa__none-inline">{t("sa_baseline")}</span>
                  : Object.entries(c.overrides).map(([k, v]) => (
                      <code key={k} className="sa__override">
                        {k}={JSON.stringify(v)}
                      </code>
                    ))}
              </td>
              <td>
                {c.produced_by_feedback
                  ? <span className="pill">↺ {t("sa_from_repair")}</span>
                  : <span className="sa__none-inline">{t("sa_from_spec")}</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function StoppedHere({
  candidates,
  stage,
}: {
  candidates: CandidateResult[];
  stage: ProcessStageId;
}) {
  const { t } = useLang();
  const here = candidates.filter((c) => c.stage === stage && c.error);
  if (here.length === 0) return <p className="sa__none">{t("sa_none_stopped")}</p>;
  return (
    <>
      <p className="sa__lede">{t("sa_lede_stopped").replace("{n}", String(here.length))}</p>
      {here.map((c) => (
        <div key={c.tag} className="sa__err">
          <span className="tab__meta-label">{c.tag}</span>
          {/* The real captured OpenLane output, not a paraphrase — the
              specific numbers in it are what a diagnosis is built on. */}
          <pre>{c.error}</pre>
        </div>
      ))}
    </>
  );
}

function Verdicts({ candidates }: { candidates: CandidateResult[] }) {
  const { t } = useLang();
  const scored = candidates.filter((c) => c.verdict);
  if (scored.length === 0) return <p className="sa__none">{t("sa_none_verdicts")}</p>;
  return (
    <>
      <p className="sa__lede">{t("sa_lede_verdicts")}</p>
      <table className="tab__summary sa__table">
        <thead>
          <tr>
            <th>candidate</th><th>verdict</th><th>area µm²</th>
            <th>util</th><th>worst setup</th><th>power</th>
          </tr>
        </thead>
        <tbody>
          {scored.map((c) => {
            const v = c.verdict!;
            return (
              <tr key={c.tag}>
                <td><code>{c.tag}</code></td>
                <td>
                  <span className={`pill ${v.passed ? "pill--good" : "pill--critical"}`}>
                    {v.passed ? "PASS" : "FAIL"}
                  </span>
                  {!v.passed && v.violations.length > 0 && (
                    <div className="sa__viol">{v.violations.join("; ")}</div>
                  )}
                </td>
                <td>{v.area_um2 ?? "—"}</td>
                <td>{v.utilization?.toFixed(3) ?? "—"}</td>
                <td>{v.worst_setup_wns} ns</td>
                <td>
                  {v.power?.total_w != null
                    ? `${(v.power.total_w * 1000).toFixed(4)} mW`
                    : "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </>
  );
}

function Feedback({
  pipelineCase,
  candidates,
}: {
  pipelineCase: PipelineCase;
  candidates: CandidateResult[];
}) {
  const { t } = useLang();
  const repaired = candidates.filter((c) => c.produced_by_feedback);
  const reviews = pipelineCase.human_in_the_loop ?? [];
  return (
    <>
      <p className="sa__lede">
        {t("sa_lede_feedback")
          .replace("{stop}", pipelineCase.stop_reason ?? "—")
          .replace("{n}", String(repaired.length))}
      </p>
      {repaired.length > 0 && (
        <table className="tab__summary sa__table">
          <thead>
            <tr><th>repaired candidate</th><th>config it was given</th><th>result</th></tr>
          </thead>
          <tbody>
            {repaired.map((c) => (
              <tr key={c.tag}>
                <td><code>{c.tag}</code></td>
                <td>
                  {Object.entries(c.overrides ?? {}).map(([k, v]) => (
                    <code key={k} className="sa__override">{k}={JSON.stringify(v)}</code>
                  ))}
                </td>
                <td>
                  {c.verdict
                    ? <span className={`pill ${c.verdict.passed ? "pill--good" : "pill--critical"}`}>
                        {c.verdict.passed ? "PASS" : "FAIL"}
                      </span>
                    : <span className="pill pill--critical">FAIL TO RUN</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {reviews.length > 0 && (
        <div className="sa__reviews">
          <span className="tab__meta-label">{t("sa_reviews").replace("{n}", String(reviews.length))}</span>
          <ul>
            {reviews.map((r, i) => (
              <li key={i}>
                <code>{r.agent}</code> <span className="sa__when">{r.reviewed_at}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  );
}

// Which declared constraints the candidates actually ran with different
// values for. Deduplicated and kept in candidate order so the list reads
// as the sequence a repair walked through (8 → 16 → 32 → 64 µm), not as
// an unordered set.
export function overriddenConstraints(
  candidates: CandidateResult[]
): Map<string, unknown[]> {
  const out = new Map<string, unknown[]>();
  for (const c of candidates) {
    for (const [k, v] of Object.entries(c.overrides ?? {})) {
      const seen = out.get(k) ?? [];
      // Compare structurally: DIE_AREA values are arrays, so identity
      // would keep every repeat of the same rectangle.
      const key = JSON.stringify(v);
      if (!seen.some((s) => JSON.stringify(s) === key)) out.set(k, [...seen, v]);
    }
  }
  return out;
}

export default function StageArtifacts({
  stage,
  stageName,
  pipelineCase,
}: {
  stage: ProcessStageId;
  stageName: string;
  pipelineCase: PipelineCase;
}) {
  const candidates = pipelineCase.iterations.flatMap((it) => it.results);
  return (
    <div className="sa">
      <span className="sa__title">{stageName}</span>
      {stage === "extraction" && <Extraction candidates={candidates} />}
      {stage === "topology" && <Topology pipelineCase={pipelineCase} />}
      {stage === "placement_strategy" && <Proposals candidates={candidates} />}
      {/* Stage 4 evaluates the physical constraints, so this is where
          they belong — showing which candidates died here without ever
          showing what they were held to left the reader unable to judge
          the verdict. The failures come first: they are the answer to
          "what happened", and the rules are the reference for it. */}
      {stage === "physical_constraint" && (
        <>
          <StoppedHere candidates={candidates} stage={stage} />
          <ConstraintsView
            constraints={pipelineCase.constraints}
            overridden={overriddenConstraints(candidates)}
          />
        </>
      )}
      {(stage === "routing_generation" || stage === "routing_candidate") && (
        <StoppedHere candidates={candidates} stage={stage} />
      )}
      {stage === "verification_ppa" && <Verdicts candidates={candidates} />}
      {stage === "feedback" && (
        <Feedback pipelineCase={pipelineCase} candidates={candidates} />
      )}
    </div>
  );
}
