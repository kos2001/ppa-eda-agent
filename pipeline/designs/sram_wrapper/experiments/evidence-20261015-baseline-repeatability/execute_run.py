"""One authorized 7200-second baseline replay, with automatic evidence retention."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone

packet = Path(__file__).resolve().parent
repo = next(parent for parent in packet.parents if (parent / 'pipeline/sram_point_replay.py').is_file())
runtime = Path('/home/kos2001/ppa-sram-bounded-20261015')
plan = json.loads((packet / 'plan.json').read_text())
out = Path(plan['output_dir'])
if out.exists():
    raise RuntimeError('Attempt directory already exists; refusing automatic replay')
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
preflight = json.loads((packet / 'preflight.json').read_text())
expected = json.loads((packet.parent / 'evidence-20261015-bounded-point/environment/model-hashes.json').read_text())
for name, digest in expected.items():
    if sha(runtime / 'pdk' / name) != digest:
        raise RuntimeError('Model snapshot mismatch: ' + name)
if sha(runtime / 'install-indexed/share/ngspice/scripts/spinit') != preflight['startup_sha256']:
    raise RuntimeError('Startup file changed')
sources = packet / 'execution_sources'
sources.mkdir(exist_ok=True)
for name in ('sram_point_replay.py', 'sram_point_stimulus.py', 'characterize_sram.py',
             'openram_adapter.py', 'magic_abstract_drc.py'):
    shutil.copyfile(repo / 'pipeline' / name, sources / name)
command = [sys.executable, str(sources / 'sram_point_replay.py'),
    '--source-dir', str(runtime / 'inputs'),
    '--point-manifest', str(runtime / 'inputs/preparation.json'),
    '--simulator', str(runtime / 'install-indexed/bin/ngspice'),
    '--simulator-sha256', preflight['simulator_sha256'],
    '--model-root', str(runtime / 'pdk'),
    '--netlist', str(runtime / 'inputs/sram.sp'),
    '--netlist-sha256', preflight['netlist_sha256'],
    '--startup-dir', str(runtime / 'install-indexed/share/ngspice/scripts'),
    '--output-dir', str(out), '--timeout', '7200']
if (packet / 'runner.log').exists():
    shutil.copyfile(packet / 'runner.log', packet / 'pre-simulation-import-error.log')
    shutil.copyfile(packet / 'plan.json', packet / 'pre-simulation-import-error-plan.json')
for key in ('finished_at', 'runner_returncode'):
    plan.pop(key, None)
plan.update(status='running', authorized_wall_timeout_seconds=7200,
            simulation_started=True, started_at=datetime.now(timezone.utc).isoformat(), command=command)
(packet / 'plan.json').write_text(json.dumps(plan, indent=2) + '\n')
with (packet / 'runner.log').open('w') as log:
    result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
plan.update(status='runner_returned', runner_returncode=result.returncode,
            finished_at=datetime.now(timezone.utc).isoformat())
if out.exists():
    retained = packet / 'result'
    shutil.copytree(out, retained)
    for source in out.rglob('*'):
        if source.is_file() and sha(source) != sha(retained / source.relative_to(out)):
            raise RuntimeError('Retained output hash mismatch')
    manifest_path = retained / 'manifest.json'
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text())
        historical = json.loads((packet.parent / 'evidence-20261004/characterization-algorithm/completed-ss-port0-20261004/measurement-summary.json').read_text())['measurement_values_SI']
        measured = manifest.get('measurements', {}).get('values_SI', {})
        comparisons = {name: {'historical_SI': historical[name], 'replay_SI': value,
            'difference_SI': value - historical[name],
            'relative_difference': (value - historical[name]) / abs(historical[name]) if historical[name] else None}
            for name, value in measured.items() if name in historical}
        summary = {key: manifest.get(key) for key in
            ('status', 'wall_seconds', 'returncode', 'timed_out', 'measurements', 'point_checks')}
        summary.update(model_qualified=False, comparisons=comparisons,
            comparison_scope='Observed differences only; no numerical equivalence tolerance asserted',
            retained_bytes_verified=True)
        (packet / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
        plan['status'] = manifest['status']
(packet / 'plan.json').write_text(json.dumps(plan, indent=2) + '\n')
(packet / 'artifact-hashes.json').write_text(json.dumps({p.relative_to(packet).as_posix(): sha(p)
    for p in sorted(packet.rglob('*')) if p.is_file() and p.name != 'artifact-hashes.json'}, indent=2) + '\n')
print(json.dumps(plan, indent=2), flush=True)
raise SystemExit(result.returncode)
