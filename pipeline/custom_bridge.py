#!/usr/bin/env python3
"""The custom/analog half of the flow — a bridge to the open-source
counterpart of Cadence Virtuoso, speaking virtuoso-bridge-lite's contract.

Why this exists. Everything else in this pipeline is the *digital*
implementation flow: RTL in, OpenLane2/OpenROAD placement and routing out,
signed off with Magic/KLayout/netgen/OpenSTA. That is the open-source
answer to Innovus / Fusion Compiler / Calibre. It is not the answer to
Virtuoso, and Virtuoso is what a custom or mixed-signal designer actually
sits in all day: schematic capture, transistor-level simulation, custom
polygon layout, and LVS back to the schematic. This repo had no path to
any of that — `sim/` runs OpenSTA, which is a static timing tool with no
transistor in it anywhere.

On virtuoso-bridge-lite. `~/gitspace/virtuso_bridge/virtuoso-bridge-lite`
(github.com/Arcadia-1/virtuoso-bridge-lite, MIT) solves the same shape of
problem from the commercial side: an agent drives a real Virtuoso over an
SSH tunnel, executing SKILL and reading Maestro/Spectre results back as a
typed `VirtuosoResult`. Measured on this machine on 2026-09-12, installed
from source into a scratch venv:

    virtuoso-bridge status   -> "No profiles found. Set VB_REMOTE_HOST ..."
    virtuoso-bridge license  -> "VB_CADENCE_CSHRC is not set."
    command -v virtuoso      -> not found
    command -v spectre       -> not found

So it cannot be *run* here: both of its backends (Virtuoso over SKILL,
Spectre standalone) need Cadence software and a license server this
machine has no access to. That is a fact about the environment, not a
judgement about the package — on a host with a Virtuoso session it is the
right tool, and nothing here replaces it.

What is borrowed is the part that does apply: its interface. Its
`models.py` declares `VirtuosoInterface` as an ABC over
`ensure_ready / execute_skill / test_connection` returning a
`VirtuosoResult(status, output, errors, warnings, execution_time,
metadata)`. Those four `ExecutionStatus` values are reproduced here
exactly — including the decision NOT to add a fifth for "tool not
installed", which is reported as ERROR with `metadata["reason"] =
"not_installed"` so a caller written against either bridge keeps working.
soul.md's "borrow the working part, not the whole machine": the contract
earns its place, pydantic and the SSH tunnel do not.

What replaces SKILL. Virtuoso's counterpart here is not one program, it
is the Open Circuit Design stack the PDKs in `pdk/` already carry tech
files for (`libs.tech/{xschem,magic,klayout,netgen,ngspice}` under both
sky130A and gf180mcuD). Each one has its own batch scripting language,
which is what `evaluate()` dispatches into:

    backend   Virtuoso equivalent                     language
    xschem    Schematic Editor (Composer)             Tcl
    magic     Layout Suite (custom polygon editing)   Tcl
    klayout   Layout Suite viewer + PVS/Calibre deck  Python
    ngspice   ADE + Spectre                           .control block
    netgen    Assura / PVS LVS                        Tcl

Real, or say so. `probe()` reports what is actually resolvable on this
host and never pretends otherwise. Measured here on 2026-09-12: ngspice
46 and netgen 1.5.323 are installed locally; magic, klayout and xschem
are not (the /Applications/KLayout directory is an unextracted
"MacStdUser-ReadMeFirst" folder, not a binary — which is why resolution
probes for an executable rather than trusting a path). magic, klayout and
netgen are additionally reachable through the pinned OpenLane image this
pipeline already runs, so `probe(docker=True)` asks the container and
`evaluate()` falls back to it — measured: magic loads the real
`sky130A.tech` from the mounted PDK in 0.24 s, klayout 0.29.4 answers in
1.6 s. Only xschem is genuinely absent here; it ships in
IIC-OSIC-TOOLS, a separate image this repo deliberately does not pull.

First real analog measurement in this repo, produced by `run_spice()`
against `pdk/sky130A/libs.tech/ngspice/sky130.lib.spice` at tt: a
sky130 inverter (pfet W=1.0, nfet W=0.5, L=0.15) switches at
vtrip = 0.8676 V on a 1.8 V supply. No Docker, no license, no mock.

Usage:
    custom_bridge.py status [--docker]
    custom_bridge.py eval --backend magic --code 'puts [tech name]'
    custom_bridge.py spice --netlist inv.spice --corner tt --pdk sky130A
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from toolchain import OPENLANE_IMAGE, platform_args

REPO_ROOT = Path(__file__).resolve().parent.parent
PDK_ROOT = REPO_ROOT / "pdk"


# ---------------------------------------------------------------------------
# Result contract — field-for-field virtuoso-bridge-lite's VirtuosoResult
# ---------------------------------------------------------------------------

class ExecutionStatus:
    """virtuoso-bridge-lite's four statuses, as plain strings.

    Not an enum subclass of anything, and deliberately not extended: a
    fifth value like "unavailable" would be more precise and would also
    be the one thing that breaks a caller written against the commercial
    bridge. Absence is ERROR plus a `reason` in metadata.
    """

    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"
    ERROR = "error"


@dataclass
class BridgeResult:
    """What every call here returns. Same field names as VirtuosoResult."""

    status: str
    output: str = ""
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    execution_time: float | None = None
    metadata: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == ExecutionStatus.SUCCESS

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "output": self.output,
            "errors": self.errors,
            "warnings": self.warnings,
            "execution_time": self.execution_time,
            "metadata": self.metadata,
        }

    def save_json(self, path, *, indent: int = 2) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=indent), encoding="utf-8")


# ---------------------------------------------------------------------------
# Backends — the open-source stack that stands in for Virtuoso
# ---------------------------------------------------------------------------

# `argv` is a function of the script path so each tool's own batch
# convention stays visible rather than being normalised away. These are
# the non-interactive invocations: every one of them must exit on its
# own, because an agent calling this has no keyboard to rescue a tool
# that dropped into its own prompt. (`netgen -batch` with no file does
# exactly that — it was found by hanging this session's shell for two
# minutes, which is why every call below also gets stdin closed.)
BACKENDS: dict[str, dict] = {
    "xschem": {
        "virtuoso_equivalent": "Virtuoso Schematic Editor (Composer)",
        "language": "Tcl",
        "exe": ["xschem"],
        "argv": lambda s: ["xschem", "-q", "-n", "-s", "--script", str(s)],
        "in_openlane_image": False,
        "note": "Not in the OpenLane image; ships in hpretl/iic-osic-tools.",
    },
    "magic": {
        "virtuoso_equivalent": "Virtuoso Layout Suite (custom polygon edit)",
        "language": "Tcl",
        "exe": ["magic"],
        "argv": lambda s: ["magic", "-dnull", "-noconsole", str(s)],
        "in_openlane_image": True,
        "note": "-dnull -noconsole is the headless pair; either alone still "
                "opens a window or a prompt.",
    },
    "klayout": {
        "virtuoso_equivalent": "Virtuoso Layout Suite viewer + PVS/Calibre deck runner",
        "language": "Python",
        "exe": ["klayout"],
        "argv": lambda s: ["klayout", "-b", "-r", str(s)],
        "in_openlane_image": True,
        "note": "-b is batch; without it -r still raises a GUI.",
    },
    "ngspice": {
        "virtuoso_equivalent": "ADE Explorer / Spectre",
        "language": "SPICE deck with a .control block",
        "exe": ["ngspice"],
        "argv": lambda s: ["ngspice", "-b", str(s)],
        "in_openlane_image": False,
        "note": "Installed locally here (46, KLU). The .control block must "
                "end in `quit` or -b still waits.",
    },
    "netgen": {
        "virtuoso_equivalent": "Assura / PVS LVS",
        "language": "Tcl",
        "exe": ["netgen"],
        "argv": lambda s: ["netgen", "-batch", "source", str(s)],
        "in_openlane_image": True,
        "note": "`-batch source FILE`, not `-batch FILE` — the latter reads "
                "stdin and hangs.",
    },
}

# Per-PDK SPICE corner section names. Not cosmetic: sky130's typical
# section is `tt` and gf180mcu's is `typical`, so a deck written for one
# PDK silently fails to find its models in the other. Read out of the
# real model files in pdk/ (sky130.lib.spice, sm141064.ngspice), not
# from memory.
SPICE_CORNERS: dict[str, dict[str, str]] = {
    "sky130": {"tt": "tt", "ss": "ss", "ff": "ff", "sf": "sf", "fs": "fs"},
    "gf180mcu": {"tt": "typical", "ss": "ss", "ff": "ff", "sf": "sf", "fs": "fs"},
}


def _pdk_family(pdk: str) -> str:
    return "gf180mcu" if pdk.startswith("gf180") else "sky130"


def spice_lib(pdk: str, corner: str = "tt") -> tuple[Path, str]:
    """The model library file and section name for one PDK corner.

    Raises rather than guessing: a deck that includes the wrong section
    still simulates, it just simulates the wrong silicon, and nothing
    downstream would flag it.
    """
    family = _pdk_family(pdk)
    sections = SPICE_CORNERS[family]
    if corner not in sections:
        raise ValueError(f"{pdk}: unknown corner {corner!r}; have {sorted(sections)}")
    if family == "sky130":
        lib = PDK_ROOT / pdk / "libs.tech" / "ngspice" / "sky130.lib.spice"
    else:
        lib = PDK_ROOT / pdk / "libs.tech" / "ngspice" / "sm141064.ngspice"
    return lib, sections[corner]


# ---------------------------------------------------------------------------
# Tool resolution
# ---------------------------------------------------------------------------

def resolve(backend: str) -> str | None:
    """Absolute path to a backend's executable on this host, or None.

    Order: an explicit `CUSTOM_BRIDGE_<BACKEND>` override, then PATH.
    Nothing else is searched — a well-known install directory that
    happens to exist is not evidence a binary is in it, which is exactly
    how /Applications/KLayout reads as "installed" on this machine while
    containing no klayout at all.
    """
    spec = BACKENDS[backend]
    override = os.environ.get(f"CUSTOM_BRIDGE_{backend.upper()}", "").strip()
    if override:
        return override if Path(override).exists() else None
    for name in spec["exe"]:
        found = shutil.which(name)
        if found:
            return found
    return None


def _version(backend: str, exe: str) -> str | None:
    """One line identifying the build, for the record. Never fatal."""
    probes = {
        "ngspice": ([exe, "-v"], 15),
        "netgen": ([exe, "-batch", "quit"], 15),
        "magic": ([exe, "--version"], 15),
        "klayout": ([exe, "-v"], 15),
        "xschem": ([exe, "--version"], 15),
    }
    argv, timeout = probes.get(backend, ([exe, "--version"], 15))
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    # Skip banner rules: `ngspice -v` opens with a line of asterisks, and
    # reporting "******" as the tool version is worse than reporting
    # nothing — it looks like an answer.
    for line in (r.stdout + r.stderr).splitlines():
        line = line.strip().lstrip("*").strip()
        if any(c.isalnum() for c in line):
            return line[:120]
    return None


def _docker_has(tool: str, timeout: int = 120) -> bool:
    """Whether the pinned OpenLane image can run `tool`. Asked, not assumed."""
    if not shutil.which("docker"):
        return False
    argv = ["docker", "run", "--rm", *platform_args(), OPENLANE_IMAGE,
            "bash", "-lc", f"command -v {tool}"]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


# ---------------------------------------------------------------------------
# Output parsing (pure — this is what the tests pin)
# ---------------------------------------------------------------------------

# Anchored at line start and NOT at line end. `meas ... when` prints
# `vtrip = 8.67e-01` alone, but `meas ... trig/targ` prints
# `tphl = 6.63e-11 targ= 1.11e-09 trig= 1.05e-09` — an end-anchored
# pattern silently returns only the DC measurement and every delay
# number goes missing with nothing reporting it.
_MEAS_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_.]*)\s*=\s*"
    r"([-+]?[0-9.]+(?:[eE][-+]?[0-9]+)?)(?=\s|$)")


def parse_measurements(text: str) -> dict:
    """`.meas` results out of an ngspice batch log.

    Every line starts `name = value`; some then carry the measure's own
    context (`targ=`, `trig=`, `from=`) which is deliberately not read
    back as further measurements.
    """
    out: dict = {}
    for line in text.splitlines():
        m = _MEAS_RE.match(line)
        if m:
            out[m.group(1)] = float(m.group(2))
    return out


def parse_problems(text: str) -> tuple[list, list]:
    """(errors, warnings) from a batch log, tool-agnostic.

    `failed!` is in the error set because of a real case: ngspice exits 0
    after `meas dc vtrip when v(out)=0.9 rise=1 failed!` — the measure
    that the whole run existed to take did not happen, and the exit code
    says nothing. Returncode alone is not a verdict for these tools.
    """
    errors, warnings = [], []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        low = s.lower()
        if low.startswith("error") or "fatal" in low or "failed!" in low:
            errors.append(s[:300])
        elif low.startswith("warning"):
            warnings.append(s[:300])
    return errors, warnings


def classify(returncode: int, errors: list) -> str:
    """Map an exit code plus parsed errors onto the four statuses.

    PARTIAL is the interesting one: it is what a tool that finished, was
    asked several things, and silently dropped one of them looks like.
    Collapsing that into SUCCESS is how a missing measurement becomes a
    number someone trusts.
    """
    if returncode != 0:
        return ExecutionStatus.FAILURE
    return ExecutionStatus.PARTIAL if errors else ExecutionStatus.SUCCESS


# ---------------------------------------------------------------------------
# The bridge itself
# ---------------------------------------------------------------------------

_SUFFIX = {"xschem": ".tcl", "magic": ".tcl", "netgen": ".tcl",
           "klayout": ".py", "ngspice": ".spice"}


def probe(docker: bool = False) -> dict:
    """What of the Virtuoso-equivalent stack this host can actually run."""
    report = {}
    for name, spec in BACKENDS.items():
        exe = resolve(name)
        entry = {
            "virtuoso_equivalent": spec["virtuoso_equivalent"],
            "language": spec["language"],
            "available": exe is not None,
            "path": exe,
            "version": _version(name, exe) if exe else None,
            "via": "local" if exe else None,
            "note": spec["note"],
        }
        if exe is None and docker and spec["in_openlane_image"]:
            if _docker_has(spec["exe"][0]):
                entry.update(available=True, via="docker", path=OPENLANE_IMAGE)
        report[name] = entry
    return report


def status(docker: bool = False) -> BridgeResult:
    """`virtuoso-bridge status`'s counterpart, for the open-source stack."""
    started = time.time()
    report = probe(docker=docker)
    have = [n for n, e in report.items() if e["available"]]
    missing = [n for n, e in report.items() if not e["available"]]
    lines = [f"{n:<8} {'OK ' if report[n]['available'] else '-- '}"
             f"{report[n]['via'] or 'not installed':<8} "
             f"{report[n]['version'] or report[n]['virtuoso_equivalent']}"
             for n in BACKENDS]
    return BridgeResult(
        status=ExecutionStatus.SUCCESS if have else ExecutionStatus.FAILURE,
        output="\n".join(lines),
        warnings=[f"{n}: not resolvable on this host" for n in missing],
        execution_time=round(time.time() - started, 3),
        metadata={"backends": report, "available": have, "missing": missing,
                  "pdks": sorted(p.name for p in PDK_ROOT.glob("*")
                                 if (p / "libs.tech").is_dir())},
    )


