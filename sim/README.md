# Live simulation design

`example1.v` and `nangate45_typ.lib.gz` are copied verbatim from
[The-OpenROAD-Project/OpenSTA](https://github.com/The-OpenROAD-Project/OpenSTA)
`examples/` directory (GPLv3), 2026-08-16. `example1.v` is a small 5-cell
design (3 flops, a buffer, an AND gate) — small enough to simulate in
under a second, real enough to show genuine timing/power behavior when the
clock period changes.

`run.tcl.template` is filled in by `server/index.mjs` with a user-chosen
clock period and run against these files via the `openroad/opensta`
Docker image (see `server/README.md`).

Not redistributed as a product — used locally to drive this dashboard's
"Simulate" tab. Only timing and power come out of this (OpenSTA has no
`report_area` — see `references/see-also.md`).
