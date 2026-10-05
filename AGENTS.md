# Repository agent guide

This repository runs and evaluates real PPA experiments. Treat tool output as
evidence, not as a prompt to fill gaps with plausible values.

## Start here

- `pipeline/orchestrator.py` owns candidate expansion, bounded repair, scoring,
  and case creation.
- `pipeline/run_stage.py` is the OpenLane command boundary.
- `pipeline/designs/<name>/config.json` describes the design; its
  `run_spec.json` describes an experiment. Keep those roles separate.
- `reference-db/cases/` contains measured history. Compare cases only after
  checking design, PDK, standard-cell library, and toolchain provenance.
- `.codex/skills/ppa-eda-flow/SKILL.md` is the workflow for planning, running,
  and reviewing experiments. The specialist prompts live in `.claude/agents/`.
  Claude Code reads the identical copy in `.claude/skills/ppa-eda-flow/`. Edit
  the `.codex` source, then replace the copy with
  `rm -rf .claude/skills/ppa-eda-flow && cp -R .codex/skills/ppa-eda-flow
  .claude/skills/`; `tests/test_skill_mirror.py` fails when the two differ.

## Evidence rules

Prefer evidence in this order: the current run's metrics and logs, compatible
measured cases, the pinned local tool contract, then official documentation for
the installed version. Missing metrics are unknown. Never convert them to zero
or infer that a stage passed because a later-looking file exists.

OpenLane configuration names are version-specific. This repository uses
OpenLane 2 conventions in run specs: for example `PL_TARGET_DENSITY_PCT`, while
`PL_TARGET_DENSITY` is an OpenLane 1 name. A standard-cell-library comparison
uses candidate `scl`; an override named `STD_CELL_LIBRARY` can be accepted yet
ignored. Keep run tags safe and unique after normalization. Do not submit two
candidates with the same overrides, PDK, and SCL.

Treat final Magic DRC, Netgen LVS, every reported timing corner, and requested
functional equivalence as hard gates before comparing PPA. TritonRoute's DRC is
an intermediate routing signal and does not replace signoff DRC.

For visual review, use the selected case's recorded source reports and DEF
geometry. A step folder or state snapshot proves an artifact exists, not that
signoff passed. A geometric cell-footprint map is not congestion or IR-drop
evidence. Keep missing run files and unknown source compatibility explicit.

For schematic review, retain native xschem drawings and actual symbol pin
interfaces. Geometry previews do not establish ERC/LVS, expand buses, or resolve
symbolic sizing. Keep unresolved symbols and implicit power/well attributes
visible. Re-drafting a source sheet requires independent native before/after
netlist comparison, including ordered terminals and device parameters.
The connected CMOS layout may preserve identical existing undriven-well
diagnostics only with exit 0, both native files and equal netlists. Record those
diagnostics with source/symbol hashes and keep them visible in Review; do not
claim ERC clean. Reject new diagnostics or other netlisting failures. Drawing-only
code-box removal must never save the transient canvas or feed a simulation.

Candidate `flow` selects a supported flow explicitly. `FanoutRepair` and
`MacroFanoutRepair` are opt-in SKY130 HD physical experiments: they split
internal single-driver nets after antenna insertion and rerun placement,
routing and antenna repair without changing the final SDC. A later diode
pass can reintroduce fanout violations; inspect the final reports. They are
not covered by the current surrogate. Custom flow sources are snapshotted
before execution and retained with hashes in the run directory.

For coupled antenna/fanout repair, `FANOUT_REPAIR_ROUNDS` is bounded to 1–3.
Use headroom on measured nets before expanding a global target: stronger global
buffering can create new capacitance, slew and antenna failures. Preserve every
pass's report, use unique instance names across passes, and evaluate the last
routed result rather than an intermediate zero count.

