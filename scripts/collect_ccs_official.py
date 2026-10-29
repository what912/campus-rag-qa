"""Enumerate public CCS faculty/teaching columns with recorded pagination and saved sources."""
import argparse,hashlib,json,re,time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin,urlparse
import httpx
from lxml import html

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'materials/ccs-official'
BASE='https://ccs.cuc.edu.cn/'
SEEDS=[('faculty_leaders','学院领导','xyld_6238/list.htm'),('faculty_cs','计算机科学与技术系','jsjkxyjsx/list.htm'),
       ('faculty_security','网络空间安全系','wlkjaqx/list.htm'),('faculty_lab','实验实践教学中心','sysjjxzx/list.htm'),
       ('faculty_office','学院办公室','bgs/list.htm'),('faculty_adjunct','兼职博导','jzbd/list.htm'),
       ('supervisors','导师情况','dsqk/list.htm'),('undergraduate_teaching','本科教学','bkjx/list.htm'),
       ('graduate_teaching','研究生教学','yjsjx/list.htm'),('undergraduate_majors','本科专业','bkzyjs/list.htm'),
       ('graduate_majors','研究生专业','yjszyjs/list.htm'),('practice','实践创新（含社会培训）','sjcx/list.htm'),
       ('teaching_archive','教学工作旧栏目','p5584c5559/list.psp'),
       ('news_courses','学院新闻课程线索','xyxw/list.htm'),('notice_courses','通知公告课程线索','tzgg/list.htm')]
ARTICLE=re.compile(r'/\d{4}/\d{4}/c\d+a(\d+)/page\.(?:htm|psp)$')
def digest(data):return hashlib.sha256(data).hexdigest()
def save_json(path,value):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
def text(node):
    return re.sub(r'[ \t\r\u00a0\u3000]+',' ','\n'.join(node.itertext())).strip()
def safe_url(url):
    parsed=urlparse(url)
    if parsed.scheme=='http' and parsed.hostname=='ccs.cuc.edu.cn' and not parsed.username and not parsed.password:
        parsed=parsed._replace(scheme='https');url=parsed.geturl()
    if parsed.scheme!='https' or parsed.hostname!='ccs.cuc.edu.cn' or parsed.username or parsed.password:
        raise ValueError('Only the reviewed CCS public HTTPS origin is allowed')
    return url
def fetch(url,client,cache,kind):
    url=safe_url(url)
    if url in cache:
        row=cache[url];path=ROOT/row['local_path'];body=path.read_bytes()
        if digest(body)!=row['sha256']:raise ValueError('Cached source hash changed: '+str(path))
        return body,row
    initial=url
    for _ in range(4):
        response=client.get(safe_url(url))
        if response.is_redirect:url=urljoin(url,response.headers['location']);continue
        response.raise_for_status();body=response.content
        if len(body)>20*1024*1024:raise ValueError('Public source is unexpectedly large')
        extension=Path(urlparse(url).path).suffix.lower()
        if kind!='attachment':extension='.html'
        if extension not in ('.html','.pdf','.doc','.docx','.png','.jpg','.jpeg'):extension='.bin'
        path=FOLDER/('attachments' if kind=='attachment' else 'originals')/(digest(initial.encode())[:16]+extension)
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(body)
        row={'url':initial,'resolved_url':str(response.url),'kind':kind,'content_type':response.headers.get('content-type',''),
             'local_path':path.relative_to(ROOT).as_posix(),'sha256':digest(body),'bytes':len(body),'collected_at':datetime.now().astimezone().isoformat()}
        cache[initial]=row;save_json(FOLDER/'acquisition.json',{'sources':list(cache.values())})
        return body,row
    raise ValueError('Too many redirects')
def article_links(tree,url):
    result={}
    for a in tree.xpath('//a[@href]'):
        observed=urljoin(url,a.get('href'));target=observed
        if urlparse(target).hostname=='ccs.cuc.edu.cn':target=safe_url(target)
        match=ARTICLE.search(urlparse(target).path)
        if match and urlparse(target).hostname=='ccs.cuc.edu.cn':
            title=(a.get('title') or ''.join(a.itertext())).strip()
            if title:result[match.group(1)]={'article_id':match.group(1),'title':title,'url':target,'observed_url':observed}
    return list(result.values())
