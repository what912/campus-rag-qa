"""Import public originals, then enable the locally verified semantic model."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


if __name__ == '__main__':
    from app import app
    from fastapi.testclient import TestClient
    kb = app.state.kb
    records = json.loads((ROOT / 'materials/manifest.json').read_text(encoding='utf-8'))
    for item in records:
        if item['usage'] != 'knowledge':
            continue
        path = ROOT / item['local_path']
        try:
            kb.add(path.name, path.read_bytes(), item['category'], item['version'], item['source_page'],
                   metadata={'faculty':'全校通用', 'major':item['major'], 'cohort':item['cohort'],
                             'published_at':item['published_at'], 'review_note':item['review_note']})
        except ValueError as exc:
            if '已经' not in str(exc):
                raise
    with TestClient(app) as client:
        settings = client.get('/api/settings').json()
        settings.pop('has_key')
        settings.update(embedding_mode='semantic', model_path=str(ROOT / 'models/bge-small-zh-v1.5'),
                        query_prefix='为这个句子生成表示以用于检索相关文章：',
                        retrieval_strategy='hybrid_rerank', min_score=0.55, enable_ocr=True,
                        target_cohort='2023', target_major='计算机科学与技术')
        response = client.put('/api/settings', json=settings)
        response.raise_for_status()
        checks = []
        for question in ('在读证明可以通过什么方式办理？', '2023级计算机科学与技术的毕业总学分是多少？'):
            result = client.post('/api/ask', json={'question':question, 'include_samples':False,'answer_mode':'extractive'}).json()
            checks.append({'question':question, 'mode':result['mode'],
                           'sources':[(s['name'], s['page'], s['score']) for s in result['sources']]})
        report = {'status':client.get('/api/status').json(), 'checks':checks,
                  'longest_chunk_tokens':max(len(kb.model.tokenizer.encode(r['text'])) for r in kb.rows)}
        (ROOT / 'data/preparation-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2))
