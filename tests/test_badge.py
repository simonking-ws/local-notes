# -*- coding: utf-8 -*-
"""文件夹条数徽章的两条纯逻辑：显示什么文字、画布该多大。

为什么要单独钉：这两条决定了「什么算圆、什么算胶囊」，而它们只有跨过
99 那条线才看得出差别 —— 库里真有一百多条笔记的文件夹很少，靠界面冒烟
根本碰不到「99+」这一支。等哪天有人把 `>` 写成 `>=`，只有这里会红。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))      # 源码在 scripts/ 下

from widgets import badge_text, badge_size             # noqa: E402


class BadgeTextTest(unittest.TestCase):
    def test_under_cap_is_raw(self):
        self.assertEqual(badge_text(0), "0")
        self.assertEqual(badge_text(7), "7")
        self.assertEqual(badge_text(42), "42")

    def test_cap_itself_is_not_plused(self):
        """99 还是 99 ——「超过」才是 99+，等于不算。"""
        self.assertEqual(badge_text(99), "99")

    def test_over_cap_collapses(self):
        self.assertEqual(badge_text(100), "99+")
        self.assertEqual(badge_text(1234), "99+")
        self.assertEqual(badge_text(10 ** 6), "99+")

    def test_custom_cap(self):
        self.assertEqual(badge_text(9, cap=9), "9")
        self.assertEqual(badge_text(10, cap=9), "9+")


class BadgeSizeTest(unittest.TestCase):
    LH = 19            # 10pt 雅黑的行高
    PAD = 6            # 文字左右合计留白

    def test_single_digit_is_a_circle(self):
        """一位数的文字宽度远小于高度 → 宽高相等 = 正圆。"""
        w, h = badge_size(8, self.LH, self.PAD, 2)
        self.assertEqual((w, h), (h, h))
        self.assertEqual(h, self.LH + 2)

    def test_two_digits_stay_round(self):
        """两位数也只比高度宽一点点（22 vs 21），看起来仍是圆而不是胶囊。"""
        w, h = badge_size(16, self.LH, self.PAD, 2)
        self.assertEqual(h, 21)
        self.assertEqual(w, 22)
        self.assertLessEqual(w - h, 1)

    def test_three_chars_grow_into_pill(self):
        w, h = badge_size(26, self.LH, self.PAD, 2)
        self.assertEqual(h, 21)
        self.assertEqual(w, 32)
        self.assertGreater(w, h)          # 比高度宽 → 走胶囊那一支

    def test_grow_and_pad_are_honoured(self):
        """高 DPI 下 pad / grow 是乘过系数的，尺寸要跟着变而不是写死。"""
        w, h = badge_size(16, 31, 10, 3)
        self.assertEqual(h, 34)
        self.assertEqual(w, 34)           # 16+10=26 < 34 → 仍是圆
        w2, _ = badge_size(40, 31, 10, 3)
        self.assertEqual(w2, 50)


if __name__ == "__main__":
    unittest.main()
