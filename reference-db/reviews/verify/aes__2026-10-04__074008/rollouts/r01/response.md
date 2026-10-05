# feedback-optimizer review: aes__2026-10-04__074008

## Verdict: actionable. This was a valid measured experiment that failed on two narrow signoff residues.

**What failed.** The case holds one candidate, `aes-closure-20261004-driver-r3-headroom12` (flow `FanoutRepair`, `FANOUT_REPAIR_ROUNDS: 3`, the `fanout901`/`_20258_` driver upsizes, 14-entry `FANOUT_REPAIR_NET_LIMITS`, sky130A / sky130_fd_sc_hd, `CLOCK_PERIOD: 12`, `MAX_TRANSITION_CONSTRAINT: 1.5`, `IO_DELAY_CONSTRAINT: 25`, `MAX_FANOUT_CONSTRAINT: 16`). The run stopped with `stop_reason: max_iterations_reached` (`max_iterations: 1`, `candidate_budget: 1`). Its verdict has `passed: false` for two reasons:
- `route__antenna_violation__count` = 2
- `design__max_fanout_violation__count` = 2

All other gates in the case are 0: Magic/KLayout DRC, LVS, setup/hold violation counts, max-slew and max-cap. The worst setup slack is 1.55 ns at `max_ss_100C_1v60`. The minimum hold slack is 0.058 ns.

`prediction.error: "ValueError: Surrogate features do not describe the physical fanout repair flow"` does **not** mean the tool failed. AGENTS.md says the surrogate does not cover FanoutRepair.

**Evidence and how it fits the earlier runs.** Max-fanout went 4 (baseline) → 6 → 0 with 14 max-slew → 1 with 14 max-slew → 5 (driver-upsize, 2 rounds) → 2 in this run, which also has 0 max-slew. The two remaining violators are themselves repair buffers:
- `fanout1023` is a `clkbuf_8` with 18 sinks.
- `fanout1007` is a `buf_4` with 17 sinks.
- Both are over the limit of 16.

The antenna pins are `_12437_/A1_N` on `_03644_` (met3) and `_14993_/A` on `text_in_r[26]` (met1). Antenna has never closed in any recorded run in the store. AGENTS.md warns that a later diode pass can push fanout back over the limit. So it is a plausible but **unverified** cause that the 17 and 18 sink counts include diodes added after fanout repair.

**Limitations.** The `run_dir` `/private/tmp/ppa-aes-closure-20261004/...` no longer exists. Because of that I could not check:
- the per-pass fanout reports
- which nets `fanout1023` and `fanout1007` drive
- whether those sinks are diode cells
- antenna ratios and the diodes that were inserted

These results use the diagnostic 12 ns / 1.5 ns / 25% IO constraints, not the original 10 ns / 0.75 ns. Even a clean result would not close the original spec.

**What to try next** (one axis per candidate; `--validate-only` first; use a persistent run root so the reports survive):
1. **Fanout headroom (only change).** Recover the names of the nets driven by `fanout1023/X` and `fanout1007/X` from the final netlist. Then have `placement-strategist` add limits for those two nets below 16 to `FANOUT_REPAIR_NET_LIMITS`. Leave the rounds at 3, which is the maximum. AGENTS.md says to prefer headroom on measured nets over tightening the global `FANOUT_REPAIR_LIMIT`, because earlier global tightening brought back 14 max-slew violations. Accept this only if the final report shows 0 fanout violations and max-slew still at 0.
2. **Antenna (separate candidate).** `GRT_ANTENNA_ITERS: 10` and `GRT_ANTENNA_MARGIN: 50` have not removed the last two antenna pins in any run. Raising them again has no supporting evidence. `placement-strategist` should check the pinned OpenLane 2 contract for diode-insertion strategy options before choosing an override. Then check the final fanout counts on `_03644_` and `text_in_r[26]`.

Do not store this case as a closure precedent.
