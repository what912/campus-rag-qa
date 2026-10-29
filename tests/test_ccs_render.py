"""Pure record-preparation checks; intentionally author no PDFs."""
import unittest

from scripts.build_ccs_pdfs import (MISSING_DESCRIPTION, clean_text, inline_markup,
                                    prepare_records, record_heading)


class CcsRenderTests(unittest.TestCase):
    def test_title_is_complete_single_line_and_keeps_identifier(self):
        self.assertEqual(record_heading('teacher', 1, '甲\n 乙'), '教师档案 001｜甲 乙')
        name = '课程名称' * 40
        self.assertEqual(record_heading('course', 1234, name), '课程条目 1234｜' + name)
        with self.assertRaises(ValueError):
            record_heading('course', 2, '')

    def test_untrusted_markup_and_symbols_remain_literal(self):
        markup = inline_markup('甲·乙 < 2 & > 1，≤ 3\n<script>')
        self.assertIn('<font name="Helvetica">·</font>', markup)
        self.assertIn('&lt; 2 &amp; &gt; 1，≤ 3<br/>&lt;script&gt;', markup)
        self.assertEqual(clean_text('甲\u200b乙\r\n丙'), '甲乙\n丙')

    def test_teacher_summary_does_not_render_publication_or_grant_lists(self):
        data = prepare_records({'collected_at': '2026-10-27', 'teachers': [
            {'id': 't1', 'name': '教师甲', 'roles': ['计算机系'], 'fields': [
                {'label': '职称', 'value': '教授'}, {'label': '正文系别', 'value': '网络空间安全系'},
                {'label': '研究方向', 'value': '人工智能'}, {'label': '论文列表', 'value': '不应搬运'},
                {'label': '基金项目', 'value': '不应搬运'}]}]})
        teacher = data['teachers'][0]
        self.assertEqual(teacher['roles'], ['计算机系'])
        self.assertEqual([field['value'] for field in teacher['fields']], ['教授', '网络空间安全系', '人工智能'])
        self.assertEqual(len(data['omissions']), 2)

    def test_course_name_alone_never_becomes_description_or_teacher_relation(self):
        data = prepare_records({'collected_at': '2026-10-27', 'courses': [
            {'id': 'c1', 'name': '数据结构', 'description_status': 'name_only', 'description': '不应作为简介',
             'teachers': [{'name': '甲', 'source_url': 'https://ccs.cuc.edu.cn/', 'relation_text': ''}]}]})
        course = data['courses'][0]
        self.assertFalse(course['has_description'])
        self.assertEqual(course['description'], '')
        self.assertEqual(course['teachers'], [])
        self.assertIn('仅见课程名称', MISSING_DESCRIPTION)

    def test_explicit_relations_keep_original_quote_url_and_date(self):
        data = prepare_records({'collected_at': '2026-10-27', 'teachers': [
            {'id': 't1', 'name': '教师甲', 'source_url': 'https://ccs.cuc.edu.cn/t1',
             'course_mentions': [{'raw_text': '主讲《程序设计》课程。', 'courses': ['程序设计']}]}],
            'courses': [{'id': 'c1', 'name': '程序设计', 'description_status': 'available',
                         'description': '训练程序设计基本能力。', 'teachers': [
                             {'name': '教师甲', 'source_url': 'https://ccs.cuc.edu.cn/t1',
                              'source_date': '2024-01-01', 'relation_text': '主讲《程序设计》课程。'}]}]})
        self.assertEqual(data['teachers'][0]['course_mentions'][0]['source_url'], 'https://ccs.cuc.edu.cn/t1')
        relation = data['courses'][0]['teachers'][0]
        self.assertEqual(relation['source_date'], '2024-01-01')
        self.assertEqual(relation['relation_text'], '主讲《程序设计》课程。')
        self.assertTrue(data['courses'][0]['has_description'])

    def test_missing_collection_date_and_duplicate_identity_are_rejected(self):
        with self.assertRaises(ValueError):
            prepare_records({'teachers': []})
        with self.assertRaisesRegex(ValueError, '重复教师'):
            prepare_records({'collected_at': '2026-10-27', 'teachers': [
                {'id': 'a', 'name': '甲'}, {'id': 'a', 'name': '乙'}]})


if __name__ == '__main__':
    unittest.main()
