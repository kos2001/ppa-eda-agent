# AES physical repair, 2026-10-04

Three full OpenLane 2.3.10 runs used identical RTL, SKY130 PDK and HD cells.
The closest historical candidate reproduced exactly. Counts below are the
worst reported electrical count across all nine corners, not sums of corners.

| Candidate | Antenna | Fanout | Capacitance | Slew | Area, um² |
| --- | ---: | ---: | ---: | ---: | ---: |
| baseline | 15 | 4 | 2 | 0 | 132476 |
| post-antenna buffer/reroute | 6 | 2 | 2 | 17 | 134633 |
| additional measured capacitance net splitting | 6 | 6 | 0 | 0 | 134658 |

Every run completed; setup, hold, Magic DRC, KLayout DRC, routing DRC and LVS
counts were zero. **None is a final PASS.** The last candidate removes the
clock fanout and capacitance failures, but antenna and data fanout remain.
The first repair's 17 slew violations were on the capacitance-violating
`_17745_/Y` net and its loads in the SS/max corner; splitting `_09030_`
removed them. `net1321` was the other measured capacitance violator.

The original four fanout drivers were `clkbuf_leaf_70_clk/X` (19),
`clkbuf_3_1__f_clk/X` (17), `_14515_/Y` (17) and `fanout395/X` (17), against
16. The data nets had 16 signal loads plus an inserted antenna diode.
The repair flow preserves that SDC limit, inserts non-inverting buffers
only on internal single-driver nets, legalizes, reroutes and repairs antenna
again. With antenna margin 50 and ten iterations, the first repair actually
inserted 32 buffers in 16 nets; the capacitance candidate inserted 36 in 18.
The later antenna pass can add a diode to another 16-load net, so a single
fanout pass is not closure. The remaining six data drivers are
`fanout1048`, `fanout1303`, `fanout395`, `fanout628`, `fanout647`, `fanout747`.
Their final report is retained with the remaining antenna gate pins.

