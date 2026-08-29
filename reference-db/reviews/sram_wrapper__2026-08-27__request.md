# Human-in-the-loop review request — sram_wrapper (2026-08-27)

Case file: reference-db/cases/sram_wrapper__2026-08-27.json
Outcome: no candidate met targets — no auto-repairable pattern matched, needs a human/subagent decision
Stages the real run outcomes hit: physical_constraint

## Relevant subagents (dispatch these, in this order)

- `feedback-optimizer` — .claude/agents/feedback-optimizer.md
- `physical-constraint-evaluator` — .claude/agents/physical-constraint-evaluator.md

## Precedent from reference-db (retrieved, not assumed)

3 prior case(s) matched. Each is real recorded output; the match reason is stated so it can be discounted if it does not actually apply.

### sram_wrapper — 2026-08-21  (shares GRT-0097, PDN-0231, RSZ-0090, macro-power-unconnected — still open)

- outcome: no candidate met targets after all iterations
- stop reason: None
- winner: none
- reviewed by: physical-constraint-evaluator, odb-measurement, sta-measurement, liberty-measurement

```
CORRECTED after checking the macro's own liberty file directly (an earlier version of this diagnosis speculatively blamed the clk0/clk1 pins without verifying against the .lib — that was wrong; always check the actual source before committing a diagnosis). Confirmed root cause: sky130_sram_1kbyte_1rw1r_32x256_8_TT_1p8V_25C.lib defines an explicit max_transition of 0.04ns (both units confirmed: time_unit=1ns, capacitive_load_unit=1pF) on its addr0, wmask0, and addr1 input buses (lines 188, 225, 427 of the lib). RSZ-0090's reported "best achievable transition time is 0.043ns with a load of 0.01pF" matches this almost exactly — 0.01pF is essentially just that bus's own pin capacitance (0.00689pF from the lib, i.e. no additional wire), meaning even the strongest resizer-available buffer driving directly into the pin with zero wire cannot quite meet the macro's 0.04ns spec. This is NOT fixabl

[...truncated; full text in reference-db/cases/sram_wrapper__2026-08-21.json]
```

### sram_wrapper — 2026-08-26  (shares GRT-0097, PDN-0231, RSZ-0090, STA-1140 — still open)

- outcome: no candidate met targets after all iterations
- stop reason: max_iterations_reached
- winner: none

```
INVALID EXPERIMENT — kept as the record that motivated a fix, not as evidence.

The two non-baseline candidates set RE_BUFFER_CELL, which is an OpenLane 1 variable name with no OpenLane 2 equivalent. OpenLane logged "An unknown key 'RE_BUFFER_CELL' was provided" and ran the un-overridden config, so cand-rebuf8 and cand-rebuf12 are duplicates of cand-baseline. Their identical 0.043 ns failures are not evidence that stronger repair buffers fail to help; that hypothesis is untested.

This is the second ignored-override false conclusion in this project (the first was STD_CELL_LIBRARY as a config override, which faked a 0.00% technology delta). run_stage.reject_ignored_overrides now fails any run whose override OpenLane reports as an unknown key, so a candidate like these two can no longer be reported as a result. Re-running this spec today would raise instead of producing this case.

See the

[...truncated; full text in reference-db/cases/sram_wrapper__2026-08-26.json]
```

### spm — 2026-08-29  (similar topology (distance 0.219) — RESOLVED)

- outcome: passed
- stop reason: winner_found
- winner: sweep-util-55


## Existing diagnosis (read before dispatching — don't re-derive what's already known)



[2026-08-28T13:38:51Z] automatic-macro-placement experiment (run, not proposed):

The case's own recorded next step was to let a tool place the macro and measure the
result against the 249 um hand-placed baseline. That has now been done, and the answer
is NO: automatic placement makes this design's binding constraint worse.

AVAILABILITY, corrected. An earlier note said automatic macro placement was unavailable
in OpenLane 2.3.10 because OpenROAD.BasicMacroPlacement is a stub (get_script_path
raises NotImplementedError) - true of OpenLane, false of the toolchain. The OpenROAD
binary in the same image ships rtl_macro_placer (Hier-RTLMP, OpenROAD src/mpl2),
macro_placement, place_macro and write_macro_placement. Driving OpenROAD directly, as
odb_query.py already does, needs no custom flow at all.

WHAT BOTH PLACERS CHOSE. From the hand position (110, 150) N:
  rtl_macro_placer (Hier-RTLMP)  -> (10.35, 15.79) MY, moved 167.2 um
  macro_placement (annealing)    -> (10.20, 15.64),    moved 167.4 um
They agree within 0.2 um, so the corner is not one algorithm's quirk.

MEASURED CONSEQUENCE, via odb_query on a real re-run to GlobalPlacement with the chosen
location applied (designs/sram_wrapper_autoplace). Max pin-to-pin span, um:

  net        hand(110,150)N   auto(10.35,15.79)MY
  addr1[1]        288.9              507.4
  addr1[3]        246.2              505.7
  addr1[2]        273.4              502.5
  addr1[7]        137.2              441.0
  addr1[5]        114.4              430.1
  addr1[0]        249.2              322.3
  addr0[5]        295.7              215.4   (improved)
  addr0[1]        196.1              153.4   (improved)

  worst span       298.6              507.4   -> 70 percent WORSE

The addr0 (write-port) bus improves and the addr1 (read-port) bus roughly doubles.
Worst case, which is what a max_transition limit is judged on, goes from 298.6 um to
507.4 um.

