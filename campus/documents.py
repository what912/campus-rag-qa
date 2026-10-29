"""Document extraction and heading/page-aware chunking."""
import io
import re
from pathlib import Path

SUPPORTED = {'.txt', '.md', '.pdf', '.docx', '.html', '.htm'}
MAX_BYTES = 10 * 1024 * 1024
MAX_CHARS = 500_000


def parse_document(filename: str, content: bytes, enable_ocr=False) -> list[dict]:
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED:
        raise ValueError('支持 TXT、Markdown、PDF、DOCX 和 HTML 文件。')
    if not content or len(content) > MAX_BYTES:
        raise ValueError('文件不能为空，大小不能超过 10 MB。')
    if suffix == '.pdf':
        from pypdf import PdfReader
        try:
            reader = PdfReader(io.BytesIO(content))
            if reader.is_encrypted and not reader.decrypt(''):
                raise ValueError('请先移除 PDF 密码再上传。')
            if len(reader.pages) > 300:
                raise ValueError('单份 PDF 最多支持 300 页。')
            pages = [{'page': i + 1, 'text': page.extract_text() or ''}
                     for i, page in enumerate(reader.pages)]
            if any('\x00' in p['text'] for p in pages):
                try:
                    import pymupdf
                    with pymupdf.open(stream=content, filetype='pdf') as alternative:
                        pages = [{'page':i+1,'text':page.get_text('text')} for i,page in enumerate(alternative)]
                except ImportError as exc:
                    raise ValueError('PDF 字体编码异常，请安装 PyMuPDF 或将文件导出为可复制文字的格式。') from exc
            if enable_ocr:
                sparse_pages = [p for p in pages if len(p['text'].strip()) < 20]
                if len(sparse_pages) > 20:
                    raise ValueError('自动 OCR 每份最多处理 20 个扫描页，请拆分文档。')
                if sparse_pages:
                    from .ocr import extract_scanned_pages
                    recognized = extract_scanned_pages(content, [p['page'] for p in sparse_pages])
                    for page in pages:
                        if recognized.get(page['page']):
                            page['text'] = recognized[page['page']]
                            page['extraction_method'] = 'ocr'
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError('PDF 解析失败，请确认文件完整或转为 TXT。') from exc
        sections, heading = [], ''
        course_heading = re.compile(r'^\s*([\u4e00-\u9fff]{2,40}[（(][A-Za-z][^\n()（）]{1,100}[)）])\s*$', re.M)
        directory_heading=re.compile(r'^[ \t]*(目录记录\s+\d+[｜|][^\n]+)[ \t]*$',re.M)
        academic_heading=re.compile(r'^[ \t]*((?:教师档案|课程条目)\s+\d+[｜|][^\n]+)[ \t]*$',re.M)
        for page in pages:
            # Omit our own conversion footer; it carries no campus policy evidence.
            page={**page,'text':re.sub(r'(?m)^[ \t]*(?:官网网页正文排版版|学院公开网页信息汇编)[｜|]第\s*\d+\s*页[ \t]*\n?','',page['text'])}
            start = 0
            matches=sorted([*course_heading.finditer(page['text']),*directory_heading.finditer(page['text']),*academic_heading.finditer(page['text'])],key=lambda match:match.start())
            for match in matches:
                before = page['text'][start:match.start()].strip()
                if before:
                    sections.append({**page, 'text':('# ' + heading + '\n' if heading else '') + before})
                heading = match.group(1)
                start = match.end()
            remainder = page['text'][start:].strip()
            if remainder:
                sections.append({**page, 'text':('# ' + heading + '\n' if heading else '') + remainder})
        pages = sections
    elif suffix in ('.html', '.htm'):
        from lxml import html
        try:
            doc = html.fromstring(content.decode('utf-8-sig'), parser=html.HTMLParser(no_network=True))
            for unsafe in doc.xpath('//script|//style|//noscript|//template'):
                unsafe.drop_tree()
            articles = doc.xpath('//*[contains(concat(" ",normalize-space(@class)," ")," wp_articlecontent ")]')
            article = articles[0] if articles else doc
            parts = []
            for block in article.xpath('.//p|.//table|.//h1|.//h2|.//h3|.//li'):
                if block.xpath('ancestor::table'):
                    continue
                if block.tag == 'table':
                    for row in block.xpath('.//tr'):
                        cells = [' '.join(cell.text_content().split()) for cell in row.xpath('./th|./td')]
                        if any(cells):
                            parts.append(' | '.join(cells))
                else:
                    value = ' '.join(block.text_content().split())
                    if value:
                        if block.tag.startswith('h') or re.match(r'^[一二三四五六七八九十]+[、．.]', value) or value.endswith('专业人才培养方案'):
                            value = '# ' + value
                        parts.append(value)
            if not parts:
                parts = [' '.join(article.text_content().split())]
            pages = [{'page':None, 'text':'\n\n'.join(parts), 'contains_images':bool(article.xpath('.//img'))}]
        except (UnicodeError, ValueError) as exc:
            raise ValueError('HTML 正文解析失败，请使用 UTF-8 保存的网页或导出文字文件。') from exc
    elif suffix == '.docx':
        from docx import Document
        try:
            doc = Document(io.BytesIO(content))
            # Preserve paragraph/table order; DOCX has no reliable physical page numbers.
            parts = []
            for block in doc.iter_inner_content():
                if hasattr(block, 'rows'):
                    parts.append('\n'.join(' | '.join(cell.text for cell in row.cells)
                                           for row in block.rows))
                else:
                    parts.append(block.text)
            pages = [{'page': None, 'text': '\n'.join(parts)}]
        except Exception as exc:
            raise ValueError('DOCX 解析失败，请确认文件完整。') from exc
    else:
        text = None
        for encoding in ('utf-8-sig', 'gb18030'):
            try:
                text = content.decode(encoding)
                break
            except UnicodeDecodeError:
                pass
        if text is None or '\x00' in text:
            raise ValueError('文本编码无法识别，请另存为 UTF-8 的 TXT 文件。')
        pages = [{'page': None, 'text': text}]
    total = sum(len(p['text']) for p in pages)
    if total > MAX_CHARS:
        raise ValueError('提取文本超过 50 万字，请拆分文件。')
    if not any(p['text'].strip() for p in pages):
        raise ValueError('未提取到文字。扫描 PDF 请开启本地 OCR；纯图片网页请先提取图片文字。')
    return pages


