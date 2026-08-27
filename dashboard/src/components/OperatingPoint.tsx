import type {
  ClockCoverage, OperatingPoint, SupplyRail,
} from "../api/referenceDb";
import { useLang } from "../i18n";
import "./OperatingPoint.css";

// Fmax, Vmin, supply rails and clock coverage — the signoff facts a DTCO
// decision is actually made on.
//
// All of it was already measured and thrown away. The verdict reported
// "worst setup WNS 0", which is true and useless: OpenSTA clamps worst
// *negative* slack at 0, so a design with 6.85 ns of margin looked
// identical to one with none. The margin was one unread metric away, and
// with it the design's real ceiling — 255 MHz on a part constrained at
// 100 MHz.
//
// Every number here carries the corner it came from. A single Fmax with
// no corner attached is the kind of figure that gets quoted back later
// without the conditions that made it true.

function fmtV(v: number | null): string {
  if (v === null || v === undefined) return "—";
  // Sub-millivolt droop is normal and reads as 0.000 in volts.
  return Math.abs(v) < 1e-3 ? `${(v * 1e6).toFixed(1)} µV` : `${v.toFixed(4)} V`;
}

function Rails({ supplies }: { supplies: SupplyRail[] }) {
  const { t } = useLang();
  return (
    <div className="op__block">
      <h5>{t("op_supplies")}</h5>
      <div className="op__scroll">
        <table className="tab__summary op__table">
          <thead>
            <tr>
              <th>{t("op_net")}</th>
              <th>{t("op_nominal")}</th>
              <th>{t("op_worst_drop")}</th>
              <th>{t("op_drop_pct")}</th>
            </tr>
          </thead>
          <tbody>
            {supplies.map((s) => (
              <tr key={s.net}>
                <td><code>{s.net}</code></td>
                <td>{s.nominal_v != null && s.nominal_v > 0.1
                  ? `${s.nominal_v.toFixed(3)} V` : "—"}</td>
                <td>{fmtV(s.drop_worst_v)}</td>
                {/* A ground rail has no meaningful percentage of a 0 V
                    nominal; showing "0%" there would invent a margin. */}
                <td>{s.drop_pct != null ? `${s.drop_pct.toFixed(3)}%` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Clocks({ clocks }: { clocks: ClockCoverage }) {
  const { t } = useLang();
  const bad = clocks.unconstrained_clocks.length > 0;
  return (
    <div className={`op__block ${bad ? "op__block--warn" : ""}`}>
      <h5>{t("op_clocks")}</h5>
      <ul className="sa__kv">
        <li>
          <span className="sa__k">{t("op_declared")}</span>
          <span className="sa__v">
            {clocks.declared_clocks.map((c) => (
              <code
                key={c}
                className={
                  clocks.unconstrained_clocks.includes(c)
                    ? "op__clock op__clock--bad"
                    : "op__clock"
                }
              >
                {c}
              </code>
            ))}
          </span>
        </li>
        <li>
          <span className="sa__k">{t("op_constrained")}</span>
          <span className="sa__v">
            {clocks.constrained_clocks.length
              ? clocks.constrained_clocks.map((c) => (
                  <code key={c} className="op__clock">{c}</code>
                ))
              : "—"}
          </span>
        </li>
      </ul>
      {/* OpenLane's own warning, quoted rather than paraphrased — it is
          the tool saying it did not do the thing. */}
      {clocks.warnings.map((w) => (
        <p key={w} className="op__warning">{w}</p>
      ))}
      <p className="op__caveat">{clocks.note}</p>
    </div>
  );
}

export default function OperatingPointView({
  op,
  supplies,
  clocks,
}: {
  op: OperatingPoint | null | undefined;
  supplies?: SupplyRail[];
  clocks?: ClockCoverage;
}) {
  const { t } = useLang();
  if (!op && !supplies?.length && !clocks) return null;

  return (
    <div className="op">
      {op && (
        <div className="op__block">
          <h5>{t("op_title")}</h5>
          <div className="op__headline">
            <span>
              <span className="tab__meta-label">{t("op_fmax")}</span>
              <strong>{op.fmax_mhz != null ? `${op.fmax_mhz.toFixed(1)} MHz` : "—"}</strong>
              {op.fmax_limiting_corner && (
                <em>{t("op_limited_by")} {op.fmax_limiting_corner}</em>
              )}
            </span>
            <span>
              <span className="tab__meta-label">{t("op_constrained_at")}</span>
              <strong>
                {op.clock_period_ns
                  ? `${(1000 / op.clock_period_ns).toFixed(1)} MHz`
                  : "—"}
              </strong>
              {op.clock_period_ns && <em>{op.clock_period_ns} ns</em>}
            </span>
            <span>
              <span className="tab__meta-label">{t("op_vmin")}</span>
              <strong>{op.vmin_v != null ? `${op.vmin_v.toFixed(2)} V` : "—"}</strong>
              {/* Every corner passing means Vmin is bounded by the PDK's
                  corner set, not by this design. Stating it flatly would
                  claim a sweep nobody ran. */}
              {op.vmin_is_lowest_analysed && <em>{t("op_vmin_floor")}</em>}
            </span>
          </div>

          <div className="op__scroll">
            <table className="tab__summary op__table">
              <thead>
                <tr>
                  <th>{t("op_corner")}</th>
                  <th>V</th>
                  <th>{t("op_setup_slack")}</th>
                  <th>{t("op_hold_slack")}</th>
                  <th>{t("op_min_period")}</th>
                  <th>{t("op_fmax")}</th>
                </tr>
              </thead>
              <tbody>
                {op.corners.map((c) => {
                  const ok = c.setup_ok && c.hold_ok;
                  return (
                    <tr key={c.corner} className={ok ? undefined : "op__row--bad"}>
                      <td><code>{c.corner}</code></td>
                      <td>{c.voltage_v != null ? c.voltage_v.toFixed(2) : "—"}</td>
                      <td className={c.setup_ok ? undefined : "op__bad"}>
                        {c.setup_ws_ns != null ? `${c.setup_ws_ns.toFixed(3)} ns` : "—"}
                      </td>
                      <td className={c.hold_ok ? undefined : "op__bad"}>
                        {c.hold_ws_ns != null ? `${c.hold_ws_ns.toFixed(3)} ns` : "—"}
                      </td>
                      <td>{c.min_period_ns != null ? `${c.min_period_ns.toFixed(3)} ns` : "—"}</td>
                      <td>{c.fmax_mhz != null ? `${c.fmax_mhz.toFixed(1)}` : "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="op__caveat">{op.note}</p>
        </div>
      )}

      {supplies && supplies.length > 0 && <Rails supplies={supplies} />}
      {clocks && <Clocks clocks={clocks} />}
    </div>
  );
}
