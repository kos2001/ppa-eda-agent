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

Candidate `flow` selects a supported flow explicitly. `FanoutRepair` and
`MacroFanoutRepair` are opt-in SKY130 HD physical experiments: they split
internal single-driver nets after antenna insertion and rerun placement,
routing and antenna repair without changing the final SDC. A later diode
pass can reintroduce fanout violations; inspect the final reports. They are
not covered by the current surrogate. Custom flow sources are snapshotted
before execution and retained with hashes in the run directory.

For SRAM, use final `macro_inputs.csv` / `macro_slew_audit.py` in addition to
the violation table. Nonviolating control or clock inputs can exceed the
Liberty table range. Complete input-edge coverage does not establish PVT or
per-arc model validity. `characterize_sram.py --prepare-only` validates and
archives inputs; it produces no measurements or qualified Liberty.

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

Use real tool feedback for correction: locate the first failing stage and its
exact error, check the current local contract, make the smallest justified
change, validate the plan again, then rerun. A run with an ignored option,
colliding tag, missing output, or tool failure is an invalid experiment; do not
store its result as design evidence.

## Checks

Fast checks do not require Docker or a PDK:

```sh
python3 -m unittest discover -s tests -v
cd dashboard && npm run build
```

OpenLane, KLayout, PDK, and Docker-backed checks are integration work. Run them
only when the task calls for new physical results, and preserve the command,
toolchain, inputs, and case output so another agent can audit the result.
