"""Capture sources before evaluation; refuse to reconstruct old provenance."""
import glob
import hashlib
import json
import re
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def capture_inputs(design_dir):
    design_dir = Path(design_dir).resolve()
    try:
        config = json.loads((design_dir / "config.json").read_text())
        files = config.get("VERILOG_FILES", [])
        if isinstance(files, str):
            files = [files]
        hashes = {}
        visited = set()
        def capture(path):
            path = Path(path).resolve()
            if path in visited:
                return
            visited.add(path)
            raw = path.read_bytes()
            hashes[str(path.relative_to(design_dir)) if path.is_relative_to(design_dir) else str(path)] = hashlib.sha256(raw).hexdigest()
            if re.search(r'\$readmem[hb]\s*\(', raw.decode("utf-8")):
                raise ValueError("external memory data needs explicit provenance")
            for line in raw.decode("utf-8").splitlines():
                if re.match(r'^\s*`include\b', line):
                    match = re.match(r'^\s*`include\s+"([^"]+)"', line)
                    if not match:
                        raise ValueError("macro-expanded RTL include needs explicit provenance")
                    capture(path.parent / match[1])
        if not isinstance(files, list) or not files:
            raise ValueError("RTL input paths unavailable")
        for raw in files:
            if not isinstance(raw, str) or raw.startswith(("expr::", "pdk::", "ref::")):
                raise ValueError("unresolved RTL expression")
            pattern = raw.removeprefix("dir::")
            matches = sorted(glob.glob(str(design_dir / pattern)))
            if not matches:
                raise ValueError(f"RTL input not found: {raw}")
            for name in matches:
                capture(name)
        rtl_hashes = dict(hashes)
        for path in sorted((design_dir / "verify").rglob("*")):
            if path.is_file() and path.suffix in {".v", ".sv", ".vh", ".svh"}:
                capture(path)
        return {"complete": True, "config_sha256": digest(config), "rtl_sha256": rtl_hashes,
                "verification_sha256": {key: value for key, value in hashes.items() if key not in rtl_hashes}}
    except (OSError, ValueError, TypeError) as error:
        return {"complete": False, "reason": str(error)}


def complete_context(design_dir, run_dir, candidate, inputs, toolchain, verify_fn, pdk_version):
    try:
        if not inputs.get("complete") or capture_inputs(design_dir) != inputs:
            raise ValueError("input snapshot missing or changed during evaluation")
        resolved = json.loads((run_dir / "resolved.json").read_text())
        # Read the selected installed PDK, rather than assuming the first
        # SKY130 version found is the active version for every technology.
        selected_pdk = Path(__file__).resolve().parents[1] / "pdk" / str(resolved.get("PDK", ""))
        selected_parts = selected_pdk.resolve().parts
        if "versions" in selected_parts:
            pdk_version = selected_parts[selected_parts.index("versions") + 1]
        elif selected_pdk.exists():
            pdk_version = None
        sdc_files = sorted((run_dir / "final" / "sdc").rglob("*.sdc"))
        if not sdc_files:
            raise ValueError("effective final SDC unavailable")
        sdc_hashes = sorted(hashlib.sha256(p.read_bytes()).hexdigest() for p in sdc_files)
        def paths(value):
            if isinstance(value, str):
                yield value
            elif isinstance(value, list):
                for item in value:
                    yield from paths(item)
            elif isinstance(value, dict):
                for item in value.values():
                    yield from paths(item)
        libs = list(paths(resolved.get("LIB")))
        libs += list(paths(resolved.get("MACROS", {})))
        libs = sorted({raw for raw in libs if raw.endswith((".lib", ".lib.gz"))})
        if not libs:
            raise ValueError("resolved Liberty inputs unavailable")
        lib_hashes = []
        def host_path(raw):
            if raw.startswith("/pdk/"):
                return Path(__file__).resolve().parents[1] / "pdk" / raw.removeprefix("/pdk/")
            if raw.startswith("/design/"):
                return design_dir / raw.removeprefix("/design/")
            return design_dir / raw.removeprefix("dir::")
        for raw in libs:
            path = host_path(raw)
            lib_hashes.append(hashlib.sha256(path.read_bytes()).hexdigest())
        macro_views = sorted({raw for raw in paths(resolved.get("MACROS", {}))
                              if raw.endswith((".lef", ".gds", ".v", ".spice", ".lef.gz", ".gds.gz"))})
        if resolved.get("MACROS") and not macro_views:
            raise ValueError("macro physical views unavailable")
        context = {"design": resolved.get("DESIGN_NAME"), "inputs": inputs,
                   "evaluator_sha256": {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
                                        for name in ("orchestrator.py", "verdict_scoring.py", "candidate_plan.py", "winner_selection.py", "repair_proposals.py", "power_activity.py", "equiv_check.py", "evaluation_provenance.py")},
                   "sdc_sha256": sdc_hashes, "liberty_sha256": sorted(lib_hashes),
                   "macro_views_sha256": sorted(hashlib.sha256(host_path(raw).read_bytes()).hexdigest() for raw in macro_views),
                   "pdk": resolved.get("PDK"), "scl": resolved.get("STD_CELL_LIBRARY"),
                   "image": toolchain.get("openlane_image"),
                   "pdk_version": pdk_version,
                   "flow": candidate.get("flow") or (resolved.get("meta") or {}).get("flow", "Classic"),
                   "semantic_overrides": {key: value for key, value in candidate.get("overrides", {}).items()
                                          if key not in {"SYNTH_STRATEGY", "FP_CORE_UTIL", "DIE_AREA", "PL_TARGET_DENSITY_PCT"}},
                   "openroad_revision": toolchain.get("expected_openroad_revision"),
                   "verification_policy": {"rtl_netlist_equivalence_requested": bool(verify_fn)}}
        if not all(context.get(key) for key in ("design", "pdk", "scl", "pdk_version", "image", "openroad_revision")):
            raise ValueError("resolved technology or tool version unavailable")
        custom = run_dir / "custom_flow_provenance.json"
        if context["flow"] != "Classic" and not custom.exists():
            raise ValueError("custom flow source snapshot unavailable")
        if custom.exists():
            data = json.loads(custom.read_text())
            context["flow_sources"] = data.get("sources_sha256")
            if not context["flow_sources"]:
                raise ValueError("custom flow source hashes unavailable")
        return {"complete": True, "compatibility_key": digest(context), "context": context}
    except (OSError, ValueError, TypeError, AttributeError) as error:
        return {"complete": False, "reason": str(error), "inputs": inputs}
