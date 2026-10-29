"""Restore the bundled campus corpus without generating answers or changing model credentials."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from campus.knowledge import KnowledgeBase


def load_bundled_items(root=ROOT):
    public=json.loads((root/'materials/manifest.json').read_text(encoding='utf-8'))
    professional=json.loads((root/'materials/user-material-manifest.json').read_text(encoding='utf-8'))['documents']
    items=[p for p in public if p['usage']=='knowledge']+professional
    overrides=[]
    for relative in ('materials/admissions/manifest.json','materials/yz-official/manifest.json','materials/ccs-official/manifest.json'):
        path=root/relative
        if path.is_file():
            payload=json.loads(path.read_text(encoding='utf-8'));items.extend(payload['documents'])
            overrides.extend(payload.get('reference_overrides',[]))
    return items,overrides


def apply_reference_overrides(kb,overrides):
    """Prefer verified official bodies while preserving supplied screenshots and originals."""
    changed=0
    with kb.lock,kb.connect() as db:
        for item in overrides:
            if item.get('reference_only') is not True:raise ValueError('Overrides may only demote a known source to reference')
            row=db.execute('SELECT id,metadata FROM documents WHERE sha256=?',(item['sha256'],)).fetchone()
            if row is None:continue
            metadata=json.loads(row['metadata']);before=dict(metadata)
            metadata['reference_only']=True
            note=item.get('review_note_suffix','')
            if note and note not in metadata.get('review_note',''):
                metadata['review_note']=(metadata.get('review_note','')+' '+note).strip()
            if metadata!=before:
                db.execute('UPDATE documents SET metadata=? WHERE id=?',(json.dumps(metadata,ensure_ascii=False),row['id']))
                changed+=1
    if changed:kb.rebuild();kb.save_index()
    return changed


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,default=ROOT/'data')
    parser.add_argument('--enable-ocr',action='store_true')
    args=parser.parse_args()
    kb=KnowledgeBase(args.data_dir);kb.enable_ocr=args.enable_ocr
    kb.seed(ROOT/'samples')
    items,overrides=load_bundled_items()
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
    updated=apply_reference_overrides(kb,overrides)
    print(json.dumps({'documents':rows,'reference_updates':updated,'stats':kb.stats(),'generation_calls':0},ensure_ascii=False,indent=2))
