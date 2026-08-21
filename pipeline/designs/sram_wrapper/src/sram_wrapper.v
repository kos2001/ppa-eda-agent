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
  // driving the top-level pads directly from its output pins. Real
  // physical-constraint finding from this vertical slice: the macro's
  // own .lib characterizes a tight max-transition limit on dout0/dout1
  // (~0.04ns) that the resizer cannot meet driving all the way to
  // die-edge pads (RSZ-0090, best achievable 0.043ns) — a standard-cell
  // flop right next to the macro gives the long net to the pad a strong,
  // ordinary driver instead of the macro's own constrained output stage.
  reg [31:0] dout0_r;
  reg [31:0] dout1_r;
  always @(posedge clk) begin
    dout0_r <= sram_dout0;
    dout1_r <= sram_dout1;
  end
  assign dout0 = dout0_r;
  assign dout1 = dout1_r;

endmodule
