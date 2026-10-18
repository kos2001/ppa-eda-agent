"""Two supplemental calculation calls at a larger output budget; not pooled."""
import concurrent.futures
import json
from pathlib import Path

from dotenv import dotenv_values

from run_review import MODELS, OUT, PROFILE, grade, post, save


def main():
    protocol = json.loads((OUT/'protocol.json').read_text())
    task = protocol['tasks'][0]
    key = dotenv_values(PROFILE/'.env')['OPENROUTER_API_KEY']
    save('supplemental-protocol.json', {'reason':
        'One DeepSeek calculation exhausted the 2048-token completion budget. '
        'Repeat the same measured calculation once per model at 4096 tokens; '
        'keep all original failures and do not pool these observations.',
        'max_calls': 2, 'max_tokens': 4096, 'reasoning_effort': 'medium',
        'temperature': 0, 'concurrency': 2, 'retries': 0})

    def run(model):
        payload = {'model':model,'messages':[{'role':'system','content':protocol['system']},
            {'role':'user','content':task['prompt']}], 'temperature':0,'max_tokens':4096,
            'reasoning':{'effort':'medium'},'response_format':{'type':'json_object'},
            'usage':{'include':True}}
        result = post('https://openrouter.ai/api/v1/chat/completions',payload,key)
        response = json.loads(result.pop('raw','{}'))
        result.update({'request':payload,'response':response,'grade':grade(task,response)})
        save(f'supplemental-{MODELS.index(model)}.json',result)
        print(json.dumps({'model':model,'seconds':result['seconds'],
            'grade':result['grade'],'usage':response.get('usage'),
            'finish_reason':response.get('choices',[{}])[0].get('finish_reason')},ensure_ascii=False),flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(run,MODELS))


if __name__=='__main__':
    main()
