from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
from flows.driver_size import compatible_family
from flows.fanout_repair import driver_sizing_requested


class DriverSizingTests(unittest.TestCase):
    def test_rejects_function_changes_and_non_upsizes(self):
        self.assertTrue(compatible_family("sky130_fd_sc_hd__o2bb2ai_2", "sky130_fd_sc_hd__o2bb2ai_4"))
        self.assertTrue(compatible_family("sky130_fd_sc_hd__a211oi_1", "sky130_fd_sc_hd__a211oi_4"))
        for old, new in [("buf_4", "inv_8"), ("buf_8", "buf_4"),
                         ("buf_4", "buf_4"), ("dfxtp_1", "dfxtp_2"),
                         ("o2bb2ai_2", "o21ai_4"), ("a211oi_1", "a21oi_4"),
                         ("a211oi_4", "a211oi_2"), ("a211o_1", "a211oi_4")]:
            self.assertFalse(compatible_family("sky130_fd_sc_hd__" + old,
                                               "sky130_fd_sc_hd__" + new))

    def test_rejects_non_mapping_and_non_string_requests(self):
        self.assertFalse(driver_sizing_requested([]))
        self.assertTrue(driver_sizing_requested(['FANOUT_REPAIR_DRIVER_CELLS={"inst":"cell"}']))
        for value in ["[]", "null", '{"inst":42}']:
            with self.assertRaises(ValueError):
                driver_sizing_requested(["FANOUT_REPAIR_DRIVER_CELLS=" + value])
