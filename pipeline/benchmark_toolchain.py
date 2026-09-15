#!/usr/bin/env python3
"""Measure one full flow with verified tool versions and retained evidence.

Use a fresh tag/output directory for each sample. Select the toolchain
with PPA_EDA_TOOLCHAIN; images must already be installed. Exit success
means the flow returned successfully, not that a macro model is qualified.
Compare final metrics and signoff reports before adopting a candidate.
"""
import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

from run_stage import PDK_ROOT, run_stage
from toolchain import OPENLANE_IMAGE, platform_args, toolchain_info


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_inputs(design, expected):
    """Do not attribute a concurrently edited design to its earlier snapshot."""
    changed = [name for name, digest in expected.items()
               if not (design / name).is_file() or sha256(design / name) != digest]
    if changed:
        raise RuntimeError("Benchmark inputs changed during execution: " + ", ".join(changed))


def require_capacity(paths, minimum_gib):
    """Check both bind-mount destinations before starting an expensive run."""
    for path in paths:
        free = shutil.disk_usage(path).free / (1024 ** 3)
        if free < minimum_gib:
            raise RuntimeError(f"{path}: only {free:.2f} GiB free; "
                               f"benchmark requires {minimum_gib:.2f} GiB")


def inspect_tools():
    inspected = subprocess.run(
        ["docker", "image", "inspect", OPENLANE_IMAGE],
        capture_output=True, text=True, check=True, timeout=30)
    image = json.loads(inspected.stdout)[0]
    probe = subprocess.run(
        ["docker", "run", "--rm", "--pull=never", *platform_args(),
         OPENLANE_IMAGE, "python3", "-c",
         "import json,platform,subprocess,openlane; "
         "print(json.dumps({'openlane':openlane.__version__, "
         "'openroad':subprocess.check_output(['openroad','-version'],text=True).strip(), "
         "'arch':platform.machine()}))"],
        capture_output=True, text=True, check=True, timeout=60)
    versions = json.loads(probe.stdout)
    expected = toolchain_info()["expected_openroad_revision"]
    if versions["openroad"] != expected:
        raise RuntimeError(f"OpenROAD revision differs from profile: {versions!r}")
    return {"image_id": image["Id"], "repo_digests": image.get("RepoDigests", []),
            "versions": versions}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--design", required=True, type=Path)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--output", required=True, type=Path,
                    help="new evidence directory (refuses existing paths)")
    ap.add_argument("--flow", choices=["Classic", "MacroSignoff", "UpstreamClassic"])
    ap.add_argument("--override", action="append", default=[])
    ap.add_argument("--min-free-gib", type=float, default=3)
    args = ap.parse_args()
    if not args.tag or args.tag in {".", ".."} or "/" in args.tag or "\\" in args.tag:
        ap.error("--tag must be a single directory name")
    if args.min_free_gib <= 0:
        ap.error("--min-free-gib must be positive")
    design = args.design.resolve()
    output = args.output.resolve()
    if output.exists() or (design / "runs" / args.tag).exists():
        ap.error("choose a new tag and output directory; existing evidence is preserved")
    parent = output.parent
    while not parent.exists():
        parent = parent.parent
    require_capacity([design, parent], args.min_free_gib)
    tools = inspect_tools()
    # Keep a snapshot of the input tree, excluding generated results and views.
    source_paths = [design / "config.json"]
    for folder in ("src",):
        if (design / folder).is_dir():
            source_paths.extend(p for p in (design / folder).rglob("*") if p.is_file())
    source_paths.extend(design.glob("*.cfg"))
    inputs = {str(p.relative_to(design)): sha256(p) for p in sorted(set(source_paths))}
    pdk_links = {p.name: str(p.resolve()) for p in PDK_ROOT.iterdir() if p.is_symlink()}
    output.mkdir(parents=True)
    for path in source_paths:
        target = output / "inputs" / path.relative_to(design)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    report = {"status": "running", "started_at": datetime.now(timezone.utc).isoformat(),
              "design": design.name, "tag": args.tag, "flow_override": args.flow,
              "overrides": args.override, "inputs_sha256": inputs,
              "pdk_links": pdk_links, "toolchain": toolchain_info(), "observed_tools": tools}
    record = output / "benchmark.json"
    record.write_text(json.dumps(report, indent=2) + "\n")
    started = time.monotonic()
    try:
        with (output / "console.log").open("w") as log, redirect_stdout(log), redirect_stderr(log):
            run_stage(design, args.tag, None, args.override,
                      overwrite=False, flow=args.flow)
        verify_inputs(design, inputs)
        if not (design / "runs" / args.tag / "final/metrics.json").is_file():
            raise RuntimeError("Flow returned without final metrics; benchmark is incomplete")
        report["status"] = "flow_completed"
    except BaseException as exc:
        report.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        report["wall_seconds"] = time.monotonic() - started
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        run = design / "runs" / args.tag
        for name in ("resolved.json", "final/metrics.json"):
            path = run / name
            if path.is_file():
                target = output / Path(name).name
                shutil.copy2(path, target)
                report[name + "_sha256"] = sha256(path)
        record.write_text(json.dumps(report, indent=2) + "\n")
        print(record)


if __name__ == "__main__":
    main()
