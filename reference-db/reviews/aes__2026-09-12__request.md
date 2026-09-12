# Human-in-the-loop review request — aes (2026-09-12)

- Case file: reference-db/cases/aes__2026-09-12.json
- Outcome: no candidate met targets — no auto-repairable pattern matched, needs a human/subagent decision
- Stages the real run outcomes hit: verification_ppa

## Relevant subagents (dispatch these, in this order)

- `feedback-optimizer` — .claude/agents/feedback-optimizer.md
- `verification-ppa-evaluator` — .claude/agents/verification-ppa-evaluator.md

## Precedent from reference-db (retrieved, not assumed)

3 prior case(s) matched. Each is real recorded output; the match reason is stated so it can be discounted if it does not actually apply.

### aes — 2026-08-30  (shares signoff:antenna, signoff:hold, signoff:max-cap, signoff:max-fanout, signoff:max-slew, signoff:setup — still open)

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

### aes — 2026-08-30  (shares signoff:antenna, signoff:hold, signoff:max-cap, signoff:max-fanout, signoff:max-slew, signoff:setup — still open)

- outcome: no candidate met targets after all iterations
- stop reason: max_iterations_reached
- winner: none

### aes — 2026-08-30  (shares signoff:antenna, signoff:hold, signoff:max-cap, signoff:max-fanout, signoff:max-slew, signoff:setup — still open)

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

- **signoff:antenna** (16 now): never closed in any recorded run of any design.
- **signoff:max-slew** (820 now): closed 2 time(s)
  - aes: 532 -> 0 in `iter6-slew1p5` (SYNTH_STRATEGY="DELAY 0", CLOCK_PERIOD=12, MAX_TRANSITION_CONSTRAINT=1.5); still failing after: signoff:hold 263, signoff:antenna 22, signoff:max-fanout 13  [reference-db/cases/aes__2026-09-10.json -> reference-db/cases/aes__2026-09-10__044137.json]
  - riscv32i: 147 -> 0 in `iter3-area3-clk23p6-slew1p5-fanout16` (SYNTH_STRATEGY="AREA 3", CLOCK_PERIOD=23.6, RUN_POST_GRT_DESIGN_REPAIR=true, MAX_TRANSITION_CONSTRAINT=1.5, MAX_FANOUT_CONSTRAINT=16); still failing after: signoff:antenna 19, signoff:max-cap 152, signoff:max-fanout 62  [reference-db/cases/riscv32i__2026-09-10.json -> reference-db/cases/riscv32i__2026-09-10__052046.json]
- **signoff:max-fanout** (12 now): closed 1 time(s)
  - gcd: 1 -> 0 in `gf180-7t-delay2-clk13p2-fanout12` (SYNTH_STRATEGY="DELAY 2", CLOCK_PERIOD=13.2, MAX_FANOUT_CONSTRAINT=12); still failing after: nothing else  [reference-db/cases/gcd__2026-09-10__034949.json -> reference-db/cases/gcd__2026-09-10__035620.json]


## Measurements that apply

No recorded measurement matches this case's failure signature. That is a real answer, not an empty one: nothing here has debugged this shape of failure before, so reach for a tool deliberately rather than by analogy.


## Existing diagnosis (read before dispatching — don't re-derive what's already known)

(carried from an earlier case of this design, aes__2026-09-10__044137.json — it reviewed a different run)

[2026-09-10T04:48:28Z] hold-path-trace subagent verdict (dispatched via human-in-the-loop review, not self-assessed):
Hold at the ss corners, traced instead of swept (2026-09-10, WSL runner, run iter6-slew1p5 of aes__2026-09-10__044137).

WHAT THE STORE SAYS. With MAX_TRANSITION_CONSTRAINT 1.5 the max-slew DRVs went from 570 to 0 and setup stayed at 0 violations at 12 ns. Hold remained: 263 endpoints, worst -0.276 ns, and per corner in iter5 114/99/66 at max_ss/nom_ss/min_ss with zero at every tt and ff corner. Hold margin 0.3/0.25 (iter3), hold-first (iter3), post-GRT repair and post-GRT timing (iter5), margin 0.4 with no buffer cap (iter6) all left it there or made it worse.

