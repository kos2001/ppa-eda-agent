"""The trade-off chart must draw the front the pipeline chose from.

The dashboard's parallel-coordinates chart (ObjectivesChart.tsx) marks which
passing candidates sit on the Pareto front and which objectives it could
use. dashboard/src/components/objectives.ts is therefore a second copy of
orchestrator.objective_table() and pareto.fast_nondominated_sort(). Two
copies of one definition of "better" is how a chart ends up saying a
candidate is on the front while the pipeline ranked it otherwise, so this
pins them to each other over the real store.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "tests" / "objectives_check.mjs"
CASES = ROOT / "reference-db" / "cases"

sys.path.insert(0, str(ROOT / "pipeline"))
import case_store  # noqa: E402
import orchestrator  # noqa: E402
from pareto import fast_nondominated_sort  # noqa: E402


def _light_store(tmp: Path) -> None:
    """The store without layout/netlist, so the harness does not parse 200 MB."""
    for p in sorted(CASES.glob("*.json")):
        (tmp / p.name).write_text(json.dumps(case_store.load_light(p)), encoding="utf-8")


def _harness(directory: Path) -> list[dict]:
    try:
        out = subprocess.run(
            ["npx", "tsx", str(HARNESS), str(directory)],
            cwd=ROOT / "dashboard", capture_output=True, text=True,
            encoding="utf-8", timeout=300, shell=(os.name == "nt"))
    except FileNotFoundError as e:
        raise unittest.SkipTest(f"npx unavailable: {e}")
    if out.returncode != 0:
        raise unittest.SkipTest(f"tsx unavailable: {out.stderr[-200:]}")
    return json.loads(out.stdout)


def _expected(directory: Path) -> list[dict]:
    rows = []
    for p in sorted(directory.glob("*.json")):
        case = json.loads(p.read_text(encoding="utf-8"))
        for it in case.get("iterations", []):
            passing = [r for r in it["results"]
                       if not r.get("error") and (r.get("verdict") or {}).get("passed")]
            if len(passing) < 2:
                continue
            table = orchestrator.objective_table(passing)
            points = orchestrator.pareto_points(passing)
            front = [points[i].key for i in fast_nondominated_sort(points)[0]]
            rows.append({"file": p.name, "iteration": it["iteration"],
                         "used": list(table[0][1].keys()), "front": sorted(front)})
    return rows


class TestChartMatchesPipeline(unittest.TestCase):
    def test_missing_values_and_unchecked_corners_agree(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            for index, field in enumerate(["area_um2", "power", "operating_point", "worst_setup_slack"]):
                rows = [{"tag": tag, "verdict": {"passed": True, "area_um2": 100 + offset,
                        "power": {"total_w": 0.001}, "utilization": 0.5,
                        "operating_point": {"corners": [{"setup_ws_ns": 1}]}}}
                        for tag, offset in [("a", 0), ("b", 20)]]
                rows[1]["verdict"][field] = ({"corners": [{"setup_ws_ns": 1}, {"setup_ws_ns": None}]}
                                             if field == "operating_point" else "unknown" if field == "worst_setup_slack" else None)
                (tmp / f"fixture{index}.json").write_text(json.dumps({"iterations": [{"iteration": 1, "results": rows}]}))
            self.assertEqual(_harness(tmp), _expected(tmp))

    def test_front_and_objectives_agree_over_the_real_store(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            _light_store(tmp)
            got = _harness(tmp)
            want = _expected(tmp)
        self.assertGreater(len(want), 10, "the store should hold many multi-passer iterations")
        self.assertEqual(len(got), len(want))
        for g, w in zip(got, want):
            self.assertEqual(g, w, f"{w['file']} iteration {w['iteration']}")


if __name__ == "__main__":
    unittest.main()
