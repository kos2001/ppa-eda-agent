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

## Loop approach (needs Docker, not run)

`pipeline/designs/aes/run_spec_iter10.json` is the one-axis loop step: the
`hul` recipe at `GRT_ANTENNA_ITERS` 5 and 3. It validates with
`--validate-only`. Decision rule written before running:

- cluster moves with ITERS -> ITERS is a lever; compare final antenna count and
  area against `hul`;
- cluster stays at 11-12 -> drop ITERS as a lever and take the graph step above.

Loop guard worth adding to the orchestrator only if the experiment shows
diodes repeating without reducing violations: stop a repair round when a
net's diode count grows while its pin's P/R does not fall. The per-pass
`antenna_summary.rpt` files already carry the data for that check.

## Performance and structure changes in this branch

- store-wide tests read cases through `case_store.load_light`: suite 84 s -> 41 s;
- surrogate leave-one-out ranks each fold once: `scan_all` 6.6 s -> 0.7 s,
  `best_k` about 7x faster, results byte-identical on the committed store;
- `PipelineTab.tsx` split into panels and a pure model module (1644 -> 227
  lines) with row/case memoization.
