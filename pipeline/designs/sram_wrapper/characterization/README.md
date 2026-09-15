# SRAM repair and characterization

The design is **not yet a verified pass**. The production Liberty remains
the old relaxed model; generated results are kept under `../runs/characterization/`.

Three full OpenLane 2.3.10 runs on 2026-09-12 reached the final
manufacturability report. Extracted evidence is in
`physical_runs_20260912.json`; the original reports remain under `../runs/`.

| Run | Antenna violations | KLayout GDS DRC | LVS | XOR | Magic abstract DRC |
| --- | ---: | ---: | ---: | ---: | ---: |
| sram-repair-20260912 | 3 | 0 | 0 | 0 | 382 |
| sram-antenna-20260912 | 2 | 0 | 0 | 0 | 382 |
| sram-diode-20260912 | 0 | 0 | 0 | 0 | 382 |
| sram-concrete-classic-20260912 | 0 | 0 | 0 | 0 | 0 |

The last run's settings are now in the design config: KLayout streamout,
Magic DEF/LEF DRC, and heuristic diode insertion with threshold 60 um,
antenna margin 50 and 10 repair iterations. Magic's 382 nwell.4 reports
were classified as outside-macro abstract artefacts by the existing checker.
They remain unverified, and OpenLane still exits with a deferred error.
No DRC check has been disabled to manufacture a pass. The final `sram-concrete-classic-20260912` run used the custom geometry-aware Magic step and its manufacturability report says DRC, LVS and antenna all passed.

Worst reported macro input slew in the diode candidate is 0.223766 ns;
the installed Liberty grid ends at 0.04 ns. The current SPICE sweep reaches
0.245 ns (selected from the earlier 0.209 ns observation), covering this
candidate but with less than the recommended 15% headroom. A subsequent
grid recommendation from 0.223766 ns is 0.260 ns.

## Actual characterization path

`pipeline/characterize_sram.py` calls OpenRAM's standalone characterizer
on the existing PDK netlist. This does not regenerate layout: the
matching GDS/LEF remain unchanged and have the separate checks above.
It uses SPICE, retains the original slew points, and writes provenance
hashes and status to `../runs/characterization/manifest.json`.

Integration mismatches were verified in generated stimulus and fixed:

- The shipped SPICE top-level bus pins descend; OpenRAM stimulus pins
  ascend. A copy of the top-level declaration is reordered by pin name,
  with all internal named connections preserved and a strict interface check.
- The installed netlist has two `xbitcell_array` instance levels below
  `xbank0`: the bank instance targets `replica_bitcell_array`, whose nested
  instance targets `bitcell_array`. A later one-level configuration was a
  regression. On 2026-09-13, the runner resolved all 8,192 configured bitcell
  paths and their `Q`/`Q_bar` nodes against the installed SPICE, restored the
  two-level path, and added a mandatory check before simulation. Evidence and
  the source hash are in `storage_paths_20260913.json`.
- The functional checker derives its `Q`/`Q_bar` probes from that same
  validated `cell_format`.
- The adapter disables ngspice `POST=1 PROBE` waveform storage by default.
  Liberty generation uses `.meas` results, and retaining all transient
  waveforms for this macro can consume excessive memory. Set
  `spice_save_waveforms = True` when waveform dumps are needed for debugging.

The standalone `fake_sram` also lacks width/height; the runner reads them
from the installed macro LEF for Liberty area reporting.

Environment used:

- OpenRAM checkout: `/private/tmp/ppa-sram-openram`, revision
  `b2b069ce119d1488cbe6883b2240bceb5c7ce29a`.
- Python venv: `/private/tmp/ppa-sram-venv`, numpy/scipy/scikit-learn installed.
- SRAM cell source: `/private/tmp/ppa-sram-cells` from
  `https://github.com/VLSIDA/sky130_fd_bd_sram`.
  GDS cells were copied into OpenRAM's `technology/sky130/gds_lib`;
  simulation SPICE cells into `sp_lib` with `.sp` suffix and `.base.spice`
  replacing ordinary variants, following the upstream Makefile.
- Local ngspice; `use_nix=False`; existing repository SKY130 PDK.

From the repository root:

```sh
/private/tmp/ppa-sram-venv/bin/python -u pipeline/characterize_sram.py \
  --openram-root /private/tmp/ppa-sram-openram
```

Previous runs were interrupted without producing usable timing measurements.
The current `../runs/characterization/manifest.json` records `failed` with
`KeyboardInterrupt`; its Liberty artifact is empty. The latest retained
stimulus is in `/private/tmp/openram_kos2001_56745_temp/`, and the traceback
is in `/private/tmp/ppa-sram-characterize-corrected.log`. That attempt used
the incorrect one-level storage path. The retained simulator log ends after
model-scale setup; it does not establish that transient time advanced.
An earlier run reportedly consumed over nine hours, but that duration must
not be attributed to this latest attempt or treated as timing data.

The storage-path check verifies connectivity only. It neither explains the
entire runtime problem nor supplies delay/power measurements. No simulation
with the restored configuration has completed yet.

## Restored-path execution, 2026-09-13

A new run was launched at 02:13:44 UTC with all 8,192 storage paths
validated, using the separate output directory
`../runs/characterization-path-verified-20260913/`. Its manifest records the
runner PID, simulator temporary directory, start time and input hashes.
The generated measurement deck uses the restored two-level path for both
`Q` and `Q_bar`. Initial observation confirmed a live execution but no
completed measurement; consult the manifest and live process for later status.

To preserve earlier evidence, use a new directory for each attempt:

```sh
/private/tmp/ppa-sram-venv/bin/python -u pipeline/characterize_sram.py \
  --openram-root /private/tmp/ppa-sram-openram \
  --output-dir pipeline/designs/sram_wrapper/runs/characterization-NEW-RUN
```

