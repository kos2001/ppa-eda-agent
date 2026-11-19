# SS port1 20 ns diagnostic result

The period-only diagnostic completed normally in 13,217.651 seconds (about
220 minutes), within its 14,400-second limit. All 27 requested measurements
are finite; raw output and independently reconstructed checks agree.
The result remains `measured_point_checks_failed`, with `model_qualified=false`.

| Measurement | Result, ns |
| --- | ---: |
| Rising output delay | 5.29730 |
| Falling output delay | -3.62257 |
| Sense-enable delay | 5.15426 |

The falling-output measurement selected target 106.727 ns and trigger
110.350 ns, as printed in `timing.lis`. The target precedes the selected
falling clock edge. This violates the positive-delay check; it is not evidence
of a valid negative physical propagation delay. Event selection and waveform
phase must be investigated before treating this as a usable timing-table
point. The rising and sense-enable delays fit the new half-period bound,
and storage-polarity/output-rail checks pass, but those facts do not override
the failed falling-delay measurement.

The point keeps SS/1.6 V/100 C, address255/bit31, port0 writes and port1 reads,
0.560 ns input slew, 27.56 fF output load and the original numerical settings.
Only the period changes from 10 to 20 ns; the generated transient lasts 300 ns.
This diagnostic does not relax or certify the physical design's timing target.

Full raw evidence is retained under
`pipeline/designs/sram_wrapper/experiments/evidence-20261015-ss-port1-period20`.
All 27 artifact hashes were checked before publication. The measurement parser
and independent point checker reproduce the stored manifest. No production
Liberty or measured-case signoff verdict was changed.
