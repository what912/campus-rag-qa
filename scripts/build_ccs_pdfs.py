"""Build two searchable CCS academic-information PDFs from local curated JSON.

No URLs are fetched, no model is called, and no affiliation/course description is
inferred. This module is intentionally separate from collection and human review.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from html import escape
from pathlib import Path


TEACHER_FILENAME = '学院教师公开简介汇编.pdf'
COURSE_FILENAME = '学院课程公开信息与教师对应索引.pdf'
MISSING_DESCRIPTION = '仅见课程名称，未取得可核对的公开课程简介；不据名称推断教学内容、培养目标或任课安排。'
NO_TEACHER_RELATION = '未取得明确的教师—课程对应证据，不推断任课教师。'
_EXCLUDED_FIELD = re.compile(r'论文|论著|著作|基金|专利|科研项目|研究项目|项目列表|成果|获奖|荣誉')
_FACT_FIELD = re.compile(r'姓名|职称|职务|系别|系所|所属系|所属单位|研究方向|科研方向|教学专长|研究领域|教学方向|讲授|授课|课程|教育|学习经历|学位|毕业|任职|工作经历|个人简介|个人介绍|基本信息')


def clean_text(value) -> str:
    """Remove invisible controls without discarding punctuation/entities."""
    if value is None:
        return ''
    if isinstance(value, list):
        return '；'.join(clean_text(item) for item in value if clean_text(item))
    if isinstance(value, dict):
        return '；'.join(f'{key}：{clean_text(item)}' for key, item in value.items())
    value = str(value).replace('\r\n', '\n').replace('\r', '\n')
    value = re.sub(r'[\u200b\ufeff\u2060\x00-\x08\x0b\x0c\x0e-\x1f]', '', value)
    return '\n'.join(re.sub(r'[ \t\u00a0\u3000]+', ' ', line).strip()
                     for line in value.split('\n')).strip()


def inline_markup(value) -> str:
    """Escape source text and display the CID font's unsupported middle dot."""
    return escape(clean_text(value)).replace('·', '<font name="Helvetica">·</font>').replace('\n', '<br/>')


def record_heading(kind: str, number: int, name: str) -> str:
    prefix = {'teacher': '教师档案', 'course': '课程条目'}[kind]
    name = re.sub(r'\s+', ' ', clean_text(name)).strip()
    if not name:
        raise ValueError(f'{prefix}缺少名称，停止以避免错误归属。')
    return f'{prefix} {number:03d}｜{name}'


