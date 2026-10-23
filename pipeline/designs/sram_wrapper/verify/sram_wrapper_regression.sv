`timescale 1ns/1ps
module sram_wrapper_regression;
  reg clk = 0;
  reg rst = 1;
  wire [31:0] dout0, dout1;
  integer seed;
  integer cycle;
  reg [31:0] random_state;
  sram_wrapper dut(.clk(clk), .rst(rst), .dout0(dout0), .dout1(dout1));
  // Keep vendor parameters unchanged, including its arbitrary functional delay.
  always #10 clk = ~clk;
  initial begin
    if (!$value$plusargs("seed=%d", seed)) $fatal(1, "seed required");
    random_state = 32'(seed);
    // Drive before the first rising edge; do not race the macro input latches.
    for (cycle = 0; cycle < 800; cycle = cycle + 1) begin
      random_state = random_state ^ (random_state << 13);
      random_state = random_state ^ (random_state >> 17);
      random_state = random_state ^ (random_state << 5);
      rst = cycle < 2 || cycle == 300 || cycle == 620 ||
            (cycle >= 650 && random_state[4:0] == 0);
      @(posedge clk);
      #2;
      $display("PPA_SRAM cycle=%0d rst=%0d dout0=%08h dout1=%08h", cycle, rst, dout0, dout1);
      @(negedge clk);
      #5;
    end
    // This confirms trace completion only. The host oracle decides correctness.
    $display("PPA_REGRESSION_PASS checks=800");
    $finish;
  end
  initial begin
    #20000;
    $fatal(1, "watchdog timeout");
  end
endmodule
