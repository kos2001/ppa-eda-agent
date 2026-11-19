"""Discover one STA evidence layout without mixing live and archived stages."""
from pathlib import Path

from sta_report import latest_sta_dir


def reports(run_dir, filename):
    run_dir = Path(run_dir)
    try:
        stage = latest_sta_dir(run_dir)
    except FileNotFoundError:
        # Select a layout once for both report kinds; an incomplete direct
        # archive must not borrow a stale CSV from the older flat layout.
        if any(run_dir.glob("*/checks.rpt")) or any(run_dir.glob("*/macro_inputs.csv")):
            return {p.parent.name: p for p in sorted(run_dir.glob("*/" + filename))}
        suffix = {"checks.rpt": "-drv.rpt", "macro_inputs.csv": "-macro-inputs.csv"}[filename]
        return {p.name[:-len(suffix)]: p for p in sorted(run_dir.glob("*" + suffix))}
    return {p.parent.name: p for p in sorted(stage.glob("*/" + filename))}
