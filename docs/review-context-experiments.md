# Review context comparisons

Inspired by [Scaling Discovery Requires Forgetting](https://arxiv.org/pdf/2610.20563).
This is an opt-in context ablation, not a reproduction of its agent teams or a
demonstration of better EDA results. The default review workflow is unchanged.

The agent decision protocol lives in
[adaptive-search.md](../.codex/skills/ppa-eda-flow/references/adaptive-search.md).
It adds conditional refresh guidance, factual handoffs, paired visibility and
multiple measured seeds. These CLIs do not detect stagnation, dispatch sessions
or reset them. The protocol's thresholds and multiple-seed selection are local
experimental choices, not findings established by the cited paper.

Three views share the supplied design baseline, explicit measured seed and
allowed tuning keys:

- `full_history`: current measurements, bounded prior result rows and diagnoses.
- `fresh_evidence`: the same measurement rows without review/diagnosis fields.
- `seed_only`: only the selected candidate's results, with case-level constraints
  and provenance. The seed is user selected, not automatically ranked as best.

All views retain the recorded task objective/reference and budget contract,
constraints and available candidate input/toolchain provenance, verification
policy and equivalence results. Missing legacy fields stay missing. Prior
diagnoses and predictions are not substitutes for measurements.

Original cases remain untouched. The supplied config is not proof of historical
execution inputs. Prior signatures can reveal repeated configurations, but do
not prove equivalent physical experiments. Narrative text embedded in tool error
strings remains evidence; filtering removes designated narrative fields, not
arbitrary natural language.

Prepare a frozen comparison (PowerShell, from the repository root):

```powershell
python pipeline/review_context_eval.py prepare --case-file reference-db/cases/aes__2026-10-16.json --design-config pipeline/designs/aes/config.json --seed-tag surrogate-pilot-20261016-util35 --allow-override FP_CORE_UTIL --repetitions 3 --output-dir .cache/review-context/aes-20261016
```

The new directory contains a private audit snapshot, hashed prompts and a
randomized, balanced trial schedule. Existing directories are rejected. Give
each independent model session **only its prompt**, with identical model and
generation settings, no tools and no earlier conversation. Keep the private
snapshot and other trial responses inaccessible to that session. This CLI makes
no API calls and cannot enforce isolation in an external agent; instructions in
a prompt alone do not provide isolation. Save model/settings/usage alongside the
responses. Equal trial counts do not imply equal token or monetary costs.

To preserve multiple starting candidates, run `prepare` separately for each
selected seed with a distinct output directory. Each seed gets all three views
with the same repetition count. Choose compatible qualified Pareto seeds from
`python3 pipeline/evaluation_archive.py`, or label failed seeds as diagnostic.
Keep the global admissions/time ledger across these blocks; preparing another
block does not authorize another physical run or reset the evaluation budget.

Save each JSON response as `<trial-id>.json` in a separate responses directory:

```powershell
python pipeline/review_context_eval.py score --experiment .cache/review-context/aes-20261016 --responses .cache/review-context/responses --output .cache/review-context/scores.json
```

Scoring checks frozen-file hashes, JSON structure, candidate limits, allowed
delta overrides, existing candidate validation, explicit visible evidence tags
and duplicate configuration signatures against the private history. Missing
responses remain missing. Prose grounding, compatibility and physical validity
are not established by these checks. Output is created exclusively, without
overwriting earlier scores. Hashes detect accidental changes, not malicious
replacement of the manifest and all inputs.

For a single request, use `request_review.py request --design aes` with
`--context-policy fresh_evidence --seed-tag <tag> --allow-override FP_CORE_UTIL`.
Optional `--case-file`, `--design-config`, `--history-limit` and `--output-dir`
select inputs and destination. Without `--context-policy`, legacy behavior stays
unchanged. Standalone requests do not enforce a fresh agent session.

No candidate is automatically applied or executed. Before execution, validate a
separate bounded run spec and retain equivalence, final DRC/LVS, all timing
corners and model-validity gates. PPA improvement stays unknown until matched
physical runs produce measurements; model usage and cost stay unknown here.
