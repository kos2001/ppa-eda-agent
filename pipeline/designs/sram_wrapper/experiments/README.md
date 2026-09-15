# One additional SRAM experiment, 2026-09-13

Ran `sram-slew010-probe-20260913` with the existing two-stage address
buffers and `MAX_TRANSITION_CONSTRAINT=0.10` (baseline: 0.75).
The override requests more aggressive slew repair; it does not extend the
SRAM Liberty grid, which still ends at 0.04 ns.

| Measurement | Two-stage baseline | Additional experiment |
| --- | ---: | ---: |
| Worst reported address slew, ns | 0.122157 | 0.123658 |
| Instance area, um2 | 200147 | 200793 |
| Magic / KLayout DRC errors | 0 / 0 | 0 / 0 |
| LVS / antenna violations | 0 / 0 | 0 / 0 |

The candidate was not adopted: address slew did not improve. Both designs
remain unverified for macro timing. The baseline config was not changed.
See `slew010-result-20260913.json` for extracted evidence and source hash.

## Reporting limitation

`model_validity.check` reads reported slew violations, not every macro pin.
The tighter constraint exposes clock/data pins absent from the earlier
report: 27 macro pins are now reported, including clk0 at 0.324567 ns.
Thus the previous 16-address-pin result was not an exhaustive macro audit.
The whole-design violation counts (16 versus 757) use different thresholds
and must not be used as a like-for-like quality score.
The checker now explicitly reports this coverage as incomplete, including
when no reported pin exceeds the grid. Such a result remains `unverified`
in the orchestrator verdict.
Before claiming model validity, export all input-pin slews for every corner
and compare against the corresponding Liberty arc axes and PVT mapping.

## DL, RL and surrogate assessment

The actual local kNN evaluation is saved in
`surrogate-evaluation-20260913.json`. The reference store has 429 distinct
rows overall but only six SRAM configurations. SRAM area/power have no
usable labels; completion scoring refuses all six held-out samples.
No SRAM prediction was scored. These counts concern the reference store,
which does not include all the newer local physical experiments.

The existing model predicts area, power and completion, not pin slew. Its
features omit transition constraints, buffer topology and physical distances.
It therefore cannot rank this experiment against the baseline meaningfully.
Training a deeper regressor on these inputs would not fix that omission.

Deep RL has been used for macro placement; see the primary implementation:
https://github.com/google-research/circuit_training
and its paper: https://www.nature.com/articles/s41586-021-03544-w .
That establishes a possible search method, not a measured benefit on this
single-macro design. No DL/RL training or placement-policy evaluation was
performed here; local source searches found no existing training integration.

Useful prerequisites for a future learned search are source/config/PDK hashes
to distinguish RTL variants, buffer sizes and stages, pin distances and net
loads, complete pin-slew labels, and a reward penalizing physical failures
and model-range violations. Compare held-out predictions with simple
baselines before using them to select candidates. Keep actual OpenLane/STA
and SPICE verification as the acceptance criteria; predictions alone cannot
qualify a wider Liberty range.

OpenROAD repair_design semantics:
https://openroad.readthedocs.io/en/latest/main/src/rsz/README.html
