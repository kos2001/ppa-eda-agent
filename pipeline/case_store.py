"""Reading reference-db cases without their two heavy fields.

Every candidate result in a case carries `layout` (the parsed DEF: every
cell and every routed segment) and `netlist` (the Yosys graph). Measured
on the store at 70 cases: 599 MB on disk, of which those two fields are
all but ~3 MB. They are written for the dashboard's per-candidate views
and read by nothing in pipeline/ — every analysis here (retrieval, the
surrogate, self_improve, data_lineage, service_qa) walks verdicts,
overrides, errors and timings. Yet each of them parsed the whole 599 MB,
once per scan: data_lineage.report() does four scans and took 16 s,
service_qa spent 3.6 s of a 4 s question on it.

So the stripped form is kept on disk beside the store, in the gitignored
.cache/, and re-derived only when the case file's size or mtime moves.
A case rewritten by rescore_gf180_drc, repair_operating_points or
request_review changes its mtime, so a stale entry is never served;
nothing has to remember to invalidate it.

Anything that rewrites a case must keep reading the full file — writing
back a light case would delete the layouts. This module is for readers.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REFDB = REPO_ROOT / "reference-db"
CACHE_DIR = REPO_ROOT / ".cache" / "cases-light"

HEAVY_FIELDS = ("layout", "netlist")

# Bumped whenever strip_heavy's output changes shape, so entries written
# by an older version are rebuilt rather than served.
FORMAT = 2


def strip_heavy(case: dict) -> dict:
    """The case with each result's heavy fields removed, in place.

    Leaves the same `*_deferred` flags server/index.mjs's lightCase does,
    so a reader can tell "not loaded" from "never recorded".
    """
    for iteration in case.get("iterations", []) or []:
        for result in iteration.get("results", []) or []:
            layout = result.pop("layout", None)
            netlist = result.pop("netlist", None)
            result["layout_deferred"] = layout is not None
            result["netlist_deferred"] = (
                netlist is not None
                and not (isinstance(netlist, dict) and netlist.get("error")))
    return case


def _default_cache_dir(path: Path) -> Path | None:
    # Only the real store is cached. A test fixture or a scratch store
    # elsewhere is small, and writing its entries into this repo's cache
    # would mix two stores under one set of names.
    try:
        if path.resolve().parent == (REFDB / "cases").resolve():
            return CACHE_DIR
    except OSError:
        pass
    return None


def load_light(path: Path | str, cache_dir: Path | str | None = ...) -> dict:
    """One case, without layout/netlist.

    Raises OSError / json.JSONDecodeError exactly as reading the file
    directly would, so callers keep their existing skip-on-error logic.
    """
    path = Path(path)
    if cache_dir is ...:
        cache_dir = _default_cache_dir(path)
    st = path.stat()
    key = f"v{FORMAT}:{st.st_mtime_ns}:{st.st_size}"

    entry = Path(cache_dir) / path.name if cache_dir is not None else None
    if entry is not None:
        try:
            cached = json.loads(entry.read_text(encoding="utf-8"))
            if cached.get("key") == key:
                return cached["case"]
        except (OSError, ValueError, KeyError, AttributeError):
            pass

    case = strip_heavy(json.loads(path.read_text(encoding="utf-8")))

    if entry is not None:
        # Written to a temp name and renamed, so a reader in another
        # process never sees half an entry. A cache that cannot be
        # written is only a slower read, never a failure.
        try:
            entry.parent.mkdir(parents=True, exist_ok=True)
            tmp = entry.with_name(f"{entry.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps({"key": key, "case": case}), encoding="utf-8")
            os.replace(tmp, entry)
        except OSError:
            pass
    return case
