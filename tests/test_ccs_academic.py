import tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from fastapi.testclient import TestClient
from app import create_app
from campus.documents import parse_document,chunk_pages
from campus.routing import infer_category


class AcademicCorpusTests(unittest.TestCase):
    def test_academic_routing_preserves_admissions_and_course_outline_questions(self):
        self.assertEqual(infer_category('数据结构由哪些老师讲授？'),'课程简介')
        self.assertEqual(infer_category('黄玮老师的研究方向是什么？'),'师资信息')
        self.assertEqual(infer_category('黄玮博士的研究方向？',teacher_names={'黄玮'}),'师资信息')
        self.assertEqual(infer_category('2027年黄玮老师硕士招生名额有几个？'),'招生信息')
        self.assertEqual(infer_category('数据结构课程的考核方式是什么？'),'课程大纲')

    def test_named_teacher_does_not_retrieve_another_biography(self):
        with tempfile.TemporaryDirectory() as folder:
            app=create_app(Path(folder),seed=False);kb=app.state.kb
            for number,name in [(1,'教师甲'),(2,'教师乙')]:
                kb.add(name+'.md',f'# 教师档案 {number:03d}｜{name}\n{name}老师研究方向为媒体安全，讲授示例课程。'.encode(),'师资信息',metadata={'domain':'faculty'})
            with TestClient(app) as client:
                result=client.post('/api/ask',json={'question':'教师甲老师研究方向是什么？','answer_mode':'extractive'}).json()
                missing=client.post('/api/ask',json={'question':'无名老师简介是什么？','answer_mode':'extractive'}).json()
            self.assertEqual({s['name'] for s in result['sources']},{'教师甲.md'})
            self.assertEqual(missing['mode'],'no_evidence')

    def test_course_relation_keeps_historical_evidence_and_specific_subject(self):
        with tempfile.TemporaryDirectory() as folder:
            app=create_app(Path(folder),seed=False);kb=app.state.kb
            kb.add('课程.md','# 课程条目 001｜数据结构\n课程名称：数据结构。教师甲曾经讲授该课，原页未注明当前学期。\n# 课程条目 002｜计算机网络\n教师乙讲授计算机网络。'.encode(),'课程简介',metadata={'domain':'courses'})
            with TestClient(app) as client:
                result=client.post('/api/ask',json={'question':'数据结构由谁讲授？','answer_mode':'extractive'}).json()
            self.assertTrue(result['sources'])
            self.assertTrue(all('数据结构' in s['heading'] for s in result['sources']))
            self.assertIn('曾经',result['answer'])

    def test_profile_and_course_titles_continue_without_mixing_people_or_footer(self):
        first=Mock();first.extract_text.return_value='教师档案 001｜教师甲\n职称：副教授。'
        second=Mock();second.extract_text.return_value='学院公开网页信息汇编｜第 2 页\n研究方向：媒体安全。\n教师档案 002｜教师乙\n研究方向：人工智能。'
        with patch('pypdf.PdfReader',return_value=Mock(is_encrypted=False,pages=[first,second])):
            chunks=chunk_pages(parse_document('教师汇编.pdf',b'fixture'))
        continued=next(c for c in chunks if '媒体安全' in c['text'])
        self.assertIn('教师甲',continued['text'])
        self.assertNotIn('教师乙',continued['text'])
        self.assertTrue(all('学院公开网页信息汇编' not in c['text'] for c in chunks))


if __name__=='__main__':unittest.main()
