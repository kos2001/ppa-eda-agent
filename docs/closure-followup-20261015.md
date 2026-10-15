# Closure follow-up (2026-10-15)

This follow-up distinguishes tool/test failures, design violations and missing
qualification. A historical PASS is not a current PASS. Diagnostic timing
constraints do not qualify the original target.

## Corrections

- Graph loop returns `historical_pass_requires_revalidation`, not a current
  winner, when only a historical passing candidate is available. Revalidate an
  explicit candidate with orchestrator; RTL, technology, toolchain and targets
  have not been matched by this historical shortcut.
- `MacroSignoff` now composes the existing full input-edge STA wrapper with its
  maximum-pin-slew report. The wrapper sources the upstream script and has no
  inline corner anchor; the old patch therefore rejected it.
- Clean pin maxima never certify the macro model. Per-arc input ranges, PVT,
  output loads and measured provenance remain separate qualifications. NaN,
  infinity, negative slew, duplicate pins and inconsistent corner pin sets do
  not establish complete measurement coverage.
- Windows drive paths are interpreted before host-specific path resolution,
  including when the test runs under Linux. An allowed external PDK symlink
  retains absolute schematic-symbol provenance instead of crashing on
  `relative_to`.
- The ledger regression checks that historical AES setup improvement remains
  visible, not that every later candidate is also setup-clean. Later measured
  failures remain in the ledger.
- Re-measured surrogate defaults on the pre-run store: power k=1 wins 352/366
  folds versus k=3 at 351/366. MAE is worse (0.00150494 vs 0.00142960 W), but the
  existing selection rule prioritizes win rate. This tiny margin is not a
  confidence or signoff claim. Area and completion remain k=1.

## Bounded RISC-V experiment

`pipeline/designs/riscv32i/run_spec_closure_20261015.json` admits three full
evaluations, serially, with a four-hour admission deadline and functional
verification requested. It compares a fresh baseline with one and two physical
fanout-repair rounds. All use the same 23.6 ns diagnostic clock and final SDC.
The original 15 ns target remains unqualified even if diagnostic closure wins.

The reference is `riscv32i__2026-10-14__024847.json`: 25 antenna, 122 capacitance
and 46 fanout violations, no slew violations. All 46 measured fanout nets have
at most 16 non-diode loads. The hypothesis is to reserve diode headroom with a
per-net split limit of 12, without globally relaxing final electrical limits.
Final extracted timing, DRC, LVS, antenna and equivalence govern acceptance;
an intermediate clean repair report does not.

The one-round candidate really inserted 92 buffers across all 46 targeted
nets (none skipped). Its final STA reports fanout 23 and capacitance 85, but
antenna 34 and worst setup slack -1.076838 ns, compared with baseline fanout
46, capacitance 122, antenna 25 and setup +0.530555 ns. This is not acceptance.
The slow-corner worst path is `instr[4] -> aluout[29]`. Four newly inserted
`buf_4` stages lie on that path:

| Buffer | Original net | Next original sink | Cell delay (ns) |
|---|---|---|---:|
| `fanout_repair_10` | `_02626_` | `_10008_/A` | 0.841258 |
| `fanout_repair_25` | `_02750_` | `_10119_/A` | 0.604887 |
| `fanout_repair_35` | `_02861_` | `_10120_/A` | 0.662157 |
| `fanout_repair_37` | `_02862_` | `_11353_/A` | 0.617421 |

Their total reported cell delay is 2.725723 ns, not the isolated causal WNS
delta: other cells and routing also changed. This evidence argues against
blindly increasing repair rounds. A next bounded hypothesis should preserve
the critical sinks directly on their existing driver while buffering only
other load clusters, or independently evaluate stronger branch drivers. Such
timing-aware bypass is not implemented or measured by this follow-up. Final
fanout/antenna closure must still be checked; these changes cannot be accepted
from timing intuition alone.

## AES residual-net diagnosis

`reference-db/aes_antenna_net_analysis_20261015.json` joins the archived `hul`
antenna report to its exact final netlist and records both hashes. The new
`pipeline/antenna_net_report.py` refuses empty/unrecognized reports and keeps
unknown net matches unknown rather than reporting zero loads.

| Net | Layer | Ratio | Existing diodes | Logic loads |
|---|---|---:|---:|---:|
| `u0.w[1][21]` | met3 | 1.96 | 2 | 2 |
| `_04779_` | met3 | 1.39 | 0 | 2 |
| `text_in_r[26]` | met1 | 1.38 | 0 | 4 |
| `_03113_` | met3 | 1.28 | 0 | 1 |
| `net5` | met3 | 1.03 | 0 | 1 |
| `_00231_` | met1 | 1.01 | 0 | 1 |

The archive lacks this run's routed DEF/ODB. Do not infer diode locations or
layer lengths from another candidate. Next: reproduce the exact `hul` recipe,
retain routed geometry, then test protection on the five diode-free nets;
inspect route segmentation/protection placement on the already-protected net.
Each change must retain electrical, timing, DRC and LVS gates. `hul` uses a
12 ns diagnostic clock, not the original 10 ns target. Prior ITERS changes were
ineffective; lower antenna margin reduced diode count but increased residual
violations (`docs/open-issues-loop-graph-20261007.md`).

