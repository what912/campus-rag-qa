import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import numpy as np

from campus.knowledge import KnowledgeBase
from campus.search_features import intent_match


class AdmissionsRetrievalTests(unittest.TestCase):
    def test_exact_clause_in_long_pdf_survives_low_cosine_without_crossing_scope(self):
        with tempfile.TemporaryDirectory() as folder:
            kb=KnowledgeBase(Path(folder))
            for year,program,name in [('2026','international_1plus1','target'),
                                      ('2027','international_1plus1','wrong-year'),
                                      ('2026','general_master','wrong-program')]:
                kb.add(name+'.txt',(name+'海外院校与培养介绍。'*24+'招生对象：须提交学历证明并接受审核。').encode(),
                       '招生信息',metadata={'domain':'admissions','admission_year':year,
                       'admission_level':'master','admission_program':program})
            kb.lexical_matrix=kb.matrix
            kb.matrix=np.zeros((len(kb.rows),2))
            kb.model=Mock();kb.model.encode_query.return_value=np.array([[1.,0.]])
            kb.mode='semantic'
            found=kb.search('2026年1+1国际硕士预科项目的招生对象是什么？',
                            strategy='hybrid_rerank',min_score=.55)
            self.assertTrue(found)
            self.assertEqual({row['name'] for row in found},{'target.txt'})
            self.assertIn('招生对象',found[0]['text'])

    def test_clause_labels_survive_pdf_line_breaks_but_do_not_match_other_facets(self):
        self.assertEqual(intent_match('2026年本科招生是否设置专业级差？','学校不设专业\n级差。'),1)
        self.assertEqual(intent_match('2026年艺术类本科初试考什么？','初试考试\n内容：文化素养基础测试。'),1)
        self.assertEqual(intent_match('2026年本科招生是否设置专业级差？','招生计划另行公布。'),0)


if __name__=='__main__':unittest.main()
