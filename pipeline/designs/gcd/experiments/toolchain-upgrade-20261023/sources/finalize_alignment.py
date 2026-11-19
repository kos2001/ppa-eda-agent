import hashlib
import json
import shutil
from pathlib import Path

outer = Path(__file__).resolve().parents[1]
root = outer / '.cache/gcd-clock-merge'
rel = Path('pipeline/designs/gcd/experiments/toolchain-upgrade-20261023')
archive = root / rel
comparison_path = root / 'reference-db/gcd_toolchain_comparison_20261023.json'
comparison = json.loads(comparison_path.read_text())
labels = ['stable-derate5', 'librelane-cts25', 'librelane-cts25-postgrt']
tags = {f'gf180-upgrade-{label}-13p2-20261023': label for label in labels}
found = {}
for case in (root / 'reference-db/cases').glob('gcd__2026-10-23*.json'):
    data = json.loads(case.read_text())
    for iteration in data.get('iterations', []):
        for result in iteration['results']:
            if result['tag'] in tags:
                found[result['tag']] = (case, result)
assert set(found) == set(tags), set(tags) - set(found)
comparison['rows'] = [r for r in comparison['rows'] if r['tag'] not in tags]
for tag, label in tags.items():
    case, result = found[tag]
    run = root / 'pipeline/designs/gcd/runs' / tag
    target = archive / label
    verdict = result.get('verdict') or {}
    metrics = json.loads((run / 'final/metrics.json').read_text())
    activity = verdict.get('power_activity') or {}
    row = {'profile': label, 'case': str(case.relative_to(root)).replace('\\', '/'), 'tag': tag,
           'tools': json.loads((target / 'tools.json').read_text()),
           'passed': verdict.get('passed'), 'error': result.get('error'),
           'violations': verdict.get('violations'), 'unverified': verdict.get('unverified'),
           'cell_area_um2': metrics.get('design__instance__area__stdcell'),
           'core_area_um2': metrics.get('design__core__area'),
           'raw_total_instance_area_um2': metrics.get('design__instance__area'),
           'worst_setup_slack_ns': metrics.get('timing__setup__ws'),
           'worst_hold_slack_ns': metrics.get('timing__hold__ws'),
           'activity_power_w': activity.get('annotated', {}).get('total', {}).get('total_w'),
           'power_corner': activity.get('corner'), 'signoff_checks': verdict.get('signoff_checks')}
    comparison['rows'].append(row)

for row in comparison['rows']:
    run = root / 'pipeline/designs/gcd/runs' / row['tag']
    target = archive / row['profile']
    selected = [run / s for s in ['resolved.json', 'final/metrics.json', 'final/sdc/gcd.sdc', '_power_activity/tool.log', '_power_activity/simulation.log', '_power_activity/run.sh']]
    stages = []
    for state in sorted(run.glob('*/state_out.json')):
        if any(s in state.parent.name for s in ['yosys-synthesis', 'staprepnr', 'stapostpnr']):
            selected.append(state)
            selected.extend(state.parent.glob('**/_env*.tcl'))
            m = json.loads(state.read_text()).get('metrics', {})
            stages.append({'step': state.parent.name, 'metrics': {k:v for k,v in m.items() if k in ['design__instance__area', 'design__instance__area__stdcell', 'timing__setup__ws', 'timing__hold__ws', 'design__max_slew_violation__count']}})
    for src in selected:
        if src.is_file():
            dst = target / src.relative_to(run)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    (target / 'stage-comparison.json').write_text(json.dumps(stages, indent=2) + '\n')
    sdc = (run / 'final/sdc/gcd.sdc').read_text()
    row['effective_derate_percent'] = 5 if 'set_timing_derate -late 1.0500' in sdc else 0
    row['qualification'] = 'Historical PASS used 0% effective derating; not qualified at 5%.' if row['effective_derate_percent'] == 0 else 'Measured with 5% effective derating; consult hard gates.'
    manifest = {str(p.relative_to(target)).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest() for p in target.rglob('*') if p.is_file() and p.name != 'sha256.json'}
    (target / 'sha256.json').write_text(json.dumps(manifest, indent=2) + '\n')

comparison['comparison_correction'] = 'Old OpenLane base.sdc divides TIME_DERATING_CONSTRAINT by integer 100. Integer 5 applied 0%; float 5.0 applies 5%. LibreLane divides by 100.0. Original PASS is not evidence for the intended 5% derated target.'
comparison['default_promotion'] = 'Neither corrected stable nor tested LibreLane configuration closes 13.2 ns at 5% derating. Do not promote an unqualified configuration.'
comparison['constraints']['time_derating_percent'] = 5
comparison['constraints']['historical_exception'] = 'stable original row effectively used 0%'
comparison_path.write_text(json.dumps(comparison, indent=2) + '\n')
shutil.copytree(outer / '.cache/derate-audit', archive / 'derate-audit', dirs_exist_ok=True)
for name in ['run_gcd_alignment.py', 'run_gcd_postgrt.py', 'inspect_derate.py', 'derate_expr.tcl', 'finalize_alignment.py']:
    shutil.copy2(outer / '.cache' / name, archive / 'sources' / name)
shutil.copytree(archive, outer / rel, dirs_exist_ok=True)
index = json.loads((outer / 'reference-db/index.json').read_text())
for row in comparison['rows']:
    source = root / row['case']
    name = f"gcd__2026-10-23__upgrade-{row['profile']}.json"
    shutil.copy2(source, outer / 'reference-db/cases' / name)
    row['case'] = 'reference-db/cases/' + name
    if name not in index['gcd']:
        index['gcd'].append(name)
for label in labels:
    name = f'run_spec_upgrade_{label}_20261023.json'
    shutil.copy2(root / 'pipeline/designs/gcd' / name, outer / 'pipeline/designs/gcd' / name)
(outer / 'reference-db/index.json').write_text(json.dumps(index, indent=2) + '\n')
(outer / 'reference-db/gcd_toolchain_comparison_20261023.json').write_text(json.dumps(comparison, indent=2) + '\n')
print(json.dumps([{k:r.get(k) for k in ['profile', 'passed', 'worst_setup_slack_ns', 'worst_hold_slack_ns', 'cell_area_um2', 'activity_power_w', 'violations']} for r in comparison['rows']], indent=2))
