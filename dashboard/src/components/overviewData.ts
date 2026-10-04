import type { CandidateResult, PipelineCase } from "../api/referenceDb";

export type CandidateState = "clean" | "violations" | "unknown" | "error";
export const CANDIDATE_STATES: CandidateState[] = ["clean", "violations", "unknown", "error"];

// An absent verdict/check is unknown, even if a legacy case says passed.
// Model coverage can block a candidate whose physical checks were clean.
export function candidateState(result: CandidateResult): CandidateState {
  if (result.not_evaluated) return "unknown";
  if (result.error) return "error";
  const verdict = result.verdict;
  if (!verdict) return "unknown";
  if (verdict.violations.length || verdict.signoff_checks?.some(check => check.count !== null && check.count > 0)) return "violations";
  if (verdict.unverified?.length || !verdict.signoff_checks?.length || verdict.signoff_checks.some(check => check.count === null || !Number.isFinite(check.count))) return "unknown";
  if (verdict.model_validity?.macro_arc_audit?.model_qualified === false) return "unknown";
  return verdict.passed ? "clean" : "unknown";
}

export function candidateSummary(cases: PipelineCase[]) {
  const counts: Record<CandidateState, number> = { clean: 0, violations: 0, unknown: 0, error: 0 };
  for (const c of cases) for (const iteration of c.iterations ?? []) {
    for (const result of iteration.results ?? []) if (!result.not_evaluated || result.screen_evaluation) counts[candidateState(result)]++;
  }
  return counts;
}
