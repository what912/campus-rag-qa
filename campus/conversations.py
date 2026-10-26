"""Local conversation persistence and bounded, deterministic follow-up resolution.

Previous answers are never treated as campus evidence. Retrieval always runs again.
"""
import json
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from .admissions import PROGRAM_PATTERNS


class ConversationStore:
    def __init__(self, directory):
        self.path=Path(directory)/'conversations.db'
        self.lock=threading.RLock()
        self.active=set()
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,title TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS turns (
                    id INTEGER PRIMARY KEY,session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    question TEXT NOT NULL,result TEXT NOT NULL,created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS turns_session ON turns(session_id,id);
            ''')

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db: yield db
        finally: db.close()

    def create(self):
        now=datetime.now(timezone.utc).isoformat();identifier=uuid.uuid4().hex
        with self.connect() as db:
            if db.execute('SELECT count(*) FROM sessions').fetchone()[0]>=100:
                raise ValueError('最多保存100个本机会话，请先删除不需要的记录。')
            db.execute('INSERT INTO sessions VALUES (?,?,?,?)',(identifier,'新会话',now,now))
        return identifier

    def list(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('''SELECT s.*,count(t.id) AS turn_count FROM sessions s
                LEFT JOIN turns t ON s.id=t.session_id GROUP BY s.id ORDER BY s.updated_at DESC LIMIT 100''')]

    def get(self, identifier):
        with self.connect() as db:
            row=db.execute('SELECT * FROM sessions WHERE id=?',(identifier,)).fetchone()
            if row is None: raise ValueError('会话不存在或已经删除。')
            result=dict(row)
            result['turns']=[{**dict(r),'result':json.loads(r['result'])} for r in db.execute(
                'SELECT question,result,created_at FROM turns WHERE session_id=? ORDER BY id',(identifier,))]
            return result

    @contextmanager
    def answering(self, identifier):
        with self.lock:
            self.get(identifier)
            if identifier in self.active: raise ValueError('该会话正在回答，请等待当前请求完成。')
            self.active.add(identifier)
        try: yield
        finally:
            with self.lock: self.active.discard(identifier)

    def append(self, identifier, question, result):
        now=datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            if db.execute('SELECT count(*) FROM turns WHERE session_id=?',(identifier,)).fetchone()[0]>=100:
                raise ValueError('单个会话最多100轮，请新建会话。')
            db.execute('INSERT INTO turns(session_id,question,result,created_at) VALUES (?,?,?,?)',
                       (identifier,question,json.dumps(result,ensure_ascii=False),now))
            db.execute('UPDATE sessions SET title=CASE WHEN title=? THEN ? ELSE title END,updated_at=? WHERE id=?',
                       ('新会话',question[:40],now,identifier))

    def delete(self, identifier):
        with self.lock:
            if identifier in self.active: raise ValueError('该会话正在回答，暂时不能删除。')
            with self.connect() as db:
                return bool(db.execute('DELETE FROM sessions WHERE id=?',(identifier,)).rowcount)


TOPICS=('计算机科学与技术','网络空间安全','辅修学士学位','学士学位','在读证明','在学证明','成绩单','转专业','保留入学资格','学籍',
        '奖学金','请假','毕业','数据结构','操作系统','计算机网络','计算机组成原理','程序设计','软件工程',
        '艺术类本科招生','艺术类校考','本科招生','硕士招生','硕士研究生招生','博士招生','博士研究生招生','MBA','工商管理专硕','1+1国际硕士','国际硕士','传媒起航计划')
FACETS=(('学分',r'学分|修满多少'),('学时',r'学时'),('考核方式',r'考核|考查|考试|成绩|占比'),
        ('申请材料',r'材料|委托|代办'),('办理流程',r'办理|申请|流程'))
ADMISSION_SUBJECTS={
    'general_undergraduate':'普通本科招生','art_undergraduate':'艺术类本科校考招生',
    'general_master':'普通硕士招生','mba':'MBA招生','international_1plus1':'1+1国际硕士项目招生',
    'general_doctor':'博士招生','media_qihang':'传媒起航计划招生',
}


def _admission_project_followup(question,last):
    """Replace an explicit new admission subject without carrying over the old project."""
    scope=last['result'].get('admission_scope',{})
    if not scope.get('domain'):return None
    match=re.fullmatch(r'(?:那|那么)?[，,\s]*(?:(20\d{2})\s*年?\s*)?(.+?)呢[？?]?',question)
    if not match:return None
    target=match.group(2).strip()
    program=next((key for key,pattern in PROGRAM_PATTERNS.items() if re.fullmatch(pattern,target,re.I)),None)
    if program is None:
        program={'本科':'general_undergraduate','硕士':'general_master','普通硕士':'general_master',
                 '普通本科':'general_undergraduate','博士':'general_doctor','艺术类':'art_undergraduate'}.get(target)
    if not program:return None
    old=last['result'].get('resolved_question') or last['question']
    old_pattern=PROGRAM_PATTERNS.get(scope.get('program'),'')
    matches=list(re.finditer(old_pattern,old,re.I)) if old_pattern else []
    if not matches and scope.get('program') in ('general_undergraduate','general_master'):
        bare='本科' if scope['program']=='general_undergraduate' else '硕士(?:研究生)?'
        matches=list(re.finditer(bare+r'(?=\s*(?:学费|报名|报考|申请|录取|入学))',old))
    if not matches:
        return {'query':question,'rewritten':False,'clarification':'请说明希望了解该招生项目的哪方面，例如招生对象、报名条件、费用或考试内容。','context':None}
    targets=[m for m in matches if '招生' in m.group()]
    selected=(targets or matches)[-1]
    query=old[:selected.start()]+ADMISSION_SUBJECTS[program]+old[selected.end():]
    query=query.replace('招生招生','招生')
    if match.group(1):
        pattern=r'20\d{2}(?!\s*(?:级|届|年\s*(?:入学|级|出生|毕业)))\s*年?'
        query=re.sub(pattern,match.group(1)+'年',query,count=1) if re.search(pattern,query) else match.group(1)+'年'+query
    return {'query':query,'rewritten':query!=question,'clarification':'','context':last['result']}


def resolve_question(question, turns, topic_terms=()):
    """Resolve common explicit ellipsis; ask for clarification when the referent is unclear."""
    question=question.strip()
    last=turns[-1] if turns else None
    if last and last['result'].get('admission_scope',{}).get('domain'):
        year=re.fullmatch(r'(?:那|那么)?\s*(20\d{2})\s*年?呢[？?]?',question)
        if year:
            old=last['result'].get('resolved_question') or last['question']
            cycle_pattern=r'20\d{2}(?!\s*(?:级|届|年\s*(?:入学|级|出生|毕业)))\s*年?'
            query=re.sub(cycle_pattern,year.group(1)+'年',old,count=1) if re.search(cycle_pattern,old) else year.group(1)+'年'+old
            return {'query':query,'rewritten':True,'clarification':'','context':last['result']}
        switched=_admission_project_followup(question,last)
        if switched:return switched
    previous=turns[-1] if turns and turns[-1]['result'].get('sources') and turns[-1]['result'].get('mode') in ('rag','extractive') else None
    topics=sorted(set(TOPICS)|{s for s in topic_terms if 2<=len(s)<=30},key=len,reverse=True)
    def topic(text):
        matches=[s for s in topics if s in text]
        courses=[s for s in matches if s+'课程' in text or s+'课' in text]
        specific=[s for s in matches if s not in ('计算机科学与技术','网络空间安全','软件工程')]
        return next(iter(courses or specific or matches),'')
    explicit=topic(question)
    followup=bool(re.search(r'^(那|那么|它|这个|这门|该|上述|刚才|还有)|(?:呢|多少|怎么|怎样|如何|哪些|什么|多久)[？?]?$',question))
    incomplete=bool(re.search(r'它|这个|这门课|该课程|该专业|那门课|上述|刚才',question))
    bare_facet=not explicit and len(question)<=26 and bool(re.match(
        r'^(那|那么|还有)?[，, ]*(学分|学时|考核|考试|需要|要带|怎么办|怎么申请|如何申请|办理|申请材料)',question))
    if not previous:
        clarify='请说明具体课程、专业或办理事项，以便查找对应原文。' if incomplete or bare_facet else ''
        return {'query':question,'rewritten':False,'clarification':clarify,'context':None}
    result=previous['result'];old=result.get('resolved_question') or previous['question']
    old_topic=topic(old)
    if not explicit and (incomplete or bare_facet or re.search(r'^(那|那么|还有)',question)):
        if not old_topic:
            return {'query':question,'rewritten':False,'clarification':'这次追问指哪门课程或哪项办理事项？请补充名称。','context':None}
        query=re.sub(r'这门课|该课程|那门课|该专业|这个|它|上述事项|刚才的事项',old_topic,question)
        if old_topic not in query: query=old_topic+'：'+query
        if re.fullmatch(r'(那|那么|还有)?[：:，, ]*(呢|这个呢)?[？?]?',question):
            return {'query':question,'rewritten':False,'clarification':'请说明想继续了解的内容，例如学分、考核方式或办理材料。','context':None}
        return {'query':query,'rewritten':query!=question,'clarification':'','context':result}
    if explicit and followup and re.fullmatch(r'(那|那么)?[，, ]*'+re.escape(explicit)+r'(课程|专业)?呢[？?]?',question):
        facet=next((label for label,pattern in FACETS if re.search(pattern,old)),'')
        if not facet:
            return {'query':question,'rewritten':False,'clarification':'请说明想了解该课程或专业的哪方面内容。','context':None}
        is_major='专业' in question or ('专业' in old and '课程' not in old)
        context={**result,'major':explicit} if is_major else result
        return {'query':explicit+('专业' if is_major else '')+'的'+facet+'是什么？','rewritten':True,'clarification':'','context':context}
    return {'query':question,'rewritten':False,'clarification':'','context':None}
