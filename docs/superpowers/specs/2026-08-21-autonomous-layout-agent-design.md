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

## "Dashboard" was the wrong frame — carrying stage 8 inside the console

Observation from the user: the word *dashboard* itself was constraining
what this could be, given the goal is an end-to-end DTCO process run by
agents. That turned out to be diagnosable rather than merely semantic.
The name was the symptom; the constraint was real and located precisely:

Stages 1–7 happen in the UI (trigger a run, watch it, read the verdict).
Stage 8 did not. At escalation — the exact moment the process needs
judgment — the panel rendered a shell command:

    needs review — run `python3 pipeline/request_review.py request …`

and stopped. There were no review endpoints on the server at all. So the
tool watched the process and then handed it off at the hardest step,
which is what a dashboard does and what a console must not.

Closed with three endpoints, one per real step of the workflow
`request_review.py` already defines, so the console drives that script
rather than reimplementing it:

- `POST /review/request` — generates the request from the case's real
  diagnosis and returns the file's actual content, so the operator sees
  exactly what a reviewer would be given.
- `POST /review/ask` — streams a real second opinion from
  hermes-gateway, over the same SSE path the diagnosis and translation
  features already use.
- `POST /review/apply` — writes the verdict back into the case. The
  response text goes through a temp file, never a shell argument — the
  same reason `request_review.py` exists at all, after a backtick in
  review prose once ate part of a diagnosis through shell interpolation.

`ReviewWorkflow` in `PipelineTab.tsx` renders these as three gated steps
(step 2 unlocks only once a request exists, step 3 only once a review
does). The verdict is recorded under `hermes-review`, not under a
subagent's name: it came from the gateway model, and filing it as
`feedback-optimizer` would misstate who actually reviewed it.

`isSafeDesignName()` was factored out while doing this — the design-name
validation was inline in one route, the review endpoints need the
identical rule, and two copies of a security check is one too many.

Verified against the real OPEN case (`sram_wrapper`) rather than a
fixture: `/review/request` returned the real 9,607-character request and
the browser rendered it; path traversal was rejected (400); `/review/ask`
returned a substantive real review through the gateway — which notably
*disagreed* with the earlier subagent verdict, arguing one more candidate
was worth trying; `/review/apply` took `human_in_the_loop` from 1 entry
to 2 with the right agent name. The apply test's entry was then reverted,
since it was a mechanism test rather than a real review.

## Why translation was slow, and the plain-model route

Asked why "just a translation" took minutes. Measured rather than
guessed, against the real gateway:

| input | prompt tokens | completion tokens | elapsed |
|---|---|---|---|
| `"hi"` (2 chars) | 19,129 | 57 | — |
| 17 chars | 19,221 | 159 | **9.2s** |
| 2,000 chars | 19,715 | 1,488 | ~20s |
| 8,744 chars (real `sram_wrapper` diagnosis) | 21,234 | 9,843 | ~200s |

Three findings, in order of how much they cost:

1. **It was never "just a translation" to the endpoint.** Every model on
   this hermes-gateway is somebody's persona — all 17 of them — and
   `ppa-eda-analyst` spends **~19,100 prompt tokens before it reads any
   input**. Prompt size barely moves across a 500× change in input,
   which is what identifies it as fixed persona overhead rather than
   anything to do with the text.
2. **So it reasons instead of translating.** The 8,744-character
   diagnosis produced 9,843 completion tokens for roughly 3,500 tokens
   of actual Korean — about **two thirds of the wait was the analyst
   thinking about chip design**. Latency tracks completion tokens, not
   prompt: ~6s fixed plus roughly 1/51s per generated token.
3. **It doesn't stream token-by-token** (every chunk shares one upstream
   timestamp), so nothing appears until it is entirely finished.

The fix is to stop asking an analyst to translate. `proxyChat()` now
takes `{ preferDirect: true }`, and `/translate` sets it: with
`PPA_EDA_DIRECT_LLM_KEY` + `PPA_EDA_DIRECT_LLM_MODEL` configured it
calls a plain OpenAI-compatible model directly (OpenRouter by default)
instead of the persona gateway. Entirely opt-in — unconfigured, it falls
back to the gateway and behaves exactly as before, verified.

Borrowed from `~/gitspace/lsi_error_analyzer`, which drives OpenRouter
directly (through Agno, confirmed installed and configured there at
2.6.9). The transferable part is the *direct-LLM route*, not Agno: that
is a Python framework, this is a Node server already speaking the
identical wire format, so adopting it would add a dependency to gain
nothing — `soul.md`'s "borrow the working part, not the whole machine".

Verified both branches without ever handling a real third-party key:
unconfigured, `/gateway-status` reports `directLlm: null` and a
translation still completes through the gateway (13 SSE events, 24.9s);
configured with a deliberately fake key, the same request genuinely
reaches openrouter.ai and returns `direct LLM error 401` — proving the
switch routes, not just that it parses. A real key is the operator's to
supply.

**A caching attempt that did not work, and was reverted.** A diagnosis
is immutable once written, so caching its translation in `reference-db`
should make every later viewing instant. One real bug was found and
fixed along the way — the server-side SSE parser assembled 0 characters
because it parsed each transport chunk independently, and SSE events
routinely straddle chunk boundaries (the browser's own `pipeSse()`
buffers the partial trailing line; the server copy did not). Even after
that fix no cache file appeared, and rather than keep spending on it the
whole cache was reverted: code that silently never works is worse than
code that was never added. Worth recording that the apparent speed-up
seen while testing (0.5s on a repeat call) was **the gateway's own
prompt caching**, not this cache — there was no cache header and no file
on disk.

## Grouping the 8 stages by role, and drawing the repair loop

The 8 process stages rendered as a flat 4-column grid of identical
cards. Three problems with that, all about what it failed to
distinguish:

- **It read as a checklist, not a pipeline.** No direction, no sense
  that a candidate moves through it.
- **Every stage looked like the same kind of thing.** "Reads the
  design", "proposes configurations to try", "runs and judges a
  candidate", and "decides what happens next" are four different kinds
  of work, and the grid gave them identical visual weight.
- **The repair loop was invisible** — despite being this agent's
  defining behaviour. Stage 8 doesn't end a run; it feeds a new
  candidate set back into stage 3. Nothing on screen said so.

`ProcessStages` now groups the stages into four phases — UNDERSTAND
(1–2), PROPOSE (3), EVALUATE (4–7), DECIDE (8) — laid out left to right
with arrows between them, each phase carrying a one-line statement of
what that kind of work *is*. Column widths follow the real distribution
of work, so EVALUATE is visibly the widest at 4 of the 8 stages.

The grouping follows the code rather than being tidy: stages 4–7 are
exactly the ids `classify_stage()` can assign to a candidate (where its
run actually got to), stage 3 is what `placement-strategist` proposes,
and stage 8 is the only one tracked per-candidate by
`produced_by_feedback` instead of by stage.

The loop is drawn as a full-width return path beneath the row, labelled
from this case's real numbers: "3 candidate(s) here came from DECIDE
feeding a new configuration back into PROPOSE" when it fired, and an
explanation of when it *would* fire when it didn't — so it reads as
something that happened rather than diagram decoration.

