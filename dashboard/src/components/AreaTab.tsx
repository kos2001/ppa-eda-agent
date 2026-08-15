import { useMemo, useState } from "react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer } from "recharts";
import ReportInput from "./ReportInput";
import { parseArea } from "../parsers/parseArea";
import { EXAMPLE_AREA_REPORT } from "../exampleReports";
import { useLang } from "../i18n";
import "./Tabs.css";

export default function AreaTab() {
  const { t } = useLang();
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
      <div className="panel">
        <span className="panel__title">report_area — input</span>
        <div className="panel__body">
          <ReportInput
            value={text}
            onChange={setText}
            onLoadExample={() => setText(EXAMPLE_AREA_REPORT)}
            placeholder="Paste Design Compiler report_area output here…"
          />
        </div>
      </div>

      {result && !result.ok && <div className="tab__error">{result.error}</div>}

      {result?.ok && (
        <>
          <div className="panel">
            <span className="panel__title">{t("key_metrics")}</span>
            <div className="panel__body">
              <table className="tab__summary">
                <tbody>
                  <tr><td>{t("total_cell_area")}</td><td>{result.data.totalCellArea.toLocaleString()}</td></tr>
                  <tr><td>{t("combinational")}</td><td>{result.data.totalCombinationalArea.toLocaleString()}</td></tr>
                  <tr><td>{t("noncombinational")}</td><td>{result.data.totalNoncombinationalArea.toLocaleString()}</td></tr>
                  <tr><td>{t("macro_area")}</td><td>{result.data.totalMacroArea.toLocaleString()}</td></tr>
                  {result.data.totalBufInvArea !== undefined && (
                    <tr><td>{t("bufinv_subset")}</td><td>{result.data.totalBufInvArea.toLocaleString()}</td></tr>
                  )}
                  {result.data.numCells !== undefined && (
                    <tr><td>{t("number_of_cells")}</td><td>{result.data.numCells.toLocaleString()}</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          <div className="panel">
            <span className="panel__title">{t("area_breakdown")}</span>
            <div className="panel__body">
              <ResponsiveContainer width="100%" height={180}>
                <BarChart data={chartData} layout="vertical">
                  <XAxis type="number" tick={{ fill: "var(--text-dim)", fontSize: 11 }} stroke="var(--border)" />
                  <YAxis type="category" dataKey="name" tick={{ fill: "var(--text-dim)", fontSize: 11 }} stroke="var(--border)" />
                  <Tooltip contentStyle={{ background: "var(--surface-raised)", border: "1px solid var(--border)", fontFamily: "var(--mono)", fontSize: 12 }} />
                  <Legend wrapperStyle={{ fontFamily: "var(--mono)", fontSize: 11 }} />
                  <Bar dataKey="combinational" stackId="a" fill="var(--accent)" name={t("combinational")} />
                  <Bar dataKey="bufInv" stackId="a" fill="var(--accent-soft)" name="buf/inv" />
                  <Bar dataKey="noncombinational" stackId="a" fill="var(--warn)" name={t("noncombinational")} />
                  <Bar dataKey="macro" stackId="a" fill="var(--good)" name={t("macro_area")} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
