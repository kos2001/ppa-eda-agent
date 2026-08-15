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

- **`report_timing` differs from PrimeTime's default column format.**
  PrimeTime's default is `Point / Incr / Path` (what `report-timing.md`
  documents). OpenSTA's default is `Delay / Time / Description` — same
  underlying concept (per-point incremental delay + running total), same
  `Startpoint:`/`Endpoint:`/`Path Group:`/`slack (MET/VIOLATED)` structure,
  different column headers and spacing. Worth adding as a documented
  variant rather than treating PrimeTime's format as the only one an agent
  will ever see.
- **`report_power -format json` is structurally different from
  PrimePower's text report.** Instead of PrimePower's internal/switching/
  leakage breakdown of a single total, OpenSTA's JSON breaks power down by
  *component category* first (`Sequential`, `Combinational`, `Clock`,
  `Macro`, `Pad`), each with its own internal/switching/leakage/total. Same
  underlying physics, different axis of aggregation — useful to recognize
  either shape rather than assuming PrimePower's flat breakdown is the only
  one.
- No `report_area` equivalent was found in OpenSTA's test suite — OpenSTA
  is a timing/power tool, not a synthesis tool, so it doesn't produce a
  Design-Compiler-style area report. Don't assume one exists there.

Since it's runnable for free (no Synopsys license), this is the most
practical path to Phase 2: run `create_clock`/`report_timing`/`report_power`
against one of OpenSTA's own bundled test designs (`test/nangate45/`,
`test/asap7/`) to get genuine report text to validate the parsers against,
rather than waiting on a real customer/project report.

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
