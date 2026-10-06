// Pure data and derivations behind the Pipeline tab: stage ownership, verdict
// wording, repair-chain lookup and case ordering helpers. Split out of
// PipelineTab.tsx so node can load it without a stylesheet or React.
import type {
  CandidateResult,
  CandidateVerdict,
  PipelineCase,
  ProcessStageId,
} from "../api/referenceDb";
import { recordedAt } from "./caseGrouping";
import type { DictKey } from "../i18n";

// Fallback for cases written before orchestrator.py started including
// process_stages — keeps older reference-db entries renderable instead
// of crashing on a missing field.
export const FALLBACK_PROCESS_STAGES: { id: ProcessStageId; name: string }[] = [
  { id: "extraction", name: "Circuit & Layout Extraction" },
  { id: "topology", name: "Topology Understanding" },
  { id: "placement_strategy", name: "Placement Strategy / Candidate Generation" },
  { id: "physical_constraint", name: "Physical Constraint Evaluation" },
  { id: "routing_generation", name: "Routing Generation Evaluation" },
  { id: "routing_candidate", name: "Routing Candidate Generation" },
  { id: "verification_ppa", name: "Verification & PPA Evaluation" },
  { id: "feedback", name: "AI Feedback / Repair / Optimization" },
];

// Which real subagent (.claude/agents/*.md) owns each of the 8 pipeline
// stages, and what it actually does — paraphrased from each agent's own
// description/system-prompt file, not invented. Used both for the small
// "owner" badge on each ProcessStages card and the AgentRoles legend
// panel, so the two never drift (one source of truth).
export const STAGE_AGENT: Record<
  ProcessStageId,
  { agent: string; role: { en: string; ko: string } }
> = {
  extraction: {
    agent: "circuit-layout-extractor",
    role: {
      en: "Extracts a structural summary and topology signature from RTL (and any existing LEF/DEF layout) for the rest of the pipeline to reason about.",
      ko: "RTL(및 기존 LEF/DEF 레이아웃이 있다면 그것까지)에서 구조 요약과 토폴로지 시그니처를 추출해 나머지 파이프라인이 참고할 수 있게 합니다.",
    },
  },
  topology: {
    agent: "topology-analyst",
    role: {
      en: "Classifies the design's topology and surfaces similar past cases from reference-db/ before placement is proposed cold.",
      ko: "설계의 토폴로지를 분류하고, reference-db/에서 비슷한 과거 사례를 찾아 배치 전략가가 무(無)에서 시작하지 않도록 제공합니다.",
    },
  },
  placement_strategy: {
    agent: "placement-strategist",
    role: {
      en: "Proposes N real OpenLane config-override candidates (utilization, die sizing, macro hints) — every candidate gets a real run, no scoring without one.",
      ko: "실제 OpenLane 설정 오버라이드 후보(utilization, 다이 크기, 매크로 배치 힌트 등) N개를 제안합니다 — 모든 후보는 실제로 실행되며, 실행 없이 점수만 매기지 않습니다.",
    },
  },
  physical_constraint: {
    agent: "physical-constraint-evaluator",
    role: {
      en: "Flags real physical-constraint problems (density, legalization, PDN, congestion) before the expensive routing stage runs.",
      ko: "비용이 큰 라우팅 단계로 넘어가기 전에 실제 물리적 제약 문제(밀도, legalization, PDN, congestion)를 점검해 걸러냅니다.",
    },
  },
  routing_generation: {
    agent: "routing-candidate-evaluator",
    role: {
      en: "Reads TritonRoute's real routing output (DRC violations, wirelength, via count) for candidates that survived the placement check.",
      ko: "배치 검사를 통과한 후보의 실제 TritonRoute 라우팅 결과(DRC 위반, 배선 길이, via 개수)를 평가합니다.",
    },
  },
  routing_candidate: {
    agent: "routing-candidate-evaluator",
    role: {
      en: "Same agent as Routing Generation Evaluation — evaluates the detailed-routing result once a run reaches that step.",
      ko: "Routing Generation Evaluation과 동일 에이전트 — 실행이 detailed routing 단계에 도달하면 그 결과를 평가합니다.",
    },
  },
  verification_ppa: {
    agent: "verification-ppa-evaluator",
    role: {
      en: "Produces the final correctness + PPA verdict from a completed run's real metrics.json (area, all timing corners, power, DRC, LVS).",
      ko: "완료된 실행의 실제 metrics.json(면적, 전체 타이밍 코너, 파워, DRC, LVS)을 바탕으로 최종 정합성 + PPA 판정을 내립니다.",
    },
  },
  feedback: {
    agent: "feedback-optimizer",
    role: {
      en: "Closes the loop orchestrator.py's mechanical propose_repairs() can't: decides winner vs. next-iteration candidates, interprets each case.",
      ko: "orchestrator.py의 기계적 propose_repairs()가 처리하지 못하는 부분을 마무리합니다 — 승자 확정 또는 다음 iteration 후보 결정, 각 케이스에 대한 해석을 담당합니다.",
    },
  },
};

