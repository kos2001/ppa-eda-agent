"""Replay retained final STA inputs read-only and export macro output loads."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import uuid

from toolchain import OPENLANE_IMAGE, platform_args
from macro_output_load_audit import audit_log

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_inputs(path):
    with path.open() as stream:
        rows = list(csv.DictReader(stream))
    pins = [row['pin'] for row in rows]
    if not pins or len(set(pins)) != len(pins):
        raise ValueError('empty or duplicate input pin records')
    return dict(zip(pins, rows))


def compare_inputs(original, replay, tolerance=1e-6):
    try:
        original, replay = read_inputs(original), read_inputs(replay)
    except (KeyError, ValueError) as error:
        return {'matches': False, 'issues': [str(error)], 'pins_compared': 0}
    issues = []
    if set(original) != set(replay):
        issues.append('input pin sets differ')
    for pin in original.keys() & replay.keys():
        for edge in ('max_rise_ns', 'max_fall_ns', 'min_rise_ns', 'min_fall_ns'):
            try:
                a, b = float(original[pin][edge]), float(replay[pin][edge])
                if not math.isfinite(a) or not math.isfinite(b) or abs(a-b) > tolerance:
                    issues.append(f'{pin} {edge}: {a} vs {b}')
            except (KeyError, ValueError):
                issues.append(f'{pin} {edge}: unknown')
    return {'matches': not issues, 'tolerance_ns': tolerance, 'issues': issues,
            'pins_compared': len(original.keys() & replay.keys())}


def replay(design, stage, pdk, out, corners, timeout):
    out.mkdir(parents=True, exist_ok=False)
    script = Path(__file__).with_name('macro_output_loads.tcl')
    shutil.copyfile(script, out / script.name)
    for source in (Path(__file__), Path(__file__).with_name('macro_output_load_audit.py')):
        shutil.copyfile(source, out / source.name)
    scripts = '/nix/store/ss2cw3sxbrwwx9jl0rrppbw4kgcmgi2n-python3-3.11.9-env/lib/python3.11/site-packages/openlane/scripts'
    manifest = {'scope': 'report-only final STA replay; no P&R or model qualification',
                'image': OPENLANE_IMAGE, 'corner_timeout_s': timeout,
                'script_sha256': digest(script), 'corners': {}, 'model_qualified': False}
    image_info = subprocess.run(['docker', 'image', 'inspect', OPENLANE_IMAGE, '--format', '{{.Id}}'],
                                capture_output=True, text=True, timeout=30, check=True)
    manifest['image_id'] = image_info.stdout.strip()
    manifest['retained_state_sha256'] = digest(stage / 'state_in.json')
    manifest['resolved_config_sha256'] = digest(stage.parent / 'resolved.json')
    snapshot = subprocess.run(['docker', 'run', '--rm', *platform_args(),
        '-v', f'{out}:/out', manifest['image_id'], 'python3', '-c',
        'import shutil; from pathlib import Path; p=Path(' + repr(scripts) + '); '
        'shutil.copyfile(p/"base.sdc", "/out/base.sdc"); '
        'shutil.copyfile(p/"openroad/common/io.tcl", "/out/io.tcl")'],
        capture_output=True, text=True, timeout=30, check=True)
    manifest['source_sha256'] = {p.name: digest(p) for p in out.iterdir() if p.is_file()}
    def host(path):
        if path.startswith('/design/'):
            return design / path[len('/design/'):]
        if path.startswith('/pdk/'):
            return pdk / path[len('/pdk/'):]
        return None
    for corner in corners:
        target = out / corner
        target.mkdir()
        old = stage / corner
        envs = list(old.glob('_env_*.tcl'))
        if len(envs) != 1:
            raise ValueError(f'{corner}: expected one retained environment')
        shutil.copyfile(envs[0], target / 'original-env.tcl')
        log = (old / 'sta.log').read_text()
        libs = re.findall(r"Reading cell library for the '[^']+' corner at '([^']+)'", log)
        sdcs = re.findall(r"Reading design constraints file at '([^']+)'", log)
        spefs = re.findall(r"Reading top-level design parasitics for the '[^']+' corner at '([^']+)'", log)
        netlists = re.findall(r"Reading top-level netlist at '([^']+)'", log)
        models = re.findall(r"Reading Verilog model at '([^']+)'", log)
        if not libs or len(sdcs) != 1 or len(spefs) != 1 or len(netlists) != 1:
            raise ValueError(f'{corner}: ambiguous original inputs')
        if host(sdcs[0]) is None and sdcs[0] != scripts + '/base.sdc':
            raise ValueError(f'{corner}: unsnapshotted image SDC')
        environment = {'SCRIPTS_DIR': scripts, '_TCL_ENV_IN': '/out/original-env.tcl',
                       '_CURRENT_CORNER_NAME': corner, '_CURRENT_CORNER_LIBS': ' '.join(libs),
                       '_CURRENT_CORNER_NETLISTS': '', '_CURRENT_SPEF_BY_CORNER': spefs[0],
                       '_SDC_IN': sdcs[0]}
        owned = 'ppa-macro-loads-' + uuid.uuid4().hex
        command = ['docker', 'run', '--rm', '--name', owned, *platform_args(),
                   '-v', f'{design}:/design:ro', '-v', f'{pdk}:/pdk:ro',
                   '-v', f'{target}:/out', '-v', f'{out / script.name}:/audit.tcl:ro']
        for key, value in environment.items():
            command += ['-e', key + '=' + value]
        command += [manifest['image_id'], 'sta', '-no_splash', '-exit', '/audit.tcl']
        record = {'command': command, 'environment': environment,
                  'input_sha256': {path: digest(host(path)) for path in libs + spefs + netlists + models + sdcs if host(path)},
                  'original_log_sha256': digest(old / 'sta.log'),
                  'retained_env_sha256': digest(envs[0]), 'status': 'running'}
        manifest['corners'][corner] = record
        (out / 'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
        with (target / 'sta.log').open('wb') as stdout:
            try:
                result = subprocess.run(command, stdout=stdout, stderr=subprocess.STDOUT, timeout=timeout)
                record.update(returncode=result.returncode, status='returned')
            except subprocess.TimeoutExpired:
                record.update(status='timeout_unverified')
                try:
                    cleanup = subprocess.run(['docker','rm','-f',owned], capture_output=True, timeout=30)
                    record.update(cleanup_returncode=cleanup.returncode)
                except (subprocess.TimeoutExpired, OSError) as error:
                    record['cleanup_error'] = str(error)
        if (target / 'macro_inputs.csv').is_file():
            record['input_slew_comparison'] = compare_inputs(old / 'macro_inputs.csv', target / 'macro_inputs.csv')
        audit = audit_log((target / 'sta.log').read_text())
        (target / 'output-load-audit.json').write_text(json.dumps(audit, indent=2)+'\n')
        record['output_load_coverage_complete'] = audit['complete']
        record['output_sha256'] = {p.name: digest(p) for p in target.iterdir() if p.is_file()}
        (out / 'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
        print(corner, record['status'], record.get('returncode'), record.get('input_slew_comparison'), flush=True)
        if record.get('returncode') != 0 or not record.get('input_slew_comparison',{}).get('matches') or not audit['complete']:
            raise RuntimeError('replay failed or original input slews did not reproduce; stop before further corners')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--design', required=True, type=Path)
    parser.add_argument('--stage', required=True, type=Path)
    parser.add_argument('--pdk', type=Path, default=ROOT/'pdk')
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--corner', action='append', help='default: all resolved STA corners')
    parser.add_argument('--timeout', type=float, default=60)
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('timeout must be finite and positive')
    corners = args.corner or json.loads((args.stage.parent/'resolved.json').read_text())['STA_CORNERS']
    replay(args.design.resolve(), args.stage.resolve(), args.pdk.resolve(), args.out.resolve(), corners, args.timeout)


if __name__ == '__main__':
    main()
