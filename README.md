# ppa-eda-agent

Semiconductor PPA (Power, Performance, Area) analysis — a Claude Code
subagent that reads Synopsys/OpenSTA EDA reports, a live OpenSTA
simulation you can actually run, and a dashboard that ties both together.

Not related to [ppa-agent](https://github.com/kos2001/ppa-agent) (Ansible
Personal Package Archive tooling) despite the shared acronym — same three
letters, completely different domain (chip design vs. Linux packaging).

See [`soul.md`](soul.md) for the commitments behind this project — real
runs over mocked ones, documented dead-ends over hidden ones — before
diving into the components below.

## What's here

```
.claude/agents/ppa-eda-analyst.md   Claude Code subagent: diagnoses PPA
                                     issues from pasted/given report text
references/                         Report format knowledge the agent and
                                     the dashboard's parsers are built on:
  report-area.md                      Design Compiler report_area
  report-timing.md                    PrimeTime report_timing
  report-power.md                     PrimePower report_power
  see-also.md                         Real open-source examples (OpenSTA,
                                       Yosys) and documented format variants
sim/                                 A real 5-cell OpenSTA design (from
                                     OpenSTA's own examples/) used to drive
                                     live simulation
server/index.mjs                    Local server that runs that design
                                     through the openroad/opensta Docker
                                     image on demand
dashboard/                          React + Vite + TypeScript UI: paste a
                                     report and see it visualized, or run a
                                     real simulation and get a live agent
                                     diagnosis
docs/superpowers/                   Design specs and implementation plans
                                     from how this was built
pipeline/                           Autonomous layout pipeline: real
                                     placement/routing candidate
                                     generation and evaluation via
                                     OpenLane 2 + sky130 (see below)
reference-db/                       Case store of past pipeline runs —
                                     topology signature, candidate
                                     configs tried, real PPA/DRC/LVS
                                     results — for reuse across designs
```

## Autonomous layout pipeline

Beyond reading reports, this repo can now drive a real
RTL → placement → routing → signoff loop and evaluate the candidates it
produces — see
`docs/superpowers/specs/2026-08-21-autonomous-layout-agent-design.md` for
the full design and
`.claude/agents/{circuit-layout-extractor,topology-analyst,
placement-strategist,physical-constraint-evaluator,
routing-candidate-evaluator,verification-ppa-evaluator,
feedback-optimizer}.md` for the subagents that reason about it.

