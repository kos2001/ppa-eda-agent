v {xschem version=3.4.4 file_version=1.2
* CMOS inverter: a connected pull-up / pull-down review sheet.
* Review layout: VDD above, VSS below; original D/G/S/B and device
* parameters retained. Native netlists are compared before/after redraw.
}
G {}
K {}
V {}
S {}
E {}
N 180 -420 520 -420 {lab=VDD}
N 340 -420 340 -350 {lab=VDD}
N 180 -40 520 -40 {lab=VSS}
N 340 -110 340 -40 {lab=VSS}
N 340 -290 340 -230 {lab=Z}
N 340 -230 340 -170 {lab=Z}
N 340 -230 520 -230 {lab=Z}
N 160 -230 240 -230 {lab=A}
N 240 -320 240 -230 {lab=A}
N 240 -230 240 -140 {lab=A}
N 240 -320 300 -320 {lab=A}
N 240 -140 300 -140 {lab=A}
N 340 -320 500 -320 {lab=VDD}
N 340 -140 500 -140 {lab=VSS}
C {devices/ipin.sym} 160 -230 0 0 {name=p1 lab=A}
C {devices/opin.sym} 520 -230 0 0 {name=p2 lab=Z}
C {devices/iopin.sym} 180 -420 0 0 {name=p3 lab=VDD}
C {devices/iopin.sym} 180 -40 0 0 {name=p4 lab=VSS}
C {devices/lab_pin.sym} 500 -320 0 1 {name=lbp lab=VDD}
C {devices/lab_pin.sym} 500 -140 0 1 {name=lbn lab=VSS}
C {sky130_fd_pr/nfet_01v8.sym} 320 -140 0 0 {name=M1
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
C {sky130_fd_pr/pfet_01v8.sym} 320 -320 0 0 {name=M2
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
