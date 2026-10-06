# Human-in-the-loop review request — aes (2026-10-07)

- Case file: reference-db/cases/aes__2026-10-07.json
- Outcome: no candidate met targets after all iterations
- Stages the real run outcomes hit: verification_ppa

## Relevant subagents (dispatch these, in this order)

- `feedback-optimizer` — .claude/agents/feedback-optimizer.md
- `verification-ppa-evaluator` — .claude/agents/verification-ppa-evaluator.md

## Precedent from reference-db (retrieved, not assumed)

3 prior case(s) matched. Each is real recorded output; the match reason is stated so it can be discounted if it does not actually apply.

### aes — 2026-08-30  (shares signoff:antenna — still open)

- outcome: no candidate met targets after all iterations
- stop reason: recovered_from_run_dirs
- winner: none
- reviewed by: hermes-review

```
[2026-08-30T13:27:49Z] hermes-review subagent verdict (dispatched via human-in-the-loop review, not self-assessed):
리뷰 판정에 앞서 케이스 파일이 실제로 존재하는지 확인해 보겠습니다.

검증 결과, 케이스 파일 `reference-db/cases/aes__2026-08-30.json`이 실제로 존재하고 1개 iteration의 완전한 측정 데이터를 담고 있어, 증거 기반 판정이 가능했습니다. 리뷰 요청서에는 "diagnosis (none recorded)"라고 되어 있어 제가 케이스 파일에서 직접 진단을 재구성했습니다.

## 판정: Actionable — 시도할 다음 후보 설정이 존재합니다

## 케이스 파일에서 확인된 사실

- 후보는 1개뿐: tag `c-hd-clock_period6`, overrides `SYNTH_STRATEGY: "AREA 0"`, `CLOCK_PERIOD: 6`, scl `sky130_fd_sc_hd`, pdk `sky130A`, top `aes_cipher_top`.
- Violations: 481 setup, 122 hold, 15 routing antenna, 1058 max-slew (DRV), 4 max-capacitance (DRV), 31 max-fanout (DRV). worst setup WNS -3.041697646515859, worst hold WNS -0.4906544198701505.
- Corner 분석이 핵심 단서입니다: 9개 corner 중 실패는 ss_100C_1v60 세 개(`max_ss_100C_1v60`, `min_ss_100C_1v60`, `nom_ss_100C_1v60`)뿐이고, ff와 tt corner은 setup/hol

[...truncated; full text in reference-db/cases/aes__2026-08-30.json]
```

### aes — 2026-08-30  (shares signoff:antenna — still open)

- outcome: no candidate met targets after all iterations
- stop reason: max_iterations_reached
- winner: none

### aes — 2026-08-30  (shares signoff:antenna — still open)

- outcome: no candidate met targets after all iterations
- stop reason: max_iterations_reached
- winner: none
- reviewed by: feedback-optimizer

```
[2026-08-30T14:37:55Z] feedback-optimizer subagent verdict (dispatched via human-in-the-loop review, not self-assessed):
# 리뷰 응답 — aes__2026-08-30__143202 (verification_ppa 단계, max_iterations_reached)

## 판정: Actionable — 시도할 다음 후보 설정이 존재합니다

케이스 파일 `reference-db/cases/aes__2026-08-30__143202.json`(54,367,314 bytes)를 실제로 확인했고, iteration 1에 2개 후보의 완전한 측정 데이터가 있어 근거 기반 판정이 가능합니다.

## 이 케이스 파일에서 확인된 사실

- stop_reason: `max_iterations_reached` — orchestrator의 기계적 auto-repair(`propose_repairs()`) 범위 밖의 타이밍 실패이므로 feedback-optimizer 결정 트리 branch 2(subagent 진단)에 해당합니다. 진정한 dead end가 아닙니다.
- 두 후보 모두 `SYNTH_STRATEGY: "DELAY 0"`, `CLOCK_PERIOD: 10` (이전 케이스 aes__2026-08-30.json의 `AREA 0` + period 6 실험과 다른 런입니다 — 이전 진단의 "타겟 period 미확인" 문제는 해소되었습니다: `constraints.design.settings`에 `CLOCK_PERIOD: 10`이 기록되어 있고, 두 후보 모두 10으로 실행됐습니다.)
- 후보 1 `iter3-hold-margin` (`PL_RESIZER_HOLD_SLACK_MARGIN: 0.3`, `GRT_RE

