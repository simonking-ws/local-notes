# -*- coding: utf-8 -*-
"""行距三件套 `widgets.line_spacing()` 的纯逻辑测试。

为什么值得测：Tk 里 `spacing1/2/3` 是三个**互不相关**的选项，少给一个不会报错，
只会让「自动折行的行距」与「回车换行的行距」差一截（用户 2026-11-05 反馈的问题）。
这里锁住三条不变式：

  1. 三个键必须在同一份返回值里（调用方用 `**` 展开，缺一个就静默用默认值 0）；
  2. 正文型（不给 wrap）：spacing2 == spacing1 + spacing3，折行 = 回车；
  3. 标题型（给 wrap）：spacing2 取 spread 的一半，别把两行标题撑成两段。

另外锁住 2026-11-05 的「方案 2」：首行标题不再挂 firstline 压 spacing1，
改由 TEXT_PADY(12) 让出 8px —— 上下都是 8、离顶仍是 20、底部留白也仍是 20。
含一个真实 Tk Text 的 dlineinfo 对比（改版前后逐像素等价），没有图形环境时跳过。

运行：
    python -m unittest discover -s tests -v
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import dpi                                        # noqa: E402
from widgets import TEXT_PADY, line_spacing        # noqa: E402


class LineSpacingTest(unittest.TestCase):

    def test_keys_complete(self):
        sp = line_spacing(2, 2)
        self.assertEqual(set(sp), {"spacing1", "spacing2", "spacing3"})

    def test_body_wrap_equals_newline(self):
        """正文：折行间距 = 上 + 下，与回车换行的段间距一致。"""
        for up, down in ((2, 2), (3, 3), (6, 6), (8, 8)):
            sp = line_spacing(up, down)
            self.assertEqual(sp["spacing2"], sp["spacing1"] + sp["spacing3"],
                             "up=%d down=%d 时折行间距与段间距不等" % (up, down))

    def test_title_wrap_is_half(self):
        """标题：折行只吃一半，1.5 倍行高左右，不能等于 16 的段间距。"""
        sp = line_spacing(8, 8, wrap=8)
        self.assertEqual(sp["spacing2"], sp["spacing1"])
        self.assertLess(sp["spacing2"], sp["spacing1"] + sp["spacing3"])

    def test_zero(self):
        self.assertEqual(line_spacing(0, 0), {"spacing1": 0, "spacing2": 0,
                                             "spacing3": 0})

    def test_scales_with_dpi(self):
        """换缩放比时三个值同步放大，关系不变（px() 各自取整）。"""
        old = dpi.SCALE
        try:
            dpi.SCALE = 1.66
            sp = line_spacing(2, 2)
            self.assertEqual(sp["spacing1"], 3)
            self.assertEqual(sp["spacing3"], 3)
            self.assertEqual(sp["spacing2"], 7)     # px(4) = 6.64 → 7
            self.assertGreaterEqual(sp["spacing2"], sp["spacing1"] + sp["spacing3"] - 1)
            dpi.SCALE = 2.0
            sp2 = line_spacing(2, 2)
            self.assertEqual(sp2["spacing2"], 8)    # px(4) = 8
        finally:
            dpi.SCALE = old


class FirstLineSpacingTest(unittest.TestCase):
    """首行标题：8/8 对称，离顶与底部留白口径都和改版前一致。"""

    def test_heading_spacing_is_symmetric(self):
        sp = line_spacing(8, 8, wrap=8)
        self.assertEqual(sp["spacing1"], sp["spacing3"])

    def test_top_inset_unchanged(self):
        """12 + title.spacing1(8) == 旧版 20 + firstline(0)。"""
        self.assertEqual(TEXT_PADY, dpi.px(12))
        self.assertEqual(TEXT_PADY + line_spacing(8, 8, wrap=8)["spacing1"],
                         dpi.px(20))

    def test_bottom_inset_unchanged(self):
        """状态条与 TEXT_PADY 解耦，但 12 + 8 仍还原出原来的 20。"""
        from app_ui import FOOTER_H, FOOTER_GAP, FOOTER_HOT_H
        self.assertEqual(FOOTER_H, dpi.px(20))
        self.assertEqual(FOOTER_GAP, dpi.px(8))
        self.assertEqual(TEXT_PADY + FOOTER_GAP, FOOTER_H)
        self.assertGreater(FOOTER_HOT_H, FOOTER_H)

    def test_no_firstline_tag_mounted(self):
        from app_actions import ActionsMixin

        class _FakeText(object):
            def __init__(self, empty):
                self.empty = empty
                self.added, self.removed = [], []

            def tag_remove(self, t, a, b):
                self.removed.append(t)

            def tag_add(self, t, a, b):
                self.added.append(t)

            def compare(self, a, op, b):
                return self.empty

        class _Stub(object):
            LINE_TAGS = ("title", "h2", "body", "quote")
            LI_TAG_PREFIX = "li_"
            ensure_title_line = ActionsMixin._ensure_title_line
            ephemeral = ActionsMixin._is_ephemeral_tag

            def __init__(self, empty):
                self.text = _FakeText(empty)

        for empty in (False, True):
            s = _Stub(empty)
            s.ensure_title_line()
            self.assertNotIn("firstline", s.text.added)
            self.assertNotIn("firstline", s.text.removed)
            self.assertEqual(s.text.added.count("title"), 0 if empty else 1)
        # 旧库里万一有残留，仍必须被当成临时标签过滤掉
        self.assertTrue(_Stub(False).ephemeral("firstline"))

    def test_tk_geometry_matches_old_inset(self):
        """真实 Text：改版前后首行字盒顶同 y，新版上下各 8，选中不改布局。"""
        try:
            import tkinter as tk
            import tkinter.font as tkfont
            root = tk.Tk()
            root.withdraw()
            # withdraw 的 root 要先跑完一次几何协商：否则第一个 Text 落在 1x1 的
            # 未布局窗口里，dlineinfo 会返回 height=1 的假数据，对比全是噪声。
            root.geometry("600x400")
            root.update()
        except Exception:
            self.skipTest("tkinter / display unavailable")
        try:
            fam = tkfont.nametofont("TkDefaultFont").actual("family")

            def probe(pady, legacy):
                t = tk.Text(root, bd=0, highlightthickness=0, padx=0,
                            pady=pady, wrap=tk.WORD, font=(fam, 16),
                            width=40, height=10)
                t.pack()
                t.tag_configure("title", font=(fam, 24, "bold"),
                                **line_spacing(8, 8, wrap=8))
                t.tag_configure("body", font=(fam, 16), **line_spacing(2, 2))
                t.insert("1.0", "标题文字\n正文一行")
                t.tag_add("title", "1.0", "1.end")
                t.tag_add("body", "2.0", "2.end")
                if legacy:
                    t.tag_configure("firstline", spacing1=0)
                    t.tag_add("firstline", "1.0", "1.end")
                    t.tag_raise("firstline")
                root.update()
                info = t.dlineinfo("1.0")
                t.tag_add("sel", "1.0", "1.end")
                root.update()
                selected = t.dlineinfo("1.0")
                t.destroy()
                return info, selected

            before, _ = probe(dpi.px(20), True)
            after, after_sel = probe(TEXT_PADY, False)
            # ⚠️ dlineinfo 的 y 是「行盒顶」：spacing1 的空白算在盒子内部，
            # 所以改版后 y 从 20 掉到 12，但文字没动。比基线（y + baseline）。
            # 全部用相对差，不依赖字体族 / 点值换算，换台机器也不会漂。
            self.assertEqual(before[1] + before[4], after[1] + after[4],
                             "首行文字基线必须与改版前一致")
            self.assertEqual(after[4] - before[4], dpi.px(8),
                             "新版行盒上方多出 8px 的 spacing1")
            self.assertEqual(after[3] - before[3], dpi.px(8),
                             "行盒总高同步多 8px（下方 spacing3 不变）")
            self.assertEqual(after[1:], after_sel[1:], "选中不得改变布局")
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
