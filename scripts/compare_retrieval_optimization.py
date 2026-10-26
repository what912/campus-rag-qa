"""Compare existing retrieval with focused hybrid reranking on the development set only."""
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
from contextlib import closing
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from campus.knowledge import KnowledgeBase
from scripts.evaluate_retrieval import run
from campus.search_features import RERANK_VERSION

if __name__=='__main__':
    dataset_path=ROOT/'evaluation/public_questions.development.json'
    dataset=json.loads(dataset_path.read_text(encoding='utf-8'))
    with tempfile.TemporaryDirectory(dir=ROOT/'output') as temporary:
        snapshot=Path(temporary)
        with closing(sqlite3.connect(ROOT/'data/knowledge.db')) as source,closing(sqlite3.connect(snapshot/'knowledge.db')) as target:
            source.backup(target)
        shutil.copytree(ROOT/'data/uploads',snapshot/'uploads')
        kb=KnowledgeBase(snapshot)
        results=[run(kb,dataset['cases'],'vector',.08,4)]
        kb.rebuild(mode='semantic',model_path=str(ROOT/'models/bge-small-zh-v1.5'),
                   query_prefix='为这个句子生成表示以用于检索相关文章：')
        results += [run(kb,dataset['cases'],strategy,.55,4) for strategy in ('vector','hybrid','hybrid_rerank')]
    report={'created_at':datetime.now().astimezone().isoformat(),'dataset_role':'development',
        'dataset_sha256':hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
        'rerank_version':RERANK_VERSION,'results':results,
        'note':'仅19道已核对开发题。新增重排序依据开发集设计，不是独立测试结论；没有调用生成或运行20道候选题。'}
    destination=ROOT/'evaluation/results/retrieval-optimization-report.json'
    destination.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps([{k:v for k,v in r.items() if k!='rows'} for r in results],ensure_ascii=False,indent=2))
