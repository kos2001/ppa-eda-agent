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
