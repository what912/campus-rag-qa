"""Prepare unreviewed, unused candidate questions from stored professional-source text.

This script does not perform retrieval, tune parameters, or call a model.
"""
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CURRICULUM='本科专业人才培养方案(2022版).html'
OUTLINE='中国传媒大学计算机科学与技术专业课程大纲_详细版.pdf'
# Each tuple supplies a location plus an exact source-text cue for independent review.
CASES=[
    ('major01',CURRICULUM,5,'培养方案表格中，计算机科学与技术的专业代码是什么？',
     '080901 | 计算机科学与技术','080901。'),
    ('major02',CURRICULUM,17,'2023级计算机科学与技术沿用的培养方案规定的学制是几年？',
     '四年','四年。'),
    ('major03',CURRICULUM,5,'培养方案“专业名称及代码”表中，计算机科学与技术授予什么学位？',
     '080901 | 计算机科学与技术 | Computer Science and Technology | 4 | 工学学士','表格列为工学学士。'),
    ('major04',CURRICULUM,18,'培养方案原文列出的专业基础课程毕业要求是多少学分？',
     '专业基础课程23.5学分','原文列为23.5学分；不重算毕业总学分。'),
    ('major05',CURRICULUM,18,'培养方案原文列出的专业核心课程毕业要求是多少学分？',
     '专业核心课程26学分','原文列为26学分。'),
    ('major06',CURRICULUM,18,'2022版培养方案原文列出的专业选修课程要求是多少学分？',
     '专业选修课程20学分','原文列为20学分。'),
    ('major07',CURRICULUM,18,'培养方案中毕业实习与毕业论文（设计）合计列了多少学分？',
     '毕业实习与毕业论文（设计）15学分','原文列为15学分；未分别给出两项各自学分。'),
    ('major08',CURRICULUM,31,'培养方案实践教学环节原文写的合计实践学分是多少？',
     '共计45.5学分','原文写45.5学分；该项是包含在其他课程类别中的实践学分，不另外加到总学分。'),
    ('outline01',OUTLINE,3,'数据结构课程大纲在图结构部分列了哪些主要算法？',
     '深度优先搜索、广度优先搜索、最短路径算法、最小生成树算',
     '深度优先搜索、广度优先搜索、最短路径算法、最小生成树算法等；另有图的存储方式。'),
    ('outline02',OUTLINE,3,'数据结构课程大纲列出的考核方式有哪些？',
     '课堂学习、实验实践、算法设计作业、课程项目和期末考试',
     '课堂学习、实验实践、算法设计作业、课程项目、期末考试。未列各项占比。'),
    ('outline03',OUTLINE,5,'操作系统课程的内存管理部分包括哪些内容？',
     '地址转换、分页机制、分段机制、虚拟存储技术以及页面置换算法',
     '地址转换、分页机制、分段机制、虚拟存储技术、页面置换算法。'),
    ('outline04',OUTLINE,6,'操作系统课程大纲写了哪些考核方式？',
     '通过实验报告、课程实践、课堂表现以及综合考试评价学生掌握情况',
     '实验报告、课程实践、课堂表现、综合考试。未列各项占比。'),
    ('outline05',OUTLINE,7,'计算机网络课程大纲列了哪两种网络体系结构模型？',
     'OSI模型、TCP/IP模型','OSI模型与TCP/IP模型。'),
    ('outline06',OUTLINE,8,'计算机网络课程采用哪些考核方式？',
     '采用网络实验、课程报告、平时作业和期末综合考试进行评价',
     '网络实验、课程报告、平时作业、期末综合考试。未列各项占比。'),
    ('outline07',OUTLINE,9,'数据库系统课程的事务管理部分包含哪些主题？',
     '事务特性、并发控制、锁机制和恢复技术','事务特性、并发控制、锁机制、恢复技术。'),
    ('outline08',OUTLINE,10,'数据库系统课程大纲列出的考核方式是什么？',
     '通过数据库设计项目、实验报告、课程作业和考试综合评价',
     '数据库设计项目、实验报告、课程作业、考试。未列各项占比。'),
    ('outline09',OUTLINE,11,'软件工程课程大纲在软件测试部分列出哪些测试层次？',
     '单元测试、集成测试和系统测试','单元测试、集成测试、系统测试；另包括测试方法与测试流程。'),
    ('outline10',OUTLINE,12,'软件工程课程采用哪些考核方式？',
     '采用软件项目成果、项目文档、实验实践、课堂参与和综合考核评价',
     '软件项目成果、项目文档、实验实践、课堂参与、综合考核。未列各项占比。'),
    ('outline11',OUTLINE,9,'数据库系统课程的索引技术部分列出了什么内容？',
     'B树、B+树、哈希索引以及查询优化','B树、B+树、哈希索引、查询优化。'),
    ('outline12',OUTLINE,6,'操作系统课程实验使用什么环境，主要理解什么？',
     '通过Linux环境实践理解系统调用和系统管理方法',
     '通过Linux环境实践理解系统调用和系统管理方法。'),
]


