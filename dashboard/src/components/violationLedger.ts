// What a run failed on, as counts per kind of check, read out of the
// verdict's own `violations` strings — and how those counts moved from
// one run of a design to the next.
//
// THE QUESTION THIS ANSWERS. aes has eight recorded runs and every one
// reads "OPEN · 0 PASS · 2 FAIL" in the record. That is true and says
// nothing about whether the agent is getting anywhere: setup went from
// 172 violations to 0, max-slew from 504 to 0, hold from 172 to 263 to
// 0, and antenna stayed at ~8 throughout. Those numbers were in every
// case file, in prose, one card at a time.
//
// Split out of the component so node can load it — tests run this over
// the real reference-db/cases/ store, the same split as
// progressTimeline.ts. Nothing here invents a count: every number is
// parsed from a string score() wrote as `${count} ${label}`, and a kind
// that does not appear in a verdict is not drawn as zero — it is drawn
// as absent, because a check that was not gated on the day a case was
// scored is not a check that passed.
import type { CandidateResult, PipelineCase } from "../api/referenceDb";
import { recordedAt } from "./caseGrouping";

// The kinds, in the order score() reads its checks, each matched by the
// label text score() emits. Kept as a substring match on the label so
// a count-bearing string is recognised even when the label is later
// reworded around the same noun.
export interface Kind {
  id: string;
  /** Column label, short enough for a heatmap header. */
  short: string;
  match: RegExp;
}

export const KINDS: Kind[] = [
  { id: "setup", short: "setup", match: /setup timing violation/ },
  { id: "hold", short: "hold", match: /hold timing violation/ },
  { id: "slew", short: "max-slew", match: /max-slew/ },
  { id: "cap", short: "max-cap", match: /max-capacitance/ },
  { id: "fanout", short: "max-fanout", match: /max-fanout/ },
  { id: "antenna", short: "antenna", match: /antenna violation/ },
  { id: "drc", short: "DRC", match: /DRC error/ },
  { id: "lvs", short: "LVS", match: /LVS/ },
  { id: "pdn", short: "power grid", match: /power-grid violation/ },
  { id: "overlap", short: "overlap", match: /illegal layout overlap/ },
  { id: "xor", short: "GDS XOR", match: /XOR difference/ },
  { id: "unmapped", short: "unmapped", match: /unmapped instance/ },
  { id: "disconnected", short: "disconnected", match: /disconnected pin/ },
  { id: "synth", short: "synth check", match: /synthesis check error/ },
  { id: "lint", short: "lint", match: /lint error/ },
];

const COUNTED = /^(\d+)\s+(.*)$/;

/** Counts per kind from one verdict's `violations`.
 *
 * Only strings of the form `${count} ${label}` are counted; the
 * slack/utilisation/IR-drop strings ("worst setup WNS -1.18 (timing
 * violation)") carry a measurement rather than a count and are
 * returned separately so the caller can still show them.
 */
export function countViolations(violations: string[] | undefined): {
  counts: Record<string, number>;
  other: string[];
} {
  const counts: Record<string, number> = {};
  const other: string[] = [];
  for (const v of violations ?? []) {
    const m = COUNTED.exec(v);
    if (!m) {
      other.push(v);
      continue;
    }
    const n = Number(m[1]);
    const kind = KINDS.find((k) => k.match.test(m[2]));
    if (!kind) {
      other.push(v);
      continue;
    }
    counts[kind.id] = (counts[kind.id] ?? 0) + n;
  }
  return { counts, other };
}

export function totalViolations(counts: Record<string, number>): number {
  return Object.values(counts).reduce((a, b) => a + b, 0);
}

/** One run of a design in the ledger. */
export interface LedgerRun {
  file: string | undefined;
  at: string;
  /** Tag of the candidate the row describes. */
  tag: string;
  passed: boolean;
  counts: Record<string, number>;
  /** Kinds this candidate's verdict never checked (its `unverified`). */
  unverifiedKinds: string[];
  candidates: number;
}

function candidates(c: PipelineCase): CandidateResult[] {
  const out: CandidateResult[] = [];
  for (const it of c.iterations ?? []) for (const r of it.results ?? []) out.push(r);
  return out;
}

/** The candidate a run is judged by: a passing one if any, else the
 * one with the fewest counted violations. Ties keep the earlier
 * candidate, which is the one the orchestrator listed first. Runs with
 * no scored candidate at all (failed before signoff) yield null. */
export function representative(c: PipelineCase): { cand: CandidateResult; counts: Record<string, number> } | null {
  let best: { cand: CandidateResult; counts: Record<string, number>; total: number } | null = null;
  for (const cand of candidates(c)) {
    const v = cand.verdict;
    if (!v) continue;
    const { counts } = countViolations(v.violations);
    const total = v.passed ? -1 : totalViolations(counts);
    if (best === null || total < best.total) best = { cand, counts, total };
  }
  return best ? { cand: best.cand, counts: best.counts } : null;
}

/** The runs of one design, oldest first, each reduced to its
 * representative candidate's counts. */
export function ledger(cases: PipelineCase[]): LedgerRun[] {
  const runs: LedgerRun[] = [];
  for (const c of cases) {
    const rep = representative(c);
    if (!rep) continue;
    const v = rep.cand.verdict!;
    const unverifiedKinds = (v.unverified ?? [])
      .map((label) => KINDS.find((k) => k.match.test(label))?.id)
      .filter((id): id is string => id !== undefined);
    runs.push({
      file: c.file ?? undefined,
      at: recordedAt(c),
      tag: rep.cand.tag,
      passed: v.passed,
      counts: rep.counts,
      unverifiedKinds,
      candidates: candidates(c).length,
    });
  }
  return runs.sort((a, b) => a.at.localeCompare(b.at));
}

/** Kinds that appear (as a count or as never-checked) in any run, in
 * KINDS order — the heatmap's rows. A design that never failed a kind
 * does not get a row for it. */
export function kindsPresent(runs: LedgerRun[]): Kind[] {
  return KINDS.filter((k) =>
    runs.some((r) => r.counts[k.id] !== undefined || r.unverifiedKinds.includes(k.id)));
}

/** The largest count anywhere in the ledger, for the colour scale. */
export function maxCount(runs: LedgerRun[]): number {
  let m = 0;
  for (const r of runs) for (const n of Object.values(r.counts)) if (n > m) m = n;
  return m;
}
