import { useMemo, useState } from "react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, Cell, ResponsiveContainer } from "recharts";
import ReportInput from "./ReportInput";
import { parseTiming } from "../parsers/parseTiming";
import { EXAMPLE_TIMING_REPORT } from "../exampleReports";
import "./Tabs.css";

export default function TimingTab() {
  const [text, setText] = useState("");
  const result = useMemo(() => (text.trim() ? parseTiming(text) : null), [text]);

  const sortedPaths = result?.ok
    ? [...result.data.paths].sort((a, b) => a.slack - b.slack)
    : [];
  const wns = sortedPaths.length > 0 ? sortedPaths[0].slack : null;
  const violatedCount = sortedPaths.filter((p) => p.violated).length;

  const chartData = sortedPaths.map((p) => ({
    name: `${p.startpoint} → ${p.endpoint}`,
    slack: p.slack,
  }));

  return (
    <div className="tab">
      <ReportInput
        value={text}
        onChange={setText}
        onLoadExample={() => setText(EXAMPLE_TIMING_REPORT)}
        placeholder="Paste PrimeTime report_timing output here (one or more paths)…"
      />
      {result && !result.ok && <div className="tab__error">{result.error}</div>}
      {result?.ok && (
        <>
          <div className="tab__meta">
            <span>WNS: {wns !== null ? wns.toFixed(2) : "—"}</span>
            <span>Violated paths: {violatedCount} / {sortedPaths.length}</span>
          </div>
          <ResponsiveContainer width="100%" height={Math.max(150, sortedPaths.length * 40)}>
            <BarChart data={chartData} layout="vertical">
              <XAxis type="number" />
              <YAxis type="category" dataKey="name" width={220} tick={{ fontSize: 10 }} />
              <Tooltip />
              <Bar dataKey="slack">
                {chartData.map((entry, i) => (
                  <Cell key={i} fill={entry.slack < 0 ? "#e5484d" : "#3dd68c"} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </>
      )}
    </div>
  );
}
