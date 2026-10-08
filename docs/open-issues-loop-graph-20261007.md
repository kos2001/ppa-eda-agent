# Open issues and how to close them: loop and graph approaches (2026-10-07)

Status of what is measured and what is not. Nothing here is a new physical
result; every number comes from archived runs in
`pipeline/designs/aes/experiments/evidence-20261005/`.

## Open issue: aes has no signoff-clean candidate

Best candidate `hul` has fanout, slew, capacitance, setup, hold, DRC and LVS at
0, but 6 antenna violations, +8.2% area, and runs at diagnostic constraints
(12 ns clock, I/O delay 25), so it is not a PASS.

## Graph approach (done on archived data)

`evidence-20261005/diode_clusters.py` builds the net -> sink graph of a final
netlist and joins it with the first antenna check.

| Observation | c0 | hul | orig |
|---|---|---|---|
| nets with 8+ diodes | 36 | 39 | 11 |
| of those, with exactly one real sink | 35 | 37 | 10 |
| r(first-check P/R, final diodes) | 0.17 | 0.17 | 0.38 |

What it rules out: "one diode per sink pin" (the nets have one sink) and
"diode count follows violation size". What it weakens: "`GRT_ANTENNA_ITERS` 10
plus one explains the 11-12 cluster", because `orig` ran the same 10 and has no
cluster. What it does not establish: the mechanism that makes a barely
violating single-sink pin accumulate 11-12 diodes.

Next graph step, still on archived data: add the placement/route geometry of
those 35 nets (wire length per layer from the DEF) to see whether the cluster
is the nets whose route goes through a layer the diode does not protect.

## Loop approach (run 2026-10-07)

`pipeline/designs/aes/run_spec_iter10.json` ran the `hul` recipe at
`GRT_ANTENNA_ITERS` 5 and 3. Decision rule written before running: if the
cluster stays at 11-12, drop ITERS as a lever.

Result: the option reached the repair steps (5 and 3), and both final netlists
are byte-identical to the ITERS 10 run (same sha256, 6 antenna, 1116 diodes,
145844 um2). The rule applies: ITERS is dropped as a lever, and "ITERS plus one"
is withdrawn. Evidence: `experiments/evidence-20261007/summary.json`.

Next loop step, one axis, not yet run: `GRT_ANTENNA_MARGIN` (50 in every
coupling run) on the `hul` recipe, then the geometry graph step above for the
35 single-sink nets.

## Performance and structure changes in this branch

- store-wide tests read cases through `case_store.load_light`: suite 84 s -> 41 s;
- surrogate leave-one-out ranks each fold once: `scan_all` 6.6 s -> 0.7 s,
  `best_k` about 7x faster, results byte-identical on the committed store;
- `PipelineTab.tsx` split into panels and a pure model module (1644 -> 227
  lines) with row/case memoization.

## Update 2026-10-09: GRT_ANTENNA_MARGIN

`run_spec_iter11.json` ran the `hul` recipe at `GRT_ANTENNA_MARGIN` 25 and 10
(was 50). Diodes 1116 -> 415 -> 236, nets with 8+ diodes 39 -> 6 -> 1, area
-1.2% / -1.5%, but antenna violations 6 -> 10 -> 11, and margin 10 brings back
2 slew and 1 capacitance violation. Neither is a PASS. MARGIN, unlike ITERS, is a
lever on the cluster; it trades diode count against residual antenna pins.
Evidence: `experiments/evidence-20261009/summary.json`. Next, one axis: a margin
between 25 and 50, and the geometry step above for the remaining residual pins.
