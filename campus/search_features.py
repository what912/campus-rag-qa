"""Deterministic query features for optional hybrid reranking, without answer rules."""
import re

RERANK_VERSION='focus-rrf-v1'
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
    for term in sorted(FILLERS,key=len,reverse=True):
        text=text.replace(term,'')
    expansions=[words for pattern,words in SYNONYMS if re.search(pattern,question)]
    text=re.sub(r'\s+',' ',text).strip()
    return ' '.join([text or question,*expansions])


def intent_match(question,text):
    """Match requested information types, without selecting a value or an answer."""
    facets=(
        (r'委托|代办|别人办理',r'委托|代办'),
        (r'施行|生效|开始实施',r'施行|生效'),
        (r'学期.*组织|组织.*学期',r'春季|秋季|春学期|秋学期'),
    )
    patterns=[target for question_pattern,target in facets if re.search(question_pattern,question)]
    return sum(bool(re.search(pattern,text)) for pattern in patterns)/len(patterns) if patterns else 0
