"""Convert two provided long screenshots into paginated searchable PDFs, entirely locally.

Source pixels remain visible and unchanged. OCR is an invisible auxiliary layer, not
an independently verified transcription of an official admissions policy.
"""
import argparse
import hashlib
import io
import json
import os
from datetime import datetime,timezone
from pathlib import Path

os.environ.setdefault('OMP_NUM_THREADS','2')
os.environ.setdefault('MKL_NUM_THREADS','2')
import numpy as np
import pymupdf
from PIL import Image
from rapidocr import RapidOCR

ROOT=Path(__file__).resolve().parents[1]
NAMES=(
    '中国传媒大学2026年博士学位研究生招生简章（官方发布页）.png',
    '中国传媒大学攻读硕士学位研究生招生专业目录（官方发布页）.png',
)
TITLES=(
    '中国传媒大学2026年博士学位研究生招生简章',
    '中国传媒大学2027年硕士研究生招生简章',
)
OUTPUT_NAMES=(Path(NAMES[0]).stem+'.pdf','中国传媒大学2027年硕士研究生招生简章（截图转PDF）.pdf')


def repair_to_unicode(document,font,characters):
    """Restrict ToUnicode to used glyphs and encode non-BMP characters as UTF-16 pairs."""
    mappings={font.has_glyph(ord(char)):char.encode('utf-16-be').hex().upper() for char in characters if font.has_glyph(ord(char))}
    lines=['/CIDInit /ProcSet findresource begin','12 dict begin','begincmap',
           '/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def',
           '/CMapName /CampusOCRUnicode def','/CMapType 2 def','1 begincodespacerange',
           '<0000> <FFFF>','endcodespacerange']
    ordered=sorted(mappings.items())
    for start in range(0,len(ordered),100):
        block=ordered[start:start+100];lines.append(str(len(block))+' beginbfchar')
        lines.extend(f'<{glyph:04X}> <{unicode}>' for glyph,unicode in block)
        lines.append('endbfchar')
    lines.extend(['endcmap','CMapName currentdict /CMap defineresource pop','end','end'])
    cmap=('\n'.join(lines)+'\n').encode('ascii')
    seen=set()
    for page in document:
        for entry in page.get_fonts():
            if entry[4]!='OCRText' or entry[0] in seen:continue
            seen.add(entry[0]);kind,value=document.xref_get_key(entry[0],'ToUnicode')
            assert kind=='xref'
            document.update_stream(int(value.split()[0]),cmap)


def segments(image,max_height):
    """Prefer white horizontal inter-line gaps; retain every source pixel exactly once."""
    gray=np.asarray(image.convert('L'))
    darkness=np.mean(gray<215,axis=1)
    rows=[];start=0
    while start<image.height:
        target=min(image.height,start+max_height)
        if target<image.height:
            candidates=[y for y in range(max(start+int(max_height*.7),target-110),target)
                        if darkness[y]<.0008 and darkness[y-1]<.0008]
            if candidates:
                target=candidates[-1]
            else:
                target=min(range(max(start+1,target-80),target),key=lambda y:float(darkness[y]))
        rows.append((start,target));start=target
    return rows