def evaluate(backend: str, code: str, timeout: int = 120) -> BridgeResult:
    """Run a script in one backend's own language — `execute_skill`'s analogue.

    SKILL is one language because Virtuoso is one program. The
    open-source equivalent is five programs and three languages, so the
    backend is an argument rather than something inferred: guessing which
    tool a fragment of Tcl was meant for is a way to run layout commands
    in an LVS engine and get a confusing error instead of a clear one.
    """
    if backend not in BACKENDS:
        return BridgeResult(status=ExecutionStatus.ERROR,
                            errors=[f"unknown backend {backend!r}; "
                                    f"have {sorted(BACKENDS)}"],
                            metadata={"reason": "unknown_backend"})
    spec = BACKENDS[backend]
    exe = resolve(backend)
    via = "local"
    if exe is None:
        # Falling back to the container is not a nicety — `status()`
        # reports magic and klayout as reachable through the pinned
        # OpenLane image on this host, and a bridge that advertises a
        # backend as available and then refuses to run it is worse than
        # one that never offered it.
        if not (spec["in_openlane_image"] and shutil.which("docker")):
            return BridgeResult(
                status=ExecutionStatus.ERROR,
                errors=[f"{backend} is not installed on this host"],
                metadata={"reason": "not_installed", "backend": backend,
                          "in_openlane_image": spec["in_openlane_image"],
                          "note": spec["note"]},
            )
        via = "docker"

    started = time.time()
    with tempfile.TemporaryDirectory() as td:
        script = Path(td) / f"bridge{_SUFFIX[backend]}"
        script.write_text(code, encoding="utf-8")
        if via == "docker":
            inner = spec["argv"](f"/work/{script.name}")
            argv = ["docker", "run", "--rm", *platform_args(),
                    # Offscreen or a GUI tool in a container blocks on a
                    # display it will never get; the PDK is read-only
                    # because a tech file is an input, never an output.
                    "-e", "QT_QPA_PLATFORM=offscreen",
                    "-v", f"{PDK_ROOT}:/pdk:ro", "-v", f"{td}:/work",
                    "-w", "/work", OPENLANE_IMAGE, *inner]
            exe = OPENLANE_IMAGE
        else:
            argv = [exe, *spec["argv"](script)[1:]]
        try:
            r = subprocess.run(argv, capture_output=True, text=True,
                               timeout=timeout, stdin=subprocess.DEVNULL, cwd=td)
        except subprocess.TimeoutExpired:
            return BridgeResult(status=ExecutionStatus.ERROR,
                                errors=[f"{backend} did not exit within {timeout}s"],
                                execution_time=round(time.time() - started, 3),
                                metadata={"reason": "timeout", "argv": argv})
        except OSError as exc:
            return BridgeResult(status=ExecutionStatus.ERROR, errors=[str(exc)],
                                metadata={"reason": "exec_failed", "argv": argv})

    text = r.stdout + r.stderr
    errors, warnings = parse_problems(text)
    return BridgeResult(
        status=classify(r.returncode, errors),
        output=text,
        errors=errors,
        warnings=warnings,
        execution_time=round(time.time() - started, 3),
        metadata={"backend": backend, "language": spec["language"],
                  "virtuoso_equivalent": spec["virtuoso_equivalent"],
                  "returncode": r.returncode, "exe": exe, "via": via},
    )


