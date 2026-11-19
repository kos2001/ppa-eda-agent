// Logical interface monitor, NOT a SRAM timing or memory model.
// All 57 macro input signals are observable through the wrapper's 64 outputs.
// Ideal gates check polarity and sequential RTL behavior only, not clock skew.
module sky130_fd_sc_hd__buf_16(input A, output X);
  assign X = A;
endmodule
module sky130_fd_sc_hd__clkinv_2(input A, output Y);
  assign Y = ~A;
endmodule
module sky130_fd_sc_hd__clkinv_4(input A, output Y);
  assign Y = ~A;
endmodule
module sky130_fd_sc_hd__clkinv_8(input A, output Y);
  assign Y = ~A;
endmodule
module sky130_fd_sc_hd__clkinv_16(input A, output Y);
  assign Y = ~A;
endmodule
module sky130_sram_1kbyte_1rw1r_32x256_8(
  input clk0, csb0, web0, input [3:0] wmask0,
  input [7:0] addr0, input [31:0] din0, output [31:0] dout0,
  input clk1, csb1, input [7:0] addr1, output [31:0] dout1
);
  assign dout0 = din0;
`ifdef INTERFACE_NEGATIVE_CONTROL
  wire observed_web0 = ~web0;
`else
  wire observed_web0 = web0;
`endif
  assign dout1 = {7'b0, clk0, csb0, observed_web0, wmask0, addr0, clk1, csb1, addr1};
endmodule
