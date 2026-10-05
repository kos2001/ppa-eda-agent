import type { CandidateResult, PipelineCase } from "../api/referenceDb";
import { recordedAt } from "./caseGrouping";

export interface InspectionRow {
  id: string;
  candidate: CandidateResult;
  pipelineCase: PipelineCase;
  at: string;
  technology: string;
}

export function inspectionRows(cases: PipelineCase[]): InspectionRow[] {
  return cases.slice().sort((a, b) => recordedAt(b).localeCompare(recordedAt(a))).flatMap(c =>
    c.iterations.flatMap(it => it.results.map(r => ({ id: `${c.file ?? `${c.design}:${c.date}`}:${it.iteration}:${r.tag}`,
      candidate: r, pipelineCase: c, at: recordedAt(c),
      technology: `${r.pdk ?? "unrecorded PDK"} / ${r.scl ?? "unrecorded SCL"} / ${c.toolchain?.openlane_image ?? "unrecorded image"}` }))));
}

export const finiteMetric = (value: unknown): number | null => typeof value === "number" && Number.isFinite(value) ? value : null;

export function comparisonEvidence(rows: InspectionRow[]): "compatible" | "different" | "unknown" {
  if (rows.length < 2) return "unknown";
  const provenance = rows.map(row => row.candidate.evaluation_provenance);
  if (provenance.some(p => !p?.complete || !p.compatibility_key)) return "unknown";
  return new Set(provenance.map(p => p!.compatibility_key)).size === 1 ? "compatible" : "different";
}

export function checkState(value: unknown): "clean" | "violation" | "unknown" {
  const count = finiteMetric(value);
  return count === null || count < 0 || !Number.isInteger(count) ? "unknown" : count === 0 ? "clean" : "violation";
}
