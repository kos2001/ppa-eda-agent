import type { PipelineCase } from "../api/referenceDb";

// What a candidate run costs, per design, from runs already recorded.
//
// The manual answered "how long does a run take?" with a sentence typed
// by hand — "about 27 s for counter4" — and the first feedback entry
// this console ever received said that sentence could not tell them
// what a *large* design costs. It could not: aes takes twenty minutes
// under contention, and no hand-typed number was going to keep up with
// a store that grows by hundreds of runs a day.
//
// The number already exists. collect.py's run_one() writes `seconds` on
// every result and its recorded_seconds() takes the per-design median
// to plan batches. This is the same computation on the same field, in
// the browser, so the manual and the action rows show what the store
// measured rather than what someone remembered. tests/test_run_cost.py
// pins the two implementations to each other.
//
// Median, not mean, for the reason collect.py gives: a run killed
// mid-batch or one that ran under three-way contention should not drag
// the typical figure with it.

export interface RunCost {
  design: string;
  medianSeconds: number;
  runs: number;
  // Architectures the timed runs came from. A number measured on an
  // arm64 laptop is a property of that machine as much as the design —
  // 68 s emulated against 28 s native on the same counter4 — so the
  // figure is shown with where it came from.
  hosts: string[];
}

export function runCosts(cases: PipelineCase[]): Record<string, RunCost> {
  const per = new Map<string, { seconds: number[]; hosts: Set<string> }>();
  for (const c of cases) {
    const host = c.toolchain?.host;
    const hostLabel = host
      ? `${host.arch ?? "?"}/${host.docker_platform ?? "native"}`
      : null;
    for (const it of c.iterations ?? []) {
      for (const r of it.results ?? []) {
        if (typeof r.seconds !== "number" || !Number.isFinite(r.seconds)) continue;
        const row = per.get(c.design) ?? { seconds: [], hosts: new Set<string>() };
        row.seconds.push(r.seconds);
        if (hostLabel) row.hosts.add(hostLabel);
        per.set(c.design, row);
      }
    }
  }
  const out: Record<string, RunCost> = {};
  for (const [design, row] of per) {
    // Same median as collect.recorded_seconds(): sort, take the middle
    // element (upper on an even count). Kept identical rather than
    // "more correct" so the two cannot disagree by a rounding rule.
    const sorted = row.seconds.slice().sort((a, b) => a - b);
    out[design] = {
      design,
      medianSeconds: sorted[Math.floor(sorted.length / 2)],
      runs: sorted.length,
      hosts: [...row.hosts].sort(),
    };
  }
  return out;
}

// "27 s", "3.4 min", "1.2 h" — the unit a person would pick.
export function formatSeconds(s: number): string {
  if (s < 90) return `${Math.round(s)} s`;
  if (s < 5400) return `${(s / 60).toFixed(1)} min`;
  return `${(s / 3600).toFixed(1)} h`;
}
