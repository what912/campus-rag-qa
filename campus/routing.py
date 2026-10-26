"""Shared category routing for the app and offline experiments."""
import re
from .admissions import infer_admission_scope


def infer_category(question, explicit=''):
    if explicit:
        return explicit
    if infer_admission_scope(question)['domain']:
        return '招生信息'
    if re.search(r'(每门|各门|所有).*课程|课程总成绩',question) and re.search(r'由谁|谁.*确定|比例',question):
        return '办事流程'
    if re.search(r'毕业.*学分|学分.*毕业|专业选修.*学分|必修课有哪些',question):
        return '培养方案'
    if re.search(r'(课程|这门课).*(考核|成绩.*比例|平时.*占|先修)|数据结构.*占比',question):
        return '课程大纲'
    return ''