Verified against real data in the browser, both branches: the four
phases render on one row at x = 299/521/717/1113 with widths
199/171/**373**/171 and stage counts 2/1/4/1; `counter4__2026-08-23`
shows the loop idle and `counter4_tinydie__2026-08-23` shows it fired
with 3 — cross-checked against the case JSON
(`produced_by_feedback` = 0 and 3 respectively).

One fix came out of measuring rather than eyeballing: the first colour
assignment gave PROPOSE `--accent` (67,217,211) next to EVALUATE
`--good` (52,211,153), an RGB distance of ~59 — two adjacent phases that
are nearly the same teal, which defeats the point of colouring by role.
Moving PROPOSE to the theme's existing `--warn` amber raised the minimum
pairwise distance across all four phases to **104**.

## Emphasis and liveness: what the console was quietly getting wrong

Three related complaints, all correct, all about attention rather than
missing features.

**Human-in-the-loop was buried.** It rendered *last* in a case card —
after the metrics, the 8 stages, the agent legend, the layout render, the
chart, and every iteration table, and immediately after an 8,700-character
diagnosis. It is the only part of a case that asks the operator to *do*
something, and it sat past the point anyone scrolls. Moved to directly
under the status metrics (verified: it is now the 2nd child of the card
body, was last), and an OPEN case now carries a real bordered treatment
plus a "NEEDS YOU" badge rather than reading like another subsection.

**The diagnosis dominated by sheer volume.** It is real, kept, and worth
reading — but open by default it outweighed everything actionable. Now a
`<details>` with a one-line teaser and the character count, so the choice
to read it is deliberate.

**Nothing was live.** The page was a snapshot of whenever the tab was
opened. `reference-db` is written by things this browser doesn't
control — an `orchestrator.py` run from a terminal, `self_improve.py` on
a schedule, an MCP tool call — so the console silently showed stale state
until a manual reload. Added a 15s auto-refresh plus a status strip
carrying the two facts that make liveness legible: how many cases are
waiting on a human, and when the view last actually re-read the store.
Without the timestamp an auto-refreshing page is indistinguishable from a
frozen one. The poll is cheap because `GET /reference-db` is already
mtime-cached server-side — a poll that finds nothing new costs a `stat`
per case file, not a re-read and re-parse.

Verified live in the browser rather than from the diff: the strip reports
"1 case waiting on a human decision" (matching the one OPEN case,
`sram_wrapper`), the refresh timestamp advanced on its own from 7:32:03
to 7:32:33 across a single 18s wait, the pulse animation is running (and
is disabled under `prefers-reduced-motion`), the HITL block renders with
its badge, and the diagnosis renders collapsed.

## Using OpenROAD directly — and overturning a recorded conclusion

OpenLane already runs OpenROAD for every `OpenROAD.*` step, so in one
sense it was applied from the start. What was never used is OpenROAD
*as its own tool*: the `.odb` database it writes holds per-net placement
facts that OpenLane's aggregate `metrics.json` does not expose, and this
pipeline had no way to ask about one specific net.

That gap was not hypothetical — it blocked a real conclusion.
`physical-constraint-evaluator`, reviewing sram_wrapper's RSZ-0090
failure, recorded one item it could not close:

> "The diagnosis's adjacency claim is inferred entirely from capacitance
> arithmetic, never from an actual placed net length — there's no
> .odb/placement report on disk ... I can't verify this either way
> without run artifacts, and none exist."

`pipeline/odb_query.py` closes it: it runs `openroad` (already in the
image) against a run's real `.odb` and reports per-net pin count, HPWL
and max span in microns. sram_wrapper was re-run to
`OpenROAD.GlobalPlacement` — the placement state `RepairDesignPostGPL`
evaluates, i.e. exactly where RSZ-0090 fires — and queried.

**The measurement contradicts the recorded diagnosis.** The case had
concluded, with both `feedback-optimizer` and
`physical-constraint-evaluator` concurring, that placement was "already
near-optimal ... missing spec by a small, physically-floored margin, not
a distance the placer hasn't tried yet", resting on the claim that
"0.01pF is essentially just that bus's own pin capacitance ... i.e. no
additional wire". The real `.odb` says otherwise: `addr0[*]` nets span
170–299 µm and `addr1[*]` span 114–289 µm. The cleanest counterexample
is `addr1[0]` — a **two-pin** net, one driver and one SRAM input pin, no
fanout to blame — whose pins sit **249 µm apart**. That is a quarter of
a millimetre of wire, not "no additional wire".

So the macro-adjacent-placement fix the diagnosis itself proposed has
not in fact been achieved by the placer, and therefore was never tried.
The case stays OPEN, but for a better reason: not "physically floored,
nothing to try" but "the proposed fix was never applied".

Two things deliberately *not* claimed, recorded in the case as open:
this does not prove fixing adjacency resolves RSZ-0090 — a 250–300 µm
sky130 wire would contribute far more than the 0.01pF RSZ reported, so
that figure needs reconciling with the measured length before choosing
what to run next. And `wmask0` has no signal net at all, which is
correct rather than a measurement gap: `sram_wrapper.v` ties it to
`4'b1111`, so the earlier diagnosis listing it as a violating bus was
over-broad on that one pin.

Recorded through `request_review.py apply` (agent `odb-measurement`), so
it lands in both the diagnosis and the `human_in_the_loop` history like
any other review; the grounding check still passes. Exposed as
`ppa_odb_query` on the MCP server.

## Using Yosys directly: nothing was checking that the circuit is correct

Same shape as the OpenROAD work above — Yosys already runs every
`Yosys.Synthesis` step, so what was missing was using it *as its own
tool*. Here that exposed a genuine hole rather than a convenience gap.

`score()` checks DRC, LVS, utilization and timing. All real, none
functional. LVS compares the *layout* against the *netlist*, confirming
the physical implementation matches what was handed to placement —
**nothing has ever compared that netlist against the RTL**. A synthesis
result that is clean, legal, fast and functionally wrong would be
reported as PASS.

