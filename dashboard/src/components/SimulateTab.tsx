import { useEffect, useState } from "react";
import { fetchSimScript, runSimulation } from "../api/simulation";
import { parseTiming } from "../parsers/parseTiming";
import { parsePower } from "../parsers/parsePower";
import { useLang } from "../i18n";
import { useAgent } from "../agentContext";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import "./Tabs.css";
import "./SimulateTab.css";

export default function SimulateTab() {
  const { t } = useLang();
  const { key, serverConfigured, diagnosing, runDiagnosis } = useAgent();
  const isReady = Boolean(key) || serverConfigured;
  const [period, setPeriod] = useState(2.0);
  const [running, setRunning] = useState(false);
  const [output, setOutput] = useState<string | null>(null);
  // The script the server will run. Read from its template rather than
  // written out here, so what the page shows and what executes cannot
  // drift apart.
  const [script, setScript] = useState<string | null>(null);

  useEffect(() => {
    fetchSimScript().then(setScript).catch(() => setScript(null));
  }, []);
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
  const timingPaths = timingResult?.ok ? timingResult.data.paths : [];
  const worstSlack = timingPaths.length ? Math.min(...timingPaths.map((path) => path.slack)) : null;
  const violations = timingPaths.filter((path) => path.violated).length;
  const powerChart = powerResult?.ok
    ? [
        { name: "Internal", value: powerResult.data.cellInternalPowerMw, color: "var(--accent)" },
        { name: "Switching", value: powerResult.data.netSwitchingPowerMw, color: "var(--warn)" },
        { name: "Leakage", value: powerResult.data.cellLeakagePowerMw, color: "var(--text-dim)" },
      ]
    : [];

  return (
    <div className="tab">
      {/* What this page is. The title said "5-cell design, real Nangate45
          library" and nothing else, so why five cells, why no area, and
          how this differs from the pipeline were only in sim/README.md —
          available to whoever already knew to look for them. */}
      <p className="sim-intro">{t("sim_intro")}</p>
      <p className="sim-intro sim-intro--dim">{t("sim_why_small")}</p>

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
          <span className="sim-controls__hint">{t("sim_try")}</span>
        </div>
      </div>

      {script && (
        <details className="panel sim-script">
          <summary className="panel__title">{t("sim_runs_title")}</summary>
          <div className="panel__body">
            <pre className="sim-output">
              {script.replace("{{PERIOD}}", String(period))}
            </pre>
            <p className="sim-note">{t("sim_runs_note")}</p>
          </div>
        </details>
      )}

      {simError && (
        <div className="tab__error">
          {simError}. {t("sim_error_hint")}{" "}
          <code>node server/index.mjs</code> in the repo root, and is Docker
          running with the <code>openroad/opensta</code> image pulled?
        </div>
      )}

      {output && (
        <>
          <div className="metric-grid sim-metrics">
            <div className={`metric-card ${violations ? "metric-card--critical" : "metric-card--good"}`}><span className="metric-card__label">timing status</span><strong className="metric-card__value">{violations ? "VIOLATED" : "MET"}</strong><span className="metric-card__note">{violations} violating paths</span></div>
            <div className={`metric-card ${(worstSlack ?? 0) < 0 ? "metric-card--critical" : "metric-card--good"}`}><span className="metric-card__label">worst slack</span><strong className="metric-card__value">{worstSlack?.toFixed(3) ?? "—"}<span className="metric-card__unit">ns</span></strong><span className="metric-card__note">period target · {period.toFixed(2)} ns</span></div>
            <div className="metric-card"><span className="metric-card__label">total power</span><strong className="metric-card__value">{powerResult?.ok ? powerResult.data.totalPowerMw.toFixed(4) : "—"}<span className="metric-card__unit">mW</span></strong><span className="metric-card__note">Nangate45 typical corner</span></div>
            <div className="metric-card"><span className="metric-card__label">paths analyzed</span><strong className="metric-card__value">{timingPaths.length}</strong><span className="metric-card__note">real OpenSTA report</span></div>
          </div>
          <div className="sim-visual-grid">
            {timingResult?.ok && <div className="panel"><span className="panel__title">slack distribution</span><div className="panel__body"><ResponsiveContainer width="100%" height={260}><BarChart data={timingPaths.map((path) => ({ name: path.endpoint, slack: path.slack }))} layout="vertical" margin={{ left: 20 }}><CartesianGrid stroke="var(--border)" strokeDasharray="3 3" horizontal={false} /><XAxis type="number" tick={{ fill: "var(--text-dim)", fontSize: 10 }} /><YAxis type="category" dataKey="name" width={110} tick={{ fill: "var(--text-dim)", fontSize: 9 }} /><Tooltip contentStyle={{ background: "var(--surface-raised)", border: "1px solid var(--border)", borderRadius: 8 }} /><Bar dataKey="slack" radius={[0, 5, 5, 0]}>{timingPaths.map((path, index) => <Cell key={index} fill={path.violated ? "var(--critical)" : "var(--good)"} />)}</Bar></BarChart></ResponsiveContainer></div></div>}
            {powerResult?.ok && <div className="panel"><span className="panel__title">power composition</span><div className="panel__body"><ResponsiveContainer width="100%" height={260}><BarChart data={powerChart}><CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} /><XAxis dataKey="name" tick={{ fill: "var(--text-dim)", fontSize: 10 }} /><YAxis tick={{ fill: "var(--text-dim)", fontSize: 10 }} /><Tooltip contentStyle={{ background: "var(--surface-raised)", border: "1px solid var(--border)", borderRadius: 8 }} /><Bar dataKey="value" radius={[5, 5, 0, 0]}>{powerChart.map((item) => <Cell key={item.name} fill={item.color} />)}</Bar></BarChart></ResponsiveContainer></div></div>}
          </div>
          <details className="panel sim-raw-details"><summary className="panel__title">{t("sim_raw_output")}</summary><div className="panel__body"><pre className="sim-output">{output}</pre></div></details>
        </>
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

      {output && isReady && (
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

      {output && !isReady && (
        <div className="panel">
          <span className="panel__title">{t("sim_diagnosis_title")}</span>
          <div className="panel__body">
            <p className="sim-diagnosis-hint">{t("key_input_prompt")}</p>
          </div>
        </div>
      )}

      {/* The boundaries, at the bottom because they answer questions the
          page raises rather than ones it opens with. Each is a real
          limit with a reason, not a disclaimer: OpenSTA genuinely has no
          report_area, and these runs genuinely must not enter the case
          store. */}
      <div className="sim-scope">
        <section>
          <span className="sim-scope__title">{t("sim_scope_title")}</span>
          <ul>
            <li>{t("sim_scope_area")}</li>
            <li>{t("sim_scope_store")}</li>
            <li>{t("sim_scope_pnr")}</li>
          </ul>
        </section>
        <section>
          <span className="sim-scope__title">{t("sim_place_title")}</span>
          <p>{t("sim_place_body")}</p>
        </section>
      </div>
    </div>
  );
}
