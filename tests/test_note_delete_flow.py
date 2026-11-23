# -*- coding: utf-8 -*-
"""删除交互回归：只删点中的那条、正文不被误关、其余行不重建、内层不被压扁。

跑法：D:/Python/python_install/python.exe tests/test_note_delete_flow.py
"""
import os
import sys
import shutil
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import dpi                                              # noqa: E402
dpi.init()

import tkinter as tk                                    # noqa: E402
import store                                            # noqa: E402
import widgets                                          # noqa: E402
from store import Store                                 # noqa: E402


def _note(i):
    return {"id": "n%d" % i, "folder": "f_work",
            "content": [["t", "第 %d 条笔记" % i], ["t", "\n摘要"]],
            "pinned": False, "starred": False,
            "created": 1.0, "updated": 10.0 + i}


class DeleteFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="del_flow_")
        store._STORE = Store(os.path.join(cls.tmp, "notes.db"))
        import notes_app
        cls.root = tk.Tk()
        cls.root.withdraw()
        cls.app = notes_app.NotesApp(cls.root)
        cls.app._ask_confirm = lambda *a, **k: True
        cls.app._show_info = lambda *a, **k: None
        cls.root.update_idletasks()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.root.destroy()
        except Exception:
            pass
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        app = self.app
        app.data["notes"] = [_note(i) for i in (1, 2, 3, 4, 5)]
        app.data["current_folder"] = "f_all"
        app.data["current_note"] = "n2"
        store.store().recycle_clear()
        app.refresh_all()
        # 标签条跨用例会攒着上一轮的标签，而「删当前篇顺到谁」优先看标签邻居 ——
        # 清空它，才测得到「标签里没有 → 顺到列表里的下一条」。
        app._tabs = []
        app._tab_active = None
        app.select_note("n2")
        self.root.update_idletasks()

    def test_delete_other_row_keeps_editor_on_current_note(self):
        """右键删「不是正在看的那篇」时，正文不能跟着关掉。"""
        app = self.app
        app._popup_delete("n4")
        self.assertEqual(app.data["current_note"], "n2", "正文被牵连切走了")
        self.assertEqual(app._editor_note, "n2")
        self.assertIn("第 2 条笔记", app.text.get("1.0", "end-1c"))
        self.assertNotIn("n4", {n["id"] for n in app.data["notes"]})
        self.assertEqual(app._note_sel, {"n2"}, "列表高亮没跟着编辑器")

    def test_delete_current_note_opens_the_row_below(self):
        """删掉正在看的那篇：正文顺到「顶替它位置」的下一条（列表按修改时间倒序）。"""
        app = self.app
        app._popup_delete("n2")
        self.assertEqual(app.data["current_note"], "n1")
        self.assertEqual(app._editor_note, "n1")
        self.assertIn("第 1 条笔记", app.text.get("1.0", "end-1c"))

    def test_delete_last_row_falls_back_to_previous(self):
        """删的是列表最后一条：没有下一条可顶，退回上面那条。"""
        app = self.app
        app._tabs = []
        app._tab_active = None
        app.select_note("n1")
        app._popup_delete("n1")
        self.assertEqual(app.data["current_note"], "n2")

    def test_surviving_rows_are_not_rebuilt(self):
        """其余行必须是原来那几个控件：重建 = 肉眼可见地闪一下。"""
        app = self.app
        before = {nid: rec["wrap"] for nid, rec in app._rows.items() if nid != "n4"}
        app._popup_delete("n4")
        for nid, wrap in before.items():
            self.assertIs(app._rows[nid]["wrap"], wrap,
                          "第 %s 行被销毁重建了（列表会闪）" % nid)

    def test_scroll_panel_never_collapses_inner(self):
        """删行后算高度不许再走「压成 1×1」那一招。"""
        panel = self.app.note_panel
        calls = []
        orig = widgets.ScrollPanel._nudge_reqheight

        def spy(self):
            calls.append(1)
            orig(self)

        widgets.ScrollPanel._nudge_reqheight = spy
        try:
            self.app._popup_delete("n3")
            panel._update()
        finally:
            widgets.ScrollPanel._nudge_reqheight = orig
        self.assertEqual(calls, [], "又回去了：压扁内层 = 整列行闪没")
        self.assertGreater(panel.inner.winfo_height(), 1)

    def test_scrollregion_matches_rows_after_delete(self):
        panel = self.app.note_panel
        self.app._popup_delete("n3")
        panel.inner.update_idletasks()
        panel._update()
        want = 0
        for w in panel.inner.pack_slaves():
            want += w.winfo_reqheight() + widgets._pad_px(w.pack_info().get("pady", 0))
        got = int(panel.canvas.cget("scrollregion").split()[3])
        vis = panel.canvas.winfo_height()
        self.assertEqual(got, max(want, vis),
                         "scrollregion 没跟着行数收缩（滚动条会停在高位）")

    # ---------- 批量删除：一次删多条时的时序 ----------
    def _seed(self, n):
        """把列表换成 n 条笔记（批量删除的用例需要比 5 条更长的列表）。"""
        app = self.app
        app.data["notes"] = [_note(i) for i in range(1, n + 1)]
        app.data["current_folder"] = "f_all"
        app.data["current_note"] = "n1"
        app.refresh_all()
        app._tabs = []
        app._tab_active = None
        app.select_note("n1")
        self.root.update_idletasks()

    def test_rows_are_repacked_before_returning(self):
        """删完即已重排：最后一行底边 == 逐行请求高度之和。

        不成立的话，屏幕上会先画出「行没了、空位还在」—— 一次删好多条时，
        那片空位就是肉眼看到的整片空白。
        """
        self._seed(20)
        app = self.app
        panel = app.note_panel
        app._delete_notes(["n5", "n6", "n7", "n8"])
        want = panel._content_height()
        last = panel.inner.pack_slaves()[-1]
        self.assertEqual(last.winfo_y() + last.winfo_height(), want,
                         "行没在返回前重排（会闪一片空白）")

    def test_repaint_is_requested_after_batch_delete(self):
        """冻结重绘后必须要求整幅重画，否则屏幕留着删除前的旧像素（残影）。"""
        self._seed(20)
        app = self.app
        calls = []
        orig = widgets.ScrollPanel.repaint
        widgets.ScrollPanel.repaint = lambda self: (calls.append(1), orig(self))
        try:
            app._delete_notes(["n5", "n6", "n7"])
        finally:
            widgets.ScrollPanel.repaint = orig
        self.assertEqual(len(calls), 1, "没有触发整幅重画 → 残影")

    def test_batch_delete_skips_protected_notes(self):
        """系统笔记（内置说明）不许跟着一起删。"""
        self._seed(20)
        app = self.app
        sample = store.new_sample_note()
        app.data["notes"].insert(0, sample)
        app.refresh_all()
        self.root.update_idletasks()
        app._delete_notes([sample["id"], "n9"])
        ids = {n["id"] for n in app.data["notes"]}
        self.assertIn(sample["id"], ids, "系统笔记被批量删掉了")
        self.assertNotIn("n9", ids)

    def test_batch_delete_keeps_surviving_rows(self):
        self._seed(20)
        app = self.app
        before = {nid: rec["wrap"] for nid, rec in app._rows.items()
                  if nid not in ("n5", "n6", "n7")}
        app._delete_notes(["n5", "n6", "n7"])
        for nid, wrap in before.items():
            self.assertIs(app._rows[nid]["wrap"], wrap, "第 %s 行被重建了" % nid)

    def test_scroll_offset_is_handed_to_restore(self):
        """删行前记下的视口偏移必须原样交给「放回偏移」那一步。

        （真窗的滚动位置在 withdraw 状态下量不准，这里只钉住传参；钳取规则由
        `PanelOffsetTest` 按纯函数口径覆盖。）
        """
        self._seed(20)
        app = self.app
        panel = app.note_panel
        got = []
        orig = app._restore_panel_offset
        app._restore_panel_offset = lambda p, off: got.append((p, off))
        try:
            panel.canvas.configure(scrollregion=(0, 0, 300, 2000))
            panel.canvas.yview_moveto(0.4)
            self.root.update_idletasks()
            off = panel.canvas.canvasy(0)
            app._delete_notes(["n11", "n12"])
        finally:
            app._restore_panel_offset = orig
        self.assertEqual(len(got), 1)
        self.assertIs(got[0][0], panel)
        self.assertAlmostEqual(got[0][1], off, delta=1)

    # ---------- 彻底删除：连回收站都不进 ----------
    def _rc(self):
        return store.store().recycle_count()

    def test_normal_delete_still_writes_snapshot(self):
        before = self._rc()
        self.app._popup_delete("n4")
        self.assertEqual(self._rc(), before + 1,
                         "普通删除必须留回收站快照（不然「还原此项」就失效了）")

    def test_forever_delete_writes_no_snapshot(self):
        app = self.app
        before = self._rc()
        app._popup_delete("n4", forever=True)
        self.assertEqual(self._rc(), before, "彻底删除不该留回收站快照")
        self.assertNotIn("n4", {n["id"] for n in app.data["notes"]})

    def test_forever_delete_confirm_says_unrecoverable(self):
        app = self.app
        seen = []
        orig = app._ask_confirm
        app._ask_confirm = lambda t, m, **k: (seen.append((t, m, k)), True)[1]
        try:
            app._popup_delete("n4", forever=True)
        finally:
            app._ask_confirm = orig
        title, msg, kw = seen[0]
        self.assertEqual("彻底删除", title)
        self.assertIn("无法还原", msg, "彻底删除必须写明不可还原")
        self.assertEqual("彻底删除", kw.get("ok_text"))

    def test_batch_forever_delete_skips_recycle_bin(self):
        self._seed(20)
        app = self.app
        before = self._rc()
        app._delete_notes(["n5", "n6", "n7"], forever=True)
        self.assertEqual(self._rc(), before, "批量彻底删除不该留回收站快照")
        left = {n["id"] for n in app.data["notes"]}
        self.assertFalse({"n5", "n6", "n7"} & left)

    def test_folder_forever_delete_skips_recycle_bin(self):
        app = self.app
        fid = next(f["id"] for f in app.data["folders"]
                   if f["id"] not in store.SMART_FOLDERS
                   and f["id"] != store.SYSTEM_FOLDER)
        before = self._rc()
        app._delete_folders([fid], forever=True)
        self.assertEqual(self._rc(), before, "文件夹彻底删除不该留回收站快照")
        self.assertNotIn(fid, {f["id"] for f in app.data["folders"]})