WHAT OPENSTA SAYS (sta_path.query, ss_100C_1v60 liberty, the run's own SPEF and SDC, report_checks -path_delay min):
  Startpoint text_in[96] (input port)  ->  input254 clkbuf_1  ->  _22043_ mux2_1  ->  _22204_/D
    input external delay 2.000, data arrival 2.374
    clock network delay (propagated) 2.355, clock uncertainty 0.250, library hold -0.067 -> required 2.538
    slack -0.163
  Startpoint text_in[42]: the same shape, slack -0.163.
  Every violating path at this corner starts at an input port; all of them are text_in[*] (18 of 18 with -slack_max 0). No register-to-register path violates hold.

WHAT THAT MEANS. The SDC (final/sdc/aes_cipher_top.sdc) declares every input as arriving 2.000 ns after the ideal clock edge (set_input_delay 2.0000, from IO_DELAY_CONSTRAINT 20 in resolved.json). At the ss corner the clock reaches the capturing flop 2.355 ns after that edge (propagated CTS insertion delay), plus 0.25 ns uncertainty. An input that really arrived 2.0 ns after the edge would be captured by the previous clock at these flops - which is what the check reports. The violation is between two constraints (the assumed input arrival and the measured clock insertion delay at ss), not inside the placed logic; the input-to-flop path is a buffer and a mux, 0.37 ns, and there is nothing in it for the resizer to slow down without inserting pure delay on 128 inputs - which is what it was asked to do with margin 0.4 and which produced a worse run. That the resizer found 63 hold endpoints against signoff's 263 is the same thing seen from the tt-ish repair view versus max-RC ss signoff.

WHAT WOULD SETTLE IT, AS A LADDER. (a) IO_DELAY_CONSTRAINT 30: input delay ~3.0 ns, above 2.355 + 0.25 + 0.067 - 0.37 = 2.30 ns, one variable. If hold goes to 0 the diagnosis is confirmed; setup on input paths has 12 - 3.0 ns for a 0.4 ns path, so nothing to trade. (b) the same plus RUN_HEURISTIC_DIODE_INSERTION for the 22 antenna violations (a step every aes run has skipped, per step_coverage) and MAX_FANOUT_CONSTRAINT 16 for the 13 fanout DRVs, the knob gcd on gf180 closed on. Written into run_spec_iter7.json.

WHAT IS NOT CLAIMED. That 2.0 ns or 3.0 ns is the right input delay for aes - that number belongs to whatever drives the chip and no run measures it. What is measured is that the design closes hold at ss for any input arrival later than ~2.3 ns after the clock edge, and that OpenLane's default TIMING_VIOLATION_CORNERS ['*tt*'] would never have looked here at all (resolved.json); this pipeline judges all nine corners, which is why aes has been OPEN.

## Already tried for this design (newest first)

Each line is a real recorded run: the configuration, then what
its own verdict counted. A configuration listed here with a bad
outcome has been tested and failed — proposing it again needs a
reason this case supplies.

- `iter8-io25` {"CLOCK_PERIOD": 12, "IO_DELAY_CONSTRAINT": 25, "MAX_TRANSITION_CONSTRAINT": 1.5, "SYNTH_STRATEGY": "DELAY 0"} -> FAIL: 8 setup, 17 max-fanout  (aes__2026-09-10__055204.json)
- `iter8-io25-fanout16` {"CLOCK_PERIOD": 12, "IO_DELAY_CONSTRAINT": 25, "MAX_FANOUT_CONSTRAINT": 16, "MAX_TRANSITION_CONSTRAINT": 1.5, "SYNTH_STRATEGY": "DELAY 0"} -> FAIL: 4 max-fanout  (aes__2026-09-10__055204.json)
- `iter7-io30` {"CLOCK_PERIOD": 12, "IO_DELAY_CONSTRAINT": 30, "MAX_TRANSITION_CONSTRAINT": 1.5, "SYNTH_STRATEGY": "DELAY 0"} -> FAIL: 8 setup, 17 max-fanout  (aes__2026-09-10__052614.json)
- `iter7-io30-diodes-fanout16` {"CLOCK_PERIOD": 12, "IO_DELAY_CONSTRAINT": 30, "MAX_FANOUT_CONSTRAINT": 16, "MAX_TRANSITION_CONSTRAINT": 1.5, "RUN_HEURISTIC_DIODE_INSERTION": true, "SYNTH_STRATEGY": "DELAY 0"} -> FAIL: 382 max-slew, 1158 max-fanout  (aes__2026-09-10__052614.json)
- `iter6-slew1p5` {"CLOCK_PERIOD": 12, "MAX_TRANSITION_CONSTRAINT": 1.5, "SYNTH_STRATEGY": "DELAY 0"} -> FAIL: 263 hold, 13 max-fanout  (aes__2026-09-10__044137.json)
- `iter6-slew1p5-hold0p4` {"CLOCK_PERIOD": 12, "GRT_RESIZER_HOLD_MAX_BUFFER_PCT": 100, "GRT_RESIZER_HOLD_SLACK_MARGIN": 0.4, "MAX_TRANSITION_CONSTRAINT": 1.5, "PL_RESIZER_HOLD_MAX_BUFFER_PCT": 100, "PL_RESIZER_HOLD_SLACK_MARGIN": 0.4, "SYNTH_STRATEGY": "DELAY 0"} -> FAIL: 1 setup, 270 hold, 75 max-slew, 12 max-fanout  (aes__2026-09-10__044137.json)
- `iter5-postgrt-repair` {"CLOCK_PERIOD": 12, "RUN_POST_GRT_DESIGN_REPAIR": true, "SYNTH_STRATEGY": "DELAY 0"} -> FAIL: 279 hold, 532 max-slew, 13 max-fanout  (aes__2026-09-10.json)
- `iter5-postgrt-repair-timing` {"CLOCK_PERIOD": 12, "RUN_POST_GRT_DESIGN_REPAIR": true, "RUN_POST_GRT_RESIZER_TIMING": true, "SYNTH_STRATEGY": "DELAY 0"} -> FAIL: 272 hold, 545 max-slew, 13 max-fanout  (aes__2026-09-10.json)
- `iter4-delay0-clk11p2` {"CLOCK_PERIOD": 11.2, "SYNTH_STRATEGY": "DELAY 0"} -> FAIL: 3 setup, 36 hold, 431 max-slew, 15 max-fanout  (aes__2026-08-30__145637.json)
- `iter4-delay0-clk12` {"CLOCK_PERIOD": 12, "SYNTH_STRATEGY": "DELAY 0"} -> FAIL: 264 hold, 570 max-slew, 12 max-fanout  (aes__2026-08-30__145637.json)
- `iter3-hold-margin` {"CLOCK_PERIOD": 10, "GRT_RESIZER_HOLD_SLACK_MARGIN": 0.25, "PL_RESIZER_HOLD_SLACK_MARGIN": 0.3, "SYNTH_STRATEGY": "DELAY 0"} -> FAIL: 218 setup, 200 hold, 544 max-slew, 14 max-fanout  (aes__2026-08-30__143202.json)
- `iter3-hold-first` {"CLOCK_PERIOD": 10, "GRT_RESIZER_FIX_HOLD_FIRST": true, "GRT_RESIZER_HOLD_SLACK_MARGIN": 0.25, "PL_RESIZER_FIX_HOLD_FIRST": true, "PL_RESIZER_HOLD_SLACK_MARGIN": 0.3, "SYNTH_STRATEGY": "DELAY 0"} -> FAIL: 207 setup, 208 hold, 554 max-slew, 14 max-fanout  (aes__2026-08-30__143202.json)

## What to do

1. Read the subagent .md file(s) above for their actual scope/decision tree.
2. Dispatch each via the Agent tool (or run manually), giving it this file's context plus the full case file.
3. Once you have a real response, run:

   python3 request_review.py apply --design aes --agent <name> --response-file <path>
