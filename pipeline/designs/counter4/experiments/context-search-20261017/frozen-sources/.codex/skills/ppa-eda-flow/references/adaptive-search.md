# Adaptive search context

Use for repeated candidate-generation rounds, especially when proposals narrow
without measured progress. This updates agent decision guidance; it does not
add a session-reset service or change `orchestrator.py` admission and scoring.

The paper [Scaling Discovery Requires Forgetting](https://arxiv.org/abs/2610.20563)
studies periodic restarts from one best candidate. Conditional triggers, selective
factual memory, split visibility and multiple seeds below are local experimental
extensions from the supplied article, not results established by that paper or
measured EDA improvements.

## Decide when to refresh

`feedback-optimizer` records the comparison objective, compatible run identities,
metric units, improvement tolerance/noise basis and observation window before
using a plateau to justify a refresh. For a first trial, three completed,
comparable rounds without improvement plus repeated hypotheses, duplicate
configurations or increasingly small deltas is a reasonable starting rule;
record any different rule. Small changes alone are not a reason to reject a
useful hypothesis. For multiple objectives, track the qualified Pareto frontier;
before closure, track declared residual gates separately from qualified PPA.

Count empty proposal responses and configuration deltas from actual records.
Use reasoning-token trends only if the runner exposes measured usage; otherwise
mark them unavailable. Empty responses, shrinking edits and a model's claim that
the design is optimal are supporting observations, never proof of an optimum.
Do not count missing, incompatible, budget-deferred or infrastructure-failed
evaluations as measured non-improvement. Inspect a live stalled tool process
through the existing diagnosis workflow; a new model session cannot repair it.

When the rule fires, prepare a fresh proposal context before declaring the
search exhausted, provided the authorized budget leaves room for a bounded
round. Record `continue`, `refresh_context`, `repair_environment` or `stop`, with
observed reasons. Stop at the existing budget/deadline; a reset never replenishes
admissions, clears duplicate history or weakens a gate. If a fresh round has no
supported novel hypothesis, report that limit without claiming global optimality.

## Select what crosses the round boundary

Retain the full case/log archive, preserving measured rows and original source
artifacts. Forgetting changes a proposer's view, not the underlying evidence or
the coordinator's accounting. Store round notes beside review artifacts rather
than rewriting measured rows. A handoff contains:

- Original task, objective, constraints, allowed tuning axes, all required gates
  and remaining evaluation/time budget. Keep failed and unverified obligations
  visible even in a seed-only view.
- Selected seed identities: case, tag, effective configuration including flow,
  source hashes, PDK/SCL, toolchain and verification policy. Include recorded
  metric values, units, corners and source report paths/hashes where available.
  Missing provenance stays unknown; a current config is not a historical input.
- A bounded optional memo of tool/evaluator observations: configuration tested,
  metric or exact error, evaluation fidelity, compatibility and gate status.
  Keep a tool failure as an execution fact, never a negative physical result.

Keep diagnoses, recommendations, abandoned plans, cost guesses, predictions and
claims such as "this direction cannot work" out of the factual memo. A failed
configuration does not rule out an entire tuning axis. Agent-written diagnoses
may remain in the audit archive with their attribution, outside fresh views.
The existing context filter removes named narrative fields; it does not verify
arbitrary prose or turn raw error strings into certified facts. Inspect the
capsule and its original sources before describing it as measured-only memory.

## Preserve more than one promising seed

Use `python3 pipeline/evaluation_archive.py` to inspect compatible qualified
frontiers. Retain a small budgeted set of nonduplicate tradeoffs rather than
always replacing everything with the scalar winner. For example, keep a low-area
and a high-timing-margin candidate when both are qualified and nondominated.
Choose the cap and selection rule before dispatch, using only shared available
metrics; do not fill missing power with zero or mix provenance cohorts.

If no qualified frontier exists, explicitly select measured diagnostic seeds
with different residual failures. They remain failed/unverified, not qualified
PPA alternatives. One usable seed is acceptable when the evidence supplies only
one; do not manufacture diversity. Record why each selected seed is retained.

## Choose who sees the memo

For an authorized context experiment, give fresh proposal sessions the same
task, constraints and selected seed within each paired block:

- `seed_only`: the seed's measurements and mandatory task/gate information;
  no optional prior-candidate memo or earlier interpretation.
- `fresh_evidence`: the same seed and obligations plus bounded factual history;
  no earlier interpretation.
- `full_history`: a control arm with the same evidence and designated diagnoses.

For multiple seeds, prepare a separate balanced block for each seed; do not
confound memo visibility with starting candidate quality. Keep model/settings,
candidate allowance and available budgets comparable. Record actual usage and
cost when exposed; equal response counts are not equal cost. During proposal
generation, keep other arms' responses and private history inaccessible.

Use the existing `pipeline/review_context_eval.py prepare` and `score` commands
in `docs/review-context-experiments.md`. They prepare prompts and check responses;
they do not launch agents, detect plateaus, automatically select best seeds or
enforce session/filesystem isolation. Run only the relevant roles when delegation
is authorized. Without isolated sessions, record isolation as unenforced and
do not label a continued conversation a fresh-context experiment. A proposer
assigned a capsule must not retrieve hidden precedent through another role or
repository search. Full-evidence verification remains available after proposals
are frozen.

The coordinator checks effective configurations against the private complete
history before physical admission, including flow/repair options and normalized
tags. Deduplication feedback should identify the repeated configuration without
reintroducing prior diagnoses. Use `review_verify.py` for repeated independent
reviews before applying one; model agreement is not measured evidence. Validate
the resulting bounded run spec, retain shared budget accounting across rounds,
and require the original functional, signoff, all-corner and model gates.

## Record what the experiment establishes

For each round retain the trigger observations, objective/tolerance, selected
seed/source hashes, capsule hashes, visibility assignments, model/settings,
isolation status, responses, deduplication decisions and remaining budget. Record
unavailable usage as unknown. Structural response scores establish neither prose
grounding nor better PPA. Claim improvement only from compatible measured runs
that satisfy the original hard gates; report physical comparisons separately
from proposal diversity and model cost.
