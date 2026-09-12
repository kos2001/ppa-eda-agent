# Schematic capture for the custom/analog flow — the step this pipeline
# started one below.
#
# Why an image of our own rather than an existing one. xschem is the
# open-source Virtuoso Schematic Editor (see
# docs/superpowers/specs/2026-09-12-virtuoso-counterpart-and-custom-bridge.md),
# and it is the one backend of that five-tool stack reachable from
# neither this host nor the OpenLane image this pipeline already pulls:
#
#   - no Homebrew formula exists (checked: `brew info xschem` → no
#     available formula)
#   - building from source on macOS needs XQuartz plus a Tk linked
#     against X11 rather than Aqua (xschem's own README_MacOS.md), which
#     is a larger dependency than the tool
#   - IIC-OSIC-TOOLS ships it, along with the whole analog stack, at a
#     size that is not worth pulling to run one netlister
#
# Debian trixie packages xschem 3.4.4, current enough for the sky130
# symbol library the PDK ships. That makes this image one apt package on
# a slim base — small enough to build locally in under a minute, and
# pinned by its Debian version rather than by "whatever apt returns".
#
# Build (pipeline/custom_bridge.py does this for you on first use):
#   docker build -f pipeline/docker/xschem.Dockerfile -t ppa-eda/xschem:3.4.4 .
#
# Run: mount this repo's pdk/ at /pdk — the PDK's own
# libs.tech/xschem/xschemrc reads PDK_ROOT and PDK to find the models
# and the sky130_fd_pr symbols.
FROM debian:trixie-slim

# xschem 3.4.4-1 in trixie. --no-install-recommends keeps the GUI-only
# extras out; netlisting runs with -b (no X11 at all).
RUN apt-get update \
 && apt-get install -y --no-install-recommends xschem \
 && rm -rf /var/lib/apt/lists/*

ENV PDK_ROOT=/pdk \
    PDK=sky130A
WORKDIR /work
