# Human-in-the-loop review request — counter4 (2026-08-30)

- Case file: reference-db/cases/counter4__2026-08-30__045617.json
- Outcome: no candidate met targets after all iterations
- Stages the real run outcomes hit: (none classified)

## Relevant subagents (dispatch these, in this order)

- `feedback-optimizer` — .claude/agents/feedback-optimizer.md

## Precedent from reference-db (retrieved, not assumed)

3 prior case(s) matched. Each is real recorded output; the match reason is stated so it can be discounted if it does not actually apply.

### counter4 — 2026-08-21  (similar topology (distance 0.0) — RESOLVED)

- outcome: passed
- stop reason: None
- winner: sweep-util-25

### counter4 — 2026-08-22  (similar topology (distance 0.0) — RESOLVED)

- outcome: passed
- stop reason: None
- winner: sweep-util-35

### counter4 — 2026-08-23  (similar topology (distance 0.0) — RESOLVED)

- outcome: passed
- stop reason: winner_found
- winner: sweep-util-35


## Measurements that apply (retrieved from what actually worked)

4 of the recorded measurements match this case's failure signature. Each names the trap that wastes the attempt, because in every instance below the trap is what a previous session actually fell into.

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

### ppa_sta_query  (matched override_changed_nothing)

- **answers**: Anything OpenSTA can report about a completed run that no tool here wraps — power by group, check types, a pin's properties, the units the numbers are in.
- **run**: `python3 -c "import sys;sys.path.insert(0,'pipeline');import sta_path;print(sta_path.query(D,R,'report_power')['output'])"`
- **trap**: Reach for it when a wrapped tool nearly answers the question but not quite. Five config sweeps were run on sram_wrapper before anyone asked STA directly, and the direct question settled it in one command. Commands that modify are refused, so this cannot repair anything — only ask.
- **on the record**: sram_wrapper: `report_checks -to u_sram/addr0[3]` showed repair_design fixing a slew violation with delay cells. That query existed in no tool until it was added as one, which is the argument for a general way to ask.

### re-sweep the floorplan rather than reusing the old one  (matched scl_gf180mcu_fd_sc_mcu7t5v0)

- **answers**: Whether a floorplan that worked for one library is big enough for another.
- **run**: `python3 pipeline/run_stage.py --design D --tag T --override DIE_AREA=0,0,W,H`
- **trap**: Two of them. A die carried over from the previous technology fails without naming the library as the reason — GPL-0301 says 'Utilization 122%' and nothing about cells being four times larger. And FP_CORE_UTIL is inert when the design sets FP_SIZING: absolute, so the obvious override changes nothing and the run fails identically; DIE_AREA is the knob that exists there.
- **on the record**: counter4_tinydie: smallest die that completes is 48um on sky130_fd_sc_hd and 56um on hs, and gf180mcu needs 256um where sky130 needed 8. cdc_twoclock at its fixed 60x60 die hit GPL-0301 at 122% utilisation on both gf180mcu libraries and completed at 128x128 (2627.7 and 3053.8 um2). All four designs now build on both foundries.


## Existing diagnosis (read before dispatching — don't re-derive what's already known)

(none recorded yet)

## What to do

1. Read the subagent .md file(s) above for their actual scope/decision tree.
2. Dispatch each via the Agent tool (or run manually), giving it this file's context plus the full case file.
3. Once you have a real response, run:

   python3 request_review.py apply --design counter4 --agent <name> --response-file <path>
