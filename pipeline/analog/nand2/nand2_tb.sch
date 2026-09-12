v {xschem version=3.4.4 file_version=1.2
* Testbench for nand2.sch.
*
* B is held at VDD and A switches: that is the input pattern where the
* pull-down has to conduct through BOTH series nfets, which is the case
* the series stack actually costs something in. Measuring the other
* pattern would flatter the cell.
}
G {}
K {}
V {}
S {}
E {}
C {nand2/nand2.sym} 400 -200 0 0 {name=x1}
C {devices/lab_pin.sym} 250 -220 0 0 {name=l1 lab=a}
C {devices/lab_pin.sym} 250 -200 0 0 {name=l2 lab=vdd}
C {devices/lab_pin.sym} 550 -220 0 1 {name=l3 lab=out}
C {devices/lab_pin.sym} 550 -200 0 1 {name=l4 lab=vdd}
C {devices/gnd.sym} 550 -180 0 0 {name=l5}
C {devices/vsource.sym} 100 -100 0 0 {name=Vsup value=1.8}
N 100 -150 100 -130 { lab=vdd}
C {devices/lab_pin.sym} 100 -150 0 0 {name=l6 lab=vdd}
C {devices/gnd.sym} 100 -70 0 0 {name=l7}
C {devices/vsource.sym} 200 -100 0 0 {name=Vin value="pulse(0 1.8 1n 100p 100p 2n 4n)"}
N 200 -150 200 -130 { lab=a}
C {devices/lab_pin.sym} 200 -150 0 0 {name=l8 lab=a}
C {devices/gnd.sym} 200 -70 0 0 {name=l9}
C {devices/capa.sym} 700 -100 0 0 {name=Cl m=1 value=10f}
N 700 -150 700 -130 { lab=out}
C {devices/lab_pin.sym} 700 -150 0 0 {name=l10 lab=out}
C {devices/gnd.sym} 700 -70 0 0 {name=l11}
C {devices/code.sym} 100 -400 0 0 {name=MODELS
only_toplevel=true
value="
%PDK_LIB%
"
spice_ignore=false}
C {devices/code.sym} 400 -400 0 0 {name=COMMANDS
only_toplevel=true
value=".param W_P=1.0 W_N=0.5 L_P=0.15 L_N=0.15
.control
dc Vin 0 1.8 0.01
meas dc vtrip WHEN v(out)=0.9 FALL=1
tran 10p 8n
meas tran tphl TRIG v(a) VAL=0.9 RISE=1 TARG v(out) VAL=0.9 FALL=1
meas tran tplh TRIG v(a) VAL=0.9 FALL=1 TARG v(out) VAL=0.9 RISE=1
meas tran ivdd_avg AVG i(Vsup) FROM=0 TO=8n
quit
.endc
"}
