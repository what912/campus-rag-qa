import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts import evaluate_answers


class FrozenPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)
        data=json.dumps({'status':'draft','cases':[{'id':'c1','question':'课程学分？','reviewed':False}]}).encode()
        (self.folder/'dataset.json').write_bytes(data)
        (self.folder/'knowledge.db').write_bytes(b'fixture database')
        manifest={'all_labels_reviewed':False,'dataset_sha256':hashlib.sha256(data).hexdigest(),
                  'database_sha256':hashlib.sha256(b'fixture database').hexdigest(),'source_hashes':{},
                  'school':'中国传媒大学','seed':2023,'execution_order':'balanced',
                  'configuration':{'base_url':'https://api.deepseek.com','model':'deepseek-flash',
                                   'retrieval_strategy':'hybrid_rerank'}}
        (self.folder/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')

    def run_plan(self,*extra):
        with patch('sys.argv',['evaluate_answers.py','--frozen',str(self.folder),'--allow-draft','--limit','1',*extra]),contextlib.redirect_stdout(io.StringIO()) as output,contextlib.redirect_stderr(io.StringIO()):
            evaluate_answers.main()
        return json.loads(output.getvalue())

    def test_preview_uses_frozen_settings_without_generation(self):
        with patch('campus.providers.generate_answer',side_effect=AssertionError('must not call API')) as generate:
            plan=self.run_plan()
        self.assertEqual(plan['maximum_generation_calls'],4)
        self.assertEqual(plan['hybrid_strategy'],'hybrid_rerank')
        self.assertEqual(plan['execution_order'],'balanced')
        generate.assert_not_called()

    def test_draft_cannot_generate_or_change_frozen_model(self):
        with patch('campus.providers.generate_answer',side_effect=AssertionError('must not call API')) as generate:
            for extra in (('--run',),('--model','different-model'),('--seed','42')):
                with self.subTest(extra=extra),self.assertRaises(SystemExit) as exit_code:
                    self.run_plan(*extra)
                self.assertEqual(exit_code.exception.code,2)
        generate.assert_not_called()

    def test_embedding_change_invalidates_snapshot(self):
        path=self.folder/'manifest.json'
        manifest=json.loads(path.read_text(encoding='utf-8'))
        manifest['configuration']['model_path']=str(self.folder)
        (self.folder/'config.json').write_bytes(b'original config')
        manifest['model_file_hashes']={'config.json':hashlib.sha256(b'original config').hexdigest()}
        path.write_text(json.dumps(manifest),encoding='utf-8')
        self.run_plan()
        (self.folder/'config.json').write_bytes(b'changed config')
        with self.assertRaises(SystemExit) as exit_code:
            self.run_plan()
        self.assertEqual(exit_code.exception.code,2)


if __name__=='__main__':
    unittest.main()
