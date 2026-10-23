`timescale 1ns/1ps
module counter4_regression;
  reg clk = 0;
  reg rst = 0;
  reg en = 0;
  wire [3:0] count;
  integer total = 0;
  integer checks = 0;
  integer seed;
  reg [31:0] random_state;
  counter4 dut (.clk(clk), .rst(rst), .en(en), .count(count));
  always #5 clk = ~clk;

  task automatic tick(input bit reset_value, input bit enable_value);
    @(negedge clk);
    rst = reset_value;
    en = enable_value;
    if (reset_value) total = 0;
    else if (enable_value) total = total + 1;
    @(posedge clk);
    #1;
    $display("PPA_TRACE cycle=%0d rst=%0d en=%0d count=%0d", checks, rst, en, count);
    if (int'(count) != total % 16)
      $fatal(1, "mismatch check=%0d expected=%0d actual=%0d", checks, total % 16, count);
    checks = checks + 1;
  endtask

  initial begin
    if (!$value$plusargs("seed=%d", seed)) $fatal(1, "seed required");
    random_state = 32'(seed);
    tick(1, 0);
    repeat (48) tick(0, 1); // wrap-around
    repeat (16) tick(0, 0); // hold
    tick(1, 1); // reset must win over enable
    repeat (256) begin
      random_state = random_state ^ (random_state << 13);
      random_state = random_state ^ (random_state >> 17);
      random_state = random_state ^ (random_state << 5);
      tick(random_state[4:0] == 0, random_state[8]);
    end
    $display("PPA_REGRESSION_PASS checks=%0d", checks);
    $finish;
  end
  initial begin
    #100000;
    $fatal(1, "watchdog timeout");
  end
endmodule