WHY, and why it is not a tool bug. Both placers minimise total wirelength. This macro's
total is dominated by its 32-bit data buses (din0, dout0, dout1 = 96 bits) rather than
by the two 8-bit address buses. Trading address length for data length is correct for
their objective and wrong for ours, because the 0.04 ns max_transition in the macro's
liberty sits on addr0/addr1 and on nothing else. Neither placer is told that, and no
available knob expresses it - -wirelength_weight scales the whole objective, not one bus.

STATUS: the hand placement at (110, 150) is better than what either automatic placer
selects, and this line of attack is closed. sram_wrapper stays OPEN. What the numbers
now point at is per-net control rather than macro position: keeping the addr drivers
within ~145 um (lib_query's limit for buf_12) is a placement-region or repeater question
about eight nets, not a question about where the macro sits.

[2026-08-29T09:20:00Z] web-research + measurement pass. Three things were wrong,
two of them ours.

1. PDN_MACRO_CONNECTIONS had its fields swapped. The documented format is
   `<instance_rx> <vdd_net> <gnd_net> <vdd_pin> <gnd_pin>`, net before pin. Ours
   read `u_sram vccd1 vssd1 vccd1 vssd1` - the macro's pin names in the net slots,
   naming nets this design does not have (its rails are VPWR/VGND, per VDD_PIN /
   GND_PIN). OpenLane said so and only at WARNING level:
   `[PDN-0231] u_sram is not connected to any power/ground nets`. Corrected to
   `u_sram VPWR VGND vccd1 vssd1`; the warning count goes 1 -> 0. The macro had
   no power connection in the generated PDN for the life of this case.

2. The max_transition limit is on the ADDRESS INPUTS, not the data outputs.
   Parsing the .lib's bus blocks: `max_transition : 0.04` appears on addr0,
   addr1 and wmask0, all direction:input. dout0 and dout1 carry none. This case
   and the RTL comment both recorded it as a dout0/dout1 constraint, which is
   what sent the previous session after data-bus wirelength and macro position.

3. The 0.04 ns is a characterisation ceiling, not an electrical requirement.
   Every timing table in that .lib is indexed `index_1("0.00125, 0.005, 0.04")`.
   The constraint is where the sweep stopped. The standard cell library, for
   comparison, is characterised to 1.5 ns.

WHAT RSZ-0090 ACTUALLY IS. A feasibility precheck, not a violation report. It
compares the tightest max_transition in the design against the best any
available buffer can do at 0.01 pF and aborts before doing any work - whether or
not a single net violates. That is why no amount of placement or repair tuning
moved it.

RESULT OF RELAXING IT (0.04 -> 0.05 on those three pins, in a derived lib kept at
designs/sram_wrapper/lib/*.relaxed.lib with its justification in the file):
the flow goes from aborting at step 31 of 78 to reaching step 58 - through
placement, CTS, detailed routing and post-PnR STA across nine corners. Setup and
hold come back clean: WNS +9.39 ns, 0 setup violations, 0 hold violations, 0
max-cap violations. It then fails in Magic on unrelated sky130 SRAM GDS layer
mapping (layer 33 datatype 42/43, layer 22/21), which is a separate problem.

WHY THAT IS NOT A FIX. The addr pins settle at 0.3-0.9 ns, up to 22x past where
the model stops. Every delay STA reports on those paths is extrapolated off the
end of the table and looks exactly like a measurement. The clean WNS is not
trustworthy for them. pipeline/model_validity.py now detects exactly this and
marks such a run `unverified` rather than passed.

THE REMAINING PROBLEM IS DRIVE, NOT DISTANCE - which also retires the previous
next step. Measured spans on the routed design: addr1[7] is 138.6 um, addr0[3]
is 3.5 um. A buf_12 meets 0.05 ns at 138.6 um (0.0372 ns computed from the
liberty). The pins are instead driven directly by xnor2_2 / nand2_1 / inv_2 with
no buffer at all - xnor2_2 into that load is 0.27 ns. So "keep addr drivers
within ~145 um" was aiming at a constraint that is already satisfied.

FIVE LEVERS TRIED, ALL NULL: DESIGN_REPAIR_MAX_SLEW_PCT=0,
GRT_DESIGN_REPAIR_MAX_SLEW_PCT=0, DESIGN_REPAIR_MAX_WIRE_LENGTH=145,
GRT_DESIGN_REPAIR_MAX_WIRE_LENGTH=145, MAX_TRANSITION_CONSTRAINT=0.10 and 0.5,
SYNTH_CLK_DRIVING_CELL=clkbuf_16/X. The last produced byte-identical results.
The resizer inserts buf_1/buf_2 elsewhere and never touches the addr nets.

STILL OPEN, with the question sharpened: why does repair_design leave a macro
input pin driven by an unbuffered xnor2_2 when a buf_12 would meet the limit at
the measured span? A related observation not yet chased: the generated SDC
assumes the clock input port is driven by inv_2 across a 557 um net
(`set_driving_cell -lib_cell sky130_fd_sc_hd__inv_2 [get_ports {clk}]`), and clk
slew is 0.834 ns - past even the design-wide 0.75 ns limit. Flop Q transitions
are indexed by clock slew, so that degradation propagates into every addr net.


## What to do

1. Read the subagent .md file(s) above for their actual scope/decision tree.
2. Dispatch each via the Agent tool (or run manually), giving it this file's context plus the full case file.
3. Once you have a real response, run:

   python3 request_review.py apply --design sram_wrapper --agent <name> --response-file <path>
