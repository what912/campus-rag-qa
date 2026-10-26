import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import create_app
from campus.credentials import save_key, load_key, clear_key
from campus.documents import parse_document, chunk_pages


class MaterialTests(unittest.TestCase):
    def test_html_article_preserves_numbers_and_tables_without_navigation_or_scripts(self):
        content = '<html><nav>无关导航</nav><div class="wp_articlecontent"><p>毕业总学分：<span>1</span><span>71</span>学分</p><table><tr><td>专业核心</td><td>26</td></tr></table><img src="remote.png"><script>evil()</script></div></html>'.encode()
        pages = parse_document('plan.html',content)
        self.assertIn('171学分',pages[0]['text'])
        self.assertIn('专业核心 | 26',pages[0]['text'])
        self.assertNotIn('evil()',pages[0]['text'])
        self.assertNotIn('无关导航',pages[0]['text'])
        self.assertTrue(pages[0]['contains_images'])

    def test_course_heading_is_retained_on_continuation_page(self):
        import pymupdf
        with pymupdf.open() as doc:
            first=doc.new_page()
            first.insert_text((30,50),'操作系统（Operating Systems）',fontname='china-s')
            first.insert_text((30,80),'学习进程管理。',fontname='china-s')
            second=doc.new_page()
            second.insert_text((30,50),'实验采用 Linux 环境。',fontname='china-s')
            pages=parse_document('course.pdf',doc.tobytes())
        chunks=chunk_pages(pages)
        continued=next(c for c in chunks if c['page']==2)
        self.assertIn('操作系统',continued['heading'])
        self.assertIn('Linux',continued['text'])


@unittest.skipUnless(os.name=='nt','Windows DPAPI')
class CredentialTests(unittest.TestCase):
    def test_encrypted_key_roundtrip_origin_binding_and_clear(self):
        with tempfile.TemporaryDirectory() as temporary:
            save_key(temporary,'unit-test-secret','https://example.invalid')
            self.assertNotIn(b'unit-test-secret',(Path(temporary)/'credential.dpapi').read_bytes())
            self.assertEqual(load_key(temporary,'https://example.invalid/v1'),'unit-test-secret')
            self.assertEqual(load_key(temporary,'https://different.invalid'),'')
            clear_key(temporary)
            self.assertEqual(load_key(temporary,'https://example.invalid'),'')

    def test_restart_restores_key_without_returning_or_plaintext_persistence(self):
        with tempfile.TemporaryDirectory() as temporary:
            with TestClient(create_app(Path(temporary),seed=False)) as client:
                response=client.put('/api/settings',json={'base_url':'https://example.invalid','model':'fake',
                    'api_key':'unit-test-secret','remember_key':True})
                self.assertEqual(response.status_code,200)
            self.assertNotIn('unit-test-secret',(Path(temporary)/'settings.json').read_text(encoding='utf-8'))
            with TestClient(create_app(Path(temporary),seed=False)) as client:
                status=client.get('/api/settings')
                self.assertTrue(status.json()['has_key'])
                self.assertNotIn('unit-test-secret',status.text)
                with patch('app.test_provider',return_value={'models':['fake']}) as provider:
                    client.post('/api/model/test',json={'base_url':'https://different.invalid','model':'fake'})
                    self.assertEqual(provider.call_args.args[0]['api_key'],'')
