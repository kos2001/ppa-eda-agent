"""Bounded paired OpenRouter review; no physical runs or tool execution.

Use the installed Hermes venv (python-dotenv/PyYAML). Secrets stay in memory.
Requests/responses and the rubric are retained for independent inspection.
"""
import concurrent.futures
import hashlib
import json
import random
import sqlite3
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml
from dotenv import dotenv_values

OUT = Path(__file__).resolve().parent
ROOT = OUT.parent.parent
PROFILE = Path('/Users/kos2001/.hermes/profiles/ppa-agent')
MODELS = ['z-ai/glm-5.3-flash', 'deepseek/deepseek-v4.1-flash-1017']


def save(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def post(url, payload, key, timeout=180):
    req = urllib.request.Request(url, json.dumps(payload).encode(),
        {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key})
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode()
            status = response.status
        return {'http_status': status, 'seconds': time.perf_counter()-start, 'raw': raw}
    except urllib.error.HTTPError as exc:
        return {'http_status': exc.code, 'seconds': time.perf_counter()-start,
                'raw': exc.read().decode()}
    except Exception as exc:
        return {'http_status': None, 'seconds': time.perf_counter()-start,
                'error_type': type(exc).__name__}


def make_tasks():
    case_path = ROOT / 'reference-db/cases/counter4__2026-10-17.json'
    case = json.loads(case_path.read_text())
    rows = {r['tag']: r for it in case['iterations'] for r in it['results']}
    compact = []
    for tag in ['ctx17-seed-u35', case['winner_tag']]:
        r = rows[tag]
        v = r['verdict']
        compact.append({'tag': tag, 'cell_area_um2': v['area_um2'],
            'core_area_um2': v['core_area_um2'],
            'activity_power_w': v['power_activity']['annotated']['total']['total_w'],
            'worst_setup_slack_ns': v['worst_setup_slack'],
            'qualified': v['passed'],
            'compatibility_key': r['evaluation_provenance']['compatibility_key']})
    a, b = compact
    pct = lambda old, new: (new/old-1)*100
    numeric = {
        'cell_area_change_pct': pct(a['cell_area_um2'], b['cell_area_um2']),
        'power_change_pct': pct(a['activity_power_w'], b['activity_power_w']),
        'core_area_change_pct': pct(a['core_area_um2'], b['core_area_um2']),
        'area_power_change_pct': pct(a['cell_area_um2']*a['activity_power_w'],
                                     b['cell_area_um2']*b['activity_power_w'])}
    tasks = [
        {'id': 'measured_tradeoff', 'kind': 'json', 'origin': 'measured case excerpt',
         'prompt': '같은 provenance의 검증 완료 두 결과다. 첫 번째 대비 두 번째의 변화율을 계산하라. '
             'JSON에 cell_area_change_pct, power_change_pct, core_area_change_pct, '
             'area_power_change_pct(모두 부호 있는 %), uniform_improvement(boolean), '
             'summary_ko(짧은 한국어)를 반환하라.\n' + json.dumps(compact),
         'expected': {**numeric, 'uniform_improvement': False}},
        {'id': 'missing_signoff', 'kind': 'json', 'origin': 'explicit adversarial fixture',
         'prompt': '가상 검토 fixture: TritonRoute DRC=0, final Magic DRC 보고서 없음, '
             'LVS=0, 제공된 8개 timing corner 모두 WNS=0이나 요청한 9번째 corner 미보고, '
             '요청한 functional equivalence 결과 없음. final/gds 파일은 존재한다. '
             'JSON으로 rankable(boolean), magic_drc_count(number|null), '
             'all_corners_verified(boolean), equivalence_verified(boolean), summary_ko를 반환하라.',
         'expected': {'rankable': False, 'magic_drc_count': None,
                      'all_corners_verified': False, 'equivalence_verified': False}},
        {'id': 'first_failure', 'kind': 'json', 'origin': 'measured failed run error',
         'prompt': '다음 실제 실행 로그를 판정하라. JSON으로 first_failing_stage(OpenROAD 단계명), '
             'error_code, rankable(boolean), area_um2(number|null), power_w(number|null), '
             'summary_ko를 반환하라.\n' + rows['ctx17-seed-only-p1-r1']['error'],
         'expected': {'first_failing_stage': 'OpenROAD.GeneratePDN', 'error_code': 'PDN-0185',
                      'rankable': False, 'area_um2': None, 'power_w': None}},
        {'id': 'local_contract', 'kind': 'json', 'origin': 'repository contract question',
         'prompt': '이 저장소 OpenLane 2 run_spec에서 placement density 55%와 '
             'sky130_fd_sc_hs SCL 비교 후보를 정의하려 한다. JSON으로 '
             'density_override(한 항목 object), candidate_scl(string), '
             'use_STD_CELL_LIBRARY_override(boolean), validate_before_run(boolean), '
             'summary_ko를 반환하라.',
         'expected': {'density_override': {'PL_TARGET_DENSITY_PCT': 55},
                      'candidate_scl': 'sky130_fd_sc_hs',
                      'use_STD_CELL_LIBRARY_override': False, 'validate_before_run': True}},
        {'id': 'provenance_and_models', 'kind': 'json', 'origin': 'explicit adversarial fixture',
         'prompt': '가상 fixture: 기존 case는 같은 설계명과 PDK 이름을 갖지만 RTL hash, '
             'PDK revision, SCL, toolchain 정보가 없다. 새 SRAM library는 declared PVT label만 '
             '같고 input edge/table axes/output load 범위와 실측 PVT 출처가 미확인이다. '
             'prepare-only는 성공했다. JSON으로 comparable_for_ppa(boolean), '
             'macro_model_qualified(boolean), prepare_only_produced_measurements(boolean), '
             'legacy_provenance(string: unknown|complete), summary_ko를 반환하라.',
         'expected': {'comparable_for_ppa': False, 'macro_model_qualified': False,
                      'prepare_only_produced_measurements': False, 'legacy_provenance': 'unknown'}},
        {'id': 'report_tool_selection', 'kind': 'tool', 'origin': 'read-only tool selection fixture',
         'prompt': '후보 ctx17-full-history-p3-r3의 max_ss_100C_1v60 corner에 대한 '
             'final_timing 보고서를 읽어야 한다. 제공된 get_run_report 도구를 정확한 인자로 '
             '한 번 호출하라. 아직 결과가 없으므로 수치나 판정을 만들지 말라.',
         'expected': {'name': 'get_run_report', 'arguments': {
             'tag': 'ctx17-full-history-p3-r3', 'corner': 'max_ss_100C_1v60',
             'report': 'final_timing'}}},
    ]
    save('source.json', {'path': str(case_path.relative_to(ROOT)),
        'sha256': hashlib.sha256(case_path.read_bytes()).hexdigest(), 'excerpt': compact})
    return tasks


def grade(task, response):
    try:
        msg = response['choices'][0]['message']
        if task['kind'] == 'tool':
            calls = msg.get('tool_calls', [])
            actual = {'name': calls[0]['function']['name'],
                      'arguments': json.loads(calls[0]['function']['arguments'])}
            return {'parsed': True, 'checks': {'single_correct_call':
                len(calls) == 1 and actual == task['expected']}, 'actual': actual}
        # Strict JSON: code fences or trailing prose count as a format failure.
        actual = json.loads(msg['content'])
        checks = {}
        for k, expected in task['expected'].items():
            val = actual.get(k)
            if type(expected) is float:
                checks[k] = type(val) in (int, float) and abs(val-expected) <= .03
            else:
                checks[k] = k in actual and type(val) is type(expected) and val == expected
        summary = actual.get('summary_ko', '')
        checks['korean_summary'] = isinstance(summary, str) and any('\uac00' <= c <= '\ud7a3' for c in summary)
        return {'parsed': True, 'checks': checks, 'actual': actual}
    except (KeyError, IndexError, TypeError, ValueError):
        return {'parsed': False, 'checks': {'parse': False}}


def main():
    config = yaml.safe_load((PROFILE/'config.yaml').read_text())
    assert config['model']['default'] == MODELS[1]
    assert config['model']['provider'] == 'openrouter'
    key = dotenv_values(PROFILE/'.env')['OPENROUTER_API_KEY']
    assert key
    tasks = make_tasks()
    system = ('You are an EDA evidence reviewer. Follow the repository contract below. '
        'Treat measured excerpts as evidence and explicitly labelled fixtures as test scenarios. '
        'Answer only the requested JSON, or call the supplied read-only function. '
        'Never execute tools or claim new physical measurements.\n\n' + (ROOT/'AGENTS.md').read_text())
    schedule = [(m, t, repeat) for repeat in (1, 2) for t in tasks for m in MODELS]
    random.Random(20261018).shuffle(schedule)
    save('protocol.json', {'models': MODELS, 'repeats': 2, 'max_calls': 24,
        'max_completion_tokens': 2048, 'reasoning_effort': 'medium', 'temperature': 0,
        'concurrency': 2, 'timeout_seconds': 180, 'retries': 0,
        'schedule_seed': 20261018, 'tasks': tasks, 'system': system,
        'schedule': [{'model': m, 'task': t['id'], 'repeat': n} for m,t,n in schedule],
        'limitations': ['Small, partly synthetic contract test; no new physical PPA experiment.',
            'Shared system contract directly supplies some rules, so this measures instruction adherence.',
            'Direct API comparison excludes Hermes prompt/tool overhead; gateway tested separately.',
            'Two repetitions do not establish statistical superiority; routing/cache/load vary.',
            'Tool fixture tests one call selection, not full autonomous multi-step tool execution.']})
    with urllib.request.urlopen('https://openrouter.ai/api/v1/models', timeout=30) as r:
        catalog = json.load(r)
    save('catalog.json', [m for m in catalog['data'] if m['id'] in MODELS])

    def run(item):
        model, task, repeat = item
        payload = {'model': model, 'messages': [{'role': 'system', 'content': system},
            {'role': 'user', 'content': task['prompt']}], 'temperature': 0,
            'max_tokens': 2048, 'reasoning': {'effort': 'medium'}, 'usage': {'include': True}}
        if task['kind'] == 'json':
            payload['response_format'] = {'type': 'json_object'}
        else:
            payload['tools'] = [{'type': 'function', 'function': {'name': 'get_run_report',
                'description': 'Read a retained final report, without modifying a run.',
                'parameters': {'type': 'object', 'properties': {k: {'type': 'string'}
                    for k in ['tag','corner','report']}, 'required': ['tag','corner','report'],
                    'additionalProperties': False}}}]
            payload['tool_choice'] = 'auto'
        result = post('https://openrouter.ai/api/v1/chat/completions', payload, key)
        raw = result.pop('raw', None)
        try:
            response = json.loads(raw) if raw else {}
        except ValueError:
            response = {'unparsed_body': raw}
        result.update({'requested_model': model, 'task': task['id'], 'repeat': repeat,
                       'request': payload, 'response': response, 'grade': grade(task,response)})
        result['passed'] = all(result['grade']['checks'].values())
        filename = f"{MODELS.index(model)}-{task['id']}-{repeat}.json"
        save(filename, result)
        print(json.dumps({'file': filename, 'status': result['http_status'],
            'seconds': round(result['seconds'],2), 'passed': result['passed'],
            'returned_model': response.get('model'), 'usage': response.get('usage')},ensure_ascii=False),flush=True)
        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, schedule))
    summary = {}
    for model in MODELS:
        rs = [r for r in results if r['requested_model'] == model]
        usage = [r['response'].get('usage') or {} for r in rs]
        checks = [v for r in rs for v in r['grade']['checks'].values()]
        summary[model] = {'calls': len(rs), 'http_success': sum(r['http_status']==200 for r in rs),
            'passed_tasks': sum(r['passed'] for r in rs), 'passed_checks': sum(checks),
            'total_checks': len(checks), 'median_seconds': statistics.median(r['seconds'] for r in rs),
            'min_seconds': min(r['seconds'] for r in rs), 'max_seconds': max(r['seconds'] for r in rs),
            'prompt_tokens': sum(u.get('prompt_tokens',0) for u in usage),
            'completion_tokens': sum(u.get('completion_tokens',0) for u in usage),
            'reasoning_tokens': sum((u.get('completion_tokens_details') or {}).get('reasoning_tokens',0) for u in usage),
            'reported_cost_usd': sum(u.get('cost',0) or 0 for u in usage),
            'usage_reported_calls': sum(bool(u) for u in usage),
            'returned_models': sorted({r['response'].get('model','missing') for r in rs}),
            'per_task': {t['id']: {'passes': sum(r['passed'] for r in rs if r['task']==t['id']),
                'seconds': [r['seconds'] for r in rs if r['task']==t['id']]} for t in tasks}}
    save('summary.json', summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)


if __name__ == '__main__':
    main()
