# -*- coding: utf-8 -*-
"""左栏整列重建不许闪：刷新必须「冻结重绘 → 重建 → 解冻 → 整幅重画」。

删文件夹（连同里面的笔记一起删）走的是 `refresh_all → refresh_folders`，
而整列重建要把所有行销毁再建一遍。原来的顺序是「销毁 → 立刻 update_idletasks
→ 建行」，中间那一帧父窗口收到曝光重绘、行却还没回来，左栏就是空的 ——
用户看到的就是「删完文件夹，列表闪一下」。

这里钉住三件事：

  * `refresh_folders` 把「销毁 + 重建」整段包在 `frozen_paint` 里（中间一帧不发到屏幕）；
  * 冻结退出时行**已经建好**（否则把冻结套在空操作上也能过测试）；
  * 解冻后 `panel.repaint()` 被调用一次（否则冻结期间写进画布的滚动区不会触发重画，留残影）。

跑法：D:/Python/python_install/python.exe tests/test_folder_refresh_flicker.py
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
import app_ui                                           # noqa: E402
import store                                            # noqa: E402
from store import Store                                 # noqa: E402


class FolderRefreshFlickerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="folder_flicker_")
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
        # 只留智能分类与「系统」，真实文件夹换成自己这三只（甲 → 乙 是父子）
        app.data["folders"] = [f for f in app.data["folders"]
                               if f.get("smart") or f["id"] == "f_system"]
        app.data["folders"].extend([
            {"id": "f_a", "name": "甲"},
            {"id": "f_b", "name": "乙", "parent": "f_a"},
            {"id": "f_c", "name": "丙"},
        ])
        app.data["notes"] = [
            {"id": "fx1", "folder": "f_a", "content": [["t", "甲里的笔记"]],
             "pinned": False, "starred": False, "created": 1.0, "updated": 2.0},
            {"id": "fx2", "folder": "f_b", "content": [["t", "乙里的笔记"]],
             "pinned": False, "starred": False, "created": 1.0, "updated": 2.0},
        ]
        app.data["current_folder"] = "f_all"
        store.save_data(app.data)
        app.refresh_all()
        self.root.update_idletasks()
        self.root.update()

    # ---------- 刷新路径：冻结 + 重建 + 重画 ----------
    def _trace_refresh(self):
        """跑一次 refresh_folders，记录冻结窗口与退出瞬间的行数。"""
        seen = {"enter": 0, "exit": 0, "rows_on_exit": None, "repaint": 0}
        app = self.app
        orig_frozen = app_ui.frozen_paint
        orig_repaint = app.folder_panel.repaint

        class _Rec(object):
            def __init__(self, win):
                pass

            def __enter__(self):
                seen["enter"] += 1
                return self

            def __exit__(self, *exc):
                seen["exit"] += 1
                seen["rows_on_exit"] = len(app._folder_rows)
                return False

        app_ui.frozen_paint = _Rec
        app.folder_panel.repaint = lambda: seen.__setitem__(
            "repaint", seen["repaint"] + 1)
        try:
            app.refresh_folders()
        finally:
            app_ui.frozen_paint = orig_frozen
            app.folder_panel.repaint = orig_repaint
        return seen

    def test_refresh_folders_freezes_paint_while_rebuilding(self):
        seen = self._trace_refresh()
        self.assertEqual(seen["enter"], 1,
                         "整列重建没冻重绘：销毁与建行之间会露出空列（闪一下）")
        self.assertEqual(seen["exit"], 1, "冻结没成对退出")

    def test_rows_already_built_when_unfreezing(self):
        """冻结要罩住重建本身，不能只是「先冻住、退出后再建行」。"""
        seen = self._trace_refresh()
        self.assertIsNotNone(seen["rows_on_exit"])
        self.assertGreaterEqual(seen["rows_on_exit"], 5,
                                "解冻时行还没建回来，冻结等于白做")

    def test_repaint_called_after_unfreeze(self):
        seen = self._trace_refresh()
        self.assertEqual(seen["repaint"], 1,
                         "解冻后没强制整幅重画：冻结期间写进画布的滚动区不会重绘（留残影）")

    # ---------- 删文件夹：结果与刷新路径 ----------
    def test_delete_folder_leaves_no_ghost_rows(self):
        app = self.app
        app.del_folder("f_a")           # 连子层 f_b 与两篇笔记一起删
        self.root.update_idletasks()
        self.root.update()
        self.assertNotIn("f_a", app._folder_rows)
        self.assertNotIn("f_b", app._folder_rows, "子文件夹没跟着走，左栏会留幽灵行")
        for fid, row in app._folder_rows.items():
            self.assertTrue(row.winfo_exists(), "行对象已销毁却还挂在索引里：%s" % fid)
        ids = {n["id"] for n in app.data["notes"]}
        self.assertEqual(ids & {"fx1", "fx2"}, set(),
                         "文件夹里的笔记没跟着删掉")
        self.assertEqual(app.data["current_folder"], "f_all")

    def test_delete_folder_repaints_once(self):
        app = self.app
        calls = []
        orig = app.folder_panel.repaint
        app.folder_panel.repaint = lambda: (calls.append(1), orig())[1]
        try:
            app.del_folder("f_c")
        finally:
            app.folder_panel.repaint = orig
        self.assertGreaterEqual(len(calls), 1,
                                "删文件夹后左栏没重画：屏幕会停在删之前的样子")


if __name__ == "__main__":
    unittest.main(verbosity=2)