def discover():
    FOLDER.mkdir(parents=True,exist_ok=True)
    acquired=FOLDER/'acquisition.json';cache={r['url']:r for r in json.loads(acquired.read_text(encoding='utf-8'))['sources']} if acquired.exists() else {}
    columns=[];articles={};errors=[]
    with httpx.Client(timeout=30,follow_redirects=False,headers={'User-Agent':'CampusRAG-GraduationProject/0.8 (public academic profiles)'}) as client:
        for id,label,relative in SEEDS:
            url=urljoin(BASE,relative);pages=[];seen=set();found={};all_found={};expected=None;direct={}
            while url and url not in seen:
                seen.add(url)
                try:body,source=fetch(url,client,cache,'column');tree=html.fromstring(body)
                except Exception as exc:errors.append({'column':id,'url':url,'error':str(exc)[:250]});break
                links=article_links(tree,url)
                for link in links:all_found[link['article_id']]=link
                if id in ('news_courses','notice_courses'):
                    links=[link for link in links if re.search(r'课程|教学|教材|培训|工作坊|专业介绍',link['title'])]
                for anchor in tree.xpath('//a[@href]'):
                    target=urljoin(url,anchor.get('href'))
                    if re.search(r'\.(?:pdf|docx?|xlsx?)(?:$|\?)',target,re.I):
                        title=(anchor.get('title') or ''.join(anchor.itertext())).strip()
                        if title:direct[target]={'title':title,'url':target,'status':'listed_not_collected','reason':'Direct legacy administration/download item, outside course/profile source selection'}
                total=tree.xpath('//*[contains(@class,"all_count")]/em/text()')
                if total and str(total[0]).strip().isdigit():expected=int(total[0])
                if id=='supervisors':
                    body_nodes=tree.xpath('//*[contains(@class,"wp_articlecontent")]')
                    if body_nodes:links=article_links(body_nodes[0],url)
                for link in links:
                    found[link['article_id']]=link
                    entry=articles.setdefault(link['article_id'],dict(link,columns=[],candidate_urls=[]))
                    if label not in entry['columns']:entry['columns'].append(label)
                    if link['url'] not in entry['candidate_urls']:entry['candidate_urls'].append(link['url'])
                    if id.startswith('faculty_'):entry['faculty']=True;entry['url']=link['url']
                pages.append({'url':url,'source_path':source['local_path'],'article_count':len(links),'sha256':source['sha256']})
                next_links=tree.xpath('//*[contains(@class,"wp_paging")]//a[contains(concat(" ",@class," ")," next ")]/@href')
                next_url=urljoin(url,next_links[0]) if next_links and not next_links[0].startswith('javascript:') else ''
                url=next_url if next_url not in seen else ''
                if len(pages)>40:raise ValueError('Unexpected pagination size; inspect before continuing')
            columns.append({'id':id,'label':label,'pages':pages,'expected_records':expected,'unique_articles':len(found),
                            'all_observed_articles':len(all_found),'title_screened_out':len(all_found)-len(found),
                            'direct_download_entries':list(direct.values()),
                            'complete':expected is None or expected==len(all_found)+len(direct),'articles':list(found.values())})
            print(label+': '+str(len(found))+' articles / '+str(len(pages))+' pages',flush=True)
        # Current homepage supplies additional course-specific news absent from the archive column.
        body,source=fetch(BASE,client,cache,'homepage')
        for link in article_links(html.fromstring(body),BASE):
            if re.search(r'课程|教学|教材',link['title']):
                entry=articles.setdefault(link['article_id'],dict(link,columns=[],candidate_urls=[]))
                entry['columns'].append('首页课程教学线索');entry['candidate_urls'].append(link['url'])
        # An observed official course-teaching case page, useful as a dated activity rather than a timetable.
        explicit=[('266879','教学案例：数据结构','https://ccs.cuc.edu.cn/2026/0202/c5526a266879/page.htm')]
        for aid,title,url in explicit:articles.setdefault(aid,{'article_id':aid,'title':title,'url':url,'columns':['课程教学线索'],'candidate_urls':[url]})
        for index,entry in enumerate(articles.values(),1):
            candidates=list(dict.fromkeys([entry['url'],*entry['candidate_urls']]))
            for url in candidates:
                try:
                    body,source=fetch(url,client,cache,'faculty' if entry.get('faculty') or '导师情况' in entry['columns'] and len(entry['title'])<=5 else 'teaching')
                    tree=html.fromstring(body);nodes=tree.xpath('//*[contains(@class,"wp_articlecontent")]')
                    if not nodes:raise ValueError('No article body; refusing navigation/error page')
                    article=nodes[0];entry.update(source=source,body_characters=len(''.join(article.itertext())),published_at='')
                    date=re.search(r'发布时间\s*[：:]?\s*(\d{4}-\d{2}-\d{2})',''.join(tree.itertext()))
                    if date:entry['published_at']=date.group(1)
                    heading=tree.xpath('//h1/text()');entry['actual_title']=' '.join(heading).strip() or entry['title']
                    attachments=[]
                    for value in article.xpath('.//@pdfsrc|.//a/@href|.//iframe/@src|.//object/@data'):
                        attachment=urljoin(url,value)
                        if urlparse(attachment).hostname=='ccs.cuc.edu.cn' and re.search(r'\.(?:pdf|docx?)(?:$|\?)',attachment,re.I):attachments.append(attachment)
                    entry['attachments']=[]
                    for attachment in dict.fromkeys(attachments):
                        try:
                            content,asset=fetch(attachment,client,cache,'attachment')
                            entry['attachments'].append(asset)
                        except Exception as exc:errors.append({'article_id':entry['article_id'],'url':attachment,'error':str(exc)[:250]})
                    # Preserve image-only academic text for explicit OCR review, never infer it from titles.
                    entry['body_images']=[urljoin(url,u) for u in article.xpath('.//img/@src')]
                    break
                except Exception as exc:
                    if url==candidates[-1]:errors.append({'article_id':entry['article_id'],'url':url,'error':str(exc)[:250]})
            if index%10==0:print('Article sources checked '+str(index)+'/'+str(len(articles)),flush=True)
        result={'collected_at':datetime.now().astimezone().isoformat(),'entry_url':BASE,'columns':columns,'articles':list(articles.values()),'errors':errors,
                'scope':'All pages of the named public faculty/academic columns and recorded course-specific homepage leads; not an unbounded full-site mirror.'}
        save_json(FOLDER/'discovery.json',result)
        print(json.dumps({'columns':len(columns),'articles':len(articles),'fetched_articles':sum('source' in a for a in articles.values()),'errors':len(errors)},ensure_ascii=False),flush=True)

if __name__=='__main__':discover()
