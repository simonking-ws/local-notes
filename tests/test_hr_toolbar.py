# -*- coding: utf-8 -*-
"""插入分割线入口：工具栏常驻、⋯ 菜单不再重复。

用户 2026-11-17 定的：把分割线做成工具栏按钮，同时从 ⋯ 更多菜单里删掉。
这里纯逻辑断言（不建第二个 tk.Tk，见项目红线）。
"""
import os
import sys
import unittest
from types import MethodType

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import tkinter as tk                                    # noqa: E402
import widgets                                          # noqa: E402
import app_ui                                           # noqa: E402


class FakeMenu:
    def __init__(self):
        self.items = []

    def add(self, label, **kw):
        self.items.append(label)

    def add_separator(self):
        pass

    def popup(self, *a, **k):
        pass


class HrToolbarTest(unittest.TestCase):
    def test_registered_in_toolbar(self):
        ids = [i[0] for i in app_ui.UIMixin.TOOL_ITEMS]
        self.assertIn("hr", ids)
        self.assertEqual(app_ui.UIMixin.TOOL_SHORT_NAMES["hr"], "插入分割线")
        self.assertIn("hr", app_ui.UIMixin.READONLY_LOCKED,
                      "分割线改正文，只读笔记里要禁用")

    def test_not_in_more_menu(self):
        fake = type("F", (), {
            "root": None,
            "pal": {},
            "is_readonly": lambda self: False,
            "doodle_active": lambda self: False,
            "toggle_doodle": lambda self: None,
            "_folds": [],
            "_fold_unfold_all": lambda self: None,
            "reset_format": lambda self: None,
            "export_note": lambda self, fmt: None,
            "_line_numbers_on": lambda self: True,
            "set_line_numbers": lambda self, v: None,
            "_set_toolbar_collapsed": lambda self, v: None,
            "_popup_under": lambda self, btn, m: None,
        })()
        fake._open_more_menu = MethodType(app_ui.UIMixin._open_more_menu, fake)
        orig = app_ui.PopupMenu
        menu = FakeMenu()
        try:
            app_ui.PopupMenu = lambda *a, **k: menu
            fake._open_more_menu()
        finally:
            app_ui.PopupMenu = orig
        self.assertFalse(any("分割线" in x for x in menu.items),
                         "⋯ 菜单不该再有插入分割线")


class HrIconGeometryTest(unittest.TestCase):
    """hr 图标：整数矩形拼、保留上一版的 2 行线宽、墨迹中线精确对齐按钮中心。

    ⚠️ Tk 矩形 (y0, y1) 按**半开区间**点亮 y0..y1-1 —— 实测 13-15 点亮
    13、14 两行。所以判断「中线对齐」必须看点亮行的墨迹中心 (y0+y1-1)/2，
    不能用 (y0+y1)/2（那是半开边界的中点，恒定偏 .5）。

    上一版 ymid=round(cy)=14（银行家舍入 round(14.5)=14），点亮 13、14，
    墨迹中心 13.5，比按钮中心 14.5 高 1px。曾试图把 2 行线压成 1 行来对齐，
    用户否掉了（线细了一半）——正确做法是保持线宽、整体下移一行。
    """

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    @staticmethod
    def _ink_center(box):
        """矩形实际点亮行的墨迹中心（半开区间 y0..y1-1）。"""
        return (box[1] + box[3] - 1) / 2.0

    def _rects(self, size=16, cx=None, cy=None):
        cv = tk.Canvas(self.root, width=size, height=size, bg="#ffffff", bd=0,
                       highlightthickness=0)
        cx = size / 2.0 if cx is None else cx
        cy = size / 2.0 if cy is None else cy
        widgets._draw_icon(cv, "hr", cx, cy, size, "#222222")
        rects = [cv.coords(i) for i in cv.find_all() if cv.type(i) == "rectangle"]
        cv.destroy()
        return rects

    def test_three_integer_rectangles(self):
        rects = self._rects(cx=16, cy=14.5)
        self.assertEqual(len(rects), 3, "hr 应该是三条整数矩形横线")
        for box in rects:
            for v in box:
                self.assertEqual(v, int(v), "坐标必须是整数（禁浮点圆头描边）")

    def test_line_width_stays_two_rows(self):
        # ⚠️ 上一版线宽是 2 行（矩形 y1-y0=2）。有人为对齐把线压成 1 行，
        # 用户明确否掉「图标向下移动半个像素」时线不能变细。这条钉死线宽。
        for box in self._rects(cx=16, cy=14.5):
            self.assertEqual(box[3] - box[1], 2,
                             "每条线必须点亮 2 行，不能为对齐把线压成 1 行")

    def test_vertical_symmetry_and_centered(self):
        # 真实按钮 cy=14.5：三条线点亮 9,10 / 14,15 / 19,20，
        # 墨迹中心 9.5 / 14.5 / 19.5 —— 中线精确落在按钮中心，上下等距。
        rects = sorted(self._rects(cx=16, cy=14.5), key=lambda b: b[1])
        centers = [self._ink_center(b) for b in rects]
        self.assertEqual(centers, [9.5, 14.5, 19.5],
                         "墨迹中心必须是 cy-d / cy / cy+d，中线精确落在 14.5")
        self.assertEqual(centers[1] - centers[0], centers[2] - centers[1],
                         "上下间距必须相等")
        self.assertEqual(centers[1], 14.5, "中线必须落在按钮中心 14.5，不能偏高")

    def test_matches_neighbor_icon_center(self):
        # 真实按钮高 29（cy=14.5），同排 bullet 的墨迹中点也在 cy。
        # 直接对比两者，防止只把 hr 搬到某个「看着差不多」的整数行。
        cv = tk.Canvas(self.root, width=32, height=29, bg="#ffffff", bd=0,
                       highlightthickness=0)
        widgets._draw_icon(cv, "hr", 16, 14.5, 16, "#222222")
        hr_centers = [self._ink_center(cv.coords(i))
                      for i in cv.find_all() if cv.type(i) == "rectangle"]
        cv.delete("all")
        widgets._draw_icon(cv, "bullet", 16, 14.5, 16, "#222222")
        dots = [cv.coords(i) for i in cv.find_all() if cv.type(i) == "oval"]
        bullet_mid = ((min(b[1] for b in dots) + max(b[3] for b in dots)) / 2.0)
        cv.destroy()
        self.assertEqual(hr_centers[1], bullet_mid,
                         "hr 中线与同排图标墨迹中线必须重合，不能偏高或偏低")


if __name__ == "__main__":
    unittest.main(verbosity=2)
