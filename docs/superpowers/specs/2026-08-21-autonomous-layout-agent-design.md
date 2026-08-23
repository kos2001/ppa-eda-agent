# Autonomous Layout Agent Pipeline — Design Spec

## Goal

Extend `ppa-eda-agent` from a post-synthesis PPA *report reader* into an
autonomous physical-design pipeline: given RTL/netlist input, generate
placement and routing candidates, evaluate them against physical
constraints and PPA targets, and iterate — reusing layout experience from
past designs (a reference DB) instead of starting cold each time.

Driving need: every new process node / architecture requires a new SRAM
and standard-cell library, and today that layout work is largely manual.
This pipeline targets the repeatable part of that work first.

## Scope decisions (locked for this phase)

- **Extends `ppa-eda-agent`**, not a new repo — reuses its subagent/
  docker/dashboard conventions and its existing OpenSTA sim harness stays
  as the lightweight paste-and-check path; the new pipeline is additive.
- **Real execution backend**: [OpenLane 2](https://github.com/The-OpenROAD-Project/OpenLane)
  (`ghcr.io/efabless/openlane2:2.3.10`, already present locally), which
  wraps the actual OpenROAD placer/CTS/TritonRoute router, Yosys
  synthesis, and Magic/Netgen DRC/LVS — a real RTL-to-GDSII flow, not a
  simulated one. PDK: SkyWater 130nm (`sky130`, open, via `volare`).
- **Layout scope, this phase: standard-cell library / digital macro
  place-and-route.** Full-custom SRAM bitcell layout is a different
  problem (hand-crafted or bitcell-compiler-generated, not standard-cell
  P&R) — out of scope here. Noted as a likely Phase 2 integration point
  (e.g. OpenRAM for SRAM macro generation) once this pipeline is
  validated, since the feedback-loop architecture below is reusable
  across both.
- **No mocked EDA runs.** Every stage that claims to produce a metric
  invokes the real tool. If a stage can't run in a given environment
  (missing PDK, no Docker), it must say so rather than fabricate output.

## Data model

Every design carried through this pipeline is described by four data
categories (this is what `reference-db/` cases and
`circuit-layout-extractor`'s output are organized around):

1. **Circuit data** — schematic/RTL, netlist, device/instance info,
   connectivity hierarchy, power domains.
2. **Layout data** — DEF, LEF, GDS (real physical views, once a run
   produces them).
3. **Physical design rules/constraints** — the PDK (sky130 version
   actually enabled under `pdk/`) and design constraints (SDC).
4. **Verification data** — DRC/LVS results, parasitics (SPEF), timing,
   power, area — OpenLane's real signoff output.

`pipeline/orchestrator.py`'s `data_pointers()` records real paths into
all four for every candidate that completes a run (see a real example in
`reference-db/cases/counter4__2026-08-21.json`) rather than
re-deriving or duplicating that data elsewhere.

## Process mapping

| User's process step | Component |
|---|---|
| Circuit & layout extraction | `circuit-layout-extractor` subagent + `pipeline/extract.py` |
| Topology understanding | `topology-analyst` subagent, reads reference DB |
| Placement strategy / candidate generation | `placement-strategist` subagent → OpenLane config candidates |
| Physical constraint evaluation | `pipeline/run_stage.py --to floorplan,place` (fast prune) |
| Routing generation | `pipeline/run_stage.py --to route` (TritonRoute) |
| Routing candidate evaluation | parsed congestion/DRC from OpenLane's own reports |
| Verification & PPA evaluation | OpenLane's `metrics.json` + Magic DRC + Netgen LVS + OpenSTA |
| AI feedback / repair / optimization | `feedback-optimizer` subagent, writes deltas, loops |

**This table was originally aspirational** — written before any of it
existed, describing intent rather than what runs. That gap was real and
went unaddressed for a while: `topology.json` (the "Topology
understanding" step's output) existed on disk per design but nothing in
`orchestrator.py` ever read it, and the dashboard's stage indicator
showed a generic `RTL → Floorplan → Place → Route → Signoff` strip that
had nothing to do with this table at all. Fixed:
`orchestrator.py`'s `PROCESS_STAGES` constant is now the single source
of truth for these 8 stage names (id + name), `read_topology()` actually
reads `topology.json` into every written case, and
`classify_stage()`/`produced_by_feedback` tag every candidate result
with which stage its real run outcome reached — all surfaced in
`reference-db/*.json` and rendered directly in the dashboard's Pipeline
tab (`ProcessStages`/`TopologySummary` components), instead of living
only as prose in this doc.

Still honestly aspirational: stages 5 (Routing Generation Evaluation)
and 6 (Routing Candidate Generation) are not run as separate steps —
`run_stage.py` runs one full OpenLane flow per candidate and doesn't
stop to re-evaluate between global and detailed routing.
`classify_stage()` tags a routing-stage failure with whichever of the
two names its real OpenLane error text matches most specifically, which
is real classification of real failures, not a claim that this pipeline
implements two distinct routing steps. And the subagents in this table
(`circuit-layout-extractor`, `topology-analyst`, `placement-strategist`,
`feedback-optimizer`) are Claude Code subagents a human/agent session
invokes — they are not wired into `orchestrator.py`'s automated loop,
which only knows the two mechanical repair patterns in
`propose_repairs()`.

## Components

### 1. `pipeline/orchestrator.py`

Drives one full iteration:
1. Read a `run_spec.json` (design name, RTL paths, target clock period,
   PPA/area targets, max iterations).
2. Ask `placement-strategist` for N candidate OpenLane configs
   (`config.json` variants: `FP_CORE_UTIL`, `PL_TARGET_DENSITY`,
   `DIE_AREA`/aspect ratio, macro placement hints).
3. For each candidate, run OpenLane inside the docker image up through
   placement only first (cheap) via `run_stage.py`; drop candidates that
   fail legalization or blow density/congestion estimates.
4. Run the surviving candidates through routing + signoff.
5. Collect `runs/<tag>/final/metrics.json` (OpenLane's own structured PPA:
   area, WNS/TNS, power, DRC/LVS violation counts) per candidate.
6. Between iterations, `orchestrator.py` itself mechanically retries the
   one real, observed failure mode it can pattern-match on (PDN strap-
   width failure from utilization set too high — see `propose_repairs()`)
   for up to `max_iterations`. Anything it can't pattern-match on (an
   unrecognized failure, or `max_iterations` exhausted) is handed to
   `feedback-optimizer`, which diagnoses the real cause and proposes a
   genuinely new candidate set via `placement-strategist`.
7. Write the winning (or best-so-far) run's config + metrics + a short
   rationale into `reference-db/`.

Each step above is a real subprocess call (`docker run ... openlane ...`)
or a real file read — no simulated numbers.

### 2. `pipeline/run_stage.py`

Thin wrapper: takes a design dir + OpenLane `config.json` + a `--to
<stage>` stop point, runs
`docker run --rm --platform linux/amd64 -v <pdk>:/pdk -v <design>:/design
ghcr.io/efabless/openlane2:2.3.10 openlane --pdk-root /pdk --to <stage>
/design/config.json`, and returns the run's output directory. Mirrors the
existing `server/index.mjs` pattern (temp workdir, docker subprocess,
structured result) rather than inventing a new style.

### 3. `reference-db/`

Flat-file case store (JSON + copied config), one entry per completed run:

```
reference-db/
  cases/
    <design-name>__<date>__<hash>.json   # topology signature, config used,
                                          # metrics.json, outcome, notes
  index.json                             # small lookup: signature -> case files
```

Topology signature = coarse structural fingerprint (cell count, macro
count, fanout distribution, clock domain count) computed by
`circuit-layout-extractor` — enough for `topology-analyst` to find "designs
like this one" without a real graph database. Deliberately simple (YAGNI):
upgrade to a real similarity index only if the flat lookup proves
insufficient.

### 4. New subagents (`.claude/agents/`)

Each is a focused Claude Code subagent, same style as the existing
`ppa-eda-analyst.md` — a role, a checklist, an explicit scope boundary,
and pointers to the reference docs / scripts it should read or invoke
rather than duplicating that knowledge inline:

- `circuit-layout-extractor.md` — reads RTL/netlist + any existing LEF/DEF,
  produces the structural summary + topology signature.
- `topology-analyst.md` — classifies topology (datapath/control/regular/
  irregular, macro-heavy vs. std-cell-only), queries `reference-db/` for
  similar past cases, reports what worked there.
- `placement-strategist.md` — proposes N candidate OpenLane
  configs given the topology classification + reference-DB precedent,
  each with an explicit rationale.
- `physical-constraint-evaluator.md` — reads floorplan/placement-stage
  OpenLane output (density, legalization, congestion estimate reports),
  flags candidates to prune before the expensive routing stage.
- `routing-candidate-evaluator.md` — reads TritonRoute's routing reports
  (DRC violation count, wirelength, via count) once routing completes.
- `verification-ppa-evaluator.md` — reads OpenLane's `metrics.json` +
  Magic DRC + Netgen LVS results, produces the final PPA+correctness
  verdict per candidate.
- `feedback-optimizer.md` — compares candidate verdicts against
  `run_spec.json` targets, either declares a winner or proposes one or
  more constraint deltas for the next iteration, and writes the
  reference-DB case entry.

Orchestration between subagents is the `orchestrator.py` script, not a
meta-agent — keeps each subagent single-purpose and testable in
isolation (the isolation/clarity principle: each subagent's job,
inputs, and outputs are independently describable).

### 5. Dashboard

Out of scope for this phase's implementation — the existing dashboard's
Simulate/Diagnosis tabs are unaffected. Noted as future work: a "Layout
Pipeline" tab showing iteration history and candidate comparison, once
the backend loop above is validated on a real design.

## Testing / validation

- **Vertical-slice validation, this phase**: run the full loop end-to-end
  on one small, real RTL design (not the existing `sim/example1.v`, which
  is a pre-synthesized 5-cell netlist with no RTL source — a fresh small
  design, e.g. a synchronous counter, is added under `pipeline/designs/`)
  through sky130, producing a real `metrics.json` and real DRC/LVS
  results for at least one full iteration.
- Each subagent gets a manual validation pass the same way
  `ppa-eda-analyst` did: invoke it against a real (not fabricated)
  intermediate artifact from that vertical-slice run and confirm its
  output is sane.
- No unit-test framework for the Python pipeline scripts is being
  introduced beyond what's needed to check `run_stage.py`'s argument
  handling and `orchestrator.py`'s candidate-pruning logic — these are
  thin orchestration wrappers around a real external tool, and the real
  validation is "did a real OpenLane run actually produce these files."

## Second vertical slice: a macro-heavy design

`pipeline/designs/sram_wrapper` adds a real hard macro (sky130's
`sky130_sram_1kbyte_1rw1r_32x256_8` OpenRAM-generated SRAM, 256×32b) to
validate the `has_macros: true` topology path — `MACROS` config
(gds/lef/lib/instance placement), not the `EXTRA_LEFS`/`EXTRA_LIBS`
approach originally tried (that leaves the macro unplaced going into PDN
generation; `MACROS` places-and-fixes it as part of floorplanning).

This case does **not** currently pass — and that's a real, useful result,
not a gap to hide (see `reference-db/cases/sram_wrapper__2026-08-21.json`'s
`diagnosis` field for the full write-up, including a correction: an
earlier version of this diagnosis speculatively blamed the clock pins
without checking the actual liberty file — wrong, and corrected once
verified). Confirmed root cause: the macro's own liberty file specifies
an explicit `max_transition` of 0.04ns on its `addr0`/`wmask0`/`addr1`
input buses — tighter than the strongest resizer-available buffer can
drive even at zero wire length (RSZ-0090's reported achievable transition,
0.043ns, is against a load of ~0.01pF — essentially just that bus's own
pin capacitance). Not fixable by SDC overrides (tried both
`MAX_TRANSITION_CONSTRAINT` and `CLOCK_TRANSITION_CONSTRAINT` — no
effect, since `repair_design` reads this limit from the liberty pin
attribute directly) and not fixable by registering the macro's *outputs*
(tried — irrelevant, since the violating pins are macro *inputs*). A
follow-up experiment confirmed the diagnosis without fully solving it:
switching `STD_CELL_LIBRARY` to `sky130_fd_sc_hs` (faster cells) got the
flow past this exact failure (stage 31/78 → stage 43/78) but hit a
second, unrelated problem. That one turned out to have a real fix:
`OpenROAD.RepairAntennas` is the actual orchestratable step id (the
private `_DiodeInsertion` sub-step inside it isn't a standalone step —
confirmed via the CLI's own "no step(s) with ID found" error), and
`--skip OpenROAD.RepairAntennas` gets past the hs cell-inventory gap,
advancing to stage 61/78. That surfaced a third, also-real bug:
`u_sram`'s power pins were never actually hooked into the grid (the
"not connected to power/ground nets" warning present since the very
first run) — `PDN_MACRO_CONNECTIONS` is the real OpenLane variable for
explicit macro power-grid hookup, added to `config.json` permanently
(confirmed it doesn't change the `hd` baseline's earlier RSZ-0090
failure, so it's a safe, unconditionally-correct addition). With both
fixes plus `hs`, the flow reaches stage 61/78 (Magic LEF write) before
hitting a fourth blocker: the vendor SRAM macro's own `.mag` view has
self-overlapping contacts in its `row_cap_array` cells — looks like a
quirk in the macro's own reference layout, not this project's config.
`STD_CELL_LIBRARY` reverted to `hd` for the committed baseline since
`hs` still doesn't reach a clean pass; full write-up of all four
findings in the reference-db case's `diagnosis` field. This is a genuinely different
failure mode from `counter4`'s PDN/utilization one —
`orchestrator.py`'s `propose_repairs()` correctly does *not* auto-repair
it (out of its narrow, evidence-based scope) and iteration stops for a
human/`feedback-optimizer` to pick up. A `feedback-optimizer` subagent was
actually dispatched (not self-assessed) to make that call: its verdict was
that there is **no actionable `placement-strategist` next step right now**.
The "keep the driving logic physically adjacent" idea doesn't survive
scrutiny against the numbers already in hand — RSZ-0090's best-achievable
transition (0.043ns) was measured at a load (~0.01pF) already close to the
macro pin's bare capacitance (0.00689pF), meaning placement is already
near-optimal and missing spec by a small, physically-floored margin, not a
distance the placer hasn't tried. The `hs`-library path's remaining Magic
`.mag` blocker is a vendor-macro-artifact/tooling issue outside all three
subagents' scope. Left open for a future session rather than forced past
or handed to a subagent with no real lever to pull.

## Third vertical slice: teaching propose_repairs() a second failure pattern

`pipeline/designs/counter4_tinydie` (same RTL as `counter4`, deliberately
started at an 8x8um `DIE_AREA` — too small to fit even core margins) exists
purely to exercise a *second* real, auto-repairable failure signature:
OpenROAD's Floorplan Init step rejecting a `DIE_AREA` whose core area
(die minus margins) comes out zero or negative (`STA-0572 core_area ...
is not a positive float`) — a much earlier-stage failure than the PDN
strap-width one, and repaired by growing `DIE_AREA` rather than lowering
utilization.

Real, validated convergence: 8x8 → (die-too-small repair) → 16x16 →
(now big enough for floorplan, but hits the *same* PDN-0185 strap error
as `counter4`'s pattern — except this candidate has no `FP_CORE_UTIL`
override to step down, only `DIE_AREA`) → 32x32 → (still PDN-0185) → 64x64
→ **PASS** (0 DRC/LVS, 0 timing violations, area 315.302µm²), all four
iterations automatic, no human intervention. See
`reference-db/cases/counter4_tinydie__2026-08-21.json`.

This exercise also caught a real bug in the orchestrator itself:
`run_candidates()` was serializing list-valued overrides (like
`DIE_AREA`) as a literal JSON array (`"[0, 0, 8, 8]"`) for OpenLane's
`--override-config`, which OpenLane's CLI parser doesn't accept for
`List[Decimal]`-typed variables — it mis-split the value and errored on
a phantom `DIE_AREA[0]`. Fixed in `override_value()`: lists now serialize
as a bare comma-joined list (`"0,0,8,8"`), which is what the CLI
actually expects. Found by trying an override no earlier candidate set
had exercised — a reminder that this pipeline is only as validated as
the config surface it's actually been run against.

## Borrowed from prior art: sweep-based candidate generation

Researched what exists already in this space before continuing to
hand-list every candidate in `run_spec.json`. The relevant prior art is
real and actively maintained: the OpenROAD Project's own
[AutoTuner](https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts/tree/master/tools/AutoTuner)
does hyperparameter optimization over exactly this flow's parameters
(Ray + hyperopt/genetic search, cloud-scale). Deliberately **not**
adopting that dependency here — this pipeline's candidate counts are
still single digits (see every `reference-db/cases/*.json` so far) and
its repair loop already does the "improve from feedback" job AutoTuner
does at scale; pulling in Ray/hyperopt would be substantial, untested
machinery for a problem this pipeline doesn't have yet.

What was genuinely worth borrowing: the *shape* of declaring a parameter
sweep instead of hand-listing every candidate. `orchestrator.py`'s
`expand_sweeps()` (~15 lines, no new dependency) reads an optional
`run_spec.json` `"sweeps"` list — `{"param": "FP_CORE_UTIL", "values":
[...], "tag_prefix": "..."}` — and expands it into concrete candidates
alongside any explicit `"candidates"`. Validated for real on `counter4`:
a 5-value utilization sweep (25/35/45/55/65) run with `--max-parallel 3`
(also the first real exercise of that flag) found the same real PDN
strap-width boundary as before, with two passing candidates instead of
one — `reference-db/cases/counter4__2026-08-21.json` has the current
result.

## Making placement, routing, power domain, and timing actually visible

Up to this point the dashboard showed pass/fail verdicts and file
*paths* into the four data categories, but never the physical/timing/
power content itself — someone had to go read a DEF file or
`metrics.json` by hand to see an actual placement, an actual routed
wire, or an actual timing corner. Closed that gap with real data, not
mocked:

- **`pipeline/def_layout.py`**: a small, targeted DEF/LEF parser (real
  Cadence/Si2 interchange formats) extracting real cell placement
  (`COMPONENTS`) and real routed-wire geometry (`NETS`' `ROUTED`/`NEW`
  segments), with real footprints resolved from LEF `SIZE` blocks (one
  parser covers both standard cells and hard macros — same syntax).
  Filler/decap/tap cells are excluded from what's shown (real OpenLane
  output, just not part of the design's actual logic). Wired into
  `orchestrator.py`'s `run_candidate()` as a `layout` field.
- **`score()`** now also extracts every real timing corner (typically
  9: `{min,nom,max} x {ff_n40C_1v95, tt_025C_1v80, ss_100C_1v60}`, setup
  *and* hold WNS each) instead of just the single worst value, plus real
  power (`power__{internal,leakage,switching,total}`) and real power-
  domain/IR-drop numbers (`ir__drop__{avg,worst}`, `ir__voltage__worst`)
  — all of it was already in `metrics.json`, just never surfaced.
- **Dashboard**: `LayoutView.tsx` renders the real placement+routing as
  an SVG (die outline, cell rects, metal-layer-colored wire segments);
  `TimingCorners` and `PowerSummary` render the per-corner timing table
  and power/IR-drop card. All inside each candidate's existing expand
  row, next to the file-pointer list that was already there.

`LayoutView`'s rendering approach — layer-tinted rects/lines with a
small metal-stack color legend — is adapted from
`~/gitspace/ip-dev-fde/strongarm_sim`'s own `LayoutView.tsx` +
`virtuoso.ts`, a working "Virtuoso Layout XL"-style SVG viewer built for
that sibling project's hand-synthesized transistor-level GDS. Same
visual language and SVG structure, applied here to real OpenLane DEF/LEF
placement+routing instead of synthesized geometry.

Verified for real: all three `reference-db/cases/*.json` regenerated by
actually rerunning `orchestrator.py` (not hand-edited), and a headless
Playwright browser confirms the layout SVG, timing table, and power card
all render with correct real data and zero console errors —
`counter4`'s `sweep-util-35` candidate shows 27 real standard cells,
real `li1`/`met1`/`met2`/`met3` routed wires, 9 real timing corners (all
clean), and real power numbers (0.0955mW total, 1.8V supply, 0.0991mV
worst IR drop).

## Borrowed from prior art: Pareto-front candidate selection

`pick_winner()` had a real blind spot: when multiple candidates passed,
it picked "smallest area" and ignored everything else, including ties
it couldn't actually see past. Real example that surfaced this — 
`counter4`'s `sweep-util-25` and `sweep-util-35` have *identical* area
(290.278µm²) but different real power (`9.565e-5W` vs `9.550e-5W`, the
latter strictly lower) — the old rule picked whichever Python's `min()`
happened to see first, blind to a real, measurable difference.

Researched a second sibling project before building a fix:
[`kos2001/analog-layout-optimizer`](https://github.com/kos2001/analog-layout-optimizer)'s
`layout_opt/ppa.py` implements NSGA-II for its op-amp sizing problem
(power/area/GBW have no single optimum — Pareto front is the honest
answer). This pipeline doesn't need the evolutionary half (no
crossover/mutation/generations — candidates here come from
`expand_sweeps()`/`propose_repairs()`, not a genetic search over a
continuous parameter space), but the *ranking* half — constrained
non-dominated sorting plus crowding distance — is exactly the general
"multiple real objectives, no single winner rule" problem `pick_winner()`
has. Ported that half (not the generational search) into
`pipeline/pareto.py`, pure Python, same dependency-free style as the
source. `pick_winner()` now ranks passing candidates by real area/power/
timing-margin trade-offs via the Pareto front instead of area alone.

Verified for real: rerunning `counter4`'s utilization sweep with the new
ranking picks `sweep-util-35` over `sweep-util-25` — confirmed correct
by hand (identical area, strictly lower power, tied margin — a genuine
Pareto win, not an arbitrary tiebreak). See
`reference-db/cases/counter4__2026-08-22.json`.

## Using more of OpenLane: SYNTH_STRATEGY, and a real bug it found

OpenLane ships its own `Optimizing` and `SynthesisExploration` demo
flows that sweep `SYNTH_STRATEGY` (9 real values: `AREA 0`-`3`,
`DELAY 0`-`4` — ABC logic-synthesis strategies; OpenLane's own docs say
"there is no way to know which strategy is the best before trying
them") — but both stop at global placement, never reaching real
signoff. `expand_sweeps()` already generalizes to any config variable,
so this needed no new code, just a new `sweeps` entry — validated for
real on `counter4`: `AREA 0`/`AREA 2` match the default (290.278µm²),
`DELAY 1`/`DELAY 4` trade area for speed (294.032/300.288µm²) — a real,
new trade-off axis, run through the *full* flow to real signoff instead
of stopping at placement like OpenLane's own demo.

Running it surfaced a real, reproducible OpenLane bug, isolated by
elimination: the exact same `SYNTH_STRATEGY="AREA 0"` override, run
directly via `docker run ... openlane ...`, succeeds standalone but
fails with a phantom `1 Lint errors found` (Verilator resolving a stray
`sky130_fd_sc_hd__udp_pwrgood_pp$PG` reference that has nothing to do
with this design) purely because the run tag was `"sweep-synth-AREA 0"`
— a space in `--run-tag` (and thus the `runs/<tag>/` directory name)
breaks something in OpenLane's own internal subprocess invocations.
Confirmed by rerunning with only the tag changed (no space) — same
override, clean pass. Fixed in `expand_sweeps()`: tags built from a
string sweep value now have spaces replaced before ever reaching
`--run-tag`, rather than assuming every sweep value is filesystem/CLI-
safe.

Also fixed in the same pass: `override_value()` was `json.dumps()`-
wrapping *all* scalars, which is correct for numbers (`35` → `"35"`)
but wrong for strings — `json.dumps("AREA 0")` produces `'"AREA 0"'`
(literal quote characters included), which fails OpenLane's
`Literal`-type validation outright (a clean, loud error, unlike the
run-tag bug above — this one was found first, immediately, from the
CLI's own "Value ... is invalid" message).

## Self-improvement loop

The pieces above — `propose_repairs()`'s mechanical patterns,
`request_review.py`'s human-in-the-loop escalation, `reference-db/`'s
growing case history — didn't have a single entry point tying them
together into an actual loop a human (or a schedule) could just run.
`pipeline/self_improve.py` is that entry point. Each run:

1. **Auto-repair coverage** — for every design, counts what real
   fraction of non-passing candidate runs matched a known
   `propose_repairs()` pattern (PDN strap-width, die-too-small) vs.
   needed a human decision. A real, honest metric: it only goes up when
   a genuinely new pattern gets added to `orchestrator.py`, never by
   redefining what counts as "covered."
2. **Review backlog** — any OPEN case with no `human_in_the_loop` entry
   yet gets a `request_review.py request` run automatically, so nobody
   has to remember which design needs attention.
3. **Pattern promotion candidates** (flagged, not automated) — any
   design that's OPEN *and already reviewed* means a human/subagent
   looked and concluded there's nothing `propose_repairs()` can do yet.
   That's exactly the signal worth periodically re-examining: either a
   new mechanical pattern should be written from the diagnosis, or it
   genuinely doesn't generalize. Left as a flag, not auto-applied —
   deciding whether a one-off diagnosis is a real, generalizable pattern
   needs judgment a regex can't provide.

Validated for real on the three existing designs:
`counter4`/`counter4_tinydie` both report `CLOSED`, `3/3` auto-repair
coverage; `sram_wrapper` reports `OPEN, reviewed`, `0/1` coverage, and
is correctly flagged as a pattern-promotion candidate — the loop doesn't
mistake "a subagent looked at it" for "it's resolved."

Not a Claude-Code-specific loop (no dependency on `/loop` or
`ScheduleWakeup`) — `self_improve.py` is a plain, schedulable CLI script.
Run it by hand, from a real crontab entry, or from a Claude Code `/loop`
wrapping the same command; the script itself doesn't care which.

## Dashboard as the agent's control surface, not a report viewer

Until this point the dashboard's Layout Pipeline tab was strictly
read-only: it rendered whatever `reference-db/` already contained, and
every actual `orchestrator.py` invocation happened from a terminal. That
made the dashboard read as a *report viewer* for a DTCO
(design-technology co-optimization) agent rather than the agent's own
interface — a real gap, not just a framing complaint, since it meant the
one artifact most people would actually open couldn't drive the thing it
was displaying.

Closed by adding a real trigger path:

- `server/index.mjs`: `POST /pipeline/run {"design": "<name>"}` spawns a
  real `python3 orchestrator.py --design designs/<name> --run-spec
  designs/<name>/run_spec.json` subprocess (validates the design exists
  first via its `run_spec.json`), tracked in an in-memory `Map` keyed by
  design name (`status`, `startedAt`, `finishedAt`, a capped stdout/
  stderr tail, `error`). Returns `202` immediately (the run itself can
  take minutes); `409` if a run for that design is already in flight,
  with the existing state attached so the caller can resume polling
  instead of erroring out. `GET /pipeline/run-status?design=<name>`
  reports that state. Deliberately no persistence/queue — this is a
  single-operator local tool, and a run's live status being lost on
  server restart is the same trade-off any other locally-spawned process
  already has.
- `dashboard/src/components/PipelineTab.tsx`'s new `RunAgentPanel`: a
  "run agent now" button for the currently filtered design, polling
  `/pipeline/run-status` every 4s while running and showing the real
  tail log, then refreshing the reference-db view when the run
  completes so the new case appears without a manual reload.
- Branding (`i18n.tsx`, `index.html`) updated from "PPA Readout /
  Synopsys report reader" to "DTCO Agent Console / DTCO AI Agent" —
  the old copy was accurate for the report-paste tabs but didn't
  describe what the Pipeline tab (the primary tab) actually is anymore.

Validated for real: `POST /pipeline/run {"design":"counter4"}` via curl
spawned a real `docker run ... openlane` invocation (confirmed in the
run-status tail), and the dashboard's button — clicked while that same
run was still in flight — correctly received the `409`, adopted the
in-flight state instead of erroring, and rendered the live tail log via
polling (Playwright screenshot, not just a build check).

## On-demand machine translation for reference-db content

The dashboard's `i18n.tsx` only covers UI chrome (buttons, labels) — it
never touches real reference-db content (`diagnosis`, `human_in_the_loop`
review summaries), since that's real subagent-written evidence with
precise numbers (transition times, capacitances) that a translation
shouldn't be allowed to silently round or drop. Added `POST /translate
{text}` (`server/index.mjs`, reusing the same `proxyChat()` hermes-gateway
proxy `/diagnose` already used, refactored out of the old
`proxyDiagnose()`) and a `TranslateBlock` component
(`PipelineTab.tsx`) that only appears when the UI language is Korean,
shows the original text always, and renders the translation in a
separate, clearly-labeled block below it ("기계번역 — 원문(위)이 정확한
기준입니다") rather than replacing the original.

Validated for real, with a mixed result — documented honestly rather than
claimed as fully working:

- **Short/medium text: works correctly.** A one-sentence technical
  diagnosis fragment translated via `curl` in well under a second, with
  every number and identifier preserved exactly (`0.01pF`, `0.043ns`,
  `0.04ns`, `RSZ-0090`, `addr0`, `max_transition` all came through
  unchanged in the Korean output). Confirmed the same path end-to-end in
  the browser via Playwright.
- **Very long text: works, but slowly and with no partial output.**
  `sram_wrapper`'s real diagnosis (8,744 characters) first appeared to
  hang — over two minutes with only SSE keepalives — and was wrongly
  aborted as broken on the first attempt. Root cause, isolated by
  re-running with patience and inspecting the raw SSE frames: the
  `ppa-eda-analyst` gateway model does not stream token-by-token —
  every chunk in a response shares the exact same upstream `created`
  timestamp, meaning the full response is generated server-side and
  released as one burst only once generation finishes. For this
  diagnosis that meant `prompt_tokens: 21234` (the persona's own system
  prompt dominates this — a 2,000-character excerpt alone already cost
  ~19,700 prompt tokens) and `completion_tokens: 9843`, taking roughly
  3-4 minutes end to end with the client seeing nothing until the very
  last moment. Confirmed correct once it landed: the Korean output
  ended on a complete sentence, not a cutoff. Mitigated in
  `TranslateBlock` with a visible elapsed-time counter and an explicit
  "this can take a few minutes, the gateway sends everything at once"
  hint — not a technical fix (chunking the text or switching models
  could reduce wait time but wasn't attempted), just making an
  already-working feature legible instead of looking hung.

## Performance: reference-db caching and deferred chart loading

Two real, measured performance fixes made while the user was away
(explicit "find what needs it yourself" instruction) — both low-risk,
transparent (no behavior/API change), verified with real before/after
measurements rather than assumed:

1. **`GET /reference-db` per-file caching** (`server/index.mjs`). Every
   call re-read and re-`JSON.parse`d every case file from disk, even
   though `reference-db/` only grows (self_improve.py, the dashboard's
   own trigger button, and manual runs all append to it — the
   `counter4` case alone is already 200KB) and the dashboard polls this
   endpoint on every load plus after every finished triggered run.
   `readCaseFileCached()` now stats each file (cheap) and only re-reads
   + re-parses when its `mtimeMs` actually changed, keyed in an
   in-memory `Map`. Verified correct, not just fast: modified a case
   file's content directly and confirmed the very next request reflects
   the change (cache invalidates correctly on mtime change) — not just
   a "trust the timestamp" assumption.
2. **Deferred recharts chunk** (`CandidateAreaChart.tsx`, split out of
   `PipelineTab.tsx`). Real measurement via Playwright on the production
   build (`vite preview`, not dev mode): recharts' internal
   `CategoricalChart` chunk is 88KB gzipped — the single largest JS
   chunk in the app — and was statically imported at the top of
   `PipelineTab.tsx`, the default/first tab every session loads,
   forcing it into the initial render's blocking script graph. Moved
   the one chart that uses it (the case card's "candidate area
   comparison" bar chart) into its own component behind
   `lazy(() => import(...))` + `Suspense`. Measured before/after: the
   `CategoricalChart` chunk's request now starts at 85.7ms, *after*
   `domContentLoadedEventEnd` (62.5ms) — it no longer blocks initial
   paint, just loads in parallel and fills in shortly after. Confirmed
   the chart still renders correctly post-change (real browser check,
   not just a successful build).

## Graph engineering: total guards + a ledger for the orchestrate loop

User pointed at github.com/topics/graph-engineering and asked to apply
it actively. That topic turned out to be about structuring *agent
workflows* as graphs (typed nodes, total guards, bounded cycles, a
durable ledger — RonMizrahi/sdlc-graph-engineering's terms), not graph
algorithms on circuit netlists as first assumed — genuinely applicable
here, since `orchestrate()`'s loop already has real branching, bounded
retries (`propose_repairs()`), and human stops (`request_review.py`),
which is exactly the "when a graph earns its cost" case that guide
names (a purely linear process wouldn't have warranted this).

Applied two of that methodology's principles for real, without adopting
its plugin/graph-file machinery (this pipeline has no need for an
installed graph spec — the loop already exists in code):

1. **Total guards.** `orchestrate()`'s while-loop always had exactly
   three exits in *code* (winner found / max_iterations reached / no
   repairable pattern), but the case JSON only ever recorded a
   collapsed `outcome` string that couldn't distinguish reasons 2 and 3.
   Named them explicitly as `STOP_REASONS = ("winner_found",
   "max_iterations_reached", "no_repairable_failures")`, asserted
   exactly one always fires (`assert stop_reason in STOP_REASONS`), and
   `write_case()` now records it. The dashboard shows it as a "stop
   reason" note on OPEN cases.
2. **Derive, never duplicate.** `mcp_server.py`'s `_tool_orchestrate`
   had silently re-implemented the same loop rather than calling
   `orchestrator.orchestrate()` — and had already drifted: its `if
   winner or iteration >= max_iterations: break` collapsed two guards
   into one untagged branch, exactly the failure mode principle #1
   above exists to catch. Refactored `orchestrate()` out of `main()`
   into its own function in `orchestrator.py`, called from both
   `main()` and `mcp_server.py` now — one implementation, so this can't drift
   again.

Validated for real: reran `orchestrator.py` on `counter4` end to end
(real OpenLane, `--max-parallel 3`) after the refactor — printed `stop
reason: winner_found`, and the written case JSON has
`"stop_reason": "winner_found"` — confirmed via the actual output, not
assumed from the diff.

## Investigated and declined: MCP4EDA, MasterRTL, eda-sim-ai

User asked to apply these three repos to the agent service. Investigated
each for real rather than force-fitting an integration — a documented
decline is the honest outcome here, the same way `sram_wrapper` stays
open rather than being papered over.

- **`NellyW8/MCP4EDA`** (paper: *LLM-Powered MCP RTL-to-GDSII
  Automation*). Validates the same idea `pipeline/mcp_server.py` already
  implements — an MCP server exposing EDA tools to an LLM agent — but
  its GitHub `main` branch contains only the project's marketing website
  (`agent4eda.com`'s source), not the actual MCP server implementation.
  Nothing concrete to port; the paper's existence is a useful signal
  that this project's own MCP server direction (ported from
  `strongarm-sizing-console`, see above) is a reasonable one, not a
  source of new code.
- **`hkust-zhiyao/MasterRTL`** (pre-synthesis PPA estimation via a
  learned "Simple Operator Graph" representation). The repo's own
  README says it's no longer maintained, redirecting to `RTL-Timer`.
  More importantly, its core value is a *trained* ML model for
  timing/power correlation — this directly contradicts `soul.md`'s
  existing, deliberate commitment ("Not RL- or surrogate-model-driven —
  there isn't enough reference-db data to train either honestly").
  Adopting it would mean either fabricating a model with no real
  training data (violates "real, or say so") or shipping an untrained/
  mis-trained estimator presented as if it worked. Declined for the
  same reason RL was declined at this project's outset, not a new
  decision.
- **`forUAi/eda-sim-ai`** (imitation learning + GNN surrogate + RL
  fine-tuning chip placement). Same problem as MasterRTL, more acutely:
  1 star, no evidence of being run to completion by anyone, needs GPU
  infra and its own multi-phase training pipeline (expert data →
  imitation learning → surrogate → RL fine-tune) this project has
  neither the data nor the infra for. Declined.

**What would change this**: if `MCP4EDA`'s actual server code gets
published, worth a second look for concrete tool ideas. If
`reference-db/` grows to dozens of real cases (it's 4 today), revisit
whether a *non-ML* piece of `MasterRTL`'s pipeline — its Yosys-based
RTL→bit-level-graph construction step, not the trained PPA model built
on top of it — could feed `topology.json`'s already-flagged "coarse
heuristic, not a learned embedding" limitation with real graph
statistics instead of regex-based counts. Not attempted now: it would
add a Pyverilog dependency this project's "no third-party packages"
pipeline philosophy doesn't currently carry, for a payoff that needs
more reference-db scale to justify.

## Making agent ownership visible in the dashboard

User asked for clearer visualization of which agent does what. Until
this point the 8 process-stage cards named the *stage* but not its
*owner* — a viewer had to already know that, say,
"physical-constraint-evaluator" is the one that evaluates stage 4.
`PipelineTab.tsx` now has a `STAGE_AGENT` map (paraphrased from each
`.claude/agents/*.md` file's own description — not invented) that:

- Adds a small owner badge (agent name) under each ProcessStages card,
  with the full role as its tooltip.
- Powers a collapsible `AgentRolesLegend` panel right below the process
  row — all 7 pipeline subagents in pipeline order, plus
  `ppa-eda-analyst` (the separate report-paste/live-simulation
  diagnosis agent, explicitly noted as not one of the 8 stages so it
  doesn't get miscounted as a 9th).
- The same map backs a tooltip on human-in-the-loop review pills
  (`AGENT_ROLE_BY_NAME`, a reverse index of `STAGE_AGENT`) — so a
  reviewer's name there is also self-explanatory, not just a label.

One source of truth (`STAGE_AGENT`) backs all three surfaces, so they
can't drift from each other the way the badge and the legend would if
each had its own copy of the role text. Verified rendered correctly via
Playwright (both the per-stage badges and the expanded legend, in
Korean, in the actual browser) rather than assumed from the diff.

## Applying PostEDA-Bench (arxiv.org/html/2605.06936v3)

User asked to read this paper and apply what's applicable. It's a
benchmark for LLM agents on two "last-mile" chip-design tasks — DRC
violation repair and PPA convergence — evaluated across 8 LLMs and 3
agent scaffolds. Two findings mapped onto this pipeline for real:

1. **"Vision compensates for missing geometric evidence"** — the paper
   measured that adding layout images to text-only prompts
   "consistently improves DRC performance... never harmful," on real
   post-flow violations specifically (not the synthetic tier). Before
   this, `physical-constraint-evaluator` and `routing-candidate-evaluator`
   only ever had text (logs, `metrics.json`) to reason from, even though
   density/legalization/congestion/routing-DRC are inherently spatial
   judgments. Added `pipeline/render_layout.py`: renders a real PNG from
   a completed run's actual GDS via KLayout, headless — which is already
   bundled in the `ghcr.io/efabless/openlane2` Docker image this
   pipeline already runs, so this is zero new project dependencies, not
   a new tool to install. Two real integration bugs found and fixed
   while getting this working (documented in the module's own comments,
   not hidden): `LayoutView.load_layout()` takes a filename, not a
   pre-loaded `pya.Layout` (raises a real `TypeError` otherwise), and
   without `QT_QPA_PLATFORM=offscreen` KLayout segfaults with no Python
   traceback (a raw memory-map dump) because `save_image` still goes
   through Qt's rendering pipeline even in `-z` batch mode. Exposed as
   `ppa_render_layout` in `mcp_server.py`, and both subagent files now
   have an explicit "render and view this run's actual layout image via
   the Read tool" step, citing the paper's finding directly, before
   their existing text-based checks.
2. **"Agents greedily optimize one PPA dimension rather than balancing
   competing targets"** (the paper's headline PPA-Multi failure mode) —
   checked `pareto.py`/`pick_winner()` against this and found the
   existing constrained-Pareto-front ranking (feasibility first, then
   true multi-objective dominance — all objectives ≤, at least one <,
   never a single weighted score) already avoids exactly this failure
   mode. No change needed; noted here as a validated alignment, not a
   gap, since it would be dishonest to claim credit for a fix that
   wasn't necessary.

Validated for real: rendered a PNG from a real `counter4` run's actual
GDS (confirmed visually — real power rings, standard-cell rows, pin
labels, not a placeholder), then re-verified through the actual
`ppa_render_layout` MCP tool call end to end (not just the standalone
script).

## Closing the loop: self_improve.py acts on stop_reason

`stop_reason` was added to the case JSON (see "Graph engineering" above)
but its most important consumer never read it: `self_improve.py` still
inferred everything from `winner_tag` alone, so it treated all OPEN
cases identically and filed a human-review request for every one. That
made the two STOP_REASONS a distinction the data recorded but nothing
acted on — a half-applied change.

The two OPEN reasons need opposite responses:

- `no_repairable_failures` — `propose_repairs()` had no pattern for the
  failure. A human/subagent is genuinely the only way forward. Review
  request is correct.
- `max_iterations_reached` — `propose_repairs()` was *still producing
  new candidates* each iteration and just ran out of budget. A human
  has nothing to add that another iteration wouldn't. Filing a review
  here is a false alarm, and a backlog full of false alarms is one
  people stop reading.

`scan_design()` now branches on this: budget-exhausted cases get a
distinct status, are excluded from both the review backlog and the
pattern-promotion list (the existing patterns were still firing, so
they say nothing about whether a *new* pattern is needed), and instead
report a concrete re-run command with a doubled budget — grounded in
the design's real `run_spec.json` value, not an invented number. Cases
written before `stop_reason` existed (`None`) keep the old behaviour,
since we genuinely can't tell which kind of OPEN they were.

This is the "iteration budgeting and termination logic" point from
arxiv.org/html/2605.06936v3 applied concretely — hard tasks benefit
from additional budget where easy ones saturate early, so "hit the cap"
and "genuinely stuck" must not collapse into one status.

Validated with a real run, not a hand-edited fixture: `counter4_tinydie`
genuinely needs 4 iterations to close, so re-running it with
`--max-iterations 2` produced a real `max_iterations_reached` case from
a real OpenLane flow. The scan then correctly reported "OPEN, iteration
budget exhausted (not a review case)", filed **no** review request
(verified `reference-db/reviews/` stayed empty of it), excluded it from
pattern promotion, and emitted a `--max-iterations 8` retry command —
while `auto_repair_coverage: 2/2` confirmed auto-repair had been
working the whole time, which is exactly why escalating would have been
wrong. The deliberately under-budgeted case was then deleted rather
than committed: it's a real run, but keeping it as that design's
*latest* case would misrepresent a design that closes fine at its
normal budget.

## Layout images in the durable record (and the dashboard)

The first pass at PostEDA-Bench's layout-image finding (above) applied
it in the *least durable* way available: `render_layout.py` renders from
a live `runs/<tag>/` directory, but `runs/` is gitignored and routinely
deleted. Checking the committed cases confirmed the consequence — every
one of them records a `data.layout.gds` path that is already dangling.
So the tool only worked inside the brief window a run directory still
existed, and the human looking at the dashboard got nothing at all.

`soul.md` calls reference-db the project's memory. A rendered layout is
exactly the kind of real evidence that belongs in it:

- `orchestrator.py`'s `write_case()` now calls `capture_layout_image()`,
  which renders the case's most informative candidate to
  `reference-db/layouts/<design>__<date>__<tag>.png` and records the
  relative path on the case. `pick_layout_subject()` picks the winner,
  or — when there's no winner — the candidate that got furthest through
  `PROCESS_STAGES`, which is the failure case where a picture helps
  most.
- One image per case, not per candidate: each render is a real
  Docker/KLayout invocation, and same-design passing candidates look
  near-identical, so per-candidate rendering would multiply run time for
  little added signal. Subagents needing a specific failed candidate's
  view still have `render_layout.py` against the live run directory.
- It never raises: a missing image leaves the case without one rather
  than failing a run whose real EDA work already succeeded.
- `server/index.mjs` serves `GET /reference-db/layouts/<name>.png`,
  filename-validated against a strict pattern so the endpoint can't be
  used to read arbitrary files.
- `PipelineTab.tsx`'s `CaseLayoutImage` shows it on the case card. The
  paper's measured advantage came from giving a *diagnoser* the layout;
  there's no reason that should stop at the agent boundary, so the
  human gets the same evidence.

Validated end to end with a real run: `counter4` orchestrated for real,
`layout_image` populated automatically for the winner
(`sweep-util-35`), then **the run directories were deleted** — the exact
condition that made the previous approach useless — and the image still
served (`http=200`, `image/png`, 103760 bytes) and rendered in the
browser at its real 900×900 (Playwright-verified, screenshot inspected:
real power rings, cell rows, pin labels). Path traversal against the new
endpoint was also confirmed rejected (`http=400`).

## A regression suite, and diagnosis grounding (from strongarm-sizing-console)

Two further things borrowed from
github.com/kos2001/strongarm-sizing-console, after its MCP server pattern
(above).

**1. A real test suite — the practice, not the framework.** That repo has
26 regression files; this one had *zero* tests. That gap was expensive
and provable: this session alone hit five real bugs by accident, each
found only by a slow real OpenLane run — `override_value()` JSON-quoting
strings (broke `SYNTH_STRATEGY`), the same function bracketing lists
(phantom `DIE_AREA[0]`), a space in a derived run tag (phantom Verilator
lint error), `classify_stage()` matching incidental WARNING lines
(misfiled sram_wrapper's failure stage), and `mcp_server.py`'s duplicated
orchestrate loop drifting from the real one. Every one is a pure
function needing no Docker, and every one dies instantly to a unit test.

`tests/` now covers exactly that layer (38 tests, milliseconds, run with
`python3 -m unittest discover -s tests`). What was borrowed is the
convention that each test names the real failure it guards, so a reader
can tell a deliberate pin from an incidental assertion. What was
deliberately *not* borrowed is pytest: this pipeline is dependency-free
by design, so the suite uses the standard library — soul.md's "borrow the
working part, not the whole machine" applied to the very repo that phrase
came from.

Integration behaviour is not mocked. Faking Docker/OpenLane would prove
nothing about the tools this pipeline actually shells out to, and those
paths already run for real on every orchestrate.

The suite was validated by negative control, not just by passing:
re-introducing the string-quoting bug and the WARNING-classification bug
each failed exactly its own test, and restoring each turned the suite
green again. A suite that cannot fail is not evidence.

**2. Diagnosis grounding (`verify_diagnosis.py`).** The genuinely
transferable idea in that repo's `scripts/agent_selftest.py` is its
grading principle: judge an agent's answer by *independent
cross-validation against what the backend really measured*, never by
string similarity to an expected answer. This pipeline has no always-on
agent endpoint, but it does have agent-written prose stored in
reference-db, so the principle applies to those artifacts directly.

It guards a failure that already happened here: sram_wrapper's first
diagnosis confidently blamed the macro's `clk0`/`clk1` pins without ever
opening the `.lib`, and had to be rewritten once the real liberty file
was read. The check is deliberately narrow, because the honest version
has to be — it cannot decide whether a diagnosis is *correct* (that is
exactly the judgment `request_review.py` escalates to a human). What it
can decide: every EDA error code and candidate tag the prose cites must
appear in that case's own recorded data. That catches invented
references and stale copy-paste from another design, and nothing more —
claiming otherwise would be the kind of over-reach the check exists to
oppose.

Wired into `self_improve.py`'s scan rather than left as a standalone
script (a check outside the loop is a check that doesn't happen) and
exposed as the `ppa_verify_diagnosis` MCP tool. A real false positive
surfaced on its first run against real data — the tag pattern matched the
ordinary English word "candidate" — and is now fixed (a hyphen is
required after the prefix) and pinned by a test, since a checker that
cries wolf on normal prose gets switched off. Verified against every
committed case: all cited references are grounded.

## Making the dashboard explain the service

Opened cold as a newcomer would, the dashboard had three measured
problems — all confirmed in a real browser before changing anything,
not assumed:

1. **It never said what the service does.** The first prose on screen
   explained where the data came from ("each case below is a real
   `orchestrator.py` run") — the data *source*, never the service. A
   reader could not tell what goes in (RTL + PPA targets), what comes
   out (a signed-off layout), or what the agent does in between.
   Everything on the page was evidence for a process the page never
   stated.
2. **22.3 screens of scrolling** (16,062px). Every case rendered fully
   expanded, so there was no way to see *what cases exist* without
   scrolling through all of their contents.
3. **The layout image was 902px tall** — larger than the viewport. A
   regression introduced by the previous change: it buried everything
   below it and made the page read as one giant picture.

Fixed:

- **`HowItWorks.tsx`** — an orientation panel above everything else:
  one paragraph on what the agent does, the loop as a real four-step
  flow (input → propose → run real OpenLane → judge on metrics.json),
  and its three outcomes written as what they actually are —
  `PASS` (Pareto-ranked winner), `FAIL ↺` (recognised failure, self-
  repaired within a bounded budget), `FAIL ⚑` (unrecognised failure,
  escalated rather than guessed). Those three are exactly
  `orchestrate()`'s three STOP_REASONS, so the diagram is a view of
  real control flow, not an idealised funnel.

  Its summary counts are computed from the same cases rendered below,
  so the explanation and the evidence cannot drift apart. Verified
  against an independent computation over `reference-db/`: 3 designs,
  5 cases, 28 real candidate runs, 4 closed, 3 self-repaired — all five
  match exactly.
- **Cases collapse by default**, newest open. A collapsed row still
  shows design, date, CLOSED/OPEN and pass/fail counts, so the list is
  scannable at a glance — which was impossible before.
- **Layout image capped at 300px**, click to expand/shrink.
- **Sidebar label** changed from "report tabs" to "analyze your own
  reports": those tabs are a separate capability from the pipeline
  (paste an existing EDA report, run a one-off OpenSTA sim), and the
  bare label left newcomers reading them as more pipeline output.

Result, measured in the same browser: **22.3 screens → 6.2** (a 72%
reduction) with strictly more explanation on the page than before.
Expand/collapse, both locales, and the flow's layout geometry (four
steps in one row, arrows between and none trailing) were each verified
live rather than inferred from the diff.

## The technology half of DTCO (from analog-layout-optimizer)

`pareto.py`'s ranking was taken from
github.com/kos2001/analog-layout-optimizer earlier. Revisiting that repo,
the other genuinely transferable thing is `layout_opt/process_change.py`
— not its analog machinery (differential-pair device geometry, none of
which applies to standard-cell digital), but its *framing*: when the
process changes, the schematic is fixed while the physical
implementation must be rebuilt, and what you want out of it is a
before/after pair plus an explicit record of what stayed invariant.

That named a real gap here. The dashboard calls itself a DTCO
(design-technology co-optimization) console, but every case in
reference-db varies only *design* knobs — `FP_CORE_UTIL`, `DIE_AREA`,
`SYNTH_STRATEGY` — against one fixed technology. The technology half of
"co-optimization" was branding, not something the pipeline had ever
done, even though this repo's own PDK has two real standard-cell
technologies installed (`sky130_fd_sc_hd`, `sky130_fd_sc_hs`).

`pipeline/tech_compare.py` closes it: same design, N technologies, one
real full OpenLane run each, judged by the same `score()` as every other
case, reported as a PPA delta alongside the `design_invariants` that
were held constant (without which the deltas are uninterpretable).

**A real silent bug found and fixed while building it, worth recording
because the first result looked like a finding.** Selecting the library
via `--override-config STD_CELL_LIBRARY=<x>` is accepted by OpenLane and
lands correctly in the run's `resolved.json` — and changes nothing. The
first comparison reported a perfect 0.00% delta on area, utilization and
power between hd and hs, which reads as "these technologies are
equivalent" rather than "the override did nothing". Checking the actual
gate-level netlist rather than trusting the requested config exposed it:
both runs instantiated only `sky130_fd_sc_hd` cells. OpenLane 2 selects
the SCL from the `--scl` CLI flag, now plumbed through `run_stage()`.

Two defences were added, not one, because the config-looked-right
failure mode is invisible in metrics:

- `cells_used()` reads which libraries the real netlist actually
  instantiates, and a run whose netlist doesn't contain the technology it
  requested is marked `technology_not_applied`.
- `delta_vs_baseline()` refuses to compute a delta against such a run.
  A silently meaningless comparison is worse than a failed one.

With `--scl` actually in effect the result is a genuine DTCO finding
rather than a fake tie: `sky130_fd_sc_hd` passes at 290.278 µm² (netlist
confirmed hd), while `sky130_fd_sc_hs` **fails signoff with 21 Magic DRC
errors** — verified to have really synthesized `sky130_fd_sc_hs` cells
before failing, so this is the technology failing for this design, not
the flag being ignored again. "This technology does not work here yet" is
a real result and is reported as one rather than dropped.

## RL / linear programming, and whether OpenLane is being used fully

Asked (a) how RL or linear programming could optimize this pipeline and
(b) whether OpenLane's own features are being used fully. Answered from
this repo's real data and real measurements rather than from what those
techniques do in general.

**The relationship isn't even a function, let alone a linear one.**
Extracting every real (requested `FP_CORE_UTIL` → achieved utilization)
pair from reference-db gives 4 distinct points, and the same requested
35 produced three different achieved values on the same design:
`AREA 0`/`AREA 2` → 0.604, `DELAY 1` → 0.565, `DELAY 4` → 0.468.
Achieved utilization depends on `SYNTH_STRATEGY` as well, and with only
two distinct `FP_CORE_UTIL` values ever run (25, 35) the multivariate
version is under-determined. So an LP formulation has nothing to stand
on here: LP needs a linear objective over decision variables, and the
objective (area/power/timing) is only observable by running OpenLane.
This also retroactively justifies keeping the repair step-down a
conservative constant rather than scaling it by the overshoot — that
scaling would have assumed exactly the relationship the data refutes.

**RL** remains declined for the reason already in `soul.md`, now with a
number attached: 28 real candidate runs exist in total, each ~1 minute.
RL over config choices needs orders of magnitude more episodes than
that, and manufacturing them would mean fabricating the thing this
project refuses to fabricate.

**What actually pays is using OpenLane's own features.** The audit found
real capabilities never used: `-f/--flow` (the shipped `Optimizing` and
`SynthesisExploration` flows — 6 and 3 steps against `Classic`'s 78,
measured at 11s and 10s against Classic's 64s on counter4), `-j/--jobs`,
`--from`/`--only`, and `--reproducible`. Every candidate has been paying
for all 78 steps.

**The screening step, and the wrong reasoning that preceded it.** The
first design screened every candidate to a cheap cutoff on the argument
that all 13 crashed candidates in reference-db died at step 13/78 or
20/78, so an early cutoff reproduces 100% of observed failures. Measured
end to end, it made a crash-heavy run *slower* (107s vs 95s on
`counter4_tinydie`). The reasoning conflated two different claims: a
crashing candidate already costs only ~10s, because OpenLane exits at
the failure. Screening it saves nothing and adds a process launch.
"Failures happen early" is not "failures are expensive."

The expensive case is the opposite: a candidate that completes all 78
steps and is only then rejected against a target this pipeline set.
`screen_candidates()` was rewritten to prune on the early utilization
metric instead of on crashes, and a candidate that crashes during
screening is deliberately returned as a survivor so the real run records
it through one code path.

The prune is sound rather than heuristic: it fires only when the *early*
utilization already exceeds the target, and utilization can only grow
after that point (CTS and timing repair add cells inside a fixed die).
Measured on counter4: 0.3646 at the cutoff, 0.6042 at signoff — the
direction that makes a false prune impossible. The early number is a
lower bound and is never recorded as if it were the final result.

Measured on the case it is for — a candidate that would complete and
then miss its target — **69s → 10s, a 6.9× saving for the identical
verdict**. Opt-in (`--screen`), because it is a loss when everything
passes or everything crashes early.

## Known limitations / explicit non-goals

- SRAM bitcell/array layout generation is not covered by this pipeline.
- The topology "signature" is a coarse heuristic, not a learned
  embedding — fine for bootstrapping a reference DB, likely to need
  revisiting once there are enough cases to see whether it actually
  clusters similar designs.
- Runs under Docker emulation on Apple Silicon (the image is amd64-only,
  same constraint the existing OpenSTA sim server already has) — slower
  than native, acceptable for a validation vertical slice, worth
  revisiting if iteration turnaround becomes the bottleneck.
- On-demand diagnosis translation (`POST /translate`) works for text of
  any length tested so far, but the `ppa-eda-analyst` gateway model
  delivers the whole response in one burst rather than token-by-token —
  a long diagnosis (thousands of characters) can take 3-4 minutes with
  zero visible progress until the end. `TranslateBlock` shows an
  elapsed-time counter and an explicit hint rather than a silent
  spinner, but the underlying wait itself isn't shortened (no chunking,
  no alternate faster model attempted).
