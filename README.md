# ppa-eda-agent

A DTCO (design-technology co-optimization) AI agent for semiconductor
PPA (Power, Performance, Area): it runs real RTL through real OpenLane2
placement/routing, evaluates the result against real signoff data, and
repairs what it can on its own. The dashboard is that agent's control
surface — trigger a real run and watch it work — not a static report
viewer, though it can also read pasted Synopsys/OpenSTA reports and
drive a live OpenSTA simulation on demand.

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
dashboard/                          React + Vite + TypeScript UI — the
                                     DTCO agent's control surface: trigger
                                     a real pipeline/orchestrator.py run
                                     and watch it live, browse past
                                     reference-db/ cases, paste a report
                                     and see it visualized, or run a real
                                     simulation and get a live agent
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
  cases/                              one JSON per design per day
  layouts/                            real KLayout renders of each case's
                                       actual GDS, kept here so they
                                       outlive the (gitignored) run dirs
  reviews/                            human-in-the-loop review requests
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

Or trigger the same real run from the dashboard's Layout Pipeline tab
("run agent now") — `server/index.mjs`'s `POST /pipeline/run
{"design": "counter4"}` spawns `orchestrator.py` server-side and `GET
/pipeline/run-status?design=counter4` reports its live status (running/
done/error) plus a tail of its real stdout/stderr, which the tab polls
until the run finishes and the new `reference-db/` case appears. This is
the difference between the dashboard being a viewer of past runs and
being the agent's actual control surface — see `soul.md`.

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
subagent review instead of leaving it silently open. This runs **in the
console** — the Layout Pipeline tab shows an OPEN case as three gated
steps (generate the request, get an AI review through hermes-gateway,
apply the verdict into the case). The same workflow from a terminal:

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

## Tests

```sh
python3 -m unittest discover -s tests -v
```

No install step and no test dependency — the standard library's
`unittest`, matching this repo's dependency-free pipeline. Practice
borrowed from
[`strongarm-sizing-console`](https://github.com/kos2001/strongarm-sizing-console)'s
`tests/`, including its convention that every test names the real failure
it guards against; its pytest dependency deliberately isn't borrowed (see
`soul.md`, "borrow the working part, not the whole machine").

Scope is the pipeline's *pure* decision logic — override formatting,
sweep expansion, failure-stage classification, scoring, auto-repair
proposal, winner selection, coverage accounting, diagnosis grounding.
That's where every bug this project has actually shipped has lived, and
none of it needs Docker, OpenLane, or a PDK, so the suite runs in
milliseconds. Real EDA behaviour isn't mocked: it's exercised for real on
every `orchestrator.py` run instead.

## MCP server

`pipeline/mcp_server.py` — a dependency-free JSON-RPC 2.0 stdio MCP
server (ported from
[`strongarm-sizing-console`](https://github.com/kos2001/strongarm-sizing-console)'s
own `mcp_server.py`) exposing this pipeline as agent-callable tools
instead of shelled-out commands:

| Tool | Does |
|---|---|
| `ppa_run_stage` | One real OpenLane candidate run, returns real `metrics.json` |
| `ppa_orchestrate` | The full real candidate-generation-and-auto-repair loop (`orchestrator.orchestrate()`) for a design |
| `ppa_get_case` | Reads the latest real reference-db case (read-only, no new run) |
| `ppa_self_improve_scan` | Real auto-repair coverage + review-backlog scan across designs |
| `ppa_request_review` | Generates a human-in-the-loop review request from a case's real diagnosis |
| `ppa_apply_review` | Applies a subagent's real review response back into the case |
| `ppa_render_layout` | Renders a real PNG of a completed run's actual GDS via KLayout (bundled in the OpenLane image already used — no new dependency) |
| `ppa_verify_diagnosis` | Cross-checks a case's diagnosis prose against its own recorded data (groundedness of cited error codes / candidate tags — not a correctness check) |
| `ppa_equiv_check` | Proves a run's netlist is functionally equivalent to its RTL (Yosys SAT, ~1s) — the check DRC/LVS/timing leave out |
| `ppa_odb_query` | Queries a run's real OpenROAD `.odb` for measured per-net placement facts (pin count, HPWL, max span) — answers per-net questions `metrics.json` cannot |
| `ppa_tech_compare` | Runs the same design across standard-cell technologies and returns a real PPA delta with the design held fixed — the technology half of DTCO |

Within a Claude Code session already working in this repo, a subagent
can just call the underlying Python modules directly via Bash — this
server exists for contexts that want a typed tool boundary instead (a
future session, a non-Claude-Code agent, hermes-agent). Register it
with Claude Code or hermes-agent's `api_server` pointed at
`pipeline/mcp_server.py`.

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
