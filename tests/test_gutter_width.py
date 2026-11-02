# -*- coding: utf-8 -*-
"""行号栏宽度：不跟内容走。

为什么值得单独测：这条规则的全部价值就在「不变」两个字上 —— 9→10、99→100、
999→1000 每跨一档就变宽一次，正文会跟着右挪一下，读到一半被推一下很难受。
写成常量后很容易被后人「优化」回按内容算，所以钉住。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))      # 源码在 scripts/ 下

import widgets                                                   # noqa: E402

UNIT = 8          # 一个数字的像素宽（100% 缩放下 10pt 雅黑量出来约 7~8）


class GutterWidthTest(unittest.TestCase):
    def test_stable_across_line_counts(self):
        """30 行、150 行、999 行——同一个宽度。这是本需求的核心。"""
        widths = {widgets.gutter_width(UNIT, n) for n in (1, 9, 10, 30, 99, 100,
                                                         150, 999, 1000, 9999)}
        self.assertEqual(len(widths), 1, "行数变了栏宽就跟着变：%s" % sorted(widths))

    def test_reserves_four_digits(self):
        """1 行也按 4 位数留：留白换稳定，是有意的取舍。"""
        self.assertEqual(widgets.gutter_width(UNIT, 1),
                         UNIT * widgets.GUTTER_DIGITS + 13)

    def test_widens_only_past_budget(self):
        """真超过 4 位才让位（再不让就要把数字裁掉，那比挪一下更糟）。"""
        base = widgets.gutter_width(UNIT, 1)
        self.assertEqual(widgets.gutter_width(UNIT, 9999), base)
        self.assertEqual(widgets.gutter_width(UNIT, 10000), base + UNIT)

    def test_scales_with_font(self):
        """字号变了要跟着变：栏宽 = 位数 × 单字宽 + 固定留白。"""
        self.assertEqual(widgets.gutter_width(16, 1) - widgets.gutter_width(8, 1), 32)

    def test_bad_input_does_not_explode(self):
        """总行数拿不到时（0 / None 边角）按 1 算，不能抛。"""
        self.assertEqual(widgets.gutter_width(UNIT, 0), widgets.gutter_width(UNIT, 1))
        self.assertEqual(widgets.gutter_width(UNIT, 1), widgets.gutter_width(UNIT, 1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
