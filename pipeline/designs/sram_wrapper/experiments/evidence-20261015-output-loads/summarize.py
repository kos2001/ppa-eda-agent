"""Rebuild this packet's scoped summary and hash handoff from retained output."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parent
manifest = json.loads((root / 'manifest.json').read_text())
audits = {corner: json.loads((root / corner / 'output-load-audit.json').read_text())
          for corner in manifest['corners']}
rows = [row for audit in audits.values() for row in audit['observations']]
complete = len(audits) == 9 and all(a['complete'] for a in audits.values())
reproduced = all(c['returncode'] == 0 and c['input_slew_comparison']['matches']
                 for c in manifest['corners'].values())
summary = {
    'scope': 'retained SRAM output-load observation coverage only; not design signoff',
    'output_load_coverage_complete': complete and reproduced,
    'corners': len(audits), 'observations': len(rows), 'expected_observations': 2304,
    'table_load_pf': {'min': min(r['table_load_pf'] for r in rows),
                      'max': max(r['table_load_pf'] for r in rows)},
    'all_within_interpolation_brackets': all(a['all_within_interpolation_brackets'] for a in audits.values()),
    'input_slews_reproduced': reproduced,
    'model_qualified': False,
    'remaining': ['TT macro model used at six SS/FF corners',
                  'measured PVT and per-arc tables unqualified',
                  'prior port0 attempt timeout_unverified; port1 prepared only'],
}
(root / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
artifacts = {p.relative_to(root).as_posix(): {
    'path': p.relative_to(root).as_posix(),
    'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
    for p in sorted(root.rglob('*')) if p.is_file()
    and p.name not in {'handoff.json', 'handoff-review.json'}}
handoff = {
    'schema_version': 1, 'run_id': root.name,
    'design': 'srio_r15_clock_fanout16_revalidate_20261015',
    'toolchain': manifest['image_id'],
    'target_identity': manifest['retained_state_sha256'],
    'scope': summary['scope'],
    'required_gates': ['output_load_coverage', 'macro_model'],
    'gates': {
        'output_load_coverage': {'state': 'pass' if complete and reproduced else 'unverified',
                                 'evidence': ['summary.json', 'manifest.json']},
        'macro_model': {'state': 'unverified', 'evidence': ['summary.json']},
    },
    'artifacts': artifacts,
}
(root / 'handoff.json').write_text(json.dumps(handoff, indent=2) + '\n')
print(json.dumps(summary, indent=2))
