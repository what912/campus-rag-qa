"""Evidence-aware retrieval evaluation shared by the API and experiment CLI."""
import re
import time
import numpy as np
from .routing import infer_category


def normalize(text):
    return re.sub(r'\s+','',text or '')


def evaluate_cases(kb,cases,*,strategy,threshold,top_k=4,cohort='2023',major='计算机科学与技术',include_samples=False):
    rows=[]
    for index,case in enumerate(cases):
        start=time.perf_counter()
        document=case.get('document',case.get('expected_document',''))
        year=re.search(r'(20\d{2})\s*(?:级|年入学)',case['question'])
        category=infer_category(case['question'])
        found=kb.search(case['question'],top_k=top_k,include_samples=include_samples,
            min_score=threshold,cohort=case.get('cohort') or (year.group(1) if year else cohort),
            major=case.get('major') or major,strategy=strategy,category=category)
        evidence=case.get('evidence','')
        available=any(r['name']==document and (include_samples or not r['sample']) for r in kb.rows)
        gold={r['id'] for r in kb.rows if (include_samples or not r['sample']) and document and evidence
            and r['name']==document and r['page']==case.get('page')
            and normalize(evidence) in normalize(r['text'])}
        ranking=[rank for rank,s in enumerate(found,1) if s['id'] in gold]
        document_hit=any(s['name']==document for s in found) if document else not found
        evidence_hit=bool(ranking) if document and evidence and gold else (not found if not document else None)
        rows.append({'id':case.get('id',str(index)), 'question':case['question'],
            'expected_document':document,'reference_page':case.get('page'),'reference_evidence':evidence,
            'category':category,'answerable':bool(document),'skipped':bool(document and not available),
            'label_resolved':bool(gold) if document and evidence else not document,
            'document_hit':document_hit,'evidence_hit':evidence_hit,'hit':evidence_hit if evidence_hit is not None else document_hit,
            'recall_at_k':len(ranking)/len(gold) if gold else None,
            'mrr':1/min(ranking) if ranking else 0,'rejected':not found,
            'elapsed_ms':round((time.perf_counter()-start)*1000,3),
            'sources':[{'document':s['name'],'page':s['page'],'chunk':s['ordinal'],'score':s['score'],
                        'ranking_score':s['ranking_score']} for s in found]})
    positives=[r for r in rows if r['answerable'] and not r['skipped']]
    resolved=[r for r in positives if r['label_resolved']]
    negatives=[r for r in rows if not r['answerable']]
    mean=lambda values:float(np.mean(values)) if values else None
    return {'mode':kb.mode,'strategy':strategy,'threshold':threshold,'top_k':top_k,
        'scored_answerable':len(resolved),'total_questions':len(rows),
        'unresolved_labels':[r['id'] for r in rows if r['answerable'] and not r['label_resolved']],
        'document_hit_rate':mean([r['document_hit'] for r in positives]),
        'evidence_hit_rate':mean([r['evidence_hit'] for r in resolved]),
        'mean_recall_at_k':mean([r['recall_at_k'] for r in resolved]),
        'mean_mrr':mean([r['mrr'] for r in resolved]),
        'negative_rejection_rate':mean([r['rejected'] for r in negatives]),
        'rejection_rate':mean([r['rejected'] for r in negatives]),
        'answerable_false_rejection_rate':mean([r['rejected'] for r in positives]),
        'retrieval_p95_ms':float(np.percentile([r['elapsed_ms'] for r in rows],95)) if rows else None,'rows':rows}
