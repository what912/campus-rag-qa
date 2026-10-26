"""Import user-provided documents with the provenance explicitly confirmed in this chat."""
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


if __name__ == '__main__':
    from app import app
    incoming = ROOT / 'materials/incoming'
    assets = Path(r'C:\Users\吴少泽\Documents\综测\毕业论文\本科专业人才培养方案(2022版)_files')
    if assets.is_dir():
        shutil.copytree(assets, incoming/assets.name, dirs_exist_ok=True)
    items = [
        {'name':'本科专业人才培养方案(2022版).html', 'category':'培养方案', 'version':'2022版，2023级沿用（用户确认）',
         'source':'https://ccs.cuc.edu.cn/2022/0726/c5535a196269/page.htm', 'published_at':'2022-07-26',
         'authority':'学院官网；2023级适用性由用户确认',
         'review_note':'用户确认2023级沿用2022版；原文表格数字照录，不自动修正；图片课程地图不进入文字检索。'},
        {'name':'中国传媒大学计算机科学与技术专业课程大纲_详细版.pdf', 'category':'课程大纲',
         'version':'用户提供详细版，适用于2023级（用户确认）',
         'source':'用户提供，确认由任课教师/教务提供；未提供公开下载链接', 'published_at':'',
         'authority':'任课教师/教务来源（用户确认）',
         'review_note':'用户确认教师/教务正式来源；首页注明参考培养要求扩展整理。只回答文件明载内容，未载学分学时、先修和具体考核占比时说明无法确认。'}
    ]
    kb = app.state.kb
    imported = []
    for item in items:
        path = incoming/item['name']
        metadata = {k:item[k] for k in ('authority','published_at','review_note')}
        metadata.update(faculty='计算机与网络空间安全学院',major='计算机科学与技术',cohort='2023')
        try:
            document_id = kb.add(item['name'], path.read_bytes(), item['category'], item['version'], item['source'], metadata=metadata)
        except ValueError as exc:
            if '已经' not in str(exc):
                raise
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            document_id = next(d['id'] for d in kb.documents() if d['sha256']==digest)
        imported.append({**item,'id':document_id,'metadata':metadata,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                         'collected_at':'2026-10-13','local_path':str(path.relative_to(ROOT)),
                         'chunks':len(kb.document(document_id)['chunks'])})
    report = {'imported_at':datetime.now().astimezone().isoformat(),'documents':imported,'status':kb.stats(),
              'max_chunk_tokens':max(len(kb.model.tokenizer.encode(row['text'])) for row in kb.rows)}
    (ROOT/'materials/user-material-manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    previous = ROOT/'evaluation/public_questions.draft.json'
    dataset = json.loads(previous.read_text(encoding='utf-8'))
    removed = [c['id'] for c in dataset['cases'] if not c.get('document')]
    dataset['cases'] = [{**c,'reviewed':True,'reviewed_by':'用户人工核对','reviewed_at':'2026-10-13'} for c in dataset['cases'] if c.get('document')]
    dataset.update(name='学校公开规定开发题集 v2（用户核对）', status='reviewed_development', role='development',
                   reviewed_at='2026-10-13', removed_cases=removed,
                   note='用户确认其余19题正确，并要求删除4道无依据题。本集已用于开发，不能作为独立测试集；不再评价拒答率。')
    target = ROOT/'evaluation/public_questions.development.json'
    target.write_text(json.dumps(dataset,ensure_ascii=False,indent=2),encoding='utf-8')
    # Keep existing references operational, with the same reviewed development contents.
    previous.write_text(json.dumps(dataset,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':report['status'],'new_documents':[(d['name'],d['chunks']) for d in imported],
                      'max_chunk_tokens':report['max_chunk_tokens'],'development_questions':len(dataset['cases']),'removed':removed},ensure_ascii=False,indent=2))
