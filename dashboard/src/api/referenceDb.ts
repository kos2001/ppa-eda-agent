export const REFERENCE_DB_URL = "http://127.0.0.1:8123/reference-db";

export interface CandidateVerdict {
  passed: boolean;
  violations: string[];
  area_um2: number | null;
  utilization: number | null;
  worst_setup_wns: number;
}

export interface CandidateDataPointers {
  circuit: Record<string, string | null>;
  layout: Record<string, string | null>;
  constraint_pdk: Record<string, string | null>;
  verification: Record<string, string | null>;
}

export interface CandidateResult {
  tag: string;
  overrides: Record<string, unknown>;
  verdict?: CandidateVerdict;
  error?: string;
  run_dir?: string;
  data?: CandidateDataPointers;
}

export interface IterationResult {
  iteration: number;
  results: CandidateResult[];
}

export interface PipelineCase {
  design: string;
  date: string;
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
