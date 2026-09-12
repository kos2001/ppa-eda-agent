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

Two integration mismatches were verified in generated stimulus and fixed:

- The shipped SPICE top-level bus pins descend; OpenRAM stimulus pins
  ascend. A copy of the top-level declaration is reordered by pin name,
  with all internal named connections preserved and a strict interface check.
- The installed macro's bitcell hierarchy has two `xbitcell_array` levels
  below `xbank0`, without the additional `xreplica_bitcell_array` level in
  OpenRAM's default measurement path. `config.py` supplies the actual path.

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

On this session the corrected run was launched with output in
`/private/tmp/ppa-sram-characterize.log`. It reached ngspice transient startup but did not produce a measurement after more than nine hours, so it was terminated. The manifest remains `running` only because the process ended outside the wrapper; it must not be treated as a generated Liberty.
The initial, incorrectly connected simulation was interrupted and discarded. The corrected netlist reached ngspice transient startup, then exceeded nine hours without advancing its log; it was terminated after confirming the process was consuming a full core and about 1.1 GB. This is recorded as an execution failure, not timing data.

## Remaining acceptance work

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
