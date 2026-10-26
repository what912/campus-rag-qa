"""Offline experimental bookkeeping and citation-number checks; does not grade answer correctness."""
import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
LABELS = {'direct':'直接回答','tfidf':'TF-IDF RAG','semantic':'语义 RAG','hybrid':'混合 RAG'}


def normalize(value):
    return re.sub(r'\s+','',value or '')


def summarize(report):
    if not report.get('completed'):
        raise ValueError('本批次尚未完成，不能生成最终汇总。')
    groups = []
    audits = []
    for row in report['rows']:
        numbers = [int(n) for n in re.findall(r'\[(\d+)\]',row['answer'])]
        valid = {s['citation'] for s in row['sources']}
        golden = row['reference']
        found = any(s['name']==golden['document'] and s['page']==golden['page']
                    and normalize(golden['evidence']) in normalize(s['text']) for s in row['sources'])
        audits.append({'id':row['id'],'mode':row['mode'],
                       'generated':bool(row['provider']), 'reference_fragment_found':found if row['mode']!='direct' else None,
                       'citation_numbers_present':bool(numbers) if row['mode']!='direct' and row['provider'] else None,
                       'invalid_citation_numbers':sorted(set(numbers)-valid) if row['mode']!='direct' else [],
                       'truncated':row['provider'].get('finish_reason')=='length',
                       'needs_content_review':True})
    for mode,label in LABELS.items():
        rows = [r for r in report['rows'] if r['mode']==mode]
        checked = [a for a in audits if a['mode']==mode]
        generated = [r for r in rows if r['provider']]
        usages = [r['provider'].get('usage') or {} for r in generated]
        metrics = {'mode':mode,'label':label,'questions':len(rows),'generation_calls':len(generated),
            'local_no_evidence':sum(not r['provider'] for r in rows),
            'mean_elapsed_ms':float(np.mean([r['elapsed_ms'] for r in rows])),
            'p95_elapsed_ms':float(np.percentile([r['elapsed_ms'] for r in rows],95)),
            'mean_generation_ms':float(np.mean([r['generation_ms'] for r in generated])) if generated else None,
            'prompt_tokens':sum(u.get('prompt_tokens',0) for u in usages),
            'completion_tokens':sum(u.get('completion_tokens',0) for u in usages),
            'total_tokens':sum(u.get('total_tokens',0) for u in usages),
            'responses_with_usage':sum(bool(u) for u in usages),
            'truncated_answers':sum(a['truncated'] for a in checked),
            'answers_with_invalid_citation_numbers':sum(bool(a['invalid_citation_numbers']) for a in checked),
            'generated_answers_without_citations':sum(a['generated'] and a['citation_numbers_present'] is False for a in checked),
            'reference_fragment_found':sum(a['reference_fragment_found'] is True for a in checked) if mode!='direct' else None}
        groups.append(metrics)
    direct_prompt = report.get('prompts',{}).get('direct_system','')
    school_explicit = bool(report.get('plan',{}).get('school')) or '中国传媒大学' in direct_prompt
    return {'created_at':datetime.now().astimezone().isoformat(),
            'completed':report['completed'],'dataset_role':report['dataset_role'],
            'generation_attempts':report.get('generation_attempts'),'generation_successes':report.get('generation_successes'),
            'result_rows':len(report['rows']), 'corrections':report.get('corrections',[]),
            'response_models':dict(Counter(r['provider'].get('model') for r in report['rows'] if r['provider'])),
            'groups':groups,'audit':audits,'baseline_school_explicit':school_explicit,
            'limitations':['19题已参与开发，不是独立测试集。','当前题集没有无依据题，无法评价无依据拒答率。',
                '引用编号存在不代表对应结论受证据支持；答案正确性需逐条核对。',
                '四组顺序执行，API负载与缓存会影响时延，未随机化。'] +
                ([] if school_explicit else ['直接回答组未明确学校名称，而RAG组可从资料获知学校；本轮不能作为公平的RAG效果结论。']) +
                (['本报告含开发后路由修正，原始记录另保留；不能视为冻结配置的一次独立实验。'] if report.get('corrections') else [])}


def export_review(report,path):
    lines=['# 模型回答核对表','','这是实际保存的模型原始回答和检索依据，不是人工评分结果。正文、条件和引用需逐条核对；评分栏留空。']
    by_id = {}
    for row in report['rows']:
        by_id.setdefault(row['id'],[]).append(row)
    for question_id,rows in by_id.items():
        lines += ['',f"## {question_id} · {rows[0]['question']}",'',
                  f"标注依据：{rows[0]['reference']['document']}，文件第{rows[0]['reference']['page']}页；证据提示：{rows[0]['reference']['evidence']}。"]
        for row in rows:
            lines += ['',f"### {LABELS[row['mode']]}",'',row['answer'],'',
                      f"总耗时：{row['elapsed_ms']:.1f} ms。模型：{row['provider'].get('model') or '无证据，本地提示'}。",
                      '', '人工正确性（0–2）：____；证据支持：____；引用正确：____。']
            if row['sources']:
                lines += ['','<details>','<summary>展开本组检索证据</summary>','']
                for s in row['sources']:
                    lines += [f"**[{s['citation']}] {s['name']} · 第{s['page']}页 / 片段{s['ordinal']}**",'',s['text'],'']
                lines += ['</details>']
    path.write_text('\n'.join(lines)+'\n',encoding='utf-8')


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--report',type=Path)
    args=parser.parse_args()
    source=args.report or sorted(p for p in (ROOT/'evaluation/results').glob('answers-*.json')
                                if not p.stem.endswith('-summary'))[-1]
    report=json.loads(source.read_text(encoding='utf-8'))
    summary=summarize(report)
    summary.update(source_report=source.name,source_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    destination=source.with_name(source.stem+'-summary.json')
    destination.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    with source.with_name(source.stem+'-metrics.csv').open('w',encoding='utf-8-sig',newline='') as file:
        writer=csv.DictWriter(file,fieldnames=list(summary['groups'][0]))
        writer.writeheader();writer.writerows(summary['groups'])
    with source.with_name(source.stem+'-manual-scores.csv').open('w',encoding='utf-8-sig',newline='') as file:
        writer=csv.writer(file)
        writer.writerow(['题号','方案','问题','参考文件','参考页码','回答','人工正确性0至2','证据支持','引用正确','备注'])
        for row in report['rows']:
            writer.writerow([row['id'],LABELS[row['mode']],row['question'],row['reference']['document'],row['reference']['page'],row['answer'],'','','',''])
    export_review(report,ROOT/'docs/模型回答核对表.md')
    print(json.dumps({k:v for k,v in summary.items() if k not in ('audit','corrections')},ensure_ascii=False,indent=2))
