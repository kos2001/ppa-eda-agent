import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys

module = importlib.import_module(sys.argv[1])
base = Path(module.__file__).parent
out = Path('/work/derate-audit') / sys.argv[1]
out.mkdir(parents=True, exist_ok=True)
rows = []
for path in sorted(p for p in (base / 'scripts').rglob('*') if p.is_file() and p.suffix in ('.tcl', '.sdc')):
    lines = path.read_text().splitlines()
    matches = [i for i, line in enumerate(lines) if 'TIME_DERATING_CONSTRAINT' in line or 'set_timing_derate' in line or 'SYNTH_TIMING_DERATE' in line]
    if matches:
        rel = path.relative_to(base)
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
        rows.append({'path': str(rel), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                     'excerpts': ['\n'.join(f'{j+1}: {lines[j]}' for j in range(max(0,i-2),min(len(lines),i+3))) for i in matches]})
print(json.dumps(rows, indent=2))