def prepare_records(data: dict) -> dict:
    """Validate and select facts; safe to call without writing any artifacts."""
    collected_at = clean_text(data.get('collected_at'))
    if not collected_at:
        raise ValueError('输入缺少 collected_at，不能给来源记录补造采集日。')
    teachers, courses, omissions = [], [], []
    seen_teacher_ids, seen_course_ids = set(), set()
    for number, item in enumerate(data.get('teachers', []), 1):
        identifier = clean_text(item.get('id')) or f'teacher-{number:03d}'
        if identifier in seen_teacher_ids:
            raise ValueError(f'重复教师 id：{identifier}')
        seen_teacher_ids.add(identifier)
        name = clean_text(item.get('name'));heading = record_heading('teacher', number, name)
        fields = []
        for field in item.get('fields', []):
            label, value = clean_text(field.get('label')), clean_text(field.get('value'))
            if not value:
                continue
            if _EXCLUDED_FIELD.search(label) or not _FACT_FIELD.search(label):
                omissions.append({'teacher_id': identifier, 'label': label,
                                  'reason': '不属于本汇编的姓名、职称、正文系别、研究方向、明确讲授课程或事实经历字段'})
                continue
            fields.append({'label': label, 'value': value})
        mentions = []
        for mention in item.get('course_mentions', []):
            raw = clean_text(mention.get('raw_text'))
            if not raw:
                omissions.append({'teacher_id': identifier, 'label': 'course_mentions',
                                  'reason': '课程对应缺少明确原句，未把仅有名称当作任课证据'})
                continue
            mentions.append({'raw_text': raw,
                             'courses': [clean_text(course) for course in mention.get('courses', [])],
                             'source_url': clean_text(mention.get('source_url')) or clean_text(item.get('source_url'))})
        teachers.append({'id': identifier, 'heading': heading, 'name': name,
                         'roles': [clean_text(role) for role in item.get('roles', [])],
                         'published_at': clean_text(item.get('published_at')),
                         'source_url': clean_text(item.get('source_url')),
                         'fields': fields, 'bio_summary': clean_text(item.get('bio_summary')),
                         'course_mentions': mentions,
                         'notes': [clean_text(note) for note in item.get('notes', [])]})
    for number, item in enumerate(data.get('courses', []), 1):
        identifier = clean_text(item.get('id')) or f'course-{number:03d}'
        if identifier in seen_course_ids:
            raise ValueError(f'重复课程 id：{identifier}')
        seen_course_ids.add(identifier)
        name = clean_text(item.get('name'));heading = record_heading('course', number, name)
        description = clean_text(item.get('description'))
        status = clean_text(item.get('description_status'))
        negative = re.search(r'missing|name.only|not.available|unavailable|not.found|no.description|仅.*名称|无简介|未.*简介|缺.*简介', status, re.I)
        has_description = bool(description and not negative)
        evidence = []
        for source in item.get('source_evidence', []):
            evidence.append({key: clean_text(source.get(key)) for key in
                             ('source_url', 'title', 'published_at', 'kind', 'excerpt','page','pdf_path')})
        relations = []
        for relation in item.get('teachers', []):
            text = clean_text(relation.get('relation_text'))
            teacher_name = clean_text(relation.get('name'))
            if not text or not teacher_name:
                omissions.append({'course_id': identifier, 'label': 'teachers',
                                  'reason': '教师对应缺少姓名或关系原句，未展示为明确任课关系'})
                continue
            relations.append({key: clean_text(relation.get(key)) for key in
                              ('name', 'source_url', 'source_date', 'relation_text','relation_type','temporal_status')})
        notes = [clean_text(note) for note in item.get('notes', [])]
        if description and not has_description:
            notes.append('输入记录标为仅名称或缺少简介；未将 description 字段当作正式课程简介。')
        courses.append({'id': identifier, 'heading': heading, 'name': name,
                        'aliases': [clean_text(alias) for alias in item.get('aliases', [])],
                        'level': clean_text(item.get('level')), 'description_status': status,
                        'description': description if has_description else '', 'has_description': has_description,
                        'source_evidence': evidence, 'teachers': relations, 'notes': notes})
    return {'collected_at': collected_at, 'coverage': data.get('coverage', {}),
            'teachers': teachers, 'courses': courses,
            'limitations': [clean_text(value) for value in data.get('limitations', [])],
            'omissions': omissions}


