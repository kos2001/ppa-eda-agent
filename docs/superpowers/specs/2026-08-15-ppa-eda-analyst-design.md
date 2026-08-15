# PPA EDA Analyst — Design Spec

## Goal

Create a Claude Code subagent that helps analyze and optimize semiconductor
PPA (Power, Performance, Area) by reading and interpreting Synopsys EDA tool
reports — Design Compiler (`report_area`), PrimeTime (`report_timing`), and
PrimePower (`report_power`).

This is unrelated to the `ppa-agent` project (`~/gitspace/ppa-agent`,
Ansible Personal Package Archive tooling) — same three-letter acronym,
completely different domain (chip design vs. Linux packaging).

## Scope

Phase 1 (this spec): a guide-first subagent. No real Synopsys report files
are available yet, so the subagent's knowledge is built from well-documented,
standard Synopsys report formats and structured as reference docs it reads.

Phase 2 (future, out of scope here): once real report files are available,
validate/adjust the format knowledge against them and optionally add a
parsing script for automated metric extraction.

## 1. Repository

New git repo at `~/gitspace/ppa-eda-agent`. Structure:

```
ppa-eda-agent/
  .claude/agents/ppa-eda-analyst.md
  references/
    report-area.md
    report-timing.md
    report-power.md
  docs/superpowers/specs/...
```

## 2. `references/` — report format knowledge

Three markdown files, one per report type, each covering:
- What the report is generated from (which Synopsys command / tool)
- Its typical section structure
- Where the key PPA metrics live in that structure
- Common violation/warning patterns and what they mean
- A representative example snippet illustrating the format (clearly
  labeled as an illustrative example, not a captured real report, since none
  exist yet)

**report-area.md** — Design Compiler `report_area`: combinational/
noncombinational/buf-inv/macro/net area breakdown, cell count, and how
area relates to the other two legs of PPA (e.g. area vs. timing trade-offs
from buffer insertion, area vs. power from cell sizing).

**report-timing.md** — PrimeTime `report_timing`: path groups, startpoint/
endpoint, data path vs. clock path, slack, the difference between setup and
hold analysis, and how to read a full timing path report to find the
dominant delay contributor.

**report-power.md** — PrimePower `report_power`: switching/internal/leakage
power breakdown, how activity factors (from VCD/SAIF) affect the numbers,
and typical power-vs-performance trade-offs (clock gating, voltage/
frequency scaling implications visible in these reports).

## 3. Subagent: `ppa-eda-analyst`

`.claude/agents/ppa-eda-analyst.md`. Its job: given one or more Synopsys
report files (or pasted report text), identify PPA bottlenecks and
violations, explain what's driving them, and suggest concrete optimization
directions — always framed as trade-offs across the three PPA legs (e.g.
"tightening this path via upsizing will cost N% area").

It reads the three `references/*.md` files as its format knowledge rather
than having that knowledge duplicated inline in the agent frontmatter,
keeping the agent definition itself short.

Diagnostic checklist the agent follows (documented in the agent file):
1. Read report type from content/filename, load the matching reference doc.
2. Extract key metrics (WNS/TNS for timing, total cell area for area, total
   power breakdown for power).
3. Flag violations (negative slack, area/power over a stated budget if the
   user gives one).
4. Map each violation to likely root causes using the reference docs'
   "common patterns" sections.
5. Propose optimization directions, explicitly noting the PPA trade-off
   each one implies.

## Testing / validation

- No automated tests possible yet (no real report files, no parsing code).
- Manual validation: invoke the subagent with a synthetic example report
  (the illustrative snippets in `references/`) and confirm it correctly
  identifies the injected issue (e.g. a negative-slack path, an
  oversized macro) and gives a sensible optimization suggestion.
- Phase 2 will add real validation once report files exist.
