r"""Whether STA was asked a question its timing models can answer.

A liberty file's timing tables are indexed by input slew, and that index
has a top. Ask for a delay at a slew above it and the tool does not
refuse — it extrapolates off the end of the table and returns a number
that looks exactly like a measurement. Nothing in the flow says so.

This is not hypothetical here. sky130's OpenRAM SRAM macro is
characterised over `index_1("0.00125, 0.005, 0.04")` — input slew up to
0.04 ns and no further — while its own `max_transition : 0.04` on
addr0/addr1/wmask0 is that same ceiling written down as a constraint.
The standard cell library, for comparison, goes to 1.5 ns.

Two things follow, and the second is the reason this module exists:

  - OpenROAD refuses to start when a limit that tight is present, with
    `[RSZ-0090] Max transition time from SDC is 0.040ns. Best achievable
    transition time is 0.043ns`. That is a feasibility precheck, not a
    violation report: it aborts the whole flow before doing any work,
    whether or not a single net actually violates.
  - Relax the limit and the flow completes — but the addr pins then
    settle at 0.3-0.6 ns, eight to fifteen times past where the model
    stops. Setup and hold come back clean (WNS +9.39 ns, zero setup and
    hold violations) and every one of those numbers on those paths is
    extrapolated.

So relaxing the limit converts a loud abort into a quiet fiction. That
is a worse failure, and the pipeline should not be able to report it as
a pass. What this module produces is not a violation — nobody proved
the design is bad — but an `unverified` entry, which is the honest
category: a check nobody can run with the models at hand.

The fix is a re-characterised liberty. Until there is one, a run that
lands here is data, not signoff.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# `index_1 ("0.00125, 0.005, 0.04");` — the input-slew axis. The last
# entry is where the characterisation stops.
_INDEX_1 = re.compile(r'index_1\s*\(\s*"([^"]+)"\s*\)')

# A row of OpenSTA's `report_check_types -max_slew -violators` table:
#   u_sram/addr1[7]    0.050000   0.880400   -0.830400 (VIOLATED)
_SLEW_ROW = re.compile(
    r"^\s*(\S+)\s+([\d.]+)\s+([\d.]+)\s+(-?[\d.]+)\s*\(VIOLATED\)", re.M)


class ModelValidityError(RuntimeError):
    pass


def characterisation_ceiling(lib_path: Path | str) -> float | None:
    """The highest input slew this library was characterised at, in ns.

    None when the file declares no `index_1` at all, which is a fact
    about the file rather than a ceiling of zero — returning 0.0 there
    would mark every pin extrapolated.
    """
    text = Path(lib_path).read_text(errors="ignore")
    tops = []
    for m in _INDEX_1.finditer(text):
        parts = [p.strip() for p in m.group(1).split(",") if p.strip()]
        try:
            tops.append(float(parts[-1]))
        except (ValueError, IndexError):
            continue
    return max(tops) if tops else None


def macro_ceilings(design_dir: Path | str) -> dict[str, float]:
    """Each macro instance mapped to the ceiling of its own liberty.

    Keyed by *instance* rather than by macro, because the STA report
    names pins as `<instance>/<pin>` and that is what has to be matched.
    """
    design_dir = Path(design_dir)
    cfg_path = design_dir / "config.json"
    if not cfg_path.is_file():
        return {}
    cfg = json.loads(cfg_path.read_text())
    out: dict[str, float] = {}
    for macro, spec in (cfg.get("MACROS") or {}).items():
        libs = spec.get("lib") or {}
        paths = []
        for entry in libs.values():
            paths.extend(entry if isinstance(entry, list) else [entry])
        ceiling = None
        for raw in paths:
            resolved = _resolve(raw, design_dir)
            if resolved is None or not resolved.is_file():
                continue
            got = characterisation_ceiling(resolved)
            # The tightest of a macro's corners is the one that binds.
            if got is not None:
                ceiling = got if ceiling is None else min(ceiling, got)
        if ceiling is None:
            continue
        for inst in (spec.get("instances") or {}):
            out[inst] = ceiling
    return out


def _resolve(raw: str, design_dir: Path) -> Path | None:
    """A config path, which may be `dir::`-relative or container-absolute."""
    raw = str(raw)
    if raw.startswith("dir::"):
        return design_dir / raw[len("dir::"):]
    if raw.startswith("/pdk/"):
        # The container mount. Map it back to the checkout's own PDK.
        hits = sorted((REPO_ROOT / "pdk").rglob(Path(raw).name))
        return hits[0] if hits else None
    p = Path(raw)
    return p if p.is_absolute() else design_dir / raw


def find_reports(run_dir: Path | str) -> list[Path]:
    """Every per-corner `checks.rpt` the signoff STA step wrote."""
    run_dir = Path(run_dir)
    hits = sorted(run_dir.glob("*stapostpnr*/*/checks.rpt"))
    return hits or sorted(run_dir.glob("*sta*/*/checks.rpt"))


def parse_slews(text: str) -> list[dict]:
    """Violating pins from one corner's max-slew table.

    Only the violators — the report lists nothing else, and a pin that
    meets its limit cannot be past the ceiling that produced it.
    """
    section = text.split("max slew", 1)[1] if "max slew" in text else ""
    section = section.split("max fanout", 1)[0]
    out = []
    for m in _SLEW_ROW.finditer(section):
        out.append({
            "pin": m.group(1),
            "limit_ns": float(m.group(2)),
            "slew_ns": float(m.group(3)),
        })
    return out


def check(design_dir: Path | str, run_dir: Path | str) -> dict | None:
    """Pins whose reported slew is past their model's characterisation.

    None when there is nothing to judge — no macro with a readable
    liberty, or no signoff STA report. Absence of evidence is reported
    as absence, not as a pass.
    """
    ceilings = macro_ceilings(design_dir)
    if not ceilings:
        return None
    reports = find_reports(run_dir)
    if not reports:
        return None

    worst: dict[str, dict] = {}
    for rpt in reports:
        corner = rpt.parent.name
        for row in parse_slews(rpt.read_text(errors="ignore")):
            inst = row["pin"].split("/", 1)[0]
            ceiling = ceilings.get(inst)
            if ceiling is None or row["slew_ns"] <= ceiling:
                continue
            entry = {
                "pin": row["pin"],
                "corner": corner,
                "slew_ns": row["slew_ns"],
                "characterised_to_ns": ceiling,
                "times_past_ceiling": round(row["slew_ns"] / ceiling, 1),
            }
            prev = worst.get(row["pin"])
            if prev is None or entry["slew_ns"] > prev["slew_ns"]:
                worst[row["pin"]] = entry

    pins = sorted(worst.values(), key=lambda e: -e["slew_ns"])
    return {
        "macro_ceilings_ns": ceilings,
        "corners_read": len(reports),
        "extrapolated_pins": pins,
        "worst_times_past_ceiling": pins[0]["times_past_ceiling"] if pins else 0.0,
    }


def unverified(result: dict | None) -> list[str]:
    """The verdict lines this produces, if any.

    Deliberately `unverified` and not a violation: an extrapolated
    number is not proof the design is bad, it is proof that nobody can
    say from here. Conflating the two would either hide the problem or
    invent a failure.
    """
    if not result or not result["extrapolated_pins"]:
        return []
    pins = result["extrapolated_pins"]
    worst = pins[0]
    return [
        f"timing on {len(pins)} macro pin(s) is extrapolated beyond the "
        f"liberty's characterisation: worst is {worst['pin']} at "
        f"{worst['slew_ns']:.3f} ns against a model characterised to "
        f"{worst['characterised_to_ns']:.3f} ns "
        f"({worst['times_past_ceiling']}x past the last table entry, "
        f"corner {worst['corner']}) — these delays are off the end of "
        f"the table, not measurements, and signoff needs a "
        f"re-characterised liberty"
    ]


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--design", required=True, type=Path)
    ap.add_argument("--run-dir", required=True, type=Path)
    args = ap.parse_args()
    got = check(args.design, args.run_dir)
    if got is None:
        raise SystemExit(
            f"{args.design.name}: nothing to judge — no macro liberty, or "
            f"the run has no signoff STA report")
    print(json.dumps(got, indent=2))
    for line in unverified(got):
        print("\nUNVERIFIED:", line)


if __name__ == "__main__":
    main()
