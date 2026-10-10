source $::env(_TCL_ENV_IN)
set ::env(SCRIPTS_DIR) /nix/store/0cgica8q52vjb9a7vbwaccncbajs33gx-python3-3.11.9-env/lib/python3.11/site-packages/openlane/scripts
set ::env(_PNR_LIBS) /pdk/sky130A/libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib
set _f [open $::env(PNR_EXCLUDED_CELL_FILE) r]; set ::env(_PNR_EXCLUDED_CELLS) [string map {"\n" " "} [string trim [read $_f]]]; close $_f
set ::env(STEP_DIR) /tmp
set_debug_level GRT repair_antennas 2
source $::env(SCRIPTS_DIR)/openroad/antenna_repair.tcl
