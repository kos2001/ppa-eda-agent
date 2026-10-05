# Why aes has never passed — 2026-10-02

`aes_cipher_top` (134,000 um2 of cells on sky130hd, about 25 minutes a run) has
no passing candidate in `reference-db/`. It fails for several independent
reasons that were found one at a time, by hand, over nine iterations. Counts
below are signoff counts from stored runs, not estimates.

## What failed, and what each fix was

| Failure | Size at the start | Cause (how it was established) | Fix that worked |
|---|---|---|---|
| setup | 481 at 6 ns, 172 at 10, 88 at 10 (DELAY 2), 3 at 11.2 | the netlist's slowest corner needs 11.45 ns (`operating_point`) | `CLOCK_PERIOD` 12; now an auto-repair pattern |
| max-slew | 400 to 1,058 | the PnR slew limit was tighter than the library's own 1.5 ns | `MAX_TRANSITION_CONSTRAINT` 1.5 (the library's limit, not a looser one) |
| hold | 122 to 314 | every violating path starts at an input the SDC says arrives 2.0 ns after the clock edge, while the built clock tree has 2.355 ns insertion delay plus 0.25 ns uncertainty. No register-to-register path violates (OpenSTA at ss on the run's own SPEF) | `IO_DELAY_CONSTRAINT` 25 to 30 (an input-arrival assumption); 263 to 0 |
| antenna | 8 to 30 | long routes | untried until iteration 9, see below |
| max-fanout | 17 at the default limit of 10 | clock buffers and high-fanout data nets | `MAX_FANOUT_CONSTRAINT` 16 takes it to 4, but that raises the limit |
| max-cap | 1 to 4 | | |

Things that did not help, measured: the post-GRT repair steps (iteration 5:
"change nothing"), a hold slack margin of 0.3 to 0.4 ns (more hold buffers,
the same hold failures), and heuristic diode insertion, which took max-fanout
to 1,158 and area from 133,578 to 185,204 um2.

After iteration 8 the best candidate had setup, hold and slew at 0 and 15 antenna,
2 max-cap and 4 max-fanout violations left.

## Why it keeps failing in practice

1. **The recipe lives in run specs, not in the design.** `aes/config.json` still
   has `CLOCK_PERIOD` 10 and nothing else, so a default run reproduces the original
   failures (the 2026-09-12 case: 820 slew, 12 to 28 fanout, hold at 8 ns).
2. **The repair loop knows one of the six.** `propose_repairs()` repairs setup.
   Slew, hold, antenna, fanout and cap each took a person a 25-minute run to
   diagnose, so the loop stops at `no_repairable_failures`.
3. **Some fixes change the check, not the design.** A fanout limit of 16 and a slew
   limit of 1.5 pass more nets by asking less of them. The slew limit is the
   library's own; the fanout limit is not, and a pass that depends on it is weaker.

## Iteration 9: the first try at antenna repair

No aes candidate had set `GRT_ANTENNA_MARGIN` (default 10) or `GRT_ANTENNA_ITERS`
(default 3); `sram_wrapper` went from 3 to 0 antenna violations with 50 and 10.
Three candidates on the iteration-8 recipe (`run_spec_iter9.json`):

| candidate | setup | hold | antenna | slew | max-cap | max-fanout |
|---|---|---|---|---|---|---|
| iteration 8 best, for reference | 0 | 0 | 15 | 0 | 2 | 4 |
| + antenna margin 50, iterations 10 | 0 | 0 | **8** | 0 | 3 | **16** |
| + resizer cap margin 40 | 0 | 0 | 12 | 0 | **0** | 17 |
| antenna knobs, default fanout limit | **6** | 0 | 14 | 0 | 0 | **63** |

The antenna knobs work (15 to 8) and the others do not follow. **Antenna repair
inserts diodes, and each diode is another load on the net it protects**, so fanout
rises from 4 to 16 and cap from 2 to 3 as antenna falls. The same coupling is why
heuristic diode insertion blew fanout up to 1,158. The counts move against each
other, which is why fixing them one knob at a time kept looking like progress and
never reached zero. The last row also shows the fanout limit is not a neutral knob:
both candidates that raise it to 16 have setup at 0, both that do not have setup
violations (8 in iteration 8, 6 here), so the limit changes what the resizer does
as well as what is counted.

## What has been tried since

Updated 2026-10-05 (see `pipeline/designs/aes/experiments/README.md`):

- **Utilization** 45 and 55 shorten wire (-5.5%, -9.3%) and do not reduce
  antenna monotonically (final 4 and 5 against 5 at util 35).
- **Diode headroom** (`FANOUT_REPAIR_LIMIT` 15 under an SDC limit of 16) removes
  the fanout violators. The final netlists show each was the limit in real loads
  plus one antenna diode. It costs area and exposes a marginal slew/cap net.
- **Antenna against fanout as one problem** is settled for fanout: they are
  coupled through the diode. Antenna itself is not closed in any run.
- **Two targeted fixes on top of the headroom** (`_20258_` up-sized, `net1321`
  split) give the first candidate with every gate except antenna at 0, at
  +8.2% area; it still has 6 antenna violations and is at the diagnostic
  constraints.

## What has not been tried

- Varying `GRT_ANTENNA_ITERS`. About 40% of the diodes sit on 36 to 39 nets with
  11 to 12 diodes each, which matches 10 iterations plus one; the iteration count
  was never varied.
- Resizer margins for fanout without moving the limit.
- Splitting the high-fanout round-key and control nets in the RTL.

## Reproduce

```
python3 pipeline/orchestrator.py --design pipeline/designs/aes \
  --run-spec pipeline/designs/aes/run_spec_iter9.json --max-parallel 3
```
