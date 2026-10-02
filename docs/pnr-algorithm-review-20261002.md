# Place-and-route decision layer review — 2026-10-02

This repository has no placer or router. RePlAce, TritonCTS, FastRoute and
TritonRoute live inside OpenROAD in the OpenLane 2.3.10 image. What the
repository owns is the layer around them: which configuration to try, what to
do when a run dies, and which passing run to call the winner. This review
audited that layer against the 484 candidates in `reference-db/`, read the
OpenROAD / OpenLane / AutoTuner sources and papers for what is known, and
applied only what a real run backed.

Everything below marked *measured* came from real OpenLane runs on the WSL
runner; the raw rows are in `reference-db/pnr_study/` and
`reference-db/pnr_repair_checks.json`.

## 1. What the audit found

| Finding | Evidence |
|---|---|
| Four repair patterns, none for a placement error | 484 candidates, 159 passed. 31 died with GPL/DPL/RSZ errors no pattern matched. |
| The error text often states the repair | `GPL-0302` prints "Suggested target density: 0.42". `GPL-0307` says "smaller max_phi_cof". `GPL-0301` prints the utilization. |
| Placement and routing knobs were never explored | Overrides in the store: `SYNTH_STRATEGY` 232, `CLOCK_PERIOD` 144, `FP_CORE_UTIL` 140. `PL_TARGET_DENSITY_PCT` 4, CTS 2, `GRT_ADJUSTMENT` 0. |
| The timing-margin objective never discriminated | `-worst_setup_wns` is 0 for all 159 passing candidates: OpenSTA clips WNS at zero. |
| Nothing ranked the floorplan | `counter4` cell area is 290.278 um2 at utilization 25 and 35. The most-swept placement knob could not change the winner. |
| Ties in the Pareto front were decided by list order | Crowding distance gives every extreme infinite distance. 10 of 23 multi-passer iterations had a front larger than one. |
| A stale OpenLane 1 name sat in an agent doc | `placement-strategist` told agents to vary `PL_TARGET_DENSITY`; 2.3.10 only has `PL_TARGET_DENSITY_PCT`. |

## 2. What was applied

**Repair from the tool's own words** (`pipeline/pnr_repair.py`, wired into
`propose_repairs()` as a fall-through after the four proven patterns).
Replayed through the live loop by `pnr_repair_check.py`:

| Code | Rule | Replay |
|---|---|---|
| GPL-0302 | `PL_TARGET_DENSITY_PCT` = suggested + 2 points | counter4: 40 -> 44, passes |
| GPL-0307 | `PL_MAX_PHI_COEFFICIENT` 1.05 -> 1.01 | counter4_tinydie gf180: 1.03 still diverged, 1.01 converged |
| GPL-0301 | grow a fixed die by sqrt(reported util / 50%) | cdc_twoclock gf180: 122% -> past the error (104 um die) |
| DPL-0036 | `FP_CORE_UTIL` -15 | counter4 gf180: repaired; the existing utilization pattern finished it |
| RSZ-0060 (hold) | `PL_RESIZER_HOLD_SLACK_MARGIN` -> 0 before touching the cap | spm hs: passes at the original utilization |
| PDN-0185 | utilization step from the design's default | unit-tested; same step as the proven pattern |

The RSZ-0060 rule is the one a guess would have got wrong. Raising the buffer
cap, or lowering utilization, both complete, but at 6520 um2 with 221 hold
buffers. Margin 0 is 5296 um2 with 32, and 28% less core.

**Selection** (`pareto.pick_knee`, `orchestrator.pareto_points`). Objectives
are now cell area, power, core area and real setup slack, each added only when
every passing candidate has it. The winner is the knee of the front. Replayed
over the store, 3 of 23 multi-passer iterations choose differently, two of
them a strictly smaller core at equal cell area.

**Polish** (`pnr_polish.py`, opt-in with `"polish": true` or `--polish`). After
a pass, try measured moves, keep one only if it passes signoff, is no worse
than the flow's own movement on any objective and is at least 3% better on
cell area, power or core. Accepted moves are then combined in one more run.
*Measured end to end on gcd:* 3004 -> 2734 (hold margin 0) / 2873 (input
buffers off) -> **2603 um2 combined, -13.4%**, all signoff checks passing.

**Observation** (`live_view.py`, `live_events.py`). See section 5.

## 3. Which knobs matter (measured)

