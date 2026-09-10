// Testbench for gcd (the PyMTL GcdUnit vendored from
// OpenROAD-flow-scripts), in the shape power_activity.py expects: the
// design instantiated as `dut`, `$dumpvars` on the testbench for the VCD
// at scope gcd_tb/dut, one "ok"/"err" line per transaction.
//
// Protocol, from the RTL: a val/rdy request carrying {a, b} as two
// 16-bit halves of req_msg, a val/rdy response carrying gcd(a, b) in
// resp_msg. The testbench computes the expected value with Euclid's
// algorithm in plain Verilog so the check does not depend on the design.
//
// The workload mixes operand sizes deliberately: a GCD's cycle count is
// data-dependent (a and b subtract down), so a handful of large coprime
// pairs keep the datapath busy for hundreds of cycles, which is the
// activity a fixed toggle rate cannot guess.
`timescale 1ns/1ps
module gcd_tb;
  reg         clk;
  reg         reset;
  reg  [31:0] req_msg;
  reg         req_val;
  wire        req_rdy;
  wire [15:0] resp_msg;
  wire        resp_val;
  reg         resp_rdy;

  gcd dut (
    .clk(clk),
    .req_msg(req_msg),
    .req_rdy(req_rdy),
    .req_val(req_val),
    .reset(reset),
    .resp_msg(resp_msg),
    .resp_rdy(resp_rdy),
    .resp_val(resp_val)
  );

  always #5 clk = ~clk;

  function [15:0] euclid;
    input [15:0] a;
    input [15:0] b;
    reg [15:0] x, y, t;
    begin
      x = a; y = b;
      while (y != 0) begin
        t = y;
        y = x % y;
        x = t;
      end
      euclid = x;
    end
  endfunction

  reg [15:0] a, b, expected;
  integer i;
  integer errors;
  integer cycles;

  initial begin
    $dumpvars(0, gcd_tb);
    clk = 0;
    reset = 1;
    req_val = 0;
    req_msg = 0;
    resp_rdy = 1;
    errors = 0;
    repeat (3) @(posedge clk);
    #1 reset = 0;

    for (i = 0; i < 24; i = i + 1) begin
      case (i % 4)
        0: begin a = $random; b = $random; end          // arbitrary
        1: begin a = 16'd65521; b = 16'd65519; end      // large coprimes: many steps
        2: begin a = $random; b = a * 3; end            // easy: b multiple of a
        default: begin a = 16'd60000 + i; b = 16'd7; end
      endcase
      if (b == 0) b = 16'd1;
      expected = euclid(a, b);

      // Request handshake.
      req_msg = {a, b};
      req_val = 1;
      @(posedge clk);
      while (!req_rdy) @(posedge clk);
      #1 req_val = 0;

      // Response handshake, with a cycle bound so a stuck netlist ends
      // the run instead of hanging it.
      cycles = 0;
      while (!resp_val && cycles < 200000) begin
        @(posedge clk); #1;
        cycles = cycles + 1;
      end
      if (resp_msg !== expected || !resp_val) errors = errors + 1;
      $display("gcd(%0d, %0d) expected %0d, got %0d after %0d cycles (%s)",
               a, b, expected, resp_msg, cycles,
               (resp_val && resp_msg === expected) ? "ok " : "err");
      @(posedge clk); #1;
    end

    $display("gcd_tb: %0d error(s)", errors);
    $finish;
  end
endmodule
