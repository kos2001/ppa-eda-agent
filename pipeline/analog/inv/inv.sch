v {xschem version=3.4.4 file_version=1.2
* A CMOS inverter, drawn as a schematic — the entry point of the custom
* flow. The device instance parameters (ad/pd/as/ps/nrd/nrs, spiceprefix)
* are the sky130_fd_pr idiom copied verbatim from the PDK's own
* libs.tech/xschem/sky130_tests/passgate.sch rather than invented: they
* are what make the netlisted devices carry real diffusion parasitics.
}
G {}
K {}
V {}
S {}
E {}
N 320 -230 320 -200 { lab=VSS}
N 320 -470 320 -440 { lab=VDD}
N 270 -470 290 -470 { lab=VDD}
N 270 -200 290 -200 { lab=VSS}
N 350 -200 370 -200 { lab=Z}
N 350 -470 370 -470 { lab=Z}
N 370 -470 370 -340 { lab=Z}
N 370 -340 370 -200 { lab=Z}
N 370 -340 430 -340 { lab=Z}
N 320 -550 320 -510 { lab=A}
N 280 -550 320 -550 { lab=A}
N 320 -160 320 -120 { lab=A}
N 280 -120 320 -120 { lab=A}
C {devices/ipin.sym} 280 -550 0 0 {name=p1 lab=A}
C {devices/opin.sym} 430 -340 0 0 {name=p2 lab=Z}
C {devices/iopin.sym} 270 -470 0 1 {name=p3 lab=VDD}
C {devices/iopin.sym} 270 -200 0 1 {name=p4 lab=VSS}
C {devices/lab_pin.sym} 280 -120 0 0 {name=l1 lab=A}
C {devices/lab_pin.sym} 320 -230 3 1 {name=l2 lab=VSS}
C {devices/lab_pin.sym} 320 -440 3 0 {name=l3 lab=VDD}
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
C {sky130_fd_pr/pfet_01v8.sym} 320 -490 3 1 {name=M2
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
