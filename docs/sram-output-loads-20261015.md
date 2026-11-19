# SRAM output-load coverage, 2026-10-15

The report-only replay completed all nine retained final STA corners. Each
corner provides 256 unique observations: 64 SRAM outputs × min/max analysis
× rising/falling output transition. All 2,304 observations are present in pF.
Actual Liberty lookup loads span **0.006764022–0.014844512 pF**
(6.764022–14.844512 fF). Every lookup is within its printed interpolation
bracket. The loaded macro's full load axis is 0.0017225, 0.00689, 0.02756 pF.

This closes the missing output-load observation coverage for the retained
design. It does not qualify the SRAM model: all nine corners still load the
same TT relaxed Liberty, including six SS/FF corners. Full measured PVT/arc
tables and successful port0/port1 measurement evidence remain unresolved.
The prior 300-second SPICE attempt remains timeout_unverified; no new SPICE
or physical implementation was run for this report.

## Replay fidelity

The source run is `srio_r15_clock_fanout16_revalidate_20261015/runs/io`,
stage `54-openroad-stapostpnr`. Original log paths select the exact Liberty,
blackbox, fill-insertion netlist, and extracted nominal/minimum/maximum SPEFs.
Original logs show the image's `base.sdc` was loaded, rather than the saved
state's SDC; the replay follows that recorded behavior. Each corner's 57
input pins reproduce all four original slew values within 0.000001 ns.

The design and PDK are mounted read-only. Execution uses the recorded Docker
image ID of OpenLane 2.3.10 and an archived Tcl script, with a 60-second
timeout per corner and bounded owned-container cleanup. All nine invocations
returned zero and passed completion and coverage checks. The preceding
single-corner pilot is retained separately.

## Measurement interpretation

`report_dcalc -to <output> -min/-max -digits 9` enumerates both output
transitions. The audit retains the actual delay/slew table load, Ceff, and
reduced pi-network C1+C2 separately. It also preserves the net API's
rise/fall-collapsed capacitances in `net_loads.csv`; those are not substituted
for edge-aware measurements. Printed interpolation brackets are not presented
as the full table extent. All delay/slew load pairs agree.

Each log explicitly declares capacitance units of 1 pF. The 34 unannotated
drivers are four clock-load cells and 30 tie cells; no SRAM output is listed.
Zero partially unannotated drivers are reported. This statement is limited
to SRAM output annotation, not a claim that the entire design is annotated.

## Evidence and validation

Evidence is in
`pipeline/designs/sram_wrapper/experiments/evidence-20261015-output-loads`.
It retains per-corner logs, input slews, net loads, parsed output observations,
source snapshots, image identity, and dependency/output SHA256 hashes.
`summary.json` and `handoff.json` distinguish completed output-load coverage
from unverified macro-model qualification. The handoff is a scoped SRAM
evidence update, not a full-design signoff verdict.

Seven focused tests cover missing/duplicate output evidence, nonfinite loads,
unit and annotation failures, missing completion markers, and input-slew
drift. Independent agents reviewed parser interpretation and replay fidelity.

Reproduce from Linux with a new output directory:

```sh
python3 pipeline/export_macro_loads.py \
  --design pipeline/designs/srio_r15_clock_fanout16_revalidate_20261015 \
  --stage pipeline/designs/srio_r15_clock_fanout16_revalidate_20261015/runs/io/54-openroad-stapostpnr \
  --out /path/to/new-output-load-evidence --timeout 60
```
