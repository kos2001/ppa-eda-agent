# Specialist recheck, 2026-10-15

Three independent, read-only specialist reviews were dispatched using the new
evidence/environment, SRAM model and equivalence role definitions. This was
a review and diagnostic replay, not new physical closure or characterization.
No measured case was rewritten and no new design PASS was established.

## Checks executed

- Windows: graph-loop 8 tests, closure-review 13 tests, macro-model audit 5
  tests, artifact archive 4 tests passed. Final-equivalence 4 tests passed with
  the Docker integration test skipped on Windows.
- WSL: all 5 final-equivalence tests passed, including actual Docker/Yosys
  correct-buffer and deliberately wrong-inverter controls with both reference
  modes. These small controls do not prove the full RISC-V RTL.
- WSL/Docker responded; Docker server 29.7.2, 847 GiB reported available on the
  Linux filesystem. This is not a write-permission or performance guarantee.
- AES antenna/netlist analysis was rerun from the exact archived hul sources:
  six violating nets, five without existing diodes. No routed geometry was
  supplied, so positions and layer lengths remain unknown.

## Evidence/environment review

All 130 RISC-V archived artifacts match recorded uncompressed hashes: baseline
21, r1 49, r2 60. Their runtime final/synthesis netlists and physical proof logs
also match structured proof records. Both r1/r2 max SPEFs match the recorded
slew-net comparison. Full baseline/r1/r2 runtime directories still exist under
`pipeline/designs/riscv32i/runs/`; residual graph reconstruction does not require
a new P&R run. The direct-critical raw artifacts remain missing; its retained
case and structured proof match the retention manifest but cannot replace them.

## Equivalence review

Baseline RTL-to-final remains 3 proven / 96 unproven, without a demonstrated
counterexample. The split retry matches register-file state names to mapped FFs
but times out during output processing in `equiv_simple`, before induction.
Its 1123 initial points are not a final proof result. Baseline physical-only
equivalence remains 10224 proven / 0 unproven with matching retained log hash.

`equiv_check.py` currently runs simple proof before induction and bounds only
final-netlist proofs to 600 seconds; ordinary RTL-to-synthesis has no timeout.
Do not blindly rerun the latter. The next useful experiment requires a tested,
bounded state-first proof option and positive/negative sequential controls.
Keep all obligations in the final assertion; do not assume an all-zero initial
register file or equate unproved partition outputs.

## SRAM model review

The direct audit CLI does not discover the flattened physical evidence layout:
it returns zero corners/incomplete coverage despite exit 0. The specialist
replayed the checker in memory with only report discovery adapted to that
layout, without changing files: 9 corners, 6 TT-to-SS/FF mapping mismatches,
complete input coverage, zero positive-input-axis extrapolations, zero unknown
input checks, model_qualified=false. This is not a new STA measurement.
Thirty zero-slew inputs per corner agree with tied wrapper inputs and are not
evidence of an active-pin measurement defect.

Six nonempty SS single-point source files differ from recorded raw hashes in
this Windows checkout, but all match after CRLF-to-LF normalization. Empty
stderr matches raw. Keep that distinction explicit rather than claiming raw
byte integrity. Retained resolved configuration and production Liberty match
their recorded raw hashes.

Missing qualifications include measured SS/TT/FF tables, both read ports,
setup/hold axes, final output-load coverage and internal SRAM function. Existing
interface proof and one SS measurement do not discharge these obligations.
The full characterizer lacks point/port selection and a wall-time bound; the
earlier single stimulus took about 89 minutes. A persistent, hash-verified
simulator/model environment and a bounded single-point replay must precede
another large characterization run.

## Next execution order

1. Support flattened audit evidence explicitly and make missing-corner output
   unmistakably incomplete; preserve original evidence bytes.
2. Add and test a bounded RTL proof harness before one state-first experiment.
3. Refresh RISC-V residual nets/near-critical paths from retained runtime data.
4. Prepare one bounded SRAM baseline point; generate/verify port1 separately.

These are follow-up actions, not completed fixes. Specialist recommendations
have not been applied to case verdicts or admitted as new expensive runs.
