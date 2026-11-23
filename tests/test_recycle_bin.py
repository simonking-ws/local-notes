# -*- coding: utf-8 -*-
"""回收站回归：数据层（独立表 / 7 天过期 / 还原）与真窗口交互。

跑法：D:/Python/python_install/python.exe tests/test_recycle_bin.py
"""
import os
import sys
import time
import shutil
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import dpi                                              # noqa: E402
dpi.init()

import tkinter as tk                                    # noqa: E402
import store                                            # noqa: E402
from store import (Store, seed, RECYCLE_FOLDER, RECYCLE_TTL_DAYS,  # noqa: E402
                   RECYCLE_TTL_MS, recycle_item_view, uid)


class RecycleStoreTest(unittest.TestCase):
    """不建窗口：只测独立表、过期与快照包装。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="recycle_store_")
        cls.db = os.path.join(cls.tmp, "notes.db")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.s = Store(self.db)
        self.s.reset(seed())
        self.old = store._STORE
        store._STORE = self.s

    def tearDown(self):
        store._STORE = self.old
        self.s.close()

    def test_put_all_get_remove_clear(self):
        rid1 = self.s.recycle_put("note", {"id": "n1", "content": []})
        self.s.recycle_put("note", {"id": "n2", "content": []})
        self.assertEqual(self.s.recycle_count(), 2)
        self.assertEqual(len(self.s.recycle_all()), 2)
        got = self.s.recycle_get(rid1)
        self.assertEqual(got["payload"]["id"], "n1")
        self.assertEqual(got["kind"], "note")
        self.assertGreater(got["deleted_at"], 0)
        self.s.recycle_remove(rid1)
        self.assertIsNone(self.s.recycle_get(rid1))
        self.assertEqual(self.s.recycle_count(), 1)
        self.s.recycle_clear()
        self.assertEqual(self.s.recycle_all(), [])

    def test_expired_after_seven_days(self):
        now = time.time() * 1000
        self.s.recycle_put("note", {"id": "old"}, now=now - RECYCLE_TTL_MS - 1000)
        self.s.recycle_put("note", {"id": "fresh"}, now=now - RECYCLE_TTL_MS + 1000)
        self.assertEqual(self.s.recycle_purge(now=now), 1)
        left = self.s.recycle_all()
        self.assertEqual(len(left), 1)
        self.assertEqual(left[0]["payload"]["id"], "fresh")
        # 边界之内不能误删：再清一次应该是 0
        self.assertEqual(self.s.recycle_purge(now=now), 0)

    def test_reset_also_clears_recycle(self):
        self.s.recycle_put("note", {"id": "n"})
        self.s.reset(seed())
        self.assertEqual(self.s.recycle_count(), 0)

    def test_view_is_fake_note_with_recycle_id(self):
        rid = self.s.recycle_put(
            "note", {"id": "real_note", "content": [["t", "真标题"], ["t", "\n摘要"]]})
        rec = self.s.recycle_get(rid)
        v = recycle_item_view(rec)
        self.assertEqual(v["id"], rid)      # 关键：不是原笔记 id
        self.assertEqual(v["folder"], RECYCLE_FOLDER)
        self.assertEqual(v["content"][0][1], "真标题")
        self.assertIn("天后自动清除", v["content"][1][1])

        # 真实「删除文件夹」写入的 payload 只有 folders / notes，没有 singular folder 键；
        # 名称靠 origin（被删文件夹 id）从 folders 里取。旧代码误读 payload.folder，
        # 标题永远退化成「未命名」——这条断言就是回归点。
        fid = self.s.recycle_put(
            "folder", {"folders": [{"id": "f", "name": "项目"},
                                    {"id": "sub", "name": "子层"}],
                       "notes": [{"id": "n"}]},
            origin="f")
        fv = recycle_item_view(self.s.recycle_get(fid))
        self.assertIn("文件夹 · 项目", fv["content"][0][1])
        self.assertIn("1 个子文件夹 · 1 条笔记", fv["content"][1][1])

    def test_folder_view_shows_deleted_folder_name_not_child(self):
        """多层子孙一起进站时，标题必须是用户删的那一层，不能串成子文件夹。"""
        now = time.time() * 1000
        # origin 指向根文件夹 f_root；folders 里子文件夹排在前面，验证按 id 精确定位
        rid = self.s.recycle_put(
            "folder",
            {"folders": [{"id": "f_child", "name": "子层", "parent": "f_root"},
                         {"id": "f_root", "name": "工作资料"}],
             "notes": []},
            origin="f_root", now=now)
        v = recycle_item_view(self.s.recycle_get(rid))
        self.assertIn("工作资料", v["content"][0][1])
        self.assertNotIn("子层 ·", v["content"][0][1])
        self.assertIn("1 个子文件夹", v["content"][1][1])

        # 再来一份：origin 指向子文件夹时，标题就该是子文件夹自己的名字
        rid2 = self.s.recycle_put(
            "folder",
            {"folders": [{"id": "f_root", "name": "工作资料"},
                         {"id": "f_child", "name": "子层", "parent": "f_root"}],
             "notes": []},
            origin="f_child", now=now)
        v2 = recycle_item_view(self.s.recycle_get(rid2))
        self.assertIn("文件夹 · 子层", v2["content"][0][1])


class RecycleUITest(unittest.TestCase):
    """真窗口：删除 → 回收站 → 还原 / 清空，以及「不可打开」。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="recycle_ui_")
        cls.db = os.path.join(cls.tmp, "notes.db")
        store._STORE = Store(cls.db)
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
        self.app.data["notes"] = [
            {"id": "rn1", "folder": "f_work", "content": [["t", "可删笔记"], ["t", "\n正文"]],
             "pinned": False, "starred": False, "created": 1.0, "updated": 2.0},
            {"id": "rn2", "folder": "f_temp", "content": [["t", "文件夹里的笔记"]],
             "pinned": False, "starred": False, "created": 1.0, "updated": 2.0},
        ]
        self.app.data["folders"] = [f for f in self.app.data["folders"]
                                    if f["id"] not in ("f_temp",)]
        self.app.data["folders"].append({"id": "f_temp", "name": "临时"})
        self.app.data["current_folder"] = "f_all"
        self.app.data["current_note"] = "rn1"
        store.store().recycle_clear()
        self.app.refresh_all()
        self.root.update_idletasks()
        self.root.update()

    def test_delete_note_goes_to_recycle_and_can_restore(self):
        app = self.app
        app.delete_note()
        self.assertEqual(store.store().recycle_count(), 1)
        self.assertNotIn("rn1", {n["id"] for n in app.data["notes"]})
        rec = store.store().recycle_all()[0]
        self.assertEqual(rec["kind"], "note")
        self.assertEqual(rec["payload"]["id"], "rn1")
        app.recycle_restore(rid=rec["id"])
        self.assertIn("rn1", {n["id"] for n in app.data["notes"]})
        self.assertEqual(store.store().recycle_count(), 0)

    def test_delete_folder_keeps_subtree_and_restores_it(self):
        app = self.app
        app.data["folders"].append({"id": "f_sub", "name": "子层", "parent": "f_temp"})
        app.data["notes"].append(
            {"id": "rn3", "folder": "f_sub", "content": [["t", "子层笔记"]],
             "pinned": False, "starred": False, "created": 1.0, "updated": 2.0})
        app.del_folder("f_temp")
        self.assertEqual(store.store().recycle_count(), 1)
        rec = store.store().recycle_all()[0]
        self.assertEqual(rec["kind"], "folder")
        ids = {f["id"] for f in rec["payload"]["folders"]}
        self.assertEqual(ids, {"f_temp", "f_sub"})
        self.assertEqual({n["id"] for n in rec["payload"]["notes"]}, {"rn2", "rn3"})
        app.recycle_restore(rid=rec["id"])
        fids = {f["id"] for f in app.data["folders"]}
        self.assertIn("f_temp", fids)
        self.assertIn("f_sub", fids)
        self.assertEqual(next(f for f in app.data["folders"]
                              if f["id"] == "f_sub").get("parent"), "f_temp")
        nids = {n["id"] for n in app.data["notes"]}
        self.assertTrue({"rn2", "rn3"} <= nids)

    def test_deleted_folder_row_shows_its_name(self):
        """真实删除文件夹后，回收站那一行要显示文件夹名，不能是「未命名」。"""
        app = self.app
        app.del_folder("f_temp")            # setUp 里这个名字是「临时」
        app.recycle_open()
        items = app.visible_notes()
        self.assertEqual(len(items), 1)
        title = items[0]["content"][0][1]
        self.assertIn("临时", title)
        self.assertNotIn("未命名", title)

    def test_recycle_view_never_loads_note(self):
        app = self.app
        app.delete_note()                       # rn1 进回收站
        app.recycle_open()
        self.assertEqual(app.data["current_folder"], RECYCLE_FOLDER)
        items = app.visible_notes()
        self.assertEqual(len(items), 1)
        rid = items[0]["id"]
        self.assertNotEqual(rid, "rn1")
        # 模拟点击那一行：只选中，不装载正文
        app._note_press(type("E", (), {"state": 0, "x_root": 10, "y_root": 10})(), rid)
        self.assertIsNone(app._drag)
        self.assertIn("不能打开", app.text.get("1.0", "end-1c"))
        self.assertEqual(app._editor_note, None)
        # 直接调 select_note 也不能绕过
        app.select_note("rn1")
        self.assertEqual(app._editor_note, None)
        app.recycle_clear()
        self.assertEqual(store.store().recycle_count(), 0)

    def test_item_right_click_menu_restores_only_that_item(self):
        app = self.app
        r1 = store.store().recycle_put("note", {"id": "a", "content": []})
        r2 = store.store().recycle_put("note", {"id": "b", "content": []})
        app.recycle_open()

        class E(object):
            x_root = 200
            y_root = 200

        app._popup(E(), r1)
        self.root.update()
        win = [w for w in self.root.winfo_children()
               if str(w.winfo_class()) == "Toplevel"][-1]
        labels = []
        for c in win.winfo_children():
            if str(c.winfo_class()) == "Canvas":
                for item in c.find_all():
                    try:
                        if c.type(item) == "text":
                            labels.append(str(c.itemcget(item, "text")))
                    except Exception:
                        pass
        self.assertIn("还原此项", " ".join(labels))
        win.destroy()
        app.recycle_restore(rid=r1)
        left = {r["id"] for r in store.store().recycle_all()}
        self.assertEqual(left, {r2})

    def test_recycle_bar_right_click_menu_has_restore_and_clear(self):
        app = self.app
        store.store().recycle_put("note", {"id": "menu_n", "content": []})
        app.recycle_open()

        class E(object):
            x_root = 200
            y_root = 200

        app._recycle_popup(E())
        self.root.update()
        menu = [w for w in self.root.winfo_children()
                if str(w.winfo_class()) == "Toplevel"][-1]
        labels = []
        for c in menu.winfo_children():
            if str(c.winfo_class()) == "Canvas":
                for item in c.find_all():
                    try:
                        if c.type(item) == "text":
                            labels.append(str(c.itemcget(item, "text")))
                    except Exception:
                        pass
        joined = " ".join(labels)
        self.assertIn("还原", joined)
        self.assertIn("清空", joined)
        menu.destroy()

    def test_restore_all_folder_first(self):
        app = self.app
        store.store().recycle_put("note", {"id": "n_x", "folder": "f_temp",
                                     "content": [["t", "X"]],
                                     "created": 1.0, "updated": 1.0})
        store.store().recycle_put(
            "folder", {"folders": [{"id": "f_temp", "name": "临时"}],
                       "notes": []})
        app.recycle_open()
        app.recycle_restore(restore_all=True)
        self.assertEqual(store.store().recycle_count(), 0)
        self.assertIn("f_temp", {f["id"] for f in app.data["folders"]})
        self.assertIn("n_x", {n["id"] for n in app.data["notes"]})

    def test_recycle_bar_style_distinct_and_theme_safe(self):
        """回收站入口是独立工具槽：分隔线 + 描边卡片，选中态和换肤都要稳。

        换肤这一条是回归点：回收站就地刷新、不像文件夹行整列重建，卡片画布的
        「外侧底色」若漏了跟着换，深色主题下圆角会留着旧主题的浅色楔形。
        """
        app = self.app
        parts = app._recycle_parts
        self.assertIn("sep", parts, "回收站入口应该有顶部分隔线")
        self.assertTrue(parts["row"]._card._border_ink,
                        "回收站卡片应该有描边，区别于文件夹行")

        def lum(color):
            return sum(int(color[i:i + 2], 16) for i in (1, 3, 5))

        app.data["current_folder"] = "f_all"
        app._restyle_recycle_bar()
        normal = parts["row"]._base_bg
        app.data["current_folder"] = RECYCLE_FOLDER
        app._restyle_recycle_bar()
        active = parts["row"]._base_bg
        self.assertNotEqual(normal, active, "选中态和常态底色不该一样")
        self.assertLess(lum(active), lum(normal),
                        "浅色主题下选中态应比常态沉一档")

        from theme import _mix_hex, _ink_target
        old = app.data.get("settings", {}).get("theme", "forest")
        try:
            app.set_theme("midnight")
            ink = _ink_target(app.pal["side"])
            self.assertEqual(str(parts["row"]._card.cget("bg")), app.pal["side"],
                             "换肤后卡片外侧底色没跟着变，圆角会留旧色楔形")
            self.assertEqual(str(parts["sep"].cget("bg")),
                             _mix_hex(app.pal["side"], ink, 0.18),
                             "分隔线换肤后没跟着变")
        finally:
            app.set_theme(old)
            app.data["current_folder"] = "f_all"
            app._restyle_recycle_bar()


    # ---------- 回收站行的右键菜单 / 清空当前项 ----------
    def _popup_labels(self):
        """读出当前弹出的菜单里所有文字（菜单是自绘 Canvas）。"""
        self.root.update()
        win = [w for w in self.root.winfo_children()
               if str(w.winfo_class()) == "Toplevel"][-1]
        labels = []
        for c in win.winfo_children():
            if str(c.winfo_class()) == "Canvas":
                for item in c.find_all():
                    try:
                        if c.type(item) == "text":
                            labels.append(str(c.itemcget(item, "text")))
                    except Exception:
                        pass
        win.destroy()
        return labels

    def test_recycle_open_clears_folder_highlight(self):
        """点回收站后，之前选中的文件夹不能还亮着（两处同时高亮很误导）。"""
        app = self.app
        app.select_folder("f_work")
        self.assertIn("f_work", app._folder_sel)
        app.recycle_open()
        self.assertEqual(app._folder_sel, set(), "回收站视图里还留着文件夹多选")
        self.assertIsNone(app._folder_anchor)
        row = app._folder_rows["f_work"]
        self.assertEqual(row._base_bg, app.pal["side"],
                         "文件夹行底色没退回常态，说明还挂着选中态")
        app.select_folder("f_all")

    def test_item_menu_only_restore_and_purge(self):
        """单条右键只留「还原此项 / 清空当前笔记」，批量的回左栏入口。"""
        app = self.app
        rid = store.store().recycle_put(
            "note", {"id": "m1", "content": [["t", "菜单笔记"]]})
        store.store().recycle_put("note", {"id": "m2", "content": []})
        app.recycle_open()

        class E(object):
            x_root = 200
            y_root = 200

        app._popup(E(), rid)
        joined = " ".join(self._popup_labels())
        self.assertIn("还原此项", joined)
        self.assertIn("清空当前笔记", joined)
        self.assertNotIn("清空回收站", joined, "单条菜单里不该出现整站清空")
        self.assertNotIn("还原全部", joined, "还原全部应归左下角回收站菜单")

    def test_item_menu_purge_label_for_folder(self):
        """文件夹那一行不能写成「笔记」，否则用户以为只删了里面的一篇。"""
        app = self.app
        rid = store.store().recycle_put(
            "folder", {"folders": [{"id": "f_temp", "name": "临时"}], "notes": []},
            origin="f_temp")
        app.recycle_open()

        class E(object):
            x_root = 200
            y_root = 200

        app._popup(E(), rid)
        joined = " ".join(self._popup_labels())
        self.assertIn("清空当前文件夹", joined)

    def test_purge_item_removes_only_that_one(self):
        app = self.app
        r1 = store.store().recycle_put(
            "note", {"id": "p1", "content": [["t", "第一条"]]})
        r2 = store.store().recycle_put(
            "note", {"id": "p2", "content": [["t", "第二条"]]})
        app.recycle_open()
        app.recycle_purge_item(r1)
        self.assertEqual(store.store().recycle_count(), 1)
        self.assertEqual({r["id"] for r in store.store().recycle_all()}, {r2})
        self.assertEqual(len(app.visible_notes()), 1, "列表没跟着重画")
        self.assertNotIn(r1, app._note_sel)

    def test_purge_item_cancel_keeps_item(self):
        app = self.app
        rid = store.store().recycle_put("note", {"id": "c1", "content": []})
        app.recycle_open()
        app._ask_confirm = lambda *a, **k: False
        try:
            app.recycle_purge_item(rid)
        finally:
            app._ask_confirm = lambda *a, **k: True
        self.assertEqual(store.store().recycle_count(), 1, "取消后不该删掉")

    # ---------- 多选右键：批量清空 ----------
    def _put_notes(self, n):
        return [store.store().recycle_put(
                    "note", {"id": "q%d" % i, "content": [["t", "第 %d 条" % i]]})
                for i in range(n)]

    def test_multi_selection_menu_offers_batch_purge(self):
        """Shift/Ctrl 多选后右键：清空要按选中集合走，不能只剩「当前这一条」。"""
        app = self.app
        r1, r2, r3 = self._put_notes(3)
        app.recycle_open()
        app._note_sel = {r1, r2}
        app._note_anchor = r1

        class E(object):
            x_root = 200
            y_root = 200

        app._popup(E(), r1)
        joined = " ".join(self._popup_labels())
        self.assertIn("清空选中的 2 项", joined, "多选时没给批量清空")
        self.assertIn("还原选中的 2 项", joined)
        self.assertNotIn("清空当前笔记", joined,
                         "多选时还留着单条口径，会让人以为只清一条")

    def test_purge_items_removes_only_selected(self):
        app = self.app
        r1, r2, r3 = self._put_notes(3)
        app.recycle_open()
        app._note_sel = {r1, r2}
        app._note_anchor = r1
        app.recycle_purge_items([r1, r2])
        left = {r["id"] for r in store.store().recycle_all()}
        self.assertEqual(left, {r3}, "批量清空动了没选中的项目")
        self.assertEqual(len(app.visible_notes()), 1, "列表没跟着重画")
        self.assertEqual(app._note_sel, set(), "清完还留着选中集合")
        self.assertIsNone(app._note_anchor)

    def test_purge_items_cancel_keeps_all(self):
        app = self.app
        r1, r2 = self._put_notes(2)
        app.recycle_open()
        app._ask_confirm = lambda *a, **k: False
        try:
            app.recycle_purge_items([r1, r2])
        finally:
            app._ask_confirm = lambda *a, **k: True
        self.assertEqual(store.store().recycle_count(), 2, "取消后不该删掉任何一条")

    def test_purge_items_reports_mixed_composition(self):
        """文件夹与笔记混选时点明构成：清一个文件夹会连带它里面的笔记。"""
        app = self.app
        rid_n = store.store().recycle_put("note", {"id": "mx1", "content": []})
        rid_f = store.store().recycle_put(
            "folder", {"folders": [{"id": "f_mx", "name": "混合"}], "notes": []},
            origin="f_mx")
        app.recycle_open()
        seen = {}

        def fake(title, msg, **kw):
            seen["title"] = title
            seen["msg"] = msg
            return False

        app._ask_confirm = fake
        try:
            app.recycle_purge_items([rid_n, rid_f])
        finally:
            app._ask_confirm = lambda *a, **k: True
        self.assertEqual("清空选中的 2 项", seen["title"])
        self.assertIn("1 个文件夹", seen["msg"])
        self.assertIn("1 条笔记", seen["msg"])
        self.assertIn("无法还原", seen["msg"])

    def test_restore_selected_restores_all_marked(self):
        """菜单里给了「还原选中的 N 项」，底层就得真按选中集合还原。"""
        app = self.app
        r1, r2, r3 = self._put_notes(3)
        app.recycle_open()
        app._note_sel = {r1, r2}
        before = len(app.data["notes"])
        app.recycle_restore()                  # 不传 rid / 不传 restore_all = 按选中
        self.assertEqual(store.store().recycle_count(), 1, "没选中的那条不该被还原")
        self.assertEqual(len(app.data["notes"]), before + 2, "选中的两条都要回来")
        self.assertEqual({r["id"] for r in store.store().recycle_all()}, {r3})

    def test_purge_items_with_stale_ids_is_quiet(self):
        """记录已被定时清理带走时不能炸，也不能留下「清空 0 项」的确认框。"""
        app = self.app
        app.recycle_open()
        app._note_sel = {"gone-1", "gone-2"}
        app.recycle_purge_items(["gone-1", "gone-2"])
        self.assertEqual(app._note_sel, set())
        self.assertEqual(store.store().recycle_count(), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
