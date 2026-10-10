"""Tests for the opt-in DiodeTrim step (flows/diode_trim.py) and its plumbing.

The step exists because OpenROAD's antenna repair gives a gate that cannot reach
the margin-tightened ratio 11 diodes in one iteration, and on the aes recipe 40
to 64% of the diodes land on such nets. What the pure logic must get right: only
nets at or above the threshold are touched, the first KEEP by insertion number
survive, every non-diode pin connection is unchanged, and a request that cannot
mean anything fails before a flow starts. The ODB itself is a fake here; the real
database is exercised by a container run.
"""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
import orchestrator  # noqa: E402
from flows.diode_trim import select_removals, trim_diodes  # noqa: E402
from flows.fanout_repair import diode_trim_requested  # noqa: E402


class SelectRemovalsTests(unittest.TestCase):
    def test_only_nets_at_or_above_the_threshold_are_touched(self):
        got = select_removals({"a": [f"ANTENNA_{i}" for i in range(1, 12)],
                               "b": [f"ANTENNA_{i}" for i in range(20, 29)],      # 9 diodes
                               "c": [f"ANTENNA_{i}" for i in range(40, 50)]},     # exactly 10
                              min_diodes=10, keep=2)
        self.assertEqual(sorted(got), ["a", "c"])
        self.assertEqual(len(got["a"]), 9)
        self.assertEqual(len(got["c"]), 8)

    def test_the_kept_diodes_are_the_lowest_insertion_numbers_not_the_lexical_order(self):
        # ANTENNA_9 sorts after ANTENNA_100 as text; insertion order is numeric.
        names = ["ANTENNA_100", "ANTENNA_9", "ANTENNA_20", "ANTENNA_3"]
        got = select_removals({"n": names}, min_diodes=3, keep=2)
        self.assertEqual(got["n"], ["ANTENNA_20", "ANTENNA_100"])  # 3 and 9 are kept

    def test_the_choice_does_not_depend_on_input_order(self):
        names = [f"ANTENNA_{i}" for i in (7, 2, 11, 5, 13, 3, 17, 19, 23, 29, 31)]
        a = select_removals({"n": names}, 10, 3)
        b = select_removals({"n": list(reversed(names))}, 10, 3)
        self.assertEqual(a, b)

    def test_arguments_that_could_not_mean_anything_are_rejected(self):
        for keep in (0, -1, True, 2.5, None):
            with self.assertRaises(ValueError):
                select_removals({}, 10, keep)
        for minimum in (2, 1, 0, True):
            with self.assertRaises(ValueError):
                select_removals({}, minimum, 2)       # minimum must exceed keep


class FakeDb:
    """Just enough of OpenDB for trim_diodes: block, insts, nets, iterms."""

    class MTerm:
        def __init__(self, name, sig="SIGNAL"):
            self.name, self.sig = name, sig
        def getName(self): return self.name
        def getSigType(self): return self.sig

    class Master:
        def __init__(self, name): self.name = name
        def getName(self): return self.name

    class ITerm:
        def __init__(self, inst, mterm, net):
            self.inst, self.mterm, self.net = inst, mterm, net
        def getInst(self): return self.inst
        def getMTerm(self): return self.mterm
        def getNet(self): return self.net

    class Inst:
        def __init__(self, name, master, dont_touch=False):
            self.name, self.master, self.dont_touch, self.iterms = name, master, dont_touch, []
        def getName(self): return self.name
        def getMaster(self): return self.master
        def getITerms(self): return self.iterms
        def isDoNotTouch(self): return self.dont_touch

    class Net:
        def __init__(self, name): self.name = name
        def getName(self): return self.name
        def getITerms(self): return [t for i in FakeDb.current.insts for t in i.iterms if t.net is self]

    current = None

    def __init__(self):
        FakeDb.current = self
        self.insts, self.nets = [], {}

    def net(self, name):
        return self.nets.setdefault(name, FakeDb.Net(name))

    def gate(self, name, net, pin="A", master="sky130_fd_sc_hd__nand2_1"):
        inst = FakeDb.Inst(name, FakeDb.Master(master))
        inst.iterms.append(FakeDb.ITerm(inst, FakeDb.MTerm(pin), self.net(net)))
        # a power pin: present on every instance, never on a signal net
        inst.iterms.append(FakeDb.ITerm(inst, FakeDb.MTerm("VPWR", "POWER"), self.net("VPWR")))
        self.insts.append(inst)
        return inst

    def diode(self, n, net, dont_touch=False):
        inst = FakeDb.Inst(f"ANTENNA_{n}", FakeDb.Master("sky130_fd_sc_hd__diode_2"), dont_touch)
        inst.iterms.append(FakeDb.ITerm(inst, FakeDb.MTerm("DIODE"), self.net(net)))
        inst.iterms.append(FakeDb.ITerm(inst, FakeDb.MTerm("VPWR", "POWER"), self.net("VPWR")))
        self.insts.append(inst)
        return inst

    def destroy(self, inst):
        self.insts.remove(inst)

    # --- OpenDB surface used by the step
    def getChip(self): return self
    def getBlock(self): return self
    def getInsts(self): return list(self.insts)
    def findNet(self, name): return self.nets[name]


