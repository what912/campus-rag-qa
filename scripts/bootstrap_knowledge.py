"""Restore the bundled campus corpus without generating answers or changing model credentials."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from campus.knowledge import KnowledgeBase


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,default=ROOT/'data')
    parser.add_argument('--enable-ocr',action='store_true')
    args=parser.parse_args()
    kb=KnowledgeBase(args.data_dir);kb.enable_ocr=args.enable_ocr
    kb.seed(ROOT/'samples')
    public=json.loads((ROOT/'materials/manifest.json').read_text(encoding='utf-8'))
    professional=json.loads((ROOT/'materials/user-material-manifest.json').read_text(encoding='utf-8'))['documents']
    items=[p for p in public if p['usage']=='knowledge']+professional
    admissions=ROOT/'materials/admissions/manifest.json'
    if admissions.is_file():items.extend(json.loads(admissions.read_text(encoding='utf-8'))['documents'])
    rows=[]
    for item in items:
        path=(ROOT/item['local_path'].replace('\\','/')).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file():
            raise SystemExit('资料路径无效或原件不存在：'+item['local_path'])
        content=path.read_bytes()
        if hashlib.sha256(content).hexdigest()!=item['sha256']:
            raise SystemExit('原件哈希不一致：'+path.name)
        metadata=item.get('metadata') or {k:item.get(k,'') for k in ('major','cohort','published_at','review_note')}
        try:
            identifier=kb.add(path.name,content,item['category'],item['version'],item.get('source_page') or item.get('source',''),metadata=metadata)
            rows.append({'name':path.name,'status':'imported','chunks':len(kb.document(identifier)['chunks'])})
        except ValueError as exc:
            if '已经' not in str(exc): raise
            rows.append({'name':path.name,'status':'already_present'})
    print(json.dumps({'documents':rows,'stats':kb.stats(),'generation_calls':0},ensure_ascii=False,indent=2))
