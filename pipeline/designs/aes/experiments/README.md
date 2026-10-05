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

### Next candidates

- **Lower `GRT_ANTENNA_ITERS` on the `hul` recipe** (for example 5 and 3), one
  axis. If the 11 to 12 cluster is one diode per iteration it should shrink to
  about the iteration count plus one, which should relieve slew, capacitance and
  fanout on those nets; the cost to watch is the final antenna count.
- **Antenna itself**: 4 to 6 pins at P/R 1.0 to 2.4 after detailed routing, a
  different set in each run (`text_in_r[26]` recurs). Diode count and iterations
  are the lever the data points to; flow work such as repair after detailed
  routing is not supported by the step counts above.
- A qualification attempt needs the original 10 ns, 0.75 ns and fanout 10; `orig`
  shows the recipe is far from it, and nothing here changes that.
