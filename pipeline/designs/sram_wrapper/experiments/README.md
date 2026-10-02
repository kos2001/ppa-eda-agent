# One additional SRAM experiment, 2026-09-13

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

## 2026-10-02: does the macro's placement, relative to its own pin groups, matter?

The question was whether placement and routing should account for the SRAM's big
functional blocks (IO, decode, control). Those blocks are inside the hard macro,
which OpenROAD sees only as pin groups on its four sides. Read from the LEF
(480 x 397.5 um):

| group | side |
|---|---|
| addr0, csb0, web0 | left |
| din0, dout0, wmask0 | bottom |
| addr1, csb1 | right |
| dout1 | top |

The wrapper's 8-bit counter is the controller, and it has to drive addr0 (left),
addr1 (right) and din0 (bottom): three sides of a 480 um block from one small
cluster. The IO placer already puts `dout0` on the bottom edge and `dout1` on the
top, matching the macro's own pins, so IO assignment is block-aware already.

`pipeline/sram_floorplan_grid.py` runs the rest as a grid (same design and flow;
only the die and the macro's orientation change; result in
`reference-db/sram_floorplan_grid.json`):

| die | orientation | max-slew | routed wire | other |
|---|---|---|---|---|
| 700x700 | N (shipped) | 16 | 20,688 um | DRC/LVS 0 |
| 700x700 | MY | 21 | 36,489 um | 31 max-cap |
| 700x700 | MX | 28 | 83,219 um | 61 max-cap |
| 700x700 | S | 254 | 106,302 um | 37 max-cap |
| 540x458 | N, MY, MX, S | - | - | all four crashed: GRT-0118 congestion (MY, S) or thousands of Magic/LVS errors (N, MX) |

Two readings. The orientation the design ships with is the only one that respects
the pin sides, and every other costs 1.8x to 5x the wire and up to 16x the slew
violations: placement relative to the pin groups is the largest lever in this
floorplan, and it was already pulled correctly by hand. And the die cannot simply
be shrunk to macro-plus-margin: the macro is opaque to routing, so the space around
it is the routing channel, and a 30 um margin fails.

Not tested: the macro's offset within the 700 um die (it is centred), a margin
between 30 and 110 um, and moving the counter or splitting it per port in the RTL.
The last is the untried block-aware change: `addr_prev` already gives port 1 its
own register, but it is fed from `addr_ctr` across the die.

