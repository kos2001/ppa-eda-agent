# External references worth pulling in

Found and spot-checked 2026-08-15 while looking for real (non-fabricated)
sample reports to close the Phase 1→Phase 2 gap noted in
`docs/superpowers/specs/2026-08-15-ppa-eda-analyst-design.md`.

## OpenSTA — free, command-compatible STA tool with real sample reports

<https://github.com/The-OpenROAD-Project/OpenSTA>

Open-source static timing analyzer from the OpenROAD project. Its TCL
command surface (`report_timing`, `report_power`, `create_clock`, etc.) is
deliberately modeled on Synopsys PrimeTime, and its `test/` directory ships
dozens of real `.ok` golden-output files — actual tool-generated report
text, not synthetic. Confirmed by inspection:

**`report_timing` has at least three real column variants**, all pulled
from actual `.ok` golden files in `test/` and `sdf/test/` (not synthetic —
verified by cloning and reading them directly, 2026-08-15):

1. `Delay / Time / Description` — the plain default
   (`test/mcmm3.ok`, `test/prima_singular.ok`). Same underlying concept as
   PrimeTime's `Point / Incr / Path` (per-point incremental delay + running
   total), different header/spacing.
2. `Slew / Delay / Time / Description` — adds transition-time reporting
   per point (`test/prima3.ok`).
3. `Cap / Slew / Delay / Time / Description / Src Attr` — the richest
   variant, adds load capacitance *and* a source-line attribution column
   pointing back into the Verilog (`test/report_checks_src_attr.ok`, run
   against the **SkyWater sky130** open PDK, cell names like
   `sky130_fd_sc_hd__dfrtp_1`):
   ```
       Cap    Slew   Delay    Time   Description                          Src Attr
   ---------------------------------------------------------------------------------------------------------------
              0.00    0.00    0.00   clock clk (rise edge)
              0.00    0.00    0.00 ^ _1415_/CLK (sky130_fd_sc_hd__dfrtp_1) synthesis/tests/counter.v:22.3-28.6
      0.00    0.04    0.33    0.33 v _1415_/Q (sky130_fd_sc_hd__dfrtp_1)  synthesis/tests/counter.v:22.3-28.6
   ```
   (excerpt, GPLv3-licensed source, quoted for format reference)

All three keep the same `Startpoint:`/`Endpoint:`/`Path Group:`/`Path
Type:` framing `report-timing.md` documents — **only the per-point column
set inside the path body changes.** Don't treat an unfamiliar column
header as "not a timing report."

**Bug found and fixed by testing against these real files, 2026-08-15:**
`dashboard/src/parsers/parseTiming.ts` originally assumed the slack number
always comes *after* `(MET)`/`(VIOLATED)` (Synopsys PrimeTime style:
`slack (MET)   0.77`). Real OpenSTA output puts the number *before* it
instead (`test/prima3.ok`: `228.48   slack (MET)`;
`test/report_checks_src_attr.ok`'s sky130 example:
`9.55   slack (MET)`) — consistent with the rest of that format's
`number ... description` column order. The parser silently failed on both
real files (`ok: false`) until fixed to check both orders. This was only
caught by testing against real, unmodified files pulled from the repos
above — the synthetic example in `report-timing.md` never would have
surfaced it, since it was itself written number-after. **Lesson: any
future format documented here should be spot-tested against a real file
where possible, not just written from written-out knowledge of the
format.**

**Multi-corner/multi-mode adds two more header fields.**
`test/mcmm3.ok` shows `Mode: mode1` and `Corner: scene1` lines appearing
right after `Path Type: max`, for designs analyzed across multiple PVT
corners/operating modes simultaneously — not mentioned at all in
`report-timing.md` currently, worth adding if a pasted report has extra
header lines between `Path Type:` and the column header.

**Slack can show `(VIOLATED)` at exactly `0.00`, not just negative.**
`sdf/test/sdf_advanced.ok` has a path reporting `0.00   slack (VIOLATED)`
— the tool's own status label doesn't strictly require a negative *printed*
number (the underlying unrounded value is presumably ≤0). This validates
`parseTiming.ts`'s design choice of trusting the reported `MET`/`VIOLATED`
label rather than re-deriving violation status from the slack number's
sign — don't "fix" that to `slack < 0 ? violated : met`, the tool doesn't
always agree with that simplification at the boundary.

**`report_power -format json` is structurally different from
PrimePower's text report.** Instead of PrimePower's internal/switching/
leakage breakdown of a single total, OpenSTA's JSON breaks power down by
*component category* first (`Sequential`, `Combinational`, `Clock`,
`Macro`, `Pad`), each with its own internal/switching/leakage/total
(`test/power_json.ok`). Same underlying physics, different axis of
aggregation.

**No `report_area` equivalent exists in OpenSTA's test suite** — it's a
timing/power tool, not a synthesis tool, so it doesn't produce a
Design-Compiler-style area report. Don't assume one exists there.

## Yosys `stat` — a real, structurally different area report

<https://github.com/YosysHQ/yosys>

Yosys (open-source RTL synthesis) has no `report_area` command at all —
its equivalent is `stat -liberty <lib>`, and its output shape is *not* a
Design-Compiler-style combinational/noncombinational/macro split.
Per [YosysHQ's own docs](https://yosyshq.readthedocs.io/projects/yosys/en/0.35/cmd/stat.html)
(official documentation, not a raw test fixture — flag this one step down
in confidence from the OpenSTA excerpts above, which came from actual
golden-output files):

```
=== counter ===
Number of cells: 32
AND2_X1     6
DFF_X1      8
INV_X1     12
NAND2_X1    4
OR2_X1      2
Chip area for module '\counter': 89.456
```

A per-cell-type histogram plus a single "Chip area" total — no
combinational/noncombinational/macro breakdown, no buf/inv subset. If
`ppa-eda-analyst` is ever asked to read a Yosys `stat` dump, treat it as a
genuinely different report shape, not a malformed DC `report_area`.

## Phase 2 status

Both OpenSTA and Yosys are runnable for free (no Synopsys license) —
running `create_clock`/`report_timing`/`report_power` against OpenSTA's own
bundled test designs (`test/nangate45/`, `test/asap7/`), or `synth` +
`stat -liberty` in Yosys against an open PDK, would produce more genuine
report text on demand rather than relying only on the `.ok` golden files
already in the repos. Not done this session — the excerpts above came from
reading existing checked-in output, not from actually invoking either
tool. Actually running them is the natural next step if broader coverage
(more designs, more corner cases) is needed.

## Ground-truth PPA datasets from academic tooling

Found via search, not yet inspected directly — flagging as leads, not
verified claims:

- **MasterRTL** — <https://github.com/hkust-zhiyao/MasterRTL> — a
  pre-synthesis PPA estimation framework; its training data reportedly
  includes real PrimeTime-generated report examples used as ground truth.
- **LLMVeriPPA** — <https://github.com/kiranthorat3/LLMVeriPPA> — uses
  Synopsys Design Compiler with the open ASAP7 PDK to generate PPA reports
  for evaluating LLM-generated Verilog.

If real Synopsys-format samples are needed and OpenSTA's PrimeTime-style
output isn't close enough, these are worth cloning and checking for actual
`report_area`/`report_power` text output before trusting anything in them
as format ground truth — same "read it before citing it" standard applied
to OpenSTA above.
