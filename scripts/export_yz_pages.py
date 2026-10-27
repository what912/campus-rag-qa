"""Offline, provenance-preserving PDF export of downloaded CUC admissions pages.

Parsing is available separately from PDF authoring. No network requests are made.
Merged table cells are expanded only according to explicit HTML spans, never by
forward-filling blank values. Directory quotas remain original-cell groups.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from html import escape
from pathlib import Path
from urllib.parse import urljoin

from lxml import html


ARTICLE_XPATH = '//*[contains(concat(" ",normalize-space(@class)," ")," wp_articlecontent ")]'
DIRECTORY_NOTICE = '合并单元格展开；人数按原表分组、不得逐方向相加。重复展示人数用于保留关系，不表示各方向分别招生该人数。'
BLOCK_TAGS = {'p', 'div', 'section', 'article', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}
SKIP_TAGS = {'script', 'style', 'noscript', 'iframe', 'form', 'nav'}


def _text(node) -> str:
    """Preserve line boundaries in cells, mathematical entities and link labels."""
    parts = []

    def visit(element):
        if not isinstance(element.tag, str) or element.tag.lower() in SKIP_TAGS:
            return
        if element.text:
            parts.append(element.text)
        for child in element:
            tag = child.tag.lower() if isinstance(child.tag, str) else ''
            if tag == 'br':
                parts.append('\n')
            else:
                if tag in BLOCK_TAGS:
                    parts.append('\n')
                visit(child)
                if tag in BLOCK_TAGS:
                    parts.append('\n')
            if child.tail:
                parts.append(child.tail)

    visit(node)
    lines = [re.sub(r'[\t\r\f\v \u00a0\u3000]+', ' ', line).strip()
             for line in ''.join(parts).split('\n')]
    return '\n'.join(line for line in lines if line)


def expand_table(table, table_number: int = 1) -> list[list[dict | None]]:
    """Expand rowspan/colspan, keeping the exact originating cell for every slot."""
    rows = [r for r in table.xpath('.//tr') if r.xpath('ancestor::table[1]')[0] is table]
    occupied = {}
    width = 0
    for ri, row in enumerate(rows):
        ci = 0
        for node in row.xpath('./th|./td'):
            while (ri, ci) in occupied:
                ci += 1
            try:
                rowspan, colspan = int(node.get('rowspan', '1')), int(node.get('colspan', '1'))
            except ValueError as exc:
                raise ValueError('表格包含无效 rowspan/colspan，停止以避免错误继承。') from exc
            if rowspan == 0:  # HTML: spans to the end of the current row group.
                rowspan = sum(r.getparent() is row.getparent() for r in rows[ri:])
            if rowspan < 1 or colspan < 1 or rowspan * colspan > 100000:
                raise ValueError('表格合并单元格范围无效或过大。')
            text = _text(node)
            strong_text = ''.join(_text(b) for b in node.xpath('.//strong|.//b'))
            fully_bold = bool(text and re.sub(r'\s+', '', text) == re.sub(r'\s+', '', strong_text))
            cell = {'text': text, 'row': ri + 1, 'column': ci + 1,
                    'rowspan': rowspan, 'colspan': colspan,
                    'id': f'T{table_number}:R{ri + 1}C{ci + 1}', 'header': node.tag.lower() == 'th' or fully_bold}
            for rr in range(ri, min(len(rows), ri + rowspan)):
                for cc in range(ci, ci + colspan):
                    if (rr, cc) in occupied:
                        raise ValueError('表格合并单元格重叠，停止以避免错误继承。')
                    occupied[rr, cc] = cell
            ci += colspan
            width = max(width, ci)
            if len(occupied) > 200000:
                raise ValueError('表格过大，停止自动排版。')
    width = max([width] + [c + 1 for _, c in occupied])
    return [[occupied.get((ri, ci)) for ci in range(width)] for ri in range(len(rows))]


def _unique_cells(row):
    seen = set()
    return [cell for cell in row if cell and not (cell['id'] in seen or seen.add(cell['id']))]


def _header_row(row) -> bool:
    cells = _unique_cells(row)
    if not cells:
        return False
    if all(c['header'] for c in cells):
        return True
    matches = sum(bool(re.search(r'^(学科或专业|类别或领域|专业代码|专业名称|研究方向|研究领域简介|初试科目|复试科目|拟招.*人数|推免.*人数|招生人数|招生计划|招生指标|学院[（(].*|培养单位|导师姓名|电子邮箱)$', c['text'].replace('\n', ''))) for c in cells)
    return matches >= 2


def _headers(header_rows, width):
    result = []
    for column in range(width):
        values = []
        for row in header_rows:
            cell = row[column]
            if cell and cell['text'] and cell['text'] not in values:
                values.append(cell['text'])
        result.append(' / '.join(values) or f'第{column + 1}列')
    return result


def table_records(grid, table_number=1, context='') -> dict:
    """One record per direction, with explicit quota-cell grouping and headers."""
    records, header_rows = [], []
    width = max(map(len, grid), default=0)
    section = context
    in_data = False
    notices = []
    for row_number, row in enumerate(grid, 1):
        unique = _unique_cells(row)
        if not unique or not any(c['text'] for c in unique):
            continue
        if len(unique) == 1 and unique[0]['colspan'] >= width and width > 1:
            section = unique[0]['text']
            header_rows = []
            in_data = False
            notices.append(section)
            continue
        if _header_row(row):
            if in_data:
                header_rows = []
            header_rows.append(row)
            in_data = False
            continue
        in_data = True
        labels = _headers(header_rows, width)
        if not header_rows:
            # Unlabelled data stays literal; do not guess column meanings.
            notices.append(f'表{table_number}第{row_number}行缺少可识别表头，使用原列序号。')
        fields = []
        for column, cell in enumerate(row):
            fields.append({'label': labels[column], 'value': cell['text'] if cell else '',
                           'source_cell': cell['id'] if cell else None,
                           'source_rowspan': cell['rowspan'] if cell else 1,
                           'source_colspan': cell['colspan'] if cell else 1,
                           'inherited': bool(cell and (cell['row'] != row_number or cell['column'] != column + 1)),
                           'quota_group': cell['id'] if cell and re.search(r'人数|计划|名额|指标', labels[column]) else None})
        direction_index = next((i for i, f in enumerate(fields) if '研究方向' in f['label']), None)
        direction_values = [fields[direction_index]['value']] if direction_index is not None else ['']
        if direction_index is not None:
            value = fields[direction_index]['value']
            if re.match(r'^\d{2}[^\d]', value) and re.search(r'\n\s*\d{2}[^\d]', value):
                direction_values = re.split(r'\n(?=\s*\d{2}[^\d])', value)
        for direction in direction_values:
            copied = [dict(field) for field in fields]
            if direction_index is not None:
                copied[direction_index]['value'] = direction
            records.append({'table': table_number, 'source_row': row_number, 'section': section,
                            'direction': direction, 'fields': copied})
    quota_caveat = next((re.sub(r'^\s*\d+[.．、]\s*', '', line)
                         for notice in notices for line in notice.splitlines()
                         if '拟招生人数' in line and '指标' in line), '')
    for record in records:
        record['quota_caveat'] = quota_caveat
    return {'columns': width, 'records': records, 'notices': list(dict.fromkeys(notices)),
            'quota_caveat': quota_caveat}


def _field_order(field):
    """Keep programme, direction, faculty and exams adjacent for retrieval."""
    label = field['label'].replace('\n', '')
    if field['quota_group']:
        return 5
    if re.search(r'学科或专业|类别或领域|专业代码|专业名称', label):
        return 0
    if '研究方向' in label:
        return 1
    if re.search(r'学院|培养单位', label):
        return 2
    if re.search(r'初试科目|复试科目|考试科目', label):
        return 3
    return 4


def parse_page(source: Path, source_url: str, published_at: str = '', kind: str = 'article') -> dict:
    """Parse a saved page without authoring an artifact or requesting any URL."""
    if kind not in {'article', 'directory'}:
        raise ValueError('kind 必须为 article 或 directory')
    source = Path(source)
    tree = html.fromstring(source.read_bytes(), parser=html.HTMLParser(no_network=True))
    articles = tree.xpath(ARTICLE_XPATH)
    if not articles:
        raise ValueError('无法定位 wp_articlecontent 正文，停止导出以避免混入导航或其他页面。')
    article = articles[0]
    for node in article.xpath('.//script|.//style|.//noscript|.//iframe|.//form'):
        node.drop_tree()
    for node in article.xpath('.//*[@hidden or contains(translate(@style," ",""),"display:none")]'):
        if node.getparent() is not None:
            node.drop_tree()
    headings = tree.xpath('//h1[normalize-space()]')
    title = _text(headings[0]) if headings else tree.xpath('string(//title)').strip()
    title = title or source.stem
    if not published_at:
        page_text = ' '.join(tree.itertext())
        match = re.search(r'发布时间\s*[：:]?\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2})', page_text)
        published_at = match.group(1).replace('/', '-') if match else ''
    blocks, tables, warnings = [], [], []
    context = ''
    scoped_fields = []

    def append_text(text, tag='p'):
        nonlocal context
        if text:
            blocks.append({'kind': 'heading' if re.fullmatch(r'h[1-6]', tag) else 'paragraph', 'text': text})
            context = text if len(text) < 120 else context

    def walk(node):
        nonlocal scoped_fields
        tag = node.tag.lower() if isinstance(node.tag, str) else ''
        if tag in SKIP_TAGS:
            return
        if tag == 'table':
            number = len(tables) + 1
            grid = expand_table(node, number)
            table = {'number': number, 'grid': grid, 'context': context,
                     **table_records(grid, number, context)}
            key_value = bool(grid and len(grid[0]) == 2 and all(
                row[0] and row[1] and re.search(r'[：:]$', row[0]['text']) for row in grid))
            if key_value:
                # Doctoral directory pages state programme/faculty/exam context in
                # a separate key-value table above their supervisor rows.
                scoped_fields = [{'label': row[0]['text'].rstrip('：:'), 'value': row[1]['text'],
                                  'source_cell': row[1]['id']} for row in grid]
                table['key_value_fields'] = [dict(field) for field in scoped_fields]
                table['records'] = []
                table['notices'] = []
            else:
                for record in table['records']:
                    record['context_fields'] = [dict(field) for field in scoped_fields]
            tables.append(table)
            blocks.append({'kind': 'table', 'table': table})
            return
        if tag == 'img':
            label = node.get('alt', '').strip()
            address = urljoin(source_url, node.get('src', ''))
            append_text(f'［网页图片：{label or "无替代文字"}；{address}；本排版版未转录图片内容］')
            warnings.append('正文含图片；图片内容未作 OCR，须核对官方原页或图片。')
            return
        structural = [child for child in node if isinstance(child.tag, str) and
                      (child.tag.lower() in BLOCK_TAGS | {'table', 'ul', 'ol', 'img'})]
        if not structural:
            append_text(_text(node), tag)
            return
        if node.text and node.text.strip():
            append_text(node.text.strip(), tag)
        inline = []
        for child in node:
            child_tag = child.tag.lower() if isinstance(child.tag, str) else ''
            if child_tag in BLOCK_TAGS | {'table', 'ul', 'ol', 'img'}:
                if inline:
                    append_text(' '.join(inline));inline.clear()
                walk(child)
            elif child_tag not in SKIP_TAGS:
                value = _text(child)
                if value:
                    inline.append(value)
            if child.tail and child.tail.strip():
                inline.append(child.tail.strip())
        if inline:
            append_text(' '.join(inline))

    walk(article)
    if not blocks:
        raise ValueError('正文为空，未创建 PDF。')
    links, seen = [], set()
    for link in article.xpath('.//a[@href]'):
        href = urljoin(source_url, link.get('href'))
        text = _text(link)
        if not href.lower().startswith(('http://', 'https://')) or (text, href) in seen:
            continue
        seen.add((text, href));links.append({'text': text or '未命名链接', 'url': href})
    return {'title': title, 'source_url': source_url, 'published_at': published_at,
            'collected_at': datetime.now().astimezone().date().isoformat(), 'kind': kind,
            'blocks': blocks, 'tables': tables, 'links': links, 'warnings': list(dict.fromkeys(warnings))}


def export_page(source: Path, target: Path, source_url: str, published_at: str = '', kind: str = 'article') -> dict:
    """Create a searchable PDF solely from the already downloaded HTML file."""
    parsed = parse_page(source, source_url, published_at, kind)
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
    from pypdf import PdfReader

    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    body = ParagraphStyle('yz-body', fontName='STSong-Light', fontSize=10.5, leading=17,
                          wordWrap='CJK', spaceAfter=6, splitLongWords=True)
    heading = ParagraphStyle('yz-heading', parent=body, fontSize=12, leading=19, spaceBefore=9, keepWithNext=True)
    title_style = ParagraphStyle('yz-title', parent=heading, fontSize=16, leading=24, alignment=TA_CENTER, spaceAfter=14)
    small = ParagraphStyle('yz-small', parent=body, fontSize=8.5, leading=13)
    cell_style = ParagraphStyle('yz-cell', parent=small, spaceAfter=1)
    # The built-in CJK font can omit U+00B7 visually in some renderers.
    safe = lambda value: escape(str(value)).replace('·','<font name="Helvetica">·</font>').replace('\n', '<br/>')
    para = lambda value, style=body: Paragraph(safe(value), style)
    display_title = parsed['title'] + '（官网网页正文排版版）'
    story = [para(display_title, title_style), para('来源 URL：' + source_url, small),
             para('发布日期：' + (parsed['published_at'] or '原页未识别，未推断') + '；采集日：' + parsed['collected_at'], small),
             para('本文件由已保存的官网网页正文离线排版。链接所指附件未自动合并，政策以原页及其正式附件为准。', small)]
    if kind == 'directory':
        story.append(para(DIRECTORY_NOTICE, small))
    story.append(Spacer(1, 10))
    record_count = 0
    for block in parsed['blocks']:
        if block['kind'] != 'table':
            story.append(para(block['text'], heading if block['kind'] == 'heading' else body))
            continue
        table = block['table']
        if table.get('key_value_fields'):
            story.extend(para(field['label'] + '：' + field['value']) for field in table['key_value_fields'])
            story.append(Spacer(1, 8))
            continue
        if kind == 'directory':
            for record in table['records']:
                record_count += 1
                programme = next((field['value'] for field in record['fields']
                                  if re.search(r'学科或专业|类别或领域|专业代码|专业名称', field['label'].replace('\n', ''))
                                  and field['value']), '')
                if not programme:
                    programme = next((field['value'] for field in record.get('context_fields', [])
                                      if '专业' in field['label'] and field['value']), '')
                short_title = re.sub(r'\s+', ' ', programme or parsed['title']).strip()
                core = [para(f'目录记录 {record_count}｜{short_title}', heading)]
                if record['section']:
                    core.append(para('原表分组：' + record['section'], small))
                for field in record.get('context_fields', []):
                    core.append(para(field['label'] + '：' + field['value']))
                quota_fields = []
                for field in sorted(record['fields'], key=_field_order):
                    value = field['value'] or '（原表空白，未补写）'
                    if field['quota_group']:
                        value += f'（人数分组 {field["quota_group"]}；原格覆盖 {field["source_rowspan"]} 行；同组不得按方向累加）'
                        quota_fields.append(para(field['label'] + '：' + value))
                    else:
                        core.append(para(field['label'] + '：' + value))
                # Keep programme/direction/faculty/exams (and doctoral context)
                # together; quotas and long caveats may continue on the next page.
                story.append(KeepTogether(core))
                story.extend(quota_fields)
                if record.get('quota_caveat'):
                    story.append(para('原表人数限定（重复引用）：' + record['quota_caveat'], small))
                story.append(Spacer(1, 8))
        else:
            grid = table['grid']
            if not grid:
                continue
            width = len(grid[0])
            # Very wide tables become labelled rows so no source cell can run off the page.
            complex_rows = any(cell and cell['rowspan'] > 1 for row in grid for cell in row)
            if width > 6 or complex_rows or any(cell and len(cell['text']) > 300 for row in grid for cell in row):
                for record in table['records']:
                    story.append(para(f'表{table["number"]} 原表第{record["source_row"]}行', heading))
                    story.extend(para(f['label'] + '：' + (f['value'] or '（空白）')) for f in record['fields'])
            else:
                values = [[para(cell['text'] if cell else '', cell_style) for cell in row] for row in grid]
                rendered = Table(values, colWidths=[(A4[0] - 88) / width] * width,
                                 repeatRows=1 if _header_row(grid[0]) else 0, splitByRow=1)
                rendered.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), .35, colors.HexColor('#CCCCCC')),
                                              ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                                              ('LEFTPADDING', (0, 0), (-1, -1), 5),
                                              ('RIGHTPADDING', (0, 0), (-1, -1), 5),
                                              ('TOPPADDING', (0, 0), (-1, -1), 5),
                                              ('BOTTOMPADDING', (0, 0), (-1, -1), 5)]))
                story.extend([rendered, Spacer(1, 10)])
        # Full-width rows often contain binding caveats rather than another
        # direction. Preserve them even when no following data record exists.
        for notice in table['notices']:
            if '缺少可识别表头' not in notice:
                story.append(para('原表完整行文字：' + notice, small))
    if parsed['links']:
        story.append(para('正文链接（链接目标附件未自动包含在本文件中）', heading))
        for link in parsed['links']:
            story.append(para(link['text'] + '：' + link['url'], small))
    for warning in parsed['warnings']:
        story.append(para('转换范围说明：' + warning, small))

    def footer(canvas, document):
        canvas.setFont('STSong-Light', 8)
        canvas.setFillColor(colors.HexColor('#666666'))
        canvas.drawCentredString(A4[0] / 2, 22, f'官网网页正文排版版｜第 {document.page} 页')

    target = Path(target);target.parent.mkdir(parents=True, exist_ok=True)
    SimpleDocTemplate(str(target), pagesize=A4, leftMargin=44, rightMargin=44,
                      topMargin=38, bottomMargin=40, title=display_title,
                      author='中国传媒大学研究生招生网；离线正文排版', allowSplitting=True).build(story, onFirstPage=footer, onLaterPages=footer)
    document=PdfReader(target)
    character_counts = [len(page.extract_text() or '') for page in document.pages]
    return {'source_path': str(source), 'target_path': str(target), 'title': parsed['title'],
            'source_url': source_url, 'published_at': parsed['published_at'], 'collected_at': parsed['collected_at'],
            'kind': kind, 'pages': len(character_counts), 'characters_per_page': character_counts,
            'table_count': len(parsed['tables']), 'directory_records': record_count,
            'quota_caveats': [table['quota_caveat'] for table in parsed['tables'] if table['quota_caveat']],
            'quota_rule': DIRECTORY_NOTICE if kind == 'directory' else '', 'links': parsed['links'],
            'warnings': parsed['warnings'] + [notice for table in parsed['tables'] for notice in table['notices'] if '缺少可识别表头' in notice],
            'bytes': target.stat().st_size, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--source-url', required=True)
    parser.add_argument('--published-at', default='')
    parser.add_argument('--kind', choices=('article', 'directory'), default='article')
    args = parser.parse_args()
    print(json.dumps(export_page(args.source, args.target, args.source_url, args.published_at, args.kind), ensure_ascii=False, indent=2))
