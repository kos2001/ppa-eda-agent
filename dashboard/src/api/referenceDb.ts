export const REFERENCE_DB_URL = "http://127.0.0.1:8123/reference-db";

export interface TimingCorner {
  corner: string;
  setup_wns: number;
  hold_wns: number | null;
}

export interface Power {
  internal_w: number | null;
  leakage_w: number | null;
  switching_w: number | null;
  total_w: number | null;
}

export interface PowerDomain {
  ir_drop_avg_v: number | null;
  ir_drop_worst_v: number | null;
  voltage_worst_v: number | null;
}

export interface CandidateVerdict {
  passed: boolean;
  violations: string[];
  area_um2: number | null;
  utilization: number | null;
  worst_setup_wns: number;
  timing_corners: TimingCorner[];
  power: Power | null;
  power_domain: PowerDomain | null;
}

export interface LayoutCell {
  inst: string;
  master: string;
  x: number;
  y: number;
  w: number;
  h: number;
  orient: string;
}

export interface LayoutNetSegment {
  layer: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

export interface LayoutNet {
  name: string;
  segments: LayoutNetSegment[];
}

export interface LayoutSummary {
  die: [number, number, number, number] | null;
  cells: LayoutCell[];
  nets: LayoutNet[];
}

export interface CandidateDataPointers {
  circuit: Record<string, string | null>;
  layout: Record<string, string | null>;
  constraint_pdk: Record<string, string | null>;
  verification: Record<string, string | null>;
}

// The 8 process-stage ids orchestrator.py's classify_stage() can assign —
// keep in sync with pipeline/orchestrator.py's PROCESS_STAGES.
export type ProcessStageId =
  | "extraction"
  | "topology"
  | "placement_strategy"
  | "physical_constraint"
  | "routing_generation"
  | "routing_candidate"
  | "verification_ppa"
  | "feedback";

export interface ProcessStage {
  id: ProcessStageId;
  name: string;
}

export interface Topology {
  module_count: number;
  has_macros: boolean;
  clock_domain_count: number;
  port_count: number;
  sequential_element_estimate: number;
  power_domain_count: number;
  notes: string;
}

export interface CandidateResult {
  tag: string;
  overrides: Record<string, unknown>;
  verdict?: CandidateVerdict;
  error?: string;
  run_dir?: string;
  data?: CandidateDataPointers;
  layout?: LayoutSummary | null;
  stage?: ProcessStageId;
  produced_by_feedback?: boolean;
}

export interface IterationResult {
  iteration: number;
  results: CandidateResult[];
}

export interface PipelineCase {
  design: string;
  date: string;
  process_stages?: ProcessStage[];
  topology?: Topology | null;
  iterations: IterationResult[];
  winner_tag: string | null;
  outcome: string;
  diagnosis?: string;
}

export interface ReferenceDb {
  designs: Record<string, PipelineCase[]>;
}

export async function fetchReferenceDb(): Promise<ReferenceDb> {
  const res = await fetch(REFERENCE_DB_URL);
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data?.error ?? `${res.status} ${res.statusText}`);
  }
  return data;
}
