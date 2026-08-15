import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
} from "recharts";
import { TRADEOFFS } from "../tradeoffs";
import { useLang } from "../i18n";
import "./Tabs.css";
import "./TradeoffsTab.css";

const AXIS_TICKS = [-1, 0, 1];

function areaOrPowerColor(v: number) {
  if (v > 0) return "var(--critical)";
  if (v < 0) return "var(--good)";
  return "var(--text-dim)";
}

function timingColor(v: number) {
  if (v > 0) return "var(--good)";
  if (v < 0) return "var(--critical)";
  return "var(--text-dim)";
}

export default function TradeoffsTab() {
  const { t } = useLang();
  const chartData = TRADEOFFS.map((item) => ({
    technique: item.technique,
    area: item.area,
    timing: item.timing,
    power: item.power,
  }));

  return (
    <div className="tab">
      <div className="panel">
        <span className="panel__title">{t("ppa_triangle_title")}</span>
        <div className="panel__body">
          <div className="ppa-triangle">
            <svg viewBox="0 0 200 176" role="img" aria-label="PPA triangle: Power, Performance, Area">
              <polygon
                points="100,10 190,166 10,166"
                fill="var(--accent-soft)"
                stroke="var(--accent)"
                strokeWidth="1.5"
              />
              <line x1="100" y1="10" x2="100" y2="166" stroke="var(--border)" strokeDasharray="3 3" />
              <text x="100" y="4" textAnchor="middle" className="ppa-triangle__label">Power</text>
              <text x="8" y="176" textAnchor="start" className="ppa-triangle__label">Area</text>
              <text x="192" y="176" textAnchor="end" className="ppa-triangle__label">Performance</text>
            </svg>
            <p className="ppa-triangle__caption">{t("ppa_triangle_caption")}</p>
          </div>
        </div>
      </div>

      <div className="panel">
        <span className="panel__title">{t("tradeoffs_chart_title")}</span>
        <div className="panel__body">
          <div className="tradeoffs-legend">
            <span><span className="swatch swatch--good" /> {t("legend_improves")}</span>
            <span><span className="swatch swatch--critical" /> {t("legend_worsens")}</span>
            <span><span className="swatch swatch--neutral" /> {t("legend_neutral")}</span>
          </div>
          <ResponsiveContainer width="100%" height={TRADEOFFS.length * 90}>
            <BarChart data={chartData} layout="vertical" barGap={2} margin={{ left: 8 }}>
              <XAxis
                type="number"
                domain={[-1, 1]}
                ticks={AXIS_TICKS}
                tick={{ fill: "var(--text-dim)", fontSize: 10 }}
                stroke="var(--border)"
              />
              <YAxis
                type="category"
                dataKey="technique"
                width={140}
                tick={{ fill: "var(--text)", fontSize: 11 }}
                stroke="var(--border)"
              />
              <ReferenceLine x={0} stroke="var(--border)" />
              <Tooltip
                contentStyle={{
                  background: "var(--surface-raised)",
                  border: "1px solid var(--border)",
                  fontFamily: "var(--mono)",
                  fontSize: 12,
                }}
              />
              <Bar dataKey="area" name="Area" barSize={12}>
                {chartData.map((d, i) => (
                  <Cell key={i} fill={areaOrPowerColor(d.area)} />
                ))}
              </Bar>
              <Bar dataKey="timing" name="Timing (slack)" barSize={12}>
                {chartData.map((d, i) => (
                  <Cell key={i} fill={timingColor(d.timing)} />
                ))}
              </Bar>
              <Bar dataKey="power" name="Power" barSize={12}>
                {chartData.map((d, i) => (
                  <Cell key={i} fill={areaOrPowerColor(d.power)} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="panel">
        <span className="panel__title">{t("why_each_case")}</span>
        <div className="panel__body tradeoff-notes">
          {TRADEOFFS.map((item) => (
            <div key={item.technique} className="tradeoff-note">
              <div className="tradeoff-note__head">
                <strong>{item.technique}</strong>
                <code>{item.source}</code>
              </div>
              <p>{item.note}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
