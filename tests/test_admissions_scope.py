import unittest
from campus.admissions import infer_admission_scope,scope_matches,choose_admission_year,describe_scope


def metadata(year='2026',program='general_master',level='master',**extra):
    return {'domain':'admissions','admission_year':year,'admission_program':program,'admission_level':level,**extra}


class AdmissionsScopeTests(unittest.TestCase):
    def test_comparisons_with_omitted_level_words_or_multiple_programs_are_ambiguous(self):
        for question in ('2026年本科和硕士招生有什么区别？',
                         '2026年1+1国际硕士和MBA学费一样吗？',
                         '2026年普通硕士和1+1国际硕士招生对象有什么不同？',
                         '2026年和2027年硕士招生有什么区别？',
                         '2026年硕士招生和2027年硕士招生有什么变化？'):
            with self.subTest(question=question):
                scope=infer_admission_scope(question)
                self.assertTrue(scope.get('ambiguous'))
                self.assertFalse(scope_matches(metadata(),scope))

    def test_student_identity_is_not_a_comparison_of_admission_levels(self):
        question='我是2023级本科生，2026年硕士招生报考条件和往年相比有什么不同？'
        scope=infer_admission_scope(question)
        self.assertFalse(scope.get('ambiguous'))
        self.assertEqual(scope['program'],'general_master')
        self.assertEqual(scope['year'],'2026')
        self.assertFalse(infer_admission_scope('MBA硕士招生学费与往年一样吗？').get('ambiguous'))

    def test_each_program_is_distinguished(self):
        examples=[('2026年本科招生章程','general_undergraduate'),
                  ('2026年艺术类本科校考报名条件','art_undergraduate'),
                  ('2027年硕士研究生报考条件','general_master'),
                  ('2026年博士研究生招生简章','general_doctor'),
                  ('中传工商管理专硕招生学费','mba'),
                  ('MBA 报考条件','mba'),
                  ('1＋1国际硕士项目招生','international_1plus1'),
                  ('传媒起航计划招生条件','media_qihang')]
        for question,program in examples:
            with self.subTest(question=question):
                scope=infer_admission_scope(question)
                self.assertTrue(scope['domain']);self.assertEqual(scope['program'],program)

    def test_enrollment_cohort_is_not_admission_cycle(self):
        scope=infer_admission_scope('我是2023级学生，2026年硕士招生条件是什么？')
        self.assertEqual(scope['year'],'2026')
        self.assertEqual(infer_admission_scope('2023级学生报考硕士需要哪些条件？')['year'],'')
        self.assertEqual(infer_admission_scope('2023年入学学生咨询2027年硕士招生')['year'],'2027')
        self.assertFalse(infer_admission_scope('2023级计算机科学与技术专业毕业总学分多少')['domain'])

    def test_explicit_year_excludes_other_and_unknown_years(self):
        scope=infer_admission_scope('2026年硕士研究生招生条件')
        self.assertTrue(scope_matches(metadata(),scope))
        self.assertFalse(scope_matches(metadata(year='2027'),scope))
        self.assertFalse(scope_matches(metadata(year=''),scope))
        self.assertFalse(scope_matches({'cohort':'2026'},scope))

    def test_master_query_excludes_mba_and_international_programs(self):
        scope=infer_admission_scope('2026年硕士研究生招生学费是多少？')
        self.assertTrue(scope_matches(metadata(),scope))
        self.assertFalse(scope_matches(metadata(program='mba'),scope))
        self.assertFalse(scope_matches(metadata(program='international_1plus1'),scope))
        mba=infer_admission_scope('2026年工商管理专硕学费是多少？')
        self.assertFalse(scope_matches(metadata(),mba))
        self.assertTrue(scope_matches(metadata(program='mba'),mba))

    def test_ordinary_undergraduate_excludes_art_and_doctor(self):
        scope=infer_admission_scope('2026年本科招生章程')
        self.assertTrue(scope_matches(metadata(program='general_undergraduate',level='undergraduate'),scope))
        self.assertFalse(scope_matches(metadata(program='art_undergraduate',level='undergraduate'),scope))
        self.assertFalse(scope_matches(metadata(program='general_doctor',level='doctor'),scope))

    def test_old_special_undergraduate_cycle_is_not_relabelled_as_current(self):
        scope=infer_admission_scope('2026年传媒起航计划招生学费')
        self.assertFalse(scope_matches(metadata(year='2017',program='media_qihang',level='undergraduate'),scope))
        explicit_old=infer_admission_scope('2017年传媒起航计划招生学费')
        self.assertTrue(scope_matches(metadata(year='2017',program='media_qihang',level='undergraduate'),explicit_old))

    def test_generic_campus_registration_is_not_automatically_admissions(self):
        self.assertFalse(infer_admission_scope('运动会怎样报名？')['domain'])
        self.assertFalse(infer_admission_scope('选修课程报名怎么办？')['domain'])
        self.assertTrue(infer_admission_scope('博士申请条件是什么？')['domain'])
        self.assertTrue(infer_admission_scope('硕士学费多少？')['domain'])
        self.assertFalse(infer_admission_scope('工商管理课程考核采用什么方式？')['domain'])
        self.assertFalse(infer_admission_scope('转专业拟录取名单公示期有多久？')['domain'])

    def test_cross_level_comparison_is_not_silently_limited_to_one_level(self):
        scope=infer_admission_scope('2026年本科招生和硕士招生有什么区别？')
        self.assertTrue(scope['ambiguous'])
        self.assertFalse(scope_matches(metadata(),scope))

    def test_latest_year_is_selected_within_same_program_only(self):
        rows=[{'metadata':metadata(year='2026')},{'metadata':metadata(year='2027')},
              {'metadata':metadata(year='2028',program='mba')},
              {'metadata':metadata(year='2029',reference_only=True)},
              {'sample':True,'metadata':metadata(year='2030')}]
        chosen=choose_admission_year(rows,infer_admission_scope('硕士研究生招生条件'))
        self.assertEqual(chosen['year'],'2027')
        self.assertTrue(chosen['year_selected_from_corpus'])
        self.assertIn('2027',describe_scope(chosen))
        self.assertEqual(choose_admission_year(rows,infer_admission_scope('2026年硕士招生'))['year'],'2026')

    def test_generic_cross_program_query_does_not_choose_global_latest(self):
        scope=infer_admission_scope('学校招生资料')
        self.assertEqual(scope['program'],'');self.assertEqual(scope['level'],'')
        rows=[{'metadata':metadata(year='2026')},{'metadata':metadata(year='2027',program='mba')}]
        self.assertEqual(choose_admission_year(rows,scope)['year'],'')
        self.assertTrue(scope_matches(metadata(year='2026'),scope))
        self.assertTrue(scope_matches(metadata(year='2027',program='mba'),scope))

    def test_teaching_query_excludes_admissions_without_changing_legacy_metadata(self):
        scope=infer_admission_scope('在读证明如何办理？')
        self.assertTrue(scope_matches({'cohort':'2023','major':'计算机科学与技术'},scope))
        self.assertFalse(scope_matches(metadata(),scope))
        self.assertTrue(infer_admission_scope('学费多少？',category='招生信息')['domain'])

    def test_business_undergraduate_or_doctor_is_not_mba(self):
        self.assertEqual(infer_admission_scope('工商管理本科招生要求')['program'],'general_undergraduate')
        self.assertEqual(infer_admission_scope('工商管理博士招生条件')['program'],'general_doctor')

    def test_current_bachelor_status_does_not_override_requested_master_level(self):
        self.assertEqual(infer_admission_scope('我是2023级本科生，2026年硕士招生条件')['program'],'general_master')
        self.assertEqual(infer_admission_scope('本科生考研需要哪些材料？')['program'],'general_master')
        self.assertEqual(infer_admission_scope('本科生申请艺术硕士招生条件')['program'],'general_master')
        self.assertEqual(infer_admission_scope('硕士毕业申请本科招生')['program'],'general_undergraduate')
        self.assertEqual(infer_admission_scope('2026年推免报考条件')['program'],'general_master')


if __name__=='__main__':unittest.main()
