"""Merge source-reviewed academic facts; exact names only, no inferred teaching assignments."""
import hashlib,json,math,re
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'materials/ccs-official'
def normalize(value):return re.sub(r'\s+','',str(value or ''))
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def key(name,level):return normalize(name),{'未明确':'未注明'}.get(level,level or '未注明')
def prepare():
    profiles=json.loads((FOLDER/'teacher-records.json').read_text(encoding='utf-8'))
    course_data=json.loads((FOLDER/'course-records.json').read_text(encoding='utf-8'))
    discovery=json.loads((FOLDER/'discovery.json').read_text(encoding='utf-8'))
    assert len(profiles['teachers'])==64 and not discovery['errors']
    assert all(c['complete'] for c in discovery['columns'])
    courses=course_data['courses'];index={key(c['name'],c['level']):c for c in courses}
    if len(index)!=len(courses):raise ValueError('Reviewed course source contains duplicate name/level keys')
    states={'explicit_current':'原网页写“目前”，未核对当前学期',
            'explicit_historical':'原网页明确曾经讲授或历史授课时期',
            'unspecified':'原网页列出讲授课程，但未指定当前学期'}
    added=0;relations=0
    for teacher in profiles['teachers']:
        for mention in teacher['course_mentions']:
            for detail in mention.get('course_details',[]):
                course_key=key(detail['name'],detail['level'])
                if course_key not in index:
                    identifier=hashlib.sha256(('teacher-course:'+repr(course_key)).encode()).hexdigest()[:16]
                    course={'id':'ccs-profile-course-'+identifier,'name':detail['name'],'aliases':[],
                            'level':course_key[1],'description_status':'name_only','description':'',
                            'source_evidence':[],'teachers':[],
                            'notes':['课程名称来自教师公开讲授字段；未据名称编写课程简介。']}
                    index[course_key]=course;courses.append(course);added+=1
                course=index[course_key]
                course['teachers'].append({'name':teacher['name'],'source_url':teacher['source_url'],
                    'source_date':teacher['published_at'],'relation_text':mention['raw_text'],
                    'relation_type':'教师个人页明确讲授字段','temporal_status':states[detail['temporal_status']]})
                relations+=1
                if not any(e.get('source_url')==teacher['source_url'] for e in course['source_evidence']):
                    course['source_evidence'].append({'source_url':teacher['source_url'],'title':teacher['name']+'公开简介',
                        'published_at':teacher['published_at'],'kind':'teacher_profile_course_name',
                        'excerpt':detail['name'],'scope':states[detail['temporal_status']]})
                if detail['temporal_status']=='explicit_historical':
                    course['notes'].append(teacher['name']+'对应本课程的是曾经或历史讲授记录，不能当作当前学期任课。')
    order={'本科':0,'本科学术训练':1,'硕士':2,'研究生':3,'博士':4,'研究生学术训练':5,'预科':6,'未注明':7,'未明确':7,'课外竞赛培训':8,'社会培训':9}
    courses.sort(key=lambda c:(order.get(c['level'],7),c['name']))
    limitations=[
        '覆盖采集日学院官网具名师资、导师页链接和所列教学栏目的可访问内容，不等于当前学期完整课程表或任课表。',
        '64位人员含行政、实验实践、兼职和导师页保留人员；官网栏目归属不自动等同于正文系别或当前在岗身份。',
        '仅课程名称、教学报道片段、历史2018方案和社会培训模块分别标记，不声称收齐逐门完整课程简介。',
        '教师页的目前/曾经和公布日期按原页保留，采集日期不是网页最后更新时间，也不证明当年招生名额。',
        '2位导师页仅出现姓名而无个人简介链接，未补写其简介；外部个人站、教学Wiki和登录资源不扩爬。',
        '事实摘要为助手据公开资料整理，不能当作独立人工标注或课程教学金标准。']
    limitations.extend(course_data.get('limitations',[]))
    coverage={'官网栏目':len(discovery['columns']),'栏目分页':sum(len(c['pages']) for c in discovery['columns']),
              '正文页面':len(discovery['articles']),'师资简介':len(profiles['teachers']),
              '无独立个人页姓名':len(profiles.get('unlinked_people',[])),
              '课程及培训项目条目':len(courses),'仅名称条目':sum(not c['description'] for c in courses),
              '有有限内容的条目':sum(bool(c['description']) for c in courses),
              '教师明确讲授关系':relations,'采集日':discovery['collected_at'][:10]}
    payload={'collected_at':discovery['collected_at'],'coverage':coverage,'teachers':profiles['teachers'],
             'courses':courses,'unlinked_people':profiles.get('unlinked_people',[]),'limitations':list(dict.fromkeys(limitations))}
    (FOLDER/'academic-records.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    report={'prepared_at':datetime.now().astimezone().isoformat(),'input_hashes':{name:digest(FOLDER/name) for name in ('discovery.json','teacher-records.json','course-records.json')},
            'teacher_count':len(profiles['teachers']),'source_course_count':len(course_data['courses'])-added,
            'teacher_only_course_level_records_added':added,'course_count':len(courses),
            'course_name_count':len({c['name'] for c in courses}),'teacher_course_level_relationships':relations,
            'by_level':dict(Counter(c['level'] for c in courses)),
            'by_description_status':dict(Counter(c['description_status'] for c in courses)),
            'expected_output_pdfs':1+max(1,math.ceil(len(courses)/100)),
            'records_sha256':digest(FOLDER/'academic-records.json'),'generation_api_calls':0}
    (FOLDER/'quality').mkdir(exist_ok=True)
    (FOLDER/'quality/preparation-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':prepare()