[...truncated; full text in reference-db/cases/aes__2026-08-30__143202.json]
```

## What closed each remaining failure, anywhere in the store

Per kind of check this case's best candidate still fails: every recorded run of any design where that kind went from failing to clean, with the overrides that run used. A kind with no closure anywhere is named as such — the store cannot advise on it, and the next step is a real run, not a search.

- **signoff:antenna** (6 now): never closed in any recorded run of any design.


## Measurements that apply (retrieved from what actually worked)

5 of the recorded measurements match this case's failure signature. Each names the trap that wastes the attempt, because in every instance below the trap is what a previous session actually fell into.

### read resolved.json and the generated SDC  (matched override_changed_nothing)

- **answers**: Whether a config change reached the tool at all, before concluding anything about what it does.
- **run**: `grep -n set_driving_cell <run>/*floorplan*/*.sdc`
- **trap**: Identical metrics do not prove a knob is inert. Confirm the artefact changed, then judge the effect.
- **on the record**: SYNTH_CLK_DRIVING_CELL was recorded as 'byte-identical results, so it does not reach the SDC'. It does reach it — the SDC goes from inv_2/Y to clkbuf_16/X. The inference was wrong because the SDC was never opened.

### run one candidate by hand before blaming the sweep  (matched override_changed_nothing)

- **answers**: Whether a batch failed because of what it swept or because of how the runs were named.
- **run**: `python3 pipeline/run_stage.py --design D --tag t --override 'KEY=VALUE'`
- **trap**: A batch where *every* run fails and none of the errors mentions the sweep is the signature of the harness, not the design. Run one candidate directly with the same override: if it passes, the override was never the problem and the difference is the tag built from it. Failures like these must also be deleted from reference-db — left there they read as 'this design does not build' and move the metrics.
- **on the record**: A 171-run batch failed completely because SYNTH_STRATEGY values look like 'DELAY 1' and safe_tag was applied only where sweeps are expanded, never in run_candidate. The rows took completion's win-rate from 0.82 to 0.56 before they were removed. Second occurrence: the first rendered a DIE_AREA list into a tag.

### ppa_render_layout, and ppa_gate_schematic for the netlist behind it  (matched macro_present)

- **answers**: The run's real rendered GDS, for a question about where things physically ended up.
- **run**: `python3 pipeline/render_layout.py --design D --tag T`
- **trap**: An image is not a measurement — use it to form the question, then answer it with ppa_odb_query's numbers rather than by eye.
- **on the record**: arxiv.org/html/2605.06936v3 measured that a layout image improves diagnosis of real post-flow violations over text alone; this pipeline stores the render in reference-db so it survives the run directory being cleaned up.

### ppa_verify_diagnosis / model_validity  (matched macro_present)

- **answers**: Whether the STA numbers are measurements or extrapolation off the end of a liberty table.
- **run**: `python3 pipeline/model_validity.py --design D --run-dir R`
- **trap**: Clean setup and hold prove nothing if the slews sit past the model's characterisation ceiling. Judge such a run by the model_validity flag, not by WNS.
- **on the record**: sram_wrapper reported WNS +9.39 ns with zero violations while its addr pins sat 22x past where the model stops.

### ppa_sta_query  (matched override_changed_nothing)

- **answers**: Anything OpenSTA can report about a completed run that no tool here wraps — power by group, check types, a pin's properties, the units the numbers are in.
- **run**: `python3 -c "import sys;sys.path.insert(0,'pipeline');import sta_path;print(sta_path.query(D,R,'report_power')['output'])"`
- **trap**: Reach for it when a wrapped tool nearly answers the question but not quite. Five config sweeps were run on sram_wrapper before anyone asked STA directly, and the direct question settled it in one command. Commands that modify are refused, so this cannot repair anything — only ask.
- **on the record**: sram_wrapper: `report_checks -to u_sram/addr0[3]` showed repair_design fixing a slew violation with delay cells. That query existed in no tool until it was added as one, which is the argument for a general way to ask.


## Existing diagnosis (read before dispatching — don't re-derive what's already known)

(carried from an earlier case of this design, aes__2026-10-04__074008.json — it reviewed a different run)

2026-10-04 measured physical algorithm candidate aes-closure-20261004-driver-r3-headroom12. Final violations: ['2 routing antenna violation(s)', '2 max-fanout (DRV) violation(s)']. All 9 reported timing corners were evaluated; Magic DRC=0, KLayout DRC=0, LVS=0. This uses the diagnostic 12ns / 1.5ns transition / generic 25% IO constraints; it does not establish original 10ns / 0.75ns or real-interface qualification. Both requested driver masters remain up-sized in the final routed netlist. Independent actual SS Liberty Boolean functions match, and all123150 original pre-buffer pin connections were checked unchanged before the physical repair. The remaining fanout violators are fanout1023/X (18 sinks) and fanout1007/X (17 sinks) against16; the two antenna pins are _12437_/A1_N on _03644_ at met3 and _14993_/A on text_in_r[26] at met1. A third pass moves the coupled repair frontier rather than establishing closure. Budget exhausted after this measured correction.

[2026-10-05T08:53:53Z] verified:feedback-optimizer subagent verdict (verified across independent reviews, see verification record):
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

## Already tried for this design (newest first)

Each line is a real recorded run: the configuration, then what
its own verdict counted. A configuration listed here with a bad
outcome has been tested and failed — proposing it again needs a
reason this case supplies.

- `aes-coupling-20261005-hu-h15-upsize20258` {"CLOCK_PERIOD": 12, "FANOUT_REPAIR_CLOCK_CELL": "sky130_fd_sc_hd__clkbuf_8", "FANOUT_REPAIR_DRIVER_CELLS": "{\"_20258_\": \"sky130_fd_sc_hd__o2bb2ai_4\"}", "FANOUT_REPAIR_LIMIT": 15, "FANOUT_REPAIR_ROUNDS": 2, "FANOUT_REPAIR_SIGNAL_CELL": "sky130_fd_sc_hd__buf_4", "GRT_ANTENNA_ITERS": 10, "GRT_ANTENNA_MARGIN": 50, "IO_DELAY_CONSTRAINT": 25, "MAX_FANOUT_CONSTRAINT": 16, "MAX_TRANSITION_CONSTRAINT": 1.5, "SYNTH_STRATEGY": "DELAY 0"} -> FAIL  (aes__2026-10-05__144906.json)
- `aes-coupling-20261005-hl-h15-net1321lim8` {"CLOCK_PERIOD": 12, "FANOUT_REPAIR_CLOCK_CELL": "sky130_fd_sc_hd__clkbuf_8", "FANOUT_REPAIR_LIMIT": 15, "FANOUT_REPAIR_NET_LIMITS": "{\"net1321\": 8}", "FANOUT_REPAIR_ROUNDS": 2, "FANOUT_REPAIR_SIGNAL_CELL": "sky130_fd_sc_hd__buf_4", "GRT_ANTENNA_ITERS": 10, "GRT_ANTENNA_MARGIN": 50, "IO_DELAY_CONSTRAINT": 25, "MAX_FANOUT_CONSTRAINT": 16, "MAX_TRANSITION_CONSTRAINT": 1.5, "SYNTH_STRATEGY": "DELAY 0"} -> FAIL: 15 max-slew, 1 max-fanout  (aes__2026-10-05__144906.json)
- `aes-coupling-20261005-hul-h15-both` {"CLOCK_PERIOD": 12, "FANOUT_REPAIR_CLOCK_CELL": "sky130_fd_sc_hd__clkbuf_8", "FANOUT_REPAIR_DRIVER_CELLS": "{\"_20258_\": \"sky130_fd_sc_hd__o2bb2ai_4\"}", "FANOUT_REPAIR_LIMIT": 15, "FANOUT_REPAIR_NET_LIMITS": "{\"net1321\": 8}", "FANOUT_REPAIR_ROUNDS": 2, "FANOUT_REPAIR_SIGNAL_CELL": "sky130_fd_sc_hd__buf_4", "GRT_ANTENNA_ITERS": 10, "GRT_ANTENNA_MARGIN": 50, "IO_DELAY_CONSTRAINT": 25, "MAX_FANOUT_CONSTRAINT": 16, "MAX_TRANSITION_CONSTRAINT": 1.5, "SYNTH_STRATEGY": "DELAY 0"} -> FAIL  (aes__2026-10-05__144906.json)
- `aes-coupling-20261005-c0-util35-lim16` {"CLOCK_PERIOD": 12, "FANOUT_REPAIR_CLOCK_CELL": "sky130_fd_sc_hd__clkbuf_8", "FANOUT_REPAIR_LIMIT": 16, "FANOUT_REPAIR_ROUNDS": 2, "FANOUT_REPAIR_SIGNAL_CELL": "sky130_fd_sc_hd__buf_4", "GRT_ANTENNA_ITERS": 10, "GRT_ANTENNA_MARGIN": 50, "IO_DELAY_CONSTRAINT": 25, "MAX_FANOUT_CONSTRAINT": 16, "MAX_TRANSITION_CONSTRAINT": 1.5, "SYNTH_STRATEGY": "DELAY 0"} -> FAIL: 4 max-fanout  (aes__2026-10-05.json)
- `aes-coupling-20261005-h15-util35-lim15` {"CLOCK_PERIOD": 12, "FANOUT_REPAIR_CLOCK_CELL": "sky130_fd_sc_hd__clkbuf_8", "FANOUT_REPAIR_LIMIT": 15, "FANOUT_REPAIR_ROUNDS": 2, "FANOUT_REPAIR_SIGNAL_CELL": "sky130_fd_sc_hd__buf_4", "GRT_ANTENNA_ITERS": 10, "GRT_ANTENNA_MARGIN": 50, "IO_DELAY_CONSTRAINT": 25, "MAX_FANOUT_CONSTRAINT": 16, "MAX_TRANSITION_CONSTRAINT": 1.5, "SYNTH_STRATEGY": "DELAY 0"} -> FAIL: 14 max-slew  (aes__2026-10-05.json)
- `aes-coupling-20261005-u45-util45-lim16` {"CLOCK_PERIOD": 12, "FANOUT_REPAIR_CLOCK_CELL": "sky130_fd_sc_hd__clkbuf_8", "FANOUT_REPAIR_LIMIT": 16, "FANOUT_REPAIR_ROUNDS": 2, "FANOUT_REPAIR_SIGNAL_CELL": "sky130_fd_sc_hd__buf_4", "FP_CORE_UTIL": 45, "GRT_ANTENNA_ITERS": 10, "GRT_ANTENNA_MARGIN": 50, "IO_DELAY_CONSTRAINT": 25, "MAX_FANOUT_CONSTRAINT": 16, "MAX_TRANSITION_CONSTRAINT": 1.5, "SYNTH_STRATEGY": "DELAY 0"} -> FAIL: 1 max-fanout  (aes__2026-10-05.json)
- `aes-coupling-20261005-u55-util55-lim16` {"CLOCK_PERIOD": 12, "FANOUT_REPAIR_CLOCK_CELL": "sky130_fd_sc_hd__clkbuf_8", "FANOUT_REPAIR_LIMIT": 16, "FANOUT_REPAIR_ROUNDS": 2, "FANOUT_REPAIR_SIGNAL_CELL": "sky130_fd_sc_hd__buf_4", "FP_CORE_UTIL": 55, "GRT_ANTENNA_ITERS": 10, "GRT_ANTENNA_MARGIN": 50, "IO_DELAY_CONSTRAINT": 25, "MAX_FANOUT_CONSTRAINT": 16, "MAX_TRANSITION_CONSTRAINT": 1.5, "SYNTH_STRATEGY": "DELAY 0"} -> FAIL: 12 max-slew, 1 max-fanout  (aes__2026-10-05.json)
- `aes-coupling-20261005-orig-10ns-075-fo10` {"CLOCK_PERIOD": 10, "FANOUT_REPAIR_CLOCK_CELL": "sky130_fd_sc_hd__clkbuf_8", "FANOUT_REPAIR_LIMIT": 10, "FANOUT_REPAIR_ROUNDS": 2, "FANOUT_REPAIR_SIGNAL_CELL": "sky130_fd_sc_hd__buf_4", "GRT_ANTENNA_ITERS": 10, "GRT_ANTENNA_MARGIN": 50, "SYNTH_STRATEGY": "DELAY 0"} -> FAIL: 237 setup, 294 hold, 657 max-slew, 2 max-fanout  (aes__2026-10-05.json)
- `aes-closure-20261004-driver-r3-headroom12` {"CLOCK_PERIOD": 12, "FANOUT_REPAIR_CLOCK_CELL": "sky130_fd_sc_hd__clkbuf_8", "FANOUT_REPAIR_DRIVER_CELLS": "{\"fanout901\":\"sky130_fd_sc_hd__buf_8\",\"_20258_\":\"sky130_fd_sc_hd__o2bb2ai_4\"}", "FANOUT_REPAIR_LIMIT": 16, "FANOUT_REPAIR_NET_LIMITS": "{\"net1321\":8,\"_09030_\":8,\"net395\":14,\"net628\":14,\"net647\":14,\"net747\":14,\"net1048\":14,\"net1303\":14,\"net901\":14,\"net535\":12,\"net641\":12,\"net734\":12,\"net752\":12,\"net966\":12}", "FANOUT_REPAIR_ROUNDS": 3, "FANOUT_REPAIR_SIGNAL_CELL": "sky130_fd_sc_hd__buf_4", "GRT_ANTENNA_ITERS": 10, "GRT_ANTENNA_MARGIN": 50, "IO_DELAY_CONSTRAINT": 25, "MAX_FANOUT_CONSTRAINT": 16, "MAX_TRANSITION_CONSTRAINT": 1.5, "SYNTH_STRATEGY": "DELAY 0"} -> FAIL: 2 max-fanout  (aes__2026-10-04__074008.json)
- `aes-closure-20261004-driver-upsize` {"CLOCK_PERIOD": 12, "FANOUT_REPAIR_CLOCK_CELL": "sky130_fd_sc_hd__clkbuf_8", "FANOUT_REPAIR_DRIVER_CELLS": "{\"fanout901\":\"sky130_fd_sc_hd__buf_8\",\"_20258_\":\"sky130_fd_sc_hd__o2bb2ai_4\"}", "FANOUT_REPAIR_LIMIT": 16, "FANOUT_REPAIR_NET_LIMITS": "{\"net1321\":8,\"_09030_\":8,\"net395\":14,\"net628\":14,\"net647\":14,\"net747\":14,\"net1048\":14,\"net1303\":14,\"net901\":14}", "FANOUT_REPAIR_ROUNDS": 2, "FANOUT_REPAIR_SIGNAL_CELL": "sky130_fd_sc_hd__buf_4", "GRT_ANTENNA_ITERS": 10, "GRT_ANTENNA_MARGIN": 50, "IO_DELAY_CONSTRAINT": 25, "MAX_FANOUT_CONSTRAINT": 16, "MAX_TRANSITION_CONSTRAINT": 1.5, "SYNTH_STRATEGY": "DELAY 0"} -> FAIL: 5 max-fanout  (aes__2026-10-04__071933.json)
- `aes-closure-20261004-targeted14-r2` {"CLOCK_PERIOD": 12, "FANOUT_REPAIR_CLOCK_CELL": "sky130_fd_sc_hd__clkbuf_8", "FANOUT_REPAIR_LIMIT": 16, "FANOUT_REPAIR_NET_LIMITS": "{\"net1321\":8,\"_09030_\":8,\"net395\":14,\"net628\":14,\"net647\":14,\"net747\":14,\"net1048\":14,\"net1303\":14}", "FANOUT_REPAIR_ROUNDS": 2, "FANOUT_REPAIR_SIGNAL_CELL": "sky130_fd_sc_hd__buf_4", "GRT_ANTENNA_ITERS": 10, "GRT_ANTENNA_MARGIN": 50, "IO_DELAY_CONSTRAINT": 25, "MAX_FANOUT_CONSTRAINT": 16, "MAX_TRANSITION_CONSTRAINT": 1.5, "SYNTH_STRATEGY": "DELAY 0"} -> FAIL: 14 max-slew, 1 max-fanout  (aes__2026-10-04__063306.json)
- `aes-closure-20261004-headroom14-r2` {"CLOCK_PERIOD": 12, "FANOUT_REPAIR_CLOCK_CELL": "sky130_fd_sc_hd__clkbuf_8", "FANOUT_REPAIR_LIMIT": 14, "FANOUT_REPAIR_NET_LIMITS": "{\"net1321\":8,\"_09030_\":8}", "FANOUT_REPAIR_ROUNDS": 2, "FANOUT_REPAIR_SIGNAL_CELL": "sky130_fd_sc_hd__buf_4", "GRT_ANTENNA_ITERS": 10, "GRT_ANTENNA_MARGIN": 50, "IO_DELAY_CONSTRAINT": 25, "MAX_FANOUT_CONSTRAINT": 16, "MAX_TRANSITION_CONSTRAINT": 1.5, "SYNTH_STRATEGY": "DELAY 0"} -> FAIL: 14 max-slew  (aes__2026-10-04__055305.json)

## What to do

1. Read the subagent .md file(s) above for their actual scope/decision tree.
2. Dispatch each via the Agent tool (or run manually), giving it this file's context plus the full case file.
3. Once you have a real response, run:

   python3 request_review.py apply --design aes --agent <name> --response-file <path>

   Or dispatch the same reviewer two or more times independently and
   verify the answers against each other before applying one:

   python3 review_verify.py init --design aes --rollout <agent>=<path> --rollout <agent>=<path>
