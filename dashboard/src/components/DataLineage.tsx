import { useEffect, useState } from "react";
import "./DataLineage.css";

// What the console could not previously say.
//
// SystemHealth answers "does anything need attention" — dataset size,
// retrieval coverage, whether a surrogate is trainable. It does not show
// the path the data takes, and someone asking "is this actually learning
// from what it runs?" had to read four Python modules to find out.
//
// This is that path, laid out in the order the data moves: a run becomes
// a case, cases become rows, rows become features, and two separate
// consumers read the result for two different purposes. Every number is
// read live from the real store rather than tracked separately, so the
// page cannot drift from what the pipeline actually holds.

type Design = {
  design: string;
  rows: number;
  completed: number;
  libraries: Record<string, number>;
};

type Feature = { feature: string; rows_with_a_value: number; coverage_pct: number };

type TargetRow = {
  target: string;
  win_rate: number | null;
  folds: number | null;
  k: number | null;
  verdict: string | null;
  interval: { lo: number; hi: number; clears_threshold: boolean } | null;
};

type Report = {
  collected: {
    designs: Design[];
    technologies: Record<string, number>;
    pdks: Record<string, number>;
    knobs_swept: Record<string, number>;
  };
  stored: {
    case_files: number;
    recorded_runs: number;
    distinct_samples: number;
    collapsed_by_dedup: number;
    dedup_key: string[];
    layouts: number;
  };
  featurized: { features: Feature[]; targets: { target: string; rows_with_a_value: number }[]; total_rows: number };
  retrieved: {
    precedent: { cases_indexed: number; keyed_by: string };
    guidance: {
      measurements_indexed: number;
      source_designs: Record<string, number>;
      signatures: string[];
      single_source: boolean;
    };
  };
  learned: { targets: TargetRow[]; threshold: number };
};

const SERVER = "http://localhost:8123";

function shortLib(name: string): string {
  return name.replace(/^sky130_fd_sc_/, "sky130 ").replace(/^gf180mcu_fd_sc_/, "gf180 ");
}

function Stage({
  n,
  title,
  subtitle,
  children,
}: {
  n: number;
  title: string;
  subtitle: string;
  children: React.ReactNode;
}) {
  return (
    <section className="dl__stage">
      <header className="dl__stage-head">
        <span className="dl__stage-n">{n}</span>
        <div>
          <h3>{title}</h3>
          <p>{subtitle}</p>
        </div>
      </header>
      <div className="dl__stage-body">{children}</div>
    </section>
  );
}

