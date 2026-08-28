// Blackbox port declaration for synthesis only — matches the real macro's
// ports exactly (see pdk/.../sky130_sram_macros/verilog/
// sky130_sram_1kbyte_1rw1r_32x256_8.v for the behavioral source this
// mirrors). Yosys treats `(* blackbox *)` modules as an opaque cell with
// this pin list; the real timing/power/physical view comes from
// EXTRA_LIBS/EXTRA_LEFS/EXTRA_GDS_FILES in config.json, not from here.
(* blackbox *)
module sky130_sram_1kbyte_1rw1r_32x256_8 (
    clk0, csb0, web0, wmask0, addr0, din0, dout0,
    clk1, csb1, addr1, dout1
);
  input         clk0;
  input         csb0;
  input         web0;
  input  [3:0]  wmask0;
  input  [7:0]  addr0;
  input  [31:0] din0;
  output [31:0] dout0;
  input         clk1;
  input         csb1;
  input  [7:0]  addr1;
  output [31:0] dout1;
endmodule
