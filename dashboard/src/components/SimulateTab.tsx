import { useState } from "react";
import { runSimulation } from "../api/simulation";
import { parseTiming } from "../parsers/parseTiming";
import { parsePower } from "../parsers/parsePower";
import { useLang } from "../i18n";
import { useAgent } from "../agentContext";
import "./Tabs.css";
import "./SimulateTab.css";

export default function SimulateTab() {
  const { t } = useLang();
  const { key, diagnosing, runDiagnosis } = useAgent();
  const [period, setPeriod] = useState(2.0);
  const [running, setRunning] = useState(false);
  const [output, setOutput] = useState<string | null>(null);
  const [simError, setSimError] = useState<string | null>(null);

  async function handleRun() {
    setRunning(true);
    setSimError(null);
    setOutput(null);
    try {
      const result = await runSimulation(period);
      setOutput(result);
    } catch (e) {
      setSimError(String(e));
    } finally {
      setRunning(false);
    }
  }

  const timingResult = output ? parseTiming(output) : null;
  const powerResult = output ? parsePower(output) : null;

  return (
    <div className="tab">
      <div className="panel">
        <span className="panel__title">{t("sim_panel_title")}</span>
        <div className="panel__body sim-controls">
          <label className="sim-controls__field">
            <span className="tab__meta-label">{t("sim_clock_period")}</span>
            <input
              type="number"
              step="0.05"
              min="0.05"
              value={period}
              onChange={(e) => setPeriod(parseFloat(e.target.value) || 0)}
            />
          </label>
          <button onClick={handleRun} disabled={running}>
            {running ? t("sim_running") : t("sim_run")}
          </button>
          <span className="sim-controls__hint">{t("sim_hint")}</span>
        </div>
      </div>

      {simError && (
        <div className="tab__error">
          {simError}. {t("sim_error_hint")}{" "}
          <code>node server/index.mjs</code> in the repo root, and is Docker
          running with the <code>openroad/opensta</code> image pulled?
        </div>
      )}

      {output && (
        <div className="panel">
          <span className="panel__title">{t("sim_raw_output")}</span>
          <div className="panel__body">
            <pre className="sim-output">{output}</pre>
          </div>
        </div>
      )}

      {output && timingResult?.ok && (
        <div className="panel">
          <span className="panel__title">{t("sim_parsed_timing")}</span>
          <div className="panel__body">
            <table className="tab__summary">
              <tbody>
                {timingResult.data.paths.map((p, i) => (
                  <tr key={i}>
                    <td>
                      {p.startpoint} → {p.endpoint}
                    </td>
                    <td>
                      <span
                        className={
                          p.violated ? "pill pill--critical" : "pill pill--good"
                        }
                      >
                        {p.slack.toFixed(2)} ns ({p.violated ? "VIOLATED" : "MET"})
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {output && powerResult?.ok && (
        <div className="panel">
          <span className="panel__title">{t("sim_parsed_power")}</span>
          <div className="panel__body">
            <table className="tab__summary">
              <tbody>
                <tr>
                  <td>{t("total_power")}</td>
                  <td>{powerResult.data.totalPowerMw.toFixed(6)} mW</td>
                </tr>
                <tr>
                  <td>{t("cell_internal_power")}</td>
                  <td>{powerResult.data.cellInternalPowerMw.toFixed(6)} mW</td>
                </tr>
                <tr>
                  <td>{t("net_switching_power")}</td>
                  <td>{powerResult.data.netSwitchingPowerMw.toFixed(6)} mW</td>
                </tr>
                <tr>
                  <td>{t("cell_leakage_power")}</td>
                  <td>{powerResult.data.cellLeakagePowerMw.toFixed(6)} mW</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      )}

      {output && key && (
        <div className="panel">
          <span className="panel__title">{t("sim_diagnosis_title")}</span>
          <div className="panel__body">
            <div className="report-input__actions">
              <button onClick={() => runDiagnosis(output)} disabled={diagnosing}>
                {diagnosing ? t("sim_diagnosing") : t("sim_diagnose_button")}
              </button>
            </div>
            <p className="sim-diagnosis-hint">→ {t("agent_sidebar_title")}</p>
          </div>
        </div>
      )}

      {output && !key && (
        <div className="panel">
          <span className="panel__title">{t("sim_diagnosis_title")}</span>
          <div className="panel__body">
            <p className="sim-diagnosis-hint">{t("key_input_prompt")}</p>
          </div>
        </div>
      )}
    </div>
  );
}
