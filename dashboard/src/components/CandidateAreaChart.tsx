import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

// Split out of PipelineTab.tsx and lazy-loaded from there — recharts'
// internal chunk (CategoricalChart) is ~88KB gzipped, the single
// largest JS chunk in the whole dashboard, and PipelineTab is the
// default/first tab every session loads. A static top-level `import
// ... from "recharts"` in PipelineTab.tsx forced that chunk to block
// the initial Pipeline-tab render even though this one small bar chart
// is a minor part of the page. As its own component behind
// `lazy(() => import(...))`, the chunk now loads in parallel with (not
// blocking) the rest of the case card's text/tables.
export interface CandidateAreaChartDatum {
  name: string;
  area: number;
  passed: boolean;
}

export default function CandidateAreaChart({ data }: { data: CandidateAreaChartDatum[] }) {
  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={data} margin={{ top: 18, right: 12, left: 6, bottom: 30 }}>
        <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="name" angle={-18} textAnchor="end" tick={{ fill: "var(--text-dim)", fontSize: 10 }} stroke="var(--border)" />
        <YAxis tick={{ fill: "var(--text-dim)", fontSize: 10 }} stroke="var(--border)" />
        <Tooltip contentStyle={{ background: "var(--surface-raised)", border: "1px solid var(--border)", borderRadius: 8, fontFamily: "var(--mono)" }} />
        <Bar dataKey="area" radius={[5, 5, 0, 0]}>
          {data.map((entry) => <Cell key={entry.name} fill={entry.passed ? "var(--good)" : "var(--critical)"} />)}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
