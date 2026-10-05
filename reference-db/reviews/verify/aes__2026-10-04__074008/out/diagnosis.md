# Diagnosis: aes__2026-10-04__074008 (`aes-closure-20261004-driver-r3-headroom12`)

**What failed.** This is a valid measured physical run that fails two signoff checks. It is not a tool failure. The `verdict` shows `passed:false` with `"2 routing antenna violation(s)"` and `"2 max-fanout (DRV) violation(s)"`. Magic DRC, KLayout DRC, LVS, setup and hold counts, max-slew and max-cap are all 0. Functional equivalence was not run: `RUN_EQY=false`, the case's `equivalence` is null, and `step_coverage.missing_signoff` lists `Yosys.EQY`. Equivalence is therefore unknown, not 0. It was not requested, so no requested gate fails because of it. However, LVS checks the layout against the ODB-edited netlist, so it does not cover the driver upsizes or the 66 inserted fanout buffers.

Across all 9 corners, the worst setup WS is 1.550 ns at `max_ss_100C_1v60` and the worst hold WS is 0.0578 ns at `min_ff_n40C_1v95`. The only `error` is `prediction.error: "ValueError: Surrogate features do not describe the physical fanout repair flow"`. That is the surrogate declining the `FanoutRepair` flow, which AGENTS.md says it doesn't cover. Ignore it: it isn't a run failure and it isn't a prediction. `max_iterations=1` and `candidate_budget=1`, so `max_iterations_reached` just means the one admitted candidate was used. `propose_repairs()` has nothing it can repair here.

**Evidence.** The run dir `/private/tmp/ppa-aes-closure-20261004/.../driver-r3-headroom12` is gone. Archived copies (with hashes in `sources.json`) are in `pipeline/designs/aes/experiments/evidence-20261004/aes-closure-20261004-driver-r3-headroom12/`:
- Every corner's `checks.rpt` shows `fanout1023/X` at 18 and `fanout1007/X` at 17, both against a limit of 16.
- Neither instance appears in any of the three `fanout_repair.json` passes. Pass 3 (`51-odb-fanoutbuffers-2`) repaired only `net431` and skipped no nets, so both nets were at or under 16 sinks at step 51.
- `fanout1023` and `fanout1007` use OpenROAD resizer naming (`fanoutN`). This flow's FanoutBuffers instances are named `fanout_repair_1`..`fanout_repair_66`, so the custom pass did not insert these two. Their masters are not recorded: `final-driver-strength.json` lists only `fanout901` = `sky130_fd_sc_hd__buf_8` and `_20258_` = `sky130_fd_sc_hd__o2bb2ai_4`.
- In `flow_sources/fanout_repair.py`, every round ends with `OpenROAD.RepairAntennas`, and the source notes: "Diodes may raise a repaired data net above its fanout limit." The most likely explanation is that the last round's diode insertion pushed these nets over the limit. The chain behind that:
  - `sky130_fd_sc_hd__diode_2` has an input `DIODE` pin and the Liberty `default_fanout_load` is 1.0. A diode therefore counts as one sink both in STA and in `fanout_buffer.py`, which counts INPUT ITerms.
  - `RepairDesignPostGRT`, `ResizerTimingPostGRT` and `HeuristicDiodeInsertion` were gated off.
  - In each round, a net that the previous pass had left at 16 or fewer reappeared at 17: `_04820_` and `net662` in pass 2, `net431` in pass 3.
  - Step-54 RepairAntennas is therefore the only executed step after step 51 that could add sinks.

  I can't confirm this for these two nets because the step-54 diode log and the final netlist were not archived.
