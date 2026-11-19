"""Additional reports from the same final OpenSTA corner and parasitics."""
def tcl_word(value):
    # Double-quoted Tcl word with every substitution/quote escaped.
    return '"' + ''.join('\\' + c if c in '\\"$[]' else c for c in value) + '"'


def audit_script(original, instances):
    words = " ".join(tcl_word(x) for x in instances)
    return f"source {tcl_word(str(original))}\n" + r'''
puts "%OL_CREATE_REPORT macro_inputs.csv"
puts "pin,direction,max_rise_ns,max_fall_ns,min_rise_ns,min_fall_ns"
''' + f"foreach macro_instance [list {words}] {{\n" + r'''
    set inputs 0
    foreach pin [get_pins "${macro_instance}/*"] {
        set direction [string tolower [get_property $pin direction]]
        if {$direction != "input"} { continue }
        incr inputs
        set row [list [get_property $pin full_name] $direction]
        foreach property {slew_max_rise slew_max_fall slew_min_rise slew_min_fall} {
            if {[catch {get_property $pin $property} value]} {
                lappend row NA
            } else {
                lappend row $value
            }
        }
        puts [join $row ,]
    }
    if {$inputs == 0} { error "No STA input pins found for macro $macro_instance" }
}
puts "%OL_END_REPORT"
'''


def sta_step(base):
    from pathlib import Path

    class MacroInputSTA(base):
        # Keep the normal post-PnR step identity, outputs and metrics.
        def run(self, state_in, **kwargs):
            instances = sorted({name for macro in (self.config["MACROS"] or {}).values()
                                for name in macro.instances})
            self._input_audit_script = None
            if instances:
                path = Path(self.step_dir) / "sta_with_macro_inputs.tcl"
                path.write_text(audit_script(super().get_script_path(), instances))
                self._input_audit_script = str(path)
            return super().run(state_in, **kwargs)

        def get_script_path(self):
            return self._input_audit_script or super().get_script_path()

    return MacroInputSTA
