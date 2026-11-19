"""Verify the dashboard diagnosis path and its persisted runtime model."""
import hashlib
import json
import sqlite3
import time
import urllib.request
from pathlib import Path

import yaml

OUT = Path(__file__).resolve().parent
PROFILE = Path('/Users/kos2001/.hermes/profiles/ppa-agent')
MARKER = 'MODEL_REVIEW_20261018_DEEPSEEK_GATEWAY'


def main():
    prompt = (f'{MARKER}\n이것은 주어진 수치만 검토하는 연결 확인이다. 도구를 사용하거나 '
        '파일을 수정하지 말라. 기준: cell area 290.278 um2, power 124 uW, core area '
        '480.461 um2. 후보: cell area 287.776 um2, power 121 uW, core area 653.126 um2. '
        '두 결과는 모든 요청 signoff/proof를 통과하고 provenance가 같다. '
        'cell-area×power 개선율과 core-area 변화율을 계산하고 tradeoff를 '
        '한국어 세 문장 이내로 설명하라. 새 물리 실행을 했다고 주장하지 말라.')
    payload = {'reportText': prompt, 'lang': 'ko'}
    (OUT/'gateway-request.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
    config_before = (PROFILE/'config.yaml').read_bytes()
    cfg = yaml.safe_load(config_before)
    start = time.time()
    req = urllib.request.Request('http://127.0.0.1:8123/diagnose',
        json.dumps(payload).encode(), {'Content-Type': 'application/json'})
    with urllib.request.urlopen(req,timeout=240) as response:
        raw = response.read().decode()
        status = response.status
    elapsed = time.time()-start
    (OUT/'gateway-response.sse').write_text(raw)
    parts = []
    for line in raw.splitlines():
        if line.startswith('data: ') and line[6:] != '[DONE]':
            j = json.loads(line[6:])
            for choice in j.get('choices',[]):
                parts.append(choice.get('delta',{}).get('content',''))
    text = ''.join(parts)
    (OUT/'gateway-response.txt').write_text(text+'\n')
    db = sqlite3.connect(f'file:{PROFILE / "state.db"}?mode=ro',uri=True)
    db.row_factory = sqlite3.Row
    sessions = db.execute('''SELECT DISTINCT s.id, s.model, s.started_at, s.ended_at,
        s.tool_call_count, s.input_tokens, s.output_tokens, s.reasoning_tokens,
        s.billing_provider, s.api_call_count, s.actual_cost_usd, s.estimated_cost_usd,
        s.cost_status FROM sessions s JOIN messages m ON m.session_id=s.id
        WHERE s.started_at >= ? AND m.role='user' AND instr(m.content,?) > 0''',
        (start-2, MARKER)).fetchall()
    artifact = {'url': 'http://127.0.0.1:8123/diagnose', 'http_status': status,
        'seconds': elapsed, 'started_at_unix': start, 'nonempty_content': bool(text.strip()),
        'contains_gateway_error': '[hermes-gateway error]' in text,
        'config': {'model': cfg['model'], 'agent_reasoning_effort':
                   cfg.get('agent',{}).get('reasoning_effort')},
        'config_sha256_before': hashlib.sha256(config_before).hexdigest(),
        'config_sha256_after': hashlib.sha256((PROFILE/'config.yaml').read_bytes()).hexdigest(),
        'runtime_sessions': [dict(r) for r in sessions]}
    (OUT/'gateway-check.json').write_text(json.dumps(artifact,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(artifact,ensure_ascii=False,indent=2))
    print(text)


if __name__ == '__main__':
    main()
