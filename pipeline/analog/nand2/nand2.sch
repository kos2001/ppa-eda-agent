v {xschem version=3.4.4 file_version=1.2
* CMOS NAND2: parallel PMOS pull-up and series NMOS pull-down.
* Review layout: VDD above, VSS below; original D/G/S/B and device
* parameters retained. Native netlists are compared before/after redraw.
}
G {}
K {}
V {}
S {}
E {}
N 160 -440 740 -440 {lab=VDD}
N 320 -440 320 -370 {lab=VDD}
N 620 -440 620 -370 {lab=VDD}
N 320 -310 320 -260 {lab=Z}
N 620 -310 620 -260 {lab=Z}
N 320 -260 620 -260 {lab=Z}
N 620 -260 760 -260 {lab=Z}
N 320 -260 320 -170 {lab=Z}
N 320 -110 320 -10 {lab=ni}
N 320 50 320 100 {lab=VSS}
N 160 100 740 100 {lab=VSS}
N 140 -230 200 -230 {lab=A}
N 200 -340 200 -230 {lab=A}
N 200 -230 200 -140 {lab=A}
N 200 -340 280 -340 {lab=A}
N 200 -140 280 -140 {lab=A}
N 540 -340 580 -340 {lab=B}
N 200 20 280 20 {lab=B}
N 320 -340 490 -340 {lab=VDD}
N 620 -340 790 -340 {lab=VDD}
N 320 -140 490 -140 {lab=VSS}
N 320 20 490 20 {lab=VSS}
C {devices/ipin.sym} 140 -230 0 0 {name=p1 lab=A}
C {devices/ipin.sym} 540 -340 0 0 {name=p2 lab=B}
C {devices/opin.sym} 760 -260 0 0 {name=p3 lab=Z}
C {devices/iopin.sym} 160 -440 0 0 {name=p4 lab=VDD}
C {devices/iopin.sym} 160 100 0 0 {name=p5 lab=VSS}
C {devices/lab_pin.sym} 200 20 0 1 {name=lb lab=B}
C {devices/lab_pin.sym} 490 -340 0 1 {name=lbp1 lab=VDD}
C {devices/lab_pin.sym} 790 -340 0 1 {name=lbp2 lab=VDD}
C {devices/lab_pin.sym} 490 -140 0 1 {name=lbn1 lab=VSS}
C {devices/lab_pin.sym} 490 20 0 1 {name=lbn2 lab=VSS}
C {devices/lab_pin.sym} 320 -60 0 1 {name=lmid lab=ni}
C {sky130_fd_pr/nfet_01v8.sym} 300 -140 0 0 {name=M1
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
C {sky130_fd_pr/nfet_01v8.sym} 300 20 0 0 {name=M2
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
C {sky130_fd_pr/pfet_01v8.sym} 300 -340 0 0 {name=M3
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
C {sky130_fd_pr/pfet_01v8.sym} 600 -340 0 0 {name=M4
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
