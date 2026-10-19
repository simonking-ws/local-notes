# -*- coding: utf-8 -*-
"""中文标点归一化的单测（punct.normalize）。

这个文件守的是两条相反的边界，缺一不可：

- 该换的要换：中文输入法下敲出来的 `，` 与 `“”` 是纯脏字符，格式化时顺手清掉；
- 不该动的别动：字符串和注释里的中文标点是**用户数据**，改了就是静默毁数据。

    python tests\\test_punct.py
或：
    python -m unittest discover -s tests -v
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

from punct import normalize


class TestJson(unittest.TestCase):
    def test_chinese_quotes_become_delimiters(self):
        # 中文引号当定界符用：换完之后必须能被 json 解析
        src = '{“name”: “张三”}'
        self.assertEqual(normalize(src, "json"), '{"name": "张三"}')

    def test_chinese_comma_between_members(self):
        src = '{"a": 1，"b": 2}'
        self.assertEqual(normalize(src, "json"), '{"a": 1,"b": 2}')

    def test_chinese_colon_becomes_english(self):
        # 中文冒号夹在键与值之间：不换掉的话 json.loads 直接报错
        src = '{"a"：1, "b"：2}'
        self.assertEqual(normalize(src, "json"), '{"a":1, "b":2}')

    def test_chinese_colon_inside_string_kept(self):
        src = '{"time": "12：30"}'
        self.assertEqual(normalize(src, "json"), src)

    def test_chinese_colon_inside_chinese_quotes_kept(self):
        src = '{“time”: “12：30”}'
        self.assertEqual(normalize(src, "json"), '{"time": "12：30"}')

    def test_fully_chinese_json(self):
        # 全篇中文标点：一次格式化就变成可解析的 JSON
        src = '{“name”：“张三”，“age”：18}'
        self.assertEqual(normalize(src, "json"),
                         '{"name":"张三","age":18}')

    def test_chinese_comma_inside_chinese_quotes_kept(self):
        # 中文引号括起来的是一段文案：里面的逗号属于数据，不能被换成英文
        src = '{“name”: “张三”， “msg”: “你好，世界”}'
        self.assertEqual(normalize(src, "json"),
                         '{"name": "张三", "msg": "你好，世界"}')

    def test_inside_english_string_untouched(self):
        # 已经用英文引号括起来的内容是数据：里面的中文标点一个都不许换
        src = '{"msg": "他说，你好“世界”"}'
        self.assertEqual(normalize(src, "json"), src)

    def test_escaped_quote_in_string(self):
        # 转义引号不能提前收尾，否则后面的中文逗号会被误判成「外面」
        src = '{"a": "x\\"，y"}'
        self.assertEqual(normalize(src, "json"), src)

    def test_empty_string_value(self):
        src = '{"a": "","b": ""}'
        self.assertEqual(normalize(src, "json"), src)


class TestSql(unittest.TestCase):
    def test_comma_in_in_list(self):
        src = "SELECT * FROM t WHERE id IN (81862，82929)"
        self.assertEqual(normalize(src, "sql"),
                         "SELECT * FROM t WHERE id IN (81862,82929)")

    def test_chinese_quotes_around_value(self):
        src = "SELECT * FROM t WHERE name = “张三”"
        self.assertEqual(normalize(src, "sql"),
                         'SELECT * FROM t WHERE name = "张三"')

    def test_single_quotes_around_value(self):
        src = "SELECT * FROM t WHERE name = ‘张三’"
        self.assertEqual(normalize(src, "sql"),
                         "SELECT * FROM t WHERE name = '张三'")

    def test_chinese_comma_inside_chinese_quotes_kept(self):
        src = "SELECT * FROM t WHERE msg = “你好，世界” ， x = 1"
        self.assertEqual(normalize(src, "sql"),
                         'SELECT * FROM t WHERE msg = "你好，世界" , x = 1')

    def test_string_literal_untouched(self):
        src = "SELECT * FROM t WHERE note = 'it''s，ok'"
        self.assertEqual(normalize(src, "sql"), src)

    def test_backtick_identifier_untouched(self):
        src = "SELECT `a，b` FROM t"
        self.assertEqual(normalize(src, "sql"), src)

    def test_line_comment_untouched(self):
        src = "-- 取组织树，只留有效\nSELECT 1"
        self.assertEqual(normalize(src, "sql"), src)

    def test_block_comment_untouched(self):
        src = "SELECT /* 主键，别删 */ 1"
        self.assertEqual(normalize(src, "sql"), src)


class TestBounds(unittest.TestCase):
    def test_other_punct_kept(self):
        # 只认逗号 / 引号 / (JSON) 冒号：全角分号、括号不越界改
        src = "SELECT * FROM t；"
        self.assertEqual(normalize(src, "sql"), src)

    def test_sql_colon_kept(self):
        # 冒号只给 JSON：SQL 的 : 是命名参数 / := 的语法字符，不替用户换
        src = "SELECT * FROM t WHERE a：1"
        self.assertEqual(normalize(src, "sql"), src)

    def test_unknown_lang_is_noop(self):
        src = "a，b"
        self.assertEqual(normalize(src, "python"), src)
        self.assertEqual(normalize(src, ""), src)

    def test_empty(self):
        self.assertEqual(normalize("", "json"), "")

    def test_idempotent(self):
        src = '{“a”：1，“b”: "x，y"}'
        once = normalize(src, "json")
        self.assertEqual(normalize(once, "json"), once)


if __name__ == "__main__":
    unittest.main()
