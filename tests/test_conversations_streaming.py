import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from app import create_app
from campus.conversations import resolve_question
from campus.providers import stream_answer


class ConversationAPITests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.directory=Path(self.temporary.name)
        self.app=create_app(self.directory,seed=False);self.client=TestClient(self.app)
        self.addCleanup(self.client.close)
        self.app.state.kb.add('数据结构.md','数据结构课程学分为3学分，考核方式为闭卷考试和实验作业。'.encode(),'课程大纲',metadata={'major':'计算机科学与技术','cohort':'2023'})
        self.app.state.kb.add('操作系统.md','操作系统课程考核方式为考试和实验报告，课程有4学分。'.encode(),'课程大纲',metadata={'major':'计算机科学与技术','cohort':'2023'})
        self.session=self.client.post('/api/sessions').json()['id']

    def ask(self,question,**kwargs):
        response=self.client.post('/api/ask',json={'question':question,'session_id':self.session,'answer_mode':'extractive',**kwargs})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def test_followup_retrieves_again_and_is_persisted_across_restart(self):
        first=self.ask('2023级计算机科学与技术专业的数据结构课程有多少学分？')
        second=self.ask('那考核方式呢？')
        self.assertTrue(second['query_rewritten'])
        self.assertIn('数据结构',second['resolved_question'])
        self.assertIn('考核',second['resolved_question'])
        self.assertEqual(second['cohort'],'2023')
        self.assertTrue(second['sources'])
        with TestClient(create_app(self.directory,seed=False)) as restarted:
            record=restarted.get('/api/sessions/'+self.session).json()
        self.assertEqual(len(record['turns']),2)
        self.assertEqual(record['turns'][1]['result']['resolved_question'],second['resolved_question'])
        self.assertNotIn('api_key',json.dumps(record))

    def test_course_switch_and_new_topic_do_not_keep_old_subject(self):
        self.ask('数据结构课程的考核方式是什么？')
        changed=self.ask('那操作系统呢？')
        self.assertIn('操作系统',changed['resolved_question'])
        self.assertNotIn('数据结构',changed['resolved_question'])
        fresh=self.ask('电子成绩单在哪里办理？')
        self.assertFalse(fresh['query_rewritten'])
        self.assertNotIn('操作系统',fresh['resolved_question'])

    def test_major_switch_does_not_reuse_wrong_major_documents(self):
        self.ask('计算机科学与技术专业的数据结构课程有多少学分？')
        switched=self.ask('那软件工程专业呢？')
        self.assertEqual(switched['major'],'软件工程')
        self.assertFalse(switched['sources'])
        self.assertEqual(switched['mode'],'no_evidence')

    def test_ambiguous_first_turn_never_calls_provider(self):
        self.app.state.settings['enabled']=True
        with patch('app.generate_answer') as generate,patch('app.stream_answer') as streamed:
            result=self.ask('那这门课呢？',answer_mode='auto')
        self.assertEqual(result['mode'],'clarification')
        generate.assert_not_called();streamed.assert_not_called()

    def test_streamed_extractive_result_is_complete_and_saved_once(self):
        with patch('app.stream_answer') as provider:
            response=self.client.post('/api/ask/stream',json={'question':'数据结构课程如何考核？',
                'session_id':self.session,'answer_mode':'extractive'})
        self.assertIn('text/event-stream',response.headers['content-type'])
        for event in ('status','sources','delta','done'): self.assertIn('event: '+event,response.text)
        self.assertNotIn('event: error',response.text)
        self.assertEqual(len(self.client.get('/api/sessions/'+self.session).json()['turns']),1)
        provider.assert_not_called()

    def test_stream_failure_does_not_save_partial_answer_as_completed(self):
        self.app.state.settings['enabled']=True
        def fail(*args,**kwargs):
            yield '部分回答'
            raise ValueError('测试流中断')
        with patch('app.stream_answer',side_effect=fail):
            response=self.client.post('/api/ask/stream',json={'question':'数据结构如何考核？','session_id':self.session})
        self.assertIn('部分回答',response.text)
        self.assertIn('event: error',response.text)
        self.assertNotIn('event: done',response.text)
        self.assertEqual(self.client.get('/api/sessions/'+self.session).json()['turns'],[])

    def test_delete_session_and_unknown_session_are_reported(self):
        self.ask('数据结构如何考核？')
        self.assertEqual(self.client.delete('/api/sessions/'+self.session).status_code,200)
        self.assertEqual(self.client.get('/api/sessions/'+self.session).status_code,404)
        self.assertEqual(self.client.post('/api/ask/stream',json={'question':'你好','session_id':self.session}).status_code,404)

    def test_import_outcomes_include_success_duplicate_and_failure(self):
        response=self.client.post('/api/documents',files={'file':('新文件.txt','申请证明需要身份证。'.encode())})
        self.assertEqual(response.status_code,201)
        self.client.post('/api/documents',files={'file':('重复.txt','申请证明需要身份证。'.encode())})
        self.client.post('/api/documents',files={'file':('空.txt',b'')})
        rows=self.client.get('/api/imports').json()
        self.assertEqual([r['status'] for r in rows],['failed','failed','completed'])
        self.assertEqual(rows[-1]['chunks'],1)


class StreamingProtocolTests(unittest.TestCase):
    def test_real_protocol_deltas_usage_done_and_no_secret_leak(self):
        payloads=[]
        def handle(request):
            payloads.append(json.loads(request.content))
            rows=[{'id':'mock','model':'mock','choices':[{'delta':{'content':'依据'},'finish_reason':None}]},
                  {'choices':[{'delta':{'content':'[1]。'},'finish_reason':'stop'}],'usage':{'total_tokens':12}}]
            body=''.join('data: '+json.dumps(r,ensure_ascii=False)+'\n\n' for r in rows)+'data: [DONE]\n\n'
            return httpx.Response(200,text=body,headers={'content-type':'text/event-stream'})
        client=httpx.Client(transport=httpx.MockTransport(handle));self.addCleanup(client.close)
        info={}
        with patch('campus.providers.httpx.Client',return_value=client):
            deltas=list(stream_answer('问题',[],{'base_url':'https://example.invalid','model':'mock','api_key':'unit-test-secret'},telemetry=info))
        self.assertEqual(deltas,['依据','[1]。'])
        self.assertTrue(payloads[0]['stream'])
        self.assertEqual(info['usage']['total_tokens'],12)
        self.assertEqual(info['finish_reason'],'stop')
        self.assertNotIn('unit-test-secret',json.dumps(info))

    def test_truncated_provider_stream_is_error(self):
        client=httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,
            text='data: {"choices":[{"delta":{"content":"partial"}}]}\n\n')))
        self.addCleanup(client.close)
        with patch('campus.providers.httpx.Client',return_value=client),self.assertRaises(ValueError):
            list(stream_answer('问题',[],{'base_url':'https://example.invalid','model':'mock'}))

    def test_unanswered_topic_does_not_reuse_old_answer_context(self):
        turns=[{'question':'数据结构多少学分','result':{'sources':[{}],'mode':'extractive'}},
               {'question':'奖学金怎么办','result':{'sources':[],'mode':'no_evidence'}}]
        result=resolve_question('它需要哪些材料？',turns)
        self.assertTrue(result['clarification'])
        self.assertNotIn('数据结构',result['query'])


if __name__=='__main__': unittest.main()
