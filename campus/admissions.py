"""Separate admission cycles and programs from enrolled-student cohort filtering."""
import re


PROGRAMS={
    'general_undergraduate':('普通本科招生','undergraduate'),
    'art_undergraduate':('艺术类本科校考招生','undergraduate'),
    'general_master':('普通硕士研究生招生','master'),
    'mba':('工商管理专业学位硕士 MBA','master'),
    'international_1plus1':('1+1 国际硕士预科项目','master'),
    'general_doctor':('博士研究生招生','doctor'),
    'media_qihang':('高校专项传媒起航计划','undergraduate'),
}
LEVELS={'undergraduate':'本科招生','master':'硕士招生','doctor':'博士招生','training':'培训项目'}
PROGRAM_PATTERNS={
    'media_qihang':r'(?:传媒)?[起启]航(?:计划)?(?:招生)?',
    'international_1plus1':r'(?:1\s*[+＋]\s*1(?:国际)?(?:硕士)?(?:名校)?(?:预科)?(?:项目)?|国际硕士(?:名校)?(?:预科)?(?:项目)?)(?:招生)?',
    'mba':r'(?:(?<![A-Za-z])MBA(?![A-Za-z])(?:工商管理)?(?:专业学位)?(?:硕士)?|工商管理(?:专业学位硕士|专硕|硕士))(?:研究生)?(?:招生)?',
    'art_undergraduate':r'(?:艺术类(?:本科)?(?:校考)?|艺术本科|艺术校考)(?:招生)?',
    'general_undergraduate':r'(?:普通本科(?:招生)?|本科招生)',
    'general_master':r'(?:普通硕士(?:研究生)?(?:招生)?|硕士(?:研究生)?招生|研究生招生)',
    'general_doctor':r'博士(?:研究生)?(?:招生)?',
}
DEGREE_PATTERNS={
    'academic':r'学术学位|学术(?:型)?硕士|学术(?:型)?博士|学硕|学博',
    'professional':r'专业学位|专业(?:型)?硕士|专业(?:型)?博士|专硕|专博',
}


def _is_background_degree(text,match):
    prefix=re.split(r'[，,。；;！!？?\n]',text[:match.start()])[-1]
    identity=re.search(r'(?:(?:我|本人)(?:目前|现在)?(?:是|在读|就读(?:于)?)|(?:目前|现在)(?:在读|就读(?:于)?))',prefix)
    if identity and not re.search(r'想|打算|准备|咨询|了解|报考|申请',prefix[identity.end():]):
        return True
    return bool(re.match(r'(?:硕士|博士)?(?:研究生)?(?:毕业|在读|在校)',text[match.end():]))


def _requested_degree_types(text):
    """Ignore an enrolled student's stated identity when finding requested degrees."""
    return {degree for degree,pattern in DEGREE_PATTERNS.items()
            for match in re.finditer(pattern,text) if not _is_background_degree(text,match)}


def _compared_programs(text):
    remaining=text
    found=set()
    for program,pattern in PROGRAM_PATTERNS.items():
        if re.search(pattern,remaining,re.I):
            found.add(program)
            remaining=re.sub(pattern,' ',remaining,flags=re.I)
    return found


