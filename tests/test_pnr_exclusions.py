"""Tests for the PnR cell exclusions sram_wrapper depends on.

The macro's address pins were violating their slew limit by up to 22x.
Tracing the path with report_checks rather than sweeping config knobs
showed why: OpenROAD's repair_design was fixing the slew with delay
cells.

    _093_/Q     (dfxtp_1)          slew 0.0539   <- the flop is fine
    load_slew85 (dlymetal6s2s_1)   slew 0.1576
    load_slew84 (dlymetal6s2s_1)   slew 0.2350
    load_slew83 (clkbuf_2)         slew 0.2585
    u_sram/addr0[3]                slew 0.3453   <- violation

`dlymetal6s2s_1` is a delay cell. Its entire purpose is to be slow, and
it was being chosen to repair slew. Excluding the delay families, and
then the weakest buffers, took the worst address slew from 0.8804 ns to
0.2092 ns with hold and setup unchanged.

The exclusion list is therefore load-bearing, and it is a plain text
file that nothing else validates — a typo in a cell name silently
excludes nothing, and the result would look like the tool simply
regressing. These tests are that validation.
"""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DESIGN = ROOT / "pipeline" / "designs" / "sram_wrapper"
EXCLUDE = DESIGN / "pnr" / "pnr_exclude.cells"
PDK = ROOT / "pdk"


def library() -> Path | None:
    hits = sorted(PDK.rglob("sky130_fd_sc_hd__tt_025C_1v80.lib"))
    return hits[0] if hits else None


def cells_in_library() -> set:
    lib = library()
    if lib is None:
        return set()
    return set(re.findall(r'cell\s*\(\s*"?(sky130_fd_sc_hd__[\w]+)"?\s*\)',
                          lib.read_text(encoding="utf-8", errors="ignore")))


def excluded() -> list:
    return [ln.strip() for ln in EXCLUDE.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")]


class ExclusionFileTests(unittest.TestCase):
    def setUp(self):
        if not EXCLUDE.is_file():
            self.skipTest("exclusion list not present")

    def test_every_excluded_cell_exists(self):
        # The failure mode this guards: a misspelled name excludes
        # nothing, silently, and the design regresses to using delay
        # cells again with no error anywhere.
        known = cells_in_library()
        if not known:
            self.skipTest("no PDK liberty to check against")
        missing = sorted(set(excluded()) - known)
        self.assertEqual(missing, [], f"not real cells: {missing}")

    def test_every_delay_cell_is_excluded(self):
        # The whole point. A cell family whose purpose is to add delay
        # must never be available to a pass that repairs slew.
        known = cells_in_library()
        if not known:
            self.skipTest("no PDK liberty to check against")
        delays = {c for c in known if "__dly" in c or "__clkdly" in c}
        self.assertTrue(delays, "expected sky130 to have delay cells")
        self.assertEqual(sorted(delays - set(excluded())), [])

    def test_the_weakest_buffers_are_excluded(self):
        # Measured: five weak buffers in a chain made the slew grow
        # rather than recover — 0.0877 ns at the flop, 0.1409 ns at the
        # macro pin after them. Excluding these moved repair_design to
        # buf_4/buf_6.
        for cell in ("sky130_fd_sc_hd__buf_1", "sky130_fd_sc_hd__buf_2",
                     "sky130_fd_sc_hd__clkbuf_1"):
            self.assertIn(cell, excluded())

    def test_cts_buffers_are_left_alone(self):
        # CTS_CLK_BUFFERS is clkbuf_8/4/2 and CTS_ROOT_BUFFER is
        # clkbuf_16. Excluding one of those does not make the resizer
        # pick better — it takes away the clock tree's own cells.
        for cell in ("sky130_fd_sc_hd__clkbuf_2", "sky130_fd_sc_hd__clkbuf_4",
                     "sky130_fd_sc_hd__clkbuf_8", "sky130_fd_sc_hd__clkbuf_16"):
            self.assertNotIn(cell, excluded(), cell)

    def test_it_still_contains_the_pdks_own_exclusions(self):
        # The file is the PDK's drc_exclude.cells plus our additions, not
        # a replacement for it — dropping the DRC-driven entries would
        # trade a slew problem for a DRC one.
        pdk_list = sorted(PDK.rglob("*/openlane/sky130_fd_sc_hd/drc_exclude.cells"))
        if not pdk_list:
            self.skipTest("no PDK exclusion list")
        base = {ln.strip() for ln in pdk_list[0].read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.startswith("#")}
        self.assertEqual(sorted(base - set(excluded())), [])


class DesignConfigTests(unittest.TestCase):
    def setUp(self):
        cfg = DESIGN / "config.json"
        if not cfg.is_file():
            self.skipTest("sram_wrapper not present")
        self.cfg = json.loads(cfg.read_text(encoding="utf-8"))

    def test_the_design_actually_uses_the_list(self):
        # Verified via an override during the investigation, then moved
        # into config.json — a fix that only exists on a command line is
        # not a fix.
        self.assertEqual(self.cfg.get("PNR_EXCLUDED_CELL_FILE"),
                         "dir::pnr/pnr_exclude.cells")

    def test_the_macro_power_connection_stays_corrected(self):
        # <instance> <vdd_net> <gnd_net> <vdd_pin> <gnd_pin> — net before
        # pin. Swapped, it silently connects the macro to nothing and
        # OpenLane only warns (PDN-0231).
        conns = self.cfg.get("PDN_MACRO_CONNECTIONS") or []
        self.assertTrue(conns)
        parts = conns[0].split()
        self.assertEqual(parts[1:3], ["VPWR", "VGND"], parts)
        self.assertEqual(parts[3:5], ["vccd1", "vssd1"], parts)


class RtlTests(unittest.TestCase):
    def setUp(self):
        self.rtl = DESIGN / "src" / "sram_wrapper.v"
        if not self.rtl.is_file():
            self.skipTest("sram_wrapper not present")
        self.text = self.rtl.read_text(encoding="utf-8")
        # Comments only, stripped: the comment explaining the change
        # quotes the expression it removed, and a plain substring search
        # cannot tell an explanation from the code it describes.
        self.code = re.sub(r"//[^\n]*", "", self.text)

    def test_addr1_is_registered_not_decremented(self):
        # `addr_ctr - 8'd1` built an 8-bit combinational decrementer
        # whose last gate drove the macro pin directly at 0.364 ns
        # output slew. For a free-running counter the previous value is
        # the registered copy, so the arithmetic was never needed.
        self.assertNotIn("addr_ctr - 8'd1", self.code)
        self.assertIn("addr_prev", self.code)

    def test_the_correction_about_which_pins_are_constrained_survives(self):
        # An earlier comment here claimed the 0.04 ns limit was on
        # dout0/dout1, which sent one session after the data bus. It is
        # on addr0/addr1/wmask0, all inputs.
        self.assertIn("addr0, addr1 and wmask0", self.text)


if __name__ == "__main__":
    sys.exit(unittest.main())
