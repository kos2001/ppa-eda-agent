#!/usr/bin/env python3
r"""KLayout DRC for gf180mcu runs — the signoff OpenLane 2.3.10 skips.

The gap. `KLayout.DRC.run()` in the pinned image reads, verbatim:

    if self.config["PDK"] in ["sky130A", "sky130B"]:
        metrics_updates = self.run_sky130(state_in, **kwargs)
    else:
        self.warn(f"KLayout DRC is not supported for the {PDK} PDK. This step will be skipped.")

So on gf180mcu the step runs, logs a warning, and writes no
`klayout__drc_error__count`. score() treats an absent signoff metric as
"never checked" and puts it in `unverified`, which blocks a pass. That
is the right call for a metric — and it meant 170 gf180mcu runs in the
store, 0 passing, 139 of them for this reason alone. The technology
half of DTCO (tech_compare.py) had never reached signoff.

The check exists; only OpenLane's binding to it is missing. The gf180mcu
PDK ships GlobalFoundries' own KLayout rule deck at
`libs.tech/klayout/drc/` with a `run_drc.py` driver, and the image
carries KLayout 0.29.4, above the deck's 0.28.4 floor. The driver itself
cannot run there (it imports docopt, which the image does not have), so
this module does what its single-processor path does and nothing else:
concatenate `main.drc` + the rule tables + `tail.drc` into one runset,
copy `layers_def.drc` beside it, and call `klayout -b -r` with the same
`-rd` switches `generate_klayout_switches()` would build. The variant
letter selects the metal stack exactly as the driver's table does
(gf180mcuD: metal_top 11K, MIM option B, 5LM). Every choice here is
traceable to a line in that file rather than to memory.

Same precedent as equiv_check.py replacing a Yosys.EQY that does not
work: the pipeline's job is to produce the real number, not to report
that the tool declined to.

What the count means. `klayout__drc_error__count` is the number of
violation items in the result database, which is what OpenLane's own
`xml_drc_report_to_json.py` counts on sky130. Zero is a clean deck run,
not proof of manufacturability — density and antenna tables are off by
default in the driver too, and are left off here so the number matches
what the PDK's driver would report.

Usage:
    python3 pipeline/gf180_drc.py --run pipeline/designs/gcd/runs/<tag> --pdk gf180mcuD
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from toolchain import OPENLANE_IMAGE as IMAGE, platform_args

REPO_ROOT = Path(__file__).resolve().parent.parent
PDK_ROOT = REPO_ROOT / "pdk"

# run_drc.py's --variant table, copied rather than re-derived.
VARIANTS = {
    "A": {"metal_top": "30K", "mim_option": "A", "metal_level": "3LM"},
    "B": {"metal_top": "11K", "mim_option": "B", "metal_level": "4LM"},
    "C": {"metal_top": "9K", "mim_option": "B", "metal_level": "5LM"},
    "D": {"metal_top": "11K", "mim_option": "B", "metal_level": "5LM"},
    "E": {"metal_top": "9K", "mim_option": "B", "metal_level": "6LM"},
    "F": {"metal_top": "9K", "mim_option": "A", "metal_level": "6LM"},
}

# Tables the driver leaves out of the default run (generate_drc_run_template).
_NOT_A_TABLE = ("antenna", "density", "main", "layers_def", "tail")

# Where this module keeps its work inside a run directory. No `NN-`
# prefix on purpose: step_coverage.py reads `^\d+-` directories as
# executed OpenLane steps, and this is not one.
WORK_DIR = "gf180_klayout_drc"


def variant_for(pdk: str) -> str:
    """gf180mcuD -> "D". Raises for anything that is not a gf180mcu PDK."""
    if not pdk or not pdk.startswith("gf180mcu") or len(pdk) != len("gf180mcuD"):
        raise ValueError(f"not a gf180mcu PDK variant: {pdk!r}")
    letter = pdk[-1]
    if letter not in VARIANTS:
        raise ValueError(f"unknown gf180mcu variant {letter!r} in {pdk!r}")
    return letter


def deck_dir(pdk: str) -> Path:
    return PDK_ROOT / pdk / "libs.tech" / "klayout" / "drc"


def rule_tables(deck: Path) -> list[str]:
    """The tables the driver runs by default, in its order."""
    return sorted(
        p.stem for p in (deck / "rule_decks").glob("*.drc")
        if all(t not in p.name for t in _NOT_A_TABLE)
    )


def build_runset(deck: Path, work: Path) -> Path:
    """Mirrors generate_drc_run_template() with no tables named: main +
    every default table + tail, and layers_def.drc alongside."""
    work.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(deck / "rule_decks" / "layers_def.drc", work / "layers_def.drc")
    runset = work / "main.drc"
    with runset.open("wb") as out:
        for name in ["main"] + rule_tables(deck) + ["tail"]:
            with (deck / "rule_decks" / f"{name}.drc").open("rb") as src:
                shutil.copyfileobj(src, out)
    return runset


def switches(variant: str, *, input_path: str, topcell: str, report: str,
             threads: int) -> dict[str, str]:
    """The -rd switches generate_klayout_switches() + run_check() pass,
    with the driver's defaults (no --no_*, no --density, flat mode)."""
    sw = dict(VARIANTS[variant])
    sw.update({
        "thr": str(threads),
        "verbose": "false",
        "feol": "true",
        "beol": "true",
        "offgrid": "true",
        "conn_drc": "true",
        "density": "false",
        "split_deep": "false",
        "slow_via": "false",
        "topcell": topcell,
        "input": input_path,
        "report": report,
        "run_mode": "flat",
        "table_name": "main",
    })
    return sw


