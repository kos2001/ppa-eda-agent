# Resolve — settle the disagreements with evidence

The candidates disagree on some claims: a root cause, which stage failed
first, what a metric was, which knob moves a violation, what to run next.
Find out with evidence which of them hold, and leave a record that a
stranger could decide from. Do not read `ledger_fals.json`.

- Work first on the disagreements that best separate the candidates.
  Continue until the remaining candidates differ only in ways no recorded
  evidence can check.
- Settle each disagreement with a check: a field in `workspace/case.json`, a
  line in a run's report or log, a Liberty table, a tool contract. Never settle
  it by vote or by how rigorous an answer reads. Without a check there is no verdict.
- When two proposed next experiments differ, recorded evidence usually cannot
  pick the better one. Record `cannot tell` and name the run that would decide it.
- Record differences that change no ranking. When one candidate cites a
  violation, a corner, a net or a precedent that the others omit, record it.
  The adjudication builds from these entries and cannot see your session.

## Record

Write `ledger_elim.json` in the workspace root:

    {"disagreements": [
        {"question": "<what they answer differently, stated neutrally>",
         "checked":  "<the file and field/line, command, or contract clause — or 'nothing'>",
         "found":    "<what that showed, with figures; what each candidate commits to, by name>",
         "verdict":  "<which candidates are right, by name — or 'none of them' — or 'cannot tell'>"}
     ],
     "notes": "<which candidates you would keep and why, in one sentence — advice only>"}

When nothing recorded can settle a question, `checked` is `"nothing"`,
`verdict` starts with `"cannot tell"` and `found` states both readings. Start
any unsettled verdict with `"cannot tell"`, followed by the run that would
settle it. Do not pick a base. A separate session decides.
