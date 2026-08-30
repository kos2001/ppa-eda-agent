# Human-in-the-loop review request — aes (2026-08-30)

- Case file: reference-db/cases/aes__2026-08-30.json
- Outcome: no candidate met targets after all iterations
- Stages the real run outcomes hit: (none classified)

## Relevant subagents (dispatch these, in this order)

- `feedback-optimizer` — .claude/agents/feedback-optimizer.md

## Precedent from reference-db

No prior case shares this one's failure signature or topology. This appears to be new — treat it as such rather than reaching for a familiar fix.


## Measurements that apply

No recorded measurement matches this case's failure signature. That is a real answer, not an empty one: nothing here has debugged this shape of failure before, so reach for a tool deliberately rather than by analogy.


## Existing diagnosis (read before dispatching — don't re-derive what's already known)

(none recorded yet)

## What to do

1. Read the subagent .md file(s) above for their actual scope/decision tree.
2. Dispatch each via the Agent tool (or run manually), giving it this file's context plus the full case file.
3. Once you have a real response, run:

   python3 request_review.py apply --design aes --agent <name> --response-file <path>