Not hypothetical: this pipeline deliberately varies `SYNTH_STRATEGY`
(`AREA 0` / `AREA 2` / `DELAY 1` / `DELAY 4` are real candidates in
counter4's `run_spec.json`) and `STD_CELL_LIBRARY`, both of which change
what Yosys emits. Judging those candidates on area and timing while never
checking they still compute the same function is exactly the gap.

`pipeline/equiv_check.py` closes it with Yosys's own SAT equivalence
checker: read the RTL as `gold`, the gate netlist plus the real liberty
as `gate`, build a miter, discharge with `equiv_simple` + `equiv_induct`,
and assert via `equiv_status -assert`. Measured at **~1 second** on
counter4, so it is cheap enough per candidate. Wired into
`run_candidate()` behind `--verify-function`; a mismatch, a *vacuous*
pass, or an inability to run the check at all each fail the candidate
outright — a wrong circuit that meets timing is still wrong, and "could
not verify" must never read as "fine".

**A real bug found by running it, worth recording because the first
result looked like a finding.** Against a full flow both candidates came
back NOT EQUIVALENT — while the identical design had proved equivalent
minutes earlier. The tell was `unproven_points: None`: the regex had
matched nothing, so Yosys had *errored*, not disproved. Cause:
`final/nl` contains cells inserted after synthesis for physical reasons
(`sky130_fd_sc_hd__fill_1`, tap, decap, antenna diodes) which carry no
logic function, so `read_liberty -ignore_miss_func` skips them and Yosys
aborts on a module that "is not part of the design". `find_netlist()`
now prefers the synthesis-stage netlist — which is also the right
artifact for the question being asked, since "did synthesis preserve the
function?" is about synthesis output, and the later insertions are
covered by LVS and DRC instead. What this does *not* independently
verify is that those physical insertions were themselves harmless; that
limit is stated in the code rather than glossed.

Validated by negative control, not just by passing: corrupting the RTL
(`count + 4'd2` against a netlist that counts by 1) produced "Found 4
unproven $equiv cells" and exit 1, while the correct pair produced
"Equivalence successfully proven!" with 4 proven / 0 unproven. The
integration is separately pinned by tests covering the three ways this
gate could silently go green — mismatch, vacuous pass, checker error.

Exposed as `ppa_equiv_check` on the MCP server. Left opt-in because
enabling it changes what a verdict *means*, which should be a deliberate
choice; there is otherwise no reason not to, at a second per candidate.

## Reading the OpenSTA analysis that was already being thrown away

OpenSTA was already used twice — the `/simulate` endpoint runs it
directly, and OpenLane runs it for timing signoff. The gap was what
happened to its output.

Every run writes a full set of real OpenSTA reports per corner:
`max.rpt`/`min.rpt` (the critical path, stage by stage, with fanout,
capacitance, slew and delay at every point), `checks.rpt` (containing
`report_check_types -max_slew -max_cap -max_fanout -violators`),
`clock.rpt`, `power.rpt`, `tns`/`wns`/`skew`. **Nothing in this pipeline
had ever read one of them** — `score()` takes
`timing__setup__wns__corner:*` from metrics.json and stops. A failing
candidate reported "worst setup WNS -0.05" and nothing else: which path,
which cells, where the delay accumulated, all discarded.

`pipeline/sta_report.py` reads them. Deliberately a reader, not a re-run:
OpenSTA already did the analysis against the run's real parasitics and
liberty, so recomputing it would cost minutes to reproduce what is on
disk, and any disagreement between the two would be a bug rather than a
feature.

Two real bugs found by running it, both of the "silently wrong" kind:

- **Arrival time was under-reported.** The final port line of a path has
  no fanout/cap columns, so taking the last parsed stage gave 1.653849
  where STA itself printed 1.655644. Only 1.8 ps, but reporting a number
  the tool did not produce is wrong regardless of size. Now parsed from
  STA's own `data arrival time` line.
- **Failed runs returned an empty result, silently.** Pre- and post-PnR
  STA write one subdirectory per corner; mid-PnR STA writes the reports
  flat in the step directory. Only the first layout was handled, so
  sram_wrapper — which fails before post-PnR, i.e. exactly the case worth
  reading — produced no corners and no complaint. It now handles both and
  *raises* rather than returning empty: no corners must never read as no
  problems.

**What it found on the open sram_wrapper case, and what that overturns.**
The run was re-executed; it fails at step 31 `repairdesignpostgpl`, where
RSZ-0090 fires, so step 30's mid-PnR STA is the state immediately before
the failure. `report_check_types` reports **91 max_slew violations**,
ranked:

    u_sram/clk1      limit 0.750000  slew 1.599854  slack -0.849854
    u_sram/clk0      limit 0.750000  slew 1.593345  slack -0.843345
    u_sram/addr1[1]  limit 0.040000  slew 0.728622  slack -0.688622
    u_sram/addr0[0]  limit 0.040000  slew 0.667010  slack -0.627010

This settles three things the case had been arguing rather than measuring:

1. **The "CORRECTED" diagnosis over-corrected.** Its first version blamed
   the macro's clk0/clk1 pins and was rewritten as "that was wrong",
   moving the cause entirely to the addr buses. Both are real violators,
   and the clock pins are the *worst* by slack. The addr finding was
   right; declaring the clock observation wrong was not.
2. **"Physically floored, 0.043ns is the best achievable" does not
   describe this design.** The measured slews on those pins are
   0.504–0.729 ns against a 0.04 ns limit — twelve to eighteen times
   over, not a 3 ps overshoot. So RSZ's "0.043ns at 0.01pF" was a floor
   (best buffer into a bare pin), never the placed net's state. That was
   precisely the open question the previous `odb-measurement` review
   recorded, and it is now answered.
3. **It independently corroborates the .odb measurement.** OpenROAD found
   those nets spanning 170–299 µm; OpenSTA finds slews 12–18× over limit
   on the same pins. Two different tools, two different databases,
   agreeing — stronger than either alone.

Recorded via `request_review.py apply` (agent `sta-measurement`) with an
explicit note on what it does *not* settle: nothing here shows a 0.729 ns
slew is reducible to 0.04 ns by placement, and the clock-pin violations
are a separate clock-tree problem that was mistakenly ruled out. Exposed
as `ppa_sta_report` on MCP; tests use report text copied verbatim from
real runs so an OpenLane format change fails loudly.

## chipfoundry/openlane2: the same repository, and what that did surface

Asked to apply github.com/chipfoundry/openlane2. Checked before assuming
it was a different project, and it is not one: **the repo id is identical
to github.com/efabless/openlane2 (589378383)** — OpenLane 2 was renamed
from the efabless org to chipfoundry, and GitHub transparently redirects
the old path. So there is no fork to port ideas from; it is the upstream
this pipeline already runs.

Two further facts worth having checked rather than guessed:

- **2.3.10 is already the newest stable tag.** Everything above it is
  `3.0.0.dev*`, a pre-release line. Not adopted: every number in
  reference-db came from a real run on 2.3.10, and re-baselining all of
  it onto a development build trades real comparability for novelty.
- **There is no `ghcr.io/chipfoundry/openlane2` image.** The published
  container is still under the efabless namespace. Verified by asking
  the registry: the chipfoundry name returns not-found, the efabless one
  resolves. So the image reference must *not* be "modernised" to match
  the new org name, and that is now pinned by a test so a future tidy-up
  doesn't point the pipeline at an image that does not exist.

**What the investigation did legitimately turn up.** The pinned image was
hardcoded independently in four modules — `run_stage`, `render_layout`,
`odb_query`, `equiv_check`. Changing the pin meant editing four files,
and missing one would not fail loudly: it would run part of the pipeline
on a different OpenLane build while writing results into the same
reference-db as if they were comparable. For a project whose whole claim
is that its measurements are real and comparable, two silently different
toolchains is a correctness hazard rather than untidiness — the same
shape as the duplicated orchestrate loop and the duplicated design-name
check found earlier.

`pipeline/toolchain.py` is now the single definition, and it also carries
the rename and version reasoning above so the next person doesn't have to
re-derive it. `write_case()` records `toolchain` on every case, so a
stored result is attributable to the build that produced it instead of to
"whatever was installed at the time" — verified on a real run. A test
fails if any module reintroduces its own image string.

## KLayout: every layout image was rendered with the wrong colours

Reviewing how this project uses KLayout turned up a real defect rather
than a stylistic one.

`render_layout.py` called `load_layout()` and `save_image()` and nothing
else. It never loaded any layer properties, so KLayout auto-assigned
arbitrary colours to every GDS layer. The result: met1, met2, poly,
diff, nwell and the rest all came out as an undifferentiated green/blue
mesh. The image was *technically* a real render of the real GDS, and
practically unreadable — you could not tell a routing layer from a
diffusion region.

That matters more here than it would elsewhere. These images exist
specifically because arxiv.org/html/2605.06936v3 measured that a layout
image improves diagnosis of real post-flow violations, and they are
handed to `physical-constraint-evaluator` and
`routing-candidate-evaluator` with instructions to look at them. An
image whose colours carry no information cannot deliver that benefit —
it looks authoritative while conveying nothing about layers.

The PDK ships the fix and it was simply never used: `sky130A.lyp`,
246 KB of real sky130 layer definitions, sitting at
`pdk/sky130A/libs.tech/klayout/tech/`. Two things were needed — mounting
the PDK into the render container at all (it previously saw only its own
temp directory) and calling `load_layer_props()`.

Verified by rendering the same GDS before and after. Before: uniform
green/blue, layers indistinguishable. After: real sky130 colouring —
magenta met1 including the power rails and routing grid, cyan met2 and
port stripes, red diffusion, blue poly/contacts — with the standard-cell
rows and the routing above them clearly separable, and port labels
(`count[3]`, `clk`) legible.

The script now reports `LYP_LOADED`, and the caller warns when it is
false, so a fallback render is distinguishable from a correct one
instead of both looking equally trustworthy. Tests pin that the PDK is
mounted, that the properties are loaded, that the result is reported,
and that the `.lyp` really exists where the container path points.

Two things deliberately left alone:

- **The `Fontconfig error: Cannot load default config file` on every
  render is cosmetic and is not silenced.** Text renders correctly via
  fallback — confirmed in both images, whose labels are legible. It can
  be removed by setting `FONTCONFIG_FILE`, but only to a nix-store path
  containing a build hash (`/nix/store/s2lqglzd…-fontconfig-2.15.0/…`)
  that breaks on any image rebuild. Pinning a fragile path to hide a
  harmless warning is a worse trade than leaving the warning explained.
- **The two layout images already in `reference-db/layouts/` predate
  this fix** and still have the old colours. Their run directories are
  long deleted, so they cannot be re-rendered from the runs that
  produced them; re-running an equivalent configuration and passing the
  result off as the original case's layout would be worse than leaving
  them stale. They will be replaced naturally the next time those
  designs run.

## garden-of-eda.com: what it pointed at, and the audit it prompted

Asked to check garden-of-eda.com (a catalogue of 155 open-source EDA
tools) and apply what fits. Most of it is already in use here — Yosys,
OpenROAD, OpenLane, KLayout, Verilator, OpenSTA. Two leads were followed
properly rather than assumed:

- **CoreSmith** (`facebookexperimental/coresmith`, Meta) — "Prompt to
  GDS Agentic Flow", the same domain at much larger scope: architecture
  → RTL generation → cocotb verification → synthesis → backend, driven
  by LangGraph. Its verification stage is the one thing this project
  structurally lacks (nothing checks the *RTL's* intent; the Yosys
  equivalence work above checks netlist-vs-RTL, not RTL-vs-spec). Not
  adopted: generating testbenches is a different product from closing
  layout, and this pipeline takes RTL as given input.
- **PPABench**, cited by CoreSmith as its benchmark suite and directly
  relevant since everything here is bottlenecked on having only three
  designs. **It does not exist publicly** — `facebookresearch/ppabench`
  returns 404. Recorded as checked-and-unavailable rather than left as a
  plausible-sounding lead.

**What did come out of it is the most consequential finding of the
task.** CoreSmith's flow leans on Verilator lint, which prompted the
question of whether this pipeline uses the lint results OpenLane already
produces. It does not — and auditing that properly showed the problem is
far larger than lint:

> **OpenLane emits 279 metrics on a real completed run. `score()` read
> 32. 247 were discarded.**

Among the discarded were genuine pass/fail signoff gates:

- `klayout__drc_error__count` — **a second, independent DRC signoff.**
  Only Magic's was checked, so a candidate KLayout flagged and Magic did
  not would have been reported PASS. This is the most serious of them.
- `route__antenna_violation__count`, `antenna__violating__nets` — real
  manufacturing failures, never checked.
- `design__max_slew_violation__count` / `max_cap` / `max_fanout`, per
  corner — **the same DRV family that produces RSZ-0090**, the failure
  this project has spent the most effort diagnosing, sitting in
  metrics.json as structured numbers the entire time.
- `design__power_grid_violation__count`, `synthesis__check_error__count`,
  `design__lint_error__count`, `design__violations`.

`score()` now gates on those. Deliberately *not* gated: lint warnings and
clock skew — real signals, but not pass/fail ones, and promoting a
warning to a failure would be overreach.

Verified both directions on real data rather than by inspection: a real
clean counter4 run still passes (every new metric is genuinely zero
there, so no regression), and each new gate was confirmed to actually
fire when its metric is non-zero. Both directions are pinned by tests —
a gate that cannot fail is not a gate.

## sigdox's tool list, and the hold-timing hole it led to

Checked sigdox.com/eda-open-source-tools. Nearly everything it names is
already in use here — Magic, Netgen, OpenSTA, KLayout, Verilator, and
OpenROAD's RCX covers what SPEF-Extractor does. One tool was genuinely
new and worth evaluating properly:

**CVC** (`d-m-bailey/cvc`) — a voltage-aware ERC checker for CDL
netlists, catching a class of error nothing else in this flow does
(floating gates, power shorts, forward-biased diodes, level-shifter
errors). Declined, on evidence rather than reflex: it is not in the
OpenLane image (C++ build from source), it wants **Calibre LVS CDL**
rather than the SPICE OpenLane emits, and it requires **Python 2.7.10**,
gcc 4.9.3 and power parameters supplied **from a Microsoft Excel file**.
Its value is in multi-voltage and analog/mixed-signal designs; this
pipeline runs single-voltage standard-cell digital on qualified sky130
cells, where the errors it targets are largely prevented by
construction. High cost, low marginal value here.

**What that evaluation did lead to is much more important.** Looking for
signoff checks the flow might be missing surfaced that OpenLane's own
metric library marks a specific set of metrics `critical=True` — its own
declaration of what constitutes a fatal result. There are 22. `score()`
gated on four.

The most serious gap in that list: **hold timing was never checked.**
`score()` computed worst *setup* WNS across corners and gated on it,
recorded hold WNS per corner, rendered it on the dashboard — and never
gated on it. Demonstrated directly rather than inferred: a candidate
with `hold_wns = -0.25` and 7 hold violations scored **PASS**, while the
pipeline displayed that negative slack on screen. Hold violations are
silicon-fatal and cannot be fixed after fabrication, which makes this
the worst thing the verdict could have been silent about.

`score()` now gates on OpenLane's own critical list rather than a
hand-picked one — hold WNS across corners, plus
`design__instance_unmapped__count` (synthesis left cells unmapped),
`design__xor_difference__count` (the two tools' GDS disagree),
`magic__illegal_overlap__count` (the exact failure that blocked the
hs-library experiment earlier), `design__disconnected_pin__count`,
`route__drc_errors`, the detailed LVS counts, and the setup/hold
violation counts.

Using the tool's own definition of critical is the point: it makes the
verdict agree with the tool it already trusts, instead of guessing which
failures matter.

Verified both directions, as before. Each gate confirmed to fire when
its metric is non-zero, and a real clean `counter4` run still passes with
no violations — so this is a genuine tightening, not a change that
invalidates existing results. Hold is still reported per corner as well
as gated, since the dashboard detail and the pass/fail decision are both
worth having.

## The Action Center: organising by what needs doing, not by what happened

Feedback: information is scattered and it is not clear what the user is
supposed to do. Both true, and the cause was structural rather than
cosmetic.

**The console was organised by case — a historical record.** Every fact,
including every point where the agent needs a human, lived *inside* a
case card. So "what needs me right now" was distributed across N
collapsed cards and could only be found by opening each one. The live
strip added earlier could say "1 case waiting on a human decision", but
not which, not why, and not what to do about it — it pointed at work
without leading anywhere.

`ActionCenter.tsx` inverts that. It is the first thing on the page and
answers one question: what does the agent need from you. Rows are sorted
by how much attention they need, so the top row is always the next thing
to do.

The intervention points are not invented for the UI — they are exactly
`orchestrate()`'s three STOP_REASONS plus "never run", each mapped to one
concrete action:

| state | what it means | the control |
|---|---|---|
| never run | no case exists | run the agent |
| `winner_found` | the agent closed it itself | nothing owed |
| `max_iterations_reached` | auto-repair was still proposing candidates and ran out of turns — a decision, but a mechanical one | re-run with a doubled budget |
| `no_repairable_failures` | no repair pattern matched; the agent stopped rather than guess — the one case needing real judgement | jump to the review workflow |

Colour carries the same meaning as the pipeline phases: red for a
decision only a person can make, amber for a mechanical one, dim for
nothing owed.

**Two things were required to make this honest rather than decorative.**
The "more budget" action would have been theatre if the console could
only re-run with the budget that already failed, so `POST /pipeline/run`
now accepts `maxIterations` and passes it to `orchestrator.py`. The
doubling rule is the same one `self_improve.py` applies, so the console
and the CLI recommend the same number rather than two different ones.
And "open the review workflow" *scrolls to and opens* the right case —
pointing at work without taking you to it is the scattering this was
meant to remove.

Verified live in the browser: the header reports one design waiting,
`sram_wrapper` sorts to the top as "needs your judgement" (correctly —
it is the only OPEN case, and its stop reason is `no_repairable_failures`),
the two closed designs fall below with their winning tags, and clicking
through opens the sram_wrapper card, scrolls to it, and lands on the
three-step review workflow.

## Stage artifacts: showing what each stage actually produced

The eight stage cards named a stage, its owning agent, and a count —
"3/9 candidate run(s) reached this stage" — and stopped there. You could
see that a stage *happened* but not what came out of it. Every stage's
real output was already in the case JSON; it was either buried elsewhere
(file pointers were only visible inside an expanded candidate row) or
not rendered at all.

Each stage card is now a button that opens its own artifacts in a
full-width panel below the phase row. Full width because the content is
tables and captured tool output — trying to fit that inside a grid cell
is why it wasn't shown in the first place.

What each stage shows, all read from fields the pipeline really recorded:

| stage | artifact |
|---|---|
| 01 Extraction | the real files the run produced — netlist, powered netlist, SPICE, DEF/LEF/GDS, SDC, metrics.json, SPEF, SDF, and the actual PDK version |
| 02 Topology | the design's structural signature from `topology.json` — what the agent knew *before* proposing anything |
| 03 Placement Strategy | every proposed candidate with the exact config override that makes it different, and whether it came from `run_spec` or from auto-repair — the agent's real decision, not a summary |
| 04–06 gates | the candidates that stopped there, each with its **verbatim captured OpenLane output** |
| 07 Verification & PPA | the signoff verdicts — area, utilization, worst setup, power, and the violation list for anything that failed |
| 08 Feedback | the stop reason, the candidates auto-repair produced with the config each was given and how it turned out, and the human-in-the-loop reviews on record |

Failure output is shown verbatim rather than paraphrased: the specific
numbers in it (`PDN-0185 Insufficient width (19.32 um)`) are exactly what
a diagnosis gets built from, and this project has already been burned
once by a diagnosis that reasoned from a summary instead of the source.
A stage with no artifact says so explicitly rather than rendering an
empty shell.

Verified against real data in the browser, one type at a time: stage 01
lists all 11 file artifacts with the real PDK hash; stage 03 lists all
nine candidates with their real `FP_CORE_UTIL` / `SYNTH_STRATEGY`
overrides; stage 04 shows exactly the three candidates that stopped there
with the real `PDN-0185` text; stage 07 shows six signoff verdicts with
real area, utilization and power.

## Cutting the console back: the fix was deletion, not another panel

Repeated feedback that the console was scattered and it wasn't clear what
to do. Each round I answered by *adding* a panel — an Action Center, a
How-it-works, a live strip, stage artifacts. Each solved its local
problem and made the page longer. Measuring it finally showed that the
additions were the problem:

| | before |
|---|---|
| top-level blocks stacked | 10 |
| buttons on one page | 27 |
| preamble before any evidence | **1,096px** |
| newest case card | **3,763px** (five screens by itself) |
| total | 7.1 screens |

And the same fact was being stated in up to three places at once:

- **run controls** in both the Action Center and a separate
  `RunAgentPanel`
- **how many designs are waiting** in the Action Center header *and* a
  live strip below it
- **what the agent does** in HowItWorks, in the phase row's role text,
  *and* in the agent legend

That is what "정보가 분산" actually meant, and no new panel could fix it.

So this round deleted rather than added:

- `RunAgentPanel` removed entirely (71 lines) — the Action Center already
  runs every design, and better, since it knows *which* design needs it.
- The live strip folded into the Action Center header, where the count it
  duplicated already lived.
- The wrapper panel holding a title, an intro paragraph and the design
  filter dropped; the filter moved beside the case list, which is what it
  actually filters.
- `HowItWorks` collapsed by default. At 587px it was larger than the
  Action Center that answers the actual question — an explanation sitting
  on top of the answer.
- Every case collapsed by default, with an explicit "the record — every
  real run, newest first" heading so the evidence is visibly a different
  thing from the actions above it.

Result, measured the same way:

| | before | after |
|---|---|---|
| preamble | 1,096px | **271px** |
| buttons | 27 | **18** |
| newest case | 3,763px | **41px row** |
| total | 7.1 screens | **1.3 screens** |

The page is now three layers in order: what needs you → what the agent
does (collapsed) → the record. Nothing was lost — verified live that the
Action Center still sorts `sram_wrapper` to the top as needing judgement,
that clicking through still opens exactly that case with its three-step
review workflow and all eight stage cards, and that both duplicate
surfaces are gone from the DOM.

## The stage counts were inverted — the pipeline looked broken while working

Looking at the phase row for a better visualisation turned up a real
defect in it, not a styling gap. Each evaluation gate read:

    04 Physical Constraint Evaluation   3/9 candidate run(s) reached this stage
    05 Routing Generation Evaluation    0/9 candidate run(s) reached this stage
    06 Routing Candidate Generation     0/9 candidate run(s) reached this stage
    07 Verification & PPA Evaluation    6/9 candidate run(s) reached this stage

Which reads as: almost nothing got through, and *nothing at all* reached
routing. The opposite is true. `classify_stage()` tags a candidate with
the stage its run **ended** at, so those numbers are candidates that
*stopped* there — "0 at routing" means nobody failed at routing, and "6
at verification" means six candidates went all the way through signoff.
The label said "reached" and inverted the meaning of every gate.

Fixed by showing the flow as what it actually is — a funnel with
cumulative attrition, since whoever dies at gate 04 never enters 05:

    04 Physical Constraint Evaluation   3 of 9 stopped here
    05 Routing Generation Evaluation    all 6 passed
    06 Routing Candidate Generation     all 6 passed
    07 Verification & PPA Evaluation    6 of 9 completed signoff

Each gate also carries a two-segment bar — survivors against losses,
scaled to the whole candidate set rather than self-normalised, so the
bars are comparable across gates and the funnel is visible narrowing
once at 04 and then holding. A bare fraction hides whether a loss was
one candidate or all of them; the bar does not.

Verified against two cases with different shapes, both matching their
real recorded verdicts: `counter4` (9 candidates → 3 stopped at physical
constraint → 6 completed signoff, bar 97px/48px ≈ 6:3) and
`counter4_tinydie` (4 → 3 stopped → 1 completed, 3 of its candidates
produced by auto-repair). The single-survivor case said "all 1 passed",
which reads badly, so it now says "the 1 survivor passed".

## sram_wrapper: the "physical floor" was a default buffer cell

This case had been open since 2026-08-21 and had accumulated four
verdicts, two of which contradicted each other. It left two questions:
whether the SRAM's 0.04 ns `max_transition` is meetable in this PDK at
all, and what to do about clock-pin slew. Both are answerable from
files already on disk — the liberty models and the tech LEF — without
running anything.

**The 0.04 ns spec is meetable.** Parsing every drive cell's
`rise_transition` table in `sky130_fd_sc_hd__tt_025C_1v80.lib`
(units confirmed: `time_unit "1ns"`, `capacitive_load_unit(1, pf)`),
driving the SRAM addr pin's own 0.00689 pF and nothing else:

    inv_16   19.3 ps        buf_12   25.8 ps
    inv_12   19.5 ps        buf_8    26.8 ps
    inv_8    21.1 ps        buf_16   29.4 ps

Against a 40 ps limit. The library floor is roughly half the spec, not
above it — so "physically floored" was wrong.

**Where 0.043 ns actually came from.** Evaluated at the 0.01 pF load
RSZ reported, `sky130_fd_sc_hd__buf_4` gives 42.3 ps — matching RSZ's
"best achievable transition time is 0.043ns" to within rounding. The
sky130 PDK sets `RE_BUFFER_CELL "sky130_fd_sc_hd__buf_4"`
(`libs.tech/openlane/sky130_fd_sc_hd/config.tcl:46`). RSZ was not
reporting physics; it was reporting the one repair buffer it had been
given. buf_8, buf_12 and buf_16 all clear 40 ps at that load.

This also refutes the earlier verdict that the fix is "not something a
generic config override can express" — `RE_BUFFER_CELL` is exactly
such an override.

**The 0.01 pF was never the net's load.** From the tech LEF, a met2
wire is 0.0779 fF/µm, so the measured 249 µm addr1[0] net carries
about 19.5 fF; with the 6.9 fF pin that is ~27 fF, not 10 fF. So the
figure RSZ printed was a driver-capability probe, not a measurement of
the placed net. That was the exact open question left by the earlier
.odb review, and it is now closed.

**Neither fix works alone.** Output transition at a degraded 0.2 ns
input slew, against the 40 ps limit:

    load:          6.9 fF (pin)   10 fF   27 fF (249 µm)
    buf_4              37.3 ✓      44.8    87.2
    buf_8              29.8 ✓      34.1 ✓  58.7
    buf_12             28.5 ✓      31.7 ✓  49.0
    buf_16             31.8 ✓      34.8 ✓  51.1

At the real placed load nothing meets spec; at the bare pin even the
default buf_4 does. Solving for the longest met2 wire each buffer can
drive within 40 ps: buf_4 14.5 µm, buf_8 92.1 µm, buf_12 144.5 µm,
buf_16 110.1 µm. The measured driver-to-pin distance is 249 µm. So a
stronger repair buffer *and* a driver inside ~145 µm are both needed —
the two proposals that had been argued as alternatives are
complementary. (buf_16 being worse than buf_12 is not a typo: its
larger input capacitance makes its own transition slower at light
load.)

**The clock-pin violations are a pre-CTS artifact.** The previous
review read step 30's STA, found `u_sram/clk1` at 1.599 ns against a
0.75 ns limit — the worst violator in the report — and concluded the
clock slew was "a distinct problem that was mistakenly ruled out."
Asking the tool for the flow's step order settles it:

    30  OpenROAD.STAMidPNR            <- the report that was read
    31  OpenROAD.RepairDesignPostGPL  <- where RSZ-0090 fires
    34  OpenROAD.CTS                  <- the clock tree is built here

The clock is one unbuffered net from the port to all 72 sequential
elements at measurement time. Three independent details agree: the
SRAM clk pins are only 0.00689 pF each yet show 1.6 ns, which is what
a clkbuf produces at the *top* of its characterised load range
(0.28–1.53 pF); ordinary flops (`_157_/CLK` and friends) violate in
the same report, so it is not macro-specific; and clk0/clk1 carry no
explicit `max_transition` at all, falling under the macro's
`default_max_transition : 0.5`. The original diagnosis was right to
relocate the cause to the addr buses; the correction of that
correction was itself wrong.

One real config finding fell out of this: `MAX_TRANSITION_CONSTRAINT:
0.75` in the design's config.json is *looser* than the macro's own
0.5 ns library default.

**The experiment that looked like evidence and wasn't.** buf_4 gives
42.3 ps at 0.01 pF, matching RSZ's 0.043 ns, and the PDK sets
`RE_BUFFER_CELL "sky130_fd_sc_hd__buf_4"` — so three candidates were
run with buf_4 / buf_8 / buf_12 expecting the number to move. All three
failed identically at 0.043 ns, which reads cleanly as "stronger
buffers don't help." They were duplicates. OpenLane had logged

    WARNING  An unknown key 'RE_BUFFER_CELL' was provided.

`RE_BUFFER_CELL` is an OpenLane 1 name; OpenLane 2 has no
repair-buffer-cell variable at all (checked against the image's own
step `config_vars`). The hypothesis is untested, not refuted.

This is the second time an ignored override produced a plausible wrong
conclusion — `STD_CELL_LIBRARY` as a config override was the first, and
it faked a 0.00% technology delta. So `run_stage` now fails any run
where OpenLane reports an unknown key that we passed
(`reject_ignored_overrides`). Only keys we passed are fatal; OpenLane
warns about unknown keys in config.json too, and failing on those would
block designs carrying deliberate extra entries. Verified both
directions against real OpenLane: the same `RE_BUFFER_CELL` run now
raises, and `FP_CORE_UTIL=45` still completes with `flow__errors__count
0`. A gate that cannot fail is not a gate; one that always fires gets
switched off.

**What this added to the pipeline.** `pipeline/lib_query.py` reads
liberty transition tables, pin capacitances and tech-LEF wire
capacitance, so a slew argument can be checked against the cell models
instead of argued from the one number a tool printed. The pipeline
could already query placement (`odb_query`), timing (`sta_report`) and
function (`equiv_check`); the cell library was the gap, and it is why
"physically floored" survived four reviews. `max_wire_um()` turns "keep
the driver adjacent to the macro" — which no placer can act on — into a
distance a floorplan can be checked against. Its tests pin the real
claim (some sky130 cell meets 40 ps at that pin's load) and the two
parsing traps: liberty units must be read rather than assumed, and a
naive scan of transition tables picks up degenerate tri-state groups
and reports a 0 ps floor that would make any limit look meetable.

sram_wrapper stays OPEN — no fix was found. What changed is that the
two questions it was parked on are answered, and "nothing to try" is
now measured false rather than disputed. The next thing to measure is
whether the binding constraint is the slew *arriving* at the repair
buffer rather than the buffer's size: buf_8/12/16 give 29–33 ps at
0.01 pF with a clean input but 41.5–43.9 ps with a 0.63 ns input, which
would put the lever upstream. Stated as the next measurement, not a
conclusion.

## Showing the rules a candidate was judged against

Stage 4 is called "Physical Constraint Evaluation". It showed which
candidates died there and the real OpenLane text for each — and never
showed what the constraints were. A reader could see `RSZ-0090` and the
verdict FAIL, with no way to find out what limit was missed, by how
much, or whether it was a limit anyone could change.

That gap is not only a UI one. Both conclusions this project has had to
overturn were cases of arguing about a limit instead of reading it: a
0.04 ns `max_transition` assumed unmeetable when the library floor is
19.3 ps, and a 0.01 pF load assumed to be a measurement of a net that
actually carries ~27 fF. The constraints were on disk both times.

`pipeline/design_rules.py` collects them, and the split it draws is the
substance of the feature:

- **Fixed by the process** — from the PDK's tech LEF: manufacturing
  grid, site geometry, and per-routing-layer direction, pitch, min
  width, min spacing, min area, max density. Nothing in a config can
  change these.
- **Chosen by us** — from config.json and run_spec.json: die area, clock
  period, transition limits, PPA targets, and any macro pinned at an
  absolute location. These are the levers a repair may propose moving.

A flat list would make those look alike, and they are nothing alike: a
violation of the first kind means the candidate is impossible, a
violation of the second means the agent has something to try. sram_wrapper
is the case in point — its SRAM is pinned at (110, 150) µm, which
constrains every net routed to it, and that was invisible in the console
for the entire time the case was open.

Two parsing details that would otherwise quietly produce fiction. Values
absent from the LEF are reported as `null` and render as `—`, never 0:
`li1` genuinely has no `PITCH`, and a 0 there reads as a real rule. And
spacing appears either as a plain `SPACING` or as the first entry of a
width-dependent `SPACINGTABLE`; taking the wrong row of met1's table
gives 0.28 µm instead of 0.14 µm.

It renders inside the stage-4 artifact rather than as a new top-level
panel. The console's measured problem has been dispersion — the fix that
worked last time was deleting blocks, not adding a tenth — so the rules
sit next to the failures they explain, after them, since the failures
answer "what happened" and the rules are the reference for it.

Collection is non-fatal by design: a case that already cost real OpenLane
time must not be lost because a tech LEF moved, so failure is recorded in
the case rather than swallowed. The UI distinguishes three states that a
single empty panel would conflate — rules present, collection failed
(with the reason), and a case written before constraints were recorded at
all. "Not captured" is not "there are none". All three were checked in
the browser against real cases.

**Showing which levers actually moved.** Listing a declared constraint is
weaker than it looks: counter4_tinydie declares `DIE_AREA` 8×8 µm and its
winning candidate ran at 64×64, which reads as a contradiction until you
can see that a repair moved it. So each setting is cross-referenced
against what the candidates really ran with, and renders as

    die area (um)   [0, 0, 8, 8] → [0, 0, 16, 16], [0, 0, 32, 32], [0, 0, 64, 64]

— the declared value struck through but kept, since it is still what the
design asks for, followed by the repair's actual sequence. Two mistakes
showed up only once this was on screen with real data. Array values
comma-joined ran together as sixteen numbers with no visible boundary
(`0, 0, 8, 8, 0, 0, 16, 16, …`), so they are bracketed. And a candidate
that passes the declared value back verbatim — tinydie's baseline
restates 8×8 — was being reported as a change, striking through a
constraint nothing had touched; values equal to the declared one are now
filtered out. Both were caught by looking at the rendered page rather
than the code.

The constraints were re-collected by actually re-running both designs
rather than backfilling the existing cases from today's files. A
backfill would have presented current config as if it had been recorded
at run time, which is the same class of error as the two conclusions
above.

## A signoff check that never ran was scoring as clean

`score()` gated on 23 signoff metrics, each one OpenLane marks
`critical=True`. The loop was:

    count = metrics.get(key)
    if count:
        violations.append(...)

An absent metric is `None`, `None` is falsy, so **a check that never ran
scored identically to a check that ran clean**. Only Magic's DRC had an
explicit "is it missing?" test; the other 22 did not.

This is reachable, not hypothetical. OpenLane 2 skips steps from the
flow CLI (`--skip`, `--to`), and this project has already done it
deliberately — `--skip OpenROAD.RepairAntennas` while chasing
sram_wrapper's hs-library path.

Demonstrated against a real run rather than argued. Stopping a real
counter4_tinydie flow at `OpenROAD.STAPostPNR` — one step before the
DRC/LVS/XOR block at 59–78 — produces 255 metrics with 11 of the 23
signoff checks absent:

    magic__drc_error__count            design__lvs_net_difference__count
    klayout__drc_error__count          design__lvs_property_fail__count
    design__lvs_error__count           design__lvs_unmatched_device__count
    design__xor_difference__count      design__lvs_unmatched_net__count
    magic__illegal_overlap__count      design__lvs_unmatched_pin__count
    design__lvs_device_difference__count

Both DRC signoffs and all seven LVS checks. The old code scored that run
**PASS** — not one manufacturing-correctness check had run, and the
verdict called the candidate clean.

Requiring presence is safe: a full signoff was audited first and emits
281 metrics including every one of the 23 at 0, so absence really does
mean the step did not run.

**Absence is tracked apart from a nonzero count.** The verdict gains an
`unverified` list beside `violations`, and `passed` requires both empty.
"Found 3 DRC errors" and "never checked DRC" both block a pass, but they
are different facts — the same distinction this pipeline draws between a
measured limit and an assumed one, and the reason the sram_wrapper case
stayed wrong for five days.

The console therefore has three verdicts, not two: PASS, FAIL, and
UNVERIFIED — the last in the warn colour, deliberately not red, because
it is not a design defect and colouring it as one sends the reader
hunting a bug that does not exist. The case summary follows: a card
reading "REJECTED 1 · 0 rejected · 1 never checked" contradicted itself,
so the label becomes "not passed" whenever anything is unverified.

Verified in both directions against the two real runs — the complete one
scores `passed=True` with nothing unverified, the truncated one
`passed=False` with 11 unverified and 0 violations — and in the browser
on both. Cases written before this field render exactly as before.

## An entire clock domain was passing signoff unanalysed

OpenLane's default SDC says what it does, in its own source
(`openlane/scripts/base.sdc`):

    } elseif { $port_count != "1" } {
        puts "\[WARNING] Multi-clock files are not currently supported by
              the base SDC file. Only the first clock will be constrained."
    }
    set ::clock_port [lindex $::env(CLOCK_PORT) 0]

A design declaring two clock ports gets exactly one `create_clock`.
Every path in the second domain is analysed by nobody, and STA then
reports zero setup and zero hold violations — truthfully, because it was
never asked. `score()` read those zeros and passed the candidate.

Proven rather than argued. `pipeline/designs/cdc_twoclock` is a
deliberate negative control: two independent clocks with an 8-bit path
crossing from the `clk_a` counter into a single `clk_b` flop, no
synchronizer — the textbook metastability bug. Run through the full
flow it produced 279 metrics, `timing__setup_vio__count 0`,
`timing__hold_vio__count 0`, and scored **PASS**. Its logs carry the
warning above and `[INFO] Using clock clk_a…`, nothing more.

`pipeline/cdc_check.py` compares what the design declared against what
the run's own logs say it constrained. `clk_b` is reported as
*unverified*, not as a violation — nothing found it broken, nothing
looked, and that is the same distinction the absent-signoff-metric fix
turned on.

The gate-level netlist corroborates it independently: 8 flops clocked by
`clk_a` and 8 by `clk_b`, two real domains in silicon while one was
constrained. Two different artifacts, same conclusion.

Deliberately narrow about what it claims. This is constraint coverage,
not structural CDC analysis — nothing here looks for two-flop
synchronizers, gray coding or metastability, and a quiet result must
never be read as "CDC clean". The check says so in its own output rather
than leaving that to a reader's assumption.

Verified both ways end-to-end through the orchestrator: `cdc_twoclock`
blocks with one unverified domain; `counter4_tinydie` (single clock)
passes with none.

## Power domains: per-rail IR drop, not one number

`score()` recorded `ir__drop__worst` — a single global figure — while
OpenLane emits `design_powergrid__drop__worst__net:<net>` for every
supply it analysed. That per-net breakdown *is* the power-domain view: a
healthy core rail beside a drooping macro domain looked identical to a
design where everything was fine.

`supply_rails()` now reads each rail, deriving nominal from the pair
(worst voltage + worst drop = 1.79991 + 0.0000902 = 1.8 V) rather than
assuming it. Two details it refuses to guess at: the
`drop__average__net:VPWR` key holds 1.79999 on a 1.8 V rail — a voltage,
not a drop — so it is ignored rather than gated on; and ground rails get
no percentage, because a percentage of a 0 V nominal is meaningless, so
the absolute bounce is reported instead.

Gated only when `run_spec` sets `max_ir_drop_pct`. How much droop is
acceptable is a design decision, and a threshold invented here would be
a fabricated spec wearing a measurement's clothes.

## Fmax and Vmin were one unread metric away

`score()` read `timing__setup__wns` — worst *negative* slack, which
OpenSTA clamps at 0. A design with 6.85 ns of margin and one with 0.01 ns
both reported exactly 0. The margin was in `timing__setup__ws`, which
nothing in this pipeline had ever read.

With it, per corner, on a counter constrained at 100 MHz:

    corner              V      setup ws   min period   Fmax
    max_ff_n40C_1v95    1.95   +7.145 ns   2.855 ns    350.2 MHz
    max_tt_025C_1v80    1.80   +6.846 ns   3.154 ns    317.0 MHz
    max_ss_100C_1v60    1.60   +6.073 ns   3.927 ns    254.7 MHz

Signoff Fmax is the worst corner, not the best — 255 MHz, on a part
constrained at 100. Vmin comes from the corner names themselves
(`_1v60`, `_1v80`, `_1v95`): the lowest voltage where setup *and* hold
both still pass. Hold is judged separately because a hold failure does
not improve by slowing the clock, so such a corner is unusable at any
frequency.

Both numbers carry their limits in the output. Fmax describes *this*
placed netlist's critical path; it does not predict what re-closing the
design at 3.15 ns would produce, since a tighter constraint changes
synthesis, sizing and CTS. And when every corner passes, Vmin is the
floor of what the PDK characterises, not a swept minimum — stating
"1.6 V" flatly would claim a sweep nobody ran, so the flag
`vmin_is_lowest_analysed` says which case it is.

## Seeing the circuit, not just the layout

Stage 1 is called "Circuit & Layout Extraction" and listed file paths
for both while showing neither. The layout had a rendered view early on;
the circuit it implements had none — even though Yosys writes
`<design>.nl.v.json` during synthesis and the pipeline recorded its
path, into `runs/`, which is deleted.

`pipeline/netlist_graph.py` extracts a directed gate-level graph and
stores it in the case (9.3 KB for a 42-cell design). The file is 470 KB
because Yosys emits a blackbox module for every cell in the standard-cell
library — and that half is what makes the graph possible: it states each
cell type's port directions. Direction is read from there, never guessed
from pin names. X/Y/Q are outputs on sky130 by convention, not by rule,
and a wrong guess silently reverses an edge, producing a schematic that
looks right and is not. An unknown cell type's pins default to inputs:
better a node with no driver than an invented connection.

`SchematicView` lays it out by logic depth, inputs left, outputs right,
with sequential cells terminating a path — which is what stops a
counter's feedback from recursing forever, and makes register-to-register
logic depth visible at a glance.

One bug worth recording because it was invisible in the code and obvious
in the output: net names came out as `$abc$272$auto$rtlil.cc:2739:
MuxGate$241` where `ctr_a[0]` belonged. Yosys keeps both the RTL name and
its own aliases for the same net, and the preference for the human one
was written as `if bit not in net_names` against a dict keyed by
`str(bit)` — an int compared to string keys, so the condition was always
true and last-write-won.

## What OpenLane offers that this pipeline does not use

Surveyed against the pinned image rather than from memory.

**`SynthesisExploration` flow.** Its own description: "tries multiple
synthesis strategies (in the form of different scripts for the ABC
utility) to try and find which strategy is better by either minimizing
area or maximizing slack (and thus frequency)." Run on counter4 it
produced this in **8 seconds**:

    SYNTH_STRATEGY   Gates   Area (µm²)   Worst Setup Slack (ns)
    AREA 0           14      171.41       6.570
    AREA 3           24      255.24       6.483
    DELAY 3          16      195.19       6.676
    DELAY 4          19      242.73       6.154

This pipeline explores `SYNTH_STRATEGY` by running a *full* OpenLane
flow per candidate — 60–100 s each, so counter4's nine candidates cost
roughly nine minutes for a table this flow builds in eight seconds. It
is also exactly an area-versus-Fmax tradeoff, the pairing the operating
point work above is about.

**Steps present but unused.** `KLayout.Render` renders a layout image —
`render_layout.py` was written by hand to do that. `OpenROAD.BasicMacroPlacement`
places macros automatically, while `sram_wrapper` pins its SRAM at a
hand-chosen (110, 150) µm; given that the measured driver-to-pin distance
of 249 µm is the open problem in that case, letting the tool place it is
worth trying. `Yosys.Resynthesis`, `Odb.FuzzyDiodePlacement` and
`Odb.PortDiodePlacement` (antenna strategies) are also unused.

**Already-generated artifacts thrown away.** Synthesis writes
`hierarchy.dot` and `primitive_techmap.dot` next to the JSON netlist;
`Yosys.EQY` (step 73) is a formal equivalence check inside the Classic
flow, while this project wrote its own `equiv_check.py`.

None of these are broken as they stand — but each is work the toolchain
already does, and the netlist view above is what happens when one of
them gets picked up.

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