def convert(source,output,review,index,engine,fontpath,cached=None):
    with Image.open(source) as original:
        image=original.convert('RGB')
    width,height=pymupdf.paper_size('a4-l');margin=14;footer=16
    scale=(width-2*margin)/image.width
    ranges=[(p['source_crop'][1],p['source_crop'][3]) for p in cached['pages']] if cached else segments(image,int((height-2*margin-footer)/scale))
    document=pymupdf.open();pages=[]
    font=pymupdf.Font(fontfile=str(fontpath));used_characters=set()
    for number,(top,bottom) in enumerate(ranges,1):
        crop=image.crop((0,top,image.width,bottom))
        buffer=io.BytesIO();crop.save(buffer,format='PNG')
        page=document.new_page(width=width,height=height)
        rect=pymupdf.Rect(margin,margin,margin+image.width*scale,margin+crop.height*scale)
        page.insert_image(rect,stream=buffer.getvalue())
        if cached:
            raw_lines=[(np.asarray(line['source_quad'])-np.array([0,top]),line['text'],line['confidence'],line) for line in cached['pages'][number-1]['lines']]
        else:
            result=engine(np.asarray(crop)[:,:,::-1].copy())
            raw_lines=[(box,text,score,{}) for box,text,score in zip(result.boxes,result.txts,result.scores)]
        lines=[]
        for box,text,score,prior_line in raw_lines:
            coords=np.asarray(box,dtype=float)
            x0,y0=coords.min(axis=0);x1,y1=coords.max(axis=0)
            visual_transcription=prior_line.get('assistant_visual_transcription')
            layer_text=visual_transcription['text'] if visual_transcription else text
            anchor=pymupdf.Point(margin+x0*scale,margin+(y0+(y1-y0)*.86)*scale)
            fontsize=max(2.5,(y1-y0)*scale*.83)
            advance=font.text_length(layer_text,fontsize=fontsize)
            stretch=max(.1,(x1-x0)*scale/max(.1,advance))
            retained=float(score)>=.8 or bool(visual_transcription)
            if retained:
                page.insert_text(anchor,layer_text,fontname='OCRText',fontfile=str(fontpath),fontsize=fontsize,render_mode=3,
                                 morph=(anchor,pymupdf.Matrix(stretch,1)))
                used_characters.update(layer_text)
            line={'text':text,'confidence':round(float(score),5),
                          'text_layer_text':layer_text,
                          'text_origin':'assistant_visual_transcription' if visual_transcription else 'local_rapidocr',
                          'included_in_text_layer':retained,
                          'exclusion_reason':'' if retained else 'low_confidence_ocr_not_used_as_evidence',
                          'source_quad':[[round(float(x),2),round(float(y+top),2)] for x,y in coords],
                          'pdf_rect':[round(float(margin+x0*scale),3),round(float(margin+y0*scale),3),
                                      round(float(margin+x1*scale),3),round(float(margin+y1*scale),3)]}
            if visual_transcription:
                line['assistant_visual_transcription']=visual_transcription
            lines.append(line)
        # Keep the auxiliary conversion label visible, separate from the source pixels.
        label=f'网页截图连续分页 {number}/{len(ranges)}  OCR文本仅供检索，数字与表格请核对原图'
        page.insert_text((margin,height-9),label,fontname='OCRText',fontfile=str(fontpath),fontsize=7,color=(.35,.35,.35))
        used_characters.update(label)
        pages.append({'page':number,'source_crop':[0,top,image.width,bottom],
                      'text':'\n'.join(line['text_layer_text'] for line in lines if line['included_in_text_layer']),'lines':lines,
                      'low_confidence_lines':[line for line in lines if line['confidence']<.95]})
        print(source.name+f' page {number}/{len(ranges)}: '+str(len(lines))+' OCR lines',flush=True)
    document.set_metadata({'title':TITLES[index],'author':'用户提供的网页截图',
                           'subject':'保留原图并附加本地OCR隐藏文本层；正文年份按标题记录',
                           'creator':'Campus RAG local screenshot conversion','producer':'PyMuPDF'})
    output.parent.mkdir(parents=True,exist_ok=True)
    document.subset_fonts()
    repair_to_unicode(document,font,used_characters)
    document.save(output,garbage=4,deflate=True)
    document.close()
    with pymupdf.open(output) as check:
        text_counts=[]
        for page in check:
            text_counts.append(len(page.get_text('text')))
            page.get_pixmap(matrix=pymupdf.Matrix(2,2),alpha=False).save(review/f'image-{index+1}-page-{page.number+1}.png')
    assert ranges[0][0]==0 and ranges[-1][1]==image.height
    assert all(a[1]==b[0] for a,b in zip(ranges,ranges[1:]))
    return {'source_name':source.name,'original_filename':source.name,'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
            'source_dimensions':[image.width,image.height],'output_path':str(output.relative_to(ROOT)),
            'output_sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'pages':pages,
            'pdf_page_count':len(pages),'extracted_characters_per_page':text_counts,
            'detected_title':TITLES[index],'admission_year':'2026' if index==0 else '2027',
            'published_at':'2025-11-12' if index==0 else '2026-10-03',
            'filename_content_mismatch':index==1,
            'source_pixels_complete':True,'method':'full-width A4 landscape segments plus local OCR invisible text with embedded Unicode font',
            'text_layer_font':fontpath.name,'ocr_cache_reused':bool(cached),'low_confidence_policy':'scores below 0.8 retained in report and excluded unless an explicit assistant visual transcription of the same source coordinates is supplied; original pixels remain visible; original OCR confidence is never upgraded',
            'authority':'用户提供官方发布页截图，未独立核验网页现行状态',
            'note':'第二份原文件名含专业目录，但图中标题为2027年硕士研究生招生简章。' if index else ''}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,default=ROOT/'materials/admissions/pdf')
    parser.add_argument('--reuse-ocr',type=Path,help='Rebuild the text layer from a prior local OCR report without re-running recognition')
    parser.add_argument('--visual-transcriptions',type=Path,help='Explicit coordinate-matched assistant visual corrections; requires --reuse-ocr and preserves original OCR text and confidence')
    parser.add_argument('--font-file',type=Path,default=Path('C:/Windows/Fonts/msyh.ttc'))
    args=parser.parse_args()
    review=ROOT/'output/admissions-images-review';review.mkdir(parents=True,exist_ok=True)
    cached=json.loads(args.reuse_ocr.read_text(encoding='utf-8')) if args.reuse_ocr else None
    if args.visual_transcriptions:
        if not cached:
            parser.error('--visual-transcriptions requires --reuse-ocr')
        visual=json.loads(args.visual_transcriptions.read_text(encoding='utf-8'))
        for item in visual['items']:
            prior=next(d for d in cached['documents'] if d['source_name']==item['original_filename'])
            page=next(p for p in prior['pages'] if p['page']==item['page'])
            matches=[line for line in page['lines'] if line['source_quad']==item['source_quad']]
            assert len(matches)==1, 'Visual correction must identify exactly one source line'
            line=matches[0]
            assert line['text']==item['original_ocr_text']
            line['assistant_visual_transcription']={
                'id':item['id'],'text':item['text'],
                'review_status':item['review_status'],
                'independent_human_review':False,
                'source_report':str(args.visual_transcriptions.relative_to(ROOT)) if args.visual_transcriptions.is_absolute() else str(args.visual_transcriptions)}
    engine=None if cached else RapidOCR(params={'Global.model_root_dir':str(ROOT/'models/ocr'),
                           'EngineConfig.onnxruntime.intra_op_num_threads':2,
                           'EngineConfig.onnxruntime.inter_op_num_threads':2,
                           'Global.max_side_len':3000,'Det.limit_side_len':1800,'Det.limit_type':'max'})
    records=[]
    for index,name in enumerate(NAMES):
        prior=next((d for d in cached['documents'] if d['source_name']==name),None) if cached else None
        records.append(convert(args.source_dir/name,args.output_dir/OUTPUT_NAMES[index],review,index,engine,args.font_file,prior))
    report={'created_at':datetime.now(timezone.utc).isoformat(),'model_calls':0,'documents':records}
    (ROOT/'output/admissions-image-ocr.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'outputs':[{'file':r['output_path'],'pages':r['pdf_page_count'],
                                  'characters':r['extracted_characters_per_page']} for r in records]},ensure_ascii=False))
