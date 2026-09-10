import { useState } from "react";
import type { PipelineCase } from "../api/referenceDb";
import { useLang } from "../i18n";
import {
  countViolations,
  KINDS,
  kindsPresent,
  ledger,
  maxCount,
  type LedgerRun,
} from "./violationLedger";
import "./ClosureLedger.css";

// The design's runs as a heatmap: one column per run, oldest left, one
// row per kind of check the design has ever failed, the cell the count
// that run's best candidate still had. Read left to right it answers
// the question the record's "0 PASS · 2 FAIL" rows cannot — is the
// agent getting anywhere — in one glance: a row that fades to nothing
// is a problem the loop closed; a row that stays dark is the one it
// has not.
//
// Colour is one hue, light to dark, on a square-root scale: counts
// span 1 to 1158 in the store and a linear ramp would draw everything
// under 100 as the same faint tint. Zero is not a tint at all but a
// filled "clean" mark, and a kind that was never checked in that run is
// an outline, the same three states SignoffStrip draws. The number is
// always printed in the cell — colour ranks, text tells.

function shade(count: number, max: number): number {
  if (count <= 0 || max <= 0) return 0;
  // 0.18..1 so even a single violation is visibly not-clean.
  return 0.18 + 0.82 * Math.sqrt(count / max);
}

function shortDate(at: string): string {
  // Show MM-DD; when a day has more than one run the caller appends
  // the time.
  return at.slice(5, 10);
}

export default function ClosureLedger({ cases }: { cases: PipelineCase[] }) {
  const { t } = useLang();
  const [showTable, setShowTable] = useState(false);
  const runs = ledger(cases);
  if (runs.length < 2) return null;
  const kinds = kindsPresent(runs);
  if (kinds.length === 0) return null;
  const max = maxCount(runs);

  // Two runs on the same day need the time to be told apart.
  const dayCount = new Map<string, number>();
  for (const r of runs) dayCount.set(shortDate(r.at), (dayCount.get(shortDate(r.at)) ?? 0) + 1);
  const label = (r: LedgerRun) => {
    const d = shortDate(r.at);
    // recordedAt() yields "YYYY-MM-DDTHH:MM:SS".
    return (dayCount.get(d) ?? 0) > 1 ? `${d} ${r.at.slice(11, 16)}` : d;
  };

  const first = runs[0];
  const last = runs[runs.length - 1];
  const closed = kinds.filter((k) => (first.counts[k.id] ?? 0) > 0 && (last.counts[k.id] ?? 0) === 0 && !last.unverifiedKinds.includes(k.id));
  const remaining = kinds.filter((k) => (last.counts[k.id] ?? 0) > 0);

  return (
    <div className="ledger">
      <div className="ledger__head">
        <span className="ledger__title">{t("ledger_title")}</span>
        <span className="ledger__sub">
          {t("ledger_sub")
            .replace("{n}", String(runs.length))
            .replace("{closed}", closed.length ? closed.map((k) => k.short).join(", ") : "—")
            .replace("{open}", remaining.length ? remaining.map((k) => k.short).join(", ") : "—")}
        </span>
        <button className="ledger__toggle" onClick={() => setShowTable((v) => !v)}>
          {showTable ? t("ledger_show_map") : t("ledger_show_table")}
        </button>
      </div>

      {showTable ? (
        <div className="ledger__scroll">
          <table className="ledger__table">
            <thead>
              <tr>
                <th>{t("ledger_run")}</th>
                <th>{t("ledger_candidate")}</th>
                {kinds.map((k) => <th key={k.id}>{k.short}</th>)}
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.file ?? r.at}>
                  <td>{label(r)}</td>
                  <td><code>{r.tag}</code></td>
                  {kinds.map((k) => (
                    <td key={k.id}>
                      {r.unverifiedKinds.includes(k.id) ? t("verdict_never_ran") : (r.counts[k.id] ?? 0)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="ledger__scroll">
          <div
            className="ledger__grid"
            style={{ gridTemplateColumns: `minmax(6.5rem, auto) repeat(${runs.length}, minmax(2.6rem, 1fr))` }}
            role="img"
            aria-label={t("ledger_title")}
          >
            <div className="ledger__corner" />
            {runs.map((r) => (
              <div key={r.file ?? r.at} className={`ledger__col-head ${r.passed ? "ledger__col-head--passed" : ""}`} title={`${r.at} · ${r.tag} · ${r.candidates} ${t("ledger_candidates")}`}>
                {label(r)}
              </div>
            ))}
            {kinds.map((k) => (
              <RowCells key={k.id} kind={k} runs={runs} max={max} />
            ))}
          </div>
        </div>
      )}

      <div className="ledger__legend">
        <span><i className="ledger__swatch ledger__swatch--clean" /> {t("signoff_clean")}</span>
        <span><i className="ledger__swatch ledger__swatch--viol-low" /> 1</span>
        <span><i className="ledger__swatch ledger__swatch--viol-high" /> {max}</span>
        <span><i className="ledger__swatch ledger__swatch--never" /> {t("signoff_never")}</span>
        <span className="ledger__note">{t("ledger_note")}</span>
      </div>
    </div>
  );
}

function RowCells({ kind, runs, max }: { kind: (typeof KINDS)[number]; runs: LedgerRun[]; max: number }) {
  const { t } = useLang();
  return (
    <>
      <div className="ledger__row-head">{kind.short}</div>
      {runs.map((r) => {
        const never = r.unverifiedKinds.includes(kind.id);
        const n = r.counts[kind.id];
        const state = never ? "never" : n === undefined || n === 0 ? "clean" : "count";
        const s = state === "count" ? shade(n!, max) : 0;
        return (
          <div
            key={r.file ?? r.at}
            className={`ledger__cell ledger__cell--${state === "count" ? "viol" : state} ${s > 0.62 ? "ledger__cell--dark" : ""}`}
            // One hue mixed against the surface; the strength is data, so
            // it is set here rather than as a stylesheet token.
            style={state === "count"
              ? { background: `color-mix(in oklab, var(--critical) ${Math.round(s * 100)}%, var(--surface))` }
              : undefined}
            title={`${label(r)} · ${kind.short}: ${never ? t("verdict_never_ran") : (n ?? 0)}`}
          >
            {state === "count" ? n : state === "never" ? "·" : ""}
          </div>
        );
      })}
    </>
  );

  function label(r: LedgerRun) {
    return `${r.at.slice(0, 10)} ${r.tag}`;
  }
}

// One verdict's violations as chips — "218 setup · 200 hold · 544
// max-slew" — instead of the sentence score() wrote. Identity is the
// text; the chip carries no colour of its own beyond the critical
// border a real rejection earns, so a row of six chips reads as six
// facts rather than six alarms. Anything that is not a count (a
// negative slack, a utilisation over target) stays as its sentence.
export function ViolationChips({ violations }: { violations: string[] }) {
  const { counts, other } = countViolations(violations);
  const chips = KINDS.filter((k) => counts[k.id] !== undefined);
  if (chips.length === 0 && other.length === 0) return null;
  return (
    <span className="vchips">
      {chips.map((k) => (
        <span key={k.id} className="vchips__viol" title={`${counts[k.id]} ${k.short}`}>
          <strong>{counts[k.id]}</strong> {k.short}
        </span>
      ))}
      {other.map((o) => (
        <span key={o} className="vchips__other">{o}</span>
      ))}
    </span>
  );
}
