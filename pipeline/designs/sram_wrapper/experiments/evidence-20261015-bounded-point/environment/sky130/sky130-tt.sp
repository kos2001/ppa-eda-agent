SKY130 PVT transistor equivalence
.lib "/home/kos2001/ppa-sram-bounded-20261015/pdk/sky130A/libs.tech/ngspice/sky130.lib.spice" tt
.temp 25
vd d 0 1.8
vg g 0 1.8
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
