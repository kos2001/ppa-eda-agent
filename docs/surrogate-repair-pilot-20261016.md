# FanoutRepair surrogate features and bounded measurement pilot

The offline evaluator now represents pre-run FanoutRepair settings. The live
surrogate and candidate selection policy remain unchanged. This extends the
first-stage evaluation documented in `surrogate-forward-evaluation-20261016.md`.

## Features and comparison

`pipeline/surrogate_repair_features.py` exports feature schema version 2. It
encodes global fanout limit, repair rounds, buffer masters, targeted net limits,
direct sink mappings, driver replacements, and diode trim settings. Exact
canonical mappings and a categorical topology fingerprint distinguish different
structures even when their counts match. Generated buffer counts, routed
geometry, measured timing, and verdicts are never inputs.

Absent optional controls remain unknown. The reader does not backfill historical
settings using today's flow defaults. Invalid numeric values, malformed maps,
unknown repair controls, unsupported macro buffering and MacroFanoutRepair are
rejected explicitly. Encoding a net or master name does not prove it exists or
that its replacement preserves function.

The added `feature_knn` comparator uses k=1 with training-only standardization,
missing-value indicators, and one-hot categories. Ridge, Extra Trees and GP use
the same encoding. Unseen topology categories cause these models to abstain.
The mean baseline intentionally ignores topology. Legacy kNN remains a Classic
baseline and refuses FanoutRepair. Both kNN comparison tables use common folds.

Strict provenance cohorts are unchanged: RTL, config, final constraints,
libraries, toolchain, flow/evaluator sources, verification policy, host and
fidelity still separate records. Repair controls in semantic overrides also
remain cohort boundaries. The next pilot varies utilization, an existing
permitted optimization axis, while holding repair settings fixed.

## Historical corpus result

Before the physical pilot, the 538-record corpus contained 38 admissible rows.
All 38 can be encoded by the repair feature schema. After importing the original
pilot case, there are 540 records and 39 admissible rows: one new measured
verification-failed candidate and one budget-deferred candidate, whose targets
remain unknown. Evaluation still has zero scored folds because no
compatible cohort supplies eight earlier distinct configurations. Thus the
refusal reason changes from unsupported flow features to insufficient prior
compatible configurations; MAE remains null. There is no measured accuracy or
runtime improvement claim.

`reference-db/surrogate_repair_evaluation_20261016.json` is the separate report
for this implementation; the first-stage report remains intact. Reproduce with:

```sh
python3 -m unittest discover -s tests -p 'test_surrogate*.py'
python3 pipeline/surrogate_benchmark.py --out reference-db/surrogate_repair_evaluation_20261016.json
```

## Physical pilot

The validated spec is
`pipeline/designs/aes/experiments/surrogate-cohort-pilot-20261016.json`.
It requests two serial FanoutRepair candidates at core utilization 35 and 40,
fixed original 10 ns clock, fixed repair limit 8, one repair round, and fixed
buffer masters. No historical targeted net names are reused. Functional
equivalence is requested. The hypothesis is that higher utilization reduces
core area while possibly degrading timing or routing; all final gates remain
required. Historical diagnostic 12 ns runs are not treated as this baseline.

Budget: two evaluations, 1,200 seconds for admitting new evaluations, one
iteration, no screening or polish. The admission deadline is not a running
process timeout: an admitted candidate and its verification finish before the
next admission decision. Deferred candidates are not measured failures.

The persistent Linux workspace is
`/home/kos2001/ppa-surrogate-pilot-20261016/work`. Its `command.json` records the
command and input hashes; `orchestrator.log` retains streamed tool output;
`exit.json` is written when the orchestrator exits. Native run artifacts are
retained under `pipeline/designs/aes/runs/` in that workspace.

This two-point pilot tests collection and provenance, not surrogate accuracy.
The day-bucket evaluation deliberately cannot train and test within this same
day. A future evaluation needs at least eight distinct earlier compatible
configurations and an independent later experiment. Repeats tied within an
earlier day are selected deterministically by row id, without claiming intraday
chronological order. Reliable quality conclusions require more than this
minimum.

## Measured result

The orchestrator completed with exit code 0, meaning the experiment was recorded,
not that the circuit passed. Utilization 35 completed in 1,539.180091 seconds
(25.65 minutes). Utilization 40 was not evaluated because the admission deadline
had expired. No second run or additional physical repair was launched.

| Quantity | Utilization 35 result |
| --- | --- |
| Cell area | 159,483 µm² |
| Core area | 362,235 µm² |
| Reported total power | 0.13805142045021057 W |
| Worst setup WNS | −2.4237634807446797 ns |
| Worst hold WNS | −0.6539244886229337 ns |
| Magic / KLayout DRC / LVS errors | 0 / 0 / 0 |
| Setup / hold violation counts | 457 / 412 |
| Antenna violations | 43 |
| Max slew / capacitance / fanout violations | 630 / 2 / 0 |

The actual resolved clock was 10 ns, max fanout constraint 10, and max transition
constraint 0.75 ns. Power is the run's reported analysis value, not laboratory
power measurement. Overall verdict is **FAIL**; this is not a Pareto-qualified
candidate or evidence of prediction improvement.

The initial RTL-to-synthesis proof failed during input loading:
`ERROR: Can't open include file timescale.v`. This is an infrastructure failure,
not a counterexample. The original case remains unchanged. A separate fix in
`equiv_check.py` recursively stages literal relative includes, checks source-tree
containment, and records both original and staged source hashes. Real Yosys
positive and negative nested-include controls validate that staging does not
turn incorrect logic into a pass. The standalone AES retry has a 120-second
deadline and retains separate command, source hashes, result, and full log.
The retry reached the proof engine without the include error, then timed out
at 120 seconds (`timed_out: true`, no cleanup error). The log reports missing
SAT models for `$mem_v2` cells. It neither establishes equivalence nor supplies
a counterexample. Its evidence is under the portable evidence directory's
`proof-include-retry/`; the owned Docker container was removed. No additional
proof strategy or physical run was admitted.

The include fix changes the evaluator source hash. Future physical runs using
the fix therefore form a new strict cohort; this pilot must not be silently
pooled into it. Freeze the revised evaluator before expanding data collection.
The separate proof retry does not retroactively update physical-run provenance.

Original case: `reference-db/cases/aes__2026-10-16.json`.
Portable evidence: `pipeline/designs/aes/experiments/evidence-surrogate-pilot-20261016/`.
Its archive retains 986 input/source/report/log/netlist files; `manifest.json`
records individual hashes. Full layout databases remain in the persistent native
workspace. `closure-review.json` confirms evidence integrity while explicitly
reporting failed and unverified gates. Synthesis-to-final equivalence remains an
unverified obligation.

Validation: 99 surrogate tests passed. The include fix passed 17 focused tests,
including real Yosys positive/negative controls; two other optional integration
tests were skipped. The pilot's failed signoff remains visible independently of
these software test results.
