// sram_wrapper with a dedicated two-inverter driver on every live macro input.
//
// The shipped wrapper drives the macro's address pins through two
// hand-instantiated buf_16. Measured on the finished run, the resizer
// shrank some of them (addr0[0]'s last stage became a clkbuf_2) and the
// placer left the rest 50-80 um from the pin they drive, so the pins settle
// at 0.07-0.12 ns at the slow corner against a model characterised to 0.04.
//
// Two facts from the standard-cell liberty (pipeline/lib_slew_floor.py,
// ss_100C_1v60, 8.5 fF = the macro's 6.9 fF pin plus a few um of wire):
//
//   buf_16     0.048 ns   (two stages: its own output stage is slow to rise)
//   clkinv_16  0.026 ns   (one stage)
//
// so the last stage that can meet the macro's 0.04 ns is an inverter, not a
// buffer. Each live input gets  flop -> clkinv_4 -> clkinv_16 -> pin.  The
// instance names contain "io_drv" so RSZ_DONT_TOUCH_RX can keep the
// resizer from undoing the sizing, and so MANUAL_GLOBAL_PLACEMENTS can sit
// the last stage next to its pin.
//
// Constant inputs (csb0, csb1, wmask0, din0[31:8]) stay tied: nothing
// switches there, so there is no transition to be slow.
`ifndef IO_DRV1
`define IO_DRV1 sky130_fd_sc_hd__clkinv_4
`endif
`ifndef IO_DRV2
`define IO_DRV2 sky130_fd_sc_hd__clkinv_16
`endif

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

  // For a free-running counter the previous value is the registered copy.
  reg [7:0] addr_prev;
  always @(posedge clk) begin
    if (rst)
      addr_prev <= 8'd0;
    else
      addr_prev <= addr_ctr;
  end

  wire [7:0] addr0;
  wire [7:0] addr1;
  wire [7:0] din0_lo;
  wire       web0;

  genvar i;
  generate
    for (i = 0; i < 8; i = i + 1) begin : gen_io_drv_addr0
      wire m;
      `IO_DRV1 u_a (.A(addr_ctr[i]), .Y(m));
      `IO_DRV2 u_b (.A(m), .Y(addr0[i]));
    end
    for (i = 0; i < 8; i = i + 1) begin : gen_io_drv_addr1
      wire m;
      `IO_DRV1 u_a (.A(addr_prev[i]), .Y(m));
      `IO_DRV2 u_b (.A(m), .Y(addr1[i]));
    end
    for (i = 0; i < 8; i = i + 1) begin : gen_io_drv_din0
      wire m;
      `IO_DRV1 u_a (.A(addr_ctr[i]), .Y(m));
      `IO_DRV2 u_b (.A(m), .Y(din0_lo[i]));
    end
  endgenerate

  wire web_m;
  `IO_DRV1 io_drv_web0_a (.A(addr_ctr[0]), .Y(web_m));
  `IO_DRV2 io_drv_web0_b (.A(web_m), .Y(web0));

`ifdef IO_CLK_DRV
  // The macro's two clock pins are sinks CTS reaches by a long leaf wire.
  wire clk0_m, clk1_m, clk0_pin, clk1_pin;
  `IO_DRV1 io_drv_clk0_a (.A(clk), .Y(clk0_m));
  `IO_DRV2 io_drv_clk0_b (.A(clk0_m), .Y(clk0_pin));
  `IO_DRV1 io_drv_clk1_a (.A(clk), .Y(clk1_m));
  `IO_DRV2 io_drv_clk1_b (.A(clk1_m), .Y(clk1_pin));
`else
  wire clk0_pin = clk;
  wire clk1_pin = clk;
`endif

  wire [31:0] sram_dout0;
  wire [31:0] sram_dout1;

  sky130_sram_1kbyte_1rw1r_32x256_8 u_sram (
      .clk0  (clk0_pin),
      .csb0  (1'b0),
      .web0  (web0),                // alternate write/read on port 0
      .wmask0(4'b1111),
      .addr0 (addr0),
      .din0  ({24'd0, din0_lo}),
      .dout0 (sram_dout0),
      .clk1  (clk1_pin),
      .csb1  (1'b0),
      .addr1 (addr1),
      .dout1 (sram_dout1)
  );

  // Register the macro's outputs at the macro boundary.
  reg [31:0] dout0_r;
  reg [31:0] dout1_r;
  always @(posedge clk) begin
    dout0_r <= sram_dout0;
    dout1_r <= sram_dout1;
  end
  assign dout0 = dout0_r;
  assign dout1 = dout1_r;

endmodule
