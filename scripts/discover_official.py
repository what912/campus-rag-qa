"""Fetch only reviewed official CUC pages and discover embedded attachments."""
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import httpx

ROOT = Path(__file__).resolve().parents[1]
PAGES = [
    ('学籍管理规定_2025', 'https://jwc.cuc.edu.cn/2025/1118/c13082a263811/page.htm'),
    ('转专业管理办法_2025修订', 'https://jwc.cuc.edu.cn/2026/0106/c13082a265999/page.htm'),
    ('学士学位授予办法_2025', 'https://jwc.cuc.edu.cn/2025/0221/c13082a251196/page.htm'),
    ('在读证明管理办法_2024', 'https://jwc.cuc.edu.cn/2024/1123/c13082a245777/page.htm'),
    ('培养方案修读说明_2025', 'https://jwc.cuc.edu.cn/2026/0106/c6479a265996/page.htm'),
    ('学院培养方案_2022参考', 'https://ccs.cuc.edu.cn/2022/0726/c5559a196269/page.htm'),
    ('成绩单办理流程', 'https://jwc.cuc.edu.cn/2021/1125/c6366a189267/page.htm'),
]


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.resources = []

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        for key, value in attributes:
            if value and ('_upload' in value or '.pdf' in value.lower()):
                self.resources.append({'tag': tag, 'attribute': key, 'url': value,
                                       'label': attrs.get('title', attrs.get('alt', ''))})


def fetch(item):
    name, url = item
    folder = ROOT / 'materials' / 'discovery'
    folder.mkdir(parents=True, exist_ok=True)
    try:
        response = httpx.get(url, timeout=40, follow_redirects=True)
        response.raise_for_status()
        response.encoding = 'utf-8'
        (folder / (name + '.html')).write_text(response.text, encoding='utf-8')
        parser = Links()
        parser.feed(response.text)
        resources = parser.resources
        for path in re.findall(r'["\']([^"\']+\.(?:pdf|docx|png|jpg|jpeg)(?:\?[^"\']*)?)["\']', response.text, re.I):
            if '_upload' in path:
                resources.append({'tag': 'embedded', 'attribute': '', 'url': path, 'label': ''})
        seen = set()
        found = []
        for entry in resources:
            absolute = urljoin(str(response.url), entry['url'])
            if absolute not in seen:
                seen.add(absolute)
                found.append({**entry, 'url': absolute})
        return {'name': name, 'page_url': url, 'resources': found}
    except Exception as exc:
        return {'name': name, 'page_url': url, 'error': type(exc).__name__ + ': ' + str(exc)}


if __name__ == '__main__':
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(fetch, PAGES))
    (ROOT / 'materials' / 'discovery.json').write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(results, ensure_ascii=False, indent=2))
