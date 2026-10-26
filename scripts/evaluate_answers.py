"""Plan first; --run generates four comparable answer sets for manual scoring.

No credential is accepted on the command line or written to the report.
"""
import argparse
import getpass
import hashlib
import json
import os
import re
import random
import shutil
import sqlite3
import sys
import tempfile
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from campus.routing import infer_category


def category_for(question):
    return infer_category(question)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=ROOT / 'evaluation/public_questions.development.json')
    parser.add_argument('--allow-draft', action='store_true')
    parser.add_argument('--run', action='store_true', help='实际调用模型 API；不加此项只输出计划')
    parser.add_argument('--limit', type=int, default=5)
    parser.add_argument('--base-url', default='')
    parser.add_argument('--model', default='')
    parser.add_argument('--school', default='', help='四组共享的学校背景')
    parser.add_argument('--order',choices=['balanced','grouped'],default=None)
    parser.add_argument('--seed',type=int,default=None)
    parser.add_argument('--hybrid-strategy',choices=['hybrid','hybrid_rerank'],default='')
    parser.add_argument('--frozen',type=Path,help='使用已准备的实验快照；题集与代码哈希必须匹配')
    args = parser.parse_args()
    if not 1 <= args.limit <= 200:
        parser.error('limit must be 1–200')
    frozen=json.loads((args.frozen/'manifest.json').read_text(encoding='utf-8')) if args.frozen else None
    if frozen:
        locked={'base_url':frozen['configuration']['base_url'],'model':frozen['configuration']['model'],
                'school':frozen['school'],'order':frozen['execution_order'],'seed':frozen['seed'],
                'hybrid_strategy':frozen['configuration']['retrieval_strategy']}
        for name,value in locked.items():
            supplied=getattr(args,name)
            if supplied is not None and supplied!='' and supplied!=value:
                parser.error(f'--{name.replace("_","-")} 与冻结配置不一致；变更方案须重新冻结。')
            setattr(args,name,value)
    else:
        args.school=args.school or '中国传媒大学'
        args.order=args.order or 'balanced'
        args.seed=2023 if args.seed is None else args.seed
    dataset_bytes = (args.frozen/'dataset.json' if args.frozen else args.dataset).read_bytes()
    dataset = json.loads(dataset_bytes.decode('utf-8'))
    cases = dataset['cases'][:args.limit]
    if not cases:
        parser.error('题集没有可运行的问题。')
    if frozen:
        if args.run and not frozen.get('all_labels_reviewed'):
            parser.error('快照候选题尚未人工审核，不能执行正式生成。')
        if hashlib.sha256(dataset_bytes).hexdigest()!=frozen['dataset_sha256']:
            parser.error('快照题集哈希不匹配，请重新冻结。')
        if hashlib.sha256((args.frozen/'knowledge.db').read_bytes()).hexdigest()!=frozen['database_sha256']:
            parser.error('快照数据库哈希不匹配，请重新冻结。')
        if any(hashlib.sha256((ROOT/path).read_bytes()).hexdigest()!=digest for path,digest in frozen['source_hashes'].items()):
            parser.error('当前代码与快照不一致，请重新冻结。')
        for relative,digest in frozen.get('model_file_hashes',{}).items():
            file=Path(frozen['configuration']['model_path'])/relative
            if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest()!=digest:
                parser.error('本地Embedding文件与快照不一致，请准备同版本模型或重新冻结。')
    draft = any(not c.get('reviewed') for c in cases) or dataset.get('status', '').startswith('draft')
    if draft and not args.allow_draft:
        parser.error('问题集未经人工核对，开发试运行需加 --allow-draft。正式实验请使用独立审核题集。')
    local = frozen['configuration'] if frozen else json.loads((ROOT / 'data/settings.json').read_text(encoding='utf-8'))
    base = args.base_url or local.get('base_url') or 'https://api.deepseek.com'
    model = args.model or local.get('model') or 'deepseek-flash'
    hybrid_strategy=args.hybrid_strategy or (local.get('retrieval_strategy') if local.get('retrieval_strategy') in ('hybrid','hybrid_rerank') else 'hybrid')
    modes=['direct','tfidf','semantic','hybrid']
    ordered=list(cases)
    if args.order=='balanced':
        random.Random(args.seed).shuffle(ordered)
        schedule=[(case,mode) for i,case in enumerate(ordered) for mode in modes[i%4:]+modes[:i%4]]
    else:
        schedule=[(case,mode) for mode in modes for case in cases]
    plan = {'questions':len(cases), 'maximum_generation_calls':4 * len(cases), 'school':args.school,
            'base_url':base, 'model':model, 'draft':draft,
            'temperature':0.1, 'max_output_tokens_per_call':1400,
            'modes':['direct', 'tfidf', 'semantic', 'hybrid'],
            'execution_order':args.order,'seed':args.seed,'hybrid_strategy':hybrid_strategy,
            'execution_schedule':[{'id':case['id'],'mode':mode} for case,mode in schedule],
            'question_ids':[c['id'] for c in cases],
            'note':'RAG 无证据时本地拒答，不调用生成。实际调用可能产生费用；仅 --run 执行。'}
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    if not args.run:
        return
    from campus.providers import generate_answer, test_provider, validate_base_url, build_messages
    from campus.knowledge import KnowledgeBase
    base = validate_base_url(base)
    from campus.credentials import load_key
    key = os.environ.get('CAMPUS_LLM_API_KEY') or load_key(ROOT/'data', base) or getpass.getpass('API 密钥（隐藏输入，本机兼容服务可留空）：')
    settings = {'base_url':base, 'model':model, 'api_key':key}
    connection = test_provider(settings)
    if not connection['selected_model_available']:
        parser.error('模型列表中没有所选模型，请先核对服务商实际模型名。')
    destination = ROOT / 'evaluation/results' / ('answers-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.json')
    destination.parent.mkdir(exist_ok=True)
    (ROOT / 'output').mkdir(exist_ok=True)
    report = {'created_at':datetime.now().astimezone().isoformat(), 'plan':plan,
              'dataset_sha256':hashlib.sha256(dataset_bytes).hexdigest(),
              'embedding_model':json.loads((ROOT/'models/bge-small-zh-v1.5/campus_model_info.json').read_text(encoding='utf-8')),
              'rows':[], 'completed':False,
              'generation_attempts':0, 'generation_successes':0,
              'parameters':{'top_k':4,'tfidf_threshold':0.08,'semantic_threshold':0.55,
                            'chunk_characters':400,'overlap_characters':60,'rrf_constant':60,
                            'hybrid_strategy':hybrid_strategy,'execution_order':args.order,'seed':args.seed,
                            'target_cohort':local.get('target_cohort','2023'),
                            'target_major':local.get('target_major','计算机科学与技术')},
              'prompts':{'direct_system':'你是校园知识问答助手。请用中文回答，不确定时说明无法确认。',
                         'rag_system':build_messages('',[])[0]['content']},
              'dataset_role':dataset.get('role','development'),
              'note':'模型原始回答，未经人工评分；不自动计算答案准确率。'}
    def save():
        pending = destination.with_suffix('.tmp')
        pending.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        pending.replace(destination)
    # Work against a consistent disposable snapshot, preserving the running app's indexes.
    with tempfile.TemporaryDirectory(dir=ROOT/'output') as temporary:
        snapshot = Path(temporary)
        source_directory=args.frozen if args.frozen else ROOT/'data'
        with closing(sqlite3.connect(source_directory/'knowledge.db')) as source, closing(sqlite3.connect(snapshot/'knowledge.db')) as target:
            source.backup(target)
        shutil.copytree(source_directory/'uploads', snapshot/'uploads')
        kb = KnowledgeBase(snapshot)
        report['corpus'] = [{k:d[k] for k in ('name','version','sha256')} for d in kb.documents() if not d['sample']]
        semantic_kb=KnowledgeBase(snapshot)
        semantic_kb.rebuild(mode='semantic',model_path=local.get('model_path') or str(ROOT/'models/bge-small-zh-v1.5'),
                            query_prefix=local.get('query_prefix','为这个句子生成表示以用于检索相关文章：'))
        for case,mode in schedule:
            active_kb=kb if mode=='tfidf' else semantic_kb
            start = time.perf_counter()
            question = case['question']
            year = re.search(r'(20\d{2})\s*(?:级|年入学)', question)
            cohort = year.group(1) if year else local.get('target_cohort','2023')
            major = local.get('target_major','计算机科学与技术')
            scoped = question + f'\n用户所在学校：{args.school}。用户适用范围：{cohort}级，{major}。'
            sources = [] if mode == 'direct' else active_kb.search(question, top_k=4, category=category_for(question),
                include_samples=False, min_score=0.08 if mode == 'tfidf' else 0.55,
                cohort=cohort, major=major, strategy=hybrid_strategy if mode == 'hybrid' else 'vector')
            retrieval_ms = (time.perf_counter()-start)*1000
            generation_start = time.perf_counter()
            telemetry = {}
            try:
                if mode == 'direct' or sources:
                    if report['generation_attempts'] >= plan['maximum_generation_calls']:
                        raise ValueError('达到本次生成请求上限，已停止。')
                    report['generation_attempts'] += 1
                    save()
                if mode == 'direct':
                    messages = [{'role':'system','content':'你是校园知识问答助手。请用中文回答，不确定时说明无法确认。'},
                                {'role':'user','content':scoped}]
                    answer, warnings = generate_answer(scoped, [], settings, messages=messages, telemetry=telemetry)
                elif sources:
                    answer, warnings = generate_answer(scoped, sources, settings, telemetry=telemetry)
                else:
                    answer, warnings = '当前知识库中未找到足够相关的资料，无法确认。', []
                if mode == 'direct' or sources:
                    report['generation_successes'] += 1
            except ValueError as exc:
                report['error'] = str(exc)
                save()
                raise SystemExit(f'调用失败，已停止后续请求并保存部分结果：{destination}') from None
            report['rows'].append({'id':case['id'], 'mode':mode, 'question':question,
                'reference':{k:case.get(k) for k in ('document','page','evidence','reference_answer')},
                'answer':answer, 'warnings':warnings, 'sources':sources,
                'provider':telemetry,
                'retrieval_ms':round(retrieval_ms,3) if mode != 'direct' else 0,
                'generation_ms':round((time.perf_counter()-generation_start)*1000,3) if mode == 'direct' or sources else 0,
                'elapsed_ms':round((time.perf_counter()-start)*1000,3),
                'manual_correctness':None, 'manual_evidence_support':None, 'manual_citation_correctness':None})
            save()
            print(f"Completed {mode}: {case['id']} ({len(report['rows'])}/{4*len(cases)})", flush=True)
        report['completed'] = True
        report['completed_at'] = datetime.now().astimezone().isoformat()
        save()
    print(destination)


if __name__ == '__main__':
    main()
