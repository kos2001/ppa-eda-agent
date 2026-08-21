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
second, unrelated problem — the hs library isn't fully wired into this
OpenLane setup's antenna-repair machinery, crashing OpenROAD. Reverted
rather than chase a second gap in the same pass; full write-up in the
reference-db case's `diagnosis` field. This is a genuinely different
failure mode from `counter4`'s PDN/utilization one —
`orchestrator.py`'s `propose_repairs()` correctly does *not* auto-repair
it (out of its narrow, evidence-based scope) and iteration stops for a
human/`feedback-optimizer` to pick up. The real fix is placement-side:
keep whatever drives the address/mask buses physically adjacent to the
macro (or insert an explicit macro-local repeater) so the driver only
needs to charge the bus's bare pin capacitance — a placement-strategist
concern (macro-adjacent logic placement), not a config knob. Left open
for a future session rather than forced past.

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