The pinned resizer excludes clock nets in `repair_design`, and
`repair_clock_nets` checks the clock-root wire rather than fanout:
[installed OpenROAD source](https://github.com/The-OpenROAD-Project/OpenROAD/blob/edf00dff99f6c40d67a30c0e22a8191c5d2ed9d6/src/rsz/src/RepairDesign.cc).
This is why the experimental `FanoutRepair` flow explicitly splits them.

The diagnostic baseline uses 12 ns, 1.5 ns transition, generic IO delay 25%,
and fanout 16, matching `iter8-io25-fanout16`. It does not prove the original
0.75 ns constraint or an actual external interface requirement. OpenLane's
[2.3.10 base SDC](https://github.com/chipfoundry/openlane2/blob/2.3.10/openlane/scripts/base.sdc)
uses integer arithmetic for integer period/percentage overrides; the actual
generated input and output delays were 3 ns. The design config and default
clock sweep were not relaxed by these experiments.

Run each checked-in spec with `--validate-only`, then the orchestrator. The
`flow` candidate field is supported explicitly; unknown flows fail validation.
The fixed net names belong to these exact input hashes and must not be reused
blindly after RTL or synthesis changes. Custom physical buffer flows are
excluded from the current surrogate's unsupported features.

`evidence-20261004/` retains input hashes, resolved configs, full final metrics,
verbatim DRV report sections, antenna summaries, buffer reports, commands,
and case pointers. Full binaries are in `/private/tmp/ppa-aes-closure-20261004/`.
An independent pre-route DB-copy check preserved every original connected
pin (119881) through the added buffers; it is not final formal equivalence.
One rejected CLI dictionary invocation is archived separately as invalid
configuration evidence, outside the measured case store.

## 2026-10-04: bounded coupled repair and SPEF audit

Two additional candidates tested two buffer/reroute/antenna passes with physical
fanout headroom. Global target 14 inserted many extra buffers and ended with
antenna=12, slew=14, cap=1, fanout=0. Keeping the global target 16 and tightening
only six measured nets to 14 ended with antenna=8, slew=14, cap=1, fanout=1.
Both are rejected; neither is an electrical closure result.

A further final-report audit found partially annotated inserted drivers caused
by identical net/instance names. Earlier repair/cap-v2 and these two cases now
carry explicit unverified warnings. In particular, the earlier cap-v2's zero
slew/cap counts cannot establish extracted electrical closure.

The implementation now uses distinct net/instance names, keeps instance serials
unique across passes and bounds `FANOUT_REPAIR_ROUNDS` to 1–3. The scorer checks
final SPEF annotation for every inserted repair driver at every reported timing
corner; missing reports also block acceptance.

The targeted candidate's supplementary STA replay changed only net names in
Verilog and SPEF NAME_MAP. The inverse transformation reproduces the original
files byte for byte; cell connections, geometry, RC values and SDC remain
unchanged. All nine replay corners have complete inserted-driver annotation and
confirm slew=14, cap=1, fanout=1, setup=0, hold=0. This STA-only replay is separate
from the original full-flow record. The unchanged full physical run reports
antenna=8, Magic/KLayout DRC=0, LVS=0; it is still rejected. Reports and preparation
script are in `evidence-20261004/targeted14-spef-replay/`.

All these AES numbers use diagnostic period 12ns, transition 1.5ns and generic
25% IO delays. They do not qualify the original 10ns / 0.75ns constraints or an
actual external interface. New buffering did not improve all required gates.

## 2026-10-05: is the antenna/fanout residue diode headroom, utilization, or the constraints?

Five full OpenLane 2.3.10 runs of the name-agnostic `FanoutRepair` recipe
(`closure-20261005-coupling.json`: no per-net limits, no driver cells), same
RTL, SKY130 HD, `DELAY 0` synthesis. `c0` is the control; `h15`, `u45`, `u55`
each change one axis from it; `orig` restores the design's own constraints.
Electrical counts are the worst of the nine corners; the measured case is
`reference-db/cases/aes__2026-10-05.json`.

| Run | Change from c0 | Antenna | Fanout | Slew | Cap | Worst setup (ns) | Area, um² | Wire, um |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| c0 | none (12 ns, 1.5 ns, 25% IO, fanout 16, util 35) | 5 | 4 | 0 | 1 | 1.650 | 134796 | 694329 |
| h15 | `FANOUT_REPAIR_LIMIT` 15 | 4 | 0 | 14 | 2 | 0.761 | 145841 | 756618 |
| u45 | `FP_CORE_UTIL` 45 | 4 | 1 | 0 | 3 | 0.526 | 131315 | 656023 |
| u55 | `FP_CORE_UTIL` 55 | 5 | 1 | 12 | 2 | 1.747 | 130136 | 629778 |
| orig | 10 ns, PDK defaults for transition, IO delay and fanout (10) | 10 | 2 | 657 | 1 | -1.467 | 137363 | 735077 |

Every run completed; Magic DRC, KLayout DRC and LVS are 0 in all five, and
setup and hold are 0 in the four runs at the diagnostic constraints. **None is
a final PASS.** `orig` has 118 setup and 127 hold violations at its worst corner
(`max_ss`; the case's 237 and 294 are the sums over corners), worst setup WNS
-1.467 ns and hold WNS -0.462 ns.

### What the final netlists show

`evidence-20261005/violator_sinks.py` finds the net each violating driver
drives in the final netlist and counts its sinks and how many are antenna
diodes (outputs in `violator_sinks.json` per run, netlists archived gzipped).

- **Every fanout violator is a diode effect.** The eight violators in the runs
  that carry a fanout limit are `c0` 4, `u45` 1, `u55` 1 and `orig` 2. The six
  in `c0`, `u45` and `u55`, and `orig`'s `fanout1236` (seven in all), have
  exactly the limit in real loads plus one diode (16 + 1, or 10 + 1). `orig`'s flop
  `_22427_/Q` has 9 real loads and 9 diodes against a limit of 10. Removing the
  diodes would leave none of the eight over its limit. This confirms, for these
  runs, the explanation the 2026-10-04 review left unverified.
- **`h15` removes the fanout violators, as that explanation predicts** (4 to 0),
  but is not a fix: area +8.2%, wire +9.0%, worst setup 1.650 to 0.761 ns, and
  14 slew and 2 cap violations. The 14 slew pins are one net: `_20258_/Y`
  (`o2bb2ai_2`) drives 12 antenna diodes and one real sink, at 1.518 ns against
  1.5 and 0.0819 pF against 0.0797. The same net carries the same 12 diodes in
  `c0`, where it passes, so it is a marginal net that `h15`'s routing tipped
  over, and it is the net the 2026-10-04 driver-upsize candidate targeted.
  `u55`'s 12 slew pins are likewise one net (`_19334_/Y`, 11 real sinks, no
  diodes, 1.5067 ns).
- **`fanout1321/X` (`buf_12`, 15 sinks) violates capacitance** in `c0` and
  `h15` (0.278 and 0.271 pF against 0.2); `u45` has two similar `buf_8`/`buf_12`
  resizer buffers. Capacitance on resizer buffers, not fanout, is what remains
  once the diode effect is absorbed.
- **Antenna never reaches 0, and the repair estimate does not carry over.** Rows
  of the antenna report at each step (`orig`'s final report has 11 rows for the
  10 counted by metrics, one pin appearing twice):

  | Run | after global route | repair 1 | repair 2 | repair 3 | final, after detailed route |
  | --- | ---: | ---: | ---: | ---: | ---: |
  | c0 | 71 | 7 | 5 | 2 | 5 |
  | h15 | 71 | 7 | 12 | 2 | 4 |
  | u45 | 62 | 11 | 6 | 0 | 4 |
  | u55 | 75 | 15 | 10 | 5 | 5 |
  | orig | 82 | 19 | 6 | 7 | 11 |
  | hu | 71 | 7 | 7 | 6 | 7 |
  | hl | 71 | 7 | 8 | 5 | 5 |
  | hul | 71 | 7 | 5 | 7 | 6 |

  (`hu`, `hl` and `hul` are the follow-up below.) The last repair runs at
  global-route level and detailed routing follows it. The final report exceeds
  that last estimate by 2 to 4 in four runs (`c0`, `h15`, `u45`, `orig`) and is
  within 1 of it in the other four. So the estimate does not reliably carry over
  to detailed routing, and why it does in some runs and not in others was not
  established.

### Reading

- **Utilization shortens wire monotonically and does not reduce antenna
  monotonically.** Wire is -5.5% at 45 and -9.3% at 55, area -2.6% and -3.5%.
  Antenna after global route is 71, 62, 75 and final 5, 4, 5. A 4 against a 5 is
  one pin from one run each; the flow repeats on identical inputs, but the
  follow-up shows how a small input change moves which marginal net fails. `u45` pays 1.1 ns of setup slack and gains
  two capacitance violations.
- **The recipe is nowhere near the original constraints.** At 10 ns and the PDK
  defaults it fails setup, hold, slew and antenna together. The earlier estimate
  of about -0.45 ns setup from the 10.45 ns minimum period was wrong: measured
  -1.467 ns. That minimum period came from a 12 ns run whose timing repair
  targeted 12 ns.
- All four diagnostic runs still use fanout 16 against the library's 10, 12 ns
  against 10 and 1.5 ns against 0.75, so none qualifies the original target.

### Limits

One run per candidate, no replicates and no seeds varied. The counts compare
single measurements. Run directories were temporary; the compact reports, final
netlists and hashes are in `evidence-20261005/` (`sources.json` per run). The
flow-code variant that repairs fanout after the last antenna repair was not
built: `h15` tests the same diode explanation with an override only.

## 2026-10-05 follow-up: closing h15's two non-antenna failures

`h15` failed on exactly two nets besides antenna: `_20258_` (slew and capacitance)
and `fanout1321` (capacitance). Three candidates add to `h15` only what targets
them (`closure-20261005-targeted-nets.json`): `hu` up-sizes `_20258_`
(`o2bb2ai_2` to `o2bb2ai_4`), `hl` gives `net1321` a physical limit of 8, `hul`
does both. The swap was checked against the pinned `ss_100C_1v60` Liberty
(sha256 `9b24f0db...`): identical pins and Boolean function
`(!B1&!B2)|(A1_N&A2_N)`, and `Y` max capacitance 0.0797 pF (`_2`) to 0.1492 pF
(`_4`). In each run the edit was applied: `_20258_` is `o2bb2ai_4` in `hu` and
`hul` only, and `net1321` (13 sinks) is split in `hl` and `hul` only.

| Run | Antenna | Fanout | Slew | Cap | Worst setup (ns) | Area, um² | Wire, um |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| h15 | 4 | 0 | 14 | 2 | 0.761 | 145841 | 756618 |
| hu | 6 | 0 | 0 | 1 | 0.866 | 145794 | 757035 |
| hl | 5 | 1 | 15 | 2 | 0.754 | 145771 | 757190 |
| hul | 6 | 0 | 0 | 0 | 0.647 | 145844 | 758374 |

Every run completed with Magic DRC, KLayout DRC, LVS, setup and hold at 0.
**`hul` is the first aes candidate with every gate except antenna at 0**
(fanout, slew, capacitance, setup, hold, DRC, LVS) and still not a PASS: 6
antenna violations, area 145844 um² (+8.2% against `c0`), and still at the
diagnostic constraints.

- **`hu` does what it was for.** `_20258_`'s 14 slew pins and its capacitance
  violation go; the one capacitance violation left is `fanout1321`
  (0.277 pF, `buf_12`).
- **`hl` alone is worse than `h15`.** Splitting `net1321` removes that capacitance
  violation, but `_20194_` (`o31ai_4`, 13 sinks of which 12 diodes) joins
  `_20258_` in slew and capacitance, and `_17277_/Y` (`nand4_4`, 15 real loads plus
  2 diodes) reaches 17 against a limit of 16: headroom of one sink does not
  absorb two diodes. `_20194_` is not among the violators in the other seven runs.
- **So `hul`'s zero is a margin result.** Both `_20258_` and `_20194_` sit at the
  capacitance edge. The flow reproduced a historical candidate exactly
  (2026-10-04), so a rerun of identical inputs should repeat; what the `hl`/`hul`
  pair shows is that one edit elsewhere decides which marginal net fails.
- **The diode count is bimodal, and the cluster is large.** In `c0`, 223 of 357
  diode-bearing nets have one diode and 26 have 11 or 12; the 36 nets with 8 or
  more hold 381 of 936 diodes (41%). `hul` is alike (39 nets, 37%). `orig` has no
  such cluster (at most 9). 11 to 12 is `GRT_ANTENNA_ITERS` (10) plus one, which
  suggests an iteration adds a diode to a net whose pin stays violated. That is a
  hypothesis; the iteration count was not varied. `_20258_` and `_20194_` are two
  of these nets, so they are examples of a population, not one-off cases.

### Static analysis of the diode cluster (no new run)

`evidence-20261005/diode_clusters.py` reads the archived final netlists and the
first antenna check as a net -> sink graph (output in `diode_clusters.txt`).

- **The cluster is single-sink nets.** In `c0` 35 of the 36 nets with 8 or more
  diodes drive exactly one real pin (`mux2` A1, `dfxtp` D, `and2` B); in `hul`
  37 of 39. None has more than 3 sinks. So the count is not one diode per sink
  pin, and the pin is not one of many on a shared net.
- **It does not follow the violation size.** Pearson r between a pin's P/R in the
  first check and its net's final diode count is 0.17 (71 pins); 7 pins that
  started below P/R 2 ended with 8 or more diodes, and the one pin at P/R 6 got 11.
- **`orig` weakens the iteration hypothesis.** `orig` used the same
  `GRT_ANTENNA_ITERS` 10 and `GRT_ANTENNA_MARGIN` 50 and shows no 11 to 12
  cluster (at most 9; 11 nets with 8 or more). So 10 iterations are at most a
  necessary condition. What `orig` does not share with `c0`/`hul` (clock 10 against
  12 ns, fanout 10 against 16, the recipe) is not isolated by these runs.
- **What is not established (answered 2026-10-10 below):** why a barely violating single-sink pin accumulates
  11 to 12 diodes, and whether the diodes shorten the repair or only repeat it.
  `run_spec_iter10.json` (ITERS 5 and 3 on the `hul` recipe) was the one-axis
  test; its result is below.

### GRT_ANTENNA_ITERS 5 and 3 on the `hul` recipe (2026-10-07)

Two full OpenLane 2.3.10 runs, 1255 s each, case `aes__2026-10-07`. The option
was applied: `resolved.json` and all three `repairantennas` step configs carry
5 and 3 respectively (`evidence-20261007/summary.json`).

- **No effect.** Both runs end with the same 6 antenna violations, 1116 diode
  cells, 39 nets with 8 or more diodes (20 at 11, 7 at 12) and the same area
  (145844 um2) as `hul` at ITERS 10. The final netlists of ITERS 5, 3 and the
  archived ITERS 10 run have the same sha256. Single run each, but identical
  bytes leave no difference for replicates to resolve.
- **So the 11 to 12 cluster is not ITERS plus one.** ITERS 3 gives the same
  11 to 12. The earlier reading is withdrawn; `orig` already pointed that way.
  The repair apparently finishes within three iterations on this design, so a
  larger limit is never reached; that was an inference from the identical
  netlists when written, and the OpenROAD log reading below confirms it.
- **Still open:** what sets the 11 to 12 diodes on a single-sink net and the 6
  residual pins. Not tested here: `GRT_ANTENNA_MARGIN` (held at 50 in every
  coupling run; 10 was never combined with this recipe), diode cell choice, and
  the RTL-level splitting of the nets. The result is still diagnostic
  constraints, +8.2% area and not a PASS.


### OpenROAD log reading and an independent repeat of the ITERS runs (2026-10-09)

Case `aes__2026-10-09`: the same three-run question (`hul` recipe at ITERS 5, 3
and an unchanged 10) run again as `closure-20261009-antenna-iters.json`, started
before the 2026-10-07 entry above was noticed. It adds two things; the
outcome is the same. Compact evidence is in `evidence-20261009/`.

- **The ITERS 10 run reproduces the archived `hul` bit for bit**: the same
  metrics, antenna pins and final-netlist sha256. ITERS 5 and 3 have that same
  netlist sha256 too. The flow repeated on identical inputs in this instance.
- **The log gives the reason.** In `diodeinsertion.log` of the three repair
  steps, `repair_antennas` executes 3 or 4 iterations and the violations found
  per iteration fall 215, 42, 1, 0 (step 1), 126, 38, 4, 0 (step 2) and 43, 24, 0
  (step 3). The fourth iteration only confirms 0, so any cap of 3 or more gives
  the same result. The knob was applied: the step configs carry 3, 5 and 10, and
  the ITERS 3 logs stop at iteration 3. The iteration cap is therefore not
  what sets the 11 to 12 diodes. `GRT_ANTENNA_MARGIN` is the other value passed
  to the same command (`repair_antennas -iterations ... -ratio_margin ...`) and
  was not varied here.

### GRT_ANTENNA_MARGIN 25 and 10 on the `hul` recipe (2026-10-09)

Two full OpenLane 2.3.10 runs, case `aes__2026-10-09__011741`, spec
`run_spec_iter11.json`, ITERS back at 10. The option was applied (`resolved.json`
and the repair step configs carry 25 and 10). Numbers in
`evidence-20261009/margin_summary.json`.

| MARGIN | antenna | diodes | nets with 8+ diodes | slew / cap | area um2 |
|---|---|---|---|---|---|
| 50 (`hul`) | 6 | 1116 | 39 | 0 / 0 | 145844 |
| 25 | 10 | 415 | 6 | 0 / 0 | 144044 |
| 10 | 11 | 236 | 1 | 2 / 1 | 143596 |

- **MARGIN is a lever, unlike ITERS.** The diode cluster follows it: lowering
  the margin removes most diodes and the 11 to 12 nets, and area falls 1.2% and
  1.5%. This supports "the cluster is margin-driven repair"; it does not show
  the mechanism per net.
- **The cost is the residual antenna count.** 6 at 50, 10 at 25, 11 at 10, and at
  10 two slew and one capacitance violation return. Neither run is a PASS;
  `hul` at 50 is still the run with the fewest violations. Single runs; whether
  the 10 versus 11 difference means anything is not established.
- **The seconds of the margin 10 run (2752 s) include a cold Docker start** and
  are not a cost estimate.
- Not tested: margins between 25 and 50 (for example 35), diode cell choice,
  RTL-level net splitting.

### GRT_ANTENNA_MARGIN 75 and 100 on the `hul` recipe (2026-10-09)

Two full OpenLane 2.3.10 runs above the 50 of `hul`, case
`aes__2026-10-09__232437`, spec `closure-20261009-antenna-margin-high.json`, ITERS
10. The option was applied (`resolved.json` and the repair step configs carry 75
and 100). Evidence in `evidence-20261009-margin-high/`. Rows for 10, 25 and 50 are
from the section above.

| MARGIN | antenna | diodes | nets with 8+ diodes | fanout / slew / cap | area um2 |
|---|---|---|---|---|---|
| 10 | 11 | 236 | 1 | 0 / 2 / 1 | 143596 |
| 25 | 10 | 415 | 6 | 0 / 0 / 0 | 144044 |
| 50 (`hul`) | 6 | 1116 | 39 | 0 / 0 / 0 | 145844 |
| 75 | 3 | 4134 | 166 | 3 / 2 / 2 | 154059 |
| 100 | 64 | 0 | 0 | 0 / 0 / 0 | 143006 |

Magic DRC, KLayout DRC, LVS, setup and hold are 0 in both. Neither is a PASS.

- **MARGIN 100 is not a result for 100.** OpenROAD accepts a ratio margin in
  [0, 100). At 100 `repair_antennas` only warns (`GRT-0215`, present in the log of
  all three repair steps), the allowed ratio is multiplied by `1 - 100/100 = 0`,
  and the checker runs the PAR check only for a non-zero ratio, so it reports 0
  violations in the first iteration and inserts no diode (source excerpts at the
  pinned revision in `openroad_source_excerpts.txt`; the PSR check's zero handling
  was not read). The OpenLane variable is an unbounded int and `--validate-only`
  does not catch it. Read the run as a **no-repair control**: 71 antenna
  violations after global route, 64 after detailed routing, 0 diodes, area
  -1.9% against `hul`. The lesson is the missing range check, not a margin.
- **Within [0, 100) the residual antenna count keeps falling with the margin**
  (11, 10, 6, 3) and the diode count rises faster than it falls (236, 415, 1116,
  4134; 3.7 times `hul` at 75). At 75 the non-antenna gates break again and area
  is +5.6% against `hul`. No point in the range clears both.
- **The 75 failures are of two kinds.** Three fanout violators (`_11103_`
  `clkinv_4` 18 sinks of which 14 are diodes, `_22441_/Q` `dfxtp_4` 17 of which 15,
  `_20345_` `o2111ai_4` 18 of which 6): on low-fanout nets the diodes alone
  approach or pass the limit of 16, which the physical limit of 15 cannot
  absorb. And `_19764_` (`a211oi_1`, one sink, no diode) fails slew (1.732 against
  1.4645 ns) and capacitance (0.0329 against 0.0266 pF): a weak driver, not a
  diode effect. `clkbuf_0_clk` exceeds 0.2 pF by 0.0016 pF with 8 diodes among 12
  sinks.
- **The first repair step needs 7 iterations at 75** (violations found 634, 177,
  28, 5, 3, 1, 0) against 4 at 50, so a cap below 7 would cut it short at this
  margin although a cap of 3 suffices at 50. The 3 residual antenna pins are at
  P/R 1.03 to 1.47.
- Single runs, one point each at 75 and 100. Between 50 and 75 (for example
  60 to 65) is untested and is where a point with fewer than 6 antenna
  violations and no new failures would have to be.


### GRT_ANTENNA_MARGIN 60 and 65 on the `hul` recipe (2026-10-09)

Two full OpenLane 2.3.10 runs between `hul`'s 50 and the 75 above, case
`aes__2026-10-09__001338`, spec `closure-20261009-antenna-margin-mid.json`, ITERS 10.
Both values are inside OpenROAD's accepted [0, 100). The option was applied
(`resolved.json` and the repair step configs carry 60 and 65). Evidence in
`evidence-20261009-margin-mid/`.

| MARGIN | antenna | diodes | nets with 11-12 diodes | fanout / slew / cap | area um2 |
|---|---|---|---|---|---|
| 50 (`hul`) | 6 | 1116 | 27 | 0 / 0 / 0 | 145844 |
| 60 | 7 | 1790 | 50 | 0 / 14 / 2 | 147618 |
| 65 | 6 | 2309 | 70 | 2 / 8 / 1 | 149069 |
| 75 | 3 | 4134 | 132 | 3 / 2 / 2 | 154059 |

Magic DRC, KLayout DRC, LVS, setup and hold are 0 in both. Neither is a PASS, and
neither is better than `hul`: **the hypothesis is falsified.** No point between 50 and
75 gives fewer than 6 antenna violations with the other gates at 0; new failures
appear as soon as the margin leaves 50.

- **Across 10, 25, 50, 60, 65, 75 the residual antenna count is not monotone**
  (11, 10, 6, 7, 6, 3) while the diode count is (236, 415, 1116, 1790, 2309, 4134).
  Above 50 every margin brings back failures; the antenna count only drops
  below 6 at 75, where three fanout violations come with it.
- **The new failures are the same kind of net each time**: a weak or mid-size
  driver loaded with many diodes. At 60 `_20763_` (`xnor2_2`, 11 diodes among 12
  sinks) and `_20194_` (`o31ai_4`, 12 of 13) exceed slew and capacitance; `_20194_`
  also failed in `hl`. At 65 `_11666_` (`xor2_1`, 6 of 7 sinks) exceeds slew and
  capacitance, `_22403_/Q` (`dfxtp_2`, 13 diodes among 20 sinks, 7 real loads)
  and `_17277_/Y` (`nand4_4`, 15 real loads plus 2 diodes, also failed in `hl`)
  exceed fanout 16.
- **At 65 the iteration cap binds for the first time in these runs.** Repair
  steps 2 and 3 run all 10 iterations with one violation left from iteration 5
  or 6 on (231, 76, 7, 3, 1, 1, 1, 1, 1, 1 and 97, 60, 5, 2, 1, 1, 1, 1, 1, 1);
  step 1 ends at 0 after 7. At 50, 60 and 75 every step ended at 0 within 7
  iterations. The 2026-10-07 result (ITERS does nothing) was measured at margin
  50 and still holds there; at 65 it matters (next section).
- The driver size-up that cleared `_20258_` cannot be reused for these nets:
  `driver_size.compatible_family` accepts only the `buf`, `clkbuf` and
  `o2bb2ai` families, and the failing cells here are `xnor2`, `xor2`, `o31ai`,
  `nand4`, `dfxtp` and (at 75) `a211oi`.
- Single runs. The failures move between margins, so a neighbouring value could
  land differently; nothing here measures that.


### GRT_ANTENNA_ITERS 5 and 20 at MARGIN 65 (2026-10-09)

Two full OpenLane 2.3.10 runs of the MARGIN 65 run above with only
`GRT_ANTENNA_ITERS` changed, case `aes__2026-10-09__004442`, spec
`closure-20261009-antenna-margin65-iters.json`. The option was applied (`resolved.json`
and the repair step configs carry 5 and 20). Evidence in
`evidence-20261009-margin65-iters/`; the ITERS 10 row is the run above.

Hypothesis: the stuck violation adds a diode every iteration, so the diode
count (2309) falls at ITERS 5 and rises at ITERS 20. **The mechanism holds; the
effect is too small to move the total.**

| ITERS | antenna | diodes | nets with 11-12 diodes | fanout / slew / cap | area um2 |
|---|---|---|---|---|---|
| 5 | 3 | 2333 | 72 | 1 / 14 / 2 | 149072 |
| 10 | 6 | 2309 | 70 | 2 / 8 / 1 | 149069 |
| 20 | 5 | 2314 | 72 | 4 / 0 / 0 | 149082 |

Magic DRC, KLayout DRC, LVS, setup and hold are 0 in both new runs. Neither is a PASS.

- **A stuck iteration inserts one diode; the total barely follows the cap.** The repair log reports `Inserted N diodes` (`GRT-0015`) per
  iteration, and the sums over the three repair steps equal the final netlist's
  diode count exactly (2333 and 2314; every diode comes from these steps). In
  the ITERS 20 run repair step 2 leaves one violation from iteration 5 to
  iteration 20 and inserts one diode and reroutes one net in each of those 16
  iterations, 16 diodes of 2314 (0.7%), and the violation stays. The totals
  (2333, 2309, 2314; at most 14 on any net in all three) barely move. Step 3 of
  that run needs 12 iterations (99, 44, 5, 4, 4, 3, 3, 3, 2, 2, 1, 0) and step 1
  ends at 0 after 7.
- **The result still depends on ITERS here.** Antenna is 3, 6, 5 and the
  failing nets differ: at ITERS 5 `_20763_` (`xnor2_2`, 12 diodes among 13
  sinks, also failing at 60) fails slew (14 pins) and capacitance, `fanout1284/X`
  (`buf_8`) is 0.001 pF over, and `_15187_` (`nand4_4`, 15 real loads plus 3
  diodes) fails fanout; at ITERS 20 slew and capacitance are clean and four
  fanout violators remain: `_22403_/Q` (`dfxtp_2`, 7 real loads and 14 diodes),
  `_22447_/Q` (`dfxtp_4`, 7 and 11), `_17277_/Y` (`nand4_4`, 15 and 2) and
  `fanout1179/X` (`buf_4`, 13 and 4). At ITERS 5 all three repair steps stop at
  the cap with 1, 2 and 2 violations left.
- **Where the repair converges, ITERS is inert; where it does not, it changes the
  outcome although it hardly changes the diode count.** MARGIN 50, 60 and 75 end every repair
  step at 0 within 7 iterations, so a cap of 7 or more cannot matter there (checked
  directly only at 50, for ITERS 3, 5, 10). MARGIN 65 does not converge, so where the loop is cut
  decides which routes and which nets end up marginal. The antenna count at 65
  is a property of the cap as much as of the margin, which weakens
  any ordering of margins by single antenna counts there (60: 7, 65: 3 to 6, 75: 3).
- `_14993_/A` (`text_in_r[26]`, P/R 1.38) is among the final antenna pins at all
  three ITERS, as in several earlier runs. What the stuck violation is was not
  identified.


### What the 11-diode cluster and the stuck violation are (analysis, 2026-10-10)

No new run. It reads the archived final netlists, the per-iteration repair logs
(`antenna_repair_iterations.json`, with the diodes each iteration inserted) and
the OpenROAD source at the revision OpenLane 2.3.10 pins (`edf00dff...`):
`GlobalRouter.cpp` `repairAntennas`, `RepairAntennas.cpp` `repairAntennas`,
`AntennaChecker.cc` lines 796 to 880 and `AntennaChecker.hh` line 238.
`evidence-20261009-margin65-iters/stuck_nets.py` names the nets that received
the diodes of the late iterations (output beside each run).

**What the source does.** For every gate that violates PAR or PSR the checker
adds one diode's diffusion area at a time and re-checks, against the allowed
ratio times `1 - margin/100`, until the gate passes. It stops when the count
exceeds `max_diode_count_per_gate = 10`; the count is then 11, the violation is
recorded with it, and `RepairAntennas::repairAntennas` inserts that many diodes
per gate. A count of 0 would raise `GRT-0243`; it never appears in these logs.
The loop then re-checks only the nets it re-routed.

- **The 11 to 12 diode cluster is gates that hit that cap.** Nets with 11 diodes:
  20, 39, 55, 100 at MARGIN 50, 60, 65, 75 (10 diodes: 4, 3, 8, 8; `orig` has
  none above 9). All 11 diodes of a net arrive in one iteration: 55 of 55 nets
  (ITERS 5 run) and 55 of 56 (ITERS 20 run); 17 of 17 and 16 of 16 for 12.
  The set nests as the margin rises: 90 to 94% of the nets with 10 or more
  diodes at one margin have them at the next (31, 53, 83, 147 nets). This
  replaces the "ITERS plus one" reading: two different 10s. ITERS was 10 in those
  runs, the cap is a constant of the checker, and ITERS 3 and 5 gave the same cluster.
- **Why diodes stop helping.** The tech LEF gives the metal layers listed there
  (lines 119 to 295 of `sky130_fd_sc_hd__max.tlef`) `ANTENNADIFFSIDEAREARATIO
  PWL((0 400) (0.0125 400) (0.0225 2609) (22.5 11600))`, and `diode_2` has
  `ANTENNADIFFAREA 0.4347`. The first diode takes the allowed side-area ratio
  from 400 to about 2774; each further diode adds about 174 (slope 400 per um2).
  Ten diodes give about 4339, 1.56 times one. A gate that needs more than that
  relief at the margin-tightened ratio gets 11 diodes for little gain. Which
  gates are such, and their partial areas, were not archived, so this is the
  structure, not a per-gate reconstruction; why `orig` has no such gate was not
  isolated.
- **The stuck violation is not a violation of the real rule.** In the ITERS 20 run
  the 19 diodes of the iterations that found 1 to 3 violations in repair step 2
  went mostly to two nets driven by `clkbuf_8` buffers that `FanoutRepair`
  inserted to split the clock: `fanout_repair_net_1463` (9 of its 10 diodes; five
  `clkbuf_8` sinks) and `fanout_repair_net_1462` (8 of 11; two `clkbuf_8` and a
  `dfxtp_4`). In the other steps of both runs the late iterations work on data
  nets that already carry many diodes, `\u0.w[0][29]` (`dfxtp_2`, 14 diodes) and
  `_00757_` (`o211ai_4`, 9) in both runs. (`fanout_repair_net_29` and `_30` are
  `buf_4` data nets: the name does not mean a clock.) None of these nets is in the
  report of `check_antennas` that follows each of the six steps (3, 7, 3 and 4, 9,
  1 violations, all on other nets). So the loop keeps adding a diode to nets that
  satisfy the real rule and miss its margin-tightened target.
- **Most diodes are not on real-rule violators.** The first check lists 64 nets
  that violate the real rule; 84 to 91% of the diodes (MARGIN 50 to 75) sit
  elsewhere. This is an upper bound: nets can start violating after later
  re-routes, and nets split by `FanoutRepair` get new names.
- Not established: why a diode per iteration does not end the stuck violation, and
  why the checker's count of 1 after the first diode does not hold after
  legalization and re-routing.


### Next candidates

- **(Done 2026-10-07: no effect, see above.)** Lower `GRT_ANTENNA_ITERS` on the `hul` recipe (for example 5 and 3), one
  axis. If the 11 to 12 cluster is one diode per iteration it should shrink to
  about the iteration count plus one, which should relieve slew, capacitance and
  fanout on those nets; the cost to watch is the final antenna count.
- **(Done 2026-10-09, see above.)** `GRT_ANTENNA_MARGIN` on the `hul` recipe: a lever for diode count, trading against residual antenna count.
- **(Done 2026-10-09, 75 and 100.)** MARGIN above 50: 75 gives 3 antenna but 3 fanout,
  2 slew and 2 cap violations; 100 is out of range and disables the repair.
- **(Done 2026-10-09, 60 and 65.)** MARGIN between 50 and 75: no point has fewer than 6
  antenna violations with the other gates at 0; 60 and 65 bring back slew,
  capacitance and fanout on diode-loaded nets.
- **Driver size-up beyond `buf`/`clkbuf`/`o2bb2ai`** (flow work): the nets that fail
  across the margin runs are `xnor2`, `xor2`, `o31ai`, `nand4`, `dfxtp`, `a211oi`.
  Each family would need the Liberty function check done for `o2bb2ai`. It
  removes known failures only; the margin runs show new ones appear elsewhere
  (`_20258_` at 50, then `_20194_`, `_20763_`, `_11666_`), so this is a way to
  explore the residue, not a closure plan.
- **(Done 2026-10-09, 5 and 20.)** ITERS at MARGIN 65: the diode total barely follows
  the cap (one diode per stuck iteration), the outcome does.
- **(Done 2026-10-10.)** The 11-diode cluster is the checker's 10-diode cap and the stuck
  violation is the margin-tightened target on nets that satisfy the real rule (section above).
- **Why the first diode's relief is not enough for those gates.** Archive each gate's
  partial side and area ratios in the first check (the log does not print them) so the
  gates that hit the cap can be told apart from those that do not; then see whether a
  setting or the RTL (splitting those nets) changes them.
- **(Done: `candidate_plan.TOOL_RANGES`.)** A range check for `GRT_ANTENNA_MARGIN`
  (integer, at least 0 and below 100) in `--validate-only` and at run start. The
  tool only warned at run time, after the 25 minutes the run takes, and the flow
  reported a repair-free run as an ordinary FAIL. The table holds one variable;
  add another only with the same kind of evidence. As a consequence
  `closure-20261009-antenna-margin-high.json`, the record of the run that exposed
  the gap, no longer passes `--validate-only` (its 100 candidate is rejected).
- **Antenna itself**: 4 to 6 pins at P/R 1.0 to 2.4 after detailed routing, a
  different set in each run (`text_in_r[26]` recurs). Margin sets the diode
  count but no value from 10 to 75 clears antenna and the other gates together, and
  the iteration cap does not bind at 50; repair after detailed routing is not
  supported by the step counts above.
- A qualification attempt needs the original 10 ns, 0.75 ns and fanout 10; `orig`
  shows the recipe is far from it, and nothing here changes that.
