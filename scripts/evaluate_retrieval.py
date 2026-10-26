"""Reproducible retrieval checks; refuses unreviewed labels unless explicitly running a draft."""
import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from campus.evaluation import evaluate_cases


def normalize(text):
    return re.sub(r'\s+', '', text or '')


def run(kb, cases, strategy, threshold, top_k):
    return evaluate_cases(kb,cases,strategy=strategy,threshold=threshold,top_k=top_k)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset', type=Path, default=ROOT / 'evaluation/public_questions.development.json')
    parser.add_argument('--allow-draft', action='store_true')
    parser.add_argument('--top-k', type=int, default=4)
    args = parser.parse_args()
    if not 1 <= args.top_k <= 8:
        parser.error('top-k must be 1–8')
    dataset = json.loads(args.dataset.read_text(encoding='utf-8'))
    if not args.allow_draft and any(not c.get('reviewed') for c in dataset['cases']):
        parser.error('存在未人工核对的问题标注。开发检查请显式加 --allow-draft；不得作为正式论文结果。')
    from campus.knowledge import KnowledgeBase
    (ROOT/'output').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT/'output') as temporary:
        snapshot=Path(temporary)
        with closing(sqlite3.connect(ROOT/'data/knowledge.db')) as source, closing(sqlite3.connect(snapshot/'knowledge.db')) as target:
            source.backup(target)
        shutil.copytree(ROOT/'data/uploads',snapshot/'uploads')
        kb = KnowledgeBase(snapshot)
        baseline = run(kb, dataset['cases'], 'vector', 0.08, args.top_k)
        kb.rebuild(mode='semantic', model_path=str(ROOT / 'models/bge-small-zh-v1.5'),
                   query_prefix='为这个句子生成表示以用于检索相关文章：')
        dense = run(kb, dataset['cases'], 'vector', 0.55, args.top_k)
        hybrid = run(kb, dataset['cases'], 'hybrid', 0.55, args.top_k)
        corpus=[{k:d[k] for k in ('name','sha256','version')} for d in kb.documents() if not d['sample']]
    report = {'created_at':datetime.now().astimezone().isoformat(), 'draft':bool(args.allow_draft),
              'dataset_role':dataset.get('role','development'),
              'dataset_sha256':hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
              'model':json.loads((ROOT / 'models/bge-small-zh-v1.5/campus_model_info.json').read_text(encoding='utf-8')),
              'corpus':corpus,
              'results':[baseline,dense,hybrid],
              'note':'开发题集结果，不是独立测试集；不评价生成答案。无无依据题时拒答率为空，不计为零或100%。'}
    destination = ROOT / 'evaluation/results'
    destination.mkdir(exist_ok=True)
    name = 'retrieval-draft-report.json' if args.allow_draft else 'retrieval-development-report.json'
    (destination / name).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps([{k:v for k,v in result.items() if k != 'rows'} for result in report['results']], ensure_ascii=False, indent=2))
