# -*- coding: utf-8 -*-
"""笔记列表右键「移动到文件夹…」回归测试（不建主窗口）。

背景：把一篇笔记拖进别的文件夹要求目标**此刻在左栏可见**，收着的子层得先一层层
展开；用户要的是在笔记行上右键直接挑目标。实现复用了已有的三件套：
`_ask_choose_folder`（树形选择框）、`_move_note_to_folder`（落库 / 刷新 / 计数）、
`_select_after_move`（拖拽落定后的选中收口）。

这里用桩对象驱动 `ActionsMixin` 的三个方法，锁住：
  1. 单篇菜单里有这一项，图标独立、能点；
  2. 说明笔记 / 没有别的文件夹时置灰并写明原因，多选菜单保持原样；
  3. 候选排除智能分类、系统区、笔记自己所在的那一格（子文件夹仍可选）；
  4. 取消不动数据；选定才写 folder 并走选中收口；
  5. 新增的 move 图标真的能画出图元（名字写错时 `_draw_icon` 会静默返回空）。

运行：
    python -m unittest tests.test_note_move_menu -v
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import app_actions                                            # noqa: E402
from app_actions import ActionsMixin                           # noqa: E402
from store import SYSTEM_FOLDER                                # noqa: E402
from widgets import _draw_icon                                 # noqa: E402


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
    x_root, y_root = 100, 200


def _folder(fid, name=None, parent=None, smart=False):
    return {"id": fid, "name": name or fid, "parent": parent, "smart": smart}


def _note(nid="n1", folder="f_work", title="周会待办", sample=False):
    return {"id": nid, "folder": folder, "pinned": False, "sec": None,
            "_title": title, "sample": sample}


def _folders():
    return [
        _folder("f_all", "全部笔记", smart=True),
        _folder("f_star", "星标笔记", smart=True),
        _folder("f_lock", "加密笔记", smart=True),
        _folder(SYSTEM_FOLDER, "系统"),
        _folder("f_work", "工作"),
        _folder("f_personal", "个人"),
        _folder("f_child", "工作/子层", parent="f_work"),
    ]


class _Stub(object):
    """只实现这三个方法会碰到的协作面。"""

    _popup = ActionsMixin._popup
    _popup_move_note = ActionsMixin._popup_move_note
    _note_move_targets = ActionsMixin._note_move_targets
    is_sample_note = ActionsMixin.is_sample_note

    def __init__(self, notes, folders=None, chosen=None, goto=None):
        self.data = {"notes": notes, "folders": _folders() if folders is None else folders,
                     "current_folder": "f_work", "current_note": None}
        self._note_sel = {notes[0]["id"]} if notes else set()
        self._note_anchor = None
        self.root = object()
        self.pal = {}
        self.chosen = chosen
        self.goto = goto
        self.asks = []
        self.moved = []
        self.selected_after = []
        self.infos = []

    # —— 菜单组装时会用到的协作方法 ——
    def _restyle_notes(self):
        pass

    def _goto_item(self, n):
        return self.goto

    def _popup_pin(self, nid):
        pass

    def toggle_star(self, nid):
        pass

    def _popup_goto_folder(self, nid):
        pass

    def _popup_delete(self, nid):
        pass

    def _delete_notes(self, ids):
        pass

    def set_note_password(self, nid):
        pass

    def unlock_note(self, nid):
        pass

    def change_note_password(self, nid):
        pass

    def clear_note_password(self, nid):
        pass

    def _show_info(self, title, msg):
        self.infos.append((title, msg))

    # —— 移动链路 ——
    def title_of(self, n):
        return n.get("_title") or "新建笔记"

    def trunc(self, s, n):
        return s if len(s) <= n else s[:n] + "…"

    def _ask_choose_folder(self, title, prompt, options):
        self.asks.append((title, prompt, [f["id"] for f in options]))
        return self.chosen

    def _move_note_to_folder(self, nid, fid):
        self.moved.append((nid, fid))

    def _select_after_move(self, nid):
        self.selected_after.append(nid)


class NoteMoveMenuTest(unittest.TestCase):
    def setUp(self):
        self._orig = app_actions.PopupMenu
        app_actions.PopupMenu = _FakeMenu
        _FakeMenu.last = None

    def tearDown(self):
        app_actions.PopupMenu = self._orig

    def _menu(self, stub, nid="n1"):
        stub._popup(_Event(), nid)
        return _FakeMenu.last

    def _move_item(self, menu):
        return next((it for it in menu.items
                     if it["label"] and it["label"].startswith("移动到文件夹")), None)

    def test_single_note_has_an_enabled_move_item(self):
        stub = _Stub([_note()])
        it = self._move_item(self._menu(stub))
        self.assertIsNotNone(it, "单篇笔记右键必须有「移动到文件夹…」")
        self.assertEqual("移动到文件夹…", it["label"])
        self.assertEqual("move", it["icon"])
        self.assertTrue(it["enabled"])
        it["command"]()
        self.assertEqual(1, len(stub.asks), "点菜单项要打开树形选择框")

    def test_move_icon_does_not_duplicate_goto_folder_icon(self):
        stub = _Stub([_note()], goto=("跳到所属文件夹", True))
        menu = self._menu(stub)
        goto = next(it for it in menu.items if it["label"] == "跳到所属文件夹")
        move = self._move_item(menu)
        self.assertEqual("folder", goto["icon"])
        self.assertNotEqual(goto["icon"], move["icon"],
                            "一张菜单里两项不能共用图标")

    def test_sample_note_is_disabled_with_reason(self):
        stub = _Stub([_note(sample=True)])
        it = self._move_item(self._menu(stub))
        self.assertIn("内置说明不可移", it["label"])
        self.assertFalse(it["enabled"])
        it["command"]()
        self.assertEqual([], stub.asks, "置灰项即使被触发也不能弹框")

    def test_no_other_folder_is_disabled_with_reason(self):
        stub = _Stub([_note()], folders=[_folder("f_work", "工作"),
                                         _folder(SYSTEM_FOLDER, "系统"),
                                         _folder("f_all", "全部", smart=True)])
        it = self._move_item(self._menu(stub))
        self.assertIn("没有其他文件夹", it["label"])
        self.assertFalse(it["enabled"])

    def test_multi_selection_menu_is_untouched(self):
        stub = _Stub([_note("n1"), _note("n2")])
        stub._note_sel = {"n1", "n2"}
        menu = self._menu(stub, "n1")
        labels = [it["label"] for it in menu.items if it["label"]]
        self.assertEqual(["删除选中的 2 条笔记"], labels,
                         "多选菜单仍只提供批量删除，本需求只针对单篇")

    def test_targets_exclude_smart_system_and_home(self):
        stub = _Stub([_note()])
        self.assertEqual(["f_personal", "f_child"],
                         [f["id"] for f in stub._note_move_targets(exclude=["f_work"])],
                         "智能分类 / 系统 / 自己所在那格都不能当目标，子文件夹可以")
        self.assertIn("f_work", [f["id"] for f in stub._note_move_targets()],
                      "不传 exclude 时保留全部真实文件夹")

    def test_choose_dialog_gets_tree_and_truncated_title(self):
        stub = _Stub([_note(title="标" * 40)], chosen=None)
        stub._popup_move_note("n1")
        self.assertEqual(1, len(stub.asks))
        title, prompt, ids = stub.asks[0]
        self.assertEqual("移动到文件夹", title)
        self.assertTrue(prompt.startswith("把「"))
        self.assertTrue(prompt.endswith("」移动到："))
        self.assertLessEqual(len(prompt), 40, "标题要截断，别把弹窗撑宽")
        self.assertIn("…", prompt)
        self.assertEqual(["f_personal", "f_child"], ids)

    def test_cancel_does_not_touch_anything(self):
        stub = _Stub([_note()], chosen=None)
        stub._popup_move_note("n1")
        self.assertEqual([], stub.moved)
        self.assertEqual([], stub.selected_after)
        self.assertEqual([], stub.infos)

    def test_choose_moves_and_selects(self):
        stub = _Stub([_note()], chosen="f_personal")
        stub._popup_move_note("n1")
        self.assertEqual([("n1", "f_personal")], stub.moved)
        self.assertEqual(["n1"], stub.selected_after)

    def test_same_folder_result_is_a_noop(self):
        """兜底：万一选择框返回了自己原来那格，别把 updated/order 白改一遍。"""
        stub = _Stub([_note()], chosen="f_work")
        stub._popup_move_note("n1")
        self.assertEqual([], stub.moved)
        self.assertEqual([], stub.selected_after)

    def test_sample_note_never_opens_the_dialog(self):
        stub = _Stub([_note(sample=True)], chosen="f_personal")
        stub._popup_move_note("n1")
        self.assertEqual([], stub.asks)
        self.assertEqual([], stub.moved)

    def test_no_targets_shows_info_instead_of_empty_dialog(self):
        stub = _Stub([_note()], folders=[_folder("f_work", "工作"),
                                         _folder(SYSTEM_FOLDER, "系统")])
        stub._popup_move_note("n1")
        self.assertEqual([], stub.asks)
        self.assertEqual(1, len(stub.infos))


class MoveNoteCountRefreshTest(unittest.TestCase):
    """移动落库后刷新计数，必须走 `_refresh_folder_counts`。

    `_folder_count_labels` 里存的是自绘 `CountBadge`（tk.Canvas），没有 `-text`
    选项。早先 `_move_note_to_folder` 手写 `lbl.configure(text=...)`，拖拽和右键
    移动都会在这一步抛 TclError，后面的选中收口根本没机会跑。
    """

    class _MoveStub(_Stub):
        _move_note_to_folder = ActionsMixin._move_note_to_folder

    def test_move_note_does_not_configure_text_on_badge(self):
        import tkinter as tk
        from widgets import CountBadge
        root = tk.Tk()
        root.withdraw()
        try:
            st = self._MoveStub([_note()])
            st._folder_count_labels = {"f_work": CountBadge(root, count=1)}
            st.refreshed = []
            st.refresh_notes = lambda: st.refreshed.append("notes")
            st._refresh_folder_counts = lambda: st.refreshed.append("counts")
            st.top_note_order = lambda n: 0
            orig = app_actions.save_data
            app_actions.save_data = lambda data: None
            try:
                st._move_note_to_folder("n1", "f_personal")   # 旧实现在这里抛 TclError
            finally:
                app_actions.save_data = orig
            self.assertEqual("f_personal", st.data["notes"][0]["folder"])
            self.assertEqual(["notes", "counts"], st.refreshed)
        finally:
            root.destroy()

    def test_sample_note_is_never_moved(self):
        st = self._MoveStub([_note(sample=True)])
        st.refresh_notes = lambda: None
        orig = app_actions.save_data
        app_actions.save_data = lambda data: self.fail("说明笔记不该落库")
        try:
            st._move_note_to_folder("n1", "f_personal")
        finally:
            app_actions.save_data = orig
        self.assertEqual("f_work", st.data["notes"][0]["folder"])


class MoveIconTest(unittest.TestCase):
    def test_move_icon_draws_real_primitives(self):
        """图标名拼错时 `_draw_icon` 会静默返回空列表，菜单上就是一个空洞。"""
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        try:
            c = tk.Canvas(root, width=20, height=20)
            ids = _draw_icon(c, "move", 10, 10, 16, "#333333")
            self.assertGreaterEqual(len(ids), 3, "move 图标必须真的画出图元")
            self.assertEqual(len(ids), len(c.find_all()))
            other = tk.Canvas(root, width=20, height=20)
            folder_ids = _draw_icon(other, "folder", 10, 10, 16, "#333333")
            shape = lambda cv, ii: [tuple(round(v) for v in cv.coords(i)) for i in ii]
            self.assertNotEqual(shape(c, ids), shape(other, folder_ids),
                                "两个图标不能画成同一组图元")
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
