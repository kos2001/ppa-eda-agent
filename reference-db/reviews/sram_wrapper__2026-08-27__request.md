# Human-in-the-loop review request — sram_wrapper (2026-08-27)

Case file: reference-db/cases/sram_wrapper__2026-08-27.json
Outcome: no candidate met targets — no auto-repairable pattern matched, needs a human/subagent decision
Stages the real run outcomes hit: physical_constraint

## Relevant subagents (dispatch these, in this order)

- `feedback-optimizer` — .claude/agents/feedback-optimizer.md
- `physical-constraint-evaluator` — .claude/agents/physical-constraint-evaluator.md

## Existing diagnosis (read before dispatching — don't re-derive what's already known)

(none recorded yet)

## What to do

1. Read the subagent .md file(s) above for their actual scope/decision tree.
2. Dispatch each via the Agent tool (or run manually), giving it this file's context plus the full case file.
3. Once you have a real response, run:

   python3 request_review.py apply --design sram_wrapper --agent <name> --response-file <path>
