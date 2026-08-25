#!/usr/bin/env python3
"""Renders a real PNG image of a completed run's actual GDS layout, via
KLayout headless (bundled in the OpenLane 2 Docker image already used by
run_stage.py — no new project dependency).

Applies a real finding from arxiv.org/html/2605.06936v3 ("PostEDA-Bench":
adding layout images to text-based prompts "consistently improves DRC
performance... never harmful"): physical-constraint-evaluator and
routing-candidate-evaluator previously only had text (logs, metrics.json)
to reason from. This gives them a real rendered image of the same run's
actual layout to view via the Read tool.

Usage:
    render_layout.py --run-dir pipeline/designs/counter4/runs/<tag> \
        --output /tmp/layout.png

Requires the same Docker + image as run_stage.py.
"""
import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from toolchain import OPENLANE_IMAGE as IMAGE

REPO_ROOT = Path(__file__).resolve().parent.parent

# KLayout's LayoutView.load_layout() takes a *filename*, not a pre-loaded
# pya.Layout — passing a Layout object there raises a real TypeError
# (verified: "No overload with matching arguments... load_layout(string
# filename, ...)"), which is why this loads by path instead. QT_QPA_
# PLATFORM=offscreen is required — without it, KLayout segfaults with no
# usable error (verified: a raw /proc-style memory dump, no Python
# traceback) because LayoutView.save_image still goes through Qt's
# rendering pipeline even in `-z` (no GUI) batch mode.
_KLAYOUT_SCRIPT = """
import pya
view = pya.LayoutView()
view.load_layout("/work/layout.gds", True)
view.max_hier()
view.zoom_fit()
view.save_image("/work/out.png", {size}, {size})
"""


def find_gds(run_dir: Path) -> Path:
    final = run_dir / "final" / "gds"
    if final.exists():
        gds_files = list(final.glob("*.gds"))
        if gds_files:
            return gds_files[0]
    # Fall back to the latest GDS any step produced (e.g. a run that
    # failed at/after Magic.StreamOut but before final/ was populated).
    candidates = sorted(run_dir.glob("**/*.gds"), key=lambda p: p.stat().st_mtime)
    if not candidates:
        raise FileNotFoundError(f"no .gds found under {run_dir} — run hasn't "
                                 f"reached Magic.StreamOut yet")
    return candidates[-1]


def render_gds_png(run_dir: Path, output_path: Path, size: int = 900) -> Path:
    """Renders run_dir's real GDS to a PNG at output_path. Raises on any
    real failure (missing GDS, Docker/KLayout error) rather than writing
    a placeholder — a missing image should be visibly absent, not a
    blank/fake one silently standing in."""
    gds_path = find_gds(run_dir)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        shutil.copy(gds_path, tmp_path / "layout.gds")
        (tmp_path / "render.py").write_text(_KLAYOUT_SCRIPT.format(size=size))

        cmd = [
            "docker", "run", "--rm", "--platform", "linux/amd64",
            "-e", "QT_QPA_PLATFORM=offscreen",
            "-v", f"{tmp_path}:/work",
            IMAGE,
            "klayout", "-z", "-r", "/work/render.py",
        ]
        print(f"$ {' '.join(cmd)}", file=sys.stderr)
        result = subprocess.run(cmd, capture_output=True, text=True)
        sys.stderr.write(result.stderr)

        rendered = tmp_path / "out.png"
        if not rendered.exists():
            raise RuntimeError(
                f"klayout did not produce out.png for {gds_path} "
                f"(exit {result.returncode}); stderr tail: {result.stderr[-500:]}"
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(rendered, output_path)
    return output_path


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--size", type=int, default=900)
    args = ap.parse_args()
    out = render_gds_png(args.run_dir, args.output, args.size)
    print(out)


if __name__ == "__main__":
    main()