def chunk_pages(pages: list[dict], size=400, overlap=60) -> list[dict]:
    """Do not cross PDF pages or Markdown headings. Sizes are characters, not tokens."""
    if not 0 <= overlap < size:
        raise ValueError('片段重叠长度必须小于片段长度。')
    chunks = []
    for page in pages:
        text = page['text'].replace('\r\n', '\n').replace('\r', '\n')
        text = re.sub(r'[ \t]+', ' ', text).strip()
        sections = re.split(r'(?m)(?=^#{1,6}\s)', text)
        pending_heading = ''
        for section in sections:
            section = section.strip()
            if not section:
                continue
            if re.fullmatch(r'#{1,6}\s+[^\n]+', section):
                pending_heading += section + '\n'
                continue
            section = pending_heading + section
            pending_heading = ''
            first_line = section.split('\n', 1)[0]
            heading = first_line.lstrip('# ').strip() if first_line.startswith('#') else ''
            directory_prefix=''
            if heading.startswith(('目录记录 ','教师档案 ','课程条目 ')):
                directory_prefix='# '+heading+'\n'
                section=section.partition('\n')[2]
            budget=max(1,size-len(directory_prefix))
            start = 0
            while start < len(section):
                end = min(start + budget, len(section))
                if end < len(section):
                    # Prefer a paragraph or sentence boundary in the latter half.
                    boundaries = [section.rfind(c, start + budget // 2, end)
                                  for c in ('\n', '。', '；', '！', '？')]
                    boundary = max(boundaries)
                    if boundary >= 0:
                        end = boundary + 1
                body = section[start:end].strip()
                if body:
                    chunks.append({'text': directory_prefix+body, 'page': page['page'], 'heading': heading,
                                   'ordinal': len(chunks) + 1})
                if end == len(section):
                    break
                start = max(start + 1, end - min(overlap,max(0,budget-1)))
        if pending_heading.strip():
            chunks.append({'text': pending_heading.strip(), 'page': page['page'],
                           'heading': pending_heading.strip().lstrip('# '), 'ordinal': len(chunks) + 1})
    return chunks