// Reverse index of STAGE_AGENT, keyed by agent name — lets any place
// that shows a bare agent name (e.g. a human-in-the-loop review pill)
// look up its role for a tooltip without a second copy of the text.
export const AGENT_ROLE_BY_NAME: Record<string, { en: string; ko: string }> = Object.fromEntries(
  Object.values(STAGE_AGENT).map((entry) => [entry.agent, entry.role])
);

// A verdict has three outcomes, not two: passed, rejected by the tools,
// or blocked because a signoff step never ran. The third was previously
// impossible to express — an absent DRC metric scored as clean, so it
// rendered as PASS.
export function verdictPill(
  v: CandidateVerdict | undefined
): { cls: string; text: string } {
  if (!v) return { cls: "pill--critical", text: "FAIL TO RUN" };
  if (v.passed) return { cls: "pill--good", text: "PASS" };
  if (v.violations.length === 0 && (v.unverified?.length ?? 0) > 0)
    return { cls: "pill--warn", text: "UNVERIFIED" };
  return { cls: "pill--critical", text: "FAIL" };
}

// When a cached draft was written, in the reader's own clock. Shown so
// "the model said this" carries a when — a draft from before the last
// run is about a case that has since moved.
export function formatCachedAt(iso: string): string {
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? iso : at.toLocaleString();
}

// The 8 stages grouped by the *kind of work* each one does. Grouping is
// not cosmetic: the flat grid gave a die-too-small floorplan crash, a
// candidate proposal, and the repair decision identical visual weight,
// so nothing on screen distinguished "reads the design" from "runs and
// judges a candidate" from "decides what happens next".
//
// The boundaries follow the real code rather than being tidy: stages
// 4-7 are exactly the ids classify_stage() can assign to a candidate
// (where its run actually got to), stage 3 is what placement-strategist
// proposes, and stage 8 is the only one tracked per-candidate by
// produced_by_feedback instead of by stage. See orchestrator.py.
export const PIPELINE_PHASES: {
  id: string;
  labelKey: DictKey;
  roleKey: DictKey;
  stages: ProcessStageId[];
}[] = [
  { id: "understand", labelKey: "phase_understand", roleKey: "phase_understand_role",
    stages: ["extraction", "topology"] },
  { id: "propose", labelKey: "phase_propose", roleKey: "phase_propose_role",
    stages: ["placement_strategy"] },
  { id: "evaluate", labelKey: "phase_evaluate", roleKey: "phase_evaluate_role",
    stages: ["physical_constraint", "routing_generation", "routing_candidate",
             "verification_ppa"] },
  { id: "decide", labelKey: "phase_decide", roleKey: "phase_decide_role",
    stages: ["feedback"] },
];

export const STAGE_SHORT_LABEL: Record<ProcessStageId, { short: string; full: string }> = {
  extraction: { short: "extract", full: "Circuit & Layout Extraction" },
  topology: { short: "topology", full: "Topology Understanding" },
  placement_strategy: { short: "placement", full: "Placement Strategy / Candidate Generation" },
  physical_constraint: { short: "phys. constraint", full: "Physical Constraint Evaluation" },
  routing_generation: { short: "routing gen.", full: "Routing Generation Evaluation" },
  routing_candidate: { short: "routing cand.", full: "Routing Candidate Generation" },
  verification_ppa: { short: "verify/PPA", full: "Verification & PPA Evaluation" },
  feedback: { short: "feedback", full: "AI Feedback / Repair / Optimization" },
};

export type FailureRecovery = {
  kind: "recovered" | "available";
  label: string;
  detail: string;
};

export function dieAreaDimensions(value: unknown): string | null {
  if (!Array.isArray(value) || value.length !== 4 ||
      !value.every((coordinate) => typeof coordinate === "number")) return null;
  const [x0, y0, x1, y1] = value as number[];
  return `${x1 - x0} × ${y1 - y0} µm`;
}

