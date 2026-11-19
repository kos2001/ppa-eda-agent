"""Separate renderer preparation from content-validated schematic generation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import custom_bridge as bridge
from schematic_inspect import sheet_path


def renderer_identity():
    exe = bridge.resolve("xschem")
    if exe:
        return {"exe": exe, "sha256": hashlib.sha256(Path(exe).read_bytes()).hexdigest(),
                "version": bridge._version("xschem", exe)}
    result = subprocess.run(["docker", "image", "inspect", bridge.XSCHEM_IMAGE,
                             "--format", "{{.Id}}"], capture_output=True, text=True, timeout=30)
    if result.returncode or not result.stdout.strip():
        raise RuntimeError("Schematic renderer image is unavailable; retry preparation")
    return {"image": bridge.XSCHEM_IMAGE, "id": result.stdout.strip()}


def prepare():
    if not bridge.resolve("xschem"):
        if not shutil.which("docker"):
            raise RuntimeError("xschem and Docker are unavailable in the selected environment")
        diagnostics = []
        if not bridge.ensure_xschem_image(timeout=900, diagnostics=diagnostics):
            raise RuntimeError("Renderer image preparation failed: " +
                               ("\n".join(diagnostics) or "check Docker and network access"))
    return {"status": "ready", "renderer": renderer_identity()}


def input_key(sheet, circuit_only, identity):
    # Include symbol libraries and Tcl configuration, not just the sheet mtime.
    # Hashing contents also catches restored timestamps and replaced images.
    files = {sheet, Path(bridge.__file__), Path(__file__), bridge.XSCHEM_DOCKERFILE}
    for directory in [bridge.PDK_ROOT / "sky130A" / "libs.tech" / "xschem",
                      bridge.ANALOG_ROOT, sheet.parent]:
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if path.suffix in {".sym", ".tcl"} or path.name == "xschemrc":
                files.add(path)
    digest = hashlib.sha256(json.dumps([circuit_only, identity], sort_keys=True).encode())
    for path in sorted(files, key=str):
        digest.update(str(path).encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def cached_render(cell, circuit_only):
    sheet = sheet_path(cell)
    output = sheet.with_suffix(".circuit.svg" if circuit_only else ".svg")
    manifest = output.with_suffix(output.suffix + ".cache.json")
    identity = renderer_identity()
    key = input_key(sheet, circuit_only, identity)
    try:
        previous = json.loads(manifest.read_text(encoding="utf-8"))
        if previous["key"] == key and previous["svg_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest():
            bridge.validate_svg(output)
            return {"status": "success", "cached": True, "key": key, "svg_sha256": previous["svg_sha256"]}
    except (OSError, ValueError, KeyError, TypeError, bridge.ET.ParseError):
        pass
    result = bridge.render_schematic(sheet, out=output, circuit_only=circuit_only)
    if not result.ok:
        return result.to_dict()
    if input_key(sheet, circuit_only, renderer_identity()) != key:
        return {"status": "error", "errors": ["Drawing inputs changed during rendering; retry"],
                "metadata": {"reason": "inputs_changed"}}
    data = {"key": key, "svg_sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "renderer": identity}
    fd, temporary = tempfile.mkstemp(prefix=".svg-cache-", dir=output.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream)
        os.replace(temporary, manifest)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {"status": "success", "cached": False, "key": key, "svg_sha256": data["svg_sha256"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "render"])
    parser.add_argument("--cell")
    parser.add_argument("--circuit", action="store_true")
    args = parser.parse_args()
    # Run both preparation and cache validation where xschem/Docker actually live.
    if sys.platform == "win32" and not bridge.resolve("xschem") and not shutil.which("docker"):
        wsl = shutil.which("wsl.exe")
        if not wsl:
            raise RuntimeError("Install xschem, Docker, or a WSL rendering environment")
        worker = "import runpy,subprocess,sys; p=subprocess.check_output(['wslpath','-a','-u',sys.argv[1]],text=True).strip(); sys.path.insert(0,p); sys.argv=[p+'/schematic_service.py']+sys.argv[2:]; runpy.run_path(sys.argv[0],run_name='__main__')"
        command = [wsl]
        distro = os.environ.get("CUSTOM_BRIDGE_WSL_DISTRO", "").strip()
        if distro:
            command += ["--distribution", distro]
        command += ["--exec", "python3", "-c", worker, str(Path(__file__).parent), *sys.argv[1:]]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=960 if args.action == "prepare" else 330)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "WSL execution failed")
        print(json.dumps(json.loads(result.stdout)))
        return
    print(json.dumps(prepare() if args.action == "prepare" else cached_render(args.cell, args.circuit)))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"status": "error", "errors": [str(exc)], "metadata": {"reason": "renderer_unavailable"}}))
