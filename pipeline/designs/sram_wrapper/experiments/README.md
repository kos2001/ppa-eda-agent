# SRAM physical repair and model audit

## 2026-10-04: fanout repaired, complete macro input audit

Two new full runs completed on OpenLane 2.3.10 with the same RTL/PDK/HD cells.
The current baseline reproduced 18 fanout and 16 macro address-slew violations.
`sram-closure-20261004-fanout` added 44 non-inverting buffers in 18 internal
single-driver nets and enabled post-GRT timing repair. Final fanout and
capacitance counts are zero in every corner. Setup, hold, antenna, Magic DRC,
KLayout DRC, routing DRC and LVS counts remain zero. Area changes from 200147
to 200638 um². The remaining 16 address-slew violations still block a pass.

The same final STA processes now export all 57 signal inputs of `u_sram`
in all nine corners, with max/min and rise/fall slew. Coverage has no missing
corner, pin or unknown edge. The largest observed input is **web0**, not an
address or clock: 0.481151 ns in the baseline and 0.483124 ns in the repair,
both max/rise in SS/max. It was absent from the old DRV violation table.
The complete audit identifies 27 pins beyond the 0.04 ns model range.
`model_validity.check` now consumes these extra reports when available and
preserves the conservative incomplete-coverage behavior on older runs.

**Neither run is a final PASS.** Complete pin coverage does not qualify
per-arc tables or the wildcard TT Liberty mapping for SS/FF. The previous
0.260 ns characterization plan missed this control-pin slew. The planned
grid now retains the original points and extends to **0.560 ns**, covering
the new worst input plus 15% headroom. This changes the simulation plan,
not the production Liberty or a signoff limit.

The OpenRAM runtime was restored at the validated revision and ngspice 46
with KLU. `characterize_sram.py --prepare-only` verified the canonical macro
interface, all 8192 bitcell paths and both sense-enable nodes against the
installed SPICE, and archived the real prepared-netlist hashes. Its status is
`prepared_inputs_only`: **no new simulation measurements or Liberty were
generated**. Characterize TT/SS/FF separately before installing models.

Commands/specs, raw final metrics, all per-corner input CSVs, DRV excerpts,
antenna and buffer reports, immutable flow-source snapshots, preparation
manifest and environment revisions are retained in `evidence-20261004/`.
Full binaries and prepared SPICE remain under
`/private/tmp/ppa-sram-closure-20261004/`. The default run spec now compares
the current baseline and the measured physical fanout repair, replacing four
effective duplicates whose Magic settings were already in the design config.

To rerun the physical experiment:

```sh
python3 pipeline/orchestrator.py --design pipeline/designs/sram_wrapper \
  --run-spec pipeline/designs/sram_wrapper/run_spec.json --validate-only
python3 pipeline/orchestrator.py --design pipeline/designs/sram_wrapper \
  --run-spec pipeline/designs/sram_wrapper/run_spec.json
```

To prepare a new characterization directory without launching simulation:

```sh
/private/tmp/ppa-sram-venv-20261004/bin/python pipeline/characterize_sram.py \
  --openram-root /private/tmp/ppa-sram-openram-20261004 \
  --output-dir /private/tmp/NEW-SRAM-PREFLIGHT --prepare-only
```

Omit `--prepare-only` and use another new output directory for actual SPICE.
Use explicit `--corner ss` / `--corner ff` invocations for those PVT models.

Repository validation after these changes: `python3 -m unittest discover -s
tests` ran 1144 tests successfully (4 skipped); `cd dashboard && npm run build`
passed. These software checks are separate from the remaining physical and
macro-model signoff failures above.

The earlier entries below are historical observations.

Ran `sram-slew010-probe-20260913` with the existing two-stage address
buffers and `MAX_TRANSITION_CONSTRAINT=0.10` (baseline: 0.75).
The override requests more aggressive slew repair; it does not extend the
SRAM Liberty grid, which still ends at 0.04 ns.

| Measurement | Two-stage baseline | Additional experiment |
| --- | ---: | ---: |
| Worst reported address slew, ns | 0.122157 | 0.123658 |
| Instance area, um2 | 200147 | 200793 |
| Magic / KLayout DRC errors | 0 / 0 | 0 / 0 |
| LVS / antenna violations | 0 / 0 | 0 / 0 |

