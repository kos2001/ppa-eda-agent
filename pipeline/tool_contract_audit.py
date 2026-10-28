"""Check recorded tool inputs against emitted constraints; never execute SDC.

This is a bounded audit of standard OpenLane/LibreLane write_sdc output, not
a Tcl interpreter or certification of a tool's algorithms. Custom SDC intent
requires separate review. Historical artifacts are read without modification.
"""
import argparse
import json
import math
import re
from pathlib import Path


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def cell_area(metrics):
    # The new total includes physical-only fillers. Do not replace an invalid
    # explicit stdcell metric with a plausible-looking total.
    return metrics.get('design__instance__area__stdcell', metrics.get('design__instance__area'))


def timing_minimum(metrics, kind):
    values = [v for k, v in metrics.items()
              if (k in (f'timing__{kind}__wns', f'timing__{kind}__ws')
                  or k.startswith((f'timing__{kind}__wns__corner:', f'timing__{kind}__ws__corner:')))
              and finite(v)]
    return min(values) if values else None


def metric_issues(metrics, signoff_keys):
    issues = []
    for key in signoff_keys:
        value = metrics.get(key)
        if value is not None and (not finite(value) or value < 0 or value != int(value)):
            issues.append(f'{key}: invalid violation count')
    for kind in ['setup', 'hold']:
        for key, value in metrics.items():
            if key.startswith((f'timing__{kind}__wns', f'timing__{kind}__ws')) and not finite(value):
                issues.append(f'{key}: non-finite or invalid timing slack')
    return issues


def audit_run(run_dir):
    run_dir = Path(run_dir)
    errors, unknown, checks = [], [], []
    result = {'errors': errors, 'unverified': unknown, 'checks': checks,
              'scope': 'Scalar default-SDC constraints, requested timing corners, synthesis driver diagnostics; custom SDC intent is not interpreted.'}
    try:
        cfg = json.loads((run_dir / 'resolved.json').read_text(encoding='utf-8'))
        metrics = json.loads((run_dir / 'final/metrics.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        unknown.append(f'final metrics/resolved config unavailable: {exc}')
        result['verified'] = False
        return result
    sdcs = sorted((run_dir / 'final/sdc').glob('*.sdc'))
    if len(sdcs) != 1:
        unknown.append('exactly one final SDC is required for this audit')
        text = ''
    else:
        text = sdcs[0].read_text(encoding='utf-8')
    commands = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#')]
    def match_values(name, pattern, expected, default=None):
        values = [float(v) for line in commands for v in re.findall(pattern, line)]
        if not values and default is not None:
            values = [default]
        checks.append({'name': name, 'expected': expected, 'observed': values})
        if not finite(expected) or not values:
            unknown.append(f'{name}: expected or emitted value unavailable')
        elif any(not math.isclose(v, expected, rel_tol=1e-5, abs_tol=0.000051) for v in values):
            errors.append(f'{name}: expected {expected}, emitted {values}')
    number = r'([-+]?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?)'
    custom = cfg.get('SIGNOFF_SDC_FILE') or cfg.get('PNR_SDC_FILE')
    if custom:
        unknown.append('custom SDC intent requires explicit constraint review')
    elif text:
        match_values('clock period', r'^create_clock\b.*?-period\s+' + number, cfg.get('CLOCK_PERIOD'))
        derate = cfg.get('TIME_DERATING_CONSTRAINT')
        if finite(derate):
            for mode, value in [('early', 1 - derate / 100), ('late', 1 + derate / 100)]:
                # Only unconditional scalar commands have this exact shape.
                match_values(f'{mode} derating', r'^set_timing_derate -' + mode + r'\s+' + number + r'\s*$', value, 1.0)
        else:
            unknown.append('resolved timing derating unavailable')
        for key, command in [('CLOCK_UNCERTAINTY_CONSTRAINT', 'set_clock_uncertainty'),
                             ('CLOCK_TRANSITION_CONSTRAINT', 'set_clock_transition'),
                             ('MAX_TRANSITION_CONSTRAINT', 'set_max_transition'),
                             ('MAX_FANOUT_CONSTRAINT', 'set_max_fanout')]:
            if cfg.get(key) is not None:
                match_values(key, '^' + command + r'\s+' + number, cfg[key], 0.0 if 'CLOCK_' in key else None)
        if finite(cfg.get('CLOCK_PERIOD')) and finite(cfg.get('IO_DELAY_CONSTRAINT')):
            delay = cfg['CLOCK_PERIOD'] * cfg['IO_DELAY_CONSTRAINT'] / 100
            for command in ['set_input_delay', 'set_output_delay']:
                # Designs without data inputs/outputs legitimately omit these.
                if any(line.startswith(command + ' ') for line in commands):
                    match_values(command, '^' + command + r'\s+' + number, delay)
    corners = cfg.get('STA_CORNERS')
    if not isinstance(corners, list) or not corners:
        unknown.append('requested STA corners unavailable')
    else:
        for corner in corners:
            for kind in ['setup', 'hold']:
                key = f'timing__{kind}__ws__corner:{corner}'
                if not finite(metrics.get(key)):
                    unknown.append(f'{key}: missing or invalid')
    synth_logs = sorted(run_dir.glob('*yosys-synthesis/*.log'))
    if not synth_logs:
        unknown.append('synthesis log unavailable for driver audit')
    for log in synth_logs:
        for line in log.read_text(encoding='utf-8', errors='replace').splitlines():
            if 'Cannot find the default PI driving cell' in line:
                errors.append('synthesis input driver was not applied: ' + line.strip())
    result['verified'] = not errors and not unknown
    return result


def attach(verdict, audit):
    verdict['tool_contract_audit'] = audit
    verdict.setdefault('violations', []).extend('tool contract: ' + s for s in audit['errors'])
    verdict.setdefault('unverified', []).extend('tool contract: ' + s for s in audit['unverified'])
    verdict['passed'] = not verdict['violations'] and not verdict['unverified']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_dirs', nargs='+', type=Path)
    args = parser.parse_args()
    print(json.dumps([{'run_dir': str(p), **audit_run(p)} for p in args.run_dirs], indent=2))
