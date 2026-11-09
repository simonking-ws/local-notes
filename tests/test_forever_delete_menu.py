# -*- coding: utf-8 -*-
"""「彻底删除」入口回归：笔记行 / 文件夹行的右键菜单（不建主窗口）。

需求：删除可以选「进回收站」或「彻底删除（不进回收站）」。这里的用例钉住：

  · 单篇笔记菜单里「删除笔记」与「彻底删除」都在（都不带省略号），后者带划掉的垃圾桶图标；
  · 系统笔记（内置说明 / 汇总表）两项都置灰并写明原因；
  · 多选菜单同时给「删除 / 彻底删除」两种口径；
  · 文件夹行（单条 / 多选）同样多一项，智能分类与系统区置灰；
  · 新图标真的能画出图元（名字写错时 `_draw_icon` 会静默返回空）。

    python tests\\test_forever_delete_menu.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import tkinter as tk                                    # noqa: E402

import dpi                                              # noqa: E402
dpi.init()

import app_actions                                      # noqa: E402
import widgets                                          # noqa: E402
from app_actions import ActionsMixin                     # noqa: E402
from store import SMART_FOLDERS, SYSTEM_FOLDER           # noqa: E402

INK = "#2f7cf6"


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


def _note(nid="n1", sample=False):
    return {"id": nid, "folder": "f_work", "pinned": False, "sec": None,
            "sample": sample}


class _NoteStub(object):
    """笔记行菜单：只实现 `_popup` 会碰到的协作面。"""

    _popup = ActionsMixin._popup
    is_sample_note = ActionsMixin.is_sample_note
    is_alarm_index = ActionsMixin.is_alarm_index
    is_protected_note = ActionsMixin.is_protected_note

    def __init__(self, notes):
        self.data = {"notes": notes, "folders": [_folder("f_work", "工作")],
                     "current_folder": "f_work", "current_note": None}
        self._note_sel = {notes[0]["id"]} if notes else set()
        self._note_anchor = None
        self.root = object()
        self.pal = {}
        self.deleted = []

    def _restyle_notes(self):
        pass

    def _goto_item(self, n):
        return None

    def _note_move_targets(self, exclude=None):
        return []

    def _popup_pin(self, nid):
        pass

    def toggle_star(self, nid):
        pass

    def _popup_goto_folder(self, nid):
        pass

    def _popup_move_note(self, nid):
        pass

    def _popup_delete(self, nid, forever=False):
        self.deleted.append((nid, forever))

    def _delete_notes(self, ids, forever=False):
        self.deleted.append((list(ids), forever))


class _FolderStub(object):
    """文件夹行菜单：只实现 `_folder_popup` 会碰到的协作面。"""

    _folder_popup = ActionsMixin._folder_popup

    def __init__(self, fid):
        self.data = {"folders": [_folder("f_all", "全部笔记", smart=True),
                                 _folder(SYSTEM_FOLDER, "系统"),
                                 _folder("f_work", "工作"),
                                 _folder("f_child", "子层", parent="f_work")],
                     "current_folder": fid, "current_note": None}
        self._folder_sel = {fid}
        self._folder_anchor = fid
        self.root = object()
        self.pal = {}
        self.deleted = []

    def _restyle_folders(self):
        pass

    def rename_folder(self, fid):
        pass

    def new_folder(self, parent=None):
        pass

    def _toggle_folder(self, fid):
        pass

    def _move_notes_out(self, fid):
        pass

    def del_folder(self, fid=None, forever=False):
        self.deleted.append((fid, forever))

    def _delete_folders(self, ids, forever=False):
        self.deleted.append((list(ids), forever))


class TestForeverDeleteMenu(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self._orig = app_actions.PopupMenu
        app_actions.PopupMenu = _FakeMenu
        _FakeMenu.last = None

    def tearDown(self):
        app_actions.PopupMenu = self._orig

    def _menu(self, stub):
        _FakeMenu.last = None
        stub._popup(_Event(), "n1")
        return _FakeMenu.last

    def _items(self, menu):
        return [it for it in menu.items if it["label"]]

    # ---------- 笔记行 ----------
    def test_single_note_offers_forever_delete(self):
        stub = _NoteStub([_note()])
        items = self._items(self._menu(stub))
        labels = [it["label"] for it in items]
        self.assertIn("删除笔记", labels)
        it = next(i for i in items if i["label"] == "彻底删除")
        self.assertTrue(it["enabled"])
        self.assertTrue(it["danger"])
        self.assertEqual("trash_slash", it["icon"])
        it["command"]()
        self.assertEqual([("n1", True)], stub.deleted)

    def test_labels_carry_no_ellipsis(self):
        """末尾两项都不带省略号：文案给定，不再用「…」暗示还会弹窗。"""
        items = self._items(self._menu(_NoteStub([_note()])))
        tail = [it["label"] for it in items[-2:]]
        self.assertEqual(["删除笔记", "彻底删除"], tail)
        self.assertFalse([x for x in tail if "…" in x],
                         "删除这类菜单项里不该再出现省略号")

    def test_protected_note_disables_both(self):
        stub = _NoteStub([_note(sample=True)])
        items = self._items(self._menu(stub))
        for label_start in ("删除笔记", "彻底删除"):
            it = next(i for i in items if i["label"].startswith(label_start))
            self.assertFalse(it["enabled"], "%s 对系统笔记必须置灰" % it["label"])
            self.assertIn("不可删", it["label"], "置灰项要写明原因")
        self.assertEqual([], stub.deleted, "置灰项即使被触发也不能删")

    def test_multi_selection_offers_both(self):
        stub = _NoteStub([_note("n1"), _note("n2")])
        stub._note_sel = {"n1", "n2"}
        items = self._items(self._menu(stub))
        self.assertEqual(["删除选中的 2 条笔记", "彻底删除选中的 2 条笔记"],
                         [it["label"] for it in items])
        items[1]["command"]()
        self.assertEqual(1, len(stub.deleted))
        self.assertTrue(stub.deleted[0][1], "彻底删除没把 forever 传下去")

    # ---------- 文件夹行 ----------
    def test_folder_row_offers_forever_delete(self):
        stub = _FolderStub("f_work")
        _FakeMenu.last = None
        stub._folder_popup(_Event(), "f_work")
        items = self._items(_FakeMenu.last)
        it = next(i for i in items if i["label"] == "彻底删除文件夹")
        self.assertEqual("trash_slash", it["icon"])
        self.assertTrue(it["danger"])
        it["command"]()
        self.assertEqual([("f_work", True)], stub.deleted)

    def test_folder_multi_selection_offers_both(self):
        stub = _FolderStub("f_work")
        stub._folder_sel = {"f_work", "f_child"}
        _FakeMenu.last = None
        stub._folder_popup(_Event(), "f_work")
        labels = [it["label"] for it in self._items(_FakeMenu.last)]
        self.assertEqual(["删除选中的 2 个文件夹", "彻底删除选中的 2 个文件夹"],
                         labels)

    def test_smart_folder_stays_disabled(self):
        fid = SMART_FOLDERS[0]
        stub = _FolderStub(fid)
        _FakeMenu.last = None
        stub._folder_popup(_Event(), fid)
        it = next(i for i in self._items(_FakeMenu.last)
                  if i["label"].startswith("彻底删除文件夹"))
        self.assertFalse(it["enabled"])
        self.assertIn("智能分类不可删", it["label"])
        self.assertEqual([], stub.deleted)

    # ---------- 图标 ----------
    def _draw(self, name, size=16):
        cv = tk.Canvas(self.root, width=size, height=size, bg="#ffffff", bd=0,
                       highlightthickness=0)
        widgets._draw_icon(cv, name, size / 2.0, size / 2.0, size, INK)
        return cv

    def test_trash_slash_icon_has_ink_and_stays_inside(self):
        base = len(self._draw("trash").find_all())
        cv = self._draw("trash_slash")
        self.assertEqual(base + 1, len(cv.find_all()),
                         "彻底删除图标＝垃圾桶 + 一条斜杠")
        for item in cv.find_all():
            box = cv.bbox(item)
            if box is None:
                continue
            x0, y0, x1, y1 = box
            self.assertGreaterEqual(x0, -3)
            self.assertGreaterEqual(y0, -3)
            self.assertLessEqual(x1, 19)
            self.assertLessEqual(y1, 19)


if __name__ == "__main__":
    unittest.main(verbosity=2)
