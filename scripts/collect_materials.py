"""Preserve original public documents; keep applicability-unverified references out of import."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import httpx
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
DATES = {'学籍管理规定_2025':'2025-11-18', '转专业管理办法_2025修订':'2026-01-06',
         '学士学位授予办法_2025':'2025-02-21', '在读证明管理办法_2024':'2024-08-12',
         '培养方案修读说明_2025':'2025-09-01'}


def collect(item):
    name, url, page, date, usage = item
    folder = ROOT / 'materials' / ('official' if usage == 'knowledge' else 'reference')
    folder.mkdir(parents=True, exist_ok=True)
    host = urlparse(url).hostname or ''
    if not host.endswith('.cuc.edu.cn'):
        raise ValueError('只下载已确认的中国传媒大学官方附件。')
    response = httpx.get(url, timeout=60, follow_redirects=True)
    response.raise_for_status()
    if not response.content.startswith(b'%PDF'):
        raise ValueError(name + '：响应不是 PDF。')
    path = folder / (name + '.pdf')
    path.write_bytes(response.content)
    reader = PdfReader(path)
    counts = [len((p.extract_text() or '').strip()) for p in reader.pages]
    return {'name':path.name, 'local_path':str(path.relative_to(ROOT)), 'source_url':url,
            'source_page':page, 'published_at':date, 'collected_at':'2026-10-13',
            'sha256':hashlib.sha256(response.content).hexdigest(), 'bytes':len(response.content),
            'pages':len(counts), 'characters_per_page':counts, 'usage':usage,
            'category':'办事流程' if '培养' not in name else '培养方案',
            'version':name.split('_', 1)[-1] if '_' in name else '公开流程，年份待核验',
            'major':'', 'cohort':'2025' if '修读说明_2025' in name else '',
            'review_note':'官方来源和原文已定位；适用年级和现行效力仍按文件条款核对。'}


if __name__ == '__main__':
    discovery = json.loads((ROOT / 'materials/discovery.json').read_text(encoding='utf-8'))
    items = []
    for entry in discovery:
        links = [r['url'] for r in entry.get('resources', [])
                 if '/_upload/article/files/' in r['url'] and r['url'].lower().endswith('.pdf')]
        if links and entry['name'] in DATES:
            items.append((entry['name'], links[0], entry['page_url'], DATES[entry['name']],
                          'reference' if '修读说明' in entry['name'] else 'knowledge'))
    items.append(('成绩单办理服务流程',
                  'https://jwc.cuc.edu.cn/_upload/article/files/e1/09/785cfe02455f87cf4bd2451930a4/b3fa2338-320f-4d42-987f-1adab9d99371.pdf',
                  'https://jwc.cuc.edu.cn/2026/0717/c6364a272849/page.htm', '', 'knowledge'))
    with ThreadPoolExecutor(max_workers=3) as pool:
        records = list(pool.map(collect, items))
    (ROOT / 'materials/manifest.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps([{k:r[k] for k in ('name','pages','characters_per_page','usage','bytes')} for r in records], ensure_ascii=False, indent=2))
