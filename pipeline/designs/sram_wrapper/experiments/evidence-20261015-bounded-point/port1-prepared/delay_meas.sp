* Cycle 0      Port All    0.00 ns:      : Idle cycle (no positive clock edge)
* Cycle 1      Port 0      10.00 ns:      : W data 1 address 00000001
* Cycle 2      Port 0      20.00 ns:      : W data 0 address 11111111 to write value
* Cycle 3      Port 0      30.00 ns:      : Clock only on port 0
* Cycle 4      Port 1      40.00 ns:      : R data 1 address 00000001 to set dout caps
* Cycle 5      Port 1      50.00 ns:      : R data 0 address 11111111 to check W0 worked
* Cycle 6      Port 1      60.00 ns:      : Clock only on port 1
* Cycle 7      Port All    70.00 ns:      : Idle cycle (if read takes >1 cycle)
* Cycle 8      Port 0      80.00 ns:      : W data 1 address 11111111 to write value
* Cycle 9      Port 0      90.00 ns:      : Clock only on port 0
* Cycle 10     Port 0      100.00 ns:      : W data 0 address 00000001 to clear din caps
* Cycle 11     Port 1      110.00 ns:      : Clock only on port 1
* Cycle 12     Port 1      120.00 ns:      : R data 0 address 00000001 to clear dout caps
* Cycle 13     Port 1      130.00 ns:      : R data 1 address 11111111 to check W1 worked
* Cycle 14     Port All    140.00 ns:      : Idle cycle (if read takes >1 cycle))
* Read ports 1
.meas tran delay_lh1 TRIG v(clk1) VAL=0.8 FALL=1 TD=130n TARG v(dout1_31) VAL=0.8 RISE=1 TD=130n

.meas tran delay_hl1 TRIG v(clk1) VAL=0.8 FALL=1 TD=50n TARG v(dout1_31) VAL=0.8 FALL=1 TD=50n

.meas tran slew_lh1 TRIG v(dout1_31) VAL=0.16000000000000003 RISE=1 TD=130n TARG v(dout1_31) VAL=1.4400000000000002 RISE=1 TD=130n

.meas tran slew_hl1 TRIG v(dout1_31) VAL=1.4400000000000002 FALL=1 TD=50n TARG v(dout1_31) VAL=0.16000000000000003 FALL=1 TD=50n

.meas tran read1_power1 avg par('(-1*v(vdd)*I(vvdd))') from=130n to=140n

.meas tran read0_power1 avg par('(-1*v(vdd)*I(vvdd))') from=50n to=60n

.meas tran disabled_read1_power1 avg par('(-1*v(vdd)*I(vvdd))') from=110n to=120n

.meas tran disabled_read0_power1 avg par('(-1*v(vdd)*I(vvdd))') from=60n to=70n

.meas tran v_bl_read_zero1 FIND v(Xsky130_sram_1kbyte_1rw1r_32x256_8.xbank0.bl_1_63) AT=62.5n 

.meas tran v_br_read_zero1 FIND v(Xsky130_sram_1kbyte_1rw1r_32x256_8.xbank0.br_1_63) AT=62.5n 

.meas tran v_bl_read_one1 FIND v(Xsky130_sram_1kbyte_1rw1r_32x256_8.xbank0.bl_1_63) AT=142.5n 

.meas tran v_br_read_one1 FIND v(Xsky130_sram_1kbyte_1rw1r_32x256_8.xbank0.br_1_63) AT=142.5n 

.meas tran v_delay_lh1 FIND v(dout1_31) AT=142.5n 

.meas tran v_delay_hl1 FIND v(dout1_31) AT=62.5n 

.meas tran delay_sen1 TRIG v(clk1) VAL=0.8 FALL=1 TD=55.0n TARG v(Xsky130_sram_1kbyte_1rw1r_32x256_8.s_en1) VAL=0.8 RISE=1 TD=55.0n

.meas tran v_q_a11111111_b31_read_zero1 FIND v(Xsky130_sram_1kbyte_1rw1r_32x256_8.xbank0.xbitcell_array.xbitcell_array.xbit_r127_c63.Q) AT=62.5n 

.meas tran v_q_a11111111_b31_read_one1 FIND v(Xsky130_sram_1kbyte_1rw1r_32x256_8.xbank0.xbitcell_array.xbitcell_array.xbit_r127_c63.Q) AT=142.5n 

.meas tran v_qbar_a11111111_b31_read_zero1 FIND v(Xsky130_sram_1kbyte_1rw1r_32x256_8.xbank0.xbitcell_array.xbitcell_array.xbit_r127_c63.Q_bar) AT=62.5n 

.meas tran v_qbar_a11111111_b31_read_one1 FIND v(Xsky130_sram_1kbyte_1rw1r_32x256_8.xbank0.xbitcell_array.xbitcell_array.xbit_r127_c63.Q_bar) AT=142.5n 

* Write ports 0
.meas tran write1_power0 avg par('(-1*v(vdd)*I(vvdd))') from=80n to=90n

.meas tran write0_power0 avg par('(-1*v(vdd)*I(vvdd))') from=20n to=30n

.meas tran disabled_write1_power0 avg par('(-1*v(vdd)*I(vvdd))') from=90n to=100n

.meas tran disabled_write0_power0 avg par('(-1*v(vdd)*I(vvdd))') from=30n to=40n

.meas tran v_q_a11111111_b31_write_zero0 FIND v(Xsky130_sram_1kbyte_1rw1r_32x256_8.xbank0.xbitcell_array.xbitcell_array.xbit_r127_c63.Q) AT=32.5n 

.meas tran v_q_a11111111_b31_write_one0 FIND v(Xsky130_sram_1kbyte_1rw1r_32x256_8.xbank0.xbitcell_array.xbitcell_array.xbit_r127_c63.Q) AT=92.5n 

.meas tran v_qbar_a11111111_b31_write_zero0 FIND v(Xsky130_sram_1kbyte_1rw1r_32x256_8.xbank0.xbitcell_array.xbitcell_array.xbit_r127_c63.Q_bar) AT=32.5n 

.meas tran v_qbar_a11111111_b31_write_one0 FIND v(Xsky130_sram_1kbyte_1rw1r_32x256_8.xbank0.xbitcell_array.xbitcell_array.xbit_r127_c63.Q_bar) AT=92.5n 

