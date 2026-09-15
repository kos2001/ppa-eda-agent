# OpenLane / OpenROAD update audit — 2026-09-15

Status: implementation and unit checks prepared; candidate physical runs and
performance acceptance are **pending**. The stable default has not changed.

## Observed versions

The installed container reports OpenLane 2.3.10 and OpenROAD
`edf00dff99f6c40d67a30c0e22a8191c5d2ed9d6`, running natively on aarch64.
The OpenROAD source revision dates to 2024-10-02. Being current on the
OpenLane stable branch does not mean that OpenROAD is current.

The official OpenLane tag list still has 2.3.10 as the newest stable tag;
the newest development tag is 3.0.0.dev21 (`f43c65b15a16f8890fb242e902640d64fe7bb283`).
It is 26 commits ahead of 2.3.10. Its Nix build pins OpenROAD to
`87af90f72f3f9be1fdfa1d886f0dd8d8b8f34694` (2024-12-08), despite the changelog's
earlier intermediate `1d61007` reference. This is a candidate upgrade, not
the September 2026 OpenROAD engine.

OpenROAD master was `c751cdc78e74d062afbc70e1ffb0ed68553805e7` at inspection.
Recent changes include global-routing maze recovery (#11404), termination
of PDN repair when no work remains (#11398), and resizer startpoint/area
caching with cache invalidation fixes (September 11–12). These C++ changes
are **not installed** by selecting the OpenLane development image. A current
engine needs a separately built, compatible toolchain and regression runs.

Sources (queried directly through GitHub API and raw source):

- [OpenLane tags](https://github.com/chipfoundry/openlane2/tags)
- [Stable-to-development comparison](https://github.com/chipfoundry/openlane2/compare/2.3.10...3.0.0.dev21)
- [Development OpenROAD pin](https://github.com/chipfoundry/openlane2/blob/3.0.0.dev21/nix/openroad.nix)
- [OpenROAD routing correction](https://github.com/The-OpenROAD-Project/OpenROAD/pull/11404)
- [OpenROAD PDN correction](https://github.com/The-OpenROAD-Project/OpenROAD/pull/11398)
- [Resizer caching correction](https://github.com/The-OpenROAD-Project/OpenROAD/commit/8031f5e819c7)

## Prepared changes

`PPA_EDA_TOOLCHAIN=openlane-dev` selects the development image consistently
across pipeline clients. Provenance distinguishes expected source revisions
from the benchmark's actual container-reported versions. Unknown profiles
fail instead of silently running the stable baseline again.

`--flow UpstreamClassic` backports the fixed-pin preliminary-placement skip
from OpenLane commit `dffc4423325a238e8dd7e478d6be368d9fe1ab5f`. SPM uses
`FP_PIN_ORDER_CFG`, so its first placement is unnecessary for determining
pin locations. The flow preserves the final placement and all checkers.
It is available for comparison but is not yet the design's configured flow.

`benchmark_toolchain.py` requires a new tag and evidence directory, checks
free space, requires an already installed image, verifies the OpenROAD
revision, records wall time, and copies input files, resolved config, final
metrics, and console output. Do not compare wall time across concurrent runs.

## Execution checkpoint

Stable full-flow runs on GCD and SPM completed before the image download
failed. Their run directories were subsequently removed during user-directed
disk cleanup; regenerate both baselines for an auditable comparison.
Console logs were still present in `/private/tmp/ppa-toolchain-baseline-20260915.log`
and `/private/tmp/ppa-spm-baseline-20260915.log` at this checkpoint.

The dev21 image manifest has native arm64 and amd64 builds. Downloading it
failed during extraction with a Docker VM I/O error; the host had only
164 MiB free and Docker shut down. The user confirmed disk cleanup is in
progress. Downloads, Docker recovery, and physical runs are paused until
cleanup is finished. No image/container/volume pruning was performed here.

On the next check, host free space was 3.6 GiB. Docker's backend process
still existed and `docker desktop start` reported "already running", but
the engine socket was absent and `docker ps` failed. This does not establish
that Docker recovered. Confirmation that cleanup has finished is pending;
no download or benchmark was restarted. The benchmark also now rejects
inputs changed/deleted during execution and a successful flow return with
no final metrics. The new input-consistency regression test passed.

## Remaining acceptance work

1. After cleanup, check Docker and disk capacity before downloading images.
2. Run fresh stable and `UpstreamClassic` SPM samples sequentially with
   `pipeline/benchmark_toolchain.py`; compare runtime, area, power, timing,
   antenna, DRC, LVS, and final-netlist equivalence before adopting the fix.
3. Finish dev21 acquisition, verify the observed engine revision, and compare
   full GCD/SPM flows with all timing corners checked. Audit custom macro-flow
   API compatibility and corner/library mapping before broader adoption.
4. Evaluate the September 2026 OpenROAD corrections using a compatible build;
   the development OpenLane image alone does not complete that work.
5. Adopt only measured improvements, retain reproducible evidence, and rerun
   the relevant regression checks. No PPA or runtime improvement is claimed yet.

Example sequential samples after Docker and storage are ready:

```sh
python3 pipeline/benchmark_toolchain.py --design pipeline/designs/spm \
  --tag upstream-baseline-1 --flow Classic --output /private/tmp/spm-baseline-1 \
  --override 'TIMING_VIOLATION_CORNERS=*'
python3 pipeline/benchmark_toolchain.py --design pipeline/designs/spm \
  --tag upstream-candidate-1 --flow UpstreamClassic --output /private/tmp/spm-candidate-1 \
  --override 'TIMING_VIOLATION_CORNERS=*'
```
