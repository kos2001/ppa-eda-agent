# Surrogate forward evaluation — 2026-10-16

Implemented an offline comparison of the existing kNN predictor (fixed k=1),
mean baseline, Ridge, Extra Trees, and Gaussian Process. Live candidate selection
and signoff policy are unchanged. Optional models require scikit-learn; the
dataset reader, mean baseline, and kNN require only the standard library.

## Reproduce

From the repository root, with the desired Python environment:

```sh
python3 -m unittest discover -s tests -p 'test_surrogate*.py'
python3 pipeline/surrogate_benchmark.py --out reference-db/surrogate_forward_evaluation_20261016.json
```

The report records source file hashes, implementation hashes, package versions,
exclusion reasons, individual predictions when available, and paired model errors.
The checked-in report contains actual corpus evaluation, not synthetic scores.

## Observed data limitations

The reader retained 538 candidate records across 109 source files. Only 38 have
sufficient provenance for admission; these span 34 strict compatibility cohorts,
with at most three records in any cohort. All 38 are FanoutRepair measurements.
They retain verification-failed status; admission as measured regression data
does not imply verification success.

The shared feature schema currently supports Classic flows and does not encode
repair topology. Consequently all models abstain on this corpus: the report
status is `insufficient_compatible_history`, scored predictions are zero, and
MAE is null. Even with repair features, these cohorts cannot meet the minimum of
eight earlier distinct compatible training configurations. There is no measured
accuracy improvement or EDA runtime saving to report.

## Evaluation safeguards

- Validate provenance structure and context digest, including RTL, constraints,
  libraries, tool revisions, evaluator sources, and custom flow sources.
- Keep original measurement rows and raw-byte source hashes. Do not repair
  missing historical provenance by guessing.
- Train only on earlier UTC day buckets, using the latest recorded revision
  date. Separate experiment lineage and candidate identity; never split repeats
  across training and test for the same candidate. Test each candidate once.
- Compare only within matching provenance, fidelity, and known host context.
  Missing host information excludes runtime labels. Unknown values remain null;
  execution errors, timeouts, and deferred runs are not regression labels.
- Unqualified macro models exclude dependent power and timing labels.
- Fit encoding on training inputs only. Use fixed model settings and report
  alternative-versus-kNN MAE only on common scored folds. GP posterior standard
  deviation is explicitly uncalibrated.

## Next data collection step

Collect a bounded Classic cohort under fixed RTL, constraints, toolchain,
libraries, evaluator, host, and fidelity, with complete provenance. At least
eight distinct earlier configurations plus a later independent test experiment
are needed for even one scored fold; this is an execution minimum, not evidence
of statistical reliability. Retain failures and original logs. Keep timing
objectives and verification gates unchanged.

For SRAM or FanoutRepair, first define and validate features for macro or repair
topology and qualify model-dependent labels. Do not pool incompatible designs
or loosen provenance checks merely to obtain scores. Model promotion requires
independent measured performance and a subsequent bounded EDA comparison.

## Validation

The existing WSL ML environment ran all 87 surrogate tests successfully,
including the optional Ridge, Extra Trees, and Gaussian Process tests. Synthetic
fixtures verify implementation and leakage guards only; they do not establish
service-level prediction quality. Exact interpreter and library versions are
recorded in the JSON report.
