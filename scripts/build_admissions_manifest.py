"""Bind converted PDFs to preserved originals and audited admission scope metadata."""
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from campus.admissions import PROGRAMS


if __name__=='__main__':
    folder=ROOT/'materials/admissions'
    def record(name):
        # Fresh conversion output takes precedence; committed reports allow offline reconstruction.
        path=ROOT/'output'/name
        if not path.is_file():path=folder/('source-audit.json' if name=='admissions-source-audit.json' else 'quality/'+name)
        return json.loads(path.read_text(encoding='utf-8'))
    audit=record('admissions-source-audit.json')
    conversions=record('admissions-document-conversions.json')['documents']
    images=record('admissions-image-ocr.json')['documents']
    examined={r['filename']:r for r in audit['sources']}
    documents=[]
    def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
    for converted in conversions:
        name=converted['original_name'];checked=examined[name]
        if '1+1' in name:program='international_1plus1'
        elif '艺术类' in name:program='art_undergraduate'
        elif '初试科目' in name:program='general_master'
        elif '工商管理' in name:program='mba'
        elif '起航' in name:program='media_qihang'
        else:program='general_undergraduate'
        metadata={'domain':'admissions','admission_year':checked['admission_year'] or '',
                  'admission_level':PROGRAMS[program][1],'admission_program':program,
                  'faculty':'招生资料','cohort':'','major':'','actual_title':checked['body_title'],
                  'original_filename':name,'original_sha256':converted['original_sha256'],
                  'conversion_method':converted['conversion_method'],'authority':checked['authority_recommended'],
                  'published_at':'2026-05-28' if name.endswith('.htm') else '',
                  'review_note':checked['review_note'],'reference_only':checked['reference_only_recommended']}
        documents.append({'name':Path(converted['pdf_path']).name,'local_path':converted['pdf_path'].replace('\\','/'),
                          'original_path':converted['original_path'].replace('\\','/'),
                          'category':'招生信息','version':(metadata['admission_year']+'年' if metadata['admission_year'] else '年份未确认')+'；'+PROGRAMS[program][0],
                          'source':'用户提供原件：'+name,'metadata':metadata})
    for image in images:
        name=image['source_name'];program='general_doctor' if image['admission_year']=='2026' and '博士' in image['detected_title'] else 'general_master'
        metadata={'domain':'admissions','admission_year':image['admission_year'],'admission_level':PROGRAMS[program][1],
                  'admission_program':program,'faculty':'招生资料','cohort':'','major':'','actual_title':image['detected_title'],
                  'original_filename':name,'original_sha256':image['source_sha256'],'conversion_method':'image_pdf_with_searchable_ocr',
                  'ocr_text_layer':True,'authority':image['authority'],'published_at':image['published_at'],
                  'review_note':'原网页长截图经本地OCR生成检索层，字符、日期和金额请对照原图。'+image.get('note',''),
                  'reference_only':False}
        metadata['review_note']+='部分低置信行已由助手对照原图逐行转录，保留更正记录，不等同于独立人工审核。这里只包含网页正文，不含页面链接指向的专业目录、考试大纲等附件。'
        path=image['output_path'].replace('\\','/')
        documents.append({'name':Path(path).name,'local_path':path,'original_path':'materials/admissions/originals/'+name,
                          'category':'招生信息','version':image['admission_year']+'年；截图OCR；'+PROGRAMS[program][0],
                          'source':'用户提供官方页面截图：'+name,'metadata':metadata})
    from pypdf import PdfReader
    for document in documents:
        pdf=ROOT/document['local_path'];original=ROOT/document['original_path']
        if digest(original)!=document['metadata']['original_sha256']:raise ValueError('原件哈希不符：'+original.name)
        reader=PdfReader(pdf);page_chars=[len((page.extract_text() or '').strip()) for page in reader.pages]
        if not all(page_chars):raise ValueError('PDF存在无法提取文字的页面：'+pdf.name)
        document.update(sha256=digest(pdf),bytes=pdf.stat().st_size,pages=len(reader.pages),characters_per_page=page_chars,
                        collected_at='2026-10-26')
    payload={'prepared_at':datetime.now().astimezone().isoformat(),'documents':documents,
             'note':'全部原件保留；招生年度独立于在读年级。来源来自用户提供材料，未独立核验最新效力。三份旧版或整理稿仅参考。'}
    (folder/'manifest.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    (folder/'source-audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'pdfs':len(documents),'pages':sum(d['pages'] for d in documents),
                      'reference_only':sum(d['metadata']['reference_only'] for d in documents)},ensure_ascii=False))
