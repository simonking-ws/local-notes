# -*- coding: utf-8 -*-
"""行号的两条几何判据：该画哪几格（等距刻度）、某个 y 落在哪一格上。

为什么要单独钉：行号是**刻度尺**而不是「每行贴一个号」——
  · 一格的高度恒等于正文行高，空行和正文末尾以下的空白区照样有号；
  · 号与逻辑行号不再一一对应（代码块那一格占两百多像素，底下几行的号就差
    了八九个），点击 / 悬停都必须按**像素区间**查。
这两条一旦改回「按行号查字典」，点击行号就会跳到别的行，而且只有点的时候
才看得出来 —— 界面冒烟测试不容易发现。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))      # 源码在 scripts/ 下

import widgets                                                   # noqa: E402


class SlotsTest(unittest.TestCase):
    UNIT = 29          # 正文 12 号雅黑的行高（含段间距）

    def test_full_page_from_top(self):
        """文档顶端在 y=22、视口高 672：1~23 一格不少（含末尾空白）。"""
        k0, k1 = widgets.gutter_slots(22, self.UNIT, 672)
        self.assertEqual(k0, 0)
        self.assertEqual(k1, 22)

    def test_scrolled_keeps_numbering(self):
        """往下滚 702px：第一格压在视口上沿也要算，号不能从 1 重来。"""
        k0, k1 = widgets.gutter_slots(-702, self.UNIT, 672)
        self.assertEqual(k0, 24)
        self.assertEqual(k1, 47)

    def test_pitch_is_exactly_unit(self):
        v0, unit, h = 22, self.UNIT, 672
        k0, k1 = widgets.gutter_slots(v0, unit, h)
        ys = [v0 + k * unit for k in range(k0, k1 + 1)]
        self.assertEqual({ys[i + 1] - ys[i] for i in range(len(ys) - 1)}, {unit})

    def test_short_note_still_fills_page(self):
        """三行的短笔记也要一直编到画面底部 —— 行号跟内容无关。"""
        k0, k1 = widgets.gutter_slots(22, self.UNIT, 672)
        self.assertGreater(k1 + 1, 20)

    def test_bad_unit_does_not_hang(self):
        """行高量成 0（还没映射）时不能死循环 / 除零。"""
        k0, k1 = widgets.gutter_slots(0, 0, 100)
        self.assertLessEqual(k0, k1)

    def test_k1_never_before_k0(self):
        k0, k1 = widgets.gutter_slots(500, 29, 100)      # 整列都在视口下方
        self.assertGreaterEqual(k1, k0)


class HitAtTest(unittest.TestCase):
    def setUp(self):
        # 号是刻度数出来的：屏幕上写 6 的那一格，逻辑行仍是第 5 行（代码块所在行）
        self.hits = [(0, 29, 1, "i1"), (160, 178, 5, "i5"), (178, 196, 5, "i6"),
                     (196, 214, 5, "i7"), (214, 243, 7, "i15")]

    def test_hit_returns_target_line(self):
        self.assertEqual(widgets.gutter_hit_at(self.hits, 5), (1, "i1", False))
        self.assertEqual(widgets.gutter_hit_at(self.hits, 185), (5, "i6", False))
        self.assertEqual(widgets.gutter_hit_at(self.hits, 230), (7, "i15", False))

    def test_boundary_is_half_open(self):
        """区间是 [top, bottom)：178 不算上一格，落到下一格。"""
        self.assertEqual(widgets.gutter_hit_at(self.hits, 178), (5, "i6", False))
        self.assertEqual(widgets.gutter_hit_at(self.hits, 214), (7, "i15", False))

    def test_padding_flag_is_passed_through(self):
        """正文下方那一格的号是虚号：hit 要把它标出来，点击才不会跑去标末行。"""
        hits = [(240, 269, 12, "p12", True), (269, 298, 12, "p13", True)]
        self.assertEqual(widgets.gutter_hit_at(hits, 250), (12, "p12", True))
        # 旧的四元组登记仍当实号处理（兼容历史数据 / 老探针）
        self.assertEqual(widgets.gutter_hit_at([(0, 29, 1, "i1")], 5), (1, "i1", False))

    def test_gap_returns_none(self):
        self.assertEqual(widgets.gutter_hit_at(self.hits, 100), (None, None, False))
        self.assertEqual(widgets.gutter_hit_at(self.hits, 999), (None, None, False))

    def test_empty(self):
        self.assertEqual(widgets.gutter_hit_at([], 5), (None, None, False))
        self.assertEqual(widgets.gutter_hit_at(None, 5), (None, None, False))


class PaintCellsTest(unittest.TestCase):
    """徽标挂哪一格：一行占多格时只挂**行首格**（见 widgets.gutter_paint_cells）。

    号本身是每格都画的（号 = 格序号），这个函数只决定 gmark 徽标 / 折叠箭头
    画在哪里 —— 每格都挂的话，标记一行会让整块旁边亮成一片。
    """

    def test_normal_lines_all_marked(self):
        self.assertEqual(widgets.gutter_paint_cells([(1, False), (2, False), (3, False)]),
                         [True, True, True])

    def test_tall_title_marks_first_slot_only(self):
        """首行标题 44px 吃掉两格：徽标只挂第一格（点号 1，圈就在 1 旁边）。"""
        self.assertEqual(
            widgets.gutter_paint_cells([(1, False), (1, False), (2, False), (3, False)]),
            [True, False, True, True])

    def test_code_block_span(self):
        """内嵌代码块占五六格：徽标只在行首那一格，中间不重复画圈。"""
        cells = [(4, False)] + [(5, False)] * 5 + [(6, False)]
        self.assertEqual(widgets.gutter_paint_cells(cells),
                         [True, True, False, False, False, False, True])

    def test_padding_keeps_every_slot(self):
        """正文下方空白格每格都算行首（号接着数下去），只是不挂徽标。"""
        self.assertEqual(widgets.gutter_paint_cells([(3, False), (3, True), (3, True)]),
                         [True, True, True])


class SlotNumberTest(unittest.TestCase):
    """B 方案：号 = 格序号 + 1，一格一个，滚动只改起点，绝不跳号 / 重号。"""

    def test_numbers_are_consecutive(self):
        k0, k1 = widgets.gutter_slots(0, 29, 290)
        self.assertEqual(k0, 0)
        self.assertEqual(k1, 9)                      # 290/29 → 10 格
        self.assertEqual([k + 1 for k in range(k0, k1 + 1)], list(range(1, 11)))

    def test_scrolled_numbers_start_from_real_slot(self):
        """往下滚 3 格：号从 4 起，仍然连续 —— 标题占两格也不会出现空号。"""
        k0, k1 = widgets.gutter_slots(-87, 29, 290)
        self.assertEqual(k0, 3)
        nums = [k + 1 for k in range(k0, k1 + 1)]
        self.assertEqual(nums[0], 4)
        self.assertEqual(nums, list(range(4, nums[-1] + 1)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
