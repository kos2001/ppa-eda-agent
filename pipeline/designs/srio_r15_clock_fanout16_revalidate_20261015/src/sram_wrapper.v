// sram_wrapper, second attempt at the macro's own IO: one counter per port and a
// tapered inverter chain into every live pin.
//
// Round one (sram_wrapper_io.v) gave each pin a clkinv_4 -> clkinv_16 pair fed
// by the shared counter. The finished run's path to u_sram/addr0[3], at the slow
// corner:
//
//   _085_/Q          dfxtp_2     slew 0.793   <- one flop driving 0.1 pF of wire
//   u_a/Y            clkinv_4    slew 0.239
//   u_b/Y            clkinv_16   slew 0.059   (library floor with a sharp input: 0.032)
//   u_sram/addr0[3]  macro pin   slew 0.079   <- x1.333, see below
//
// Two separate causes. The counter bit feeds three pins on three sides of a
// 480 um block, so its net is ~600 um long and its edge is the slowest thing in
// the chain; the last stage inherits that. Here each port has its own counter,
// so each flop drives only its own chain. And the macro's liberty measures slew
// between 10% and 90% of the swing where the standard cells use 20% and 80%, so
// STA reports the same edge 1.333x as slow at the macro pin.
//
// The counters carry (* keep *) so synthesis cannot merge three registers that
// count in lockstep back into one.
`ifndef IO_CHAIN4
`define IO_CHAIN2
`endif

module sram_wrapper (
    input  wire        clk,
    input  wire        rst,
    output wire [31:0]  dout0,
    output wire [31:0]  dout1
);

  // Port 0 address, port 0 data-in, port 1 address: three independent copies.
  (* keep *) reg [7:0] ctr_a0;
  (* keep *) reg [7:0] ctr_d;
  (* keep *) reg [7:0] ctr_a1;
  (* keep *) reg       started;
  always @(posedge clk) begin
    if (rst) begin
      ctr_a0  <= 8'd0;
      ctr_d   <= 8'd0;
      ctr_a1  <= 8'd0;
      started <= 1'b0;
    end else begin
      ctr_a0  <= ctr_a0 + 8'd1;
      ctr_d   <= ctr_d + 8'd1;
      // The slot written last cycle: the same count, one cycle behind.
      started <= 1'b1;
      ctr_a1  <= ctr_a1 + {7'd0, started};
    end
  end

  wire [7:0] addr0;
  wire [7:0] addr1;
  wire [7:0] din0_lo;
  wire       web0;

  genvar i;
  generate
    for (i = 0; i < 8; i = i + 1) begin : gen_io_drv_addr0
`ifdef IO_CHAIN4
      wire m1, m2, m3;
      sky130_fd_sc_hd__clkinv_2  u_a (.A(ctr_a0[i]), .Y(m1));
      sky130_fd_sc_hd__clkinv_4  u_b (.A(m1), .Y(m2));
`ifdef IO_STRONG_PREDRIVER
      sky130_fd_sc_hd__clkinv_16 u_c (.A(m2), .Y(m3));
`else
      sky130_fd_sc_hd__clkinv_8  u_c (.A(m2), .Y(m3));
`endif
      sky130_fd_sc_hd__clkinv_16 u_d (.A(m3), .Y(addr0[i]));
`else
      wire m1;
      sky130_fd_sc_hd__clkinv_8  u_a (.A(ctr_a0[i]), .Y(m1));
      sky130_fd_sc_hd__clkinv_16 u_b (.A(m1), .Y(addr0[i]));
`endif
    end
    for (i = 0; i < 8; i = i + 1) begin : gen_io_drv_addr1
`ifdef IO_CHAIN4
      wire m1, m2, m3;
      sky130_fd_sc_hd__clkinv_2  u_a (.A(ctr_a1[i]), .Y(m1));
      sky130_fd_sc_hd__clkinv_4  u_b (.A(m1), .Y(m2));
`ifdef IO_STRONG_PREDRIVER
      sky130_fd_sc_hd__clkinv_16 u_c (.A(m2), .Y(m3));
`else
      sky130_fd_sc_hd__clkinv_8  u_c (.A(m2), .Y(m3));
`endif
      sky130_fd_sc_hd__clkinv_16 u_d (.A(m3), .Y(addr1[i]));
`else
      wire m1;
      sky130_fd_sc_hd__clkinv_8  u_a (.A(ctr_a1[i]), .Y(m1));
      sky130_fd_sc_hd__clkinv_16 u_b (.A(m1), .Y(addr1[i]));
`endif
    end
    for (i = 0; i < 8; i = i + 1) begin : gen_io_drv_din0
`ifdef IO_CHAIN4
      wire m1, m2, m3;
      sky130_fd_sc_hd__clkinv_2  u_a (.A(ctr_d[i]), .Y(m1));
      sky130_fd_sc_hd__clkinv_4  u_b (.A(m1), .Y(m2));