class _Panel(object):
    """`_restore_panel_offset` 的最小替身：只需要内容高、视口高和 yview。"""

    def __init__(self, h, vis):
        self._h = h
        self._vis = vis
        self.moved = []
        self.canvas = self

    def _content_height(self):
        return self._h

    def winfo_height(self):
        return self._vis

    def yview_moveto(self, frac):
        self.moved.append(round(frac, 4))


class PanelOffsetTest(unittest.TestCase):
    """批量删行后滚动位置怎么放回去（纯函数口径，不建真窗）。"""

    def _run(self, h, vis, off):
        p = _Panel(h, vis)
        # 直接拿类上的原函数：挂到测试实例上会变成绑定方法，self 多占一个位置
        import notes_app
        notes_app.NotesApp._restore_panel_offset(None, p, off)
        return p

    def test_keeps_pixel_offset(self):
        p = self._run(1000, 200, 300)
        self.assertEqual(p.moved, [0.3])

    def test_clamps_to_bottom(self):
        """偏移超出新内容就贴底，否则视口会停在内容之外（露一片空白）。"""
        p = self._run(1000, 200, 5000)
        self.assertEqual(p.moved, [0.8])

    def test_content_shorter_than_view_goes_top(self):
        p = self._run(150, 200, 240)
        self.assertEqual(p.moved, [0.0])

    def test_zero_height_is_noop(self):
        """内容为空、连视口高度都量不到时什么都不做（别除以 0）。"""
        p = self._run(0, 0, 100)
        self.assertEqual(p.moved, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
