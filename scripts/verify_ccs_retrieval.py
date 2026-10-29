"""Verify registered course/profile evidence synchronously, without generating answers."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from campus.academic import academic_subject
from campus.knowledge import KnowledgeBase
from campus.routing import infer_category


def verify(data_dir: Path, manifest_path: Path, report_path: Path, semantic=False, save_index=False) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    kb = KnowledgeBase(data_dir)
    settings_path = data_dir / 'settings.json'
    settings = json.loads(settings_path.read_text(encoding='utf-8')) if settings_path.exists() else {}
    strategy = settings.get('retrieval_strategy', 'vector') if semantic else 'vector'
    threshold = settings.get('min_score', 0.08) if semantic else 0.08
    documents = []
    stored = {document['sha256']: document for document in kb.documents()}
    for item in manifest['documents']:
        content = (ROOT / item['local_path']).read_bytes()
        assert hashlib.sha256(content).hexdigest() == item['sha256'], item['name'] + '原件哈希不符'
        assert item['sha256'] in stored, item['name'] + '尚未导入'
        document = stored[item['sha256']]
        details = kb.document(document['id'])
        assert document['category'] == item['category'], item['name'] + '分类不符'
        assert len(details['chunks']) == item['expected_chunks'], item['name'] + '片段数量不符'
        documents.append({'id': document['id'], 'name': document['name'], 'sha256': document['sha256'],
                          'category': document['category'], 'chunks': len(details['chunks'])})
    if semantic:
        kb.rebuild(mode=settings.get('embedding_mode', 'tfidf'), model_path=settings.get('model_path', ''),
                   query_prefix=settings.get('query_prefix', ''))
    if save_index:
        kb.save_index()
    examples = []
    preferred_teachers = [name for name in ('黄玮', '林卫国', '马永斌') if name in kb.teacher_subjects]
    preferred_courses = [name for name in ('数据结构', '计算机网络', '操作系统') if name in kb.course_subjects]
    assert preferred_teachers and preferred_courses, '未发现预期师资和课程标题'
    queries = [(name + '老师的研究方向是什么？', '师资信息', name) for name in preferred_teachers]
    queries += [(name + '由哪些老师讲授？', '课程简介', name) for name in preferred_courses]
    query_failures = []
    for question, category, subject in queries:
        actual_category = infer_category(question, teacher_names=kb.teacher_subjects)
        assert actual_category == category, question + '分类错误'
        sources = kb.search(question, top_k=4, category=category, include_samples=False, min_score=threshold,
                            cohort='2023', major='计算机科学与技术', strategy=strategy)
        matched = bool(sources) and all(academic_subject(source['heading'])[1] == subject for source in sources)
        if not matched:
            query_failures.append({'question': question, 'reason': '没有达到阈值的检索结果' if not sources else '混入其他人物/课程'})
        examples.append({'question': question, 'category': category, 'expected_subject': subject,
                         'passed': matched,
                         'sources': [{key: source[key] for key in ('document_id', 'name', 'page', 'heading', 'text', 'score')}
                                     for source in sources]})
    absent_question = '查无此人老师的研究方向是什么？'
    missing = kb.search(absent_question, category='师资信息', include_samples=False, min_score=threshold, strategy=strategy)
    assert not missing, '未知人物不应返回其他教师简介'
    annual = '2027年黄玮老师硕士招生名额有几个？'
    assert infer_category(annual, teacher_names=kb.teacher_subjects) == '招生信息', '师资简介不能代替年度招生依据'
    assert infer_category('数据结构课程的考核方式是什么？') == '课程大纲', '不能用课程简介替代正式大纲'
    report = {'verified_at': datetime.now().astimezone().isoformat(), 'mode': kb.mode,
              'strategy': strategy, 'min_score': threshold, 'stats': kb.stats(), 'documents': documents,
              'teacher_subjects': len(kb.teacher_subjects), 'course_subjects': len(kb.course_subjects),
              'positive_queries': examples, 'unknown_teacher_excluded': True, 'annual_admission_route_preserved': True,
              'course_outline_route_preserved': True, 'generation_api_calls': 0,
              'passed': not query_failures, 'query_failures': query_failures,
              'test_scope': '资料接入与检索冒烟核验；不是独立测试集或生成回答准确率评价'}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'data')
    parser.add_argument('--manifest', type=Path, default=ROOT / 'materials/ccs-official/manifest.json')
    parser.add_argument('--report', type=Path, default=ROOT / 'materials/ccs-official/quality/retrieval-report.json')
    parser.add_argument('--semantic', action='store_true', help='Use existing local embedding settings without reading generation credentials')
    parser.add_argument('--save-index', action='store_true', help='Save the rebuilt local index')
    args = parser.parse_args()
    result = verify(args.data_dir, args.manifest, args.report, args.semantic, args.save_index)
    print(json.dumps({key: result[key] for key in ('mode', 'strategy', 'stats', 'teacher_subjects', 'course_subjects', 'generation_api_calls')},
                     ensure_ascii=False, indent=2))
    if result['query_failures']:
        print(json.dumps({'query_failures': result['query_failures']}, ensure_ascii=False, indent=2))
        raise SystemExit(1)