`ifdef IO_STRONG_PREDRIVER
      sky130_fd_sc_hd__clkinv_16 u_c (.A(m2), .Y(m3));
`else
      sky130_fd_sc_hd__clkinv_8  u_c (.A(m2), .Y(m3));
`endif
      sky130_fd_sc_hd__clkinv_16 u_d (.A(m3), .Y(din0_lo[i]));
`else
      wire m1;
      sky130_fd_sc_hd__clkinv_8  u_a (.A(ctr_d[i]), .Y(m1));
      sky130_fd_sc_hd__clkinv_16 u_b (.A(m1), .Y(din0_lo[i]));
`endif
    end
  endgenerate

`ifdef IO_CHAIN4
  wire web_m1, web_m2, web_m3;
  sky130_fd_sc_hd__clkinv_2  io_drv_web0_a (.A(ctr_d[0]), .Y(web_m1));
  sky130_fd_sc_hd__clkinv_4  io_drv_web0_b (.A(web_m1), .Y(web_m2));
`ifdef IO_STRONG_PREDRIVER
  sky130_fd_sc_hd__clkinv_16 io_drv_web0_c (.A(web_m2), .Y(web_m3));
`else
  sky130_fd_sc_hd__clkinv_8  io_drv_web0_c (.A(web_m2), .Y(web_m3));
`endif
  sky130_fd_sc_hd__clkinv_16 io_drv_web0_d (.A(web_m3), .Y(web0));
`else
  wire web_m1;
  sky130_fd_sc_hd__clkinv_8  io_drv_web0_a (.A(ctr_d[0]), .Y(web_m1));
  sky130_fd_sc_hd__clkinv_16 io_drv_web0_b (.A(web_m1), .Y(web0));
`endif

`ifdef IO_CLK_DRV
  // The macro's two clock pins are CTS sinks reached by a long leaf wire; round
  // two measured them at 0.24-0.29 ns against the 0.04 the macro was
  // characterised to. Round three's four-stage clock chain crashed the post-CTS
  // resizer: the macro's clock now arrives ~0.3 ns late, every data pin into it
  // has a hold problem, and repair_timing tried to put a buffer at the output of
  // a dont_touch driver. IO_CLK_CHAIN2 uses two stages (a shorter delay).
  wire clk0_pin, clk1_pin;
`ifdef IO_CLK_CHAIN2
  wire clk0_m1, clk1_m1;
`ifdef IO_CLK_STRONG_PREDRIVER
  sky130_fd_sc_hd__clkinv_16 io_drv_clk0_a (.A(clk),     .Y(clk0_m1));
`else
  sky130_fd_sc_hd__clkinv_8  io_drv_clk0_a (.A(clk),     .Y(clk0_m1));
`endif
  sky130_fd_sc_hd__clkinv_16 io_drv_clk0_b (.A(clk0_m1), .Y(clk0_pin));
`ifdef IO_CLK_STRONG_PREDRIVER
  sky130_fd_sc_hd__clkinv_16 io_drv_clk1_a (.A(clk),     .Y(clk1_m1));
`else
  sky130_fd_sc_hd__clkinv_8  io_drv_clk1_a (.A(clk),     .Y(clk1_m1));
`endif
  sky130_fd_sc_hd__clkinv_16 io_drv_clk1_b (.A(clk1_m1), .Y(clk1_pin));
`else
  wire clk0_m1, clk0_m2, clk0_m3;
  sky130_fd_sc_hd__clkinv_2  io_drv_clk0_a (.A(clk),     .Y(clk0_m1));
  sky130_fd_sc_hd__clkinv_4  io_drv_clk0_b (.A(clk0_m1), .Y(clk0_m2));
  sky130_fd_sc_hd__clkinv_8  io_drv_clk0_c (.A(clk0_m2), .Y(clk0_m3));
  sky130_fd_sc_hd__clkinv_16 io_drv_clk0_d (.A(clk0_m3), .Y(clk0_pin));
  wire clk1_m1, clk1_m2, clk1_m3;
  sky130_fd_sc_hd__clkinv_2  io_drv_clk1_a (.A(clk),     .Y(clk1_m1));
  sky130_fd_sc_hd__clkinv_4  io_drv_clk1_b (.A(clk1_m1), .Y(clk1_m2));
  sky130_fd_sc_hd__clkinv_8  io_drv_clk1_c (.A(clk1_m2), .Y(clk1_m3));
  sky130_fd_sc_hd__clkinv_16 io_drv_clk1_d (.A(clk1_m3), .Y(clk1_pin));
`endif
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
