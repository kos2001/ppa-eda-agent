# Review verification (VeriHarness method)

`pipeline/review_verify.py` checks two or more independent review answers for one
OPEN case against each other before one of them enters the case's
`diagnosis`. The method comes from VeriHarness
([google-research/veriharness](https://github.com/google-research/veriharness),
arXiv:2610.00972). Each part of that method maps onto this pipeline as follows:

| VeriHarness | Here |
|---|---|
| task spec | `reference-db/reviews/<case>__request.md` |
| task workspace (read-only inputs) | `workspace/case.json` (light snapshot) and `workspace/runs.json` |
| N rollouts | independent answers to the same request (`rollouts/rNN/response.md`) |
| resolver → `ledger_elim.json` | `review-resolver` subagent, `pipeline/review_prompts/RESOLVE.md` |
| challenger → `ledger_fals.json` | `review-challenger` subagent, `CHALLENGE.md` |
| adjudication → `finish.json` `{base, work, open}` | `review-adjudicator` subagent, `ADJUDICATE.md` |
| delivery → `out/deliverables/` | `out/diagnosis.md` |
| content-blind gate | `review_verify.py check` |

## Why

`request_review.py apply` records one answer. The same reviewer, asked again,
can identify a different root cause. `verify_diagnosis` catches an invented error
code. It cannot catch a plausible cause that every answer shares, and it cannot
decide which of two answers is right. A disagreement means the alternatives are
already available: resolve them with recorded evidence. Agreement means the
answers may share the same blind spot: challenge it.

## Workflow

```sh
python3 pipeline/request_review.py request --design aes
# Dispatch the same reviewer two or more times. Give each dispatch the request
# and do not let it see the other answers. Save each answer to a file.
python3 pipeline/review_verify.py init --design aes \
  --rollout feedback-optimizer=/tmp/r1.md --rollout feedback-optimizer=/tmp/r2.md
# Run review-resolver and review-challenger concurrently on the workspace.
# Run review-adjudicator after both finish.
python3 pipeline/review_verify.py check --workspace reference-db/reviews/verify/<case>
python3 pipeline/review_verify.py apply --workspace reference-db/reviews/verify/<case>
```

`apply` writes the delivered diagnosis through `request_review.record_review`,
which also writes single answers. It attaches a `verification` record to the
`human_in_the_loop` entry. The record contains the answer hashes, the base, the
number of work items, the open questions, the number of disagreements left
unsettled, the number of consensus positions broken or left untested, and the
hashes of the ledgers, `finish.json` and the diagnosis. The same grounding check
as `apply` runs on the delivered text.

## Gates (content-blind)

`check` and `apply` refuse when:

- the request, the case snapshot or an answer changed after `init`;
- a verdict has nothing checked but is not `cannot tell`;
- a broken consensus does not quote the contradiction;
- `holds` is not `true`, `false` or `"untested"`;
- the base is neither a candidate nor `none`;
- a work item has no evidence;
- an open question has fewer than two readings or is missing from the diagnosis;
- the case is no longer the design's latest, or its recorded results changed.

`init` refuses a single answer, identical answers, a case that already has a
winner, a case without a recorded request, and an existing workspace.

## Limits

- No gate judges physics. A well-formed record can still be wrong. The
  sessions decide what to check and what follows from it.
- The investigations read recorded evidence and never start OpenLane, Docker or
  SPICE. A question that only a new run can settle stays open, and the run that
  would settle it goes into the open question.
- The run directories of older cases are often gone. `runs.json` records them as
  missing, and every answer is judged without them.
- The verifier is the same model family as the reviewers. VeriHarness reports
  gains because of the structure of the answers and the evidence it gathers,
  not because of a stronger judge. This pipeline has not measured such a gain.
  Use `pipeline/agent_eval.py` before claiming one.
- Each verification costs three sessions in addition to the N reviewers.
- The grounding result attached on `apply` uses `verify_diagnosis`. Its tag
  pattern only recognises `cand-*` and `sweep-*` tags. A diagnosis that cites a
  real tag such as `aes-closure-20261004-driver-r3-headroom12` is reported with
  no cited tags, so `checked: true` there does not mean tags were checked.
