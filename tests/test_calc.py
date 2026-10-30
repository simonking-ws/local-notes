# -*- coding: utf-8 -*-
"""`$` 公式自动计算（calc.expand）的单测。

两类用例泾渭分明：

- **该算的**：`$5+6=` + 空格 → 11，四则 / 括号 / 幂 / 取模 / 三角函数都要对；
- **不该算的**：没有 `$`、`$` 后跟汉字或字母、除零、tan90、位置不对 ——
  一律返回 None。这一半比前一半更重要：多算一次就是替用户改了正文。

    python tests\\test_calc.py
或：
    python -m unittest discover -s tests -v
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

from calc import expand, evaluate, format_number


def run(line):
    """把 line 当成「刚敲完末尾的空格」，返回插入的文本（不算则 None）。"""
    return expand(line, len(line) - 1)


class TestArithmetic(unittest.TestCase):
    def test_plus(self):
        # (结果, 插入位置, `$` 的列号) —— `$` 在行首，插入点在空格之后
        self.assertEqual(run("$5+6= "), ("11", 6, 0))

    def test_decimal_keeps_precision(self):
        # 0.1+0.2 用 float 会出 0.30000000000000004，Decimal 走的是真十进制
        self.assertEqual(run("$0.1+0.2= ")[0], "0.3")

    def test_two_decimals_max(self):
        self.assertEqual(run("$10/3= ")[0], "3.33")
        self.assertEqual(run("$10/4= ")[0], "2.5")

    def test_parentheses(self):
        self.assertEqual(run("$(1+2)*3= ")[0], "9")
        self.assertEqual(run("$2*(3+(4-1))= ")[0], "12")

    def test_power(self):
        self.assertEqual(run("$2^10= ")[0], "1024")
        self.assertEqual(run("$2**10= ")[0], "1024")
        self.assertEqual(run("$2^0.5= ")[0], "1.41")

    def test_mod(self):
        self.assertEqual(run("$7%3= ")[0], "1")

    def test_unary_minus(self):
        self.assertEqual(run("$-5+3= ")[0], "-2")

    def test_negative_result_not_zero(self):
        self.assertEqual(run("$1-2= ")[0], "-1")


class TestMarkColumn(unittest.TestCase):
    """第三个返回值是 `$` 的列号 —— 界面层拿它把触发符删掉。"""

    def test_mark_at_line_start(self):
        self.assertEqual(run("$5+6= ")[2], 0)

    def test_mark_after_text(self):
        # 行中间也能算：删掉 `$` 之后是 `价格 5+6= 11`
        self.assertEqual(run("价格 $5+6= ")[2], 3)

    def test_mark_always_left_of_insert_point(self):
        # 删 `$` 会让它右边的列号全部左移一格，界面层据此算插入点
        for line in ("$5+6= ", "$ 5+6= ", "$(1+2)*3= "):
            _, at, mark = run(line)
            self.assertLess(mark, at)

    def test_no_recalc_after_mark_removed(self):
        # 模拟界面层那次编辑：结果落地、`$` 消失
        line = "$5+6= "
        val, at, mark = expand(line, len(line) - 1)
        after = line[:mark] + line[mark + 1:at] + val + line[at:]
        self.assertEqual(after, "5+6= 11")
        # 这行已经不是公式了，再敲空格不该冒出第二个值
        self.assertIsNone(expand(after + " ", len(after)))


class TestTrig(unittest.TestCase):
    def test_tan_without_parens(self):
        self.assertEqual(run("$tan30= ")[0], "0.58")

    def test_tan_with_parens(self):
        self.assertEqual(run("$tan(45)= ")[0], "1")

    def test_tan_with_space(self):
        self.assertEqual(run("$tan 30= ")[0], "0.58")

    def test_sin_cos(self):
        self.assertEqual(run("$sin30= ")[0], "0.5")
        self.assertEqual(run("$cos60= ")[0], "0.5")

    def test_tan90_undefined(self):
        # tan90 是无穷大，不是 1.6e16；宁可不算
        self.assertIsNone(run("$tan90= "))

    def test_trig_in_expression(self):
        self.assertEqual(run("$2*tan45= ")[0], "2")


class TestMustNotRun(unittest.TestCase):
    def test_no_dollar(self):
        self.assertIsNone(run("5+6= "))

    def test_dollar_then_chinese(self):
        self.assertIsNone(run("$合计= "))
        self.assertIsNone(run("$总计 5+6= "))

    def test_dollar_then_letter(self):
        # 给 markdown 的 $…$ 留后路：$x^2= / $abc= 都不算
        self.assertIsNone(run("$abc= "))
        self.assertIsNone(run("$x^2= "))

    def test_div_by_zero(self):
        self.assertIsNone(run("$1/0= "))

    def test_broken_syntax(self):
        self.assertIsNone(run("$1+= "))
        self.assertIsNone(run("$1+*2= "))

    def test_plain_number(self):
        # 没有运算符就不是「算一下」，是普通文字
        self.assertIsNone(run("$5= "))

    def test_too_long(self):
        self.assertIsNone(run("$" + "1+" * 50 + "1= "))

    def test_not_right_after_equals(self):
        self.assertIsNone(run("$5+6= 11"))       # 已经算过了
        self.assertIsNone(run("$5+6= 11 "))      # 再敲空格也不该重算

    def test_caret_not_after_space(self):
        # 位置参数指的是「确认空格」的那一格；指到别处一律不算
        self.assertIsNone(expand("$5+6= ", 0))
        self.assertIsNone(expand("$5+6= ", None))


class TestFullWidth(unittest.TestCase):
    def test_fullwidth_operators(self):
        self.assertEqual(run("$5＋6= ")[0], "11")
        self.assertEqual(run("$5×6= ")[0], "30")
        self.assertEqual(run("$8÷2= ")[0], "4")
        self.assertEqual(run("$（1+2）×3= ")[0], "9")


class TestFormat(unittest.TestCase):
    def test_strip_trailing_zeros(self):
        self.assertEqual(format_number(evaluate("10/2")), "5")
        self.assertEqual(format_number(evaluate("1/4")), "0.25")

    def test_round_half_up(self):
        self.assertEqual(format_number(evaluate("2/3")), "0.67")
        self.assertEqual(format_number(evaluate("1.005")), "1.01")

    def test_tiny_value_becomes_zero(self):
        self.assertEqual(format_number(evaluate("1/1000")), "0")

    def test_huge_magnitude_refused(self):
        self.assertIsNone(evaluate("9^999"))
        self.assertIsNone(evaluate("1e30"))


if __name__ == "__main__":
    unittest.main()