`--output-dir` refuses existing directories. Without it, the historical
configured output directory is still used. A `running` manifest alone is
not proof that a process remains alive or that any Liberty was generated.

The runner accepts `--corner tt` (default), `--corner ss`, or `--corner ff`
for TT/1.80 V/25 C, SS/1.60 V/100 C, or FF/1.95 V/-40 C respectively.
SS and FF require an explicit new `--output-dir`. Each invocation uses
one exact OpenRAM `use_specified_corners` tuple and records its PVT in the
manifest. Setting the process/voltage/temperature lists alone is insufficient:
OpenRAM's nominal-only mode otherwise selects the technology's nominal PVT.
These options prepare separate measurements; they do not imply that SS or
FF characterization has been run or validated.

New runner invocations archive each simulator call under
`simulations/000001/`, `000002/`, and so on. Each archive records the input
stimulus, delay measurement deck when present, start/end timestamps, and
the simulator logs before the next call overwrites them. Console messages
announce call boundaries. `returned_unverified` means the simulator call
returned, not that OpenRAM's subsequent measurement checks passed.
This does not expose progress inside a transient solve and does not add
timeouts or retries. It does not change the already-running
`characterization-sense-verified-20260913` process or recover overwritten logs.

## Remaining acceptance work

`selected_run_slew_audit.json` preserves per-corner observations and report
hashes for the selected `sram-buffer2-probe-20260913` run. Its max-slew
violation tables report address inputs up to 0.122157 ns, while a selected
SS/max timing path shows falling `u_sram/clk0` slew of 0.228263 ns. The
clock is absent from the violation table. Thus the address maximum alone
cannot set the characterization range. These reports are not an exhaustive
rise/fall input-slew export, and this audit deliberately remains unverified.

The resolved configuration from
`../runs/sram-buffer2-probe-20260913/72-checker-holdviolations/config.json`
lists nine STA corners: each of `nom`, `min`, and `max` interconnect corners
combined with TT/25 C/1.80 V, SS/100 C/1.60 V, and FF/-40 C/1.95 V.
The current SPICE sweep covers only the first PVT combination. SS and FF
need their own measured libraries; interconnect corners still require STA.
That historical run also limited `TIMING_VIOLATION_CORNERS` to `*tt*`.
The design config now explicitly selects `*` for future timing checks.
This configuration change has not been exercised in a new physical run,
and the existing wildcard TT macro-library mapping remains unqualified.

1. Complete the actual simulation and inspect measurements, convergence,
   read/write behavior, and all generated table values. The grid checker
   alone verifies structure, not measured provenance or circuit correctness.
2. Characterize all PVT corners used by signoff. This first run is TT,
   1.8 V, 25 C only; it cannot replace the current wildcard with a valid
   multi-corner model by itself.
3. Integrate verified Liberty files with explicit corner mapping, rerun
   OpenLane, and prove slew/model validity as well as physical checks.
4. Keep the geometry-aware Magic step and its scope record with the run;
   the remaining signoff blocker is model validity, not physical DRC.

The expanded characterization and interface tests passed locally. No new
Liberty has been installed or represented as a signoff result.

## Isolated simulator diagnostic

`bitcell_dc_diagnostic_20260913.json` records a successful TT, 1.8 V,
25 C DC operating-point check of one bitcell extracted from the installed
macro, using the same PDK library and ngspice compatibility settings. The
artifacts remain under `/private/tmp/ppa-sram-bitcell-probe/`. The storage
nodes settle near 1.8 V and 0 V with an explicit initial-state hint. This
confirms basic model loading and a DC solution only; it does not test macro
read/write behavior or characterize timing. The retained warnings are
recorded in the JSON. The full-macro run remained live without measurements
at the time of this diagnostic. Process profiling was unavailable because
the sandbox denied access to the process list.

## Sense-enable measurement correction

The restored-storage-path run subsequently reached transient initialization,
where ngspice warned that the bank-local `s_en0` vector did not exist.
The bank formal inputs `s_en0/1` connect to top-level `s_en0/1`; ngspice
uses those connected node names. `sense_enable_probe.sp` and its retained
log reproduce the failed bank-local measurement and the successful
top-level measurement. OpenRAM's `check_sen_measure()` requires this
measurement, so the run was deliberately interrupted (exit 130) after
confirming the defect, not merely because observation took too long.
Its existing manifest records the interruption.

`sen_format` now points to the top level. The runner checks both sense
nodes before simulation, in addition to the 8,192 bitcells. A new attempt
uses `../runs/characterization-sense-verified-20260913/`; consult that
run's manifest and live execution for status. No completed macro timing
measurements are available yet.

## Recovery, 2026-09-14 (KST)

The sense-verified run's manifest still said `running`, but a successful
process-list check found neither its PID 99166 nor any characterizer or
ngspice process. Its Liberty was zero bytes. The exit cause is unknown.
The original manifest was preserved; surviving simulator files, hashes,
and the observation are saved in that run's `recovery-20260914/` directory.
The retained log has no completed measurements. Its latest deck uses a
6.25 ns period, but overwritten earlier logs cannot establish which
previous checks passed.

A fresh TT attempt is running under
`../runs/characterization-archived-20260914/`, with per-call archives enabled.
Its first simulator call started at 2026-09-13 15:08:31 UTC (September 14
in Korea). Console output is in
`/private/tmp/ppa-sram-characterize-archived-20260914.log`.
This is an execution checkpoint, not a completed characterization result.
The 35 characterization, adapter, and model-validity unit tests passed.
Docker image listing and an OpenLane 2.3.10 container smoke check also
succeeded, including locating OpenSTA and OpenROAD inside the container.
