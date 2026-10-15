# SRAM bounded point follow-up, 2026-10-15

The next model-qualification step is a bounded replay of the retained SS
port0 point, plus independent generation of a port1 stimulus. This is not a
new P&R search or a complete characterization sweep. No production Liberty
or measured case verdict is changed.

## Fixed experiment

The retained point is SS, 1.6 V, 100 C, port0, address `11111111`, bit31,
10 ns period, 0.560 ns 10–90% input slew and 27.56 fF output load. The full
linear input ramp is 0.700 ns. Preserve `.TRAN 10p 150n 0n 10p UIC` and
`.OPTIONS KLU RELTOL=0.001 method=gear ACCT`. The admission budget is one
full-netlist transient attempt, 300 seconds, with bounded process-group
termination. No automatic retry, period doubling or grid expansion is admitted.

The earlier completed point took 5,325.727 seconds and produced 27 finite
measurements. It is a reference for this replay, not a qualified timing table.
The 300-second budget can establish runtime progress or expose an environment
failure; it is not an expectation that the whole transient will finish.

## Environment and evidence

The previous `/tmp` simulator and PDK copies were gone after WSL restarted.
Recovery uses `/home/kos2001/ppa-sram-bounded-20261015`, retaining sources,
build/install trees, logs, model snapshot and point inputs. The official
ngspice 46 release and original source hashes are verified before applying the
retained model-bin index patch. Unmodified and patched simulators are compared
on existing model-selection controls before full-SRAM admission.

Recovery completed: both `make check` runs exited 0, all five synthetic cases
matched, and original/indexed two-device currents matched at SS/TT/FF. All 856
model files match the persistent Linux source. The new indexed executable hash
is `9c5478fbae6ddefaf97b4c52b220cf90f57031fae063aa2dc68dba1409de0f67`.
The new binary hash differs from the earlier temporary build and is recorded
as a new build, not claimed byte-identical. These limited comparisons do not
qualify the full SRAM.

The installed SRAM source hash is
`40ca6e140b45a85adfa0f70095bbf13d6270a79f0a30e3344f164cfc250afcf5`;
the prepared ascending-pin source hash is
`2d84749242dda91f42df9773b6e49ef41a444f980f30302e7daa0b8106ab9755`.
All 8,192 storage paths and both sense-enable nodes resolve. The archived
stimulus and measurement files match historical hashes only after CRLF→LF
normalization; their current raw hashes are retained separately in
`inputs/preparation.json`. The original archive is unchanged.

## New tools

`pipeline/sram_point_replay.py` verifies simulator, netlist and prepared stimulus
hashes before execution, records
model files and startup hashes, and relocates only the three external include
paths. It writes stdout, stderr and ngspice output directly to a new result
directory and samples the owned process. Timeout terminates its process group,
preserves partial output and records `timeout_unverified`. Missing, duplicate
or nonfinite requested measurements cannot become a successful point.

`pipeline/sram_point_stimulus.py` uses pinned OpenRAM
`b2b069ce119d1488cbe6883b2240bceb5c7ce29a` to generate exactly one point without
calling SPICE. Selecting read port1 retains write port0 and selects the proper
clock, output and sense-enable probes. It does not relabel a port0 deck.

Actual SS port1 preparation completed with 27 requested measurements and all
8,192 storage paths validated. Active-low chip-select windows, read-zero/read-one
cycles on port1, writer0 cycles and `clk1`/`dout1`/`s_en1` probes are checked.
The generated deck and manifest are retained under
`pipeline/designs/sram_wrapper/experiments/evidence-20261015-bounded-point/port1-prepared`.
No port1 simulation was run.

The restored OpenRAM initialization assets use cell revision `fc63b128…` from
the retained 20261004 environment, differing from the `dd642569…` recorded in
the later build manifest. This discrepancy is explicit in the retained
`environment.json`. Those cell-asset SPICE files are not included in the
generated stimulus; it uses the separately hash-verified complete SRAM netlist.

Independent point checks inspect every Q/Qbar read/write result, output rails,
sense-enable delay and positive delay/slew bounds. This avoids relying on the
upstream storage-check loop, whose success variable is overwritten for each
measurement. Even a successful point keeps `model_qualified=false`.

## Checks completed

Six replay tests, five point-stimulus/measurement tests and ten existing
characterization tests pass in WSL. They exercise actual bounded process
termination and partial log retention, include-only relocation, incomplete and
nonfinite measures, wrong storage polarity, wrong read-port probes and the
historical measurement reference. These tests do not replace a full SRAM run.

## Output-load evidence

The compact physical archive omits exhaustive output-load data, but the
original `srio_r15_clock_fanout16_revalidate_20261015/runs/io` still retains
the full final STA reports and state. The read-only extractor finds Cap
observations for all 64 SRAM outputs in all nine corners. Each corner covers
128 of 256 requested min/max × rise/fall combinations; missing observations
remain null. The report unit contract is not independently verified, so values
remain in report units even though the macro Liberty declares pF.

`output-load-coverage.json` records source hashes and incomplete coverage.
The original and archived checks reports match after newline normalization.
Final netlist, SDC, ODB and nominal/minimum/maximum SPEFs survive, so exhaustive
output-load export can use a report-only STA replay, without new P&R. The
recorded original command and required report wrapper are in that JSON.

## Actual bounded result

The single admitted port0 replay stopped at 300.217 seconds with
`timeout_unverified`, return code -15, and zero of 27 completed measurements.
The owned process was absent afterward. All inputs, partial logs and 30 process
samples were copied byte-for-byte to `ss-port0-replay-300s` in the evidence
directory and checked against the runtime hashes.

The log reached “now using transient initial conditions” after model checks.
It does not establish how far simulated time advanced. Samples showed one
CPU-active thread, peak RSS 8,059,900 KiB (about 7.69 GiB), and no swap.
No numerical tolerances were changed, no retry was admitted, and timeout is
not a functional counterexample. This establishes a reproducible bounded
execution path, not a new SRAM timing value or model qualification.

The retained `handoff.json` covers 70 artifacts. Simulator controls pass;
port0 measurement, port1 measurement, output-load coverage and full macro-model
qualification remain unverified. Measured SS/TT/FF tables and setup/hold axes
are still outstanding. The next independent action is exhaustive output-load
reporting from retained STA state; a longer single-point simulation needs an
explicit new runtime budget rather than automatically expanding this attempt.

Reproduce the bounded runner from the repository root with a new result folder:

```sh
sram_env=/home/kos2001/ppa-sram-bounded-20261015
python3 pipeline/sram_point_replay.py \
  --source-dir "$sram_env/inputs" \
  --point-manifest "$sram_env/inputs/preparation.json" \
  --simulator "$sram_env/install-indexed/bin/ngspice" \
  --simulator-sha256 9c5478fbae6ddefaf97b4c52b220cf90f57031fae063aa2dc68dba1409de0f67 \
  --model-root "$sram_env/pdk" --netlist "$sram_env/inputs/sram.sp" \
  --netlist-sha256 2d84749242dda91f42df9773b6e49ef41a444f980f30302e7daa0b8106ab9755 \
  --startup-dir "$sram_env/install-indexed/share/ngspice/scripts" \
  --output-dir "$sram_env/ss-port0-replay-new" --timeout 300
```
