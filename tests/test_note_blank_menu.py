# -*- coding: utf-8 -*-
"""笔记列表空白处右键「新建笔记」回归测试（不建主窗口）。

空白处没有对象可指：这里只该出现「新建笔记」，置顶 / 星标 / 移动到文件夹 / 加密
都留在行菜单（`_popup`）里，否则用户会怀疑「到底对哪一条操作」。

回收站视图例外：那儿的条目是回收记录，`new_note` 会改归属并整体切走视图 ——
右键一块空白却「凭空换页」，比没有菜单更意外。

    python tests\\test_note_blank_menu.py
"""
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import app_actions                                            # noqa: E402
from app_actions import ActionsMixin                           # noqa: E402
from store import RECYCLE_FOLDER                               # noqa: E402


class _FakeMenu(object):
    """替掉自绘 PopupMenu：只记下 add 的参数，不建 Toplevel。"""

    last = None

    def __init__(self, master, pal):
        self.items = []
        self.popped = None
        _FakeMenu.last = self

    def add(self, label, command=None, danger=False, enabled=True,
            icon=None, checked=False):
        self.items.append({"label": label, "command": command,
                           "enabled": enabled, "icon": icon,
                           "danger": danger, "checked": checked})
        return self

    def add_separator(self):
        self.items.append({"label": None})
        return self

    def popup(self, x, y):
        self.popped = (x, y)


class _Event(object):
    x_root, y_root = 120, 240


class _FakeApp(object):
    root = None
    pal = {}

    def __init__(self, folder="f_work"):
        self.data = {"current_folder": folder}
        self.new_calls = 0
        # 绑真方法：回收站分支的判断逻辑要和线上完全一致，别在测试里另写一份
        self._note_blank_popup = types.MethodType(
            ActionsMixin._note_blank_popup, self)
        self._recycle_view = types.MethodType(ActionsMixin._recycle_view, self)

    def new_note(self):
        self.new_calls += 1


class TestNoteBlankMenu(unittest.TestCase):
    def setUp(self):
        self._orig = app_actions.PopupMenu
        app_actions.PopupMenu = _FakeMenu
        _FakeMenu.last = None

    def tearDown(self):
        app_actions.PopupMenu = self._orig

    def test_blank_menu_only_offers_new_note(self):
        app = _FakeApp("f_work")
        app._note_blank_popup(_Event())
        m = _FakeMenu.last
        self.assertIsNotNone(m, "空白处没有弹出菜单")
        self.assertEqual([i["label"] for i in m.items], ["新建笔记"])
        self.assertEqual(m.items[0]["icon"], "pencil")
        self.assertTrue(m.items[0]["enabled"])
        self.assertEqual(m.popped, (120, 240))

    def test_item_really_creates_note(self):
        app = _FakeApp("f_work")
        app._note_blank_popup(_Event())
        _FakeMenu.last.items[0]["command"]()
        self.assertEqual(app.new_calls, 1)

    def test_recycle_view_has_no_menu(self):
        app = _FakeApp(RECYCLE_FOLDER)
        ret = app._note_blank_popup(_Event())
        self.assertEqual(ret, "break")
        self.assertIsNone(_FakeMenu.last, "回收站视图不该弹新建菜单")

    def test_blank_surfaces_are_bound(self):
        """构造区必须把 canvas 与 inner 都绑上：只绑一个等于只在半块区域生效。

        这条读源码而不是建真窗口 —— 主窗口测试只留给 test_settings_dialog.py。
        """
        with open(os.path.join(ROOT, "scripts", "app_ui.py"),
                  encoding="utf-8") as f:
            src = f.read()
        self.assertIn("self._note_blank_popup", src,
                      "app_ui.py 没有把空白处右键接到 _note_blank_popup")
        self.assertIn("self.note_panel.canvas, self.note_panel.inner", src,
                      "空白处绑定漏了 canvas 或 inner")


if __name__ == "__main__":
    unittest.main()
