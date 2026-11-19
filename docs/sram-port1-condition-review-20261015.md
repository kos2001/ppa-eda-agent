# SS port1 condition review, 2026-10-15

The completed SS port1 simulation failed the 10 ns characterization point's
timing checks. It does not establish failure or success of the original
20 ns physical design. This review reads existing evidence only: no new
simulation, numerical change or Liberty replacement was performed.

## Measured result and event timing

The simulator completed in 6,816.598 seconds, with exit 0 and all 27 requested
measurements finite. The independent storage-polarity and output-rail checks
passed. Two timing checks failed:

| Measurement | Result | Bound at 10 ns period | Excess |
| --- | ---: | ---: | ---: |
| Rising clock-to-output delay, `delay_lh1` | 5.30368 ns | 5 ns | 0.30368 ns |
| Sense-enable delay, `delay_sen1` | 5.15392 ns | 5 ns | 0.15392 ns |

The actual `.meas` records use falling `clk1` at its 0.8 V crossing.
`delay_lh1` selects events after 130 ns: trigger 135.350 ns, target
140.654 ns. `delay_sen1` selects events after 55 ns: trigger 55.350 ns,
target 60.5039 ns. Both targets occur after the following clock's rising
0.8 V crossing, at 140.350 ns and 60.350 ns respectively. These timestamps
are rounded log outputs; use the reported delay values for comparisons.

The falling output measurement reaches its target at 56.3359 ns, before
the recorded sense-enable event at 60.5039 ns. Scalar measurements alone
therefore cannot establish that these events describe the same causal
sense/read operation. Event selection and clock-phase behavior remain an
open diagnostic question; no waveform-based mechanism is asserted here.

The half-period rule comes from pinned OpenRAM revision
`b2b069ce119d1488cbe6883b2240bceb5c7ce29a`,
`compiler/characterizer/delay.py`: `check_sen_measure`, lines 871–880,
uses half a period when `meta_add_delay` is set; `check_valid_delays`,
lines 1008–1049, bounds both delays and falling slew by half a period,
and rising slew by a full period. The local checker also requires positive
delays and independently checks every stored bit value. It does not replace
one measured edge with the other.

## Comparison with retained physical usage

The effective design config specifies a 20 ns clock. Across the SS
interconnect corners, actual `clk1` rise slew is 0.037010–0.037421 ns and
fall slew is 0.035136–0.035451 ns. The SPICE point uses 0.560 ns input slew
and 27.56 fF load. All observed output loads across the nine STA corners
range from 6.764022 to 14.844512 fF. These are different operating conditions,
not grounds to reclassify the failed point.

For the retained max/SS `dout1[31]` setup path, falling launch is nominally
10 ns and reaches macro `clk1` at 11.594775 ns. The receiving positive-edge
register has required arrival 21.057755 ns after uncertainty and setup.
The available local-launch-to-required window is 9.462980 ns; output-net
propagation still consumes part of it. The path's reported 0.392683 ns macro
delay comes from the unqualified TT Liberty mapped into SS and is not a
measured SS delay. This path cannot certify signoff.

## Smallest next hypothesis

Regenerate one SS port1 point at **20 ns period only**, retaining address255,
bit31, port0 writes, 0.560 ns input slew, 27.56 fF load, full macro netlist,
and existing tolerances/solver settings. Test whether the original point's
timing failure persists at the intended clock period. Keep all independent
checks and inspect the measured event timestamps again. Changing the period
also changes the generated stimulus and measurement windows; simply applying
a 10 ns bound to the old result is invalid.

The 15-cycle transient horizon becomes 300 ns rather than 150 ns. Simple
runtime scaling would suggest about 227 minutes versus the previous 114
minutes, but that is an estimate, not a simulator prediction. A 120-minute
cap may be insufficient. Any execution needs a separately declared attempt
and wall-time budget; this review starts no run and authorizes no automatic
retry or budget expansion. Even a successful point remains unqualified for
other slews, loads, addresses, bits, setup/hold axes or TT/FF PVT.

Three qualification obligations remain:

1. Measured, validated SS/TT/FF tables across required slew/load ranges for
   both read ports.
2. Setup/hold axes and independent internal SRAM functional coverage beyond
   this selected address/bit sequence.
3. Qualified per-PVT Liberty integration followed by all-corner extracted STA
   and physical signoff.

Exact input hashes, primary measurement lines, per-corner clock slews and
the selected path excerpt are retained in
`pipeline/designs/sram_wrapper/experiments/evidence-20261015-port1-condition-review/review.json`.
