# -*- coding: utf-8 -*-
"""全局设置弹窗小节图标的测试：每种图形都得画得出来、不越界、尺寸等比。

这些图标是**运行期用 Canvas 画的块面**（不是位图产物），所以能直接建一个
Tk 根窗口把图形画出来量 bbox —— 不需要截屏比对。

值得测的点：
  · 图形非空（改网格坐标时手滑写错一个数，整枚图标会消失，肉眼要翻到设置窗才发现）
  · 不越界（Tk Canvas 不会裁剪到画布外，越界的部分会被父容器盖住或干脆丢掉）
  · 各 DPI 档（14/16/20/24/32）等比缩放后，墨迹占比一致 —— 否则高 DPI 下图标
    会比旁边的文字明显偏大 / 偏小

    python tests\\test_section_icons.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import tkinter as tk                                    # noqa: E402

import dpi                                              # noqa: E402
dpi.init()

from widgets import _draw_section_icon                   # noqa: E402

# settings(齿轮) 现在设置窗里不用了（标题改「字体设置」+字形 A），仍留作回归
KINDS = ("font", "settings", "theme", "toolbar", "tab")
SIZES = (14, 16, 20, 24, 32)
INK = "#2f7cf6"
BG = "#ffffff"


class TestSectionIcons(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def _draw(self, kind, size):
        cv = tk.Canvas(self.root, width=size, height=size, bg=BG, bd=0,
                       highlightthickness=0)
        _draw_section_icon(cv, kind, size / 2.0, size / 2.0, size, INK, BG)
        return cv

    def test_every_kind_draws_something(self):
        for kind in KINDS:
            cv = self._draw(kind, 16)
            self.assertGreaterEqual(len(cv.find_all()), 2,
                                    "%s 图标几乎是空的（只画了 %d 个元素）"
                                    % (kind, len(cv.find_all())))

    def test_icons_stay_inside_canvas(self):
        bad = []
        for size in SIZES:
            for kind in KINDS:
                cv = self._draw(kind, size)
                for item in cv.find_all():
                    box = cv.bbox(item)
                    if box is None:
                        continue
                    x0, y0, x1, y1 = box
                    # Tk 的 bbox 是「含端点」的整数框，允许 1px 取整误差
                    if x0 < -1 or y0 < -1 or x1 > size + 1 or y1 > size + 1:
                        bad.append("%s@%dpx 越界 %s" % (kind, size, box))
        self.assertEqual(bad, [], "图标画到画布外了：%s" % "; ".join(bad))

    def test_ink_scale_is_proportional(self):
        """各尺寸下墨迹框占画布的比例应当一致（图标是等比的，不是某一档特别胖）。"""
        bad = []
        for kind in KINDS:
            ratios = []
            for size in SIZES:
                cv = self._draw(kind, size)
                boxes = [cv.bbox(i) for i in cv.find_all() if cv.bbox(i)]
                x0 = min(b[0] for b in boxes)
                y0 = min(b[1] for b in boxes)
                x1 = max(b[2] for b in boxes)
                y1 = max(b[3] for b in boxes)
                ratios.append(((x1 - x0) / float(size), (y1 - y0) / float(size)))
            ws = [r[0] for r in ratios]
            hs = [r[1] for r in ratios]
            if max(ws) - min(ws) > 0.18 or max(hs) - min(hs) > 0.18:
                bad.append("%s 宽高比随尺寸漂移：宽 %s 高 %s" % (
                    kind, ["%.2f" % v for v in ws], ["%.2f" % v for v in hs]))
            if min(min(ws), min(hs)) < 0.55:
                bad.append("%s 墨迹太小（%.2f），旁边 16px 的图标会显得空"
                           % (kind, min(min(ws), min(hs))))
        self.assertEqual(bad, [], "；".join(bad))

    def test_uses_given_color(self):
        """图标必须吃主题色（换主题时跟着变），别写死某个颜色。"""
        cv = tk.Canvas(self.root, width=16, height=16, bg=BG, bd=0, highlightthickness=0)
        _draw_section_icon(cv, "toolbar", 8, 8, 16, "#e0a24a", BG)
        fills = {cv.itemcget(i, "fill") for i in cv.find_all()}
        self.assertIn("#e0a24a", fills, "工具栏图标没吃到传入的颜色（实际 %s）" % fills)


if __name__ == "__main__":
    unittest.main()
