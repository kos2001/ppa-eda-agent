# Human-in-the-loop review request — cdc_twoclock (2026-08-30)

- Case file: reference-db/cases/cdc_twoclock__2026-08-30__044616.json
- Outcome: no candidate met targets after all iterations
- Stages the real run outcomes hit: (none classified)

## Relevant subagents (dispatch these, in this order)

- `feedback-optimizer` — .claude/agents/feedback-optimizer.md

## Precedent from reference-db (retrieved, not assumed)

3 prior case(s) matched. Each is real recorded output; the match reason is stated so it can be discounted if it does not actually apply.

### counter4 — 2026-08-21  (similar topology (distance 0.208) — RESOLVED)

- outcome: passed
- stop reason: None
- winner: sweep-util-25

### counter4 — 2026-08-22  (similar topology (distance 0.208) — RESOLVED)

- outcome: passed
- stop reason: None
- winner: sweep-util-35

### counter4 — 2026-08-23  (similar topology (distance 0.208) — RESOLVED)

- outcome: passed
- stop reason: winner_found
- winner: sweep-util-35


## Measurements that apply (retrieved from what actually worked)

1 of the recorded measurements match this case's failure signature. Each names the trap that wastes the attempt, because in every instance below the trap is what a previous session actually fell into.

### re-sweep the floorplan rather than reusing the old one  (matched scl_gf180mcu_fd_sc_mcu9t5v0)

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

   python3 request_review.py apply --design cdc_twoclock --agent <name> --response-file <path>
