"""Tests for pipeline/stdcell_schematic.py — opening a standard cell.

sky130A ships 437 xschem symbols for sky130_fd_sc_hd and not one
schematic behind them, so "what IS an a21oi_2" had no answer in this
console. The transistors come from the foundry's own CDL and the drawing
from the PDK's own SPICE importer; what these tests pin is the
translation between the two, and the refusal that keeps a wrong drawing
from being produced.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import stdcell_schematic as sc  # noqa: E402


# The real CDL block for sky130_fd_sc_hd__inv_2, continuations joined.
INV2 = """.SUBCKT sky130_fd_sc_hd__inv_2 A VGND VNB VPB VPWR Y
*.PININFO A:I VGND:I VNB:I VPB:I VPWR:I Y:O
MMIN1 Y A VGND VNB nfet_01v8 m=2 w=0.65 l=0.15 mult=1 sa=0.265 sb=0.265 sd=0.28
MMIP1 Y A VPWR VPB pfet_01v8_hvt m=2 w=1.0 l=0.15 mult=1 sa=0.265 sb=0.265 sd=0.28
.ENDS sky130_fd_sc_hd__inv_2"""

# A sequential cell's device, which is what 67 of the 437 cells trip on.
SPECIAL = """.SUBCKT sky130_fd_sc_hd__dfxtp_1 CLK D VGND VNB VPB VPWR Q
MM1 a b VGND VNB special_nfet_01v8 m=1 w=0.42 l=0.15
MM2 a b VPWR VPB pfet_01v8_hvt m=1 w=0.64 l=0.15
.ENDS sky130_fd_sc_hd__dfxtp_1"""


class Translation(unittest.TestCase):
    def test_devices_become_subcircuit_calls_on_the_full_model_name(self):
        """The importer resolves a symbol from the sky130_fd_pr__ name on
        an X-prefixed call; the CDL writes an M device with a bare model."""
        spice = sc.to_spice(INV2)
        self.assertIn("sky130_fd_pr__nfet_01v8", spice)
        self.assertIn("sky130_fd_pr__pfet_01v8_hvt", spice)
        self.assertTrue(all(l.startswith(("X", ".")) for l in spice.splitlines()))

    def test_terminal_order_is_preserved(self):
        # CDL M devices are D G S B, and so is the importer's X form; a
        # swap here would draw a different circuit that still looks fine.
        line = [l for l in sc.to_spice(INV2).splitlines() if "nfet" in l][0]
        self.assertEqual(line.split()[1:5], ["Y", "A", "VGND", "VNB"])

    def test_parameters_are_spelled_the_way_the_symbols_read_them(self):
        """Guards a real wrong drawing: passing the CDL's lowercase w/l
        leaves them unknown, and every device is then annotated with the
        symbol's default 1 x 1 / 0.15 while the netlist says 0.65."""
        line = [l for l in sc.to_spice(INV2).splitlines() if "nfet" in l][0]
        self.assertIn("W=0.65", line)
        self.assertIn("L=0.15", line)
        self.assertNotIn("w=0.65", line)

    def test_multiplicity_survives(self):
        self.assertIn("m=2", sc.to_spice(INV2))

    def test_the_subckt_line_is_lowercased_for_the_importer(self):
        self.assertTrue(sc.to_spice(INV2).startswith(".subckt sky130_fd_sc_hd__inv_2 "))
        self.assertIn(".ends", sc.to_spice(INV2))

    def test_pininfo_comments_do_not_become_devices(self):
        self.assertEqual(len([l for l in sc.to_spice(INV2).splitlines()
                              if l.startswith("X")]), 2)


class Refusal(unittest.TestCase):
    def test_a_model_with_no_symbol_is_named(self):
        """dfxtp_2 came out with 21 of its 24 transistors before this
        existed — the importer drops what it cannot resolve and says
        nothing."""
        absent = sc.missing_symbols(SPECIAL)
        self.assertEqual(absent, {"special_nfet_01v8": 1})

    def test_a_cell_whose_devices_all_have_symbols_is_clean(self):
        self.assertEqual(sc.missing_symbols(INV2), {})

    def test_device_count_is_what_the_drawing_is_checked_against(self):
        self.assertEqual(sc.device_count(INV2), 2)
        self.assertEqual(sc.device_count(SPECIAL), 2)


class Library(unittest.TestCase):
    def test_the_cdl_is_where_the_pdk_puts_it(self):
        # If this moves, everything here is drawing nothing.
        self.assertTrue(sc.CDL.is_file(), sc.CDL)

    def test_cells_can_be_searched_by_substring(self):
        found = sc.cells("nand2")
        self.assertIn("sky130_fd_sc_hd__nand2_1", found)
        self.assertTrue(all("nand2" in c for c in found))

    def test_the_sequential_cells_are_the_ones_that_cannot_be_drawn(self):
        # Measured: 370 of 437 draw; the 67 that do not are flops and
        # latches, which use special_nfet_01v8 / special_pfet_01v8_hvt.
        self.assertFalse(sc.drawable("sky130_fd_sc_hd__dfxtp_2"))
        self.assertTrue(sc.drawable("sky130_fd_sc_hd__inv_2"))


if __name__ == "__main__":
    unittest.main()
