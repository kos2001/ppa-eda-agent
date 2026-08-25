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
PDK_ROOT = REPO_ROOT / "pdk"

# The PDK's KLayout layer properties, mounted read-only into the render
# container. Path is inside the container, not on the host.
LYP_IN_CONTAINER = "/pdk/sky130A/libs.tech/klayout/tech/sky130A.lyp"

# KLayout's LayoutView.load_layout() takes a *filename*, not a pre-loaded
# pya.Layout — passing a Layout object there raises a real TypeError
# (verified: "No overload with matching arguments... load_layout(string
# filename, ...)"), which is why this loads by path instead. QT_QPA_
# PLATFORM=offscreen is required — without it, KLayout segfaults with no
# usable error (verified: a raw /proc-style memory dump, no Python
# traceback) because LayoutView.save_image still goes through Qt's
# rendering pipeline even in `-z` (no GUI) batch mode.
_KLAYOUT_SCRIPT = """
import pya, os

view = pya.LayoutView()
view.load_layout("/work/layout.gds", True)
view.max_hier()

# Render with the PDK's own layer properties instead of KLayout's
# defaults. Without this every layer gets an arbitrary auto-assigned
# colour, so met1 / met2 / poly / diff are indistinguishable and the
# image cannot be reasoned about — which defeats the purpose of
# rendering it at all (see this module's docstring on why the image
# exists). sky130A.lyp is shipped by the PDK; it is 246KB of real layer
# definitions and was simply never being loaded.
lyp = "{lyp}"
loaded_lyp = False
if lyp and os.path.exists(lyp):
    view.load_layer_props(lyp)
    loaded_lyp = True

view.zoom_fit()
view.save_image("/work/out.png", {size}, {size})
# Reported so the caller can tell a correctly-coloured render from a
# fallback one, rather than both looking equally authoritative.
print("LYP_LOADED=" + str(loaded_lyp))
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
        (tmp_path / "render.py").write_text(
            _KLAYOUT_SCRIPT.format(size=size, lyp=LYP_IN_CONTAINER))

        cmd = [
            "docker", "run", "--rm", "--platform", "linux/amd64",
            "-e", "QT_QPA_PLATFORM=offscreen",
            "-v", f"{PDK_ROOT}:/pdk:ro",
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
        if "LYP_LOADED=True" not in result.stdout:
            # Not fatal — a rendered image with default colours is still
            # better than none — but it must not pass silently as if it
            # were a correct sky130 rendering.
            print(f"warning: {LYP_IN_CONTAINER} not loaded; layer colours are "
                  f"KLayout defaults, not sky130's", file=sys.stderr)
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
