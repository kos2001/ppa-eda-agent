import type { CSSProperties } from "react";
import "./CandidateAreaChart.css";

export interface CandidateAreaChartDatum {
  name: string;
  area: number;
  passed: boolean;
  winner: boolean;
  iteration: number;
}

function areaLabel(value: number): string {
  return `${value >= 1000 ? value.toFixed(0) : value.toFixed(1)} µm²`;
}

export default function CandidateAreaChart({ data }: { data: CandidateAreaChartDatum[] }) {
  const max = Math.max(...data.map((d) => d.area), 1);
  const passing = data.filter((d) => d.passed);
  const bestPassing = passing.length ? Math.min(...passing.map((d) => d.area)) : null;

  return (
    <div className="area-chart" role="list" aria-label="Candidate cell area comparison">
      <div className="area-chart__axis" aria-hidden="true">
        <span>0</span>
        <span>cell area · lower is better</span>
        <span>{areaLabel(max)}</span>
      </div>
      <div className="area-chart__rows">
        {data.map((entry) => {
          const ratio = entry.area / max;
          const delta = bestPassing == null ? null : ((entry.area - bestPassing) / bestPassing) * 100;
          const style = { "--area-ratio": ratio } as CSSProperties;
          const status = entry.winner ? "winner" : entry.passed ? "passed" : "rejected";
          return (
            <div
              className={`area-chart__row ${entry.winner ? "area-chart__row--winner" : ""}`}
              key={`${entry.iteration}-${entry.name}`}
              role="listitem"
              aria-label={`${entry.name}, iteration ${entry.iteration}, ${areaLabel(entry.area)}, ${status}`}
            >
              <div className="area-chart__identity" title={entry.name}>
                <span className="area-chart__name">{entry.name}</span>
                <span className="area-chart__iteration">I{entry.iteration}</span>
              </div>
              <div className="area-chart__track" aria-hidden="true">
                <span
                  className={`area-chart__bar area-chart__bar--${entry.passed ? "passed" : "rejected"}`}
                  style={style}
                />
              </div>
              <strong className="area-chart__value">{areaLabel(entry.area)}</strong>
              <span className={`area-chart__status area-chart__status--${status}`}>
                {entry.winner ? "WINNER" : entry.passed ? "PASS" : "FAIL"}
              </span>
              <span className="area-chart__delta">
                {entry.passed && delta != null
                  ? Math.abs(delta) < 0.01
                    ? "best passing area"
                    : `+${delta.toFixed(1)}% vs best pass`
                  : "excluded from winner field"}
              </span>
            </div>
          );
        })}
      </div>
      <div className="area-chart__legend" aria-hidden="true">
        <span><i className="area-chart__key area-chart__key--winner" />winner</span>
        <span><i className="area-chart__key area-chart__key--passed" />signoff pass</span>
        <span><i className="area-chart__key area-chart__key--rejected" />rejected / incomplete</span>
      </div>
    </div>
  );
}
