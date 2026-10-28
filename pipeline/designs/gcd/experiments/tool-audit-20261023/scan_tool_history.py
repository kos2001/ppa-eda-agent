import collections
import json
import sys
from pathlib import Path

outer = Path(__file__).resolve().parents[1]
work = outer / '.cache/gcd-clock-merge'
sys.path.insert(0, str(work / 'pipeline'))
from tool_contract_audit import audit_run

rows = []
for root, label in [(outer, 'workspace'), (work, 'upgrade-worktree')]:
    for cfg in sorted((root / 'pipeline/designs').glob('*/runs/*/resolved.json')):
        run = cfg.parent
        result = audit_run(run)
        rows.append({'checkout': label, 'run': str(run.relative_to(root)).replace('\\', '/'), **result})
counts = collections.Counter()
for row in rows:
    counts['runs'] += 1
    counts['contract_checks_passed' if row['verified'] else 'errors' if row['errors'] else 'unverified_only'] += 1
    if any('derating:' in x for x in row['errors']):
        counts['derating_mismatch'] += 1
    if any('driver was not applied' in x for x in row['errors']):
        counts['missing_synthesis_driver'] += 1
output = {'scope': 'Available raw run directories only; counts include separate checkouts. No historical case verdicts are overwritten. Passing these checks is not signoff.',
          'summary': dict(counts), 'runs': rows}
for root in (outer, work):
    (root / 'reference-db/tool_contract_audit_20261023.json').write_text(json.dumps(output, indent=2) + '\n', encoding='utf-8')
print(json.dumps(counts, indent=2))