class TrimDiodesTests(unittest.TestCase):
    def build(self):
        db = FakeDb()
        db.gate("g1", "heavy"); db.gate("g2", "heavy", "B")           # the real sinks of "heavy"
        db.gate("g3", "light")
        for i in range(1, 12): db.diode(i, "heavy")                    # 11 diodes
        for i in range(12, 15): db.diode(i, "light")                   # 3 diodes
        return db

    def test_excess_diodes_go_and_real_pins_stay(self):
        db = self.build()
        report = trim_diodes(db, min_diodes=10, keep=2, destroy=db.destroy)
        self.assertEqual(report["diodes_before"], 14)
        self.assertEqual(report["diodes_removed"], 9)
        self.assertEqual(report["diodes_after"], 5)
        self.assertEqual(report["nets"], {"heavy": {"before": 11, "after": 2}})
        left = sorted(i.getName() for i in db.insts if i.getName().startswith("ANTENNA_"))
        self.assertEqual(left, ["ANTENNA_1", "ANTENNA_12", "ANTENNA_13", "ANTENNA_14", "ANTENNA_2"])
        self.assertEqual(sorted(i.getName() for i in db.insts if i.getName().startswith("g")),
                         ["g1", "g2", "g3"])

    def test_a_net_below_the_threshold_is_left_alone(self):
        db = self.build()
        report = trim_diodes(db, min_diodes=12, keep=2, destroy=db.destroy)
        self.assertEqual(report["diodes_removed"], 0)
        self.assertEqual(len(db.insts), 3 + 14)

    def test_a_dont_touch_diode_is_an_error_not_a_skip(self):
        db = FakeDb(); db.gate("g", "n")
        for i in range(1, 12): db.diode(i, "n", dont_touch=(i == 11))
        with self.assertRaisesRegex(ValueError, "dont_touch"):
            trim_diodes(db, 10, 2, db.destroy)

    def test_a_destroy_that_drops_a_real_pin_is_caught(self):
        db = self.build()
        hit = []
        def bad_destroy(inst):
            db.insts.remove(inst)
            if not hit:                                    # collateral damage, once
                hit.append(db.insts.remove(next(i for i in db.insts if i.getName() == "g1")))
        with self.assertRaisesRegex(ValueError, "non-diode connections"):
            trim_diodes(db, 10, 2, bad_destroy)

    def test_a_diode_that_touches_two_signal_nets_is_not_ours(self):
        db = FakeDb(); db.gate("g", "n")
        for i in range(1, 12): db.diode(i, "n")
        odd = db.diode(99, "n")
        odd.iterms.append(FakeDb.ITerm(odd, FakeDb.MTerm("EXTRA"), db.net("other")))
        report = trim_diodes(db, 10, 2, db.destroy)
        self.assertTrue(any(i.getName() == "ANTENNA_99" for i in db.insts))
        self.assertEqual(report["diodes_before"], 11)


class RequestTests(unittest.TestCase):
    def test_absent_means_off(self):
        self.assertIsNone(diode_trim_requested([]))
        self.assertIsNone(diode_trim_requested(["GRT_ANTENNA_MARGIN=75"]))

    def test_keep_and_the_default_threshold(self):
        self.assertEqual(diode_trim_requested(["DIODE_TRIM_KEEP=4"]), (4, 10))
        self.assertEqual(diode_trim_requested(
            ["DIODE_TRIM_KEEP=2", "DIODE_TRIM_MIN_DIODES=6"]), (2, 6))

    def test_a_threshold_that_does_not_exceed_keep_fails_at_flow_start(self):
        with self.assertRaises(ValueError):
            diode_trim_requested(["DIODE_TRIM_KEEP=4", "DIODE_TRIM_MIN_DIODES=4"])


class PlanValidationTests(unittest.TestCase):
    def test_keep_is_range_checked_before_any_tool_starts(self):
        for ok in (1, 4, 9, "2"):
            orchestrator.validate_candidates(
                [{"tag": "t", "overrides": {"DIODE_TRIM_KEEP": ok}}])
        for bad in (0, 10, -1, True, "x", 2.5):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, "DIODE_TRIM_KEEP"):
                    orchestrator.validate_candidates(
                        [{"tag": "t", "overrides": {"DIODE_TRIM_KEEP": bad}}])


if __name__ == "__main__":
    unittest.main()
