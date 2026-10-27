import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

from campus.knowledge import KnowledgeBase
from campus.documents import parse_document,chunk_pages
from scripts.bootstrap_knowledge import apply_reference_overrides,load_bundled_items
from scripts.collect_yz_official import safe_fetch


class OfficialCorpusTests(unittest.TestCase):
    def test_missing_exam_outline_is_not_explained_as_only_old_sources(self):
        from app import create_app
        from fastapi.testclient import TestClient
        with tempfile.TemporaryDirectory() as temporary:
            app=create_app(Path(temporary),seed=False)
            scope={'domain':'admissions','admission_year':'2027','admission_level':'master','admission_program':'general_master'}
            app.state.kb.add('官方目录.txt','计算机科学与技术初试科目包括408计算机学科专业基础。'.encode(),'招生信息',metadata=scope)
            app.state.kb.add('截图.txt','同项目已保存截图用于来源核对。'.encode(),'招生信息',metadata={**scope,'reference_only':True})
            app.state.settings['enabled']=True
            with TestClient(app) as client,patch('app.generate_answer') as generated:
                result=client.post('/api/ask',json={'question':'2027年硕士408考试大纲包括哪些章节？','answer_mode':'extractive'}).json()
            self.assertEqual(result['mode'],'no_evidence')
            self.assertIn('408科目的完整考试大纲',result['answer'])
            self.assertNotIn('旧版或整理稿',result['answer'])
            generated.assert_not_called()

    def test_catalogue_heading_separates_majors_and_survives_page_break(self):
        first=Mock();first.extract_text.return_value='目录记录 1｜081200计算机科学与技术\n初试科目：408。\n'
        second=Mock();second.extract_text.return_value='官网网页正文排版版｜第 2 页\n招生人数：24。\n目录记录 2｜083900网络空间安全\n初试科目：408。'
        with patch('pypdf.PdfReader',return_value=Mock(is_encrypted=False,pages=[first,second])):
            chunks=chunk_pages(parse_document('目录.pdf',b'fixture'))
        continued=next(c for c in chunks if c['page']==2 and '招生人数' in c['text'])
        other=next(c for c in chunks if '083900' in c['heading'])
        self.assertIn('081200',continued['heading'])
        self.assertNotIn('招生人数',other['text'])
        self.assertTrue(all('官网网页正文排版版｜第' not in c['text'] for c in chunks))
        continued_chunks=chunk_pages([{'page':1,'text':'# 目录记录 3｜081200计算机科学与技术\n'+('字段：原表信息。'*60)}])
        self.assertGreater(len(continued_chunks),1)
        self.assertTrue(all('081200' in c['text'] and len(c['text'])<=400 for c in continued_chunks))

    def test_exam_outline_query_requires_matching_outline_metadata_not_directory_mentions(self):
        with tempfile.TemporaryDirectory() as temporary:
            kb=KnowledgeBase(Path(temporary))
            scope={'domain':'admissions','admission_year':'2027','admission_level':'master','admission_program':'general_master'}
            kb.add('目录.txt','硕士初试考试科目有408计算机学科专业基础，本目录不是其考试大纲。'.encode(),'招生信息',metadata=scope)
            kb.add('大纲.txt','823信号与系统考试大纲包括系统基本性质和分析方法。'.encode(),'招生信息',metadata={**scope,'exam_codes':['823']})
            self.assertEqual(kb.search('2027年硕士初试408考试大纲包含哪些章节？',min_score=.01),[])
            result=kb.search('2027年硕士初试823考试大纲包含哪些章节？',min_score=.01)
            self.assertEqual({r['name'] for r in result},{'大纲.txt'})

    def test_reference_override_is_hash_bound_idempotent_and_preserves_documents(self):
        with tempfile.TemporaryDirectory() as temporary:
            kb=KnowledgeBase(Path(temporary))
            content='旧截图正文仅供对照原件，不应优先于正式网页正文。'.encode()
            identifier=kb.add('截图.txt',content,'招生信息',metadata={'review_note':'原始识别说明'})
            other=kb.add('其他.txt','另一份正式资料应继续参与问答。'.encode(),'招生信息')
            rule={'sha256':hashlib.sha256(content).hexdigest(),'reference_only':True,'review_note_suffix':'已取得官方正文'}
            self.assertEqual(apply_reference_overrides(kb,[rule]),1)
            self.assertEqual(apply_reference_overrides(kb,[rule]),0)
            self.assertEqual(kb.stats()['documents'],2)
            metadata=kb.document(identifier)['metadata']
            self.assertTrue(metadata['reference_only'])
            self.assertEqual(metadata['review_note'],'原始识别说明 已取得官方正文')
            self.assertFalse(kb.document(other)['metadata'].get('reference_only'))
            with self.assertRaises(ValueError):apply_reference_overrides(kb,[dict(rule,reference_only=False)])

    def test_official_manifest_is_optional_and_can_extend_prior_bundles(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);folder=root/'materials';folder.mkdir()
            (folder/'manifest.json').write_text(json.dumps([{'usage':'knowledge','name':'public'},{'usage':'reference','name':'unused'}]),encoding='utf-8')
            (folder/'user-material-manifest.json').write_text(json.dumps({'documents':[{'name':'provided'}]}),encoding='utf-8')
            self.assertEqual([r['name'] for r in load_bundled_items(root)[0]],['public','provided'])
            official=folder/'yz-official';official.mkdir()
            (official/'manifest.json').write_text(json.dumps({'documents':[{'name':'official'}],'reference_overrides':[{'sha256':'bound'}]}),encoding='utf-8')
            items,overrides=load_bundled_items(root)
            self.assertEqual([r['name'] for r in items],['public','provided','official'])
            self.assertEqual(overrides,[{'sha256':'bound'}])

    def test_degree_metadata_survives_import(self):
        with tempfile.TemporaryDirectory() as temporary:
            kb=KnowledgeBase(Path(temporary))
            identifier=kb.add('专业博士.txt','专业学位博士附加材料要求。'.encode(),'招生信息',metadata={'degree_type':'professional'})
            self.assertEqual(kb.document(identifier)['metadata']['degree_type'],'professional')

    def test_collector_rejects_non_official_origins_before_network_access(self):
        client=Mock()
        for url in ('http://yz.cuc.edu.cn/main.htm','https://example.org/file.pdf',
                    'https://user:password@yz.cuc.edu.cn/main.htm','https://yz.cuc.edu.cn.example.org/file.pdf'):
            with self.subTest(url=url),self.assertRaises(ValueError):safe_fetch(url,client)
        client.stream.assert_not_called()


if __name__=='__main__':unittest.main()
