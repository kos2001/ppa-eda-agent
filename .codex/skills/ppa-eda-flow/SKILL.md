---
name: ppa-eda-flow
description: Plan, validate, run, or review bounded OpenLane PPA experiments in this repository. Use for editing run_spec.json, choosing candidate knobs, invoking orchestrator.py, diagnosing measured flow failures, comparing candidates, or recording a reference-db case. Do not use for generic RTL edits or report-only Synopsys analysis outside this pipeline.
---

# PPA EDA flow

Work from measured artifacts and keep the experiment bounded and reproducible.

1. Read `AGENTS.md`, the design's `config.json`, topology, `run_spec.json`, and
   compatible `reference-db/cases/` precedent. Check the recorded PDK, SCL, and
   toolchain before using a precedent as a baseline.
2. State the hypothesis and expected metric movement. Classify each proposed
   knob as trivial, easy, or complex using
   [references/experiment-design.md](references/experiment-design.md). Prefer
   automatic handling for trivial knobs, a short measured sweep for easy knobs,
   and an explicitly budgeted search for complex interactions.
3. Write candidates with unique descriptive tags. Use candidate `scl` and `pdk`
   axes where needed; do not encode the standard-cell library as an OpenLane
   override. Change the smallest independent set of knobs that tests the
   hypothesis.
4. Set `candidate_budget`. Add a `search` object with `mode`, `objective`,
   `reference`, and `seed` for randomized work. Run:

   ```sh
   python3 pipeline/orchestrator.py --design <design-dir> \
     --run-spec <run-spec.json> --validate-only
   ```

   Resolve every malformed sweep, normalized-tag collision, duplicate
   configuration, and budget error before invoking EDA tools.
5. Run the full orchestrator only when the task authorizes new physical results.
   Use the validated run spec rather than composing ad hoc shell overrides.
6. Evaluate hard gates first: completed metrics, functional check when requested,
   signoff DRC/LVS, and all timing corners. For a macro candidate, run
   `python3 pipeline/macro_model_audit.py --design <design-dir> --run-dir <run-dir>`
   and retain its full audit beside the compact case summary. Use effective
   resolved corner mappings and actual related/constrained input axes; matching
   PVT labels and complete input records do not qualify measured tables. Output
   loads and functional/PVT provenance remain separate checks. Then compare
   the Pareto tradeoff in area, power, core area, setup slack, wirelength, and vias where present.
7. Correct failures from observed feedback. Find the first failing stage, retain
   the exact error code and metric source, check the installed tool's current
   contract, propose one minimal repair, validate, then rerun. Do not turn an
   invalid experiment into a fact about the design.
8. Confirm the resulting case records `toolchain`, constraints,
   `synthesis_exploration`, and `search_plan`. State missing evidence and limits
   in the diagnosis.
