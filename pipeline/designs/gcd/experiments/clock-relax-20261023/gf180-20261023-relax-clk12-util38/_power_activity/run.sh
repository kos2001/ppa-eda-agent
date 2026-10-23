
set -e
cd /work
iverilog -g2012 -DFUNCTIONAL -DUNIT_DELAY=#1 -Pgcd_tb.CLOCK_PERIOD_NS=12.0 -o sim.vvp \
  /pdkv/gf180mcuD/libs.ref/gf180mcu_fd_sc_mcu7t5v0/verilog/primitives.v \
  /pdkv/gf180mcuD/libs.ref/gf180mcu_fd_sc_mcu7t5v0/verilog/gf180mcu_fd_sc_mcu7t5v0.v \
  /work/netlist.v /work/tb.v > compile.log 2>&1 || {
    echo "###COMPILE_FAILED###"; tail -20 compile.log; exit 1; }
echo "###SIM###"
vvp sim.vvp > simulation.log 2>&1 || { cat simulation.log; exit 1; }
tail -40 simulation.log
echo "###SIM_END###"
cat > sta.tcl <<'TCL'
read_liberty /pdkv/gf180mcuD/libs.ref/gf180mcu_fd_sc_mcu7t5v0/lib/gf180mcu_fd_sc_mcu7t5v0__ff_n40C_5v50.lib
read_verilog /work/netlist.v
link_design gcd
read_spef /work/parasitics.spef
read_sdc /work/constraints.sdc
puts "###VECTORLESS###"
report_power
puts "###ANNOTATE###"
read_vcd -scope gcd_tb/dut /work/dump.vcd
puts "###ANNOTATED###"
report_power
exit
TCL
sta -no_init -exit sta.tcl 2>&1
