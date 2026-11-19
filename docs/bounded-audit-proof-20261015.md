# Bounded archive audit and RTL proof, 2026-10-15

The archive reader now accepts native STA step directories, archives with
corner directories directly under the evidence root, and older flat
`<corner>-drv.rpt` / `<corner>-macro-inputs.csv` archives. Native stages take
precedence; missing native reports are not filled from stale archived files.
The original evidence is read without conversion or copying into fake stages.

The macro-model audit reports `audit_complete`, `status`, expected/missing
corners, input-audit details and report byte hashes. Its CLI exits 1 for an
incomplete audit. Exit 0 means the audit completed, not that the model qualified;
PVT mismatches and extrapolations remain explicit findings.

All ordinary RTL-to-synthesis and final-netlist proofs default to 600 seconds.
`--timeout` accepts a finite positive override. Timeout discards progress counts,
retains the full partial log and returns an unverified result. Cleanup targets
only the invocation's unique container and has a separate 30-second deadline;
cleanup failures are retained as `cleanup_error`.

The optional `--state-first` maps memory, applies `clk2fflogic`, splits bit names,
and tries induction before simple proof. This is an induction-first experiment,
not an implemented register-file partitioner. It keeps the final assertion over
all obligations and adds no initial-zero or reset assumption. Global-clock and
asynchronous-input hold assumptions remain recorded in the result.

## Verification

WSL/Docker integration: all 8 `test_final_equiv.py` tests passed, including real
buffer/inverter controls in both final-reference modes and sequential DFF
positive/negative controls for state-first RTL-to-synthesis proof. Timeout,
cleanup failure and invalid deadlines are covered by unit tests.

Archive/model tests: 6 macro-model audit tests, 3 macro-slew audit tests and
5 functional gate tests passed. The earlier 32-test model-pattern suite also
passed. Archive tests compare identical evidence in all three layouts, check
missing reports/CSV and the incomplete CLI exit, and reject mixing a live
stage with archived reports. Windows fixture creation hit sandbox temporary
directory permissions; the successful test results are from WSL.

Two parallel read-only reviewers checked SRAM discovery/completeness and proof
scope. The SRAM review exposed mixed archive-layout borrowing and count-only
input coverage. Both were fixed: one layout is selected for both report types,
declared input names must be present, and unknown arc checks prevent completion.
The final 6 audit and 3 slew tests passed after these changes. The proof review
confirmed the final log hash and the 1,123/0 result, while requiring the
initial-state limitation below to remain explicit.

## Limited replay

The SRAM CLI was rerun on `evidence-20261015-physical` with the effective design
`srio_r15_clock_fanout16_revalidate_20261015`. It reads 9 corners with complete
input coverage, 6 declared-PVT mismatches, zero positive input-axis
extrapolations and zero unknown input checks. `model_qualified` remains false.
The complete result and source byte hashes are in
`reference-db/sram_archive_audit_bounded_20261015.json`.

Only one full-design proof attempt is admitted: existing RISC-V baseline,
state-first RTL-to-synthesis, 120 seconds. It completed within that deadline:
1,123 proven points, zero unproven, nonvacuous, and no timeout. The final
`equiv_status -assert` succeeds. Induction discharged the entire workset;
simple proof had no remaining obligations. Yosys 0.46 reports 115.03 seconds
user CPU and 577.86 MB peak memory (CPU time is not wall-clock duration).
This proves the baseline synthesis relation under the recorded formal model;
it does not independently prove the ISA specification or qualify physical
signoff, other candidates, or the SRAM model.
The inductive result establishes state correspondence; it is not a separate
proof of arbitrary initial-state or reset reachability equivalence.
This distinction follows the pinned
[Yosys 0.46 `equiv_induct` contract](https://yosyshq.readthedocs.io/projects/yosys/en/0.46/cmd/equiv_induct.html).

`reference-db/bounded_audit_proof_handoff_20261015.json` records result hashes,
source hashes, the pinned Docker image, the one-attempt budget and this proof
scope. The handoff remains unresolved because macro-model qualification remains
unverified. Original measured cases were not rewritten.

No P&R or full SRAM characterization
is part of this replay. Reproduce the command from the repository root:

```sh
python3 pipeline/equiv_check.py --design pipeline/designs/riscv32i \
  --run-dir pipeline/designs/riscv32i/runs/closure-20261015-baseline \
  --state-first --timeout 120 \
  --out reference-db/riscv_state_first_bounded_20261015.json \
  --log reference-db/riscv_state_first_bounded_20261015.log
```
