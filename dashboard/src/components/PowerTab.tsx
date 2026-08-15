import { useMemo, useState } from "react";
import { PieChart, Pie, Cell, Tooltip, Legend, ResponsiveContainer } from "recharts";
import ReportInput from "./ReportInput";
import { parsePower } from "../parsers/parsePower";
import { EXAMPLE_POWER_REPORT } from "../exampleReports";
import "./Tabs.css";

const COLORS = ["#4f8ef7", "#f7934f", "#8c8c8c"];

export default function PowerTab() {
  const [text, setText] = useState("");
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
      <ReportInput
        value={text}
        onChange={setText}
        onLoadExample={() => setText(EXAMPLE_POWER_REPORT)}
        placeholder="Paste PrimePower report_power output here…"
      />
      {result && !result.ok && <div className="tab__error">{result.error}</div>}
      {result?.ok && (
        <>
          <table className="tab__summary">
            <tbody>
              <tr><td>Total power</td><td>{result.data.totalPowerMw.toFixed(4)} mW</td></tr>
              <tr><td>Total dynamic power</td><td>{result.data.totalDynamicPowerMw.toFixed(4)} mW</td></tr>
              <tr><td>Cell internal power</td><td>{result.data.cellInternalPowerMw.toFixed(4)} mW</td></tr>
              <tr><td>Net switching power</td><td>{result.data.netSwitchingPowerMw.toFixed(4)} mW</td></tr>
              <tr><td>Cell leakage power</td><td>{result.data.cellLeakagePowerMw.toFixed(4)} mW</td></tr>
            </tbody>
          </table>
          <ResponsiveContainer width="100%" height={250}>
            <PieChart>
              <Pie data={chartData} dataKey="value" nameKey="name" outerRadius={90} label>
                {chartData.map((_, i) => (
                  <Cell key={i} fill={COLORS[i % COLORS.length]} />
                ))}
              </Pie>
              <Tooltip />
              <Legend />
            </PieChart>
          </ResponsiveContainer>
        </>
      )}
    </div>
  );
}
