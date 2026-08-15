import { useMemo, useState } from "react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer } from "recharts";
import ReportInput from "./ReportInput";
import { parseArea } from "../parsers/parseArea";
import { EXAMPLE_AREA_REPORT } from "../exampleReports";
import "./Tabs.css";

export default function AreaTab() {
  const [text, setText] = useState(EXAMPLE_AREA_REPORT);
  const result = useMemo(() => (text.trim() ? parseArea(text) : null), [text]);

  const chartData = result?.ok
    ? [
        {
          name: "Area",
          combinational:
            result.data.totalCombinationalArea - (result.data.totalBufInvArea ?? 0),
          bufInv: result.data.totalBufInvArea ?? 0,
          noncombinational: result.data.totalNoncombinationalArea,
          macro: result.data.totalMacroArea,
        },
      ]
    : [];

  return (
    <div className="tab">
      <ReportInput
        value={text}
        onChange={setText}
        onLoadExample={() => setText(EXAMPLE_AREA_REPORT)}
        placeholder="Paste Design Compiler report_area output here…"
      />
      {result && !result.ok && <div className="tab__error">{result.error}</div>}
      {result?.ok && (
        <>
          <table className="tab__summary">
            <tbody>
              <tr><td>Total cell area</td><td>{result.data.totalCellArea.toLocaleString()}</td></tr>
              <tr><td>Combinational</td><td>{result.data.totalCombinationalArea.toLocaleString()}</td></tr>
              <tr><td>Noncombinational</td><td>{result.data.totalNoncombinationalArea.toLocaleString()}</td></tr>
              <tr><td>Macro/black box</td><td>{result.data.totalMacroArea.toLocaleString()}</td></tr>
              {result.data.totalBufInvArea !== undefined && (
                <tr><td>buf/inv (subset of combinational)</td><td>{result.data.totalBufInvArea.toLocaleString()}</td></tr>
              )}
              {result.data.numCells !== undefined && (
                <tr><td>Number of cells</td><td>{result.data.numCells.toLocaleString()}</td></tr>
              )}
            </tbody>
          </table>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={chartData} layout="vertical">
              <XAxis type="number" />
              <YAxis type="category" dataKey="name" />
              <Tooltip />
              <Legend />
              <Bar dataKey="combinational" stackId="a" fill="#4f8ef7" name="Combinational" />
              <Bar dataKey="bufInv" stackId="a" fill="#a8c9fb" name="buf/inv" />
              <Bar dataKey="noncombinational" stackId="a" fill="#f7934f" name="Noncombinational" />
              <Bar dataKey="macro" stackId="a" fill="#7ed992" name="Macro/black box" />
            </BarChart>
          </ResponsiveContainer>
        </>
      )}
    </div>
  );
}