def final_gds(run_dir: Path) -> Path:
    final = run_dir / "final" / "gds"
    files = sorted(final.glob("*.gds")) if final.is_dir() else []
    if not files:
        raise FileNotFoundError(f"no final GDS under {final} — the run did not "
                                f"reach streamout")
    return files[0]


def topcell_name(run_dir: Path, gds: Path) -> str:
    """DESIGN_NAME from the run's own resolved.json, else the GDS stem."""
    try:
        resolved = json.loads((run_dir / "resolved.json").read_text(encoding="utf-8"))
        name = resolved.get("DESIGN_NAME")
        if isinstance(name, str) and name:
            return name
    except (OSError, json.JSONDecodeError):
        pass
    return gds.stem


def count_violations(lyrdb: Path) -> dict:
    """Items and per-rule counts from a KLayout report database.

    The driver's get_rules_with_violations() indexes the root's eighth
    child for the item list; this walks the tree by tag instead so a
    reordering of the XML cannot silently return the wrong element.
    """
    root = ET.parse(lyrdb).getroot()
    per_rule: dict[str, int] = {}
    total = 0
    for item in root.iter("item"):
        cat = item.findtext("category") or "?"
        cat = cat.strip().strip("'")
        per_rule[cat] = per_rule.get(cat, 0) + 1
        total += 1
    return {"count": total, "per_rule": dict(sorted(per_rule.items()))}


def run(run_dir: Path | str, pdk: str, threads: int = 4,
        timeout_s: int = 3600) -> dict:
    """Runs the gf180mcu deck on a completed run's final GDS and returns
    {"count", "per_rule", "report", "variant", "tables"}.

    Raises on anything that stops a real number from being produced —
    a missing GDS, a missing deck, Docker unavailable, KLayout failing —
    so the caller can leave the metric absent (unverified) rather than
    record a zero nobody measured.
    """
    run_dir = Path(run_dir).resolve()
    variant = variant_for(pdk)
    deck = deck_dir(pdk)
    if not (deck / "rule_decks" / "main.drc").is_file():
        raise FileNotFoundError(f"no KLayout DRC deck at {deck}")
    gds = final_gds(run_dir)
    topcell = topcell_name(run_dir, gds)
    work = run_dir / WORK_DIR
    build_runset(deck, work)
    report_name = f"{gds.stem}_main.lyrdb"

    # Container paths: the run dir is mounted at /run, so every path
    # KLayout sees is inside it and the report lands beside the runset.
    sw = switches(variant,
                  input_path=f"/run/{gds.relative_to(run_dir).as_posix()}",
                  topcell=topcell,
                  report=f"/run/{WORK_DIR}/{report_name}",
                  threads=threads)
    cmd = ["docker", "run", "--rm", *platform_args(),
           "-v", f"{PDK_ROOT}:/pdk:ro",
           "-v", f"{run_dir}:/run",
           "-w", f"/run/{WORK_DIR}",
           IMAGE, "klayout", "-b", "-r", f"/run/{WORK_DIR}/main.drc"]
    for k, v in sw.items():
        cmd += ["-rd", f"{k}={v}"]
    print(f"$ docker run … klayout -b -r main.drc (gf180mcu variant {variant}, "
          f"{topcell})", file=sys.stderr)
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout_s)
    (work / "klayout.log").write_text(proc.stdout + proc.stderr, encoding="utf-8")
    report = work / report_name
    if proc.returncode != 0 or not report.is_file():
        raise RuntimeError(f"KLayout DRC did not produce {report.name} "
                           f"(exit {proc.returncode}); see {work / 'klayout.log'}")
    result = count_violations(report)
    result.update({
        "report": str(report),
        "variant": variant,
        "tables": rule_tables(deck),
        "source": "gf180mcu PDK klayout/drc rule deck, driven as run_drc.py "
                  "would (OpenLane 2.3.10's KLayout.DRC skips non-sky130 PDKs)",
    })
    (work / "summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True, type=Path, help="a completed run directory")
    ap.add_argument("--pdk", default="gf180mcuD")
    ap.add_argument("--threads", type=int, default=4)
    args = ap.parse_args()
    result = run(args.run, args.pdk, threads=args.threads)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