Requires Docker and a local sky130 PDK (fetched once via `volare`, see
the spec doc). Standard-cell-only, digital designs for now — no SRAM
bitcell layout yet (see the spec's "Known limitations").

```sh
cd pipeline
python3 orchestrator.py --design designs/counter4 \
  --run-spec designs/counter4/run_spec.json \
  --max-parallel 3
```

`run_spec.json`'s `candidates` can be listed by hand, and/or generated
via a `sweeps` entry — `{"param": "FP_CORE_UTIL", "values": [25, 35,
45, 55, 65], "tag_prefix": "sweep-util"}` expands to one candidate per
value (`orchestrator.py`'s `expand_sweeps()` — a small, dependency-free
idea borrowed from the OpenROAD Project's own
[AutoTuner](https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts/tree/master/tools/AutoTuner),
without pulling in its full Ray/hyperopt search machinery, which this
pipeline's scale doesn't need yet — see the design spec's "Borrowed
from prior art" section). Every candidate runs through a real OpenLane
flow, concurrently when `--max-parallel` is greater than 1 (keep the
default of 1 on memory-limited machines), scored against the spec's
targets using OpenLane's own real `metrics.json`
(DRC/LVS/timing/power/area), and written to `reference-db/`. If nothing
passes, it auto-repairs the one real failure mode it knows how to (an
`OpenROAD.GeneratePDN` power-grid failure from utilization pushed too
high — steps `FP_CORE_UTIL` down and retries) for up to
`max_iterations` before handing off to `feedback-optimizer` for
anything it can't pattern-match on. Validated for real:
`designs/counter4/run_spec.json`'s utilization sweep (25–65%, run with
`--max-parallel 3`) found the same real PDN strap-width boundary as
before — 25/35 pass, 45+ fails.
`reference-db/cases/counter4__2026-08-21.json` has the current result.

A third design (`pipeline/designs/counter4_tinydie`) validates a *second*
auto-repairable failure pattern — a die started too small even for
floorplan margins, repaired by growing `DIE_AREA` instead of lowering
utilization — converging for real across 4 automatic iterations (8x8um →
64x64um → clean pass). Along the way it caught a real bug in how list-
valued overrides (like `DIE_AREA`) were serialized for OpenLane's CLI;
see `reference-db/cases/counter4_tinydie__2026-08-21.json`.

A second, macro-heavy design (`pipeline/designs/sram_wrapper`, wrapping a
real sky130 SRAM hard macro) hits a different real constraint — the
macro's own liberty file specifies a max_transition on its address/mask
input buses tighter than the strongest resizer-available buffer can meet
even at zero wire length — documented with full diagnosis (including a
correction of an earlier, unverified guess) in
`reference-db/cases/sram_wrapper__2026-08-21.json`. Left open rather than
forced past; see the design spec's "Second vertical slice" section.

### Human-in-the-loop review + self-improvement loop

When `propose_repairs()` can't auto-repair a failure, escalate to a real
subagent review instead of leaving it silently open:

```sh
python3 pipeline/request_review.py request --design sram_wrapper
# ...dispatch the subagent(s) it names, e.g. via the Agent tool...
python3 pipeline/request_review.py apply --design sram_wrapper \
  --agent feedback-optimizer --response-file /path/to/response.txt
```

`pipeline/self_improve.py` ties this together into a schedulable loop:
for every design, it reports real auto-repair coverage (what fraction of
failures `propose_repairs()`'s known patterns actually fixed), generates
a review request automatically for any OPEN case with no review yet, and
flags designs where a review concluded there's nothing to auto-repair
*yet* as pattern-promotion candidates — worth a human periodically
checking whether that one-off diagnosis should become a new
`propose_repairs()` pattern. Run by hand, from a crontab entry, or from a
Claude Code `/loop` — see the design spec's "Self-improvement loop"
section.

## Dashboard

Four report-visualization tabs (Area, Timing, Power, Trade-offs) plus a
live Simulate tab and a Diagnosis page. Fully client-side for the
report-paste tabs — no backend needed. Simulate needs the local
simulation server (below); Diagnosis needs a hermes-gateway client key.

```sh
cd dashboard
npm install
npm run dev
```

### Simulate tab (real OpenSTA, not mocked)

Requires Docker. Pulls `openroad/opensta:latest` (amd64 image, runs via
emulation on Apple Silicon) on first use.

```sh
node server/index.mjs   # listens on 127.0.0.1:8123
```

Then use the Simulate tab's clock-period input and "Run simulation"
button — it runs the bundled `sim/example1.v` design through OpenSTA and
shows real timing/power results. Tightening the period below ~0.13ns
produces a genuine timing violation.

### Diagnosis page (live agent, via hermes-gateway)

The `ppa-eda-analyst` subagent is also wired up as a hermes-agent profile
so the dashboard can call it as a live, streaming diagnostic assistant —
not just a static report parser.

1. A hermes-gateway instance (local OpenAI-compatible reverse proxy in
   front of hermes-agent instances) must be running with a
   `ppa-eda-analyst` upstream registered — set up as its own hermes
   profile pointed at this repo's agent definition. This is
   environment-specific; there's no public setup doc to link here.
2. On the Diagnosis page, paste your gateway client key (from
   `GATEWAY_CLIENT_KEYS` in the gateway's `.env`) — stored only in this
   browser's `localStorage`.
3. Run a simulation on the Simulate tab, then click "Diagnose this
   result." The diagnosis streams in live (SSE), and you'll get a browser
   notification (and a badge on the Diagnosis nav tab) when it's done,
   even if you've switched to another tab.

## Status

Report parsers (`dashboard/src/parsers/`) have been validated against
real, non-synthetic report text — see `references/see-also.md` for what
was pulled from OpenSTA's own test suite and what bugs that testing
caught (the parsers originally assumed a slack-number position that turned
out to be Synopsys-specific, not universal).

`report_area` has no live-simulation path: OpenSTA is a timing/power tool
only, it doesn't do synthesis, so there's no way to generate a real
`report_area`-equivalent the way Simulate does for timing/power. The Area
tab is paste-only.