// A failed row is only one step in a bounded repair chain. Older case files
// predate per-candidate repair metadata, but their tags and overrides still
// preserve the chain, so connect the original failure to the measured result
// instead of leaving a large red log that looks terminal.
export function failureRecovery(
  candidate: CandidateResult,
  allCandidates: CandidateResult[]
): FailureRecovery | null {
  const error = candidate.error ?? "";
  if (!error.includes("STA-0572") || !error.includes("core_area")) return null;

  const descendants = allCandidates.filter(
    (other) => other.tag.startsWith(`${candidate.tag}-iter`)
  );
  const passed = descendants.find((other) => other.verdict?.passed);
  if (passed) {
    const dimensions = dieAreaDimensions(passed.overrides.DIE_AREA);
    return {
      kind: "recovered",
      label: "RECOVERED BY AUTO-REPAIR",
      detail: `DIE_AREA grew${dimensions ? ` to ${dimensions}` : ""}; ${passed.tag} passed the full flow.`,
    };
  }

  const proposed = descendants[0];
  const dimensions = dieAreaDimensions(proposed?.overrides.DIE_AREA);
  return {
    kind: "available",
    label: proposed ? "AUTO-REPAIR PROPOSED" : "REPAIR AVAILABLE",
    detail: proposed
      ? `The next iteration enlarges DIE_AREA${dimensions ? ` to ${dimensions}` : ""}.`
      : "Increase DIE_AREA. Absolute floorplan margins leave no positive core at the current size.",
  };
}

// The clock time a case was recorded, for a row that already sits under
// its date. Cases predating the timestamped filenames read as midnight,
// and showing "00:00" for them would be a time nobody measured — they
// get the date instead, which is all that was ever recorded.
export function caseStamp(c: PipelineCase): string {
  const at = recordedAt(c);
  return at.endsWith("T00:00:00") ? at.slice(0, 10) : at.slice(0, 16).replace("T", " ");
}

// Which knob keys the dictionary carries. Checked against a real set
// rather than a try/catch on t(), so a key added to one language and
// not the other fails the type check instead of rendering "undefined".
export const TRANSLATED_KNOBS: Record<string, true> = {
  knob_FP_CORE_UTIL: true,
  knob_SYNTH_STRATEGY: true,
  knob_CLOCK_PERIOD: true,
  knob_PL_TARGET_DENSITY_PCT: true,
  knob_DIE_AREA: true,
  knob_PNR_EXCLUDED_CELL_FILE: true,
  knob_PDK: true,
  knob_SCL: true,
};

// The candidates a winner was actually chosen among: the iteration that
// holds it. In a polish iteration the incumbent it was compared with sits
// in an earlier iteration, found by the tag the polish trials extend.
export function winnerFieldCandidates(pipelineCase: PipelineCase): CandidateResult[] {
  const winner = pipelineCase.winner_tag;
  if (!winner) return [];
  const idx = pipelineCase.iterations.findIndex((it) => it.results.some((r) => r.tag === winner));
  if (idx < 0) return [];
  const iter = pipelineCase.iterations[idx];
  const field = [...iter.results];
  if (iter.polish) {
    const marker = "-polish-";
    const bases = new Set(iter.results.map((r) => r.tag.split(marker)[0]));
    for (const earlier of pipelineCase.iterations.slice(0, idx)) {
      for (const r of earlier.results) if (bases.has(r.tag)) field.push(r);
    }
  }
  return field;
}

export function flattenCandidates(pipelineCase: PipelineCase): CandidateResult[] {
  return pipelineCase.iterations.flatMap((iteration) => iteration.results);
}

// The four evaluation gates in the order a candidate passes through
// them. Attrition is cumulative: whoever dies at 04 never reaches 05.
const GATE_ORDER: ProcessStageId[] = [
  "physical_constraint", "routing_generation",
  "routing_candidate", "verification_ppa",
];

export type GateFlow = Map<ProcessStageId, { entered: number; lost: number }>;

export interface StageSummary {
  candidates: CandidateResult[];
  measuredCandidates: CandidateResult[];
  feedbackCount: number;
  gateFlow: GateFlow;
}