if __name__=='__main__':
    destination=ROOT/'evaluation/professional_questions.candidate.json'
    if destination.exists():
        raise SystemExit('候选题已经存在；为保留首次创建记录，不自动覆盖。')
    connection=sqlite3.connect(ROOT/'data/knowledge.db');connection.row_factory=sqlite3.Row
    cases=[]
    normalize=lambda s:re.sub(r'\s+','',s)
    for qid,document,ordinal,question,evidence,answer in CASES:
        row=connection.execute('''SELECT c.*,d.sha256,d.version FROM chunks c JOIN documents d
            ON c.document_id=d.id WHERE d.name=? AND c.ordinal=?''',(document,ordinal)).fetchone()
        if not row or normalize(evidence) not in normalize(row['text']):
            raise SystemExit(f'原文证据不匹配：{qid}；停止写入。')
        cases.append({'id':qid,'question':question,'document':document,'page':row['page'],
            'chunk_ordinal':ordinal,'heading':row['heading'],'evidence':evidence,
            'reference_answer':answer,'source_sha256':row['sha256'],'version':row['version'],
            'cohort':'2023','major':'计算机科学与技术','reviewed':False,
            'review_note':'助手据已入库原文拟题，尚未独立人工审核，也未用于运行或调参。'})
    connection.close()
    report={'created_at':datetime.now().astimezone().isoformat(),'role':'heldout_candidate',
        'status':'draft_unreviewed','school':'中国传媒大学','cases':cases,
        'note':'20道专业候选题，仅原文匹配检查；没有检索实验、模型请求或准确率。人工审核并冻结后才可决定测试用途。'}
    destination.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# 专业候选问题核对表','','20题：8道培养方案、12道课程大纲。均有入库原文依据，但尚未独立人工审核。没有用于检索调参或模型调用，不能直接作为正式测试结果。',
        '', '原有19题保持不变，本表没有重新加入被删除的无依据题。HTML按标题和片段定位，不编造页码。',
        '', '| 题号 | 问题 | 原文位置 | 参考答案 | 审核 |','|---|---|---|---|---|']
    for case in cases:
        position=(f"PDF第{case['page']}页" if case['page'] else case['heading'])+f"，片段{case['chunk_ordinal']}"
        lines.append(f"| {case['id']} | {case['question']} | {position} | {case['reference_answer']} | 待复核 |")
    lines += ['', '源文件SHA-256、原文证据与版本见evaluation/professional_questions.candidate.json。学分回答仅转述原文，不能据分类自行修改总学分；授予学位问题明确取专业表格，保留其他段落的原文笔误。',
        '', '本表是候选题，不能保证题目难度、代表性或独立性。正式用途需本人或导师核对原件，检查与开发场景的重合，再确定划分并冻结；冻结前不依据其检索结果调整参数。后续是否调用模型及次数另定。']
    (ROOT/'docs/专业候选问题核对表.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(f'Prepared {len(cases)} candidate questions; no retrieval or generation performed.')
