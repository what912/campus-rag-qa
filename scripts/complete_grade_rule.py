"""Complete three local non-generated rows after a routing fix, respecting the original call budget."""
import argparse
import copy
import json
import shutil
import sqlite3
import sys
import tempfile
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--resume',action='store_true',help='继续已有修正记录；跳过已成功生成的行，不重试不明状态的请求')
    args=parser.parse_args()
    report=json.loads(args.report.read_text(encoding='utf-8'))
    original=[copy.deepcopy(r) for r in report['rows'] if r['id']=='student02' and r['mode']!='direct']
    if len(original)!=3 or any(r['provider'] for r in original):
        parser.error('只可补全三条尚未调用模型的student02记录，不重跑其他题。')
    if report['generation_attempts']+3>report['plan']['maximum_generation_calls']:
        parser.error('剩余请求额度不足3次，停止。')
    destination=args.report.with_name(args.report.stem+'-corrected.json')
    if destination.exists():
        if not args.resume:
            parser.error('补全记录已存在；只有显式 --resume 可继续未完成部分。')
        report=json.loads(destination.read_text(encoding='utf-8'))
        if report.get('completed') or report.get('pending_generation') or report['generation_attempts']!=report['generation_successes']:
            parser.error('记录已完成，或存在状态不明的请求，禁止重复调用。')
    from campus.credentials import load_key
    from campus.knowledge import KnowledgeBase
    from campus.providers import generate_answer
    from campus.routing import infer_category
    settings=json.loads((ROOT/'data/settings.json').read_text(encoding='utf-8'))
    settings['api_key']=load_key(ROOT/'data',settings['base_url'])
    correction={'reason':'一般课程成绩制度误路由至课程大纲，修正为办事流程；有足够检索证据时补全原本未调用API的结果。',
                'previous_rows':original,'calls_before':report['generation_attempts'],'maximum_added_calls':3}
    if args.resume and report.get('corrections'):
        correction=report['corrections'][0]
    else:
        report['corrections']=[correction]
    report['completed']=False
    def save():
        pending=destination.with_suffix('.tmp')
        pending.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        pending.replace(destination)
    with tempfile.TemporaryDirectory(dir=ROOT/'output') as temporary:
        snapshot=Path(temporary)
        with closing(sqlite3.connect(ROOT/'data/knowledge.db')) as source,closing(sqlite3.connect(snapshot/'knowledge.db')) as target:
            source.backup(target)
        shutil.copytree(ROOT/'data/uploads',snapshot/'uploads')
        kb=KnowledgeBase(snapshot)
        for mode in ('tfidf','semantic','hybrid'):
            if mode=='semantic':
                kb.rebuild(mode='semantic',model_path=str(ROOT/'models/bge-small-zh-v1.5'),
                           query_prefix='为这个句子生成表示以用于检索相关文章：')
            row=next(r for r in report['rows'] if r['id']=='student02' and r['mode']==mode)
            if row['provider']:
                continue
            start=time.perf_counter()
            sources=kb.search(row['question'],top_k=4,category=infer_category(row['question']),
                include_samples=False,min_score=.08 if mode=='tfidf' else .55,
                cohort='2023',major='计算机科学与技术',strategy='hybrid' if mode=='hybrid' else 'vector')
            if not sources:
                row.update(routing_correction=True,
                    warnings=['路由已修正；保持原实验相似度阈值，仍未检索到足够证据，因此没有发送生成请求。'],
                    retrieval_ms=round((time.perf_counter()-start)*1000,3),
                    elapsed_ms=round((time.perf_counter()-start)*1000,3))
                save()
                print(f'No evidence after routing correction: {mode}; no generation call',flush=True)
                continue
            retrieval_ms=(time.perf_counter()-start)*1000
            telemetry={}
            if report['generation_attempts']>=report['plan']['maximum_generation_calls']:
                raise SystemExit('达到授权调用上限，停止。')
            report['generation_attempts']+=1
            report['pending_generation']={'id':row['id'],'mode':mode}
            save()
            generation_start=time.perf_counter()
            try:
                answer,warnings=generate_answer(row['question']+'\n用户适用范围：2023级，计算机科学与技术。',sources,settings,telemetry=telemetry)
            except ValueError as exc:
                report['error']=str(exc);save()
                raise SystemExit('调用失败，已保存部分结果并停止后续请求。') from None
            report['generation_successes']+=1
            row.update(answer=answer,warnings=warnings,sources=sources,provider=telemetry,
                retrieval_ms=round(retrieval_ms,3),generation_ms=round((time.perf_counter()-generation_start)*1000,3),
                elapsed_ms=round((time.perf_counter()-start)*1000,3),routing_correction=True)
            report.pop('pending_generation',None)
            save()
            print(f"Completed routing correction: {mode}; total calls={report['generation_attempts']}",flush=True)
    correction['calls_after']=report['generation_attempts']
    report['completed']=True
    report['completed_at']=datetime.now().astimezone().isoformat()
    save()
    print(destination)
