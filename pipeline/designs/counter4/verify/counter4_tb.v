// Testbench for counter4, in the shape power_activity.py expects: the
// design instantiated as `dut`, `$dumpvars` on the testbench so OpenSTA
// can read the VCD at scope counter4_tb/dut, and one "ok"/"err" line per
// check so a failing gate-level netlist is visible in the simulation
// output rather than only in the power number.
//
// The workload is the one this counter exists for: reset, count with
// enable held high through several wraps, then a stretch with enable
// toggling every cycle so the incrementer is exercised at half rate as
// well. Without a testbench the power figure for this design was
// OpenSTA's default toggle-rate estimate; with one it is measured.
`timescale 1ns/1ps
module counter4_tb;
  reg clk;
  reg rst;
  reg en;
  wire [3:0] count;

  counter4 dut (
    .clk(clk),
    .rst(rst),
    .en(en),
    .count(count)
  );

  always #5 clk = ~clk;

  reg [3:0] expected;
  integer i;
  integer errors;

  initial begin
    $dumpvars(0, counter4_tb);
    clk = 0;
    rst = 1;
    en = 0;
    expected = 4'd0;
    errors = 0;
    @(negedge clk);
    @(negedge clk);
    rst = 0;

    // Count freely through three full wraps.
    en = 1;
    for (i = 0; i < 48; i = i + 1) begin
      @(negedge clk);
      expected = expected + 4'd1;
      if (count !== expected) errors = errors + 1;
      $display("count %0d expected %0d (%s)", count, expected,
               count === expected ? "ok " : "err");
    end

    // Enable toggling every cycle: the incrementer at half rate.
    for (i = 0; i < 32; i = i + 1) begin
      en = i[0];
      @(negedge clk);
      if (en) expected = expected + 4'd1;
      if (count !== expected) errors = errors + 1;
      $display("count %0d expected %0d (%s)", count, expected,
               count === expected ? "ok " : "err");
    end

    // Synchronous reset in the middle of a count.
    rst = 1;
    @(negedge clk);
    rst = 0;
    expected = 4'd0;
    $display("count %0d expected %0d (%s)", count, expected,
             count === expected ? "ok " : "err");
    en = 1;
    for (i = 0; i < 16; i = i + 1) begin
      @(negedge clk);
      expected = expected + 4'd1;
      if (count !== expected) errors = errors + 1;
    end

    $display("counter4_tb: %0d error(s)", errors);
    $finish;
  end
endmodule
