"""Record the assistant's source-based development review separately from human grades.

The decisions below were reviewed against the saved responses. This is not a judge-model call
or an automatic string-match accuracy scorer. It only reviews the core requested fact.
"""
import csv
import hashlib
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
FACTS={
 'proof01':'可信电子凭证系统自助办理，电子印章，彩色打印后可用。',
 'proof02':'本科生在读证明含中、英文版。',
 'proof03':'持相关要求文件至本科生院线下办理。',
 'proof04':'原则上本人办理；代办需委托书和双方身份证复印件。',
 'proof05':'教育部学籍在线验证报告中文版、北京高校非北京户籍大学生出境情况说明、本人填写且学院主管学生工作领导批准的因私短期出国（境）审批表。',
 'proof06':'毕业当年4月1日前。',
 'degree01':'通过毕业论文或毕业设计等毕业环节审查。',
 'degree02':'理工科、经管类通过重修重考取得的必修课程学分累计不超过28学分。',
 'degree03':'平均学分绩点不低于2.0。',
 'degree04':'2025年1月1日起施行，不把修订日期当施行日期。',
 'degree05':'收到结论起三个工作日内申请学术复核。',
 'transfer01':'原则上每学年一次，通常秋季学期。',
 'transfer02':'公示期5天。',
 'transfer03':'原专业已修读并通过，名称内容同或相近、难度相当，可以申请课程置换。',
 'student01':'保留入学资格期间不具有学籍。',
 'student02':'由教研室或任课教师根据课程性质确定。',
 'student03':'保留学籍至退役后两年；兵役时间不计入最长有效学习年限。',
 'transcript01':'系统左方菜单“我的订单”；已发送后查看邮箱。',
 'transcript02':'在线申请的成绩单可在学信网上在线验证。'}
FAILURES={('tfidf','degree04'),('tfidf','transfer01'),('semantic','proof04'),
          ('semantic','degree01'),('semantic','student02'),('hybrid','proof04'),
          ('hybrid','degree04'),('hybrid','transfer01')}

if __name__=='__main__':
    path=ROOT/'evaluation/results/answers-20261013-153455-corrected.json'
    report=json.loads(path.read_text(encoding='utf-8'))
    connection=sqlite3.connect(ROOT/'data/knowledge.db');connection.row_factory=sqlite3.Row
    normalize=lambda s:re.sub(r'\s+','',s or '')
    rows=[]
    for row in report['rows']:
        reference=row['reference']
        gold=[dict(r) for r in connection.execute('''SELECT c.text,c.page,c.ordinal,d.name FROM chunks c
            JOIN documents d ON d.id=c.document_id WHERE d.name=?''',(reference['document'],))
            if r['page']==reference['page'] and normalize(reference['evidence']) in normalize(r['text'])]
        if not gold:
            raise SystemExit('原文标签不匹配，停止保存初评。')
        if row['mode']=='direct':
            score=None;status='背景条件不一致，未评分'
            note='直接组遗漏学校名称，与RAG组背景不同；通用建议不作本校效果评分。'
        elif (row['mode'],row['id']) in FAILURES:
            score=0;status='未回答目标事实'
            note='目标条款存在于知识库，但这次检索未覆盖，回答无法确认。主要定位为检索失败，不因此认定模型编造事实。'
        else:
            score=2;status='核心事实与标注依据一致'
            note='助手已核对回答的核心事实。适用范围、附带建议和全部引文仍需逐条独立复核；重复要求确认年级专业影响简洁性。'
        supporting=[s['citation'] for s in row['sources'] if s['name']==reference['document']
            and s['page']==reference['page'] and normalize(reference['evidence']) in normalize(s['text'])]
        rows.append({'id':row['id'],'mode':row['mode'],'core_reference_fact':FACTS[row['id']],
            'assistant_core_score':score,'status':status,'note':note,
            'gold_document':reference['document'],'gold_page':reference['page'],
            'original_evidence_text':gold[0]['text'], 'supporting_source_numbers':supporting,
            'independent_human_reviewed':False,'human_score':None})
    connection.close()
    payload={'created_at':datetime.now().astimezone().isoformat(),'role':'assistant_initial_core_review',
        'source_report':path.name,'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'rows':rows,
        'note':'只评开发回答的核心事实，非全面条件/引用评分，非独立人工审核；不汇总成正式准确率。原始回答、人工评分栏与候选题均未改动。'}
    destination=ROOT/'evaluation/results/assistant-initial-review-20261013.json'
    destination.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    with destination.with_suffix('.csv').open('w',encoding='utf-8-sig',newline='') as file:
        writer=csv.writer(file);writer.writerow(['题号','方案','助手核心初评分','初评状态','核心参考事实','说明','独立人工复核'])
        for r in rows:writer.writerow([r['id'],r['mode'],r['assistant_core_score'],r['status'],r['core_reference_fact'],r['note'],'待复核'])
    print('Saved 76 assistant review notes separately; 57 RAG core reviews, 19 direct rows withheld for background mismatch.')