`pnr_study.py` moved each knob alone on gcd and spm (sky130hd), next to
probes that should not matter (`FP_CORE_UTIL` +-1, `CLOCK_PERIOD` +-1%).
OpenROAD is deterministic but chaotic: the probes moved wirelength 3%, power
3%, area up to 1.4% and first-pass DRC by 37-160%. Only a change beyond both
that spread and a 3% floor counts.

| Knob | gcd | spm | Verdict |
|---|---|---|---|
| `PL_RESIZER_HOLD_SLACK_MARGIN` 0.1 -> 0 | area -9.0%, power -3.2%, buffers -28% | area -8.7%, power -7.5%, buffers -38% | **consistent**; costs hold slack (0.114 -> 0.053 ns), signoff still gates it |
| `DESIGN_REPAIR_BUFFER_INPUT_PORTS` off | area -4.4%, buffers -37% | area -4.6%, buffers -40% | **consistent**, but a design-level assumption: selected by name, never default |
| CTS clustering size / diameter | byte-identical | byte-identical | inert: 35 sinks never reach the 25-sink cluster |
| `PL_ROUTABILITY_DRIVEN` off | identical | identical | inert: no congestion to relieve |
| `PL_RESIZER_SETUP_SLACK_MARGIN`, `DESIGN_REPAIR_MAX_SLEW_PCT` | identical | identical | inert: nothing to repair |
| `GRT_ADJUSTMENT` 0.1 / 0.5 | <0.6% | <0.6% | inert: sky130 sets per-layer adjustments that override it |
| `PL_TARGET_DENSITY_PCT`, `PL_WIRE_LENGTH_COEF`, `GPL_CELL_PADDING`, aspect ratio | within noise; several draw a single max-fanout violation | same | not a result |
| Output-port buffering off | area +9.7% | +0.4% | wrong direction |

A robustness finding sits underneath: 7 of 30 gcd perturbations, including
the harmless probe `FP_CORE_UTIL=37`, failed on exactly one max-fanout
violation. gcd's baseline pass is a knife edge, not a margin.

## 4. Where the rules stop

- `gcd` on sky130 hs at 4 ns: margin 0 is not enough, setup is unclosable
  (WNS -0.29 ns), the rule declines and the loop escalates. Kept as a negative
  control in `pnr_repair_check.py`.
- GRT congestion (GRT-0116/0118/0119), antenna repair, and aes/riscv32i DRV
  counts have no rule: none has a small-design failure to replay, and the 25
  minute runs were not repeated. The research notes `GRT_ADJUSTMENT` bisection
  and `GRT_ANTENNA_MARGIN`/`GRT_ANTENNA_ITERS` as the knobs to try first.
- Setup-side RSZ-0060 (RSZ-0062 beforehand) was never observed; not promoted.
- Two designs on one library is a lead for hold margin, not a law. `polish`
  re-measures on every design it touches.

## 5. Watching a run

```
wsl -d Ubuntu -- python3 /home/kos2001/claude_work/ppa-pnr/pipeline/live_view.py -f
```

Per run: step and progress, then the algorithm's own state from its log:
global-placement overflow and HPWL sparklines, the resizer's last table row,
CTS sinks and buffers, global-routing congestion, detailed-routing violations
per iteration (73 -> 28 -> 41 -> 0), and a cell-density map redrawn from the
DEF each placement step writes. Below that, the orchestrator's decisions, in
order: failure code, repair and the override it changed, polish trial and
whether it was adopted. Events go to `<design>/.events.jsonl`. They first went
under `runs/`, where nothing was written: the container creates that directory
as root.

## 6. Reproduce

```
python3 pipeline/pnr_study.py --design pipeline/designs/gcd --out reference-db/pnr_study/gcd.json
python3 pipeline/pnr_study.py --summarise reference-db/pnr_study/{gcd,spm}.json
python3 pipeline/pnr_repair_check.py --out reference-db/pnr_repair_checks.json
python3 pipeline/orchestrator.py --design pipeline/designs/gcd --run-spec <spec> --polish
python3 -m unittest discover -s tests
```

Sources read: OpenLane 2.3.10 (`steps/openroad.py`, `common_variables.py`),
OpenROAD gpl/dpl/rsz/grt/drt READMEs and source, ORFS `variables.yaml` and
AutoTuner, the ICCAD'21 AutoTuner paper, RePlAce (TCAD 2019), RUDY (DATE'07).
Folklore the research could not confirm in code was not used.