export default function DataLineage() {
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    fetch(`${SERVER}/data-lineage`)
      .then((r) => r.json())
      .then((d) => {
        if (!live) return;
        if (d?.error) setError(d.error);
        else setReport(d);
      })
      .catch((e) => live && setError(String(e)));
    return () => {
      live = false;
    };
  }, []);

  if (error) return <p className="dl__error">Could not read the pipeline: {error}</p>;
  if (!report) return <p className="dl__loading">Reading the store…</p>;

  const { collected, stored, featurized, retrieved, learned } = report;
  const libs = Object.keys(collected.technologies);

  return (
    <div className="dl">
      <p className="dl__lede">
        Where the data comes from, what it becomes, and who reads it. Every number
        below is read from <code>reference-db</code> as this page loads.
      </p>

      <Stage
        n={1}
        title="Collected"
        subtitle="What was actually run — real OpenLane flows, not estimates."
      >
        <table className="dl__table">
          <thead>
            <tr>
              <th>design</th>
              {libs.map((l) => (
                <th key={l}>{shortLib(l)}</th>
              ))}
              <th>completed</th>
            </tr>
          </thead>
          <tbody>
            {collected.designs.map((d) => (
              <tr key={d.design}>
                <td className="dl__name">{d.design}</td>
                {libs.map((l) => (
                  <td key={l} className={d.libraries[l] ? "" : "dl__zero"}>
                    {d.libraries[l] ?? 0}
                  </td>
                ))}
                <td>
                  {d.completed}
                  <span className="dl__of"> / {d.rows}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="dl__note">
          A row with zero completions still counts: a design that cannot build is a
          recorded fact, not a gap in the data.
        </p>
        <ul className="dl__chips">
          {Object.entries(collected.knobs_swept).map(([k, n]) => (
            <li key={k}>
              <code>{k}</code>
              <span>{n}</span>
            </li>
          ))}
        </ul>
      </Stage>

      <Stage
        n={2}
        title="Stored"
        subtitle="Cases on disk, and what deduplication removes from them."
      >
        <div className="dl__flow">
          <div className="dl__flow-box">
            <strong>{stored.recorded_runs}</strong>
            <span>recorded runs</span>
            <small>{stored.case_files} case files</small>
          </div>
          <div className="dl__flow-arrow">
            −{stored.collapsed_by_dedup}
            <small>duplicates</small>
          </div>
          <div className="dl__flow-box dl__flow-box--out">
            <strong>{stored.distinct_samples}</strong>
            <span>distinct samples</span>
            <small>{stored.layouts} layouts</small>
          </div>
        </div>
        <p className="dl__note">
          Deduplicated on {stored.dedup_key.map((k) => <code key={k}>{k}</code>)} — a
          re-run records the same configuration again, and counting those as
          independent samples would inflate any accuracy figure.
        </p>
      </Stage>

      <Stage
        n={3}
        title="Featurized"
        subtitle="Which columns a model can see, and how many rows fill them."
      >
        <ul className="dl__bars">
          {featurized.features.map((f) => (
            <li key={f.feature}>
              <span className="dl__bar-label">
                <code>{f.feature}</code>
              </span>
              <span className="dl__bar-track">
                <span
                  className={`dl__bar-fill${f.coverage_pct < 10 ? " dl__bar-fill--thin" : ""}`}
                  style={{ width: `${Math.max(f.coverage_pct, 1)}%` }}
                />
              </span>
              <span className="dl__bar-value">{f.coverage_pct}%</span>
            </li>
          ))}
        </ul>
        <p className="dl__note">
          An empty bar is a feature nobody has given data to. That has happened both
          ways here: <code>PL_TARGET_DENSITY_PCT</code> was a feature with no rows,
          and <code>scl</code> arrived as data before it was a feature.
        </p>
      </Stage>

      <Stage
        n={4}
        title="Retrieved"
        subtitle="Two RAG paths, answering two different questions."
      >
        <div className="dl__two">
          <div className="dl__card">
            <h4>Precedent — what happened before</h4>
            <p className="dl__big">{retrieved.precedent.cases_indexed}</p>
            <p className="dl__sub">cases indexed</p>
            <p className="dl__note">keyed by {retrieved.precedent.keyed_by}</p>
          </div>
          <div className="dl__card">
            <h4>Guidance — what to run about it</h4>
            <p className="dl__big">{retrieved.guidance.measurements_indexed}</p>
            <p className="dl__sub">measurements indexed</p>
            <p className="dl__note">
              from{" "}
              {Object.entries(retrieved.guidance.source_designs).map(([d, n]) => (
                <code key={d}>
                  {d} ×{n}
                </code>
              ))}
            </p>
            {retrieved.guidance.single_source && (
              <p className="dl__warn">
                Every entry comes from one design, so leave-one-out leaves that
                design nothing. The index has no transferable guidance yet.
              </p>
            )}
          </div>
        </div>
        <details className="dl__sigs">
          <summary>{retrieved.guidance.signatures.length} failure signatures indexed</summary>
          <ul className="dl__chips">
            {retrieved.guidance.signatures.map((s) => (
              <li key={s}>
                <code>{s}</code>
              </li>
            ))}
          </ul>
        </details>
      </Stage>

      <Stage
        n={5}
        title="Learned"
        subtitle={`What the surrogate can predict. A target clears when the lower bound of its interval reaches ${learned.threshold}.`}
      >
        <table className="dl__table">
          <thead>
            <tr>
              <th>target</th>
              <th>win-rate</th>
              <th>90% interval</th>
              <th>folds</th>
              <th>k</th>
            </tr>
          </thead>
          <tbody>
            {learned.targets.map((t) => (
              <tr key={t.target}>
                <td className="dl__name">{t.target}</td>
                <td>{t.win_rate == null ? "—" : t.win_rate.toFixed(2)}</td>
                <td>
                  {t.interval ? (
                    <span
                      className={
                        t.interval.clears_threshold ? "pill pill--good" : "pill pill--warn"
                      }
                    >
                      {t.interval.lo.toFixed(2)} – {t.interval.hi.toFixed(2)}
                    </span>
                  ) : (
                    "—"
                  )}
                </td>
                <td>{t.folds ?? "—"}</td>
                <td>{t.k ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="dl__note">
          The interval is a bootstrap over the leave-one-out folds. It bounds sampling
          noise, not the choice of samples — it cannot see that the folds share a
          corpus of {collected.designs.length} designs.
        </p>
      </Stage>
    </div>
  );
}
