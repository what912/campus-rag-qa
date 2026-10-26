"""Deterministic query features for optional hybrid reranking, without answer rules."""
import re
from .admissions import infer_admission_scope

RERANK_VERSION='focus-rrf-v2-admissions'
# General question wording and document topics are separated from the requested detail.
FILLERS=('中国传媒大学','计算机科学与技术','学校','培养方案','课程大纲','学士学位','在读证明',
         '转专业','本科生','学生','专业','课程','一般','可以','是否','能否','应该','有哪些',
         '是什么','是多少','什么时候','哪个','哪里','怎么','如何','从','的','吗','呢','？','?')
SYNONYMS=(
    (r'委托|代办|别人办理', '委托 代办'),
    (r'施行|生效|开始实施', '施行 生效 实施'),
    (r'学期.*组织|组织.*学期', '学期 春季 秋季'),
    (r'公示.*多久|公示.*几天', '公示 期限 天'),
    (r'入伍|服.*兵役', '入伍 兵役'),
    (r'中英文|英文版', '中文 英文'),
)


def focus_query(question):
    text=re.sub(r'20\d{2}\s*(?:级|年|版)', '', question)
    if infer_admission_scope(question)['domain']:
        for term in ('招生简章','招生章程','艺术类本科','国际硕士','名校项目','预科项目','普通本科','研究生','1+1','1＋1','硕士','博士','本科','招生','每人','分别','主要'):
            text=text.replace(term,'')
    for term in sorted(FILLERS,key=len,reverse=True):
        text=text.replace(term,'')
    expansions=[words for pattern,words in SYNONYMS if re.search(pattern,question)]
    if infer_admission_scope(question)['domain'] and re.search(r'初试.*(?:考什么|内容|主要考)',question):
        expansions.append('初试 考试 内容')
    text=re.sub(r'\s+',' ',text).strip()
    return ' '.join([text or question,*expansions])


def intent_match(question,text):
    """Match requested information types, without selecting a value or an answer."""
    # PDF line wrapping can split a clause label, e.g. “专业\n级差”.
    text=re.sub(r'\s+','',text)
    facets=(
        (r'委托|代办|别人办理',r'委托|代办'),
        (r'施行|生效|开始实施',r'施行|生效'),
        (r'学期.*组织|组织.*学期',r'春季|秋季|春学期|秋学期'),
        (r'招生对象|招收对象|面向哪些',r'招生对象|招收对象|申请对象'),
        (r'专业级差',r'专业级差'),
        (r'初试.*(?:考什么|内容|主要考)',r'初试考试内容|文化素养基础测试'),
        (r'报名费',r'报名费'),
        (r'学费',r'学费'),
        (r'报考条件|报名条件|申请条件',r'报考条件|报名条件|申请条件'),
    )
    patterns=[target for question_pattern,target in facets if re.search(question_pattern,question)]
    return sum(bool(re.search(pattern,text)) for pattern in patterns)/len(patterns) if patterns else 0
