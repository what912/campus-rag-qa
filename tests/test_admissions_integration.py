import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import create_app
from campus.conversations import resolve_question


class AdmissionsIntegrationTests(unittest.TestCase):
    def test_followup_switches_from_ordinary_undergraduate_to_art(self):
        kb=self.app.state.kb
        for program,name in [('general_undergraduate','普通本科'),('art_undergraduate','艺术校考')]:
            kb.add(name+'.md',('2026年'+name+'本科招生章程规定按照公示程序招生录取。').encode(),'招生信息',metadata={
                'domain':'admissions','admission_year':'2026','admission_level':'undergraduate','admission_program':program})
        session=self.client.post('/api/sessions').json()['id']
        first=self.client.post('/api/ask',json={'question':'2026年本科招生章程是什么？','session_id':session}).json()
        second=self.client.post('/api/ask',json={'question':'那艺术类呢？','session_id':session}).json()
        self.assertTrue(first['sources'])
        self.assertEqual(second['admission_scope']['program'],'art_undergraduate')
        self.assertEqual(second['admission_scope']['year'],'2026')
        self.assertIn('章程',second['resolved_question'])
        self.assertEqual({s['name'] for s in second['sources']},{'艺术校考.md'})

    def test_followup_switches_international_to_ordinary_master(self):
        self.app.state.kb.add('国际项目.md','2026年1+1国际硕士招生对象为本科毕业生。'.encode(),'招生信息',metadata={
            'domain':'admissions','admission_year':'2026','admission_level':'master','admission_program':'international_1plus1'})
        session=self.client.post('/api/sessions').json()['id']
        first=self.client.post('/api/ask',json={'question':'2026年1+1国际硕士招生对象有哪些？','session_id':session}).json()
        second=self.client.post('/api/ask',json={'question':'那普通硕士呢？','session_id':session}).json()
        self.assertTrue(first['sources'])
        self.assertEqual(second['admission_scope']['program'],'general_master')
        self.assertEqual(second['admission_scope']['year'],'2026')
        self.assertIn('招生对象',second['resolved_question'])
        self.assertNotIn('1+1',second['resolved_question'])
        self.assertEqual({s['name'] for s in second['sources']},{'硕士2026.md'})

    def test_cross_program_and_cross_year_comparisons_request_clarification(self):
        with patch('app.generate_answer') as generated:
            for question in ('2026年本科和硕士招生有什么区别？',
                             '2026年1+1国际硕士和MBA学费一样吗？',
                             '2026年和2027年硕士招生有什么区别？'):
                with self.subTest(question=question):
                    result=self.client.post('/api/ask',json={'question':question}).json()
                    self.assertEqual(result['mode'],'clarification')
                    self.assertFalse(result['sources'])
            generated.assert_not_called()

    def test_project_switch_preserves_personal_identity_and_fee_facet(self):
        old='我是博士生，2026年博士招生学费是多少？'
        turns=[{'question':old,'result':{'mode':'extractive','sources':[{}],'resolved_question':old,
                'admission_scope':{'domain':True,'year':'2026','level':'doctor','program':'general_doctor'}}}]
        query=resolve_question('那普通硕士呢？',turns)['query']
        self.assertIn('我是博士生',query)
        self.assertIn('普通硕士招生学费',query)
        self.assertNotIn('博士招生',query)
        old='2026年硕士学费是多少？'
        turns=[{'question':old,'result':{'mode':'no_evidence','sources':[],'resolved_question':old,
                'admission_scope':{'domain':True,'year':'2026','level':'master','program':'general_master'}}}]
        changed=resolve_question('那MBA呢？',turns)
        self.assertFalse(changed['clarification'])
        self.assertIn('MBA招生学费',changed['query'])
        old='我是2002年出生的学生，2026年硕士招生报考条件是什么？'
        turns=[{'question':old,'result':{'mode':'no_evidence','sources':[],'resolved_question':old,
                'admission_scope':{'domain':True,'year':'2026','level':'master','program':'general_master'}}}]
        changed=resolve_question('那2027年呢？',turns)['query']
        self.assertIn('2002年出生',changed)
        self.assertIn('2027年硕士招生',changed)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.app=create_app(Path(self.temp.name),seed=False);self.client=TestClient(self.app);self.addCleanup(self.client.close)
        kb=self.app.state.kb
        for year,program,name in [('2026','general_master','硕士2026'),('2027','general_master','硕士2027'),('2026','mba','MBA2026')]:
            kb.add(name+'.md',(name+'硕士招生报考条件是完成审核并提交申请材料。').encode(),'招生信息',
                metadata={'domain':'admissions','admission_year':year,'admission_level':'master','admission_program':program})

    def test_metadata_survives_and_scope_is_hard_filtered(self):
        sources=self.app.state.kb.search('2026年硕士招生报考条件',category='招生信息',min_score=.01)
        self.assertEqual([s['name'] for s in sources],['硕士2026.md'])
        self.assertEqual(sources[0]['metadata']['admission_year'],'2026')
        latest=self.app.state.kb.search('硕士招生报考条件',category='招生信息',min_score=.01)
        self.assertEqual([s['name'] for s in latest],['硕士2027.md'])

    def test_reference_material_and_wrong_domain_do_not_pollute_answers(self):
        kb=self.app.state.kb
        kb.add('未核实.md','2028年硕士招生报考条件完成审核提交申请材料。'.encode(),'招生信息',
            metadata={'domain':'admissions','admission_year':'2028','admission_level':'master','admission_program':'general_master','reference_only':True})
        kb.add('证明.md','在读证明办理时提交申请材料，完成身份审核。'.encode(),'办事流程')
        question=self.client.post('/api/ask',json={'question':'在读证明怎么办理？'}).json()
        self.assertEqual({s['name'] for s in question['sources']},{'证明.md'})
        admissions=self.client.post('/api/ask',json={'question':'硕士招生报考条件是什么？'}).json()
        self.assertEqual(admissions['admission_scope']['year'],'2027')
        self.assertNotIn('未核实.md',[s['name'] for s in admissions['sources']])

    def test_admission_prompt_does_not_claim_default_student_cohort_is_cycle(self):
        self.app.state.settings['enabled']=True
        with patch('app.generate_answer',return_value=('完成申请审核[1]。',[])) as generated:
            result=self.client.post('/api/ask',json={'question':'我是2023级本科生，2026年硕士招生报考条件是什么？'}).json()
        self.assertEqual(result['mode'],'rag')
        self.assertEqual({s['name'] for s in result['sources']},{'硕士2026.md'})
        prompt=generated.call_args.args[0]
        self.assertIn('2026年',prompt)
        self.assertNotIn('用户选择的适用范围：2023级',prompt)

    def test_admission_followup_changes_cycle_but_preserves_question(self):
        session=self.client.post('/api/sessions').json()['id']
        first=self.client.post('/api/ask',json={'question':'2026年硕士招生报考条件是什么？','session_id':session}).json()
        second=self.client.post('/api/ask',json={'question':'那2027年呢？','session_id':session}).json()
        self.assertTrue(first['sources'])
        self.assertEqual(second['admission_scope']['year'],'2027')
        self.assertIn('报考条件',second['resolved_question'])
        self.assertEqual({s['name'] for s in second['sources']},{'硕士2027.md'})

    def test_upload_requires_admission_cycle_and_program_or_reference_flag(self):
        data={'category':'招生信息'}
        bad=self.client.post('/api/documents',files={'file':('资料.txt','新招生报考资料。'.encode())},data=data)
        self.assertEqual(bad.status_code,400)
        data.update(admission_year='2026',admission_program='general_doctor')
        good=self.client.post('/api/documents',files={'file':('资料.txt','新招生报考资料。'.encode())},data=data)
        self.assertEqual(good.status_code,201)
        document=self.client.get('/api/documents/'+good.json()['id']).json()
        self.assertEqual(document['metadata']['admission_level'],'doctor')
        self.assertEqual(document['metadata']['cohort'],'')

    def test_ocr_text_layer_provenance_is_retained(self):
        kb=self.app.state.kb
        identifier=kb.add('截图转换.md','招生简章截图识别的正文。'.encode(),'招生信息',metadata={
            'domain':'admissions','admission_year':'2026','admission_level':'doctor','admission_program':'general_doctor',
            'ocr_text_layer':True,'original_filename':'原图.png','actual_title':'2026年博士招生简章'})
        meta=kb.document(identifier)['metadata']
        self.assertEqual(meta['extraction_method'],'ocr')
        self.assertEqual(meta['original_filename'],'原图.png')

    def test_followup_year_does_not_replace_persons_enrolled_cohort(self):
        turns=[{'question':'我是2023级学生，2026年硕士招生报考条件是什么？','result':{
            'mode':'extractive','sources':[{}],'admission_scope':{'domain':True,'year':'2026','program':'general_master'},
            'resolved_question':'我是2023级学生，2026年硕士招生报考条件是什么？'}}]
        resolved=resolve_question('那2027年呢？',turns)['query']
        self.assertIn('2023级',resolved);self.assertIn('2027年硕士招生',resolved)

    def test_year_followup_uses_prior_question_even_when_unanswered(self):
        turns=[{'question':'2026年硕士招生学费是多少？','result':{'mode':'no_evidence','sources':[],
            'resolved_question':'2026年硕士招生学费是多少？',
            'admission_scope':{'domain':True,'year':'2026','program':'general_master'}}}]
        result=resolve_question('那2027年呢？',turns)
        self.assertTrue(result['rewritten'])
        self.assertIn('2027年硕士招生学费',result['query'])

    def test_year_followup_does_not_replace_graduation_class(self):
        turns=[{'question':'我是2023届毕业生，2026年硕士招生报考条件是什么？','result':{
            'mode':'no_evidence','sources':[],'admission_scope':{'domain':True,'year':'2026'}}}]
        query=resolve_question('那2027年呢？',turns)['query']
        self.assertIn('2023届',query);self.assertIn('2027年硕士招生',query)


if __name__=='__main__':unittest.main()
