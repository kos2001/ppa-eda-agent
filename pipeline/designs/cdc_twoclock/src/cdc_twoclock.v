// Negative control for the CDC signoff gate. Deliberately unsafe.
//
// Two independent clock ports with a data path crossing between them and
// NO synchronizer: clk_a's counter is sampled directly by a clk_b flop.
// In silicon this is a metastability hazard — the classic CDC bug.
//
// It exists to prove the gate can fail. OpenLane's base.sdc constrains
// only the first CLOCK_PORT ("Multi-clock files are not currently
// supported by the base SDC file. Only the first clock will be
// constrained."), so everything in the clk_b domain — including this
// crossing — is analysed by nobody, and the run still reports zero
// timing violations. A pipeline that reads those zeros as a pass is
// signing off a domain it never looked at.
module cdc_twoclock (
    input  wire       clk_a,
    input  wire       clk_b,
    input  wire       rst,
    output wire [7:0] out_b
);

  reg [7:0] ctr_a;
  always @(posedge clk_a) begin
    if (rst) ctr_a <= 8'd0;
    else     ctr_a <= ctr_a + 8'd1;
  end

  // The crossing. One flop, no two-stage synchronizer, no gray coding.
  reg [7:0] capt_b;
  always @(posedge clk_b) begin
    if (rst) capt_b <= 8'd0;
    else     capt_b <= ctr_a;
  end

  assign out_b = capt_b;

endmodule
