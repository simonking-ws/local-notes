# -*- coding: utf-8 -*-
"""空文件夹新建笔记后，「这里还没有笔记…」引导必须消失。

回归点：空列表引导是 refresh_notes() 在没有可见笔记时放的 Label；但在普通
文件夹里新建走的是 _insert_note_row() 差量插入（不整列重建），旧代码没人销毁
那个 Label，于是「新笔记」和「这里还没有笔记」会同时出现在列表里。

跑法：D:/Python/python_install/python.exe tests/test_empty_hint_on_new.py
"""
import os
import sys
import tempfile
import shutil
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import dpi                                              # noqa: E402
dpi.init()

import tkinter as tk                                    # noqa: E402
import store                                            # noqa: E402
from store import Store                                 # noqa: E402


def _hints(app):
    """当前列表里还挂着的空列表引导 Label。"""
    return [w for w in app.note_panel.inner.children.values()
            if getattr(w, "_empty_hint", False)]


class EmptyHintOnNewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="empty_hint_")
        store._STORE = Store(os.path.join(cls.tmp, "notes.db"))
        import notes_app
        cls.root = tk.Tk()
        cls.root.withdraw()
        cls.app = notes_app.NotesApp(cls.root)
        cls.app._ask_confirm = lambda *a, **k: True
        cls.app._show_info = lambda *a, **k: None
        cls.root.update_idletasks()
        cls.root.update()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.root.destroy()
        except Exception:
            pass
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        app = self.app
        # 一个真实空文件夹 f_e，当前就切在它上面；清空所有笔记
        app.data["folders"] = [f for f in app.data["folders"]
                               if f.get("smart") or f["id"] == "f_system"]
        app.data["folders"].append({"id": "f_e", "name": "空文件夹"})
        app.data["notes"] = []
        app.data["current_folder"] = "f_e"
        app.data["current_note"] = None
        app.search_var.set("")
        app.refresh_all()
        self.root.update_idletasks()
        self.root.update()

    def test_empty_folder_shows_hint(self):
        """前置：空文件夹确实挂着引导。"""
        self.assertEqual(len(_hints(self.app)), 1,
                         "空文件夹应有一条引导文案")

    def test_new_note_in_empty_folder_removes_hint(self):
        """核心回归：普通空文件夹里新建，引导必须消失且新笔记行出现。"""
        app = self.app
        self.assertEqual(len(_hints(app)), 1)   # 先确认引导在
        before = len(app.data["notes"])
        app.new_note()
        self.root.update_idletasks()
        self.root.update()
        self.assertEqual(len(app.data["notes"]), before + 1, "新笔记应写入数据")
        self.assertEqual(len(_hints(app)), 0,
                         "新建后引导文案必须消失（差量插入路径也要清）")
        nid = app.data["current_note"]
        self.assertIn(nid, app._rows, "新笔记行应出现在列表里")

    def test_new_note_in_empty_folder_no_duplicate_hint_after_refresh(self):
        """新建后再整列刷新，引导不应复活。"""
        app = self.app
        app.new_note()
        self.root.update_idletasks()
        app.refresh_notes()
        self.root.update_idletasks()
        self.root.update()
        self.assertEqual(len(_hints(app)), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
