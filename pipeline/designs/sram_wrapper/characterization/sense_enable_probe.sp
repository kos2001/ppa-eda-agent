* Hierarchical formal-pin versus connected-net measurement diagnostic
.subckt bank s_en0
Rload s_en0 0 1k
.ends
.subckt macro
Vsen s_en0 0 pulse(0 1.8 1n 0.1n 0.1n 2n 4n)
Xbank0 s_en0 bank
.ends
Xmacro macro
.tran 10p 5n
.meas tran wrong FIND v(xmacro.xbank0.s_en0) AT=2n
.meas tran correct FIND v(xmacro.s_en0) AT=2n
.end
