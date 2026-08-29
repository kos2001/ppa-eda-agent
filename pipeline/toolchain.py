#!/usr/bin/env python3
"""Single source of truth for the EDA toolchain this pipeline runs.

Why this module exists. The image was hardcoded independently in four
modules (`run_stage`, `render_layout`, `odb_query`, `equiv_check`).
Changing the pinned version meant editing four files, and missing one
would not fail loudly — it would run part of the pipeline on a different
OpenLane build while recording every result into the same reference-db
as if the numbers were comparable. For a project whose entire value is
that its measurements are real and comparable, two silently different
toolchains is a correctness hazard, not untidiness.

On the upstream rename. OpenLane 2 now lives at
github.com/chipfoundry/openlane2 — the same repository as the former
github.com/efabless/openlane2 (identical repo id 589378383; GitHub
redirects the old path). The published container is still under the
efabless namespace, and `ghcr.io/chipfoundry/openlane2` does not exist,
so the image reference below is deliberately NOT "modernised" to match
the new org name. Verified rather than assumed: the chipfoundry image
name returns not-found, the efabless one resolves.

On the version. 2.3.10 is the newest *stable* 2.x tag upstream — this
pipeline is already current. Newer tags are `3.0.0.dev*` pre-releases,
deliberately not adopted: every number in reference-db came from a real
run, and re-baselining all of them onto a development build would trade
that for novelty.
"""

import os
import platform

OPENLANE_IMAGE = "ghcr.io/efabless/openlane2:2.3.10"

# Where that image comes from, recorded alongside results so a case can
# be attributed to a toolchain rather than to "whatever was installed".
OPENLANE_UPSTREAM = "github.com/chipfoundry/openlane2"


# Which architecture the container runs under.
#
# This pipeline forced `--platform linux/amd64` from its first commit,
# which on an arm64 host means every OpenLane run has been emulating
# x86. The image is multi-arch and both OpenLane and OpenROAD run
# natively: measured on counter4, 68 s emulated against 28 s native, and
# 278 of 279 metrics identical — the exception being
# power__leakage__total differing in its tenth significant figure, which
# is floating-point summation noise on a 0.5 nW number.
#
# Overridable because the choice is a real one: an x86 host is native
# either way, and a future tool in the image might be x86-only.
DOCKER_PLATFORM = os.environ.get("PPA_EDA_DOCKER_PLATFORM", "").strip()


def platform_args() -> list[str]:
    """`docker run` platform flag, or nothing to let Docker pick native."""
    return ["--platform", DOCKER_PLATFORM] if DOCKER_PLATFORM else []


def host_info() -> dict:
    """The machine a result was produced on.

    Borrowed from freerouting's benchmark table
    (github.com/freerouting/freerouting, docs/benchmarks.md), which
    states its CPU, RAM and OS above the numbers. This store recorded the
    OpenLane image and nothing about the machine, so two cases from
    different hosts — or from emulated and native runs of the same image
    — were compared as though identical, and any wall-clock figure taken
    from one had no stated environment at all.

    Deliberately no hostname or user: what makes results comparable is
    the architecture and core count, not who ran them.
    """
    return {
        "arch": platform.machine(),
        "system": platform.system(),
        "cpu_count": os.cpu_count(),
        # "" means Docker chose; anything else was forced by us.
        "docker_platform": DOCKER_PLATFORM or "native",
    }


def toolchain_info() -> dict:
    """Provenance for a reference-db case. Cheap and constant — the
    point is that a stored result carries the version that produced it,
    so two cases can be compared knowingly rather than hopefully."""
    return {
        "openlane_image": OPENLANE_IMAGE,
        "openlane_upstream": OPENLANE_UPSTREAM,
        "host": host_info(),
    }


# The Classic flow's declared step list, asked of the image itself.
#
# Needed because a run's directory listing shows what *executed*, and
# the interesting question is what was declared and did not. Cached for
# the process: it costs a container start, and it cannot change while
# the pinned image does not.
_CLASSIC_STEPS: list[str] | None = None


def classic_steps() -> list[str]:
    """Step ids in OpenLane's Classic flow, or [] if they can't be read.

    Non-fatal by design: this feeds a reporting field, and a case that
    cost real OpenLane time must not be lost because Docker was busy.
    """
    global _CLASSIC_STEPS
    if _CLASSIC_STEPS is not None:
        return _CLASSIC_STEPS
    import subprocess
    try:
        out = subprocess.run(
            ["docker", "run", "--rm", OPENLANE_IMAGE, "python3", "-c",
             "from openlane.flows import Flow\n"
             "print('\\n'.join(s.id for s in Flow.factory.get('Classic').Steps))"],
            capture_output=True, text=True, timeout=300,
        )
        _CLASSIC_STEPS = [l.strip() for l in out.stdout.splitlines() if l.strip()]
    except Exception:  # noqa: BLE001 - reporting field, not a gate
        _CLASSIC_STEPS = []
    return _CLASSIC_STEPS
