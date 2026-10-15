# Closure review and specialist handoff

Use this reference for residual closure failures, SRAM qualification, incomplete
proofs or execution/evidence failures. It does not authorize new EDA runs.

## Route work by the missing obligation

- Environment failure or missing/hash-mismatched artifacts:
  `evidence-environment-auditor`.
- Unqualified macro corner, arc, edge or output-load coverage:
  `sram-model-qualifier`.
- Missing RTL-to-synthesis or synthesis-to-final proof:
  `equivalence-specialist`.
- Candidate selection and stopping: existing `feedback-optimizer`, then
  `placement-strategist` for the smallest justified validated plan.
- Final independent assessment: `verification-ppa-evaluator`. Candidate
  authors do not certify their own result. Use the existing resolver/challenger/
  adjudicator workflow when independent reviews disagree materially.

These are role definitions, not background workers. The calling session invokes
only relevant roles when delegation is authorized; otherwise it follows the
same checklist itself. Specialists return findings, never mutate measured cases.
They do not independently admit expensive runs or enlarge budgets.

## Refresh the graph before another repair

Join final timing, fanout, capacitance, slew and antenna reports to the exact
final netlist/geometry using source hashes. Record driver, sinks, corner, metric,
units and evidence source; missing joins are unknown, not zero. Rebuild the
residual set after each routed candidate: antenna insertion can expose different
drivers, and protecting one path can move the critical path elsewhere. Consider
multiple near-critical paths and reserve root-driver headroom. Do not reuse
stale net names from another run without a verified mapping.

Describe hypothesized causes separately from observed associations. Counts alone
do not identify unique failing nets: several slew records can share one net.
Compare all hard gates before ranking PPA; improved fanout does not compensate
for worse timing, capacitance or antenna.

## Bound the loop

Declare candidate_budget and evaluation_budget before admission. Count screening,
synthesis, repair and polish under the shared evaluation budget. Record the
baseline identity and original targets; diagnostic clocks do not qualify them.
Deduplicate complete effective configurations, including flow and repair options,
not just tags. Stop on exhausted budget, repeated equivalent candidates or no
supported new hypothesis. Regressions require a new bounded hypothesis, not
automatic extra rounds. Historical PASS requires current revalidation.
Budget-deferred candidates have no measured failure; infrastructure failures are
not training examples of bad designs.

## Retain evidence before completion

Use a persistent Linux workspace for new Linux-local EDA runs, not /tmp as the
sole copy. Temporary probes may use temporary paths. Retain exact-run inputs,
resolved configuration, final reports/netlists, flow source snapshot, toolchain
identity and full proof logs. Copy and verify hashes before removing anything;
cleanup is separate, explicitly scoped work. Keep original bytes immutable.
This checker verifies present bytes; it cannot promise future retention.

Create a review manifest beside the retained evidence. This is a separate
handoff format, not a replacement for existing case JSON or raw run output:

```json
{
  "schema_version": 1,
  "run_id": "unique-run",
  "design": "design-name",
  "toolchain": "pinned-image-or-build-identity",
  "target_identity": "original-constraints-hash-or-reference",
  "required_gates": ["rtl_to_synthesis", "synthesis_to_final", "macro_model"],
  "gates": {
    "rtl_to_synthesis": {"state": "unverified", "evidence": []},
    "synthesis_to_final": {"state": "unverified", "evidence": []},
    "macro_model": {"state": "unverified", "evidence": []}
  },
  "artifacts": {}
}
```

This deliberately incomplete skeleton must fail the checker. Add real artifacts
as named records with `path` (relative portable path) and `sha256` (actual file
digest), and refer to those names in each gate's `evidence` list. Include every
applicable hard gate from the authorized run contract: final DRC/LVS, antenna,
electrical limits, all-corner timing and requested proof/model obligations.
A non-macro design need not declare macro_model. The independent evaluator
checks gate completeness; the checker cannot derive it from arbitrary names.

Gate states are `pass`, `fail`, `unverified`, `infrastructure_failure` and
`budget_deferred`. Unsupported proof constructs/timeouts are unverified, not
counterexamples. Pass/fail declarations require source artifacts; even a valid
hash does not prove the declared verdict or its relevance.

Run `python3 pipeline/closure_review.py <manifest.json>`. Exit 0 means declared
gates all pass with intact declared evidence; 1 means unresolved or invalid
handoff; 2 means unreadable/malformed input. Inspect the JSON and recommended
roles. This is not automatic dispatch, independent signoff, or an orchestrator
acceptance gate. Do not bypass existing hard gates with its result.
