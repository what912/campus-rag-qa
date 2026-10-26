import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch,Mock

import numpy as np
from fastapi.testclient import TestClient
from app import create_app
from campus.evaluation import evaluate_cases
from campus.experiments import read_report,save_review,read_review


class OptimizationTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.root=Path(self.temporary.name)
        self.app=create_app(self.root/'data',seed=False)
        self.client=TestClient(self.app)

    def tearDown(self):
        self.client.close();self.temporary.cleanup()

    def test_correct_file_without_correct_clause_is_not_evidence_hit(self):
        kb=self.app.state.kb
        kb.add('证明.md','# 办理\n在读证明可以自助办理。\n# 委托\n代办需委托书及身份证复印件。'.encode(),'办事流程')
        case={'id':'delegate','question':'证明能否委托代办？','document':'证明.md','page':None,'evidence':'委托书及身份证复印件'}
        unrelated=next(r for r in kb.rows if '自助办理' in r['text'])
        with patch.object(kb,'search',return_value=[dict(unrelated,score=.9,ranking_score=.9,citation=1)]):
            result=evaluate_cases(kb,[case],strategy='vector',threshold=.08)
        self.assertEqual(result['document_hit_rate'],1)
        self.assertEqual(result['evidence_hit_rate'],0)
        self.assertEqual(result['mean_recall_at_k'],0)

    def test_api_and_cli_use_same_route_scope_and_evidence_metrics(self):
        kb=self.app.state.kb
        kb.add('规则.md','每门课程期末考试与平时成绩的比例，由教研室或任课教师根据课程性质确定。'.encode(),'办事流程')
        kb.add('大纲.md','数据结构课程采用考试与作业评价，未列比例。'.encode(),'课程大纲')
        case={'id':'grade','question':'每门课程期末考试与平时成绩的比例由谁确定？','document':'规则.md','page':None,'evidence':'教研室或任课教师'}
        folder=self.root/'evaluation';folder.mkdir()
        (folder/'public_questions.development.json').write_text(json.dumps({'cases':[case]}),encoding='utf-8')
        with patch('app.ROOT',self.root):
            response=self.client.get('/api/evaluation').json()
        standalone=evaluate_cases(kb,[case],strategy='vector',threshold=.08)
        self.assertEqual(response['top_k'],4)
        self.assertEqual(response['evidence_hit_rate'],standalone['evidence_hit_rate'])
        self.assertEqual(response['rows'][0]['sources'],standalone['rows'][0]['sources'])
        self.assertEqual(response['rows'][0]['category'],'办事流程')
        self.assertIsNone(response['negative_rejection_rate'])

    def test_unresolved_evidence_label_is_visible_not_scored_as_correct(self):
        kb=self.app.state.kb
        kb.add('规则.md','在读证明可以在线申请并彩色打印。'.encode(),'办事流程')
        case={'id':'bad','question':'在读证明怎样申请？','document':'规则.md','page':None,'evidence':'原文中不存在的提示'}
        result=evaluate_cases(kb,[case],strategy='vector',threshold=.08)
        self.assertEqual(result['unresolved_labels'],['bad'])
        self.assertEqual(result['scored_answerable'],0)
        self.assertIsNone(result['evidence_hit_rate'])

    def test_reranker_prefers_requested_clause_and_preserves_scope(self):
        kb=self.app.state.kb
        kb.add('学位办法.md','# 申请\n学士学位授予办法办理申请与审核。\n# 生效\n本学位办法自2025年1月1日起施行。'.encode(),'办事流程')
        kb.add('其他年级.md','学士学位授予办法自2029年1月1日起施行。'.encode(),'办事流程',metadata={'cohort':'2029'})
        kb.lexical_matrix=kb.matrix
        kb.matrix=np.tile(np.array([1.,0.]),(len(kb.rows),1))
        kb.model=Mock();kb.model.encode_query.return_value=np.array([[1.,0.]])
        kb.mode='semantic'
        found=kb.search('学校学士学位授予办法从什么时候施行？',strategy='hybrid_rerank',min_score=.55,cohort='2023')
        self.assertEqual(found[0]['heading'],'生效')
        self.assertNotIn('其他年级.md',[s['name'] for s in found])

    def test_no_evidence_never_invokes_generation(self):
        self.app.state.settings['enabled']=True
        with patch('app.generate_answer') as generated:
            response=self.client.post('/api/ask',json={'question':'未入库的奖学金获奖名单'}).json()
        self.assertEqual(response['mode'],'no_evidence')
        generated.assert_not_called()

    def test_review_is_bound_to_report_hash_and_survives_readback(self):
        folder=self.root/'evaluation/results';folder.mkdir(parents=True)
        name='answers-20261013-120000.json'
        source={'created_at':'2026-10-13','rows':[{'id':'q1','mode':'tfidf'}]}
        (folder/name).write_text(json.dumps(source),encoding='utf-8')
        report=read_report(self.root,name)
        save_review(self.root/'data',report,'测试复核者',[{'id':'q1','mode':'tfidf','correctness':1}])
        self.assertEqual(read_review(self.root/'data',report)['rows'][0]['correctness'],1)
        source['rows'][0]['answer']='changed'
        (folder/name).write_text(json.dumps(source),encoding='utf-8')
        with self.assertRaises(ValueError):
            read_review(self.root/'data',read_report(self.root,name))

    def test_review_rejects_path_traversal_and_unrelated_rows(self):
        with self.assertRaises(ValueError):
            read_report(self.root,'../../settings.json')
        report={'filename':'answers-20261013-120000.json','sha256':'test','rows':[{'id':'q1','mode':'direct'}]}
        with self.assertRaises(ValueError):
            save_review(self.root/'data',report,'测试',[{'id':'q2','mode':'direct'}])
        self.assertFalse((self.root/'data/reviews').exists())

    def test_extractive_request_works_while_model_is_enabled_without_generation(self):
        self.app.state.kb.add('证明.md','在读证明可登录可信电子凭证系统办理，彩色打印后使用。'.encode(),'办事流程')
        self.app.state.settings['enabled']=True
        with patch('app.generate_answer') as generated:
            response=self.client.post('/api/ask',json={'question':'在读证明可以线上办理吗？','answer_mode':'extractive'}).json()
        self.assertEqual(response['mode'],'extractive')
        self.assertTrue(response['sources'])
        self.assertEqual(response['generation_ms'],0)
        generated.assert_not_called()


if __name__=='__main__':
    unittest.main()
