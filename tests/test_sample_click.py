# -*- coding: utf-8 -*-
"""内置说明笔记的「点击打开」回归测试（不建窗口）。

背景：说明笔记不许拖（钉在「系统」里），当初为了拦住拖拽，在 `_note_press` 里写了
`self._drag = None; return` —— 结果把「松手打开」也一起拦掉了：
把笔记装进编辑器是 `_finish_drag` 干的活，`_drag` 为空它就第一行退出。
用户在界面上看到的是「点说明笔记只高亮、正文不换」，也就是「打不开了」。

这里用桩对象直接驱动 `UIMixin` 的那两个方法，锁住三条不变式：
  1. 按下说明笔记后必须留一条 `started=False` 的待打开记录（不能是 None）；
  2. 那条记录永远不升级成拖拽（motion 不许生成浮签）；
  3. 松手必须调到 `select_note`（就是「打开」）。

运行：
    python -m unittest tests.test_sample_click -v
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from app_ui import UIMixin                                    # noqa: E402


class _Event(object):
    def __init__(self, x_root=100, y_root=200, state=0):
        self.x_root, self.y_root, self.state = x_root, y_root, state
        self.x = self.y = 6


class _Stub(object):
    """只实现 `_note_press` / `_drag_on_motion` / `_finish_drag` 会碰到的东西。"""

    note_press = UIMixin._note_press
    drag_on_motion = UIMixin._drag_on_motion
    finish_drag = UIMixin._finish_drag

    def __init__(self, notes):
        self.data = {"notes": notes, "current_note": None}
        self._note_sel = set()
        self._note_anchor = None
        self._drag = None
        self._drag_win = None
        self.opened = []
        self.drag_started = []

    # —— 被调用的协作方法 ——
    def _restyle_notes(self):
        pass

    def _drag_end(self):
        pass

    def _note_select(self, e, nid):
        self._note_sel = {nid}

    def select_note(self, nid, sync_folder=True):
        self.opened.append(nid)
        self.data["current_note"] = nid

    def _drag_start(self, nid):
        self.drag_started.append(nid)

    def _drag_move(self, x, y):
        pass

    def _drag_highlight(self, x, y):
        pass

    def _note_drop_target(self, x, y, nid):
        return None

    def _folder_under(self, x, y):
        return None

    def _select_after_move(self, nid):
        self.opened.append(nid)

    def _apply_note_reorder(self, tgt):
        pass

    def _move_note_to_folder(self, nid, fid):
        pass


SAMPLE = {"id": "n_sample", "folder": "f_system", "sample": True, "content": []}
NORMAL = {"id": "n_plain", "folder": "f_work", "content": []}


class TestSampleClick(unittest.TestCase):
    def test_sample_press_keeps_pending_open(self):
        app = _Stub([SAMPLE, NORMAL])
        app.note_press(_Event(), "n_sample")
        self.assertIsNotNone(app._drag, "说明笔记按下后不能把 _drag 清空，否则松手没人打开它")
        self.assertFalse(app._drag["started"])
        self.assertTrue(app._drag.get("sample"))
        self.assertEqual(app._drag["nid"], "n_sample")

    def test_normal_press_still_draggable(self):
        app = _Stub([SAMPLE, NORMAL])
        app.note_press(_Event(), "n_plain")
        self.assertIsNotNone(app._drag)
        self.assertFalse(app._drag.get("sample"),
                         "普通笔记要能拖，别被说明笔记的标记串味")

    def test_sample_never_becomes_drag(self):
        app = _Stub([SAMPLE, NORMAL])
        app.note_press(_Event(100, 200), "n_sample")
        # 指针跑出 8px 阈值以外也不许升级成拖拽
        app.drag_on_motion(_Event(400, 500))
        self.assertEqual(app.drag_started, [], "说明笔记不该生成浮签拖拽")
        self.assertFalse(app._drag["started"])

    def test_release_opens_note(self):
        app = _Stub([SAMPLE, NORMAL])
        app.note_press(_Event(), "n_sample")
        app.finish_drag(100, 200, 0)
        self.assertEqual(app.opened, ["n_sample"], "松手必须把说明笔记装进编辑器")
        self.assertEqual(app.data["current_note"], "n_sample")

    def test_shift_press_does_not_open(self):
        app = _Stub([SAMPLE, NORMAL])
        app.note_press(_Event(state=0x0001), "n_sample")
        self.assertEqual(app.opened, [], "Shift 多选不该打开笔记")
        self.assertIn("n_sample", app._note_sel)


if __name__ == "__main__":
    unittest.main(verbosity=2)
