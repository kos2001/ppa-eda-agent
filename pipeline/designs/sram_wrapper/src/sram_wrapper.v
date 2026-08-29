// Macro-heavy vertical-slice test case: a real sky130 OpenRAM SRAM macro
// (sky130_sram_1kbyte_1rw1r_32x256_8 — 256 x 32b, 1 read/write port +
// 1 read-only port) wrapped with a small free-running address generator.
// Exists to validate the pipeline's macro placement / macro-aware PDN /
// macro LEF+LIB+GDS handling path, distinct from counter4's pure
// standard-cell case — see topology-analyst.md's has_macros classification.
//
// Not meant to be a useful memory controller — csb0/web0 are tied
// active so the macro (and its power/timing paths) are always exercised
// during STA/power analysis, which is what this test case is for.
module sram_wrapper (
    input  wire        clk,
    input  wire        rst,
    output wire [31:0]  dout0,
    output wire [31:0]  dout1
);

  reg [7:0] addr_ctr;

  always @(posedge clk) begin
    if (rst)
      addr_ctr <= 8'd0;
    else
      addr_ctr <= addr_ctr + 8'd1;
  end

  wire [7:0] addr0 = addr_ctr;
  wire [7:0] addr1 = addr_ctr - 8'd1;  // read the slot written last cycle

  wire [31:0] sram_dout0;
  wire [31:0] sram_dout1;

  sky130_sram_1kbyte_1rw1r_32x256_8 u_sram (
      .clk0  (clk),
      .csb0  (1'b0),
      .web0  (addr_ctr[0]),        // alternate write/read on port 0
      .wmask0(4'b1111),
      .addr0 (addr0),
      .din0  ({24'd0, addr_ctr}),
      .dout0 (sram_dout0),
      .clk1  (clk),
      .csb1  (1'b0),
      .addr1 (addr1),
      .dout1 (sram_dout1)
  );

  // Register the macro's outputs right at the macro boundary instead of
  // driving the top-level pads directly from its output pins: a
  // standard-cell flop next to the macro gives the long net to the pad
  // an ordinary strong driver. Good practice, and cheap.
  //
  // This comment used to justify the registers with a max-transition
  // limit on dout0/dout1. That was wrong, and is corrected here rather
  // than quietly deleted because it sent the investigation after the
  // wrong nets for several sessions. Reading the .lib's bus blocks
  // directly, `max_transition : 0.04` is on addr0, addr1 and wmask0 —
  // all *inputs*. dout0 and dout1 carry no such limit. The RSZ-0090
  // abort comes from the address side, not the data side.
  //
  // The 0.04 ns is not an electrical requirement either: every timing
  // table in that .lib is indexed `index_1("0.00125, 0.005, 0.04")`, so
  // the "constraint" is just where characterisation stopped. See
  // pipeline/model_validity.py and lib/*.relaxed.lib.
  reg [31:0] dout0_r;
  reg [31:0] dout1_r;
  always @(posedge clk) begin
    dout0_r <= sram_dout0;
    dout1_r <= sram_dout1;
  end
  assign dout0 = dout0_r;
  assign dout1 = dout1_r;

endmodule
