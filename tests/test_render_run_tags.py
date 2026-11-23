# -*- coding: utf-8 -*-
"""回归：`_render_content` 必须给**整段 run** 打 tag，不能只挂末字符。

2026-11-23 发现：外部备份里的正文 run 是合并过的整段
（如 ["t", "技术博客", ["h2"]]），旧写法 `tag_add(tg, "insert-1c", "insert")`
只覆盖最后插入的一个字符 → 「技术博客」只有「客」是小标题，前面掉成正文。
程序自己 serialize() 的 run 是逐字符的，旧写法碰巧正确，所以一直没暴露。

这里用真实 Tk Text 跑 `_render_content`，断言两种粒度（整段 / 逐字符）下
h2 都覆盖完整文字，锁死「起点 → END」的整段打法。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from app_ui import UIMixin                       # noqa: E402


class _Stub(object):
    """够 `_render_content` 跑纯文本 run 的最小外壳；辅助方法全部 no-op。

    `render` 取未绑定方法，实例访问时自动绑定 self，等价于真实 app 的调用。
    """

    render = UIMixin._render_content

    def __init__(self, text):
        self.text = text
        self._imgs = {}
        self._alarms = {}

    def _sync_todo_tags(self):
        pass

    def _sync_todo_done(self):
        pass

    def _normalize_quote_spans(self):
        pass

    def _ensure_title_line(self):
        pass

    def _restyle_links(self):
        pass

    def _sync_list_indent(self):
        pass

    def _after_idle(self, fn):
        pass

    def _alarm_bind(self):
        pass

    def _gmark_normalize(self):
        pass


class RenderRunTagTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import tkinter as tk
            cls._tk = tk
            cls.root = tk.Tk()
            cls.root.withdraw()
        except Exception:
            raise unittest.SkipTest("tkinter / display unavailable")

    @classmethod
    def tearDownClass(cls):
        try:
            cls.root.destroy()
        except Exception:
            pass

    def _render(self, runs):
        t = self._tk.Text(self.root)
        _Stub(t).render(runs)
        return t

    @staticmethod
    def _covered(t, tag):
        rng = t.tag_ranges(tag)
        return [t.get(a, b) for a, b in zip(rng[0::2], rng[1::2])]

    def test_merged_run_covers_whole_text(self):
        """整段 run（外部备份形态）：h2 必须覆盖「技术博客」「小说」全文。

        换行是独立的无 tag run（备份真实形态），否则相邻同 tag 区间会被 Tk 合并。
        """
        t = self._render([["t", "技术博客", ["h2"]], ["t", "\n", []],
                          ["t", "小说", ["h2"]]])
        try:
            self.assertEqual(self._covered(t, "h2"), ["技术博客", "小说"])
        finally:
            t.destroy()

    def test_per_char_runs_still_cover_whole_text(self):
        """逐字符 run（程序自身形态）：回归后语义不能变，仍覆盖全文。"""
        t = self._render([["t", ch, ["h2"]] for ch in "技术博客"])
        try:
            self.assertEqual(self._covered(t, "h2"), ["技术博客"])
        finally:
            t.destroy()

    def test_multiple_tags_on_merged_run(self):
        """整段 run 带多个 tag 时，每个都要整段覆盖（不是各挂末字符）。"""
        t = self._render([["t", "重点内容", ["bold", "h2"]]])
        try:
            for tag in ("bold", "h2"):
                self.assertEqual(self._covered(t, tag), ["重点内容"],
                                 "%s 没覆盖整段" % tag)
        finally:
            t.destroy()


if __name__ == "__main__":
    unittest.main(verbosity=2)
