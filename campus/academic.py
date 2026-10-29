"""Route public course/profile questions without treating a profile as an admission policy."""
import re


def infer_academic_category(question,teacher_names=()):
    text=str(question or '')
    # A current recruitment quota or dated admission rule requires the admission corpus.
    annual_admission=bool(re.search(r'20\d{2}\s*年',text) and re.search(r'招生|报考|录取|名额',text))
    if annual_admission:return ''
    if re.search(r'谁(?:来|在|负责)?(?:教|讲)|(?:授课|任课|主讲)(?:教师|老师).*(?:谁|哪些)|(?:谁|哪些|哪位)(?:老师|教师|教授).*(?:教|讲|授)',text):
        return '课程简介'
    if re.search(r'老师|教师|教授|师资|导师简介|导师介绍|个人简介',text):
        return '师资信息'
    if any(name and name in text for name in teacher_names):return '师资信息'
    if re.search(r'课程简介|课程介绍|课程内容|(?:这门课|课程).*(?:学什么|讲什么|介绍)',text):
        return '课程简介'
    return ''


def academic_subject(heading):
    match=re.match(r'(教师档案|课程条目)\s+\d+[｜|](.+)',str(heading or ''))
    return (match.group(1),match.group(2).strip()) if match else ('','')


def requested_teacher(query,subjects):
    """Use exact catalogue names; no fuzzy person-name matching."""
    named={name for name in subjects if name and name in query}
    if named:return named
    # A clearly named but absent teacher is not answered with someone else's biography.
    cleaned=re.sub(r'^(?:请问|请介绍|介绍一下|介绍)\s*','',query)
    explicit=re.match(r'^([\u4e00-\u9fff]{2,4})(?:老师|副教授|教授|教师)(?:的)?(?:简介|介绍|研究|科研|讲授|主讲|教)',cleaned)
    if explicit and explicit.group(1) not in {'学院','学校','哪些','所有','本院','这位','那位'}:
        return {explicit.group(1)}
    return set()