- `58-openroad-checkantennas-1` shows `_03644_` / `_12437_/A1_N` on met3 at P/R 1.59 and `text_in_r[26]` / `_14993_/A` on met1 at 1.38.
- Compared with `headroom14-r2` (fanout 0, antenna 12, slew 14, max-cap 1), the residue has moved rather than closed. This run already gave `net535`, `net641`, `net734`, `net752` and `net966` (the previous run's violators) a limit of 12. None of them reappears, but two different nets that were at or under 16 at step 51 now fail. `FANOUT_REPAIR_ROUNDS=3` is already at its 1–3 bound.

**Limitations.** These results use diagnostic constraints:
- `CLOCK_PERIOD` 12 against the design's 10
- transition 1.5 against the PDK default of 0.75
- IO 25%
- `MAX_FANOUT_CONSTRAINT` 16 against the PDK default of 10 (`sky130_fd_sc_hd` `config.tcl:64`)

The 2 max-fanout residue and the limit of 16 are both measured against the relaxed fanout limit. In measured history that relaxation alone takes aes from 17 max-fanout (`iter8-io25`) to 4 (`iter8-io25-fanout16`). Closing these residues would not qualify the original target, and `targets` is `{}`.

The recorded `min_period_ns` at `max_ss_100C_1v60` is 10.4495 ns, above the 10 ns design period. A 10 ns rerun should therefore be expected to face a setup gap. A linear estimate puts it at about -0.45 ns, but that is not a measurement.

The nets driven by `fanout1023`/`fanout1007` are unknown because only pin names were archived. The pass reports pair drivers and nets by number (`fanout431/X` drives `net431`, `fanout662/X` drives `net662`), so these two probably drive `net1023` and `net1007`. That is unconfirmed. Instance names may also shift once the overrides change.

**Next steps** (one axis per candidate, `--validate-only` first, values left to `placement-strategist`):
1. **Fanout.** Make the last repair actually be the last change. For example, the flow could run a final `FanoutBuffers` check after the last `RepairAntennas`, or reserve diode headroom for nets near the limit. This needs a flow-code change, not another override round. It should also archive the final netlist and the diode log, so net names can be resolved instead of guessed.
2. **Antenna.** `resolved.json` shows `GRT_DESIGN_REPAIR_MAX_WIRE_LENGTH: 0`, `DESIGN_REPAIR_MAX_WIRE_LENGTH: 0` and `RUN_HEURISTIC_DIODE_INSERTION: false`. All three names appear in this run's `resolved.json` (`openlane_version` 2.3.10), so they exist in the pinned version. Test one long-wire repair setting aimed at `_03644_` on met3, keeping everything else at the r3 overrides. Antenna violations have never been closed in any stored case, so this is a real experiment, not a precedent.
3. After that, rerun at 10 ns / 0.75 ns with `MAX_FANOUT_CONSTRAINT` back at 10 before treating any closure as design evidence.

## Open questions

1. **Which fanout experiment comes next: per-net headroom below 16 on the nets driven by fanout1023/X and fanout1007/X, or a flow change that checks and repairs fanout after the final RepairAntennas?**
   - Per-net headroom: one override axis at the r3 settings. It is cheap, but this run already did the same for five nets and two new nets crossed 16, so it may only move the frontier again.
   - Flow change: a final FanoutBuffers check after the last RepairAntennas, or diode headroom for nets near the limit. This targets the mechanism, but it needs flow code and may create new slew, cap or antenna effects that no record measures.
   - Settling run: both candidates at the r3 overrides with a persistent run root, reading the final checks.rpt fanout, slew and cap plus the final antenna summary.

2. **Which antenna axis comes next: RUN_HEURISTIC_DIODE_INSERTION=true, or a non-zero GRT_DESIGN_REPAIR_MAX_WIRE_LENGTH/DESIGN_REPAIR_MAX_WIRE_LENGTH?**
   - Heuristic diode insertion: it adds more diodes, and each diode is an input sink, so it may add fanout violations through the same mechanism.
   - Long-wire repair: it is aimed at `_03644_` on met3 (P/R 1.59). It inserts buffers rather than diodes, but its effect on slew, cap and antenna is unmeasured for aes.
   - Settling run: a single-axis run at the r3 overrides, reading the final 58-checkantennas summary and the final fanout counts.

3. **Are the 18 and 17 sinks on fanout1023/X and fanout1007/X step-54 RepairAntennas diodes, and which nets and masters are involved?**
   - Diode sinks: the elimination chain holds, and a post-antenna fanout repair addresses the cause.
   - No diode sinks: something else changed the sink count after step 51, and the diode-headroom reasoning behind the flow change does not apply.
   - Settled by reading the final netlist/DEF sinks on those nets (probably `net1023` and `net1007`) and counting `sky130_fd_sc_hd__diode_2` instances. Neither file was archived, so this needs a rerun with a persistent run root.

4. **Does aes meet setup at the original 10 ns with MAX_TRANSITION_CONSTRAINT 0.75 and MAX_FANOUT_CONSTRAINT 10?**
   - Setup gap: `min_period_ns` 10.4495 at `max_ss_100C_1v60` implies about -0.45 ns at 10 ns, so closure would need timing work beyond the DRV/antenna residue.
   - Setup met: IO delay scales with the period and the resizer acts differently at 10 ns, so the linear estimate may not hold.
   - Only a 10 ns / 0.75 ns / fanout 10 run settles it. Whether `IO_DELAY_CONSTRAINT` 25 is a relaxation relative to the OpenLane 2.3.10 default was not checked.

5. **Is the ODB-edited netlist (driver upsizes plus 66 inserted fanout buffers) functionally equivalent to the synthesized design?**
   - Equivalent: the case diagnosis cites a Liberty-function and 123150-pin connectivity check for the upsizes, and the buffers are non-inverting.
   - Unknown: `RUN_EQY=false` and equivalence is null. LVS does not cover these edits, and the cited check was not re-audited.
   - A run with `RUN_EQY=true` settles it. Until then, report "equivalence not run".