Dedicated macro input buffers are an opt-in physical experiment. Validate all
requested pins and their existing power mappings before rewiring. Only known
non-inverting masters, or two known inverters preserving polarity, are allowed.
Verify original pin connectivity independently. Seed cells outside the macro,
legalize and route them, and protect their sizes only after net repair. Library
transition lookups must account for different slew thresholds and both edges;
they rank candidates but do not replace final extracted STA.

For SRAM, use final `macro_inputs.csv` / `macro_slew_audit.py` in addition to
the violation table. Nonviolating control or clock inputs can exceed the
Liberty table range. Complete input-edge coverage does not establish PVT or
per-arc model validity. `characterize_sram.py --prepare-only` validates and
archives inputs; it produces no measurements or qualified Liberty.

Sample a stalled owned SPICE process before changing numerical settings. Model
parsing and matrix solving are different bottlenecks. An experimental simulator
patch needs pinned source/build hashes and comparisons with the original model
selection and PVT device currents. Partial libraries and completed small-device
probes cannot qualify the full SRAM.

## Experiment workflow

Validate every edited run spec before a costly tool invocation:

```sh
python3 pipeline/orchestrator.py \
  --design pipeline/designs/<name> \
  --run-spec pipeline/designs/<name>/run_spec.json \
  --validate-only
```

Declare `candidate_budget` for new searches and put the method, objective,
reference, and randomized seed when applicable under `search`. Give each
candidate one testable hypothesis. Change one independent axis at a time unless
the hypothesis is specifically about an interaction.

For new bounded digital searches, also declare `evaluation_budget` with
`max_evaluations` and/or `max_wall_seconds`. Screening, synthesis exploration,
repair and polish share admissions. The deadline stops new evaluations;
already-admitted verification completes. Never label a budget-deferred candidate
as a failed tool run or include it in measured coverage without a real screen.

Use `python3 pipeline/evaluation_archive.py` to inspect measured costs and
compatible Pareto groups before proposing a new search. Legacy source provenance
must stay unknown. `search.evaluation_order: measured_cost` is optional and only
uses compatible full-flow timing cohorts with at least three observations. It
does not predict PPA. See `docs/evaluation-harness-20261004.md` for the counting
contract. Equivalence to generated RTL does not validate that RTL against a spec;
keep independent golden verification and physical/model gates distinct.

Use real tool feedback for correction: locate the first failing stage and its
exact error, check the current local contract, make the smallest justified
change, validate the plan again, then rerun. A run with an ignored option,
colliding tag, missing output, or tool failure is an invalid experiment; do not
store its result as design evidence.

Measured driver sizing is opt-in through `FANOUT_REPAIR_DRIVER_CELLS`. Check the
actual Liberty Boolean functions and every original OpenDB pin connection
before a same-family upsize. Keep final SDC limits unchanged, reload the edited
DB through normal placement/routing, and inspect final cell masters and all
corner reports. A slew/cap improvement can still leave fanout and antenna
failures; record those candidates as rejected.

For macro-model audits, prefer the run's `resolved.json` mapping over the
original design config:

```sh
python3 pipeline/macro_model_audit.py --design <design> --run-dir <run>
```

Compare declared PVT and actual per-table related/constrained input axes.
Expand bus pins and convert Liberty time units. Matching labels
alone do not qualify measurements. Repeated table/edge extrapolation checks are
not physical violation counts. Missing libraries, unknown edges, output-load
coverage and unmeasured PVT provenance must remain unverified.

Check the pinned simulator's batch behavior before adding output-retention
optimizations. ngspice 46 already extracts `.meas` vectors automatically in
batch mode; the recorded nonlinear RC comparison shows no RSS benefit from an
explicit `.save` list. Sample the full process to establish its current
bottleneck instead of assuming waveform retention is responsible.

## Checks

Fast checks do not require Docker or a PDK:

```sh
python3 -m unittest discover -s tests -v
cd dashboard && npm run build
```

OpenLane, KLayout, PDK, and Docker-backed checks are integration work. Run them
only when the task calls for new physical results, and preserve the command,
toolchain, inputs, and case output so another agent can audit the result.
