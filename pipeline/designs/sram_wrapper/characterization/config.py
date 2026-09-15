"""SPICE sweep for the installed 32x256 1rw1r macro (not an analytical model).

Run with pipeline/characterize_sram.py and the matching PDK SPICE netlist.
Results stay separate from the production Liberty until verified.
"""
from pathlib import Path

tech_name = "sky130"
word_size = 32
num_words = 256
write_size = 8
num_rw_ports = 1
num_r_ports = 1
num_w_ports = 0
num_banks = 1
words_per_row = 2  # recorded in the installed macro SPICE bank header
output_name = "sky130_sram_1kbyte_1rw1r_32x256_8"
output_path = str(Path(__file__).resolve().parents[1] / "runs" / "characterization")
analytical_delay = False
spice_name = "ngspice"
use_nix = False  # use the explicitly installed local simulator
slew_scales = [0.25, 1, 8, 52]  # 0.260 ns covers measured 0.223766 ns + 15%
load_scales = [0.25, 1, 4]
process_corners = ["TT"]
supply_voltages = [1.8]
temperatures = [25]
nominal_corner_only = True
netlist_only = True
check_lvsdrc = False  # characterize existing netlist; physical checks run in OpenLane
trim_netlist = False
keep_temp = True
num_sim_threads = 2
# Liberty characterization consumes .meas results; retaining every transient
# waveform for this million-device macro is needlessly expensive.
spice_save_waveforms = False
# Resolve this path against the installed netlist before simulation: bank's
# Xbitcell_array instantiates replica_bitcell_array, whose Xbitcell_array
# instantiates bitcell_array containing the storage cells.
cell_format = "X{name}{hier_sep}xbank0{hier_sep}xbitcell_array{hier_sep}xbitcell_array{hier_sep}xbit_r{row}_c{col}"
# Bank formal input s_en0/1 connects to the same-named top-level net.
# ngspice exposes that connected net, not a bank-local formal-pin alias.
sen_format = "X{name}{hier_sep}s_en"
