"""Create compact, searchable CCS course/faculty PDFs from reviewed public facts.

Keeps original acquisitions and earlier exports intact. No network or model calls.
Record headings retain the existing campus.documents chunking contract.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'materials/ccs-official'
FONT = 'CCSHei'


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def tidy(value):
    value = str(value or '').replace('\r', '').replace('\u200b', '')
    return re.sub(r'[ \t\u00a0\u3000]+', ' ', value).strip()


def prepare_data(supplement: Path):
    original = FOLDER / 'academic-records.json'
    data = json.loads(original.read_text(encoding='utf-8'))
    added = []
    supplemental = json.loads(supplement.read_text(encoding='utf-8')) if supplement.is_file() else {}
    extra = supplemental if isinstance(supplemental, list) else supplemental.get('courses', [])
    keys = {(re.sub(r'\s+', '', c['name']).casefold(), c['level']): c for c in data['courses']}
    for item in extra:
        key = re.sub(r'\s+', '', item['name']).casefold(), item['level']
        if key in keys:
            existing = keys[key]
            if existing['name'] != item['name'] and item['name'] not in existing['aliases']:
                existing['aliases'].append(item['name'])
            seen = {(e.get('source_url'), str(e.get('page', '')), e.get('kind')) for e in existing['source_evidence']}
            existing['source_evidence'].extend(e for e in item['source_evidence']
                                               if (e.get('source_url'), str(e.get('page', '')), e.get('kind')) not in seen)
            continue
        assert item['description_status'] == 'name_only' and not item.get('description')
        assert not item.get('teachers') and item.get('source_evidence')
        keys[key] = item
        data['courses'].append(item)
        added.append(item['name'])
    for update in supplemental.get('course_updates', []):
        course = keys[(re.sub(r'\s+', '', update['name']).casefold(), update['level'])]
        course['description'] += '\n' + update['description_append']
        course['source_evidence'].append(update['source_evidence'])
        course['notes'].append(update['source_evidence']['scope'])
    order = {'本科': 0, '本科学术训练': 1, '硕士': 2, '研究生': 3, '博士': 4,
             '研究生学术训练': 5, '预科': 6, '未注明': 7, '未明确': 7,
             '课外竞赛培训': 8, '社会培训': 9}
    data['courses'].sort(key=lambda c: (order.get(c['level'], 7), c['name']))
    data['coverage']['课程及培训项目条目'] = len(data['courses'])
    data['coverage']['仅名称条目'] = sum(not c['description'] for c in data['courses'])
    data['coverage']['有有限内容的条目'] = sum(bool(c['description']) for c in data['courses'])
    data['coverage']['独立课程名称'] = len({c['name'] for c in data['courses']})
    data['coverage']['课程地图补充名称条目'] = len(added)
    data['coverage']['课程地图目视核对名称'] = len(extra)
    data['coverage']['正文页面'] += len(supplemental.get('additional_sources', []))
    data['reviewed_at'] = datetime.now(timezone(timedelta(hours=8))).isoformat()
    data['review'] = {'original_path': original.relative_to(ROOT).as_posix(),
                      'original_sha256': hashlib.sha256(original.read_bytes()).hexdigest(),
                      'course_map_supplement': added,
                      'additional_sources': supplemental.get('additional_sources', []),
                      'source_audits': ['data/ccs_cuc/faculty_audit.json', 'data/ccs_cuc/course_audit.json']}
    # Earlier scope statements are retained in the original records. The reviewed
    # edition explicitly records that the readable curriculum image was reviewed.
    data['limitations'] = [v for v in data['limitations'] if '未识别图像' not in v]
    if extra:
        data['limitations'].append('补充课程地图中可辨识的课程名称，仅作为对应版本的名称证据；图中未给出逐门课程简介或任课教师。')
    target = FOLDER / 'academic-records-reviewed.json'
    save_json(target, data)
    return data, target


def build(supplement):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, PageBreak,
                                   KeepTogether, Flowable, Table, TableStyle)
    from pypdf import PdfReader, PdfWriter
    from reportlab import rl_config
    rl_config.invariant = 1

    data, source = prepare_data(supplement)
    pdfmetrics.registerFont(TTFont(FONT, 'C:/Windows/Fonts/simhei.ttf'))
    ink = colors.HexColor('#142B46')
    accent = colors.HexColor('#166D91')
    gray = colors.HexColor('#536578')
    pale = colors.HexColor('#E9F2F7')
    body = ParagraphStyle('body', fontName=FONT, fontSize=10, leading=15,
                          wordWrap='CJK', spaceAfter=5, textColor=ink)
    small = ParagraphStyle('small', parent=body, fontSize=8.2, leading=12, spaceAfter=3, textColor=gray)
    title = ParagraphStyle('title', parent=body, fontSize=25, leading=35, spaceAfter=22)
    sub = ParagraphStyle('sub', parent=body, fontSize=15, leading=22, spaceBefore=10, spaceAfter=12)
    heading = ParagraphStyle('heading', parent=body, fontSize=13, leading=20, spaceBefore=10,
                             spaceAfter=7, keepWithNext=True, textColor=accent)
    index_style = ParagraphStyle('index', parent=small, fontSize=9, leading=14)

    def p(value, style=body):
        return Paragraph(escape(tidy(value)).replace('\n', '<br/>'), style)

    def link(url, label=None):
        url = tidy(url)
        if not re.match(r'^https://ccs\.cuc\.edu\.cn/', url):
            return escape(label or url or '原资料未提供链接')
        return '<link href="' + escape(url, quote=True) + '" color="#166D91">' + escape(label or url) + '</link>'

    def source_line(url, date='', page='', label=''):
        tail = '网页标注日期：' + (date or '未标注')
        if page:
            tail += '；附件页码：' + tidy(page)
        lines = ([escape(label)] if label else []) + [link(url), escape(tail)]
        return Paragraph('<br/>'.join(lines), small)

    class Rule(Flowable):
        def __init__(self):
            super().__init__(); self.height = 12
        def draw(self):
            self.canv.setStrokeColor(colors.HexColor('#CCDCE6'))
            self.canv.setLineWidth(.5); self.canv.line(0, 4, self._availWidth, 4)
        def wrap(self, w, h):
            self._availWidth = w
            return w, self.height

    class RecordHeading(Flowable):
        def __init__(self, value, anchor):
            super().__init__(); self.value = value; self.anchor = anchor
            self.keepWithNext = True
        def wrap(self, w, h):
            self.width = w
            return w, 29
        def draw(self):
            c = self.canv
            c.bookmarkPage(self.anchor)
            size = min(13, self.width / max(1, pdfmetrics.stringWidth(self.value, FONT, 1)))
            c.setFont(FONT, size); c.setFillColor(accent)
            c.drawString(0, 8, self.value)

    def footer(c, doc):
        c.setFont(FONT, 8); c.setFillColor(gray)
        c.drawCentredString(A4[0] / 2, 22, f'学院公开网页信息汇编｜第 {doc.page} 页')

    def make_cover(kind):
        story = [Spacer(1, 26), p('中国传媒大学', sub), p('计算机与网络空间\n安全学院', title),
                 p(kind, sub), p('官网公开资料汇编 · 2026年10月27日核查', small), Spacer(1, 16)]
        story.append(p('资料来源：' + 'https://ccs.cuc.edu.cn/'))
        story.append(p('范围：师资队伍、导师情况、本科及研究生教学与专业介绍、培养方案附件，以及课程教学和社会培训的公开报道。'))
        counts = [('公开人员简介', str(len(data['teachers'])) + ' 份（含行政、实验实践与兼职人员）'),
                  ('课程与培训信息', str(len(data['courses'])) + ' 个名称/层次条目'),
                  ('可核对的有限内容', str(sum(bool(c['description']) for c in data['courses'])) + ' 条，其余仅课程名称'),
                  ('官网栏目核查', f'15 个栏目、37 个列表分页、{data["coverage"]["正文页面"]} 个正文页')]
        table = Table([[p(k, small), p(v, body)] for k, v in counts], colWidths=[120, 385])
        table.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), pale),
                                   ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                                   ('LEFTPADDING', (0, 0), (-1, -1), 10),
                                   ('RIGHTPADDING', (0, 0), (-1, -1), 10),
                                   ('TOPPADDING', (0, 0), (-1, -1), 9),
                                   ('BOTTOMPADDING', (0, 0), (-1, -1), 7)]))
        story.extend([Spacer(1, 14), table, Spacer(1, 18)])
        story.append(p('阅读说明', heading))
        for text in [
            '简介采用公开事实摘要，保留原网页链接及网页标注日期。采集日期不代表网页内容最后更新日期。',
            '课程名称按培养层次保留，部分课程可能出现多个层次条目。历史培养方案、教学报道和社会培训分别标注，不能据此认定当前学期课表。',
            '只有来源明确写出讲授关系才列为授课证据；教学活动提及者与培训导师单列。未公开的简介和教师关系不补写。',
            '本资料用于毕业设计的知识库检索与来源核对，属于官网资料整理，不作为学校现行培养方案或排课通知。']:
            story.append(p(text, small))
        return story

    teacher_story = make_cover('教师公开简介与授课索引')
    teacher_story.extend([PageBreak(), p('教师索引', sub), p('点击姓名可跳转到相应简介；也可使用 PDF 文本搜索。', small)])
    cells = []
    for n, t in enumerate(data['teachers'], 1):
        cells.append(Paragraph(f'<link href="#t{n:03d}" color="#166D91">{n:03d} {escape(t["name"])}</link>', index_style))
    rows = [cells[i:i+4] + [''] * (4-len(cells[i:i+4])) for i in range(0, len(cells), 4)]
    table = Table(rows, colWidths=[126.25]*4)
    table.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('BOTTOMPADDING', (0, 0), (-1, -1), 8)]))
    teacher_story.extend([table, Spacer(1, 10), p('仅见姓名，未公开独立简介', heading)])
    for item in data.get('unlinked_people', []):
        teacher_story.append(p(item['name'] + '：' + item['context']))
        teacher_story.append(source_line(item['source_url']))
    teacher_story.append(PageBreak())
    teacher_headings = []
    for n, t in enumerate(data['teachers'], 1):
        h = f'教师档案 {n:03d}｜{t["name"]}'
        parts = [RecordHeading(h, f't{n:03d}'), p('教师姓名：' + t['name'])]
        parts.append(p('官网栏目分类：' + '；'.join(t['roles']), small))
        for field in t['fields']:
            if field['label'] in ('姓名', '讲授课程'):
                continue
            parts.append(p(field['label'] + '：' + field['value']))
        if t['bio_summary']:
            parts.append(p('简介摘要：' + t['bio_summary']))
        for mention in t.get('course_mentions', []):
            # Some source fields are selected excerpts. Do not claim that a
            # non-contiguous selection is a single verbatim original sentence.
            parts.append(p('公开讲授课程：' + '；'.join(mention['courses'])))
            detail_states = Counter(d.get('temporal_status') for d in mention.get('course_details', []))
            if detail_states.get('explicit_historical'):
                parts.append(p('授课时态：含明确历史授课经历，不能据此认定当前任课。', small))
            elif detail_states.get('explicit_current'):
                parts.append(p('授课时态：原网页写“目前”，未核对当前学期。', small))
            else:
                parts.append(p('授课时态：来源列出讲授课程，未指定当前学期。', small))
        if not t.get('course_mentions'):
            parts.append(p('授课信息：该简介未提供明确讲授课程。', small))
        for note in t['notes']:
            if 'course_details' not in note and '课程提及为空' not in note:
                parts.append(p('来源说明：' + note, small))
        parts.extend([source_line(t['source_url'], t['published_at']), Rule()])
        teacher_story.append(KeepTogether(parts))
        teacher_headings.append(h)

    course_story = make_cover('课程公开信息与教师对应索引')
    course_story.extend([PageBreak(), p('课程层次及内容状态', sub)])
    for level, count in Counter(c['level'] for c in data['courses']).items():
        course_story.append(p(level + '：' + str(count) + ' 条'))
    course_story.extend([Spacer(1, 10), p('标记说明', heading)])
    statuses = {'name_only': '仅见课程名称，未公开可核对的逐门简介。',
                'partial_description': '公开报道提供有限教学内容，不是完整课程大纲。',
                'historical_2018': '2018版培养方案的历史实践环节内容。',
                'training_description': '非学历社会培训项目或模块内容。'}
    for status, text in statuses.items():
        course_story.append(p(text + f' 本汇编 {sum(c["description_status"] == status for c in data["courses"])} 条。'))
    course_story.append(p('每条保留可核对的官方来源。教师简介见另册或合订版教师部分。课程名、培养层次和历史记录分别保留，未将近似课程名称强行合并。', small))
    map_evidence = next((e for c in data['courses'] for e in c['source_evidence'] if e.get('image_url')), None)
    if map_evidence:
        course_story.append(source_line(map_evidence['image_url'], map_evidence['published_at'],
                                        label='补充核查：2022版培养方案课程地图原图（56个可辨名称）'))
    course_story.append(PageBreak())
    course_headings = []
    for n, c in enumerate(data['courses'], 1):
        h = f'课程条目 {n:03d}｜{c["name"]}'
        parts = [RecordHeading(h, f'c{n:03d}'), p('课程名称：' + c['name']),
                 p('层次：' + c['level'] + '；' + statuses.get(c['description_status'], c['description_status']), small)]
        if c['aliases']:
            parts.append(p('原页其他写法：' + '；'.join(c['aliases']), small))
        if c['description']:
            parts.append(p('公开内容摘要：' + c['description']))
        explicit = [r for r in c['teachers'] if r.get('relation_type') == '教师个人页明确讲授字段']
        activities = [r for r in c['teachers'] if r.get('relation_type') == 'teaching_activity']
        training = [r for r in c['teachers'] if r.get('relation_type') == 'training_instructor']
        if explicit:
            for relation in explicit:
                parts.append(p('公开讲授教师：' + relation['name'] + '；' + relation.get('temporal_status', '来源未注明学期。')))
                parts.append(source_line(relation['source_url'], relation.get('source_date', ''), label='任课证据：' + relation['name'] + '公开简介的讲授字段'))
        else:
            parts.append(p('任课教师：未取得明确的教师个人页讲授证据；不据研究方向、活动身份推断。', small))
        for relation in activities:
            parts.append(p('教学活动提及者：' + relation['name'] + '；该活动身份不单独证明当前任课。', small))
            parts.append(source_line(relation['source_url'], relation.get('source_date', ''), label='教学活动出处'))
        for relation in training:
            parts.append(p('培训项目导师：' + relation['name'] + '；仅适用于来源所列培训项目。', small))
            parts.append(source_line(relation['source_url'], relation.get('source_date', ''), label='培训导师出处'))
        seen = set()
        for ev in c['source_evidence']:
            key = ev.get('source_url'), str(ev.get('page', ''))
            if key in seen:
                continue
            seen.add(key)
            # Teacher sources already appear beside their explicit relation.
            if ev.get('kind') == 'teacher_profile_course_name' and any(r['source_url'] == ev.get('source_url') for r in explicit):
                continue
            parts.append(p('课程来源：' + ev.get('title', '官方页面'), small))
            parts.append(source_line(ev.get('source_url', ''), ev.get('published_at', ''), ev.get('page', '')))
        for note in dict.fromkeys(c['notes']):
            if note.startswith('出处适用范围：') or note.startswith('课程名称来自教师公开讲授字段'):
                continue
            parts.append(p('来源说明：' + note, small))
        parts.append(Rule())
        # Blocks stay together when they fit a page. ReportLab splits an
        # oversized KeepTogether at paragraph boundaries, retaining the heading.
        course_story.append(KeepTogether(parts))
        course_headings.append(h)

    folder = FOLDER / 'pdf-reviewed'; folder.mkdir(parents=True, exist_ok=True)
    outputs = []
    for filename, story, headings, kind in [
        ('学院教师公开简介汇编.pdf', teacher_story, teacher_headings, 'teachers'),
        ('学院课程公开信息与教师对应索引.pdf', course_story, course_headings, 'courses')]:
        target = folder / filename
        SimpleDocTemplate(str(target), pagesize=A4, leftMargin=43, rightMargin=43,
                          topMargin=42, bottomMargin=42, title=filename[:-4],
                          author='公开官网资料整理，用于毕业设计知识库').build(story, onFirstPage=footer, onLaterPages=footer)
        reader = PdfReader(target)
        texts = [page.extract_text() or '' for page in reader.pages]
        locations = []
        norm = lambda s: re.sub(r'\s+', '', s)
        for h in headings:
            found = [i+1 for i, text in enumerate(texts) if norm(h) in norm(text)]
            if len(found) != 1:
                raise ValueError(f'标题检查失败：{h}：{found}')
            locations.append({'heading': h, 'page': found[0]})
        if len(reader.pages) > 300:
            raise ValueError('分类版超过知识库单份300页限制')
        outputs.append({'kind': kind, 'path': str(target), 'pages': len(texts),
                        'bytes': target.stat().st_size, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                        'heading_pages': locations, 'characters': sum(map(len, texts))})

    # The single reading copy contains every page of the two indexed PDFs.
    # Hierarchical bookmarks provide direct access to all 64 people and courses.
    target = ROOT / 'output/pdf/中国传媒大学计算机与网络空间安全学院_课程与教师资料汇编.pdf'
    target.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter(); offset = 0
    for out, section in zip(outputs, ['教师公开简介与授课索引', '课程公开信息与教师对应索引']):
        reader = PdfReader(out['path'])
        writer.append(reader, import_outline=False)
        parent = writer.add_outline_item(section, offset)
        for item in out['heading_pages']:
            writer.add_outline_item(item['heading'], offset+item['page']-1, parent=parent)
        offset += len(reader.pages)
    writer.add_metadata({'/Title': '中国传媒大学计算机与网络空间安全学院 课程与教师资料汇编',
                         '/Author': '官网公开资料整理', '/Subject': '毕业设计知识库资料；2026-10-27核查'})
    writer.page_mode = '/UseOutlines'
    with target.open('wb') as stream:
        writer.write(stream)
    report = {'reviewed_at': data['reviewed_at'], 'source': str(source),
              'coverage': data['coverage'], 'outputs': outputs,
              'combined': {'path': str(target), 'pages': offset, 'bytes': target.stat().st_size,
                           'sha256': hashlib.sha256(target.read_bytes()).hexdigest()},
              'by_description_status': dict(Counter(c['description_status'] for c in data['courses'])),
              'source_links_preserved': True, 'network_calls': 0, 'generation_calls': 0,
              'visual_qa_required': True}
    save_json(FOLDER / 'quality/reviewed-conversion-report.json', report)
    print(json.dumps({k: v for k, v in report.items() if k != 'outputs'}, ensure_ascii=False, indent=2))
    for out in outputs:
        print(out['kind'], out['pages'], out['bytes'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--supplement', type=Path, default=FOLDER/'review-supplements.json')
    args = parser.parse_args()
    build(args.supplement)
