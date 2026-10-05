from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
from flows.macro_pin_buffer import edge_locations, inverter_ports
from flows.fanout_repair import macro_buffer_requested


class MacroPinBufferTests(unittest.TestCase):
    def test_pair_rejects_unknown_function_and_incorrect_signal_interface(self):
        master = Mock()
        master.getName.return_value = "sky130_fd_sc_hd__inv_16"
        ports = []
        for name, direction, signal in [("A", "INPUT", "SIGNAL"),
                                        ("Y", "OUTPUT", "SIGNAL"),
                                        ("VPWR", "INOUT", "POWER")]:
            port = Mock()
            port.getName.return_value = name
            port.getIoType.return_value = direction
            port.getSigType.return_value = signal
            ports.append(port)
        master.getMTerms.return_value = ports
        self.assertEqual(inverter_ports(master), ("A", "Y"))
        master.getName.return_value = "sky130_fd_sc_hd__buf_16"
        with self.assertRaises(ValueError):
            inverter_ports(master)
        master.getName.return_value = "sky130_fd_sc_hd__inv_16"
        ports[1].getName.return_value = "X"
        with self.assertRaises(ValueError):
            inverter_ports(master)

    def test_candidate_origins_stay_outside_macro_and_nearest_edge_wins(self):
        box = (110, 150, 590, 550)
        for pin in [(111, 280), (589, 233), (180, 151), (507, 549)]:
            points = edge_locations(box, pin, 12, 3, 2)
            for x, y in points:
                self.assertTrue(x + 12 <= box[0] or x >= box[2]
                                or y + 3 <= box[1] or y >= box[3])
            costs = [abs(x + 6 - pin[0]) + abs(y + 1.5 - pin[1]) for x, y in points]
            self.assertEqual(costs, sorted(costs))
        self.assertEqual(edge_locations(box, (111, 280), 12, 3, 2)[0][0], 96)

    def test_macro_buffer_request_is_explicit_and_rejects_invalid_json_shape(self):
        self.assertFalse(macro_buffer_requested([]))
        self.assertTrue(macro_buffer_requested(['MACRO_BUFFER_PINS=["u_sram/web0"]']))
        for value in ('{}', 'null', '[42]'):
            with self.assertRaises(ValueError):
                macro_buffer_requested([f'MACRO_BUFFER_PINS={value}'])


if __name__ == "__main__":
    unittest.main()
