// How the case store is ordered and grouped for the record list.
//
// Split out of PipelineTab.tsx so node can load it: the component
// imports a stylesheet, and tests/case_grouping_check.mjs runs these
// against the real reference-db/cases/ store. Same reason
// markdownParse.ts is split out of Markdown.tsx.
//
// Everything here is derived, nothing is recorded. The store is not
// changed to support this view.
import type { PipelineCase } from "../api/referenceDb";

// Filenames are `<design>__<YYYY-MM-DD>.json` or
// `<design>__<YYYY-MM-DD>__<HHMMSS>.json`. The second form exists
// because collect.py writes one dated file per design per day and a
// re-run that day appends beside it rather than overwriting.
const STAMPED = /__(\d{4}-\d{2}-\d{2})__(\d{2})(\d{2})(\d{2})\.json$/;
const DATED = /__(\d{4}-\d{2}-\d{2})\.json$/;

/** When this case was recorded, as a string that sorts chronologically.
 *
 * The list calls itself "newest first" and could not deliver it from
 * `date` alone: 41 of 54 cases share 2026-08-30, so sorting on the date
 * left three quarters of the list in index.json's order. The time is in
 * the filename, which is why the server now sends it.
 *
 * A case with no time reads as midnight. That is a choice and not a
 * measurement — mtime would be a measurement but changes on checkout,
 * so it would order the list differently on a teammate's machine.
 * Midnight is stable, and it puts the dated file the batch wrote before
 * the re-runs that followed it that day.
 */
export function recordedAt(c: PipelineCase): string {
  const name = c.file ?? "";
  const stamped = STAMPED.exec(name);
  if (stamped) {
    const [, day, hh, mm, ss] = stamped;
    return `${day}T${hh}:${mm}:${ss}`;
  }
  const dated = DATED.exec(name);
  return `${dated ? dated[1] : c.date}T00:00:00`;
}

/** The knobs this case actually swept, as recorded key names.
 *
 * What tells one case of a design from another. counter4 has thirteen,
 * and grouping by design alone leaves them thirteen identical rows;
 * the difference is that one moved utilization, another the synthesis
 * strategy, another the whole PDK x strategy cross-product.
 *
 * Only keys whose values DIFFER across the candidates count. Reporting
 * every key present instead labels a case with the knobs it deliberately
 * held fixed, which is the opposite of what a sweep is.
 *
 * PDK and SCL sit beside `overrides` rather than inside it, because
 * OpenLane takes them as flags rather than config. They are folded in
 * here: a 36-run technology cross-product whose label omitted them would
 * read as a plain synthesis-strategy sweep.
 */
export function sweptAxis(c: PipelineCase): string[] {
  const seen = new Map<string, Set<string>>();
  for (const iteration of c.iterations ?? []) {
    for (const result of iteration.results ?? []) {
      const knobs: Record<string, unknown> = { ...(result.overrides ?? {}) };
      if (result.pdk) knobs.PDK = result.pdk;
      if (result.scl) knobs.SCL = result.scl;
      for (const [key, value] of Object.entries(knobs)) {
        const values = seen.get(key) ?? new Set<string>();
        values.add(JSON.stringify(value));
        seen.set(key, values);
      }
    }
  }
  return [...seen.entries()]
    .filter(([, values]) => values.size > 1)
    .map(([key]) => key)
    .sort();
}

/** How many real candidate runs this case holds. */
export function candidateCount(c: PipelineCase): number {
  return (c.iterations ?? []).reduce(
    (n, iteration) => n + (iteration.results?.length ?? 0), 0);
}

export interface DesignGroup {
  design: string;
  cases: PipelineCase[];
  /** Cases whose orchestrator run ended with a candidate meeting targets. */
  passed: number;
  /** Real candidate runs across every case in the group. */
  candidates: number;
}

/** The record list: one group per design, newest run first throughout.
 *
 * Groups are ordered by their own newest case rather than by name or
 * size, so the first row on screen is the newest run in the store — the
 * property the flat list was named for and did not have.
 */
export function groupByDesign(cases: PipelineCase[]): DesignGroup[] {
  const byDesign = new Map<string, PipelineCase[]>();
  for (const c of cases) {
    const bucket = byDesign.get(c.design) ?? [];
    bucket.push(c);
    byDesign.set(c.design, bucket);
  }

  const groups = [...byDesign.entries()].map(([design, group]) => {
    const sorted = [...group].sort(
      (a, b) => recordedAt(b).localeCompare(recordedAt(a)));
    return {
      design,
      cases: sorted,
      passed: sorted.filter((c) => c.outcome === "passed").length,
      candidates: sorted.reduce((n, c) => n + candidateCount(c), 0),
    };
  });

  return groups.sort((a, b) =>
    recordedAt(b.cases[0]).localeCompare(recordedAt(a.cases[0])));
}
