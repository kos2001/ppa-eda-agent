# Challenge — try to break what the candidates share

Where the candidates agree, their agreement proves nothing. They can share the
same mistake. Find the positions they hold in common and try to break them.
Record each attempt. Leave disagreements alone, because another session is
working on them. Do not read `ledger_elim.json`.

- Start with claims that are cheap to check and are often wrong together: a cited error
  code, candidate tag, corner, net or file that does not exist in the recorded
  data; an OpenLane 1 variable name proposed for this OpenLane 2 flow; a
  stage called "passed" because a later-looking file exists; TritonRoute DRC
  treated as signoff DRC; a missing metric read as zero.
- Recompute the load-bearing figures from the recorded source (`final/metrics.json`,
  the per-corner STA report), not from a candidate's summary of them.
- Challenge the shared reading as well as the shared numbers. They may all
  blame the same stage because they read the same summary line. Read the first
  failing stage's own log.
- Treat a shared omission as consensus too. Check `spec/request.md` for hard
  gates no candidate examined: signoff DRC, LVS, every timing corner and the
  requested equivalence.
- Before you spend a check, ask whether the request needs it. Spend
  effort where a break would change the diagnosis.

Nothing is rebuilt on suspicion alone. Carry each attempt to a result or record
that you could not.

## Record

Write `ledger_fals.json` in the workspace root:

    {"challenges": [
        {"claim":   "<the position every candidate holds, as they hold it>",
         "tried":   "<what you did to break it, verbatim enough to re-run>",
         "found":   "<what it showed, quoted>",
         "holds":   true | false | "untested",
         "because": "<why it holds, or what is wrong and what the evidence supports instead>"}
     ],
     "notes": "<what you did not reach, and where that check would have gone>"}

`holds: false` is a strong claim. `found` must show the contradiction. Mark doubt
as `"untested"` and state what would have settled it. Do not pick a candidate.