PDK_LIB_TOKEN = "%PDK_LIB%"


def bind_corner(netlist: str, pdk: str, corner: str) -> str:
    """Substitute `%PDK_LIB%` for a real `.lib` line, or leave the deck alone.

    A token rather than an injected header: a deck that already names its
    own models is a deck whose author made a choice, and quietly
    prepending a second `.lib` on top of it would shadow that choice with
    no error anywhere.
    """
    if PDK_LIB_TOKEN not in netlist:
        return netlist
    lib, section = spice_lib(pdk, corner)
    return netlist.replace(PDK_LIB_TOKEN, f".lib {lib} {section}")


def run_spice(netlist: Path | str, pdk: str = "sky130A", corner: str = "tt",
              timeout: int = 300) -> BridgeResult:
    """A real transistor-level simulation — the ADE/Spectre counterpart.

    Returns the parsed `.meas` values in `metadata["measurements"]`, which
    is the whole point: an agent comparing two device sizings needs the
    numbers, not a log to re-read.
    """
    netlist = Path(netlist)
    if not netlist.is_file():
        return BridgeResult(status=ExecutionStatus.ERROR,
                            errors=[f"no such netlist: {netlist}"],
                            metadata={"reason": "missing_netlist"})
    deck = bind_corner(netlist.read_text(encoding="utf-8"), pdk, corner)
    result = evaluate("ngspice", deck, timeout=timeout)
    result.metadata.update({
        "netlist": str(netlist), "pdk": pdk, "corner": corner,
        "measurements": parse_measurements(result.output),
    })
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    # --json hangs off every subcommand rather than the top level: as a
    # top-level flag argparse only accepts it *before* the subcommand,
    # so the natural `... eval --backend magic --json` is rejected.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="emit the full result")

    p_status = sub.add_parser("status", parents=[common],
                              help="what of the stack this host can run")
    p_status.add_argument("--docker", action="store_true",
                          help="also ask the pinned OpenLane image (slow)")

    p_eval = sub.add_parser("eval", parents=[common], help="run a script in one backend")
    p_eval.add_argument("--backend", required=True, choices=sorted(BACKENDS))
    p_eval.add_argument("--code", help="script text; omit to read stdin")
    p_eval.add_argument("--timeout", type=int, default=120)

    p_spice = sub.add_parser("spice", parents=[common], help="run a real transistor-level sim")
    p_spice.add_argument("--netlist", required=True, type=Path)
    p_spice.add_argument("--pdk", default="sky130A")
    p_spice.add_argument("--corner", default="tt")
    p_spice.add_argument("--timeout", type=int, default=300)

    args = ap.parse_args()

    if args.cmd == "status":
        r = status(docker=args.docker)
    elif args.cmd == "eval":
        code = args.code if args.code is not None else sys.stdin.read()
        r = evaluate(args.backend, code, timeout=args.timeout)
    else:
        r = run_spice(args.netlist, pdk=args.pdk, corner=args.corner,
                      timeout=args.timeout)

    if args.json:
        print(json.dumps(r.to_dict(), indent=2))
    else:
        print(r.output or "")
        for e in r.errors:
            print(f"ERROR   {e}", file=sys.stderr)
        meas = r.metadata.get("measurements")
        if meas:
            print("\nmeasurements:")
            for k, v in meas.items():
                print(f"  {k:<20} {v:g}")
        print(f"\nstatus: {r.status}"
              + (f"  ({r.execution_time}s)" if r.execution_time else ""))
    sys.exit(0 if r.ok else 1)


if __name__ == "__main__":
    main()
