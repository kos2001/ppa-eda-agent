import { useState } from "react";
import { runSimulation } from "../api/simulation";
import {
  getStoredKey,
  setStoredKey,
  clearStoredKey,
  diagnose,
} from "../api/gateway";
import { parseTiming } from "../parsers/parseTiming";
import { parsePower } from "../parsers/parsePower";
import "./Tabs.css";
import "./SimulateTab.css";

export default function SimulateTab() {
  const [period, setPeriod] = useState(2.0);
  const [running, setRunning] = useState(false);
  const [output, setOutput] = useState<string | null>(null);
  const [simError, setSimError] = useState<string | null>(null);

  const [key, setKey] = useState<string | null>(getStoredKey());
  const [keyInput, setKeyInput] = useState("");
  const [diagnosing, setDiagnosing] = useState(false);
  const [diagnosis, setDiagnosis] = useState<string | null>(null);
  const [diagnosisError, setDiagnosisError] = useState<string | null>(null);

  async function handleRun() {
    setRunning(true);
    setSimError(null);
    setOutput(null);
    setDiagnosis(null);
    setDiagnosisError(null);
    try {
      const result = await runSimulation(period);
      setOutput(result);
    } catch (e) {
      setSimError(String(e));
    } finally {
      setRunning(false);
    }
  }

  function handleSaveKey() {
    if (!keyInput.trim()) return;
    setStoredKey(keyInput.trim());
    setKey(keyInput.trim());
    setKeyInput("");
  }

  function handleClearKey() {
    clearStoredKey();
    setKey(null);
  }

  async function handleDiagnose() {
    if (!output || !key) return;
    setDiagnosing(true);
    setDiagnosisError(null);
    try {
      const result = await diagnose(key, output);
      setDiagnosis(result);
    } catch (e) {
      setDiagnosisError(String(e));
    } finally {
      setDiagnosing(false);
    }
  }

  const timingResult = output ? parseTiming(output) : null;
  const powerResult = output ? parsePower(output) : null;

  return (
    <div className="tab">
      <div className="panel">
        <span className="panel__title">
          live OpenSTA simulation — 5-cell design, real Nangate45 library
        </span>
        <div className="panel__body sim-controls">
          <label className="sim-controls__field">
            <span className="tab__meta-label">Clock period (ns)</span>
            <input
              type="number"
              step="0.05"
              min="0.05"
              value={period}
              onChange={(e) => setPeriod(parseFloat(e.target.value) || 0)}
            />
          </label>
          <button onClick={handleRun} disabled={running}>
            {running ? "Running OpenSTA…" : "Run simulation"}
          </button>
          <span className="sim-controls__hint">
            Tightening the period below ~0.13ns will produce a real timing
            violation — try it.
          </span>
        </div>
      </div>

      {simError && (
        <div className="tab__error">
          {simError}. Is the simulation server running?{" "}
          <code>node server/index.mjs</code> in the repo root, and is Docker
          running with the <code>openroad/opensta</code> image pulled?
        </div>
      )}

      {output && (
        <div className="panel">
          <span className="panel__title">raw OpenSTA output</span>
          <div className="panel__body">
            <pre className="sim-output">{output}</pre>
          </div>
        </div>
      )}

      {output && timingResult?.ok && (
        <div className="panel">
          <span className="panel__title">parsed timing</span>
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
          <span className="panel__title">parsed power</span>
          <div className="panel__body">
            <table className="tab__summary">
              <tbody>
                <tr>
                  <td>Total power</td>
                  <td>{powerResult.data.totalPowerMw.toFixed(6)} mW</td>
                </tr>
                <tr>
                  <td>Internal</td>
                  <td>{powerResult.data.cellInternalPowerMw.toFixed(6)} mW</td>
                </tr>
                <tr>
                  <td>Switching</td>
                  <td>{powerResult.data.netSwitchingPowerMw.toFixed(6)} mW</td>
                </tr>
                <tr>
                  <td>Leakage</td>
                  <td>{powerResult.data.cellLeakagePowerMw.toFixed(6)} mW</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      )}

      {output && (
        <div className="panel">
          <span className="panel__title">ppa-eda-analyst — live diagnosis</span>
          <div className="panel__body">
            {!key && (
              <div className="chat-setup">
                <p>Enter your hermes-gateway client key to get a live diagnosis.</p>
                <p className="chat-setup__hint">
                  Stored only in this browser's localStorage.
                </p>
                <input
                  type="password"
                  value={keyInput}
                  onChange={(e) => setKeyInput(e.target.value)}
                  placeholder="gw-..."
                />
                <div className="report-input__actions">
                  <button onClick={handleSaveKey}>Save key</button>
                </div>
              </div>
            )}
            {key && !diagnosis && (
              <div className="report-input__actions">
                <button onClick={handleDiagnose} disabled={diagnosing}>
                  {diagnosing ? "ppa-eda-analyst is thinking…" : "Diagnose this result"}
                </button>
                <button onClick={handleClearKey} className="chat-panel__clear-key">
                  Clear key
                </button>
              </div>
            )}
            {diagnosisError && (
              <div className="tab__error">{diagnosisError}</div>
            )}
            {diagnosis && <div className="sim-diagnosis">{diagnosis}</div>}
          </div>
        </div>
      )}
    </div>
  );
}
