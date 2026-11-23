# -*- coding: utf-8 -*-
"""行号栏右键菜单的两枚新图标：隐藏行号 / 清除所有标记。

图标是运行期 Canvas 矢量图（不是位图产物），所以直接画出来量图元：
  · 两枚都得有墨、不越界（不然高 DPI 档会被父容器裁掉）
  · 隐藏行号必须仍是「数字栏 + 斜杠」——复用 gutter 的图形，只多一条斜杠；
    退化成一根斜线就看不出关的是哪一列
  · 菜单三项都带 icon —— PopupMenu 的缩进按整张菜单算，漏一项文字就参差

    python tests\\test_gutter_menu_icons.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import tkinter as tk                                    # noqa: E402

import dpi                                              # noqa: E402
dpi.init()

import app_ui                                           # noqa: E402
import widgets                                          # noqa: E402

INK = "#2f7cf6"
SIZES = (16, 20, 24)


class TestGutterMenuIcons(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def _draw(self, name, size):
        cv = tk.Canvas(self.root, width=size, height=size, bg="#ffffff", bd=0,
                       highlightthickness=0)
        widgets._draw_icon(cv, name, size / 2.0, size / 2.0, size, INK)
        return cv

    def test_both_icons_draw_ink(self):
        for name in ("gutter_hide", "mark_clear"):
            cv = self._draw(name, 16)
            self.assertGreaterEqual(len(cv.find_all()), 3,
                                    "%s 图标几乎是空的（只画了 %d 个元素）"
                                    % (name, len(cv.find_all())))

    def test_icons_stay_inside_canvas(self):
        bad = []
        for size in SIZES:
            for name in ("gutter_hide", "mark_clear"):
                cv = self._draw(name, size)
                for item in cv.find_all():
                    box = cv.bbox(item)
                    if box is None:
                        continue
                    x0, y0, x1, y1 = box
                    # 菜单图标画在整张菜单的大画布上，圆头描边外扩 2px 不会真被裁；
                    # 这里贴着图标尺寸画只是抓「差一半格跑到外面」那种手滑。
                    if x0 < -3 or y0 < -3 or x1 > size + 3 or y1 > size + 3:
                        bad.append("%s@%dpx 越界 %s" % (name, size, box))
        self.assertEqual(bad, [], "图标画到画布外了：%s" % "; ".join(bad))

    def test_hide_keeps_gutter_and_adds_slash(self):
        """隐藏行号 = 原 gutter 图元 + 一条斜杠，不能只剩一条斜线。"""
        plain = self._draw("gutter", 16)
        hidden = self._draw("gutter_hide", 16)
        self.assertEqual(len(hidden.find_all()), len(plain.find_all()) + 1,
                         "gutter_hide 应恰好比 gutter 多一条斜杠")

    def test_clear_is_not_a_slash(self):
        """清除标记用「圆点 + ×」，与隐藏行号的斜杠区分开。"""
        clear = self._draw("mark_clear", 16)
        hide = self._draw("gutter_hide", 16)
        self.assertNotEqual([(i, clear.type(i)) for i in clear.find_all()],
                            [(i, hide.type(i)) for i in hide.find_all()])

    def test_menu_assigns_all_icons(self):
        """三项都带图标：缩进按整张菜单算，漏一项文字起点就参差。"""
        calls = []

        class FakeMenu(object):
            def __init__(self, master, pal):
                pass

            def add(self, label, **kw):
                calls.append((label, kw.get("icon")))

            def popup(self, x, y):
                pass

        orig = app_ui.PopupMenu
        app_ui.PopupMenu = FakeMenu
        try:
            fake = type("F", (), {
                "root": None,
                "pal": {},
                "is_readonly": lambda self: False,
                "_gmark_lines": lambda self: [1],
                "_folds": {1: (1, 2)},
                "_gmark_clear": lambda self: None,
                "_fold_unfold_all": lambda self: None,
                "set_line_numbers": lambda self, v: None,
            })()
            event = type("E", (), {"x_root": 10, "y_root": 10})()
            app_ui.UIMixin._gutter_menu(fake, event)
        finally:
            app_ui.PopupMenu = orig

        self.assertEqual(dict(calls), {
            "清除本篇所有标签": "mark_clear",
            "展开所有折叠": "panel",
            "隐藏行号": "gutter_hide",
        })


if __name__ == "__main__":
    unittest.main()