def infer_admission_scope(query,category=''):
    """Infer an admission scope without interpreting 2023级 as a 2023 admission cycle."""
    text=str(query or '').strip()
    if category!='招生信息' and re.search(r'转专业|学籍|辅修学士学位|学士学位授予',text) and not re.search(r'招生|报考|初试|复试|高考|校考|硕士|博士',text):
        return {'domain':False,'year':'','level':'','program':''}
    mba=bool(re.search(r'(?<![A-Za-z])MBA(?![A-Za-z])',text,re.I))
    qihang=bool(re.search(r'传媒[起启]航|[起启]航计划',text))
    international=bool(re.search(r'1\s*[+＋]\s*1|国际硕士|国际名校硕士|硕士名校项目',text))
    art=bool(re.search(r'校考|艺术类本科|艺术类.*本科招生|本科.*艺术类招生',text))
    undergraduate='本科' in text
    master=bool(re.search(r'硕士|学硕|专硕|考研|研招|保研|推免|推荐免试',text))
    doctor=bool(re.search(r'博士|学博|专博',text))
    business='工商管理' in text
    degree_types=_requested_degree_types(text)
    target_levels=[{'学硕':'硕士','专硕':'硕士','学博':'博士','专博':'博士'}.get(match.group(),match.group())
                   for match in re.finditer(r'硕士|博士|学硕|专硕|学博|专博',text)
                   if not _is_background_degree(text,match)]
    teaching_query=bool(re.search(r'课程|学分|学时|培养方案|教学|期末|选修|必修|教材',text))
    directory_intent=bool(re.search(r'专业目录|研究方向|所属.*学院|哪个学院|哪所学院|考试科目|考试大纲|考纲|初试科目|复试科目|考.*英语[一二12]|工作经验',text))
    implicit_admissions=bool(target_levels and not teaching_query and (degree_types or directory_intent))
    comparison=bool(re.search(r'区别|比较|对比|差异|分别|一样|不同|变化|相比|异同|相同',text))
    domain=(category=='招生信息' or bool(re.search(r'招生|报考|初试|复试|校考|录取|考研|研招|保研|推免|推荐免试',text))
            or mba or qihang or international
            or implicit_admissions
            or (len(degree_types)>1 and comparison)
            or (business and not undergraduate and not doctor and bool(re.search(r'学费|申请|报考|招生|报名',text)))
            or bool(re.search(r'(?:本科|硕士|博士|学硕|专硕|学博|专博)(?:研究生)?(?:的)?(?:条件|材料)',text))
            or ((undergraduate or master or doctor) and bool(re.search(r'报名|学费|申请|入学条件|报考条件',text))))
    if not domain:return {'domain':False,'year':'','level':'','program':''}
    # The user's current education is not necessarily the requested admission level.
    requested_levels=re.findall(r'(本科|硕士|博士|研究生|学硕|专硕|学博|专博)(?:研究生)?(?:的)?(?:招生|录取|入学|报考|申请|学费|费用|收费|条件|材料)',text)
    requested_levels=[{'学硕':'硕士','专硕':'硕士','学博':'博士','专博':'博士'}.get(level,level) for level in requested_levels]
    level_words=r'(?:本科|硕士(?:研究生)?|博士(?:研究生)?|学硕|专硕|学博|专博)'
    joined_levels=bool(re.search(level_words+r'(?:招生)?\s*(?:和|与|及|、|以及|跟)\s*'+level_words,text))
    comparison_ambiguous=comparison and (len(set(requested_levels))>1 or joined_levels or len(_compared_programs(text))>1)
    requested=requested_levels[-1] if requested_levels else (target_levels[-1] if target_levels else '')
    program=''
    if qihang:program='media_qihang'
    elif international:program='international_1plus1'
    elif mba:program='mba'
    elif art:program='art_undergraduate'
    elif requested=='博士':program='general_doctor'
    elif requested=='本科':program='general_undergraduate'
    elif business and not doctor and (master or not undergraduate):program='mba'
    elif requested in ('硕士','研究生'):program='general_master'
    elif doctor:program='general_doctor'
    elif master:program='general_master'
    elif undergraduate:program='general_undergraduate'
    # Exclude a person's cohort and explicit past enrollment year from cycle candidates.
    years=[]
    for match in re.finditer(r'(?<!\d)(20\d{2})(?!\d)',text):
        suffix=text[match.end():]
        if re.match(r'\s*(?:级|届|年\s*(?:入学|级|出生|毕业))',suffix):continue
        years.append(match.group(1))
    # An explicit year immediately attached to an admission/exam event takes precedence.
    targeted=re.search(r'(?<!\d)(20\d{2})\s*年?\s*(?:本科|硕士|博士|艺术类|研究生|招生|报考|初试|复试|校考)',text)
    year=targeted.group(1) if targeted and targeted.group(1) in years else (years[-1] if years else '')
    result={'domain':True,'year':year,'level':PROGRAMS[program][1] if program else '', 'program':program}
    if len(degree_types)==1:result['degree_type']=next(iter(degree_types))
    if comparison_ambiguous or len(degree_types)>1 or (comparison and len(set(years))>1):result.update(ambiguous=True,program='',level='')
    return result


def scope_matches(metadata,scope):
    """Hard-filter cross-program/cycle evidence before ranking, with explicit unknowns excluded."""
    metadata=metadata or {}
    if scope.get('ambiguous'):return False
    admissions=metadata.get('domain')=='admissions'
    if not scope.get('domain'):return not admissions
    if not admissions:return False
    for expected,key in (('year','admission_year'),('level','admission_level'),('program','admission_program')):
        value=scope.get(expected)
        if value and str(metadata.get(key) or '')!=str(value):return False
    degree=scope.get('degree_type')
    evidence_degree=metadata.get('degree_type')
    if degree and evidence_degree and degree!=evidence_degree:return False
    return True


def choose_admission_year(rows,scope):
    """Choose the latest known cycle only after a program or level has been identified.

    Generic '学校招生' queries remain cross-type and keep year unspecified: selecting
    one global maximum would incorrectly hide an unrelated program's available cycle.
    """
    result=dict(scope)
    if not result.get('domain') or result.get('year') or not (result.get('program') or result.get('level')):
        return result
    years=[]
    for row in rows:
        metadata=row.get('metadata') or {}
        if row.get('sample') or metadata.get('reference_only') or not scope_matches(metadata,result):continue
        year=str(metadata.get('admission_year') or '')
        if re.fullmatch(r'20\d{2}',year):years.append(year)
    if years:
        result['year']=max(years)
        result['year_selected_from_corpus']=True
    return result


def describe_scope(scope):
    if not scope.get('domain'):return '在校教学与办事资料'
    subject=PROGRAMS.get(scope.get('program'),('', ''))[0] or LEVELS.get(scope.get('level'),'招生资料，项目未指定')
    year=scope.get('year')
    text=(str(year)+'年 ' if year else '招生年份未指定 ')+subject
    if scope.get('degree_type'):text+='（'+{'academic':'学术学位','professional':'专业学位'}.get(scope['degree_type'],scope['degree_type'])+'）'
    if scope.get('year_selected_from_corpus'):text+='（按该项目库内最新已知年份）'
    return text
