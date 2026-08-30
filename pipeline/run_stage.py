#!/usr/bin/env python3
"""Thin wrapper around a real OpenLane 2 flow run inside Docker.

Mirrors server/index.mjs's pattern for the OpenSTA sim server: a
subprocess call to a real EDA tool in a pinned Docker image, no
simulated/fabricated output. Every run produces OpenLane's own real
run directory (config used, per-step logs, final views, metrics.json).

Usage:
    run_stage.py --design pipeline/designs/counter4 --tag baseline \
        [--to <step-id>] [--override KEY=VALUE ...]

Requires:
    - Docker, with the image pinned in toolchain.py available
    - A sky130 PDK enabled at <repo>/pdk (see docs/superpowers/specs/
      2026-08-21-autonomous-layout-agent-design.md for how it was fetched:
      `volare enable --pdk sky130 --pdk-root <repo>/pdk <version>`)
"""
import argparse
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

from toolchain import OPENLANE_IMAGE as IMAGE, platform_args

REPO_ROOT = Path(__file__).resolve().parent.parent
PDK_ROOT = REPO_ROOT / "pdk"


_UNKNOWN_KEY = re.compile(r"[Aa]n unknown key '([^']+)' was provided")


class IgnoredOverrideError(RuntimeError):
    """An override we passed was not a variable OpenLane recognises."""


def reject_ignored_overrides(overrides: list[str], output: str,
                             tag: str) -> None:
    """Fails a run whose overrides OpenLane silently discarded.

    OpenLane logs `An unknown key 'X' was provided.` at WARNING and keeps
    going, so a candidate configured with a variable this OpenLane
    version does not have runs as an exact duplicate of the baseline and
    reports a perfectly plausible result. That is the worst kind of
    failure here: the candidate looks like evidence.

    It has already cost real conclusions twice. `STD_CELL_LIBRARY` as a
    config override lands in resolved.json but changes nothing (see
    run_stage's docstring), and `RE_BUFFER_CELL` — an OpenLane 1 name
    with no OpenLane 2 equivalent — produced three sram_wrapper
    candidates with byte-identical failures that briefly read as
    "stronger buffers don't help".

    Only keys we actually passed are fatal; OpenLane also warns about
    unknown keys inside config.json that may be there deliberately.
    """
    unknown = set(_UNKNOWN_KEY.findall(output))
    if not unknown:
        return
    passed = {kv.split("=", 1)[0] for kv in overrides}
    ignored = sorted(unknown & passed)
    if ignored:
        raise IgnoredOverrideError(
            f"OpenLane ignored {len(ignored)} override(s) in run '{tag}': "
            f"{', '.join(ignored)}. The run would have been an unmarked "
            f"duplicate of the un-overridden config, so it is failed "
            f"instead of reported. Check the variable name against this "
            f"OpenLane version."
        )


def run_stage(design_dir: Path, tag: str, to_step: str | None,
              overrides: list[str], overwrite: bool = True,
              scl: str | None = None, pdk: str | None = None) -> Path:
    """Runs a real OpenLane flow against design_dir, returns the run dir.

    `pdk` selects the process design kit (sky130A, gf180mcuD, ...) and
    `scl` selects the standard cell library within it. It is a CLI flag rather than
    a config override on purpose: OpenLane 2 chooses the SCL from
    `--scl`, and passing `--override-config STD_CELL_LIBRARY=<x>` instead
    is silently ineffective — verified directly, the override does land
    in the run's resolved.json but the resulting netlist still contains
    only the default library's cells. That failure mode is invisible in
    the metrics (a comparison against it looks like a real 0% delta), so
    it is worth the extra parameter rather than a config entry.
    """
    design_dir = design_dir.resolve()
    if not (design_dir / "config.json").exists():
        raise FileNotFoundError(f"no config.json in {design_dir}")

    cmd = [
        "docker", "run", "--rm", *platform_args(),
        "-v", f"{PDK_ROOT}:/pdk",
        "-v", f"{design_dir}:/design",
        IMAGE,
        "openlane", "--pdk-root", "/pdk",
        "--run-tag", tag,
    ]
    if scl:
        cmd += ["--scl", scl]
    # The PDK, for the same reason as --scl above: it is a flag, not a
    # config override. sky130A is OpenLane's default, so passing nothing
    # keeps every existing run byte-identical.
    if pdk:
        cmd += ["--pdk", pdk]
    if overwrite:
        cmd.append("--overwrite")
    if to_step:
        cmd += ["--to", to_step]
    for kv in overrides:
        cmd += ["--override-config", kv]
    cmd.append("/design/config.json")

    # shlex.join, not " ".join. The command is built as an argv list, so
    # an override like SYNTH_STRATEGY=AREA 2 is passed correctly — but a
    # naive join printed it unquoted, and the line then read as a shell
    # command that would split the value in two. Debugging a batch of
    # failures, that line is the first thing anyone reads, and it
    # accused the argument handling of a bug it does not have. A logged
    # command should be one you could paste.
    print(f"$ {shlex.join(cmd)}", file=sys.stderr)
    # Streamed, not captured-then-printed.
    #
    # This used to be subprocess.run(capture_output=True), which holds
    # everything until the process exits and only then writes it out. The
    # text was identical either way, so nothing looked wrong — but
    # nothing downstream could see a run *in progress*. The console's
    # live view showed one step per candidate (the last), and a human
    # watching a terminal saw a multi-minute silence followed by the
    # whole log at once.
    #
    # Output is still accumulated, because the error tail and the
    # ignored-override check both need the full text.
    captured: list[str] = []
    proc = subprocess.Popen(
        cmd, cwd=design_dir, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        captured.append(line)
        sys.stderr.write(line)
        # OpenLane redraws its progress bar with \r and flushes rarely;
        # without this a reader sees the buffer, not the run.
        sys.stderr.flush()
    returncode = proc.wait()
    output = "".join(captured)

    reject_ignored_overrides(overrides, output, tag)
    if returncode != 0:
        raise RuntimeError(
            f"OpenLane run '{tag}' exited {returncode} — "
            f"see runs/{tag}/ for logs. Tail of output:\n{output[-2000:]}"
        )
    return design_dir / "runs" / tag


def read_metrics(run_dir: Path) -> dict:
    """Reads OpenLane's own metrics.json for a completed run, if present."""
    candidates = list(run_dir.glob("**/metrics.json"))
    if not candidates:
        return {}
    # OpenLane writes one metrics.json per completed step under
    # <run_dir>/<NN-step-name>/; the final one (highest step number,
    # under final/ if signoff ran) has the fullest picture.
    final = run_dir / "final" / "metrics.json"
    if final.exists():
        return json.loads(final.read_text())
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    return json.loads(latest.read_text())


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--design", required=True, type=Path,
                     help="design directory containing config.json")
    ap.add_argument("--tag", required=True, help="run tag / directory name")
    ap.add_argument("--to", dest="to_step", default=None,
                     help="stop at this OpenLane step id (default: full flow)")
    ap.add_argument("--override", action="append", default=[],
                     help="KEY=VALUE config override, repeatable")
    args = ap.parse_args()

    run_dir = run_stage(args.design, args.tag, args.to_step, args.override)
    metrics = read_metrics(run_dir)
    print(json.dumps({"run_dir": str(run_dir), "metrics": metrics}, indent=2))


if __name__ == "__main__":
    main()
