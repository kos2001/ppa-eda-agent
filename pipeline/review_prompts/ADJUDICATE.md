# Adjudicate and deliver

Your job is to decide. Two sessions examined the candidates without seeing each
other's work: `ledger_elim.json` records disagreements and which candidates
held up, and `ledger_fals.json` records shared positions and whether each
survived an attempt to break it. Only those records, `spec/`, `workspace/` and
the responses count. Read `spec/request.md` and both records first.

Both records are provisional. Check every entry you rely on, and re-read the
cited file or field when it matters. Weigh entries; do not count them. One finding on the
first failing gate outweighs three on presentation. Overturning an entry
requires a recorded fact, not an argument.

## Decide: write `finish.json`

    {"base": "<candidate name>" | "none",
     "work": [{"what": "<the item>", "to": "<what it should say, with the figure>",
               "evidence": "<the ledger entry, or the check you ran>"}],
     "open": [{"item": "<the unsettled question>",
               "readings": ["<reading and what it implies>", "<reading and what it implies>"],
               "prefer": "<the reading you would act on alone and why, or empty>"}],
     "notes": "<what decided the base>"}

- **base**: the candidate with the fewest evidence-backed charges on what the
  request asked for. Use `"none"` when every candidate rests on a position that
  did not hold.
- **work**: each change the base needs: an item another candidate got right, a
  hard gate nobody examined, or a shared position that broke. Each needs evidence.
- **open**: each question neither record settles, with both readings. When the
  question is which experiment to run next, name the run that would settle it.
  Recording an open question is part of the work.

## Deliver: write `out/diagnosis.md`

Start from the base's response. Apply each work item after you check its
evidence yourself. Skip an item whose evidence does not hold and say so in
`notes`. Add nothing the work does not authorize. Then add a section headed
`## Open questions` that names each `open` item verbatim, with both readings.
Use the evidence's error codes and candidate tags exactly. The diagnosis will be
checked against the case's recorded data.
