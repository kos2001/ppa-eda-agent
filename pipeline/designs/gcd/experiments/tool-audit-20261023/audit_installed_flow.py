import hashlib
import importlib
import json
import re
import sys
from pathlib import Path

module = importlib.import_module(sys.argv[1])
root = Path(module.__file__).parent
rows = []
for path in sorted((root / 'scripts').rglob('*')):
    if path.suffix not in ('.tcl', '.sdc', '.py') or not path.is_file():
        continue
    matches = [{'line': i, 'text': line.strip()} for i, line in enumerate(path.read_text().splitlines(), 1)
               if re.search(r'/\s*(?:100|1000)(?![\d.])\b', line) or 'SYNTH_DRIVING_CELL' in line]
    if matches:
        rows.append({'path': str(path.relative_to(root)), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'matches': matches})
out = Path('/work/tool-audit')
out.mkdir(exist_ok=True)
(out / (sys.argv[1] + '-source-scan.json')).write_text(json.dumps(rows, indent=2) + '\n')
print(json.dumps(rows, indent=2))
