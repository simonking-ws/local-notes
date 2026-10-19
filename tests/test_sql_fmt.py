# -*- coding: utf-8 -*-
"""SQL 关键词格式化的单测。

核心契约只有一条：用户给的那段样例，格式化后必须一字不差地变成他要的样子。
其余用例守的是「不该断的地方别断」—— 括号里、字符串里、BETWEEN … AND 里。
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

from sql_fmt import format_sql, _tokenize, _pair_up


# 用户给的原始语句（换行位置完全不由人，就是日志里抄出来的那种）
SAMPLE_IN = """SELECT o.* FROM sys_organization o
WHERE o.main_engine_plant_id
IN (81862，82929) AND o.`status`=1
AND o.consociation_status=2 AND o.management_status=1 AND o.main_engine_plant_code IS NOT NULL
AND EXISTS (SELECT 1 FROM sys_org_brank b WHERE b.org_id = o.id AND b.status=3)；"""

SAMPLE_OUT = """SELECT
    o.*
FROM sys_organization o
WHERE o.main_engine_plant_id IN (81862，82929)
AND o.`status`=1
AND o.consociation_status=2
AND o.management_status=1
AND o.main_engine_plant_code IS NOT NULL
AND EXISTS (SELECT 1 FROM sys_org_brank b WHERE b.org_id = o.id AND b.status=3)；"""


class TestSample(unittest.TestCase):
    def test_user_sample_exact(self):
        self.assertEqual(format_sql(SAMPLE_IN), SAMPLE_OUT)

    def test_idempotent(self):
        once = format_sql(SAMPLE_IN)
        self.assertEqual(format_sql(once), once)
        self.assertEqual(format_sql(format_sql(SAMPLE_OUT)), SAMPLE_OUT)

    def test_crlf_and_tabs(self):
        src = SAMPLE_IN.replace("\n", "\r\n").replace(" ", "\t")
        self.assertEqual(format_sql(src), SAMPLE_OUT)


class TestKeepTogether(unittest.TestCase):
    def test_subquery_stays_on_one_line(self):
        out = format_sql("select * from t where id in (select id from u where a=1 and b=2)")
        self.assertIn("where id in (select id from u where a=1 and b=2)", out)
        self.assertEqual(len(out.split("\n")), 4)       # select / 字段 / from / where

    def test_values_list_not_broken(self):
        out = format_sql("SELECT * FROM t WHERE a IN (1, 2, 3) AND b = 4")
        self.assertIn("IN (1, 2, 3)", out)

    def test_string_literal_protected(self):
        out = format_sql("SELECT * FROM t WHERE name = 'A AND B WHERE C' AND x = 1")
        self.assertIn("'A AND B WHERE C'", out)
        # 字符串里那个 AND 不该被断成行首，真正的连接词只有一个
        self.assertEqual([ln for ln in out.split("\n") if ln.startswith("AND")],
                         ["AND x = 1"])

    def test_quoted_identifiers(self):
        out = format_sql("SELECT `where`, \"and\" FROM t")
        self.assertEqual(out.split("\n")[1:3], ["    `where`,", "    \"and\""])

    def test_between_and(self):
        out = format_sql("SELECT * FROM t WHERE d BETWEEN '2024-01-01' AND '2024-12-31' AND x = 1")
        lines = out.split("\n")
        self.assertEqual(lines[-2], "WHERE d BETWEEN '2024-01-01' AND '2024-12-31'")
        self.assertEqual(lines[-1], "AND x = 1")

    def test_escaped_quote(self):
        out = format_sql("SELECT * FROM t WHERE note = 'it''s AND ok'")
        self.assertIn("'it''s AND ok'", out)


class TestBreakPoints(unittest.TestCase):
    def test_from_on_own_line_and_fields_indented(self):
        # 用户定的形态：SELECT 独占一行、每个字段各占一行并退四格、FROM 自己一行
        out = format_sql("select a, b from t where x = 1")
        self.assertEqual(out.split("\n"), [
            "select",
            "    a,",
            "    b",
            "from t",
            "where x = 1",
        ])

    def test_one_field_per_line_keeps_paren_commas(self):
        # 括号里的逗号不能拆：函数参数、子查询字段、IN 值表都靠它保住
        out = format_sql("select id, count(a, b) as n, (select x, y from u) from t")
        self.assertEqual(out.split("\n"), [
            "select",
            "    id,",
            "    count(a, b) as n,",
            "    (select x, y from u)",
            "from t",
        ])

    def test_group_order_having_limit(self):
        out = format_sql("select a, count(*) from t where x = 1 group by a order by 2 desc limit 5")
        self.assertEqual(out.split("\n"), [
            "select",
            "    a,",
            "    count(*)",
            "from t",
            "where x = 1",
            "group by a",
            "order by 2 desc",
            "limit 5",
        ])

    def test_join_breaks_before_join_word(self):
        out = format_sql("select * from a left join b on a.id = b.a_id and b.s = 1")
        self.assertEqual(out.split("\n"), [
            "select",
            "    *",
            "from a",
            "left join b on a.id = b.a_id",
            "and b.s = 1",
        ])

    def test_union_all_select(self):
        out = format_sql("select id from a union all select id from b")
        self.assertEqual(out.split("\n"), [
            "select", "    id", "from a", "union all",
            "select", "    id", "from b",
        ])

    def test_select_distinct(self):
        out = format_sql("select distinct a, b from t")
        self.assertEqual(out.split("\n"),
                         ["select distinct", "    a,", "    b", "from t"])

    def test_update_insert_delete(self):
        out = format_sql("update t set a = 1 where id = 2")
        self.assertEqual(out.split("\n"), ["update t", "set a = 1", "where id = 2"])
        out = format_sql("insert into t (a, b) values (1, 2)")
        self.assertEqual(out.split("\n"), ["insert into t (a, b)", "values (1, 2)"])
        out = format_sql("delete from t where id = 3")
        # `DELETE FROM` 是一个整体，不该被拆成两行
        self.assertEqual(out.split("\n"), ["delete from t", "where id = 3"])

    def test_for_update_not_split(self):
        # `FOR UPDATE` 是行锁子句，拆开会让 UPDATE 单独成行，像另起一条语句
        out = format_sql("SELECT a FROM t WHERE id = 1 FOR UPDATE")
        self.assertEqual(out.split("\n"), [
            "SELECT", "    a", "FROM t", "WHERE id = 1 FOR UPDATE",
        ])

    def test_leading_keyword_not_pushed_down(self):
        # 开头就是 WHERE 时不该留出一行空白
        out = format_sql("where x = 1 and y = 2")
        self.assertEqual(out.split("\n"), ["where x = 1", "and y = 2"])

    def test_multi_statement_blank_line(self):
        out = format_sql("select 1; select 2 from dual")
        self.assertEqual(out.split("\n"),
                         ["select", "    1;", "", "select", "    2", "from dual"])


class TestSpacing(unittest.TestCase):
    def test_original_spacing_survives(self):
        out = format_sql("SELECT o.`status`=1, b.x = 2 FROM t")
        self.assertIn("o.`status`=1", out)
        self.assertIn("b.x = 2", out)

    def test_run_together_tokens(self):
        out = format_sql("SELECT a.b,c.d FROM t")
        self.assertEqual(out.split("\n")[1:3], ["    a.b,", "    c.d"])

    def test_squeeze_multiple_spaces(self):
        out = format_sql("SELECT    a     FROM    t")
        self.assertEqual(out.split("\n")[1:], ["    a", "FROM t"])


class TestComments(unittest.TestCase):
    def test_line_comment_alone(self):
        out = format_sql("-- 取组织树\nSELECT id FROM t WHERE a = 1 -- 只要有效的\nAND b = 2")
        self.assertEqual(out.split("\n"), [
            "-- 取组织树", "SELECT", "    id", "FROM t",
            "WHERE a = 1", "-- 只要有效的", "AND b = 2",
        ])

    def test_inline_block_comment(self):
        # 单行块注释贴着后一个字段走，不单独成行
        out = format_sql("SELECT o.id, /* 主键 */ o.name FROM t")
        self.assertEqual(out.split("\n")[1:3], ["    o.id,", "    /* 主键 */ o.name"])

    def test_multiline_block_comment_stands_alone(self):
        out = format_sql("SELECT a, /* x\ny */ b FROM t")
        self.assertEqual(out.split("\n")[1], "    a,")
        self.assertIn("/* x\ny */", out)


class TestEdge(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(format_sql(""), "")
        self.assertEqual(format_sql("   \n\t "), "")

    def test_single_word(self):
        self.assertEqual(format_sql("SELECT"), "SELECT")

    def test_unbalanced_paren_does_not_crash(self):
        out = format_sql("SELECT * FROM (t WHERE a = 1 AND b = 2")
        self.assertIn("SELECT", out)

    def test_trailing_semicolon_kept(self):
        self.assertTrue(format_sql("select 1;").endswith(";"))


class TestTokenizer(unittest.TestCase):
    def test_kinds(self):
        toks = _tokenize("SELECT `a` , 'b' 12 <= 3")
        kinds = [t[0] for t in toks]
        self.assertEqual(kinds, ["word", "ident", "punct", "str", "num", "punct", "num"])

    def test_space_before_flag(self):
        toks = _tokenize("a.b c")
        self.assertEqual([t[2] for t in toks], [False, False, False, True])

    def test_pair_up_join(self):
        toks = _pair_up(_tokenize("left join b"))
        self.assertEqual([t[1] for t in toks], ["left join", "b"])

    def test_pair_up_leaves_plain_join(self):
        toks = _pair_up(_tokenize("join b"))
        self.assertEqual([t[1] for t in toks], ["join", "b"])


if __name__ == "__main__":
    unittest.main()
