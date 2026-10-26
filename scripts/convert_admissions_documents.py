"""Convert the supplied Word/HTML documents and preserve existing PDFs, offline."""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime
from html import escape
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def html_to_pdf(source,target):
    from lxml import html
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    tree=html.fromstring(source.read_bytes(),parser=html.HTMLParser(no_network=True))
    articles=tree.xpath('//*[contains(concat(" ",normalize-space(@class)," ")," wp_articlecontent ")]')
    if not articles:raise ValueError('无法定位正文，停止导出以避免混入网页导航。')
    article=articles[0]
    for node in article.xpath('.//script|.//style|.//noscript'):node.drop_tree()
    title=tree.xpath('string(//title)').strip() or source.stem
    body=ParagraphStyle('body',fontName='STSong-Light',fontSize=11,leading=18,spaceAfter=8,wordWrap='CJK')
    heading=ParagraphStyle('heading',parent=body,fontSize=13,leading=21,spaceBefore=12,spaceAfter=9)
    title_style=ParagraphStyle('title',parent=heading,fontSize=18,leading=27,alignment=TA_CENTER,spaceAfter=16)
    story=[Paragraph(escape(title),title_style)]
    for node in article.xpath('.//p|.//h1|.//h2|.//h3|.//table|.//li'):
        if node.xpath('ancestor::table'):continue
        if node.tag=='table':
            rows=[]
            for row in node.xpath('.//tr'):
                values=[Paragraph(escape(' '.join(cell.text_content().split())),body) for cell in row.xpath('./th|./td')]
                if values:rows.append(values)
            if rows:
                count=max(map(len,rows));rows=[r+['']*(count-len(r)) for r in rows]
                table=Table(rows,colWidths=[(A4[0]-104)/count]*count,repeatRows=1)
                table.setStyle(TableStyle([('GRID',(0,0),(-1,-1),.5,colors.HexColor('#D9D9D9')),
                                           ('VALIGN',(0,0),(-1,-1),'TOP'),('BOTTOMPADDING',(0,0),(-1,-1),7)]))
                story.extend([table,Spacer(1,10)])
        else:
            text=' '.join(node.text_content().split())
            if text:
                is_heading=node.tag.startswith('h') or (text.startswith('第') and len(text)<24)
                story.append(Paragraph(escape(text),heading if is_heading else body))
    def footer(canvas,document):
        canvas.setFont('STSong-Light',9);canvas.setFillColor(colors.HexColor('#666666'))
        canvas.drawCentredString(A4[0]/2,27,str(document.page))
    SimpleDocTemplate(str(target),pagesize=A4,leftMargin=52,rightMargin=52,topMargin=44,bottomMargin=44,
                      title=title,author='',allowSplitting=True).build(story,onFirstPage=footer,onLaterPages=footer)


def simple_word_to_pdf(source,target):
    """Preserve plain Word paragraphs where the office export clips punctuation."""
    from docx import Document
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import SimpleDocTemplate,Paragraph
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    body=ParagraphStyle('body',fontName='STSong-Light',fontSize=11,leading=19,spaceAfter=8,wordWrap='CJK')
    heading=ParagraphStyle('heading',parent=body,fontSize=13,leading=21,spaceBefore=9,keepWithNext=True)
    title=ParagraphStyle('title',parent=heading,fontSize=18,leading=26,spaceAfter=18)
    story=[]
    for index,p in enumerate(Document(source).paragraphs):
        text=p.text.strip()
        if text:
            style=title if not story else heading if __import__('re').match(r'^[一二三四五六七八九十]+、',text) else body
            story.append(Paragraph(escape(text),style))
    SimpleDocTemplate(str(target),pagesize=A4,leftMargin=52,rightMargin=52,topMargin=48,bottomMargin=48,
                      title=source.stem,author='').build(story)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--soffice',type=Path,default=ROOT/'.tools/LibreOfficePortable/App/libreoffice/program/soffice.com')
    args=parser.parse_args()
    folder=ROOT/'materials/admissions';originals=folder/'originals';output=folder/'pdf'
    originals.mkdir(parents=True,exist_ok=True);output.mkdir(parents=True,exist_ok=True)
    sources=sorted(p for p in args.source.iterdir() if p.is_file() and p.suffix.lower() in ('.doc','.docx','.htm','.html','.png','.pdf'))
    for source in sources:shutil.copy2(source,originals/source.name)
    words=[p for p in sources if p.suffix.lower() in ('.doc','.docx')]
    if words:
        if not args.soffice.is_file():raise SystemExit('未找到项目内的便携式文档渲染程序。')
        profile=ROOT/'output/admissions-office-profile';(profile/'user').mkdir(parents=True,exist_ok=True)
        (profile/'user/registrymodifications.xcu').write_text('''<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry"><item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item></oor:items>''',encoding='utf-8')
        result=subprocess.run([str(args.soffice),'--headless','-env:UserInstallation='+profile.as_uri(),
            '--convert-to','pdf:writer_pdf_Export','--outdir',str(output),*[str(p) for p in words]],
            capture_output=True,timeout=120,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode:raise SystemExit('Word转换失败：'+result.stderr.decode(errors='replace')[:500])
    report=[]
    for source in sources:
        suffix=source.suffix.lower();target=output/(source.stem+'.pdf')
        if suffix=='.png':continue
        if suffix=='.pdf':shutil.copy2(source,target);method='original_pdf_byte_copy'
        elif suffix in ('.htm','.html'):html_to_pdf(source,target);method='offline_html_article_typesetting'
        elif source.name=='中传工商管理专硕招生简章.docx':
            simple_word_to_pdf(source,target);method='docx_content_preserving_typesetting'
        else:method='libreoffice_pdf_export'
        if not target.is_file():raise SystemExit('未成功生成PDF：'+target.name)
        report.append({'original_name':source.name,'original_path':(originals/source.name).relative_to(ROOT).as_posix(),
                       'original_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                       'pdf_path':target.relative_to(ROOT).as_posix(),'pdf_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
                       'conversion_method':method})
    destination=ROOT/'output/admissions-document-conversions.json'
    destination.write_text(json.dumps({'converted_at':datetime.now().astimezone().isoformat(),'documents':report},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'sources_preserved':len(sources),'pdfs_completed':len(report),'report':str(destination)},ensure_ascii=False))
