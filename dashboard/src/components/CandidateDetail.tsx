import type { CandidateDataPointers, CandidateResult, CandidateVerdict } from "../api/referenceDb";
import { useLang } from "../i18n";
import SlackChart from "./SlackChart";

const DATA_CATEGORY_ORDER: (keyof CandidateDataPointers)[] = [
  "circuit",
  "layout",
  "constraint_pdk",
  "verification",
];

export function DataPointers({ data }: { data: CandidateDataPointers }) {
  return (
    <div className="pipeline__data-pointers">
      {DATA_CATEGORY_ORDER.map((category) => {
        const fields = Object.entries(data[category] ?? {});
        const present = fields.filter(([, v]) => v != null);
        return (
          <div key={category} className="pipeline__data-category">
            <span className="tab__meta-label">{category.replace("_", " / ")}</span>
            {present.length === 0 ? (
              <span className="pipeline__data-missing">—</span>
            ) : (
              <ul>
                {present.map(([field, path]) => (
                  <li key={field} title={path ?? undefined}>
                    {field}
                  </li>
                ))}
              </ul>
            )}
          </div>
        );
      })}
    </div>
  );
}

// Every real PVT corner OpenLane actually analyzed (typically 9: {min,
// nom, max} x {ff_n40C_1v95, tt_025C_1v80, ss_100C_1v60}) — real setup/
// hold WNS per corner from metrics.json, not just the single worst value.
export function TimingCorners({ corners }: { corners: CandidateVerdict["timing_corners"] }) {
  if (corners.length === 0) return null;
  return (
    <div className="pipeline__timing">
      <span className="tab__meta-label">timing — real WNS per PVT corner</span>
      <SlackChart corners={corners} />
      <table className="tab__summary pipeline__timing-table">
        <thead>
          <tr>
            <th>corner</th>
            <th>setup WNS</th>
            <th>hold WNS</th>
          </tr>
        </thead>
        <tbody>
          {corners.map((c) => (
            <tr key={c.corner}>
              <td>{c.corner}</td>
              <td className={c.setup_wns < 0 ? "pipeline__timing-bad" : undefined}>
                {c.setup_wns.toFixed(3)} ns
              </td>
              <td className={c.hold_wns != null && c.hold_wns < 0 ? "pipeline__timing-bad" : undefined}>
                {c.hold_wns != null ? `${c.hold_wns.toFixed(3)} ns` : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// Real power (OpenLane's default-activity estimate — no VCD/SAIF
// annotation configured in this pipeline yet) and real IR-drop / supply-
// voltage numbers from the actual power grid OpenROAD generated for
// this candidate's power domain.
export function PowerSummary({ verdict }: { verdict: CandidateVerdict }) {
  const { power, power_domain: powerDomain } = verdict;
  if (!power && !powerDomain) return null;
  return (
    <div className="pipeline__power">
      <span className="tab__meta-label">power / power domain</span>
      <div className="tab__meta">
        {power && (
          <>
            <span>
              <span className="tab__meta-label">internal</span>
              {(power.internal_w! * 1000).toFixed(4)} mW
            </span>
            <span>
              <span className="tab__meta-label">switching</span>
              {(power.switching_w! * 1000).toFixed(4)} mW
            </span>
            <span>
              <span className="tab__meta-label">leakage</span>
              {(power.leakage_w! * 1e6).toFixed(4)} µW
            </span>
            <span>
              <span className="tab__meta-label">total</span>
              {(power.total_w! * 1000).toFixed(4)} mW
            </span>
          </>
        )}
        {powerDomain && (
          <>
            <span>
              <span className="tab__meta-label">supply voltage</span>
              {powerDomain.voltage_worst_v} V
            </span>
            <span>
              <span className="tab__meta-label">IR drop (worst)</span>
              {(powerDomain.ir_drop_worst_v! * 1000).toFixed(4)} mV
            </span>
            <span>
              <span className="tab__meta-label">IR drop (avg)</span>
              {(powerDomain.ir_drop_avg_v! * 1000).toFixed(4)} mV
            </span>
          </>
        )}
      </div>
    </div>
  );
}

// What the surrogate expected before this run, against what the run
// measured. One line, because that is what it is: an expectation being
// scored, not a result. A refused prediction says why, so a reader can
// tell "the store could not predict this" from "the model was wrong".
export function PredictionLine({ prediction }: { prediction: CandidateResult["prediction"] }) {
  const { t } = useLang();
  if (!prediction) return null;
  if ("error" in prediction && typeof prediction.error === "string") {
    return <p className="pipeline__prediction">{t("prediction_label")}: {prediction.error}</p>;
  }
  const fields = prediction as Record<string, { predicted: number | null; measured: number | null; error?: number; error_pct?: number | null; refused?: string }>;
  const parts: string[] = [];
  const area = fields["area_um2"];
  if (area) {
    parts.push(area.refused
      ? `${t("prediction_area")}: ${t("prediction_refused")} (${area.refused})`
      : `${t("prediction_area")}: ${area.predicted?.toFixed(1)} µm² → ${area.measured?.toFixed(1) ?? "—"}${area.error_pct != null ? ` (${area.error_pct >= 0 ? "+" : ""}${area.error_pct.toFixed(1)}%)` : ""}`);
  }
  const power = fields["power_w"];
  if (power && !power.refused && power.predicted != null) {
    parts.push(`${t("prediction_power")}: ${(power.predicted * 1000).toFixed(4)} mW → ${power.measured != null ? (power.measured * 1000).toFixed(4) : "—"}${power.error_pct != null ? ` (${power.error_pct >= 0 ? "+" : ""}${power.error_pct.toFixed(1)}%)` : ""}`);
  }
  if (parts.length === 0) return null;
  return (
    <p className="pipeline__prediction" title={t("prediction_hint")}>
      <span className="tab__meta-label">{t("prediction_label")}</span> {parts.join(" · ")}
    </p>
  );
}

// What placement and routing produced, in one line: the figures the winner
// is chosen on beyond cell area and power. Absent on cases recorded before
// score() kept them, in which case nothing is drawn rather than a dash row.
export function QualityLine({ verdict }: { verdict: CandidateVerdict }) {
  const core = verdict.core_area_um2 ??
    (verdict.area_um2 && verdict.utilization ? verdict.area_um2 / verdict.utilization : null);
  const bits: [string, string][] = [];
  if (core != null) bits.push(["core area", `${core.toFixed(0)} µm²`]);
  if (verdict.worst_setup_slack != null) bits.push(["setup slack", `${verdict.worst_setup_slack.toFixed(2)} ns`]);
  if (verdict.wirelength_um != null) bits.push(["routed wire", `${verdict.wirelength_um} µm`]);
  if (verdict.via_count != null) bits.push(["vias", String(verdict.via_count)]);
  if (bits.length === 0) return null;
  return (
    <p className="pipeline__quality">
      {bits.map(([k, val]) => (
        <span key={k}><span className="tab__meta-label">{k}</span>{val}</span>
      ))}
    </p>
  );
}
