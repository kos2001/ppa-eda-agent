import { useMemo, useState } from "react";
import { PieChart, Pie, Cell, Tooltip, Legend, ResponsiveContainer } from "recharts";
import ReportInput from "./ReportInput";
import { parsePower } from "../parsers/parsePower";
import { EXAMPLE_POWER_REPORT } from "../exampleReports";
import "./Tabs.css";

const SLICE_COLORS = ["var(--accent)", "var(--warn)", "var(--text-dim)"];

export default function PowerTab() {
  const [text, setText] = useState(EXAMPLE_POWER_REPORT);
  const result = useMemo(() => (text.trim() ? parsePower(text) : null), [text]);

  const chartData = result?.ok
    ? [
        { name: "Internal", value: result.data.cellInternalPowerMw },
        { name: "Switching", value: result.data.netSwitchingPowerMw },
        { name: "Leakage", value: result.data.cellLeakagePowerMw },
      ]
    : [];

  return (
    <div className="tab">
      <div className="panel">
        <span className="panel__title">report_power — input</span>
        <div className="panel__body">
          <ReportInput
            value={text}
            onChange={setText}
            onLoadExample={() => setText(EXAMPLE_POWER_REPORT)}
            placeholder="Paste PrimePower report_power output here…"
          />
        </div>
      </div>

      {result && !result.ok && <div className="tab__error">{result.error}</div>}

      {result?.ok && (
        <>
          <div className="panel">
            <span className="panel__title">key metrics</span>
            <div className="panel__body">
              <table className="tab__summary">
                <tbody>
                  <tr><td>Total power</td><td>{result.data.totalPowerMw.toFixed(4)} mW</td></tr>
                  <tr><td>Total dynamic power</td><td>{result.data.totalDynamicPowerMw.toFixed(4)} mW</td></tr>
                  <tr><td>Cell internal power</td><td>{result.data.cellInternalPowerMw.toFixed(4)} mW</td></tr>
                  <tr><td>Net switching power</td><td>{result.data.netSwitchingPowerMw.toFixed(4)} mW</td></tr>
                  <tr><td>Cell leakage power</td><td>{result.data.cellLeakagePowerMw.toFixed(4)} mW</td></tr>
                </tbody>
              </table>
            </div>
          </div>

          <div className="panel">
            <span className="panel__title">power breakdown</span>
            <div className="panel__body">
              <ResponsiveContainer width="100%" height={230}>
                <PieChart>
                  <Pie data={chartData} dataKey="value" nameKey="name" outerRadius={85} label>
                    {chartData.map((_, i) => (
                      <Cell key={i} fill={SLICE_COLORS[i % SLICE_COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip contentStyle={{ background: "var(--surface-raised)", border: "1px solid var(--border)", fontFamily: "var(--mono)", fontSize: 12 }} />
                  <Legend wrapperStyle={{ fontFamily: "var(--mono)", fontSize: 11 }} />
                </PieChart>
              </ResponsiveContainer>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
