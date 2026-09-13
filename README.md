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
pipeline/analog/                    Transistor-level cells for the custom
                                     flow (Virtuoso's counterpart), run by
                                     pipeline/custom_bridge.py against the
                                     PDK's own ngspice device models
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

`pipeline/recharacterise.py` traces where that 0.04 ns comes from, and
it is not a limit the SRAM imposes: OpenRAM's default `slew_scales`
`[0.25, 1, 8]` times sky130's `rise_time` 0.005 ns is exactly the
`index_1` axis in the macro's own liberty. The number records where
characterisation stopped. So raising `max_transition` in the .lib (as
this design's `.relaxed.lib` does) is extrapolation rather than a fix,
and is anyway short of the 0.209 ns this design actually reaches — 5.2x
the ceiling. Closing it means characterising the macro over the slew
range it will really see, which [OpenRAM](https://github.com/VLSIDA/OpenRAM)
exposes as `slew_scales` and
[sky130_sram_macros](https://github.com/VLSIDA/sky130_sram_macros)
regenerates from. The module reports the ceiling, its source, how far
past it a run sits and the grid that would cover it; it does not run
OpenRAM, since regeneration is a SPICE sweep of hours producing a new
GDS/LEF/lib set that has to be verified before anything trusts it.

### A second metrics source, and a dataset export

SRAM repair update (2026-09-12): three new full OpenLane runs reproduced
clean KLayout GDS DRC, LVS and XOR. Heuristic diode insertion eliminated
the new routing antenna violations (3 → 2 → 0), and the verified settings
are now in the design config. Actual SPICE recharacterization is in
progress through `pipeline/characterize_sram.py`; the old timing model and
the SRAM Liberty model validity check still prevents a verified pass. See
[the SRAM execution record](pipeline/designs/sram_wrapper/characterization/README.md)
for measurements, setup and remaining acceptance checks.

`score()` gates on 23 signoff checks by OpenLane's metric key names,
and now records each as a row (`signoff_checks`) that the dashboard
draws as a strip — clean, violated, or never run. Two things use that
seam, both from a survey of the Chinese open-source EDA track (see the
spec's "Two things from the Chinese open-source track"):

- `pipeline/ieda_metrics.py` maps iEDA's `feature_summary` JSON onto
  those keys, with the key names read from iEDA's writer source. The
  honest result: iEDA's JSON carries one of the 23 checks (the router's
  own violation count), so a clean iEDA run scores 22 as never checked
  and does not pass. Not yet run against a real iEDA binary.
- `pipeline/export_circuitnet.py` stages completed runs' DEF/LEF in the
  layout CircuitNet's `process_data.py` reads, one root per standard-
  cell library so DEF units never mix. Feature side only — CircuitNet's
  DRC/congestion/IR label maps come from Innovus reports this flow does
  not produce.

```sh
python3 pipeline/export_circuitnet.py --design spm --out /tmp/circuitnet
python3 pipeline/ieda_metrics.py result/feature/*.json --targets designs/gcd/run_spec.json
```

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
| `ppa_sta_report` | Reads the OpenSTA analysis a run already produced — real critical path stage by stage, plus max_slew/cap/fanout violators |
| `ppa_equiv_check` | Proves a run's netlist is functionally equivalent to its RTL (Yosys SAT, ~1s) — the check DRC/LVS/timing leave out |
| `ppa_odb_query` | Queries a run's real OpenROAD `.odb` for measured per-net placement facts (pin count, HPWL, max span) — answers per-net questions `metrics.json` cannot |
| `ppa_tech_compare` | Runs the same design across standard-cell technologies and returns a real PPA delta with the design held fixed — the technology half of DTCO |
| `ppa_custom_status` | What of the open-source Virtuoso-equivalent stack (xschem/magic/klayout/ngspice/netgen) this host can really run, each mapped to the Cadence tool it stands in for |
| `ppa_custom_eval` | Runs a script in one custom-design backend's own batch language — the counterpart of executing SKILL in Virtuoso |
| `ppa_netlist_schematic` | Netlists a schematic with xschem — the entry point of the custom flow, and what a hand-written deck silently gets wrong |
| `ppa_render_schematic` | Draws a schematic as SVG with xschem — the custom half's counterpart of `ppa_render_layout` |
| `ppa_analog_loop` | The custom half's closed loop: real sizings measured per corner, scored, and repaired from the violation's own numbers |
| `ppa_analog_scan` | Real auto-repair coverage per analog design, and whether the next step is a re-run or a human |
| `ppa_gate_schematic` | Draws a pipeline design's real synthesis netlist as a schematic — whole, or a fanin/fanout cone for designs too big to draw |
| `ppa_stdcell_schematic` | Opens a standard cell and draws its transistors, from the foundry's CDL |
| `ppa_stdcell_signoff` | Magic DRC + extraction + netgen LVS on a standard cell's real layout, recorded in `reference-db/stdcells/` |
| `ppa_spice_sim` | A real transistor-level ngspice simulation against the PDK's own device models, returning parsed `.meas` values |

Within a Claude Code session already working in this repo, a subagent
can just call the underlying Python modules directly via Bash — this
server exists for contexts that want a typed tool boundary instead (a
future session, a non-Claude-Code agent, hermes-agent). Register it
with Claude Code or hermes-agent's `api_server` pointed at
`pipeline/mcp_server.py`.

## The custom/analog half (Virtuoso's counterpart)

Everything above is the *digital* implementation flow — the open-source
answer to Innovus / Fusion Compiler / Calibre. It is not the answer to
Cadence Virtuoso, which is where custom and mixed-signal designers
actually work: schematic capture, transistor-level simulation, custom
polygon layout, LVS back to the schematic. This repo had no path to any
of it (`sim/` runs OpenSTA, which has no transistor in it anywhere).

**Virtuoso has no single open-source counterpart.** It is one program;
the open-source equivalent is five, which is why
`pipeline/custom_bridge.py` takes the backend as an argument rather than
inferring it:

| Virtuoso component | Open source | Language | On this host |
|---|---|---|---|
| Schematic Editor (Composer) | Xschem | Tcl | not installed |
| Layout Suite (polygon editing) | Magic | Tcl | **via OpenLane image** |
| Layout viewer + PVS/Calibre decks | KLayout | Python | **via OpenLane image** |
| ADE Explorer / Spectre | ngspice | `.control` block | **ngspice-46** |
| Assura / PVS LVS | netgen | Tcl | **1.5.323** |

That five-tool set is the answer because the PDKs already in `pdk/` are
built for it — `libs.tech/{xschem,magic,klayout,netgen,ngspice}` exists
under both sky130A and gf180mcuD, with 76 xschem device symbols in
`sky130_fd_pr/`. The assets were here; only the code calling them was
missing. Alternatives considered and why they lost (Electric VLSI,
GLayout/OpenFASoC, gdsfactory, BAG3, XCircuit) are in
[`docs/superpowers/specs/2026-09-12-virtuoso-counterpart-and-custom-bridge.md`](docs/superpowers/specs/2026-09-12-virtuoso-counterpart-and-custom-bridge.md).

All five run here for real: ngspice and netgen locally, Magic and KLayout
through the OpenLane image this pipeline already pulls, and xschem
through a one-package image of this repo's own
(`pipeline/docker/xschem.Dockerfile`, built on first use — Debian trixie
packages xschem 3.4.4, while Homebrew has no formula and a macOS source
build needs XQuartz plus an X11-linked Tk). `evaluate()` falls back to
whichever container carries a backend rather than reporting one
`status --docker` just called available.

**The viewer pans and zooms.** A gate-level cone is small cells with
small pin labels and xschem exports one fixed 1000x700 canvas, so a
panel-sized `<img>` is a picture of a schematic rather than a schematic.
`SchematicViewer.tsx` does what every EDA viewer and every map does:
scroll to zoom about the pointer, drag to pan, `0`/`F`/double-click to
fit, `+`/`-` to step, and a full-screen sheet that `Esc` closes. Zooming
is a CSS transform on the SVG, so a scroll wheel costs no request and no
re-parse.

**The flow starts at the schematic.** This first shipped with a
hand-written SPICE deck, which is one step below where the custom flow
actually starts: in Virtuoso nobody types a netlist, they draw a
schematic and the tool writes the netlist. The difference is measurable,
not stylistic — the same inverter, both ways:

| corner | vtrip hand / schematic (V) | tphl hand / schematic (ps) |
|---|---|---|
| ff | 0.837131 / 0.837141 | 53.3 / 54.7 |
| tt | 0.867162 / 0.867167 | 66.3 / 68.2 |
| ss | 0.894842 / 0.894843 | 85.5 / 88.3 |

The DC trip point agrees to six digits — it is the same circuit — and the
delays differ by ~3%, because the netlisted devices carry the diffusion
parasitics (`ad`/`pd`/`as`/`ps`, `nrd`/`nrs`) that the PDK's own symbols
attach to every instance and a hand-written deck has no reason to
remember. The netlisted number is the right one, and it exists only
because a tool generated it. `inv_handwritten.spice` is kept as that
baseline, under that name because xschem writes `inv.spice` from
`inv.sch`.

**And it is shown.** The dashboard's Schematic tab draws the selected
cell — xschem's own SVG export, not a second renderer, so the picture
cannot disagree with the netlist that was simulated — and runs the flow
from the same page: pick a corner, press *netlist & simulate*, and the
measurements come back beside the drawing (`GET /analog/cells`,
`GET /analog/svg`, `POST /analog/run`). The transcript records the
command and the measured round trip, the way a CIW does.

`pipeline/analog/` holds the cells. Each one earns its place by
exercising something the others do not:

| cell | what it adds |
|---|---|
| `inv/` | the first transistor-level cell: `inv.sch` → `inv.sym` (generated by xschem's own `make_symbol`) → `inv_tb.sch` |
| `nand2/` | a series pull-down stack, so the imbalance points the **other** way (ss: tphl 144.3 ps vs tplh 115.1 ps) and the inverter's repair must not fire |
| `ring/` | five stages of `inv/inv.sym` — the first cell that instantiates another cell's symbol instead of redrawing its devices, and the first measurement that is a whole-loop property (4.01 GHz at tt) |

Cross-directory instantiation needs `pipeline/analog/xschemrc`, which
sources the PDK's own and adds this tree as a library root. Without it
xschem resolves `inv/inv.sym` relative to the instantiating schematic's
directory and writes `inv IS MISSING !!!!` into the netlist, which is how
this was found. The generated deck keeps a `%PDK_LIB%` token
rather than a resolved `.lib` line: xschem's usual idiom substitutes
`$::SKYWATER_MODELS` at netlist time, which bakes in both a corner and
the container's `/pdk/...` path — the first netlisted deck died in
ngspice for exactly that reason.

```sh
python3 pipeline/custom_bridge.py draw --schematic pipeline/analog/inv/inv_tb.sch
python3 pipeline/custom_bridge.py netlist --schematic pipeline/analog/inv/inv_tb.sch
python3 pipeline/custom_bridge.py spice --netlist pipeline/analog/inv/inv_tb.spice --corner ss
python3 pipeline/custom_bridge.py status --docker
python3 pipeline/custom_bridge.py eval --backend netgen --code 'puts hi
quit'
```

`pipeline/analog/inv/inv.spice` is this repo's first transistor-level
cell. Real, measured here with no Docker and no license — sky130A models,
W_p 1.0 / W_n 0.5 / L 0.15 µm, 1.8 V, 10 fF load:

| corner | vtrip (V) | tphl (ps) | tplh (ps) |
|---|---|---|---|
| ff | 0.837 | 53.3 | 64.4 |
| tt | 0.867 | 66.3 | 80.5 |
| ss | 0.895 | 85.5 | 107.1 |

### The layout pipeline's designs, as schematics

Between synthesis and a GDS render there is a gate netlist nobody ever
looks at — and it is the one artifact a commercial console always lets
you open, because "which cells is this, and what is connected to what"
is a question a layout picture cannot answer.

`pipeline/gate_schematic.py` converts a run's real synthesis netlist with
the PDK's own importer
(`libs.tech/xschem/xschem_verilog_import/make_sky130_sch_from_verilog.awk`,
plus its 440 `sky130_fd_sc_hd` symbols), so the drawing comes from a tool
the PDK maintains rather than from a renderer here that could disagree
with it. The Schematic tab lists the results beside the custom cells.

```sh
python3 pipeline/gate_schematic.py                       # what can be drawn
python3 pipeline/gate_schematic.py --design counter4 --draw
```

Two preprocessing rules, both found by running it and getting a file
called `.sch`: Yosys writes `module counter4(clk, …)` with no space, and
the importer reads the module name from `$2`; and Yosys opens with a
`/* Generated by Yosys … */` comment, which the importer's `;\n` record
separator folds into the module line so `$1` is `/*`. Neither is a defect
in the importer — its own example netlist has neither.

It caps at 1,500 cells, measured rather than guessed (~2.6 KB of SVG per
cell):

| design | cells | SVG |
|---|---|---|
| counter4 | 19 | 48 KB |
| cdc_twoclock | 42 | 106 KB |
| spm | 224 | 600 KB |
| gcd | 280 | 720 KB |
| riscv32i | 5,423 | 14.8 MB (15 s) |
| aes | 11,616 | 39.0 MB (73 s) |

Past the cap the drawing is real and unreadable, which is why a
commercial console will not schematic-view a whole SoC in one window
either; the tab says how many elements it would draw and lets you
override.

**Cones are how the big two get looked at.** You do not open a netlist,
you open the logic around one signal — the same fanin/fanout depth an
Innovus or Virtuoso schematic view takes. `pipeline/netlist_cone.py`
extracts it and the same PDK importer draws it:

```sh
python3 pipeline/gate_schematic.py --design aes \
    --seed 'text_out[0]' --depth 5 --direction fanin --draw
```

That is 11 cells of 11,616 and 45 KB of SVG, against 39 MB for the whole
design; `riscv32i --seed 'aluout[0]' --depth 5` is 74 of 5,423. The
Schematic tab has the same control, with the design's own ports offered
as seeds — nobody types an internal Yosys name like `_01769_` from
memory. Pin direction comes from the Yosys JSON netlist beside the
Verilog one rather than from pin names, for the reason `netlist_graph.py`
already records: X/Y/Q being outputs on sky130 is a convention, not a
rule, and a wrong guess silently reverses an edge. Without that file the
cone still works, undirected, and says so. A run built with `sky130_fd_sc_hs` or a gf180mcu library has no
symbols to draw with, and that is reported rather than drawn wrong.

### Opening a standard cell

The gate-level view draws cells as boxes with pins, which is right up to
the moment the question becomes "what *is* an a21oi_2". In Virtuoso you
descend into the cell; here there was nowhere to descend to — sky130A
ships 437 xschem *symbols* for `sky130_fd_sc_hd` and not one schematic
behind them.

`pipeline/stdcell_schematic.py` generates them from the foundry's own
transistor netlist (`libs.ref/sky130_fd_sc_hd/cdl/`) through the PDK's
own SPICE importer, so the drawing cannot disagree with what the cell is:

```sh
python3 pipeline/stdcell_schematic.py --list nand2
python3 pipeline/stdcell_schematic.py --cell sky130_fd_sc_hd__inv_2 --draw
```

The Schematic tab has a search box over the library. Three format
differences stand between CDL and importer, each a real one: `.SUBCKT` vs
`.subckt`, M-devices with bare model names vs X-calls on the full
`sky130_fd_pr__` name, and — the one that mattered — parameters spelled
the way the symbols read them. Passing the CDL's lowercase `w`/`l`
leaves them unknown attributes, and the drawing then annotates every
device with the symbol's default `1 x 1 / 0.15` while the netlist says
`0.65`. Found by reading the first render: the numbers were wrong in the
only place a human would look.

**370 of the 437 cells draw.** The other 67 are the flops and latches,
which use `special_nfet_01v8` / `special_pfet_01v8_hvt` — real devices
(sky130A.tech defines them, netgen's setup lists them) with no xschem
symbol. The importer drops what it cannot resolve without saying so:
`dfxtp_2` came out with 21 of its 24 transistors. So those cells are
refused by name, and every conversion is checked against the CDL's own
device count before it is kept.

### A standard cell's layout, signed off and stored

A schematic with no layout beside it is half a cell. The foundry ships
the other half — one `.mag` per cell — and the two tools this pipeline
already runs will check it, so `pipeline/stdcell_signoff.py` does: Magic
DRC on the real layout, Magic extraction, netgen LVS against the
schematic `stdcell_schematic.py` derives from the CDL, and a case in
`reference-db/stdcells/`.

```sh
python3 pipeline/stdcell_signoff.py --cell sky130_fd_sc_hd__nand2_1
python3 pipeline/stdcell_signoff.py --scan
```

Beyond "the foundry's cells are clean" (they are — a run saying otherwise
would be a finding about this pipeline), a pass is **independent evidence
that the CDL-to-SPICE translation behind the schematic view preserves the
circuit**. Nothing else in this repo checks that conversion; LVS checks
it against the foundry's own geometry.

**The whole library, in 230 seconds** — `pipeline/stdcell_survey.py`
reads the store back:

| | cells |
|---|---|
| clean (DRC 0, LVS matched) | 414 |
| no transistors — LVS compared nothing | 12 |
| real LVS mismatches | 11 |
| real DRC errors | 2 |

The 12 are fill, decap, diode, conb, the taps and the spare-cell macro.
"Nothing matched nothing" is neither a pass nor a failure, which is the
distinction `equiv_check.py` already draws with its own `vacuous` flag;
counting the library's own filler among the failures would put it in the
same column as a real discrepancy.

The 2 DRC cells are `tapvgnd_1` and `tapvgnd2_1`, three errors each, all
of one rule: **`met1.6`, Metal1 minimum area**. That is what a cell
designed to be tiled into a row looks like when it is checked standing
alone — its met1 is a fragment of a rail that only reaches minimum area
once it abuts its neighbours. Same shape of finding as
`magic_abstract_drc.py` records for `nwell.4`, and the reason the rule
name is now stored beside the count: a count alone would have been read
as "the foundry ships a dirty cell".

The 11 mismatches have a shape, which is what running the library bought
over the first sample of 37. Every one is a compound gate (`a2111oi`,
`a211oi`, `a21boi`, `a21oi`, `a31o`, `o2111a`, `o211a`, `o211ai`, `ha`,
`probe_p`, `probec_p`) at drive 2, 4 or 8, with 1–4 devices and 0–2 nets
more in the layout than in the schematic. Reading one extraction says
what those extra nets are: `a21oi_2`'s layout implements its `m=2` nfet
series stack as **two independent stacks with their own internal nodes**
(`a_114_47#`, `a_285_47#`), while the CDL's `m=2` means two parallel
devices sharing one — and netgen cannot merge what does not share a
node. Not every multi-drive stack is laid out that way, which is why it
is 11 cells and not all of them: `nand3_2`, a three-high stack at the
same `m=2`, shares both internal nodes between its fingers and matches.
A per-cell layout style, then, not a rule about series stacks — and a
property of the library rather than of this pipeline, since the untouched
CDL reproduces it.

Both tools need `PDK_ROOT=/pdk` inside the container: the PDK's magicrc
does `tech load $PDK_ROOT/...`, and without it Magic looks for the tech
file under the absolute volare build path baked in when the PDK was
built, and dies there.

### The custom half's self-improvement loop

`pipeline/analog_loop.py` is the counterpart of `orchestrator.py` for
transistor-level design: propose a sizing, measure it for real at every
corner the spec names, score it, and derive the next candidate from what
the violation measured. `reference-db/analog/` is its case store —
separate from `reference-db/cases/` on purpose, since that schema is
shaped for digital candidates and 23 signoff checks.

```sh
python3 pipeline/analog_loop.py run --design inv    # sweep, measure, repair
python3 pipeline/analog_loop.py scan                # coverage + what needs whom
```

Two rules are carried over from `orchestrator.score()` because they are
what make a verdict trustworthy: **the worst corner governs**, and a
target with no measurement behind it is `unverified`, never `passed` —
ngspice exits 0 when a `.meas` fails, so "the number is absent" really
happens.

What makes it a loop rather than a sweep runner is that **the
measurement proposes the next candidate**. The one repair pattern
scales `W_P` by the *measured* `tplh/tphl`, because that ratio is what
the imbalance is. It was promoted only after a real run showed it
working — the same bar `propose_repairs()` holds:

| W_P | rise/fall ratio @ ss | |
|---|---|---|
| 1.0 | 1.2553 | fail |
| 1.255 (the measured step) | 1.1366 | fail — it undershoots |
| 1.5 | 0.9208 | pass, with tpd_avg improving 99.5 → 87.5 ps |

The undershoot is the evidence for re-measuring instead of solving in
one jump, and the loop does exactly that — a real run converged
`W_P` 1.0 → 1.2553 → 1.4266 and passed on the second repair. Before the
pattern existed, the same spec honestly reported `no_repairable_failures`
with 0/1 coverage; that first case is in the store too.

`nand2` is where the guard got tested rather than asserted. Its series
stack makes the fall the slow edge, so the ratio is *below* 1 — and the
inverter's pattern correctly did not fire, leaving a real
`no_repairable_failures` case in the store. A second sweep then measured
the mirror repair (`W_N` 0.5 → 0.627 = 1/0.7972 moved the ratio
0.7972 → 1.0571, with `tpd_avg` improving 129.7 → 113.3 ps; 0.8
overshoots to 1.3162), and only then was `balance-fall-rise` added. It
converges in one repair.

One bug worth naming, because it is the kind that looks like a result:
`pick_winner()` computed a min-target's margin with a max-target's sign,
so the ring oscillator's first real run picked the **slowest** of three
passing sizings. `score()` now records which way each bound points.

`scan` distinguishes the two ways a run ends without a winner, the same
way `self_improve.py` does: `max_iterations_reached` is a budget
problem and prints the re-run command, while `no_repairable_failures`
is the one that needs a person — collapsing them is how a backlog
becomes noise people learn to ignore.

### On virtuoso-bridge-lite

[`virtuoso-bridge-lite`](https://github.com/Arcadia-1/virtuoso-bridge-lite)
does the same thing from the commercial side: an agent drives a real
Virtuoso over SSH, executing SKILL and reading Maestro/Spectre results
back as a typed `VirtuosoResult`. **It cannot run here** — installed from
source and asked, it reports `No profiles found. Set VB_REMOTE_HOST in
.env first.` and `VB_CADENCE_CSHRC is not set.`, and neither `virtuoso`
nor `spectre` exists on this machine. That is a fact about the
environment, not the package; on a host with a Virtuoso session it is the
right tool and nothing here replaces it.

What *is* borrowed is its interface. Its `models.py` declares
`VirtuosoInterface` as an ABC returning `VirtuosoResult(status, output,
errors, warnings, execution_time, metadata)`, and `custom_bridge.py`
reproduces that contract field for field — including keeping exactly four
`ExecutionStatus` values and reporting a missing tool as `error` with
`metadata["reason"] = "not_installed"` rather than inventing a fifth.
An agent skill written against either bridge works against the other, so
the day a Virtuoso host becomes reachable it is an `.env` file away.
soul.md's "borrow the working part, not the whole machine".

### Not done

No GUI schematic editing (the `.sch` files here are written and read as
text and drawn read-only in the console, which is a normal way to use
xschem but not the whole tool), no
schematic→layout→LVS loop, no PEX
(today's delays are against a hand-placed 10 fF load, not extracted
parasitics), and analog results are deliberately **not** written into
`reference-db/` yet: that schema is shaped for digital candidates and
forcing analog measurements into it would pollute the labels
`surrogate.py` and `pareto.py` read.


## Dashboard

The window is shaped like the tools it sits beside — Innovus, Virtuoso,
Calibre and PrimeTime all put a menu bar on top, a navigator down the
left, a transcript across the bottom and a status bar under that, and
they spend no pixels on anything else. `src/components/EdaShell.tsx`
adds those four pieces around the existing tabs:

- **Menu bar** (File / View / Flow / Tools / Help). Every item navigates,
  flips a setting that persists, or writes a file. Nothing is there
  because a commercial tool has something in that position — a File >
  Save that saved nothing would look more like Virtuoso and mean less
  than nothing.
- **Transcript** (`src/console/log.ts`), the CIW / Innovus-console
  analogue: real backend calls with method, path, status and measured
  duration, because `installFetchLogging()` wraps `window.fetch` rather
  than asking each call site to remember to log. Filter by level, follow
  the tail, save it to a `.log`.
- **Status bar** reading `GET /gateway-status` and `GET /toolchain-status`
  — the latter runs `pipeline/custom_bridge.py`'s own probe, so the tool
  chips are the real thing: `ngspice ●` and `netgen ●` local, `magic ▣`
  and `klayout ▣` through the container, `xschem —` absent. The sidebar's
  old hardcoded "OpenLane connected" pill is gone; it asserted a
  connection nothing had checked.
- **Compact density** (View menu, remembered): 13px root, flat square
  panels, tight table rows. It is a toggle rather than a rewrite because
  the airy card styling is right for reading one report and wrong for
  watching nine designs at once. No colour changes with it — the WCAG
  work recorded in `src/index.css` survives either setting.
- **Zoom.** Browser zoom is a CSS-pixel viewport change, so fixed chrome
  takes an ever-larger share the further someone zooms in: the console
  started at a flat 216px, which is 22% of a 1000px-tall window and 45%
  of a 480px one — at that point the transcript equalled the work area.
  The chrome is proportional now (`clamp(110px, 22vh, 320px)` for the
  console, `clamp(10rem, 15vw, 15.5rem)` for the navigator) and gives
  itself up in priority order as room runs out: status-bar context
  fields first, then the tool chips, then the clock and title, then —
  below 900px — the docked frame itself, where the page goes back to
  scrolling as one column and the transcript scrolls with it rather than
  disappearing. Measured across 760x480 to 2560x1400: the work area
  holds 71-75% of the window at every size, against 45-74% before, with
  no horizontal overflow anywhere.

Behind that shell: four report-visualization tabs (Area, Timing, Power,
Trade-offs) plus a live Simulate tab and a Diagnosis page. Fully client-side for the
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

The `ppa-eda-analyst` subagent's diagnostic knowledge is also wired up as a
real [Hermes Agent](https://github.com/NousResearch/hermes-agent) profile
named `ppa-agent`, fronted by `server/hermes-gateway.mjs` — a real,
dependency-free OpenAI-compatible reverse proxy this repo ships, not a
description of one to build yourself.

1. Install Hermes Agent (e.g. via
   [Hermes Desktop](https://github.com/NousResearch/hermes-desktop)) and
   get at least the default profile talking to a real model provider —
   any provider works, the profile just needs working auth.
2. Create the `ppa-agent` profile as a clone of a working one, so it
   inherits real credentials instead of needing its own:
   `hermes profile create ppa-agent --clone`. Its persona lives at
   `~/.hermes/profiles/ppa-agent/SOUL.md` (or `%LOCALAPPDATA%\hermes\
   profiles\ppa-agent\SOUL.md` on Windows) — replace it with the PPA
   diagnostic checklist (see that subagent's own `.claude/agents/
   ppa-eda-analyst.md` for the source content this was adapted from).
3. Put a shared secret in this repo's own `.env` (gitignored, see
   `.env.example`): `PPA_EDA_GATEWAY_KEY=<anything long and random>`.
   Both `server/index.mjs` and `server/hermes-gateway.mjs` read the same
   value from the same file — nothing to keep in sync by hand.
4. Run `node server/hermes-gateway.mjs` (listens on `127.0.0.1:8700` by
   default). It shells out to the real `hermes` CLI per request — no new
   dependency, matching this repo's own style — so each diagnosis really
   does cost one real LLM call through whatever provider the profile is
   configured with.
5. On the Diagnosis page, paste the same key as `PPA_EDA_GATEWAY_KEY`
   (or set it in `server/index.mjs`'s own environment so the dashboard
   never has to handle it — see `.env.example`) — stored only in this
   browser's `localStorage` if pasted.
6. Run a simulation on the Simulate tab, then click "Diagnose this
   result." The diagnosis streams in (as one real SSE chunk — `hermes
   chat --oneshot` only returns a complete answer when the process
   exits, so this doesn't token-stream the way a raw model API call
   would), and you'll get a browser notification (and a badge on the
   Diagnosis nav tab) when it's done, even if you've switched to another
   tab.

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
