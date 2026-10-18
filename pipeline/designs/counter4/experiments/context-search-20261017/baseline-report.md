# Context search measurement — baseline phase

Actual OpenLane baseline measurements are complete. Context-policy model trials have not started because automatic approval review rejected the proposed authenticated external model calls and planned physical workload. Approval scope and exact first-round prompts are in `approval-scope.json` and `review-payloads/`.

Both baseline runs use the same frozen counter4 RTL, 10 ns clock, SKY130 HD PDK and OpenLane 2.3.10 image. Their complete evaluation compatibility keys match. These two points establish the baseline; they do not measure an effect of the agent update.

| Core utilization setting | Cell area (µm²) | Core area (µm²) | Activity power (µW) | Worst setup slack (ns) | Flow plus assessment (s) |
| --- | ---: | ---: | ---: | ---: | ---: |
| 25 | 290.278 | 630.605 | 124.000 | 6.087655 | 78.160 |
| 35 | 290.278 | 480.461 | 124.000 | 6.094356 | 48.458 |

Power is extracted-parasitic, activity-annotated OpenSTA analysis at the ff_n40C_1v95 corner with the counter4 testbench workload; it is not laboratory power. The native power table is rounded to three significant figures. Baseline cell area × activity power is 0.035994472 µm²·W for both candidates. The 35% setting dominates the 25% setting on the measured frontier, so only the former is retained as a qualified seed; a second nondominated seed is not invented.

Both runs pass recorded Magic/KLayout DRC, Netgen LVS, electrical/antenna checks and all nine reported setup/hold corners. Nonvacuous RTL-to-synthesis proofs have four proven points and synthesis-to-final proofs have fourteen proven points. The independent gate-level testbench reports zero errors. Full transcripts, inputs and final native reports are retained. `baseline-artifacts.json` lists 2,078 source/result/native files with byte hashes; integrity does not itself certify the declared gates.

Cold-start and warm execution times differ, so these two durations are not a speedup comparison between search policies. No independent physical repeat or run-to-run noise estimate is available.

The preregistered comparison has three policies, three model repetitions and three feedback rounds. It measures context-policy search performance on this small design, including new valid proposals, duplicates, measured token/time cost and verified final PPA trajectories. The conditional-reset trigger remains observational, and a single small design cannot establish general EDA superiority.

Pending execution: up to 27 model calls through the logged-in OpenAI Codex service and up to 27 additional unique local full-flow evaluations. Dollar cost is unavailable; exact token usage will be retained. No improvement percentage for the agent update is established at this phase.
