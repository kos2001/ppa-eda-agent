# Completed SRAM point measurements, 2026-10-15

Both authorized SS/1.6 V/100 C full-netlist attempts completed normally within
their individual 7,200-second limits. They use address255/bit31, a 10 ns period,
0.560 ns input slew and 27.56 fF output load. Numerical settings and the
hash-verified experimental indexed ngspice46 build remained fixed.

| Observation | Port0 replay | Port1 read / port0 write |
|---|---:|---:|
| Wall time, seconds | 6879.118 | 6816.598 |
| Finite measurements | 27/27 | 27/27 |
| Rising output delay, ns | 4.83158 | 5.30368 |
| Falling output delay, ns | 1.41998 | 0.98589 |
| Sense-enable delay, ns | 4.93488 | 5.15392 |
| Storage polarity / output rail checks | Pass | Pass |
| Single-point timing checks | Pass | Fail |

Port0 reproduces all 27 historical values at printed precision. Port1's
rising-output and sense-enable delays exceed the 5 ns half-period bounds.
These are real failed point checks, not simulator errors or timeouts. They
do not establish failure against a different clock or operating condition.

Raw logs, inputs, source snapshots, process samples and checksums are retained
in `evidence-20261015-baseline-repeatability` and `evidence-20261015-ss-port1`
under `pipeline/designs/sram_wrapper/experiments`. Each wrapper verifies copied
bytes and writes a final summary. The initial port0 Python import failure
occurred before SPICE launch and is retained separately. The corrected wrapper
snapshots all five required Python modules.

The batch simulator evaluates `.meas` statements after analysis, explaining
the absence of intermediate measurement values. Final accounting identifies
matrix loading and factorization as major costs: port1 reports 4,279 seconds
loading and 1,207 seconds factoring, with 15,270 accepted timepoints. CPU
activity alone had not established transient progress before completion.

Neither point qualifies a Liberty model or full-design signoff. TT/SS/FF table
coverage, setup/hold validation and qualified corner mappings remain open.
The new condition review compares the failed point with the actual retained
implementation before any further simulation is admitted. Earlier dated
packets retain their original status as historical snapshots.
