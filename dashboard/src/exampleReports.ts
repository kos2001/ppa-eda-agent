// Verbatim from ../../references/report-area.md
export const EXAMPLE_AREA_REPORT = `Report : area
Design : top_module
Version: T-2022.03-SP5
Date   : ...

Library(s) Used:
    saed32rvt_ss1p05v0c (File: ...)

Number of ports:                          312
Number of nets:                          8842
Number of cells:                         7615
Number of combinational cells:           5203
Number of sequential cells:              2107
Number of macros/black boxes:               2
Number of buf/inv:                       1108

Total combinational area:      12045.238400
Total noncombinational area:   18932.556800
Total buf/inv area:             2210.995200 (included above)
Total macro/black box area:    45210.880000
Net Interconnect area:          undefined (Wire load model not compiled)

Total cell area:               76188.675200
Total area:                    undefined
`;

// Verbatim from ../../references/report-timing.md
export const EXAMPLE_TIMING_REPORT = `****************************************
Report : timing
        -path_group reg2reg
        -delay_type max
Design : top_module
Version: T-2022.03-SP5
Date   : ...
****************************************

Startpoint: u_fetch/pc_reg[12] (rising edge-triggered flip-flop clocked by CLK)
Endpoint: u_decode/instr_reg[3] (rising edge-triggered flip-flop clocked by CLK)
Path Group: reg2reg
Path Type: max

Point                                  Incr       Path
--------------------------------------------------------
clock CLK (rise edge)                  0.00       0.00
clock network delay (propagated)       0.45       0.45
u_fetch/pc_reg[12]/CK (DFFR_X1)        0.00       0.45 r
u_fetch/pc_reg[12]/Q (DFFR_X1)         0.12       0.57 f
u1/Z (BUFX2)                           0.08       0.65 f
u2/Z (AND2X1)                          0.15       0.80 r
u_decode/instr_reg[3]/D (DFFR_X1)      0.02       0.82 r
data arrival time                                 0.82

clock CLK (rise edge)                  1.20       1.20
clock network delay (propagated)       0.48       1.68
clock uncertainty                     -0.05       1.63
u_decode/instr_reg[3]/CK (DFFR_X1)     0.00       1.63 r
library setup time                    -0.04       1.59
data required time                                1.59
--------------------------------------------------------
data required time                                1.59
data arrival time                                 -0.82
--------------------------------------------------------
slack (MET)                                        0.77
`;

// Verbatim from ../../references/report-power.md
export const EXAMPLE_POWER_REPORT = `****************************************
Report : power
        -analysis_effort low
Design : top_module
Version: T-2022.03-SP5
Date   : ...
****************************************

Global Operating Voltage = 0.9
Power-specific unit information :
    Voltage Units = 1V
    Capacitance Units = 1.000000pf
    Time Units = 1ns
    Dynamic Power Units = 1mW
    Leakage Power Units = 1nW

  Cell Internal Power  =   4.2103 mW   (42.1%)
  Net Switching Power  =   3.8871 mW   (38.9%)
                         -----------
  Total Dynamic Power  =   8.0974 mW  (98.7%)

  Cell Leakage Power   = 104.3200 uW   (1.3%)
                         -----------
  Total Power          =   8.2017 mW  (100%)
`;
