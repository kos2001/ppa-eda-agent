// Minimal real RTL design used as the pipeline's vertical-slice test case.
// A 4-bit synchronous up-counter with active-high synchronous reset and
// enable — small enough for a fast full-flow iteration, big enough to
// have real combinational logic (the incrementer) plus sequential state
// for placement/routing/STA to be meaningful.
module counter4 (
    input  wire       clk,
    input  wire       rst,
    input  wire       en,
    output reg  [3:0] count
);

  always @(posedge clk) begin
    if (rst)
      count <= 4'd0;
    else if (en)
      count <= count + 4'd1;
  end

endmodule
