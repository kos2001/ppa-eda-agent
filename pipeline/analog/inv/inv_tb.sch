v {xschem version=3.4.4 file_version=1.2
* Testbench for inv.sch — a schematic, not a hand-written deck.
*
* This is the entry point of the custom/analog flow: the schematic is the
* source, and the SPICE that ngspice runs is generated from it by
* xschem's own netlister (pipeline/custom_bridge.py netlist-schematic).
* Sizes live here as .param so the same cell can be swept without
* editing the cell.
}
G {}
K {}
V {}
S {}
E {}
C {inv.sym} 400 -200 0 0 {name=x1}
C {devices/lab_pin.sym} 250 -220 0 0 {name=l1 lab=in}
C {devices/lab_pin.sym} 550 -220 0 1 {name=l2 lab=vdd}
C {devices/lab_pin.sym} 550 -200 0 1 {name=l3 lab=out}
C {devices/gnd.sym} 550 -180 0 0 {name=l4}
C {devices/vsource.sym} 100 -100 0 0 {name=Vsup value=1.8}
C {devices/lab_pin.sym} 100 -130 0 0 {name=l5 lab=vdd}
C {devices/gnd.sym} 100 -70 0 0 {name=l6}
C {devices/vsource.sym} 200 -100 0 0 {name=Vin value="pulse(0 1.8 1n 100p 100p 2n 4n)"}
C {devices/lab_pin.sym} 200 -130 0 0 {name=l7 lab=in}
C {devices/gnd.sym} 200 -70 0 0 {name=l8}
C {devices/capa.sym} 700 -100 0 0 {name=Cl m=1 value=10f}
C {devices/lab_pin.sym} 700 -130 0 0 {name=l9 lab=out}
C {devices/gnd.sym} 700 -70 0 0 {name=l10}
C {devices/code.sym} 100 -400 0 0 {name=MODELS
only_toplevel=true
value="
%PDK_LIB%
"
spice_ignore=false}
C {devices/code_shown.sym} 400 -400 0 0 {name=COMMANDS
only_toplevel=true
value=".param W_P=1.0 W_N=0.5 L_P=0.15 L_N=0.15
.control
dc Vin 0 1.8 0.01
meas dc vtrip WHEN v(out)=0.9 FALL=1
tran 10p 8n
meas tran tphl TRIG v(in) VAL=0.9 RISE=1 TARG v(out) VAL=0.9 FALL=1
meas tran tplh TRIG v(in) VAL=0.9 FALL=1 TARG v(out) VAL=0.9 RISE=1
meas tran ivdd_avg AVG i(Vsup) FROM=0 TO=8n
quit
.endc
"}
