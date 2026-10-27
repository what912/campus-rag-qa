"""Fetch a bounded, reviewed set of public CUC sources and build a provenance-bound PDF corpus."""
import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin,urlparse

import httpx

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'materials/yz-official'


def digest(data):return hashlib.sha256(data).hexdigest()


def safe_fetch(url,client):
    for _ in range(4):
        parsed=urlparse(url)
        if parsed.scheme!='https' or parsed.hostname!='yz.cuc.edu.cn' or parsed.username or parsed.password:
            raise ValueError('Only the reviewed public yz.cuc.edu.cn HTTPS host is allowed')
        with client.stream('GET',url) as response:
            if response.is_redirect:
                url=urljoin(url,response.headers['location']);continue
            response.raise_for_status()
            blocks=[];size=0
            for chunk in response.iter_bytes():
                size+=len(chunk)
                if size>10*1024*1024:raise ValueError('Source exceeds the application 10 MB limit')
                blocks.append(chunk)
            return b''.join(blocks),str(response.url),response.headers.get('content-type','')
    raise ValueError('Too many redirects')


def collect(build=False,download=True):
    config=json.loads((FOLDER/'sources.json').read_text(encoding='utf-8'))
    originals=FOLDER/'originals';originals.mkdir(parents=True,exist_ok=True)
    acquisition_path=FOLDER/'source-acquisition.json'
    acquired=json.loads(acquisition_path.read_text(encoding='utf-8')) if acquisition_path.is_file() else {'sources':[]}
    previous={row['id']:row for row in acquired['sources']};records=[]
    with httpx.Client(timeout=30,follow_redirects=False,headers={'User-Agent':'CampusRAG-GraduationProject/0.7 (public academic documents)'}) as client:
        for item in config['sources']:
            suffix='.pdf' if item['kind']=='pdf' else '.html'
            source=originals/(item['id']+'_'+item['title']+suffix)
            if source.is_file():
                content=source.read_bytes()
                if item['id'] not in previous or previous[item['id']]['sha256']!=digest(content):
                    raise ValueError('Cached source has no matching acquisition record: '+source.name)
                record=previous[item['id']]
            else:
                if not download:raise ValueError('Missing source: '+source.name)
                content,url,content_type=safe_fetch(item.get('download_url') or item['source_url'],client)
                if item['kind']=='pdf':
                    if not content.lstrip().startswith(b'%PDF-'):raise ValueError('Attachment is not a PDF')
                elif b'wp_articlecontent' not in content:
                    raise ValueError('Article body is missing; refusing to save a navigation/error page')
                source.write_bytes(content)
                record={'id':item['id'],'requested_url':item.get('download_url') or item['source_url'],
                        'resolved_url':url,'source_page':item['source_url'],'content_type':content_type,
                        'local_path':source.relative_to(ROOT).as_posix(),'sha256':digest(content),
                        'bytes':len(content),'collected_at':datetime.now().astimezone().isoformat()}
                # Persist each successful retrieval so interrupted work resumes without extra network calls.
                previous[item['id']]=record
                acquisition_path.write_text(json.dumps({'sources':list(previous.values()),'evidence_pages':acquired.get('evidence_pages',[])},ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
            records.append((item,source,record))
            print(item['id']+' source verified: '+str(len(content))+' bytes',flush=True)
        evidence={row['url']:row for row in acquired.get('evidence_pages',[])}
        for proof in config.get('discovery_pages',[]):
            path=FOLDER/'discovery'/proof['filename'];path.parent.mkdir(parents=True,exist_ok=True)
            if path.is_file():
                if proof['url'] not in evidence or digest(path.read_bytes())!=evidence[proof['url']]['sha256']:
                    raise ValueError('Evidence page changed: '+path.name)
            else:
                if not download:raise ValueError('Missing source-link evidence page: '+path.name)
                body,url,content_type=safe_fetch(proof['url'],client)
                if b'wp_articlecontent' not in body:raise ValueError('Evidence article body missing')
                path.write_bytes(body)
                evidence[proof['url']]={'url':proof['url'],'resolved_url':url,'local_path':path.relative_to(ROOT).as_posix(),
                                       'sha256':digest(body),'collected_at':datetime.now().astimezone().isoformat()}
        acquired={'sources':list(previous.values()),'evidence_pages':list(evidence.values())}
        acquisition_path.write_text(json.dumps(acquired,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    # Bind attachment URLs to the saved official index or prospectus, not just a claimed parent URL.
    from lxml import html
    parent_pages={item['source_url']:source for item,source,record in records if item['kind']!='pdf'}
    parent_pages.update({row['url']:ROOT/row['local_path'] for row in acquired['evidence_pages']})
    parent_hashes={}
    for item,source,record in records:
        if item['kind']!='pdf':continue
        parent=parent_pages[item['source_url']]
        tree=html.fromstring(parent.read_bytes())
        links={urljoin(item['source_url'],value) for value in tree.xpath('//a/@href')}
        if item['download_url'] not in links:raise ValueError('Attachment not linked by captured official parent: '+item['id'])
        parent_hashes[item['id']]=digest(parent.read_bytes())
    if not build:return
    from pypdf import PdfReader
    from export_yz_pages import export_page
    pdfs=FOLDER/'pdf';pdfs.mkdir(parents=True,exist_ok=True)
    quality=FOLDER/'quality';quality.mkdir(parents=True,exist_ok=True)
    documents=[];conversions=[]
    for item,source,record in records:
        target=pdfs/(item['title']+'.pdf')
        if item['kind']=='pdf':
            shutil.copy2(source,target)
            conversion={'method':'official_pdf_byte_copy','source_unchanged':True}
        else:
            conversion=export_page(source,target,item['source_url'],item['published_at'],item['kind'])
        reader=PdfReader(target);page_chars=[len((p.extract_text() or '').strip()) for p in reader.pages]
        if not all(page_chars) or len(reader.pages)>300:raise ValueError('Unsearchable page or too many pages: '+target.name)
        metadata={'domain':'admissions','admission_year':item['admission_year'],
                  'admission_level':item['admission_level'],'admission_program':item['admission_program'],
                  'faculty':item.get('faculty','招生处'),'major':'','cohort':'','actual_title':item['title'],
                  'authority':'中国传媒大学研究生招生网官方公开页面或所链接的PDF原件；已核对采集来源',
                  'published_at':item['published_at'],'original_filename':source.name,
                  'original_sha256':record['sha256'],'conversion_method':conversion.get('method','official_html_'+item['kind']+'_typesetting'),
                  'review_note':item['note'],'reference_only':False}
        if item.get('degree_type'):metadata['degree_type']=item['degree_type']
        if item.get('exam_codes'):metadata['exam_codes']=item['exam_codes']
        documents.append({'id':item['id'],'name':target.name,'local_path':target.relative_to(ROOT).as_posix(),
                          'original_path':source.relative_to(ROOT).as_posix(),'sha256':digest(target.read_bytes()),
                          'original_sha256':record['sha256'],'bytes':target.stat().st_size,'pages':len(reader.pages),
                          'characters_per_page':page_chars,'category':'招生信息','version':item['admission_year']+'年；官方来源',
                          'source_page':item['source_url'],'download_url':item.get('download_url',''),
                          'source_page_sha256':parent_hashes.get(item['id'],record['sha256']),
                          'collected_at':record['collected_at'],'metadata':metadata})
        conversions.append({'id':item['id'],'pdf':target.relative_to(ROOT).as_posix(),**conversion})
        print(item['id']+' PDF ready: '+str(len(reader.pages))+' pages',flush=True)
    payload={'prepared_at':datetime.now().astimezone().isoformat(),'entry_url':config['entry_url'],
             'documents':documents,'reference_overrides':config['reference_overrides'],
             'limitations':config['limitations'],'note':'Curated public policy corpus, not a complete mirror of the website. No generation API calls.'}
    (FOLDER/'manifest.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    (quality/'conversion-report.json').write_text(json.dumps(conversions,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    print(json.dumps({'pdfs':len(documents),'pages':sum(d['pages'] for d in documents),'generation_calls':0},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--download-only',action='store_true')
    mode.add_argument('--build-only',action='store_true')
    args=parser.parse_args()
    collect(build=not args.download_only,download=not args.build_only)
