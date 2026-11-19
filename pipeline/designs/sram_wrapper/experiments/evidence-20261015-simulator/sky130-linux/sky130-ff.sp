SKY130 PVT transistor equivalence
.lib "/tmp/ppa-spice-pdk-20261015/sky130A/libs.tech/ngspice/sky130.lib.spice" ff
.temp -40
vd d 0 1.95
vg g 0 1.95
x1 d g 0 0 sky130_fd_pr__nfet_01v8 w=1.26 l=.15
x2 d g 0 0 sky130_fd_pr__nfet_01v8 w=.42 l=.5
.options KLU
.control
set numdgt=15
op
print i(vd)
quit
.endc
.end