// How many candidates entered each gate and how many were lost there.
//
// Fixes a real inversion, not just a wording nit. classify_stage() tags a
// candidate with the stage its run *ended* at, but the label said "reached
// this stage" — so "0/9 reached Routing Generation" read as total failure
// when it actually means nobody died at routing, and "6/9 reached
// Verification" understated that six candidates went all the way through.
// The pipeline looked broken while working.
export function summarizeStages(pipelineCase: PipelineCase): StageSummary {
  const candidates = flattenCandidates(pipelineCase);
  const measuredCandidates = candidates.filter((c) => !c.not_evaluated || c.screen_evaluation);
  const stageCounts: Partial<Record<ProcessStageId, number>> = {};
  for (const c of candidates) {
    if (c.stage) stageCounts[c.stage] = (stageCounts[c.stage] ?? 0) + 1;
  }
  const feedbackCount = candidates.filter((c) => c.produced_by_feedback).length;
  const gateFlow: GateFlow = new Map();
  let alive = measuredCandidates.length;
  for (const gate of GATE_ORDER) {
    const lost = gate === "verification_ppa" ? 0 : (stageCounts[gate] ?? 0);
    gateFlow.set(gate, { entered: alive, lost });
    alive -= lost;
  }
  return { candidates, measuredCandidates, feedbackCount, gateFlow };
}

export function stageCount(
  id: ProcessStageId,
  pipelineCase: PipelineCase,
  { candidates, measuredCandidates, feedbackCount, gateFlow }: StageSummary
): { count: number; total: number; note: string } {
  switch (id) {
    case "extraction":
      return { count: measuredCandidates.length, total: candidates.length, note: "evaluated candidates; inspect recorded artifacts" };
    case "topology":
      return pipelineCase.topology
        ? { count: 1, total: 1, note: `${pipelineCase.topology.has_macros ? "macro-heavy" : "std-cell only"}, ${pipelineCase.topology.sequential_element_estimate} flops` }
        : { count: 0, total: 1, note: "no topology.json for this design" };
    case "placement_strategy":
      return { count: candidates.length, total: candidates.length, note: `${candidates.length} candidate(s) proposed` };
    case "feedback":
      return { count: feedbackCount, total: candidates.length, note: feedbackCount > 0 ? `${feedbackCount} candidate(s) from auto-repair` : "no repair needed" };
    default: {
      const flow = gateFlow.get(id);
      if (!flow) return { count: 0, total: candidates.length, note: "" };
      const survived = flow.entered - flow.lost;
      const note = id === "verification_ppa"
        ? `${survived} of ${measuredCandidates.length} completed signoff`
        : flow.lost > 0
          ? `${flow.lost} of ${flow.entered} stopped here`
          // "all 1 passed" reads badly; say what actually happened.
          : flow.entered === 1
            ? "the 1 survivor passed"
            : `all ${flow.entered} passed`;
      return { count: flow.entered, total: candidates.length, note };
    }
  }
}

export interface CaseCounts {
  passed: number;
  deferred: number;
  screenedOnly: number;
  failed: number;
  unverified: number;
}

export function countCandidates(candidates: CandidateResult[]): CaseCounts {
  const passed = candidates.filter((candidate) => candidate.verdict?.passed).length;
  const deferred = candidates.filter((candidate) => candidate.not_evaluated).length;
  const screenedOnly = candidates.filter((candidate) => candidate.not_evaluated && candidate.screen_evaluation).length;
  const failed = candidates.length - deferred - passed;
  // Candidates blocked only because a signoff step never ran. They are
  // inside `failed`, but calling them "violated guardrails" is wrong —
  // nothing rejected them, nothing checked them. Counted so the note can
  // say which it was.
  const unverified = candidates.filter(
    (candidate) =>
      candidate.verdict &&
      !candidate.verdict.passed &&
      candidate.verdict.violations.length === 0 &&
      (candidate.verdict.unverified?.length ?? 0) > 0
  ).length;
  return { passed, deferred, screenedOnly, failed, unverified };
}

export function areaChartData(pipelineCase: PipelineCase) {
  return pipelineCase.iterations.flatMap((iteration) =>
    iteration.results
      .filter((candidate) => candidate.verdict?.area_um2 != null)
      .map((candidate) => ({
        name: candidate.tag,
        area: candidate.verdict?.area_um2 ?? 0,
        passed: Boolean(candidate.verdict?.passed),
        winner: candidate.tag === pipelineCase.winner_tag,
        iteration: iteration.iteration,
      }))
  );
}
