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

export interface HumanInTheLoopEntry {
  agent: string;
  reviewed_at: string;
  summary: string;
}

export interface PipelineCase {
  design: string;
  date: string;
  process_stages?: ProcessStage[];
  topology?: Topology | null;
  iterations: IterationResult[];
  winner_tag: string | null;
  outcome: string;
  // The total-guarded reason orchestrator.orchestrate()'s loop stopped —
  // "winner_found" | "max_iterations_reached" | "no_repairable_failures"
  // (orchestrator.py's STOP_REASONS). Optional: older cases predate this
  // field.
  stop_reason?: string | null;
  // Path (relative to reference-db/) of a real KLayout render of this
  // case's most informative candidate layout, stored by
  // orchestrator.py's capture_layout_image() so it outlives the run
  // directory. Optional: older cases predate it, and a case whose runs
  // never reached Magic.StreamOut has no GDS to render.
  layout_image?: string | null;
  layout_image_tag?: string | null;
  diagnosis?: string;
  human_in_the_loop?: HumanInTheLoopEntry[];
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

const LOCAL_SERVER_URL = "http://127.0.0.1:8123";

// URL for a case's stored layout render — layout_image is already a
// reference-db-relative path ("layouts/<...>.png"), and the server
// serves exactly that directory.
export function layoutImageUrl(relativePath: string): string {
  return `${LOCAL_SERVER_URL}/reference-db/${relativePath}`;
}

// Stage 8's human-in-the-loop, driven from the console instead of a
// terminal. Before these existed the UI could start and watch a run but
// printed a shell command at the one point the process needs judgment —
// so the end-to-end process left the tool exactly where it got hard.
export async function requestReview(design: string): Promise<{ file: string; content: string | null }> {
  const res = await fetch(`${LOCAL_SERVER_URL}/review/request`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ design }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data?.error ?? `${res.status} ${res.statusText}`);
  return data;
}

export async function applyReview(
  design: string,
  agent: string,
  responseText: string
): Promise<void> {
  const res = await fetch(`${LOCAL_SERVER_URL}/review/apply`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ design, agent, responseText }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data?.error ?? `${res.status} ${res.statusText}`);
}

export type PipelineRunStatus = "idle" | "running" | "done" | "error";

export interface PipelineRunState {
  status: PipelineRunStatus;
  startedAt?: string | null;
  finishedAt?: string | null;
  tail?: string[];
  error?: string | null;
}

// Triggers a real pipeline/orchestrator.py run against a design's
// run_spec.json via server/index.mjs's POST /pipeline/run — this is
// what turns the dashboard from a viewer of past reference-db/ cases
// into an actual control surface for the agent: pressing this button
// spawns a real OpenLane candidate-generation-and-auto-repair loop.
export async function triggerPipelineRun(design: string): Promise<PipelineRunState> {
  const res = await fetch(`${LOCAL_SERVER_URL}/pipeline/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ design }),
  });
  const data = await res.json();
  if (!res.ok && res.status !== 409) {
    throw new Error(data?.error ?? `${res.status} ${res.statusText}`);
  }
  return data;
}

export async function fetchPipelineRunStatus(design: string): Promise<PipelineRunState> {
  const res = await fetch(`${LOCAL_SERVER_URL}/pipeline/run-status?design=${encodeURIComponent(design)}`);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}
