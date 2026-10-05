# Experiment design patterns

These patterns are adapted from official upstream projects and mapped to this
repository rather than copied as new dependencies.

## Choose the search shape

OpenROAD-flow-scripts AutoTuner separates exhaustive sweeps from tuning and
declares parameter domains, sample/job limits, timeout, seed, objective, and
reference metrics. This repository has small candidate sets, so keep its local
dependency-free sweeps and synthesis exploration while borrowing the explicit
plan and budget:

- `search.mode`: `explicit`, `sweep`, `synthesis-exploration`, or `hybrid`.
- `search.objective`: the measured ranking rule or target.
- `search.reference`: the compatible baseline case or config.
- `search.seed`: required when candidate choice is randomized.
- `candidate_budget`: maximum initial full-flow candidates.

Source: [OpenROAD-flow-scripts AutoTuner instructions](https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts/blob/master/docs/user/InstructionsForAutoTuner.md).

## Classify knobs before spending runs

OpenROAD-flow-scripts groups flow variables by tuning difficulty because the
variables interact and an exhaustive search is infeasible:

- **Trivial**: deterministic bookkeeping or a value the flow can derive. Hide or
  automate it; do not spend a candidate on it.
- **Easy**: a report exposes a useful bound or monotonic response. Use a short
  sweep informed by the report.
- **Complex**: interactions or design-sensitive behavior dominate. Use several
  measured samples, declare the budget, and do not transfer a winning value to a
  different RTL/PDK/SCL/toolchain without revalidation.

Source: [OpenROAD-flow-scripts flow variables](https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts/blob/master/docs/user/FlowVariables.md).

## Use tools as the correction oracle

OpenROAD Agent documents that generated scripts can contain hallucinated APIs
even when documentation was supplied. Its useful local lesson is the
self-correction loop: execute against the real tool, detect the actual error,
and correct from that feedback. In this repository, `--validate-only` catches
plan errors; `run_stage.py` checks accepted/ignored OpenLane overrides; actual
logs and metrics decide the next repair.

Source: [OpenROAD Agent](https://github.com/OpenROAD-Assistant/OpenROAD-Agent).

## Preserve boundaries and provenance

SiliconCompiler separates a design description from the project that builds it,
tracks inputs and metrics through a flowgraph, and uses manifests/checklists for
provenance. Here, keep stable design inputs in `config.json`, experiment choices
in `run_spec.json`, and measured outputs plus `search_plan` in the case database.
Split fast pure tests from Docker/EDA integration runs.

Sources: [SiliconCompiler repository](https://github.com/siliconcompiler/siliconcompiler),
[SiliconCompiler agent guide](https://github.com/siliconcompiler/siliconcompiler/blob/main/AGENTS.md).
