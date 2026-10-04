# Schematic circuit review

The Schematic page opens a small circuit rather than analysis text. Its central
sheet uses xschem's exported symbols, wires, pin names and parameters. The
inverter and NAND sources now draw VDD above VSS, PMOS pull-up above NMOS
pull-down, an explicit output connection, bulk labels and the NAND intermediate
series net. Existing symbols, port order and sizing expressions are retained.

Click a device or net label to inspect it. The side panel shows symbol/model,
explicit and default W/L/nf/multiplicity, source attributes, ordered pin/net
mapping, and connected terminals. SKY130 standard-cell symbol defaults also
expose VPWR/VGND/VPB/VNB bindings. A child sheet can be opened and its parent
restored. A SKY130 HD gate can open its transistor schematic through the existing
PDK CDL importer; unsupported devices still fail explicitly. Large full-chip
sheets retain the existing guard and bounded cone extraction workflow.

The viewer supports pointer-centered zoom, dragging, fit, selection location,
full screen with the inspector, paper/dark backgrounds, English/Korean controls,
and mobile layouts. Keyboard zoom/fit operates only when the canvas is focused.
Source and review JSON exports carry source text/hash and symbol provenance.
Source lines identify the selected instance; native SVG is independently
available. Labels and connected device names are highlighted, not inferred
timing paths or electrical operating points.

## Read-only inspection contract

`pipeline/schematic_inspect.py` reads approved `.sch` files and local/project or
SKY130 PDK `.sym` interfaces. It does not evaluate Tcl or invoke EDA. Built-in
pin facts and default templates are pinned to xschem 3.4.4 with individual
upstream URLs and hashes in `pipeline/schematic_symbols.json`; no symbol drawing
code is copied. PDK importers' extensionless symbol references are supported.

Connectivity follows actual pin-box centers, mirror/rotation, wire endpoints,
pins on orthogonal wire segments and same-name labels. Bare crossing wires do
not join. Conflicting labels, unmatched pins, missing interfaces, unexpanded bus
ranges and diagonal-wire limitations are review notes. A no-connect marker
suppresses an unattached-pin note. Anonymous preview nets carry an explicit
`@unnamed:` identifier rather than borrowing native net names. Symbolic W/L
values remain symbolic until the testbench binds them.

These are geometry previews, **not xschem ERC, a SPICE netlist, LVS, functional
verification, timing or model qualification**. Zero review notes only means the
supported checks did not flag a geometry issue. Pin directions are library
metadata; they do not establish electrical correctness. Source mtime/hash and
symbol hashes are included. Directory traversal and symbol symlink escapes are
rejected. Inspection is bounded to 8,000 elements; larger sources should use a
cone.

SVG is inserted as an inert allowlisted drawing: active elements, event
attributes, external references and unrelated stylesheet selectors are removed.
Missing or invalid drawings are shown as errors. SVG layer colors can change in
paper mode; geometry and exported text remain native.

## Validation

- Native xschem 3.4.4 netlisting of original and re-drafted inverter/NAND sheets:
  all normalized device statements, ordered terminals and parameters identical
  (2 inverter and 4 NAND devices); both native netlist return codes 0. Native SVGs
  regenerated. Temporary comparisons live outside the repository. No SPICE
  simulation or physical evaluation was needed for these layout edits.
- Unit coverage: mirror/rotation, multiline attributes, label joins, ground,
  crossings versus T junctions, conflicting names, missing interfaces, open and
  intentional no-connect pins, scalar bit labels versus bus ranges, hierarchy,
  traversal/symlink confinement and independently expected CMOS/NAND topology.
- HTTP source inspection and invalid-ID regression, frontend build/lint and the
  repository regression suite.
- Browser verification: real SVG selection, pin/net inspection, source/export,
  full screen, hierarchy and CDL drilldown, implicit supply attributes, zoom,
  scoped shortcuts, missing drawings, inert SVG handling, mobile, Korean and
  light/dark presentation. Viewing does not submit netlist/simulation or ASIC
  flow requests.

Reference behavior follows the official xschem
[file-format and command documentation](https://xschem.sourceforge.io/stefan/xschem_man/developer_info.html),
[symbol property syntax](https://xschem.sourceforge.io/stefan/xschem_man/symbol_property_syntax.html)
and [hierarchy tutorial](https://xschem.sourceforge.io/stefan/xschem_man/creating_schematic.html).
