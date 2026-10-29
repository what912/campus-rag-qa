"""Register reviewed CCS PDFs for the existing bootstrap importer; no model calls."""
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
from campus.documents import MAX_BYTES, chunk_pages, parse_document

ENTRY_URL = 'https://ccs.cuc.edu.cn/'
JOBS = (
    ('学院教师公开简介汇编.pdf', 'teachers', '师资信息', 'faculty'),
    ('学院课程公开信息与教师对应索引.pdf', 'courses', '课程简介', 'courses'),
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_records(records: list[dict], kind: str) -> list[dict]:
    """Keep publication dates from each page, separate from the collection date."""
    found: dict[tuple, dict] = {}

    def add(url='', date='', title=''):
        if not url:
            return
        identity = (url, date)
        found.setdefault(identity, {'url': url, 'published_at': date, 'title': title})

    for record in records:
        if kind == 'teachers':
            add(record.get('source_url'), record.get('published_at', ''), record['name'])
        else:
            for evidence in record.get('source_evidence', []):
                add(evidence.get('source_url'), evidence.get('published_at', ''), evidence.get('title', ''))
            for relation in record.get('teachers', []):
                add(relation.get('source_url'), relation.get('source_date', ''), relation.get('name', '') + '公开简介')
    return list(found.values())


def register(source: Path, pdf_dir: Path, manifest_path: Path) -> dict:
    data = json.loads(source.read_text(encoding='utf-8'))
    collected_at = data['collected_at']
    documents = []
    from pypdf import PdfReader

    for number, (filename, kind, category, domain) in enumerate(JOBS, 1):
        path = (pdf_dir / filename).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file():
            raise ValueError('资料文件不存在或不在项目目录：' + str(path))
        if path.stat().st_size > MAX_BYTES:
            raise ValueError('PDF 超过系统的 10 MB 文件限制：' + filename)
        reader = PdfReader(path)
        if len(reader.pages) > 300:
            raise ValueError('PDF 超过系统的 300 页限制：' + filename)
        records = data[kind]
        chunks = chunk_pages(parse_document(filename, path.read_bytes()))
        expected = {record['name'] for record in records}
        prefix = '教师档案' if kind == 'teachers' else '课程条目'
        expected_headings = {f"{prefix} {index:03d}｜{record['name']}" for index, record in enumerate(records, 1)}
        headings = {chunk['heading'] for chunk in chunks}
        missing = sorted(expected_headings - headings)
        if missing:
            raise ValueError('PDF 解析后缺少条目标题：' + repr(missing))
        origins = source_records(records, kind)
        note = (
            '学院官网公开资料的助手整理汇编，非学校发布的原生PDF；各条目保留官方来源与原页日期。'
            '采集日不能当作发布日期，不代表当前学期任课表或招生名额。'
            '缺少简介、历史授课、教学活动和社会培训均按条目标注；不能据名称补写课程内容或教师关系。'
        )
        metadata = {
            'domain': domain,
            'faculty': '计算机与网络空间安全学院',
            'major': '',
            'cohort': '',
            'published_at': '',
            'authority': '中国传媒大学计算机与网络空间安全学院官网公开页面及链接附件；助手离线整理',
            'reference_only': False,
            'conversion_method': 'official_public_sources_reviewed_compilation',
            'original_filename': source.name,
            'original_sha256': digest(source),
            'source_collection': {
                'entry_url': ENTRY_URL,
                'collected_at': collected_at,
                'publication_date_scope': '每篇官方原页分别保留；汇编本身没有统一官方发布日期',
                'source_dates': [{'url': item['url'], 'published_at': item['published_at']} for item in origins],
            },
            'coverage_note': data.get('limitations', []),
            'review_note': note,
        }
        metadata['teacher_names' if kind == 'teachers' else 'course_names'] = sorted(expected)
        documents.append({
            'id': f'ccs-reviewed-{number:02d}',
            'name': filename,
            'local_path': path.relative_to(ROOT).as_posix(),
            'original_path': source.resolve().relative_to(ROOT).as_posix(),
            'sha256': digest(path),
            'original_sha256': digest(source),
            'bytes': path.stat().st_size,
            'pages': len(reader.pages),
            'expected_chunks': len(chunks),
            'record_count': len(records),
            'unique_names': len(expected),
            'category': category,
            'version': str(collected_at)[:10] + '采集；各条目版本见来源',
            'source_page': ENTRY_URL,
            'source_records': origins,
            'collected_at': collected_at,
            'metadata': metadata,
        })
    manifest = {
        'prepared_at': datetime.now().astimezone().isoformat(),
        'entry_url': ENTRY_URL,
        'collected_at': collected_at,
        'source': source.resolve().relative_to(ROOT).as_posix(),
        'source_sha256': digest(source),
        'documents': documents,
        'limitations': data.get('limitations', []),
        'generation_api_calls': 0,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / 'materials/ccs-official/academic-records-reviewed.json')
    parser.add_argument('--pdf-dir', type=Path, default=ROOT / 'materials/ccs-official/pdf-reviewed')
    parser.add_argument('--manifest', type=Path, default=ROOT / 'materials/ccs-official/manifest.json')
    args = parser.parse_args()
    report = register(args.source, args.pdf_dir, args.manifest)
    print(json.dumps({'manifest': str(args.manifest), 'documents': [
        {key: item[key] for key in ('name', 'category', 'pages', 'expected_chunks', 'record_count', 'sha256')}
        for item in report['documents']], 'generation_api_calls': 0}, ensure_ascii=False, indent=2))