def build_pdfs(source: Path, output_dir: Path, report_path: Path | None = None) -> dict:
    """Author the two PDFs after the caller has recorded its artifact operation."""
    source = Path(source);output_dir = Path(output_dir)
    prepared = prepare_records(json.loads(source.read_text(encoding='utf-8')))
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, PageBreak,
                                   KeepTogether, Flowable)
    from pypdf import PdfReader

    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    body = ParagraphStyle('ccs-body', fontName='STSong-Light', fontSize=10.5, leading=17,
                          wordWrap='CJK', spaceAfter=7, splitLongWords=True)
    small = ParagraphStyle('ccs-small', parent=body, fontSize=8.5, leading=13, spaceAfter=5)
    label = ParagraphStyle('ccs-label', parent=body, fontSize=11.5, leading=18, spaceBefore=9,
                           spaceAfter=7, keepWithNext=True)
    notice = ParagraphStyle('ccs-notice', parent=body, borderColor=colors.HexColor('#CCAA80'),
                            borderWidth=.6, borderPadding=8, backColor=colors.HexColor('#FFF6E8'),
                            spaceBefore=7, spaceAfter=12)
    title = ParagraphStyle('ccs-title', parent=body, fontSize=18, leading=27, spaceAfter=18)
    para = lambda text, style=body: Paragraph(inline_markup(text), style)

    class OneLineHeading(Flowable):
        """A complete one-baseline heading; draw runs preserve middle-dot glyphs."""
        def __init__(self, text):
            Flowable.__init__(self);self.text = text;self.font_size = 15

        def wrap(self, available_width, available_height):
            self.runs = re.split('(·)', self.text)
            width_at_one = sum(pdfmetrics.stringWidth(run, 'Helvetica' if run == '·' else 'STSong-Light', 1)
                               for run in self.runs)
            self.font_size = min(15, available_width / max(width_at_one, 1))
            self.width = available_width;self.height = self.font_size * 1.5 + 12
            return self.width, self.height

        def draw(self):
            text_object = self.canv.beginText(0, self.height - self.font_size * 1.2)
            for run in self.runs:
                text_object.setFont('Helvetica' if run == '·' else 'STSong-Light', self.font_size)
                text_object.textOut(run)
            self.canv.drawText(text_object)
            self.canv.setStrokeColor(colors.HexColor('#D8E0E8'))
            self.canv.line(0, 4, self.width, 4)

    source_date = lambda date: date or '原页未标注或未识别，未推断'
    provenance = lambda url, date: [para('来源 URL：' + (url or '输入未提供'), small),
                                  para('来源发布日期：' + source_date(date) + '；采集日：' + prepared['collected_at'], small)]

    def introduction(title_text, count, kind):
        story = [para(title_text, title), para('中国传媒大学计算机与网络空间安全学院｜公开网页资料离线汇编', small),
                 para('采集日：' + prepared['collected_at'] + f'；{kind}条目数：{count}', small),
                 para('本汇编以公开页面可见事实为依据，网页发布日期不代表当前任职、当期开课或排课状态。未公开或未检索到的信息不补写。', notice)]
        if kind == '教师':
            story.append(para('正文选取姓名、职称、正文系别、研究方向、明确讲授课程和事实性经历摘要；论文、基金及成果长列表不收入本汇编。官网栏目分类与正文系别分别标注。', small))
        else:
            story.append(para('课程名称、课程简介与教师讲授证据分别标注。缺少公开简介时醒目标明“仅见课程名称”；教师讲授原句不等于当学期排课表。', small))
        if prepared['coverage']:
            story.append(para('采集范围记录', label))
            story.extend(para(str(key) + '：' + clean_text(value), small) for key, value in prepared['coverage'].items())
        if prepared['limitations']:
            story.append(para('资料范围与限制', label))
            story.extend(para(value, small) for value in prepared['limitations'] if value)
        return story

    teacher_story = introduction('学院教师公开简介汇编', len(prepared['teachers']), '教师')
    teacher_expected = []
    for teacher in prepared['teachers']:
        teacher_story.append(PageBreak())
        core = [OneLineHeading(teacher['heading']), para('教师姓名：' + teacher['name'])]
        core.extend(provenance(teacher['source_url'], teacher['published_at']))
        if teacher['roles']:
            core.append(para('官网栏目分类（非正文系别）：' + '；'.join(teacher['roles']), small))
        long_fields = []
        for field in teacher['fields']:
            value = field['label'] + '：' + field['value']
            # Long biography/experience paragraphs may continue, while factual
            # title, department, research and teaching fields remain near name.
            if re.search(r'个人简介|个人介绍|教育|学习经历|工作经历', field['label']):
                long_fields.append(para(value))
            else:
                core.append(para(value))
        teacher_story.append(KeepTogether(core));teacher_story.extend(long_fields)
        if teacher['bio_summary']:
            teacher_story.append(para('事实性经历摘要（据公开简介整理）', label))
            teacher_story.extend(para(part) for part in teacher['bio_summary'].split('\n') if part)
        if teacher['course_mentions']:
            teacher_story.append(para('明确讲授课程的公开表述', label))
            for mention in teacher['course_mentions']:
                relation = [para('教师姓名：' + teacher['name']),
                            para('原页表述：' + mention['raw_text'])]
                if mention['courses']:
                    relation.append(para('该原句中的课程名称：' + '；'.join(mention['courses'])))
                relation.extend(provenance(mention['source_url'], teacher['published_at']))
                teacher_story.append(KeepTogether(relation))
        else:
            teacher_story.append(para('公开课程证据：本条未取得明确讲授课程原句，不推断任课。', small))
        for value in teacher['notes']:
            teacher_story.append(para('来源说明：' + value, small))
        teacher_expected.append(teacher['heading'])

    course_count = len(prepared['courses'])
    course_volume_count = max(1, (course_count + 99) // 100)
    course_volumes = []
    course_story = []
    course_expected = []
    for course_index, course in enumerate(prepared['courses']):
        if course_index % 100 == 0:
            if course_story:
                course_volumes.append((course_story, course_expected))
            course_story = introduction('学院课程公开信息与教师对应索引',
                                        min(100, course_count - course_index), '课程')
            course_expected = []
            course_story.append(para(f'本卷范围：全局课程条目 {course_index + 1:03d}—{min(course_index + 100, course_count):03d}；第 {course_index // 100 + 1:02d}/{course_volume_count:02d} 卷；总课程条目数：{course_count}', small))
        course_story.append(PageBreak())
        core = [OneLineHeading(course['heading']), para('课程名称：' + course['name'])]
        if course['aliases']:
            core.append(para('原页出现的别名或写法：' + '；'.join(course['aliases']), small))
        core.append(para('层次：' + (course['level'] or '来源未明确，未推断'), small))
        core.append(para('采集日：' + prepared['collected_at'], small))
        if not course['has_description']:
            core.append(para('课程简介状态：' + MISSING_DESCRIPTION, notice))
        else:
            status_label={'partial_description':'仅有教学报道中的有限内容，非完整课程大纲',
                          'historical_2018':'仅2018级历史实践环节内容，不作为2023级现行大纲',
                          'training_description':'社会培训项目或模块内容，不属于本科/研究生学分课程'}.get(course['description_status'],'有可核对的公开内容，范围见来源说明')
            core.append(para('课程内容状态：'+status_label, small))
        course_story.append(KeepTogether(core))
        if course['has_description']:
            course_story.append(para('公开内容摘要与适用范围', label))
            course_story.extend(para(part) for part in course['description'].split('\n') if part)
        if course['teachers']:
            course_story.append(para('教师—课程对应的公开证据', label))
            for relation in course['teachers']:
                group = [para('课程名称：' + course['name']), para('公开对应教师：' + relation['name']),
                         para('关系原句：' + relation['relation_text'])]
                if relation.get('relation_type'):group.append(para('关系类型：'+relation['relation_type'],small))
                if relation.get('temporal_status'):group.append(para('授课时态：'+relation['temporal_status'],small))
                group.extend(provenance(relation['source_url'], relation['source_date']))
                course_story.append(KeepTogether(group))
        else:
            course_story.append(para(NO_TEACHER_RELATION, notice))
        if course['source_evidence']:
            course_story.append(para('课程资料来源', label))
            for evidence in course['source_evidence']:
                group = [para('课程名称：' + course['name']),
                         para('来源标题：' + (evidence['title'] or '未提供'), small),
                         para('证据类型：' + (evidence['kind'] or '未提供'), small)]
                group.extend(provenance(evidence['source_url'], evidence['published_at']))
                if evidence.get('page'):group.append(para('对应官方附件页码：'+evidence['page'],small))
                if evidence['excerpt']:
                    group.append(para('原文证据：' + evidence['excerpt']))
                course_story.append(KeepTogether(group))
        else:
            course_story.append(para('来源限制：本条没有独立的课程资料来源记录，不能据此宣称已取得课程简介。', notice))
        for value in course['notes']:
            course_story.append(para('来源说明：' + value, small))
        course_expected.append(course['heading'])
    if not course_story:
        course_story = introduction('学院课程公开信息与教师对应索引', 0, '课程')
    course_volumes.append((course_story, course_expected))

    def footer(canvas, document):
        canvas.setFont('STSong-Light', 8);canvas.setFillColor(colors.HexColor('#666666'))
        canvas.drawCentredString(A4[0] / 2, 22, f'学院公开网页信息汇编｜第 {document.page} 页')

    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    jobs = [(TEACHER_FILENAME, teacher_story, teacher_expected, 'teachers', None)]
    for volume_number, (story, expected) in enumerate(course_volumes, 1):
        filename = COURSE_FILENAME if len(course_volumes) == 1 else f'{Path(COURSE_FILENAME).stem}-第{volume_number:02d}卷.pdf'
        jobs.append((filename, story, expected, 'courses', volume_number))
    for filename, story, expected, kind, volume_number in jobs:
        target = output_dir / filename
        SimpleDocTemplate(str(target), pagesize=A4, leftMargin=44, rightMargin=44,
                          topMargin=38, bottomMargin=40, title=filename.removesuffix('.pdf'),
                          author='中国传媒大学计算机与网络空间安全学院公开网页资料；离线汇编',
                          allowSplitting=True).build(story, onFirstPage=footer, onLaterPages=footer)
        reader = PdfReader(target);texts = [page.extract_text() or '' for page in reader.pages]
        normalize = lambda value: re.sub(r'\s+', '', value)
        heading_pages = []
        for heading_text in expected:
            found = [i + 1 for i, text in enumerate(texts) if normalize(heading_text) in normalize(text)]
            if len(found) != 1:
                raise ValueError(f'生成后标题抽取检查失败：{heading_text}，匹配页：{found}')
            heading_pages.append({'heading': heading_text, 'page': found[0]})
        outputs.append({'kind': kind, 'path': str(target), 'pages': len(texts),
                        'volume_number': volume_number,
                        'course_count': len(expected) if kind == 'courses' else 0,
                        'characters_per_page': [len(text) for text in texts],
                        'bytes': target.stat().st_size, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                        'heading_pages': heading_pages,
                        'volume_decision_required': len(texts) > 300})
    result = {'created_at': datetime.now().astimezone().isoformat(), 'source': str(source),
              'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
              'collected_at': prepared['collected_at'], 'teacher_count': len(prepared['teachers']),
              'course_count': len(prepared['courses']),
              'course_volume_count': len(course_volumes),
              'expected_pdf_count': len(jobs),
              'courses_with_description': sum(course['has_description'] for course in prepared['courses']),
              'courses_name_only': sum(not course['has_description'] for course in prepared['courses']),
              'teacher_course_evidence_count': sum(len(teacher['course_mentions']) for teacher in prepared['teachers']),
              'course_teacher_relation_count': sum(len(course['teachers']) for course in prepared['courses']),
              'omitted_fields': prepared['omissions'], 'limitations': prepared['limitations'],
              'outputs': outputs, 'paid_model_calls': 0, 'network_calls': 0,
              'visual_qa_required': True,
              'volume_decision_required': any(output['volume_decision_required'] for output in outputs)}
    if report_path is not None:
        report_path = Path(report_path);report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('materials/ccs-official/academic-records.json'))
    parser.add_argument('--output-dir', type=Path, default=Path('materials/ccs-official/pdf'))
    parser.add_argument('--report', type=Path, default=Path('materials/ccs-official/quality/conversion-report.json'))
    args = parser.parse_args()
    print(json.dumps(build_pdfs(args.source, args.output_dir, args.report), ensure_ascii=False, indent=2))
