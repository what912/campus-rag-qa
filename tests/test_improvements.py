import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import create_app
from campus.documents import parse_document


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app(Path(self.temp.name), seed=False)
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_major_year_and_reference_filters(self):
        kb = self.app.state.kb
        kb.add('2025.md', '毕业总学分要求为测试值 180，此为测试资料。'.encode(), '培养方案',
               metadata={'cohort':'2025', 'major':'计算机科学与技术'})
        kb.add('reference.md', '毕业总学分要求测试值 181，仅作参考资料。'.encode(), '培养方案',
               metadata={'reference_only':True})
        missing = self.client.post('/api/ask', json={'question':'2023级毕业总学分是多少？'}).json()
        self.assertEqual(missing['mode'], 'no_evidence')
        present = self.client.post('/api/ask', json={'question':'2025级毕业总学分是多少？'}).json()
        self.assertEqual([s['name'] for s in present['sources']], ['2025.md'])
        different_major = self.client.post('/api/ask', json={'question':'2025级毕业总学分是多少？', 'major':'新闻学'}).json()
        self.assertEqual(different_major['sources'], [])

    def test_missing_curriculum_is_not_answered_from_general_degree_policy(self):
        self.app.state.kb.add('degree.md', '学生应完成培养方案规定的毕业总学分，才能申请学位。'.encode(), '办事流程')
        result = self.client.post('/api/ask', json={'question':'2023级计算机专业毕业总学分是多少？'}).json()
        self.assertEqual(result['mode'], 'no_evidence')

    def test_superficial_generic_keyword_does_not_match_an_unrelated_question(self):
        self.app.state.kb.add('policy.md', '学校推荐优秀本科生免试升学，推荐资格按官方通知审核。'.encode(), '办事流程')
        result = self.client.post('/api/ask', json={'question':'推荐一道红烧牛肉的烹饪菜谱。'}).json()
        self.assertEqual(result['mode'], 'no_evidence')

    def test_diagnostics_does_not_persist_unsaved_credentials(self):
        with patch('app.test_provider', return_value={'models':['test'], 'selected_model_available':True}):
            response = self.client.post('/api/model/test', json={
                'base_url':'https://example.invalid/v1', 'model':'test', 'api_key':'only-for-diagnostics'})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('only-for-diagnostics', response.text)
        self.assertFalse(self.client.get('/api/settings').json()['has_key'])
        self.assertFalse((Path(self.temp.name) / 'settings.json').exists())

    def test_timings_and_invalid_strategy(self):
        response = self.client.put('/api/settings', json={'retrieval_strategy':'unknown'})
        self.assertEqual(response.status_code, 400)
        result = self.client.post('/api/ask', json={'question':'天气'}).json()
        self.assertEqual(result['generation_ms'], 0)
        self.assertGreaterEqual(result['elapsed_ms'], result['retrieval_ms'])

    def test_general_grade_rule_uses_policy_instead_of_specific_course_outline(self):
        self.app.state.kb.add('policy.md','每门课程期末考试与平时成绩的比例，由教研室或任课教师根据课程性质确定。'.encode(),'办事流程')
        self.app.state.kb.add('outline.md','数据结构课程考核采用作业和考试，未列出成绩比例。'.encode(),'课程大纲')
        result=self.client.post('/api/ask',json={'question':'每门课程期末考试与平时成绩的比例由谁确定？'}).json()
        self.assertEqual(result['mode'],'extractive')
        self.assertEqual({s['name'] for s in result['sources']},{'policy.md'})


class OptionalOCRTests(unittest.TestCase):
    @unittest.skipUnless((Path(__file__).resolve().parents[1] / 'models/ocr').is_dir(), '本地 OCR 模型未准备')
    def test_real_scanned_pdf_recognition(self):
        import pymupdf
        from PIL import Image, ImageDraw, ImageFont
        image = Image.new('RGB', (850, 220), 'white')
        draw = ImageDraw.Draw(image)
        draw.text((35, 65), 'Campus proof of enrollment', fill='black', font=ImageFont.load_default(size=38))
        buffer = io.BytesIO()
        image.save(buffer, format='PNG')
        with pymupdf.open() as doc:
            page = doc.new_page(width=600, height=250)
            page.insert_image(pymupdf.Rect(20, 20, 580, 220), stream=buffer.getvalue())
            pages = parse_document('scan.pdf', doc.tobytes(), enable_ocr=True)
        self.assertIn('campus', pages[0]['text'].lower())
        self.assertEqual(pages[0]['page'], 1)
        self.assertEqual(pages[0]['extraction_method'], 'ocr')
