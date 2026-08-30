r"""Files a PDK is expected to ship, created when it does not.

OpenLane derives some per-library paths by convention rather than
reading them from the PDK's config, and validates them while loading the
PDK — before any config override is applied. A library missing one is
not merely degraded, it is unusable: the flow exits during PDK load with
"Path provided for variable 'PNR_EXCLUDED_CELL_FILE' is invalid" and no
run directory at all.

Measured case. gf180mcu ships two standard cell libraries.
gf180mcu_fd_sc_mcu7t5v0 has libs.tech/openlane/<scl>/drc_exclude.cells
(two cells: mux2_1 and oai33_2). gf180mcu_fd_sc_mcu9t5v0 has no such
file, so every attempt to use the 9-track library fails identically
regardless of the design, and passing
--override-config PNR_EXCLUDED_CELL_FILE=... does not help, because PDK
loading fails first.

WHAT THIS DOES AND DOES NOT CLAIM. It creates an empty list, which
asserts nothing about which 9-track cells are DRC-clean — only that the
file exists so the library can be run at all. If a rule turns up the way
licon.11 did for sky130_fd_sc_hs, the offending cells belong in this
file and the evidence belongs in a case.

DELIBERATELY EXPLICIT. Nothing calls this automatically. It writes into
a downloaded PDK, which volare will overwrite on a re-fetch and which a
teammate's checkout will not have — so it is a step someone runs and can
see, not a side effect of running a flow.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PDK_ROOT = REPO_ROOT / "pdk" / "volare"

# The note left in any file this creates, so it is obvious the PDK did
# not ship it.
STUB_HEADER = """\
# Created by pipeline/pdk_repair.py, not shipped by the PDK.
#
# OpenLane resolves PNR_EXCLUDED_CELL_FILE to this path by convention and
# validates it while loading the PDK, before config overrides apply. With
# the file absent the library cannot be used at all — the flow exits
# during PDK load and writes no run directory.
#
# Empty on purpose. This asserts nothing about which cells are DRC-clean;
# it only lets the library run. Cells found to violate belong here, with
# the measurement recorded in reference-db.
"""

EXPECTED_FILES = ("drc_exclude.cells",)


def scl_dirs(pdk_family: str) -> list[Path]:
    """Every <version>/<variant>/libs.tech/openlane/<scl> in a family."""
    root = PDK_ROOT / pdk_family / "versions"
    if not root.is_dir():
        return []
    out = []
    for version in sorted(root.iterdir()):
        for variant in sorted(p for p in version.iterdir() if p.is_dir()):
            tech = variant / "libs.tech" / "openlane"
            if not tech.is_dir():
                continue
            out.extend(sorted(d for d in tech.iterdir()
                              if d.is_dir() and "_sc_" in d.name))
    return out


def missing(pdk_family: str) -> list[Path]:
    """Paths OpenLane will look for and not find."""
    gaps = []
    for scl in scl_dirs(pdk_family):
        for name in EXPECTED_FILES:
            if not (scl / name).is_file():
                gaps.append(scl / name)
    return gaps


def repair(pdk_family: str, apply: bool = False) -> dict:
    """Report the gaps, and optionally fill them.

    Reporting is the default. Writing into a PDK is the kind of thing
    that should be asked for rather than assumed.
    """
    gaps = missing(pdk_family)
    written = []
    if apply:
        for path in gaps:
            path.write_text(STUB_HEADER)
            written.append(path)
    return {
        "pdk_family": pdk_family,
        "libraries_checked": len(scl_dirs(pdk_family)),
        "missing": [_short(p) for p in gaps],
        "written": [_short(p) for p in written],
    }


def _short(path: Path) -> str:
    """Repo-relative when it can be, absolute otherwise.

    relative_to() raises for anything outside the checkout, which is not
    hypothetical: PDK_ROOT is overridable and a PDK installed elsewhere
    would have made this crash while reporting, after having already
    written the files.
    """
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdk", default="gf180mcu",
                    help="PDK family under pdk/volare (default: gf180mcu)")
    ap.add_argument("--apply", action="store_true",
                    help="create the missing files (default: report only)")
    args = ap.parse_args()

    got = repair(args.pdk, apply=args.apply)
    print(json.dumps(got, indent=2))
    if got["missing"] and not args.apply:
        print(f"\n{len(got['missing'])} file(s) missing — re-run with --apply "
              f"to create them. Until then those libraries cannot be used: "
              f"OpenLane exits during PDK load.")


if __name__ == "__main__":
    main()
