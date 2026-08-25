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
import subprocess
import sys
from pathlib import Path

from toolchain import OPENLANE_IMAGE as IMAGE

REPO_ROOT = Path(__file__).resolve().parent.parent
PDK_ROOT = REPO_ROOT / "pdk"


def run_stage(design_dir: Path, tag: str, to_step: str | None,
              overrides: list[str], overwrite: bool = True,
              scl: str | None = None) -> Path:
    """Runs a real OpenLane flow against design_dir, returns the run dir.

    `scl` selects the standard cell library. It is a CLI flag rather than
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
        "docker", "run", "--rm", "--platform", "linux/amd64",
        "-v", f"{PDK_ROOT}:/pdk",
        "-v", f"{design_dir}:/design",
        IMAGE,
        "openlane", "--pdk-root", "/pdk",
        "--run-tag", tag,
    ]
    if scl:
        cmd += ["--scl", scl]
    if overwrite:
        cmd.append("--overwrite")
    if to_step:
        cmd += ["--to", to_step]
    for kv in overrides:
        cmd += ["--override-config", kv]
    cmd.append("/design/config.json")

    print(f"$ {' '.join(cmd)}", file=sys.stderr)
    result = subprocess.run(cmd, cwd=design_dir, capture_output=True, text=True)
    # Stream what OpenLane printed even though we captured it, so a live
    # run is still visible — only the *raised error message* needs the
    # tail of it for propose_repairs() to pattern-match on.
    sys.stderr.write(result.stdout)
    sys.stderr.write(result.stderr)
    if result.returncode != 0:
        tail = (result.stdout + result.stderr)[-2000:]
        raise RuntimeError(
            f"OpenLane run '{tag}' exited {result.returncode} — "
            f"see runs/{tag}/ for logs. Tail of output:\n{tail}"
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
