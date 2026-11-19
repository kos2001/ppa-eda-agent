"""Connectivity controls; these do not replace extracted STA or LVS."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
from flows.fanout_buffer import direct_sink_plan, repair_fanout


class Net:
    def __init__(self, name):
        self.name, self.terms = name, []

    def getName(self): return self.name
    def getITerms(self): return list(self.terms)
    def isSpecial(self): return False
    def isDoNotTouch(self): return False
    def getBTerms(self): return []
    def getSigType(self): return "SIGNAL"
    def setSigType(self, value): pass


class Term:
    def __init__(self, inst, pin, direction, net=None):
        self.inst, self.pin, self.direction, self.net = inst, pin, direction, None
        if net is not None:
            self.connect(net)

    def getInst(self): return self.inst
    def getMTerm(self): return SimpleNamespace(getName=lambda: self.pin)
    def getIoType(self): return self.direction
    def getAvgXY(self): return True, 0, 0
    def connect(self, net):
        if self.net is not None:
            self.net.terms.remove(self)
        self.net = net
        net.terms.append(self)


class Inst:
    def __init__(self, name):
        self.name = name
        self.pins = {p: Term(self, p, d) for p, d in (("A", "INPUT"), ("X", "OUTPUT"))}

    def getName(self): return self.name
    def isDoNotTouch(self): return False
    def findITerm(self, pin): return self.pins[pin]
    def setLocation(self, x, y): pass
    def setPlacementStatus(self, status): pass


class DirectSinkTests(unittest.TestCase):
    def fixture(self, count=32):
        net = Net("data")
        driver = Inst("driver")
        driver.pins["X"].connect(net)
        sinks = [Inst(f"sink{i}") for i in range(count)]
        for sink in sinks:
            sink.pins["A"].connect(net)
        nets, insts = {"data": net}, {i.name: i for i in [driver] + sinks}
        block = Mock()
        block.findNet.side_effect = nets.get
        block.findInst.side_effect = insts.get
        block.getNets.side_effect = lambda: list(nets.values())
        block.getInsts.side_effect = lambda: list(insts.values())
        db = Mock()
        db.getChip.return_value.getBlock.return_value = block

        def create_inst(block, master, name):
            insts[name] = Inst(name)
            return insts[name]

        def create_net(block, name):
            nets[name] = Net(name)
            return nets[name]

        odb = SimpleNamespace(dbInst=SimpleNamespace(create=Mock(side_effect=create_inst)),
                              dbNet=SimpleNamespace(create=Mock(side_effect=create_net)))
        return db, block, net, sinks, nets, odb

    def test_direct_pins_survive_multilevel_tree_and_second_round(self):
        db, block, root, sinks, nets, odb = self.fixture()
        retained = {"data": ["sink0/A", "sink1/A", "sink2/A"]}
        with patch.dict(sys.modules, odb=odb), \
                patch("flows.fanout_buffer.buffer_ports", return_value=("A", "X")):
            report = repair_fanout(db, 4, "buf", "buf", direct_sinks=retained)
            again = repair_fanout(db, 4, "buf", "buf", direct_sinks=retained)
        self.assertGreater(report["inserted_buffers"], 1)
        self.assertEqual(again["inserted_buffers"], 0)
        self.assertEqual(report["repaired_nets"][0]["remaining_driver_sinks"], 4)
        for sink in sinks[:3]:
            self.assertIs(sink.pins["A"].net, root)
        for sink in sinks[3:]:
            self.assertIsNot(sink.pins["A"].net, root)
        for net in nets.values():
            self.assertLessEqual(sum(t.direction == "INPUT" for t in net.terms), 4)
            self.assertEqual(sum(t.direction == "OUTPUT" for t in net.terms), 1)
        # Trace every original sink back to the original driver; reject cycles.
        for sink in sinks:
            net, seen = sink.pins["A"].net, set()
            while net is not root:
                self.assertNotIn(net.name, seen)
                seen.add(net.name)
                upstream = next(t.inst for t in net.terms if t.direction == "OUTPUT")
                net = upstream.pins["A"].net

    def test_invalid_plan_rejected_before_any_mutation(self):
        for plan in ([], {"missing": ["sink0/A"]}, {"data": []},
                     {"data": ["driver/X"]}, {"data": ["sink0/A", "sink0/A"]},
                     {"data": ["sink0/A", "sink1/A", "sink2/A", "sink3/A"]},
                     {"data": ["sink0/A"], "missing": ["sink1/A"]}):
            with self.subTest(plan=plan):
                db, block, root, sinks, nets, odb = self.fixture()
                with patch.dict(sys.modules, odb=odb), \
                        patch("flows.fanout_buffer.buffer_ports", return_value=("A", "X")), \
                        self.assertRaises(ValueError):
                    repair_fanout(db, 4, "buf", "buf", direct_sinks=plan)
                odb.dbInst.create.assert_not_called()
                self.assertTrue(all(s.pins["A"].net is root for s in sinks))

    def test_targeted_limit_reserves_branch_capacity(self):
        _, block, _, _, _, _ = self.fixture()
        with self.assertRaises(ValueError):
            direct_sink_plan(block, {"data": ["sink0/A", "sink1/A"]}, {"data": 2}, 16)


if __name__ == "__main__":
    unittest.main()
