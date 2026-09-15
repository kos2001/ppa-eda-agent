import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
from openram_adapter import stimulus_class, setup_hold_class, _DeckWriter


class CaptureStimuli:
    def gen_pwl(self, sig_name, clk_times, data_values, period, slew, setup):
        self.ramp = slew
        self.signal = sig_name

    def gen_pulse(self, sig_name, v1, v2, offset, period, t_rise, t_fall):
        self.rise, self.fall = t_rise, t_fall


class StimulusTests(unittest.TestCase):
    def test_deck_writer_removes_waveform_storage_options(self):
        class Stream:
            def __init__(self):
                self.data = ""
            def write(self, data):
                self.data += data
        stream = Stream()
        _DeckWriter(stream, False).write(
            ".OPTIONS POST=1 RELTOL=0.001 PROBE method=gear ACCT\n")
        self.assertEqual(stream.data, ".OPTIONS RELTOL=0.001 method=gear ACCT\n")

    def test_pwl_10_to_90_crossings_match_liberty_grid(self):
        stim = stimulus_class(CaptureStimuli)()
        for slew in [0.00125, 0.005, 0.04, 0.260]:
            stim.gen_pwl("D", [0, 1], [0, 1], 1, slew, 0)
            self.assertAlmostEqual(0.9 * stim.ramp - 0.1 * stim.ramp, slew)

    def test_pulse_rise_and_fall_crossings_match_independent_slews(self):
        stim = stimulus_class(CaptureStimuli)()
        stim.gen_pulse("clk", 0, 1.8, 0, 10, 0.04, 0.260)
        self.assertAlmostEqual(stim.rise * 0.8, 0.04)
        self.assertAlmostEqual(stim.fall * 0.8, 0.260)

    def test_clock_and_data_slew_axes_are_distinct(self):
        sh = setup_hold_class(object)()
        sh.stim = stimulus_class(CaptureStimuli)()
        sh.period = 10
        sh.related_input_slew = 0.04
        sh.constrained_input_slew = 0.260
        sh.write_clock()
        self.assertEqual(sh.stim.signal, "clk")
        self.assertAlmostEqual(sh.stim.ramp * 0.8, sh.related_input_slew)
