"""Semantic parsing checks only: no PDF artifact is authored by these tests."""
import tempfile
import unittest
from pathlib import Path

from lxml import html

from scripts.export_yz_pages import expand_table, parse_page, table_records


class YzExportTests(unittest.TestCase):
    def test_rowspans_repeat_relations_and_keep_quotas_as_original_groups(self):
        table = html.fromstring('''<table><tr><th>学科或专业</th><th>研究方向</th><th>拟招总人数(含推免)</th><th>推免拟招人数</th><th>初试科目</th><th>学院（研究院、实验室）</th></tr>
        <tr><td rowspan="2">081200<br>计算机科学与技术</td><td>01智能系统</td><td rowspan="2">24</td><td rowspan="2">12</td><td rowspan="3">①101②201③301④408</td><td rowspan="2">计算机与网络空间安全学院</td></tr>
        <tr><td>02媒体计算</td></tr><tr><td>083900 网络空间安全</td><td>01网络安全</td><td>17</td><td>8</td><td>另一个学院</td></tr></table>''')
        records = table_records(expand_table(table))['records']
        self.assertEqual(len(records), 3)
        one, two, three = [record['fields'] for record in records]
        self.assertEqual(two[0]['value'], '081200\n计算机科学与技术')
        self.assertEqual(two[5]['value'], '计算机与网络空间安全学院')
        self.assertEqual(one[2]['quota_group'], two[2]['quota_group'])
        self.assertNotEqual(two[2]['quota_group'], three[2]['quota_group'])
        self.assertEqual(two[4]['value'], three[4]['value'])
        self.assertEqual(three[5]['value'], '另一个学院')
        self.assertNotEqual(two[5]['source_cell'], three[5]['source_cell'])

    def test_colspan_multirow_headers_and_explicit_empty_cells(self):
        table = html.fromstring('''<table><tr><th rowspan="2">专业名称</th><th rowspan="2">研究方向</th><th colspan="2">考试科目</th></tr>
        <tr><th>初试科目</th><th>复试科目</th></tr>
        <tr><td>计算机</td><td>01方向</td><td>408</td><td>编程</td></tr>
        <tr><td></td><td>02方向</td><td>301</td><td>面试</td></tr></table>''')
        records = table_records(expand_table(table))['records']
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]['fields'][2]['label'], '考试科目 / 初试科目')
        self.assertEqual(records[0]['fields'][3]['label'], '考试科目 / 复试科目')
        self.assertEqual(records[1]['fields'][0]['value'], '')
        self.assertFalse(records[1]['fields'][0]['inherited'])

    def test_multiple_directions_in_one_cell_share_same_quota_group(self):
        table = html.fromstring('''<table><tr><th>专业名称</th><th>研究方向</th><th>招生人数</th></tr>
        <tr><td>计算机</td><td><p>01智能系统</p><p>02媒体计算</p></td><td>24</td></tr></table>''')
        records = table_records(expand_table(table))['records']
        self.assertEqual([r['direction'] for r in records], ['01智能系统', '02媒体计算'])
        self.assertEqual(records[0]['fields'][2]['quota_group'], records[1]['fields'][2]['quota_group'])

    def test_rowspan_zero_stops_at_row_group(self):
        table = html.fromstring('''<table><tbody><tr><td rowspan="0">学院A</td><td>方向1</td></tr><tr><td>方向2</td></tr></tbody>
        <tbody><tr><td>学院B</td><td>方向3</td></tr></tbody></table>''')
        grid = expand_table(table)
        self.assertEqual(grid[1][0]['text'], '学院A')
        self.assertEqual(grid[2][0]['text'], '学院B')

    def test_full_width_trailing_quota_caveat_is_not_a_direction_or_dropped(self):
        table = html.fromstring('''<table><tr><th>专业名称</th><th>研究方向</th><th>招生人数</th></tr>
        <tr><td>计算机</td><td>01方向</td><td>24</td></tr>
        <tr><td colspan="3">备注：<br>1.2027年我校拟招生人数参照2026年计划指标数，实际招生人数以当年教育部下达指标为准。</td></tr></table>''')
        parsed = table_records(expand_table(table))
        self.assertEqual(len(parsed['records']), 1)
        caveat = '2027年我校拟招生人数参照2026年计划指标数，实际招生人数以当年教育部下达指标为准。'
        self.assertEqual(parsed['notices'], ['备注：\n1.' + caveat])
        self.assertEqual(parsed['records'][0]['quota_caveat'], caveat)

    def parse(self, content, **kwargs):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'page.html'
            path.write_text('<html><head><meta charset="utf-8"><title>备用标题</title></head><body>' + content + '</body></html>', encoding='utf-8')
            return parse_page(path, 'https://yz.cuc.edu.cn/2026/page.htm', **kwargs)

    def test_body_scope_title_entities_links_and_publication_date(self):
        parsed = self.parse('''<nav>导航不应入库</nav><h1>2027招生简章</h1><span>发布时间：2026-10-03</span>
        <div class="wp_articlecontent"><div><p>成绩 &gt; 60 且人数 ≤ 20，≥ 10。</p>
        <p>附件<a href="/files/目录.pdf">招生专业目录</a></p></div><script>恶意脚本内容</script></div>''')
        texts = [b['text'] for b in parsed['blocks']]
        self.assertEqual(texts, ['成绩 > 60 且人数 ≤ 20，≥ 10。', '附件招生专业目录'])
        self.assertEqual(parsed['title'], '2027招生简章')
        self.assertEqual(parsed['published_at'], '2026-10-03')
        self.assertEqual(parsed['links'][0]['url'], 'https://yz.cuc.edu.cn/files/目录.pdf')

    def test_separate_tables_and_pages_do_not_leak_faculty_or_year(self):
        table1 = '<table><tr><th>专业名称</th><th>研究方向</th><th>学院（研究院、实验室）</th></tr><tr><td>计算机</td><td>01方向</td><td>学院A</td></tr></table>'
        table2 = '<table><tr><th>专业名称</th><th>研究方向</th><th>学院（研究院、实验室）</th></tr><tr><td>网络安全</td><td>02方向</td><td></td></tr></table>'
        parsed = self.parse('<h1>2027目录</h1><div class="wp_articlecontent">'+table1+table2+'</div>', kind='directory')
        self.assertEqual(parsed['tables'][1]['records'][0]['fields'][2]['value'], '')
        self.assertEqual(parsed['tables'][1]['records'][0]['fields'][2]['source_cell'], 'T2:R2C3')
        again = self.parse('<h1>2026目录</h1><div class="wp_articlecontent">'+table2+'</div>', kind='directory')
        self.assertEqual(again['title'], '2026目录')
        self.assertEqual(again['tables'][0]['records'][0]['fields'][2]['value'], '')

    def test_missing_article_does_not_fallback_to_navigation(self):
        with self.assertRaisesRegex(ValueError, 'wp_articlecontent'):
            self.parse('<h1>新闻</h1><nav>招生目录</nav>')

    def test_doctoral_key_value_scope_is_repeated_for_supervisor_records(self):
        parsed = self.parse('''<h1>0810Z1 信息计算技术</h1><div class="wp_articlecontent">
        <table><tr><td>专业（专业代码）：</td><td>信息计算技术（0810Z1）</td></tr>
        <tr><td>所属学院：</td><td>计算机与网络空间安全学院</td></tr>
        <tr><td>复试科目：</td><td>科技文献阅读与写作</td></tr></table>
        <table><tr><td><strong>导师姓名</strong></td><td><strong>电子邮箱</strong></td><td><strong>招生指标</strong></td><td><strong>研究领域简介</strong></td></tr>
        <tr><td>教师甲</td><td>a@example.edu.cn</td><td>1</td><td>人工智能</td></tr>
        <tr><td>教师乙</td><td>b@example.edu.cn</td><td>1</td><td>网络安全</td></tr></table></div>''', kind='directory')
        self.assertFalse(parsed['tables'][0]['records'])
        records = parsed['tables'][1]['records']
        self.assertEqual(len(records), 2)
        self.assertEqual(records[1]['context_fields'][1]['value'], '计算机与网络空间安全学院')
        self.assertEqual(records[1]['context_fields'][2]['value'], '科技文献阅读与写作')
        self.assertEqual(records[0]['fields'][2]['label'], '招生指标')
        self.assertEqual(records[0]['fields'][2]['quota_group'], 'T2:R2C3')


if __name__ == '__main__':
    unittest.main()
