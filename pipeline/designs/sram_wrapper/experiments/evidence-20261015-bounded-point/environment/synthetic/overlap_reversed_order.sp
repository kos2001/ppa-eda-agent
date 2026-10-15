Model-bin equivalence overlap_reversed_order
.model n.2 nmos level=54 version=4.8.2 lmin=0.1u lmax=4u wmin=1u wmax=100u vth0=.8
.model n.1 nmos level=54 version=4.8.2 lmin=0.1u lmax=4u wmin=1u wmax=100u vth0=.3
vd d 0 1
vg g 0 1
m1 d g 0 0 n w=2u l=.5u
m2 d g 0 0 n w=4u l=1u nf=2
.options KLU
.control
set numdgt=15
op
print i(vd)
quit
.endc
.end
