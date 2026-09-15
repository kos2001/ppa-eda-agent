"""Stimulus corrections for OpenRAM b2b069ce, without changing its tables.

Liberty and output measurements use 10–90% transition time. Upstream
PWL/PULSE generators take the full 0–100% ramp duration instead. Convert
at the stimulus boundary so every table axis remains a measured 10–90%
slew. Setup/hold clock stimuli must use the related-pin axis.
"""

import re


class _DeckWriter:
    """Rewrite OpenRAM's ngspice control card without changing file APIs."""

    def __init__(self, stream, save_waveforms):
        self._stream = stream
        self._save_waveforms = save_waveforms

    def write(self, data):
        if not self._save_waveforms:
            data = re.sub(r"POST=1\s*", "", data, flags=re.I)
            data = re.sub(r"\s+PROBE(?=\s|$)", "", data, flags=re.I)
        return self._stream.write(data)

    def __getattr__(self, name):
        return getattr(self._stream, name)


def stimulus_class(base):
    class LibertyStimuli(base):
        def write_control(self, *args, **kwargs):
            from openram import OPTS
            original = self.sf
            self.sf = _DeckWriter(
                original,
                bool(getattr(OPTS, "spice_save_waveforms", False)),
            )
            try:
                return super().write_control(*args, **kwargs)
            finally:
                self.sf = original

        def gen_pwl(self, sig_name, clk_times, data_values, period, slew, setup):
            return super().gen_pwl(sig_name, clk_times, data_values, period,
                                   slew / 0.8, setup)

        def gen_pulse(self, sig_name, v1, v2, offset, period, t_rise, t_fall):
            return super().gen_pulse(sig_name, v1, v2, offset, period,
                                     t_rise / 0.8, t_fall / 0.8)
    return LibertyStimuli


def setup_hold_class(base):
    class SeparateClockSlew(base):
        def write_clock(self):
            self.stim.gen_pwl(
                sig_name="clk",
                clk_times=[0, 0.1 * self.period, self.period, 2 * self.period],
                data_values=[0, 1, 0, 1],
                period=2 * self.period,
                slew=self.related_input_slew,
                setup=0,
            )
    return SeparateClockSlew


def install():
    import importlib
    stimuli = importlib.import_module("openram.characterizer.stimuli")
    delay = importlib.import_module("openram.characterizer.delay")
    setup_hold = importlib.import_module("openram.characterizer.setup_hold")
    lib = importlib.import_module("openram.characterizer.lib")
    corrected = stimulus_class(stimuli.stimuli)
    delay.stimuli = corrected
    setup_hold.stimuli = corrected
    lib.setup_hold = setup_hold_class(setup_hold.setup_hold)

    # Keep functional storage probes consistent with the cell_format that
    # the runner validates against the actual installed netlist.
    functional = importlib.import_module("openram.characterizer.functional")
    def get_bit_name(self):
        from openram import OPTS
        cell_name = OPTS.cell_format.format(
            name=self.sram.name,
            hier_sep=OPTS.hier_seperator,
            row=0,
            col=0,
        )
        sep = OPTS.hier_seperator
        return (cell_name + sep + "Q", cell_name + sep + "Q_bar")
    functional.functional.get_bit_name = get_bit_name
