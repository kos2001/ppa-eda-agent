v {xschem version=3.4.4 file_version=1.2
* A five-stage ring oscillator built from the inverter cell one
* directory over — the point being that it instantiates inv/inv.sym
* rather than redrawing two transistors. That is what a symbol is for,
* and it is the reason custom_bridge mounts the whole analog tree
* instead of a single cell's directory.
*
* Wired by net label rather than by drawn wire. Labels with the same
* name are the same net in xschem, and for a ring the loop-back would
* otherwise be a long polyline running past every stage — a wire whose
* only job is to be geometrically correct is a wire that can be
* geometrically wrong.
}
G {}
K {}
V {}
S {}
E {}
C {inv/inv.sym} 400 -200 0 0 {name=x1}
C {devices/lab_pin.sym} 250 -220 0 0 {name=a1 lab=n0}
C {devices/lab_pin.sym} 550 -200 0 1 {name=z1 lab=n1}
C {devices/lab_pin.sym} 550 -220 0 1 {name=v1 lab=vdd}
C {devices/gnd.sym} 550 -180 0 0 {name=g1}
C {inv/inv.sym} 800 -200 0 0 {name=x2}
C {devices/lab_pin.sym} 650 -220 0 0 {name=a2 lab=n1}
C {devices/lab_pin.sym} 950 -200 0 1 {name=z2 lab=n2}
C {devices/lab_pin.sym} 950 -220 0 1 {name=v2 lab=vdd}
C {devices/gnd.sym} 950 -180 0 0 {name=g2}
C {inv/inv.sym} 1200 -200 0 0 {name=x3}
C {devices/lab_pin.sym} 1050 -220 0 0 {name=a3 lab=n2}
C {devices/lab_pin.sym} 1350 -200 0 1 {name=z3 lab=n3}
C {devices/lab_pin.sym} 1350 -220 0 1 {name=v3 lab=vdd}
C {devices/gnd.sym} 1350 -180 0 0 {name=g3}
C {inv/inv.sym} 1600 -200 0 0 {name=x4}
C {devices/lab_pin.sym} 1450 -220 0 0 {name=a4 lab=n3}
C {devices/lab_pin.sym} 1750 -200 0 1 {name=z4 lab=n4}
C {devices/lab_pin.sym} 1750 -220 0 1 {name=v4 lab=vdd}
C {devices/gnd.sym} 1750 -180 0 0 {name=g4}
C {inv/inv.sym} 2000 -200 0 0 {name=x5}
C {devices/lab_pin.sym} 1850 -220 0 0 {name=a5 lab=n4}
C {devices/lab_pin.sym} 2150 -200 0 1 {name=z5 lab=n0}
C {devices/lab_pin.sym} 2150 -220 0 1 {name=v5 lab=vdd}
C {devices/gnd.sym} 2150 -180 0 0 {name=g5}
C {devices/vsource.sym} 100 -100 0 0 {name=Vsup value=1.8}
N 100 -150 100 -130 { lab=vdd}
C {devices/lab_pin.sym} 100 -150 0 0 {name=l5 lab=vdd}
C {devices/gnd.sym} 100 -70 0 0 {name=l6}
C {devices/code.sym} 400 -400 0 0 {name=MODELS
only_toplevel=true
value="
%PDK_LIB%
"
spice_ignore=false}
C {devices/code.sym} 700 -400 0 0 {name=COMMANDS
only_toplevel=true
value=".param W_P=1.4266 W_N=0.5 L_P=0.15 L_N=0.15
.ic v(n0)=0
.control
tran 5p 20n
meas tran period TRIG v(n0) VAL=0.9 RISE=2 TARG v(n0) VAL=0.9 RISE=3
meas tran isup_avg AVG i(Vsup) FROM=5n TO=20n
quit
.endc
"}
