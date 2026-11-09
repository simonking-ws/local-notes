# -*- coding: utf-8 -*-
"""正文行折叠（gfold）的核心不变量。

折叠 = 选中连续行 → 首行保留作「折叠头」，其后到末行整段 elide。命门有三：
  · 折叠区间是**整行级**（头行行尾+1c → 末行行尾+1c），头行自己的换行符不藏
    —— 否则折叠头会和下一行可见内容挤成一行；
  · 折叠是**纯视图状态**：不 mark_modified、gfold* 进 _is_ephemeral_tag 不落库，
    切笔记 / 重启回到展开态；
  · 光标落进隐藏区（插入 / 删除触到）→ 护栏先展开，杜绝盲打不可见文字。

显示行数不用 text.count("displaylines") 断言：未映射（withdrawn）窗口上它不可
靠，显示行为由真窗口探针另行验证（.workbuddy/shot/probe_editor_fold.py）。

    python tests\\test_editor_fold.py -v
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import types                                            # noqa: E402
import tkinter as tk                                    # noqa: E402
import app_ui                                           # noqa: E402
import app_actions                                      # noqa: E402


class _Harness(object):
    """把需要的 mixin 方法拼到一个对象上，配一块真实 Text（不需要主窗口渲染）。"""

    def __init__(self, root):
        self.root = root
        self.text = tk.Text(root)
        self._readonly = False
        self._locked_view = False
        self._gutter_job = None
        self._gutter_phase = 0
        self._fold_quiet = False
        self._fold_seq = 0
        self._folds = []
        self._fold_chips = {}
        self.pal = {"accent": "#4a7", "editor": "#fff", "soft": "#888", "text": "#222"}
        self.body_size = 12
        self.data = {"settings": {"line_numbers": True}}
        self.after_calls = []
        self._after = lambda ms, fn: (self.after_calls.append((ms, fn)) or len(self.after_calls))
        self._cancel_after = lambda job: None
        self.LI_TAG_PREFIX = app_actions.ActionsMixin.LI_TAG_PREFIX
        # 绑 mixin 方法（不实例化整个 NotesApp）。dir() 取到的是函数描述符，
        # 必须 MethodType 绑到本实例，否则 self 丢失。
        for cls in (app_ui.UIMixin, app_actions.ActionsMixin):
            for name in dir(cls):
                if name.startswith("__"):
                    continue
                if hasattr(self, name):
                    continue
                fn = getattr(cls, name)
                if callable(fn):
                    setattr(self, name, types.MethodType(fn, self))

    def _load(self, lines):
        self.text.delete("1.0", tk.END)
        for i, s in enumerate(lines):
            self.text.insert(tk.END, s + ("\n" if i < len(lines) - 1 else ""))

    def current_note(self):
        # current_note 在主类 notes_app.NotesApp 上（mixin 拼不到），给个真值桩
        return {"id": "n1"}

    def mark_modified(self):
        self.modified_calls = getattr(self, "modified_calls", 0) + 1

    def _gutter_schedule(self, delay=8):
        self.redraw_calls = getattr(self, "redraw_calls", 0) + 1

    def _flash_readonly_hint(self):
        self.refused = True

    def is_readonly(self):
        return self._readonly


class EditorFoldTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.h = _Harness(self.root)
        self.h._load(["第一行", "第二行", "第三行", "第四行", "第五行"])
        self.h.refused = False

    def tearDown(self):
        self.h.text.destroy()

    def _fold(self, a, b):
        # 真实交互里重新选择会先清掉旧选区；直接 tag_add 会与旧选区合并，
        # 先 remove 才等价于「用户重新框选」。
        self.h.text.tag_remove("sel", "1.0", "end")
        self.h.text.tag_add("sel", a, b)
        self.h._fold_selection()

    def test_fold_hides_range_keeps_head(self):
        """折叠选区 2.0–4.0：头行 2 保留，隐藏区 = 2行末+1c → 3行末+1c。"""
        self._fold("2.0", "4.0")
        self.assertEqual(len(self.h._folds), 1)
        tag = self.h._folds[0]
        rng = self.h.text.tag_ranges(tag)
        self.assertEqual(str(rng[0]), self.h.text.index("2.end+1c"))
        self.assertEqual(str(rng[1]), self.h.text.index("3.end+1c"))
        self.assertEqual(self.h.text.tag_cget(tag, "elide"), "1")
        self.assertEqual(set(self.h._fold_head_lines()), {2})
        # 选区清掉、保存链路一次都没走（折叠是视图操作）
        self.assertFalse(self.h.text.tag_ranges("sel"))
        self.assertEqual(getattr(self.h, "modified_calls", 0), 0)

    def test_tail_inclusive_when_selection_enters_line(self):
        """选区终点落在行内（3.2）→ 那一行也折叠；停在行首（3.0）→ 不算。"""
        self._fold("2.0", "3.2")
        self.assertEqual(set(self.h._fold_head_lines()), {2})
        self.h._fold_unfold_all()
        self._fold("2.0", "3.0")
        self.assertEqual(self.h._folds, [], "终点停在 3.0：第 3 行没被选上，没法折")

    def test_single_line_refused(self):
        self._fold("2.0", "3.0")
        self.assertEqual(self.h._folds, [])

    def test_overlapping_fold_refused(self):
        self._fold("2.0", "4.0")                          # 折 2–3
        self._fold("3.0", "5.0")                          # 与之相交 → 拒绝
        self.assertEqual(len(self.h._folds), 1)
        self._fold("4.0", "6.0")                          # 4–5：不相交 → 允许
        self.assertEqual(len(self.h._folds), 2)
        self.assertEqual(set(self.h._fold_head_lines()), {2, 4})

    def test_unfold_restores(self):
        self._fold("2.0", "4.0")
        tag = self.h._folds[0]
        self.h._fold_unfold(tag)
        self.assertEqual(self.h._folds, [])
        self.assertNotIn(tag, self.h.text.tag_names())
        self.assertEqual(getattr(self.h, "modified_calls", 0), 0)

    def test_unfold_all(self):
        self._fold("1.0", "3.0")
        self._fold("4.0", "6.0")
        self.h._fold_unfold_all()
        self.assertEqual(self.h._folds, [])

    def test_fold_not_serialized(self):
        """gfold* 是视图状态：serialize 的字符 tag 里绝不能出现，chip 窗口也不落库。"""
        self._fold("2.0", "4.0")
        runs = self.h.serialize()
        for it in runs:
            self.assertEqual(it[0], "t", "chip 提示片（window）不该进序列化")
            for tg in it[2] if len(it) > 2 else ():
                self.assertFalse(str(tg).startswith("gfold"),
                                 "折叠 tag 不能写进正文")
        self.assertTrue(self.h._is_ephemeral_tag("gfold_1"))
        self.assertTrue(self.h._is_ephemeral_tag("gfold_12"))

    def test_sync_unfolds_when_insert_inside_hidden_range(self):
        """光标落进隐藏区（半开区间 [start, end)）→ 护栏先展开。"""
        for probe in ("3.0", "3.5", "4.0"):
            self._fold("2.0", "5.0")                      # 折 2–4
            self.h.text.mark_set("insert", probe)
            self.h._fold_sync_after_edit()
            self.assertEqual(self.h._folds, [], "insert=%s 应触发自动展开" % probe)

    def test_insert_at_visible_boundary_keeps_fold(self):
        """光标在折叠块**后面**的可见行行首（== 区间 end）打字，不展开。"""
        self._fold("2.0", "5.0")                          # 折 2–4，end = 5.0
        self.h.text.mark_set("insert", "5.0")
        self.h.text.insert("5.0", "可见行打字")
        self.h._fold_sync_after_edit()
        self.assertEqual(len(self.h._folds), 1)

    def test_sync_survives_edit_on_head(self):
        """在折叠头上正常打字不该把折叠拆掉。"""
        self._fold("2.0", "5.0")
        self.h.text.insert("2.3", "插入")
        self.h._fold_sync_after_edit()
        self.assertEqual(len(self.h._folds), 1)

    def test_chip_sits_at_head_end(self):
        """「⋯ N 行」提示片嵌在折叠头行末，不占正文字符。"""
        self._fold("2.0", "5.0")                          # 终点 5.0 排他 → 折 3–4 共 2 行
        tag = self.h._folds[0]
        chip = self.h._fold_chips[tag]
        self.assertEqual(self.h.text.index(str(chip)),
                         self.h._fold_chip_expected(2))
        self.assertEqual(chip._fold_label, "⋯ 2 行")
        # chip 不占字符：正文内容原封不动
        self.assertIn("第三行", self.h.text.get("1.0", "end"))

    def test_sync_repositions_chip_after_edit(self):
        """在折叠头上补字后，chip 应回到新的行末位置。"""
        self._fold("2.0", "5.0")
        self.h.text.insert("2.end", "补充")
        self.h._fold_sync_after_edit()
        chip = self.h._fold_chips[self.h._folds[0]]
        self.assertEqual(self.h.text.index(str(chip)),
                         self.h._fold_chip_expected(2))

    def test_empty_fold_cleaned_on_sync(self):
        """隐藏行被删光后 tag 自动消失 → 折叠块应被清理掉。"""
        self._fold("2.0", "4.0")                          # 只藏第 3 行
        self.h.text.delete("3.0", "4.0")
        self.h._fold_sync_after_edit()
        self.assertEqual(self.h._folds, [])

    def test_fold_cleanup_destroys_chips(self):
        """切笔记 / 重载前的清场：chip 必须销毁，不留漂浮控件。"""
        self._fold("2.0", "4.0")
        chips = list(self.h._fold_chips.values())
        self.assertTrue(chips)
        self.h._fold_cleanup()
        self.assertEqual(self.h._fold_chips, {})
        self.assertEqual(self.h._folds, [])
        for c in chips:
            self.assertFalse(c.winfo_exists())

    def test_hidden_count_from_ranges(self):
        """按 _fold_ranges 算「某逻辑行上方被藏行数」：只累计整块在上方者。

        折叠块 (head, tail) 藏 head+1..tail；对逻辑行 ln，只有 tail < ln 才算
        「整块在它上方」——折叠头本身（ln==head）不隐藏自己的号。
        """
        self._fold("2.0", "4.0")                          # head=2, tail=3，藏 3 行？否：藏 3
        ranges = self.h._fold_ranges()
        self.assertEqual(len(ranges), 1)
        _tag, _s, _e, head, tail = ranges[0]
        self.assertEqual((head, tail), (2, 3))
        above = lambda ln: sum(t - h for _t, _s, _e, h, t in ranges if t < ln)
        self.assertEqual(above(2), 0)                     # 折叠头：不跳自己的号
        self.assertEqual(above(4), 1)                     # 折叠块后第一行：跳过 1
        self.assertEqual(above(5), 1)

    def test_text_context_menu_offers_fold_with_selection(self):
        """正文右键：有折叠选区时弹「折叠选中行」，并返回 break 拦掉闹钟菜单。

        用假 PopupMenu 捕获 add 的条目，不真弹窗（真弹窗会 grab）。
        """
        import types as _t
        added = []

        class _FakeMenu(object):
            def __init__(self, *a, **kw):
                pass

            def add(self, label, **kw):
                added.append(label)

            def popup(self, *a, **kw):
                pass

        old = app_ui.PopupMenu
        app_ui.PopupMenu = _FakeMenu
        try:
            self.h.text.tag_add("sel", "2.0", "4.0")       # 跨 2 行的选区
            ev = _t.SimpleNamespace(x_root=100, y_root=100)
            ret = self.h._on_text_context_menu(ev)
        finally:
            app_ui.PopupMenu = old
        self.assertEqual(ret, "break")
        self.assertIn("折叠选中行", added)

    def test_text_context_menu_method_exists(self):
        """正文右键入口：有折叠选区弹「折叠选中行」菜单，方法绑在正文 <Button-3>。

        这里只验方法存在且无选区时放行（不弹菜单）；真窗口的菜单视觉由探针确认。
        """
        self.assertTrue(callable(getattr(self.h, "_on_text_context_menu", None)))
        self.assertIsNone(self.h._on_text_context_menu(None))  # 桩 event 无选区 → 放行

    def test_index_key_cross_line_order(self):
        """(line, col) 数值比较：字符串比较在跨行时会错（"10.0" < "9.1"）。"""
        self.assertLess(self.h._idx_key("9.5"), self.h._idx_key("10.0"))
        self.assertEqual(self.h._idx_key("3.7"), self.h._idx_key("3.7"))

    def test_readonly_can_fold_but_refuse_hinted(self):
        """折叠不改正文：只读笔记照常可用；越界提示借只读提示条闪一下。"""
        self.h._readonly = True
        self._fold("2.0", "4.0")
        self.assertEqual(len(self.h._folds), 1)
        self.assertEqual(getattr(self.h, "modified_calls", 0), 0)
        self.h._fold_unfold_all()
        self._fold("2.0", "3.0")                          # 单行 → 拒绝 + 提示
        self.assertTrue(self.h.refused)

    def test_fold_tag_at_boundaries(self):
        """_fold_tag_at 半开区间 [start, end)：首隐藏行查得到，end（下一可见
        行行首）不算隐藏区。"""
        self._fold("2.0", "5.0")                          # 折 2–4
        self.assertEqual(self.h._fold_tag_at("3.0"), self.h._folds[0])
        self.assertEqual(self.h._fold_tag_at("4.5"), self.h._folds[0])
        self.assertIsNone(self.h._fold_tag_at("1.0"))
        self.assertIsNone(self.h._fold_tag_at("5.0"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
