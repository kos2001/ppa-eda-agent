// The objectives a winner is chosen on, for drawing — the browser's copy of
// pipeline/orchestrator.py's objective_table() and pareto.py's dominance.
//
// Kept in lock-step with the Python by tests/test_objectives_chart.py,
// which compares the two over the real reference-db store. Two copies of
// one definition of "better" is a way for the chart to say a candidate is
// on the front while the pipeline picked something else.

import type { CandidateResult, CandidateVerdict } from "../api/referenceDb";

export type ObjectiveName = "area" | "power" | "core" | "margin";

// Order matters: it is the order of the axes, and the one pareto_points()
// uses. Every objective is minimised, so slack is stored as its negative
// ("margin") exactly as the pipeline does.
export const OBJECTIVES: { name: ObjectiveName; label: string; unit: string }[] = [
  { name: "area", label: "cell area", unit: "µm²" },
  { name: "power", label: "power", unit: "µW" },
  { name: "core", label: "core area", unit: "µm²" },
  { name: "margin", label: "setup slack", unit: "ns" },
];

export interface ObjectivePoint {
  tag: string;
  // Raw minimised values, only for the objectives every point has.
  values: Partial<Record<ObjectiveName, number>>;
  // What to show a reader: slack as the positive number it is.
  display: Partial<Record<ObjectiveName, number>>;
  onFront: boolean;
}

function coreArea(v: CandidateVerdict): number | null {
  if (typeof v.core_area_um2 === "number" && Number.isFinite(v.core_area_um2) && v.core_area_um2 > 0) return v.core_area_um2;
  if (v.area_um2 && v.utilization && Number.isFinite(v.area_um2) && Number.isFinite(v.utilization) && v.area_um2 > 0 && v.utilization > 0) return v.area_um2 / v.utilization;
  return null;
}

function setupSlack(v: CandidateVerdict): number | null {
  if (v.worst_setup_slack != null) return typeof v.worst_setup_slack === "number" && Number.isFinite(v.worst_setup_slack) ? v.worst_setup_slack : null;
  const slacks = (v.operating_point?.corners ?? [])
    .map((c) => c.setup_ws_ns);
  return slacks.length && slacks.every(s => typeof s === "number" && Number.isFinite(s)) ? Math.min(...slacks as number[]) : null;
}

function powerW(c: CandidateResult): number | null {
  const v = c.verdict;
  return typeof v?.power?.total_w === "number" ? v.power.total_w : null;
}

// Power measured against a testbench's real activity, when the design has
// one. The pipeline ranks on it only when every passing candidate has it,
// because a measured number and a vectorless estimate differ by ~15% and
// must never be compared (orchestrator.pick_winner explains the spm case).
function annotatedPowerW(c: CandidateResult): number | null {
  const pa = (c.verdict as unknown as {
    power_activity?: { annotated?: { total?: { total_w?: number } } };
  } | undefined)?.power_activity;
  const w = pa?.annotated?.total?.total_w;
  return typeof w === "number" ? w : null;
}

export function isPassing(c: CandidateResult): boolean {
  return !c.not_evaluated && !c.error && !!c.verdict?.passed;
}

// a dominates b when it is no worse on every objective and better on one.
function dominates(a: number[], b: number[]): boolean {
  let better = false;
  for (let i = 0; i < a.length; i++) {
    if (a[i] > b[i]) return false;
    if (a[i] < b[i]) better = true;
  }
  return better;
}

/**
 * Objective points for the passing candidates, with Pareto-front flags.
 *
 * An objective that any passing candidate lacks is dropped for all of
 * them (the pipeline's never-a-mixture rule): a missing number must not
 * read as a good one, and must not stretch an axis either.
 */
export function objectivePoints(candidates: CandidateResult[]): {
  points: ObjectivePoint[];
  used: ObjectiveName[];
} {
  const passing = candidates.filter(isPassing);
  const useAnnotated = passing.length > 0 && passing.every((c) => Number.isFinite(annotatedPowerW(c)));
  const rows = passing.map((c) => {
    const v = c.verdict as CandidateVerdict;
    const slack = setupSlack(v);
    const power = useAnnotated ? annotatedPowerW(c) : powerW(c);
    return {
      tag: c.tag,
      area: v.area_um2 ?? null,
      power: power == null ? null : power * 1e6,
      core: coreArea(v),
      slack,
    };
  });
  const used = OBJECTIVES.map((o) => o.name).filter((name) =>
    rows.length > 0 && rows.every((r) => Number.isFinite(name === "margin" ? r.slack : r[name])),
  );
  const points: ObjectivePoint[] = rows.map((r) => {
    const values: ObjectivePoint["values"] = {};
    const display: ObjectivePoint["display"] = {};
    for (const name of used) {
      if (name === "margin") {
        values.margin = -(r.slack as number);
        display.margin = r.slack as number;
      } else {
        values[name] = r[name] as number;
        display[name] = r[name] as number;
      }
    }
    return { tag: r.tag, values, display, onFront: false };
  });
  const vectors = points.map((p) => used.map((n) => p.values[n] as number));
  points.forEach((p, i) => {
    p.onFront = !vectors.some((other, j) => j !== i && dominates(other, vectors[i]));
  });
  return { points, used };
}

/** Position of a value on an axis, 0 = best (top) .. 1 = worst (bottom),
 *  min-max over the points drawn. A flat axis sits mid-way rather than
 *  dividing by zero. */
export function axisPosition(values: number[], value: number): number {
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  return hi === lo ? 0.5 : (value - lo) / (hi - lo);
}
