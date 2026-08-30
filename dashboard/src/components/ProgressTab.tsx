import { useEffect, useMemo, useState } from "react";
import { fetchReferenceDb, type PipelineCase } from "../api/referenceDb";
import { coverageCurve, frontiers, type Frontier } from "./progressTimeline";
import { useLang } from "../i18n";
import "./ProgressTab.css";

// "Are the attempts making this better?" — the question the record list
// could not answer, because 54 cards in time order show what was tried
// and never what it added up to.
//
// The page leads with the warning rather than burying it. The obvious
// chart here is recorded area against time, it is the first thing a
// reader will expect, and it is wrong for a reason no axis label can
// carry: the store spans two processes whose cell sizes differ by 3.5x.
// Saying so once, at the top, is cheaper than a caveat on every series.
//
// Everything is derived from the case store at read time — see
// progressTimeline.ts. No page-specific data is recorded.

// A sparkline, not a charting library. Recharts is already lazy-loaded
// for the pipeline's candidate chart and pulls ~88KB gzipped; these are
// monotone curves of at most a few dozen points, and an inline SVG that
// scales to its own data is both smaller and honest about resolution.
function Spark({
  values,
  labelled,
}: {
  values: number[];
  labelled: string;
}) {
  if (values.length < 2) return null;
  const max = Math.max(...values);
  const min = Math.min(...values);
  // A flat series has no range to normalise against; drawing it against
  // its own zero would turn rounding into a visible slope.
  const span = max - min || 1;
  const points = values
    .map((v, i) => {
      const x = (i / (values.length - 1)) * 100;
      const y = 20 - ((v - min) / span) * 18;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
  return (
    <svg
      className="pg__spark"
      viewBox="0 0 100 20"
      preserveAspectRatio="none"
      role="img"
      aria-label={labelled}
    >
      <polyline points={points} />
    </svg>
  );
}

function CoverageRow({
  label,
  values,
}: {
  label: string;
  values: number[];
}) {
  return (
    <div className="pg__cov-row">
      <span className="pg__cov-label">{label}</span>
      <span className="pg__cov-from">{values[0]}</span>
      <Spark values={values} labelled={`${label}: ${values[0]} → ${values[values.length - 1]}`} />
      <strong className="pg__cov-to">{values[values.length - 1]}</strong>
    </div>
  );
}

function FrontierRow({ series }: { series: Frontier }) {
  const { t } = useLang();
  const areas = series.points.map((p) => p.area);
  const first = areas[0];
  const last = areas[areas.length - 1];
  const moved = series.improvedPct > 0;

  return (
    <li className="pg__frontier">
      <div className="pg__frontier-head">
        <span className="pg__frontier-name">
          {series.design}
          {/* The technology is half the identity of a series, not a
              footnote: two series of the same design are only comparable
              within one. */}
          <span className="pg__frontier-tech">{series.scl}</span>
        </span>
        <span className={moved ? "pg__gain" : "pg__gain pg__gain--none"}>
          {moved ? `−${series.improvedPct.toFixed(2)}%` : t("pg_no_gain")}
        </span>
      </div>
      <div className="pg__frontier-body">
        <span className="pg__area">{first.toFixed(1)}</span>
        <Spark values={areas} labelled={`${series.design} ${series.scl}`} />
        <span className="pg__area pg__area--best">{last.toFixed(1)}</span>
        <span className="pg__frontier-meta">
          {t("pg_attempts").replace("{n}", String(series.attempts))}
          {" · "}
          {/* Records, not attempts. 46 runs produced 2 records, and
              showing only the records would make a long search look
              like a short one. */}
          {t("pg_records").replace("{n}", String(series.points.length - 1))}
        </span>
      </div>
    </li>
  );
}

export default function ProgressTab() {
  const { t } = useLang();
  const [cases, setCases] = useState<PipelineCase[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchReferenceDb()
      .then((db) => setCases(Object.values(db.designs).flat()))
      .catch((e) => setError(String(e)));
  }, []);

  const coverage = useMemo(() => coverageCurve(cases ?? []), [cases]);
  const series = useMemo(() => frontiers(cases ?? []), [cases]);
  const recordedRows = useMemo(
    () =>
      (cases ?? []).reduce(
        (n, c) =>
          n + (c.iterations ?? []).reduce((m, i) => m + (i.results?.length ?? 0), 0),
        0
      ),
    [cases]
  );

  if (error) return <div className="tab"><p className="tab__error">{error}</p></div>;
  if (cases === null) return <div className="tab"><p>{t("pg_loading")}</p></div>;

  const last = coverage[coverage.length - 1];

  return (
    <div className="tab pg">
      <p className="pg__intro">{t("pg_intro")}</p>

      <div className="pg__warn">
        <span className="pg__warn-title">⚠ {t("pg_warn_title")}</span>
        <p>{t("pg_warn_body")}</p>
      </div>

      <section className="panel">
        <span className="panel__title">{t("pg_coverage_title")}</span>
        <div className="panel__body">
          <CoverageRow label={t("pg_samples")} values={coverage.map((p) => p.samples)} />
          <CoverageRow label={t("pg_designs")} values={coverage.map((p) => p.designs)} />
          <CoverageRow label={t("pg_techs")} values={coverage.map((p) => p.technologies)} />
          <p className="pg__note">
            {/* Both counts are read from the store rather than written
                into the sentence. The first draft said "441 rows" and
                was already wrong by one when it shipped — the aes run
                recovered that morning made it 442. */}
            {t("pg_coverage_note")
              .replace("{r}", String(recordedRows))
              .replace("{n}", String(last?.samples ?? 0))}
          </p>
        </div>
      </section>

      <section className="panel">
        <span className="panel__title">{t("pg_frontier_title")}</span>
        <div className="panel__body">
          {series.length === 0 ? (
            <p className="pg__note">{t("pg_frontier_empty")}</p>
          ) : (
            <>
              <ul className="pg__frontiers">
                {series.map((s) => (
                  <FrontierRow key={`${s.design} ${s.pdk} ${s.scl}`} series={s} />
                ))}
              </ul>
              <p className="pg__note">{t("pg_frontier_note")}</p>
            </>
          )}
        </div>
      </section>
    </div>
  );
}
