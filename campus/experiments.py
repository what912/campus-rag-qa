"""Read saved experiments and persist explicit user review; never calls a model."""
import hashlib
import json
import re
from datetime import datetime,timezone
from pathlib import Path


def report_path(root,filename):
    if not re.fullmatch(r'answers-\d{8}-\d{6}(?:-corrected)?\.json',filename):
        raise ValueError('实验记录名称无效。')
    folder=(Path(root)/'evaluation/results').resolve()
    path=(folder/filename).resolve()
    if path.parent!=folder or not path.is_file():
        raise ValueError('实验记录不存在。')
    return path


def read_report(root,filename):
    path=report_path(root,filename)
    body=path.read_bytes()
    report=json.loads(body)
    digest=hashlib.sha256(body).hexdigest()
    initial_path=Path(root)/'evaluation/results/assistant-initial-review-20261013.json'
    initial=json.loads(initial_path.read_text(encoding='utf-8')) if initial_path.is_file() else {}
    initial_rows=initial.get('rows',[]) if initial.get('source_sha256')==digest else []
    return {**report,'filename':filename,'sha256':digest,'assistant_initial_review':initial_rows}


def list_reports(root):
    folder=Path(root)/'evaluation/results'
    reports=[]
    for path in sorted(folder.glob('answers-*.json'),reverse=True):
        if not re.fullmatch(r'answers-\d{8}-\d{6}(?:-corrected)?\.json',path.name):
            continue
        report=read_report(root,path.name)
        reports.append({'filename':path.name,'created_at':report['created_at'],
            'rows':len(report['rows']),'calls':report.get('generation_attempts'),
            'completed':report.get('completed'),'corrected':bool(report.get('corrections')),
            'school_explicit':bool(report.get('plan',{}).get('school'))})
    return reports


def read_review(data_dir,report):
    path=Path(data_dir)/'reviews'/report['filename']
    if not path.is_file():
        return {'reviewer':'','rows':[],'source_sha256':report['sha256']}
    review=json.loads(path.read_text(encoding='utf-8'))
    if review.get('source_sha256')!=report['sha256']:
        raise ValueError('原始实验内容发生变化，旧评分不可自动套用。')
    return review


def save_review(data_dir,report,reviewer,rows):
    valid={(r['id'],r['mode']) for r in report['rows']}
    keys=[(r['id'],r['mode']) for r in rows]
    if len(set(keys))!=len(keys) or any(key not in valid for key in keys):
        raise ValueError('评分条目重复或与当前实验不对应。')
    if not reviewer.strip():
        raise ValueError('请填写评分人姓名或标识，便于记录复核来源。')
    folder=Path(data_dir)/'reviews';folder.mkdir(exist_ok=True,parents=True)
    payload={'role':'user_review','reviewer':reviewer.strip(),'source_sha256':report['sha256'],
        'updated_at':datetime.now(timezone.utc).isoformat(),'rows':rows,
        'note':'由页面填写保存。是否独立复核需按实际人员与流程确认，不自动认证为正式研究评分。'}
    destination=folder/report['filename'];pending=destination.with_suffix('.tmp')
    pending.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8');pending.replace(destination)
    return payload
