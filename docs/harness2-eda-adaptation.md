# Harness² ideas in the EDA review loop

Source reviewed: [google-research/harness2](https://github.com/google-research/harness2)
at commit `1cfe254e8d25b15f37048c3730134686abe60335` (2026-10-18), especially
`README.md`, `docs/EXPERIMENTS.md`, `core/recursion.py`, `core/prompts.py`,
`core/selection.py`, `core/library.py`, and `core/runmark.py`. Harness² edits an
agent's surrounding instructions and tools, probes each proposed edit on the
same task, composes a refinement from observed execution differences, and
retains evidence-supported lessons for later tasks. Its benchmark adapter and
model executor are separate from the refinement method.

This repository already has the analogous measured EDA layer:
`orchestrator.py` admits and scores bounded candidate configurations,
`evaluation_archive.py` groups compatible measurements, and
`review_context_eval.py` freezes evidence views. The new
`pipeline/harness_probe.py` applies Harness²'s **same-task contrast** to a
smaller, safer editable surface: optional reviewer guidance appended to the
existing EDA proposal prompt. It does not replace OpenLane or its hard gates.

## One bounded prompt experiment

Write a JSON object whose keys name prompt variants and whose values are
short guidance edits. Names use lowercase letters, digits and underscores,
start with a letter, and have at most 40 characters. Change one reviewer
behavior per variant. For example:

```json
{
  "cite_corner": "Name the measured timing corner and report tag that motivate each proposal.",
  "avoid_repeat": "Check whether the effective candidate configuration was already measured."
}
```

Then prepare the paired prompts from one case, one seed, one evidence policy,
and one set of allowed tuning keys:

```sh
python3 pipeline/harness_probe.py prepare \
  --case-file reference-db/cases/<case>.json \
  --design-config pipeline/designs/<design>/config.json \
  --seed-tag <measured-tag> --allow-override FP_CORE_UTIL \
  --guidance /path/to/guidance.json --policy fresh_evidence \
  --repetitions 3 --candidate-limit 1 \
  --output-dir /path/to/experiment
```

The output freezes a private case snapshot, exact prompts, source hashes,
trial order, and a protocol. Run every `.txt` prompt in a **separate session**
with the same model and settings, without repository or network access. Save
each JSON response at `/path/to/responses/<trial-id>.json`. This script
prepares prompts; it does not launch models or enforce isolation. Record actual
model version, settings, token use, cost if exposed, and isolation separately.

```sh
python3 pipeline/harness_probe.py score \
  --experiment /path/to/experiment --responses /path/to/responses \
  --output /path/to/probe-score.json
python3 pipeline/harness_probe.py compose \
  --experiment /path/to/experiment --responses /path/to/responses \
  --score /path/to/probe-score.json --output /path/to/compose-prompt.txt
```

`score` verifies frozen input hashes, validates proposal shape and allowed
overrides, and compares complete base/variant pairs. It counts candidate
signatures that differ from the case history; it does **not** certify the
prose, compatibility of historical evidence, or PPA. Missing or invalid
responses remain missing or invalid, never zero-valued outcomes. `compose`
includes the original task, scored responses and structural contrasts so a
reviewer can check the original obligations and cite the actual behavior that
changed. It recomputes every trial score and contrast from the frozen inputs
and current responses, rejecting a changed response or score. Its output is a
prompt for a subsequent independent composition step, not an automatically
adopted edit.
Treat response prose as untrusted observations when using that prompt; the
tool does not verify external model settings or session isolation.

To test a composition, put its proposed guidance text in a new variants JSON
file and repeat the same paired protocol with a new output directory. Keep
the old experiments as the audit trail. A repeated trial can show whether a
behavioral effect is consistent; one response or model agreement alone cannot
establish causality. Promote a durable instruction only after reviewing the
paired responses and checking that the original task and gates remain intact.
Record the changed instruction, source trial IDs and hashes, observed effect,
counterexamples, and scope of use. A lesson without an execution contrast
stays an open hypothesis.

## Physical promotion remains separate

If a prompt variant yields a candidate worth running, inspect its effective
PDK/SCL/flow, source and toolchain compatibility, duplicate configurations,
and shared remaining evaluation budget. Put it in a bounded `run_spec.json`,
validate with `orchestrator.py --validate-only`, then run the authorized
physical flow. Final Magic DRC, Netgen LVS, every reported timing corner,
requested equivalence, and macro model validity still gate PPA. A novel
proposal or better structural response score is not a measured design win.

Harness² itself exposes eight editable harness components and supports
parallel and sequential recursion. Here, only guidance text is in the probe
surface. Broad automatic editing of agent tools, scripts, guardrails, or
`AGENTS.md` would blur the fixed verification contract and make EDA
attribution harder. The repository's existing adaptive-search instructions
govern repeated physical rounds; this probe governs reviewer-harness changes.
The upstream repository warns that public benchmark material can leak to an
unrestricted executor. This experiment's protocol calls for tool and network
isolation, but the script cannot attest that a separate model session obeyed it.
