import { useMemo, useState } from "react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, Cell, ResponsiveContainer } from "recharts";
import ReportInput from "./ReportInput";
import { parseTiming } from "../parsers/parseTiming";
import { EXAMPLE_TIMING_REPORT } from "../exampleReports";
import { useLang } from "../i18n";
import "./Tabs.css";

export default function TimingTab() {
  const { t } = useLang();
  const [text, setText] = useState(EXAMPLE_TIMING_REPORT);
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
      <div className="panel">
        <span className="panel__title">report_timing — input</span>
        <div className="panel__body">
          <ReportInput
            value={text}
            onChange={setText}
            onLoadExample={() => setText(EXAMPLE_TIMING_REPORT)}
            placeholder="Paste PrimeTime report_timing output here (one or more paths)…"
          />
        </div>
      </div>

      {result && !result.ok && <div className="tab__error">{result.error}</div>}

      {result?.ok && (
        <>
          <div className="panel">
            <span className="panel__title">{t("timing_summary")}</span>
            <div className="panel__body">
              <div className="tab__meta">
                <span>
                  <span className="tab__meta-label">{t("wns")}</span>
                  {wns !== null ? wns.toFixed(2) : "—"} ns
                </span>
                <span>
                  <span className="tab__meta-label">{t("violated_paths")}</span>
                  <span className={violatedCount > 0 ? "pill pill--critical" : "pill pill--good"}>
                    {violatedCount} / {sortedPaths.length}
                  </span>
                </span>
              </div>
            </div>
          </div>

          <div className="panel">
            <span className="panel__title">{t("slack_per_path")}</span>
            <div className="panel__body">
              <ResponsiveContainer width="100%" height={Math.max(150, sortedPaths.length * 42)}>
                <BarChart data={chartData} layout="vertical">
                  <XAxis type="number" tick={{ fill: "var(--text-dim)", fontSize: 11 }} stroke="var(--border)" />
                  <YAxis type="category" dataKey="name" width={230} tick={{ fill: "var(--text-dim)", fontSize: 10 }} stroke="var(--border)" />
                  <Tooltip contentStyle={{ background: "var(--surface-raised)", border: "1px solid var(--border)", fontFamily: "var(--mono)", fontSize: 12 }} />
                  <Bar dataKey="slack">
                    {chartData.map((entry, i) => (
                      <Cell key={i} fill={entry.slack < 0 ? "var(--critical)" : "var(--good)"} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