The candidate was not adopted: address slew did not improve. Both designs
remain unverified for macro timing. The baseline config was not changed.
See `slew010-result-20260913.json` for extracted evidence and source hash.

## Reporting limitation

`model_validity.check` reads reported slew violations, not every macro pin.
The tighter constraint exposes clock/data pins absent from the earlier
report: 27 macro pins are now reported, including clk0 at 0.324567 ns.
Thus the previous 16-address-pin result was not an exhaustive macro audit.
The whole-design violation counts (16 versus 757) use different thresholds
and must not be used as a like-for-like quality score.
The checker now explicitly reports this coverage as incomplete, including
when no reported pin exceeds the grid. Such a result remains `unverified`
in the orchestrator verdict.
Before claiming model validity, export all input-pin slews for every corner
and compare against the corresponding Liberty arc axes and PVT mapping.

## DL, RL and surrogate assessment

The actual local kNN evaluation is saved in
`surrogate-evaluation-20260913.json`. The reference store has 429 distinct
rows overall but only six SRAM configurations. SRAM area/power have no
usable labels; completion scoring refuses all six held-out samples.
No SRAM prediction was scored. These counts concern the reference store,
which does not include all the newer local physical experiments.

The existing model predicts area, power and completion, not pin slew. Its
features omit transition constraints, buffer topology and physical distances.
It therefore cannot rank this experiment against the baseline meaningfully.
Training a deeper regressor on these inputs would not fix that omission.

Deep RL has been used for macro placement; see the primary implementation:
https://github.com/google-research/circuit_training
and its paper: https://www.nature.com/articles/s41586-021-03544-w .
That establishes a possible search method, not a measured benefit on this
single-macro design. No DL/RL training or placement-policy evaluation was
performed here; local source searches found no existing training integration.

Useful prerequisites for a future learned search are source/config/PDK hashes
to distinguish RTL variants, buffer sizes and stages, pin distances and net
loads, complete pin-slew labels, and a reward penalizing physical failures
and model-range violations. Compare held-out predictions with simple
baselines before using them to select candidates. Keep actual OpenLane/STA
and SPICE verification as the acceptance criteria; predictions alone cannot
qualify a wider Liberty range.

OpenROAD repair_design semantics:
https://openroad.readthedocs.io/en/latest/main/src/rsz/README.html

## 2026-10-02: the "fatal errors while running Magic" tail is the old baseline

The dashboard still shows `sram_wrapper__2026-09-10` `cand-baseline`: "Encountered
one or more fatal errors while running Magic", exit 2. That candidate carries no
overrides on purpose; it is the recorded starting point of the Magic ladder
(commit ad37331), and Magic cannot read five layers of the macro GDS, so it dies at
stream-out. The design's own `config.json` has since adopted the ladder's
settings (`MAGIC_CAPTURE_ERRORS=False`, `MAGIC_DRC_USE_GDS=False`,
`PRIMARY_GDSII_STREAMOUT_TOOL=klayout`).

Re-run for real on `main` (18d5c5a), tag `sram-now`, ~3 min: the flow completes
(exit 0, 75 step directories). Magic DRC 0, KLayout DRC 0, illegal overlap 0,
LVS 0 on every count, XOR clean. The Magic failure no longer reproduces.

What still stops a pass, by the pipeline's own verdict:

- 18 max-fanout violations, identical in all nine corners. Most are clock-tree
  buffers: `clkbuf_3_*_0_clk` drive 17-33 sinks against a limit of 10. The rest
  are data nets (`fanout66`, `_082_`-`_088_` flops, 13-21).
- 16 max-slew violations, all `u_sram/addr0[*]`/`addr1[*]` pins, 0.0005-0.025 ns
  over a 0.050 ns limit.
- One `unverified`: 16 macro pins are timed 3.1x past the liberty's
  characterised range (0.122 ns against 0.040 ns). No change to the layout can
  clear that; only re-characterising the macro can.

Tried and recorded as a dead end: `CTS_SINK_CLUSTERING_SIZE` 5, 8 and 12 (with
`CTS_SINK_CLUSTERING_MAX_DIAMETER` 30, 40 and 80). All three runs are
byte-identical to the baseline: 9 clock buffers, the same 18 + 16 violations. The
clock buffers' fanout is set by the H-tree's stop criterion, not by the cluster
size, so the knob cannot reach it here. Raising `MAX_FANOUT_CONSTRAINT` would make
the number go away by editing the test, and was not done.
