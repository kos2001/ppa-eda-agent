# Report-only load export. The driver supplies the retained per-corner env.
source $::env(SCRIPTS_DIR)/openroad/common/io.tcl
set_cmd_units -time ns -capacitance pF -current mA -voltage V -resistance kOhm -distance um
set sta_report_default_digits 9
read_timing_info
read_spefs
set corner [lindex [sta::corners] 0]
sta::set_cmd_corner $corner

report_units
report_parasitic_annotation -report_unannotated

# This secondary export must reproduce the retained input-edge report.
set inputs [open /out/macro_inputs.csv w]
puts $inputs "pin,direction,max_rise_ns,max_fall_ns,min_rise_ns,min_fall_ns"
foreach pin [get_pins u_sram/*] {
    if {[get_property $pin direction] ne "input"} {continue}
    set row [list [get_property $pin full_name] input]
    foreach property {slew_max_rise slew_max_fall slew_min_rise slew_min_fall} {
        lappend row [get_property $pin $property]
    }
    puts $inputs [join $row ,]
}
close $inputs

set loads [open /out/net_loads.csv w]
puts $loads "pin,net,analysis,pin_cap_pf,wire_cap_pf,total_cap_pf"
set count 0
foreach pin [get_pins u_sram/*] {
    if {[get_property $pin direction] ne "output"} {continue}
    incr count
    set name [get_property $pin full_name]
    set nets [get_nets -of_objects $pin]
    if {[llength $nets] != 1} {error "Expected one output net: $name"}
    set net [lindex $nets 0]
    foreach mode {min max} {
        set row [list $name [get_property $net full_name] $mode]
        foreach method {pin_capacitance wire_capacitance capacitance} {
            lappend row [sta::capacitance_sta_ui [$net $method $corner $mode]]
        }
        puts $loads [join $row ,]
        puts "LOAD_DCALC_BEGIN $name $mode"
        report_dcalc -to $pin -corner [$corner name] -$mode -digits 9
        puts "LOAD_DCALC_END $name $mode"
    }
}
close $loads
if {$count != 64} {error "Expected 64 output pins, found $count"}
puts "LOAD_EXPORT_COMPLETE $count"