## SRAM: interface evidence, not model qualification

`pipeline/verify_sram_interface.py` runs the r15 IO-chain variant against the
original wrapper using ideal cells and a macro-input signal monitor. All 64
equivalence points prove; a deliberately inverted observed write-enable fails
with 25 unproven points. Logs and source hashes are retained under
`pipeline/designs/sram_wrapper/experiments/evidence-20261015-interface/`.
This checks the sampled logical macro-input interface, not internal SRAM
function, physical clock skew or Liberty validity.

The r15 physical replay uses a unique `--name-suffix revalidate-20261015`,
preserving earlier scratch runs. The first invocation lacked the active PDK
link and is explicitly an infrastructure failure, not a design observation.

Replay completed: slew, fanout, capacitance, antenna, Magic DRC, KLayout DRC
and LVS counts are all zero. Worst setup slack is +8.962652 ns and hold slack
+0.114747 ns. All 57 macro inputs were exported in nine corners; maximum slew
is 0.039395 ns. Static tied pins legitimately report zero slew. No input-axis
extrapolation was found, but six corner mappings apply a TT model to SS/FF.
The verdict remains unverified, not PASS. Reports, effective configuration,
metrics and LVS evidence are in `evidence-20261015-physical/`; the full model
audit is `reference-db/sram_macro_model_audit_20261015.json`.

OpenRAM input preparation was reproduced at revision
`b2b069ce119d1488cbe6883b2240bceb5c7ce29a`, with its Makefile-pinned SRAM cell
revision `dd64256961317205343a3fd446908b42bafba388`. Both `sp_lib` and `gds_lib`
are needed: OpenRAM uses GDS existence to select the library cell name.
An isolated Python environment supplies NumPy, SciPy and scikit-learn. The
successful `--prepare-only --corner ss` manifest verifies all 8192 storage
cells' Q/Q_bar paths and both sense-enable nodes. Source and canonical netlist
hashes match the earlier archived preparation.

The system simulator was ngspice 42 with KLU. Separate original and indexed
ngspice 46 builds were subsequently reproduced in `/tmp`, without replacing
the system binary. Release hash, eight source hashes and LF-normalized patch
hash match the earlier evidence. Both `make check` commands exit 0 (their
historical reference differences remain in the archived logs). Five synthetic
model-bin tests and actual SKY130 two-device SS/TT/FF comparisons match between
the two builds. See `evidence-20261015-simulator/build.json` and its reports.

The first real-device comparison timed out while reading the Windows PDK.
Only the required SPICE directories were copied to a Linux temporary directory;
all 856 files matched SHA256. The local-copy comparison then completed. This
is a file-location change, not a model or numerical-tolerance change.

Input preparation was repeated successfully with the indexed ngspice 46
binary hash recorded in `prepared-ss-manifest.json`. No new full-SRAM transient
measurements or Liberty were generated. The remaining task is the required
SS/TT/FF input-edge and output-load grid, followed by functional/PVT and table
qualification. The earlier SS single-point measurement is not a full table.
See `characterization/ngspice-bin-index/README.md` under the SRAM design for
the source hashes, patch and comparison commands.

## Functional verification boundaries

The existing `--verify-function` checks synthesis output, not later physical
rewiring. `equiv_check.py --final-netlist` now checks the routed logical
netlist. It only models known SKY130 HD physical-only families as logical
no-ops after checking input-only Liberty pins or power/ground-only LEF pins.
Other unknown logic remains an error. Both RTL and netlist are normalized with
memory mapping, global-clock FF conversion and bit-split names. The FF pass's
negative-hold assumption for asynchronous inputs is explicit; analog delay,
metastability and physical skew are outside the proof.

`--reference-synthesis` isolates the physical transformation from RTL mapping;
it must never be presented as an RTL proof. The baseline synthesis-to-final
proof proves 10224 points with zero unproven. The initial RTL-to-final proof
left 96 of 99 points unproven: no counterexample was established, but no full
proof was obtained either. Bit-split state matching reached the ten-minute
deadline without a final result; its initially listed points are not final
proof counts. Further RTL proof work needs register-file/state partitioning
and explicit reset assumptions, not treating timeout as a counterexample.
Actual Docker/Yosys controls show the correct buffer passes and a deliberately
wrong inverter fails with either reference. Final proofs have a ten-minute
deadline and remove only their own uniquely named container on timeout.

## Regression and environment checks

WSL full Python suite: 1365 tests, no failures, six conditional skips. An
additional 31 targeted tests checked the final measurement-coverage wording
(one Docker-conditional skip). Actual final-proof Docker controls were run
separately rather than inferred from the pure suite.
The earlier full run exposed the schematic symlink error, stale surrogate
default, monotonic-history assumption and four missing PDK support files.
The existing `pdk_repair.py --pdk sky130 --apply` restored only empty,
explicitly annotated `drc_exclude.cells` lists for HVL/OSU libraries in
sky130A/B. This neither disables final DRC nor qualifies those cells; active HD
libraries were unchanged. PDK downloads and scratch design directories are not
source changes to commit.

Windows sandbox temp-directory access caused a separate test invocation to
fail before fixture creation. The WSL suite above is the actual regression
result; environment permission failures are not silicon failures.
