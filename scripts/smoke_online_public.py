"""One public-document RAG request through the running local app, separate from batch evaluation."""
import json
from datetime import datetime
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]


if __name__ == '__main__':
    question = '在读证明可以线上办理吗？如果有特殊格式或用章要求怎么办？'
    with httpx.Client(timeout=90) as client:
        response = client.post('http://127.0.0.1:8765/api/ask', json={
            'question':question, 'category':'办事流程', 'include_samples':False, 'top_k':4})
        response.raise_for_status()
        result = response.json()
    assert result['mode']=='rag'
    assert result['sources'] and all(s['category']=='办事流程' and not s['sample'] for s in result['sources'])
    report = {'tested_at':datetime.now().astimezone().isoformat(),'question':question,'result':result,
              'note':'一条公开文件的完整检索生成检查，不是批量实验或答案准确率。'}
    (ROOT/'data/online-public-smoke.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'mode':result['mode'],'answer':result['answer'],'sources':[(s['name'],s['page']) for s in result['sources']],
                      'elapsed_ms':result['elapsed_ms']},ensure_ascii=False,indent=2))
