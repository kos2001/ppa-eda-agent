import type { MacroArcAudit, MacroPvt } from "../api/referenceDb";
import { useLang } from "../i18n";
import "./MacroModelCoverage.css";

function complete(pvt?: MacroPvt | null): pvt is MacroPvt {
  return Boolean(pvt?.process && Number.isFinite(pvt.voltage_V) && Number.isFinite(pvt.temperature_C));
}

function displayPvt(pvt?: MacroPvt | null) {
  if (!complete(pvt)) return "—";
  return `${pvt.process} · ${pvt.voltage_V} V · ${pvt.temperature_C} °C`;
}

export default function MacroModelCoverage({ audit, tag }: { audit: MacroArcAudit; tag: string }) {
  const { t } = useLang();
  const mismatched = audit.corner_models.filter(r => complete(r.expected_pvt) && complete(r.declared_pvt) && !r.pvt_matches_declared).length;
  const unknown = audit.corner_models.filter(r => !complete(r.expected_pvt) || !complete(r.declared_pvt)).length;
  return (
    <section className="macro-coverage" aria-label={t("macro_model_title")}>
      <div className="macro-coverage__head">
        <strong>{t("macro_model_title")}</strong>
        <code>{tag}</code>
        <span className="macro-coverage__status">{audit.model_qualified ? t("macro_model_qualified") : t("macro_model_unqualified")}</span>
      </div>
      <div className="macro-coverage__metrics">
        <div><strong>{mismatched} / {audit.corner_models.length}</strong><span>{t("macro_pvt_mismatch")}{unknown > 0 ? ` · ${unknown} ${t("macro_unknown")}` : ""}</span></div>
        <div><strong>{audit.input_coverage_complete ? t("macro_complete") : t("macro_incomplete")}</strong><span>{t("macro_input_coverage")}</span></div>
        <div><strong>{audit.input_axis_extrapolation_count.toLocaleString()}</strong><span>{t("macro_axis_outside")}</span></div>
        <div><strong>{audit.unknown_input_check_count.toLocaleString()}</strong><span>{t("macro_unknown_checks")}</span></div>
      </div>
      <details>
        <summary>{t("macro_model_mapping")}</summary>
        <p className="macro-coverage__scroll-hint">{t("macro_scroll_hint")}</p>
        <div className="macro-coverage__scroll" role="region" aria-label={t("macro_model_mapping")} tabIndex={0}>
          <table>
            <thead><tr><th>{t("macro_corner")}</th><th>{t("macro_expected")}</th><th>{t("macro_declared")}</th><th>{t("macro_pvt_result")}</th></tr></thead>
            <tbody>{audit.corner_models.map(r => (
              <tr key={`${r.macro}/${r.corner}`}>
                <th scope="row"><code>{r.corner}</code><small>{r.macro}</small></th>
                <td>{displayPvt(r.expected_pvt)}</td>
                <td title={r.error}>{displayPvt(r.declared_pvt)}</td>
                <td className={r.pvt_matches_declared ? "" : "macro-coverage__mismatch"}>{!complete(r.expected_pvt) || !complete(r.declared_pvt) ? t("macro_unknown") : r.pvt_matches_declared ? t("macro_pvt_matches") : t("macro_pvt_differs")}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </details>
      <p>{t("macro_model_scope")}</p>
    </section>
  );
}
