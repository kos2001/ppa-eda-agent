v {xschem version=3.4.4 file_version=1.2
* A 2-input NAND — the first cell here that is not a single stack.
*
* What it adds over inv: the pull-down is two nfets in SERIES, so the
* inverter's sizing logic does not carry over. Matching a series stack
* needs roughly twice the nfet width, not a wider pfet, which is why
* this cell exists in a repo whose one repair pattern widens W_P: a
* pattern that fired here would be firing on a failure it was never
* shown to fix.
*
* Wired by net label rather than by drawn wire, the same as the ring:
* same-named labels are the same net, and the device stubs are copied
* from the PDK's own passgate.sch placement, which is what got the
* inverter's connectivity right on the first try.
}
G {}
K {}
V {}
S {}
E {}
N 270 -200 290 -200 { lab=ni}
N 350 -200 370 -200 { lab=Z}
N 280 -120 320 -120 { lab=A}
N 320 -160 320 -120 { lab=A}
N 320 -230 320 -200 { lab=VSS}
N 470 -200 490 -200 { lab=VSS}
N 550 -200 570 -200 { lab=ni}
N 480 -120 520 -120 { lab=B}
N 520 -160 520 -120 { lab=B}
N 520 -230 520 -200 { lab=VSS}
N 270 -470 290 -470 { lab=VDD}
N 350 -470 370 -470 { lab=Z}
N 280 -550 320 -550 { lab=A}
N 320 -550 320 -510 { lab=A}
N 320 -470 320 -440 { lab=VDD}
N 470 -470 490 -470 { lab=VDD}
N 550 -470 570 -470 { lab=Z}
N 480 -550 520 -550 { lab=B}
N 520 -550 520 -510 { lab=B}
N 520 -470 520 -440 { lab=VDD}
C {devices/lab_pin.sym} 270 -200 0 0 {name=ln1 lab=ni}
C {devices/lab_pin.sym} 370 -200 0 1 {name=lz1 lab=Z}
C {devices/lab_pin.sym} 280 -120 0 0 {name=la1 lab=A}
C {devices/lab_pin.sym} 320 -230 3 1 {name=lb1 lab=VSS}
C {sky130_fd_pr/nfet_01v8.sym} 320 -180 1 1 {name=M1
L=L_N
W=W_N
nf=1
mult=1
ad="'int((nf+1)/2) * W/nf * 0.29'"
pd="'2*int((nf+1)/2) * (W/nf + 0.29)'"
as="'int((nf+2)/2) * W/nf * 0.29'"
ps="'2*int((nf+2)/2) * (W/nf + 0.29)'"
nrd="'0.29 / W'" nrs="'0.29 / W'"
sa=0 sb=0 sd=0
model=nfet_01v8
spiceprefix=X
}
C {devices/lab_pin.sym} 470 -200 0 0 {name=ln2 lab=VSS}
C {devices/lab_pin.sym} 570 -200 0 1 {name=lz2 lab=ni}
C {devices/lab_pin.sym} 480 -120 0 0 {name=la2 lab=B}
C {devices/lab_pin.sym} 520 -230 3 1 {name=lb2 lab=VSS}
C {sky130_fd_pr/nfet_01v8.sym} 520 -180 1 1 {name=M2
L=L_N
W=W_N
nf=1
mult=1
ad="'int((nf+1)/2) * W/nf * 0.29'"
pd="'2*int((nf+1)/2) * (W/nf + 0.29)'"
as="'int((nf+2)/2) * W/nf * 0.29'"
ps="'2*int((nf+2)/2) * (W/nf + 0.29)'"
nrd="'0.29 / W'" nrs="'0.29 / W'"
sa=0 sb=0 sd=0
model=nfet_01v8
spiceprefix=X
}
C {devices/lab_pin.sym} 270 -470 0 0 {name=lp1 lab=VDD}
C {devices/lab_pin.sym} 370 -470 0 1 {name=lpz1 lab=Z}
C {devices/lab_pin.sym} 280 -550 0 0 {name=lpa1 lab=A}
C {devices/lab_pin.sym} 320 -440 3 0 {name=lpb1 lab=VDD}
C {sky130_fd_pr/pfet_01v8.sym} 320 -490 3 1 {name=M3
L=L_P
W=W_P
nf=1
mult=1
ad="'int((nf+1)/2) * W/nf * 0.29'"
pd="'2*int((nf+1)/2) * (W/nf + 0.29)'"
as="'int((nf+2)/2) * W/nf * 0.29'"
ps="'2*int((nf+2)/2) * (W/nf + 0.29)'"
nrd="'0.29 / W'" nrs="'0.29 / W'"
sa=0 sb=0 sd=0
model=pfet_01v8
spiceprefix=X
}
C {devices/lab_pin.sym} 470 -470 0 0 {name=lp2 lab=VDD}
C {devices/lab_pin.sym} 570 -470 0 1 {name=lpz2 lab=Z}
C {devices/lab_pin.sym} 480 -550 0 0 {name=lpa2 lab=B}
C {devices/lab_pin.sym} 520 -440 3 0 {name=lpb2 lab=VDD}
C {sky130_fd_pr/pfet_01v8.sym} 520 -490 3 1 {name=M4
L=L_P
W=W_P
nf=1
mult=1
ad="'int((nf+1)/2) * W/nf * 0.29'"
pd="'2*int((nf+1)/2) * (W/nf + 0.29)'"
as="'int((nf+2)/2) * W/nf * 0.29'"
ps="'2*int((nf+2)/2) * (W/nf + 0.29)'"
nrd="'0.29 / W'" nrs="'0.29 / W'"
sa=0 sb=0 sd=0
model=pfet_01v8
spiceprefix=X
}
C {devices/ipin.sym} 180 -120 0 0 {name=p1 lab=A}
C {devices/ipin.sym} 180 -100 0 0 {name=p2 lab=B}
C {devices/opin.sym} 180 -80 0 0 {name=p3 lab=Z}
C {devices/iopin.sym} 180 -60 0 0 {name=p4 lab=VDD}
C {devices/iopin.sym} 180 -40 0 0 {name=p5 lab=VSS}
