# -*- coding: utf-8 -*-
"""删除笔记后回收站徽标必须立刻跟着变（回归）。

曾经的 bug：单条删除 / 批量删除都只做「差量移除行 + 刷文件夹计数」，不重建整列，
于是走不到 `refresh_notes()` 里那条 `_restyle_recycle_bar()`；用户要**点一下回收站**
（`recycle_open` 里才刷）才看到条数变化。

这两条路径各自补了一行 `_restyle_recycle_bar()`，这里把「删完就刷」钉住：
  · 单条 delete_note
  · 批量 _delete_notes
（删文件夹走 refresh_all → refresh_notes，本来就有，不在此列。）

    python tests\\test_recycle_badge_refresh.py
"""
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import app_actions                                            # noqa: E402
import app_ui                                                 # noqa: E402
from app_actions import ActionsMixin                           # noqa: E402
from app_ui import UIMixin                                     # noqa: E402


class _FakeStore(object):
    def __init__(self):
        self.puts = 0

    def recycle_put(self, *a, **kw):
        self.puts += 1

    def recycle_count(self):
        return self.puts


def _data():
    return {"notes": [{"id": "n1", "folder": "f_work"},
                      {"id": "n2", "folder": "f_work"}],
            "folders": [{"id": "f_work", "name": "工作"}],
            "current_folder": "f_work",
            "current_note": "n1"}


class _FakeApp(object):
    """只实现删除路径碰到的那些方法，别把整个 NotesApp 拖进来。"""

    def __init__(self):
        self.data = _data()
        self._note_sel = {"n1"}
        self._note_anchor = "n1"
        self._rows = {"n1": object()}      # 有行 → 走 _remove_note_rows 分支
        self._active_title = self._active_preview = self._active_date = ""
        self.recycle_restyled = 0
        self.folder_counts = 0
        self.loaded = 0
        self.refreshed = 0

    # --- 删除路径的桩 ---
    def is_protected_note(self, n):
        return False

    def _ask_confirm(self, *a, **kw):
        return True

    def _refuse_sample_delete(self, n=None):
        pass

    def _tab_drop(self, nid):
        return None

    def _remove_note_rows(self, ids):
        for nid in ids:
            self._rows.pop(nid, None)

    def refresh_notes(self):
        self.refreshed += 1

    def _refresh_folder_counts(self):
        self.folder_counts += 1

    def _restyle_notes(self):
        pass

    def _restyle_recycle_bar(self):
        self.recycle_restyled += 1

    def load_editor(self):
        self.loaded += 1

    def current_note(self):
        return next((n for n in self.data["notes"]
                     if n["id"] == self.data.get("current_note")), None)

    def visible_notes(self):
        # delete_note 要按列表顺序找「顶替它的那一条」；这个桩没有视图，直接给全量
        return list(self.data["notes"])


class TestRecycleBadgeRefresh(unittest.TestCase):
    def setUp(self):
        self.fake_store = _FakeStore()
        self._orig = (app_actions.store, app_actions.save_data,
                      app_ui.store, app_ui.save_data)
        app_actions.store = lambda: self.fake_store
        app_actions.save_data = lambda d: None
        app_ui.store = lambda: self.fake_store
        app_ui.save_data = lambda d: None
        self.app = _FakeApp()
        self.app.delete_note = types.MethodType(ActionsMixin.delete_note, self.app)
        self.app._delete_notes = types.MethodType(UIMixin._delete_notes, self.app)
        self.app._recycle_view = types.MethodType(ActionsMixin._recycle_view, self.app)

    def tearDown(self):
        (app_actions.store, app_actions.save_data,
         app_ui.store, app_ui.save_data) = self._orig

    def test_single_delete_refreshes_badge_immediately(self):
        self.app.delete_note()
        self.assertEqual(self.fake_store.puts, 1, "没进回收站")
        self.assertNotIn("n1", [n["id"] for n in self.app.data["notes"]])
        self.assertEqual(self.app.recycle_restyled, 1,
                         "单条删除后没刷回收站徽标（要等点一下才变）")

    def test_batch_delete_refreshes_badge_immediately(self):
        self.app._delete_notes(["n1", "n2"])
        self.assertEqual(self.fake_store.puts, 2)
        self.assertEqual(self.app.data["notes"], [])
        self.assertEqual(self.app.recycle_restyled, 1,
                         "批量删除后没刷回收站徽标（要等点一下才变）")

    def test_purge_timer_refreshes_badge_even_when_closed(self):
        """过期清理发生在用户看别处时：徽标也得跟上，不能等他点开回收站。"""
        self.app.recycle_open_calls = 0
        self.app.recycle_open = lambda: setattr(
            self.app, "recycle_open_calls", self.app.recycle_open_calls + 1)
        self.app.recycle_refresh_if_open = types.MethodType(
            ActionsMixin.recycle_refresh_if_open, self.app)
        self.app.data["current_folder"] = "f_work"     # 不在回收站视图
        self.app.recycle_refresh_if_open()
        self.assertEqual(self.app.recycle_restyled, 1,
                         "过期清理后徽标没刷（正开着别的视图）")
        self.assertEqual(self.app.recycle_open_calls, 0,
                         "没开回收站视图不该整列重画")


if __name__ == "__main__":
    unittest.main()
