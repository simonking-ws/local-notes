# -*- coding: utf-8 -*-
"""回收站清空 → 级联回收图片资源。

钉住的链：
  · 笔记删进回收站后，快照仍引用图片 → 图**不能**被 GC（还原要能用）；
  · 清空回收站（整站 / 单条 / 批量）后，快照没了 → 只被它引用的图被删；
  · 全库里还有别的笔记引用同一张图 → 清空也不能删（GC 按全库引用判定）；
  · 文件夹子树快照里的图，清空后同样回收。

⚠️ 不建 Tk：只 stub 掉清空流程里的 UI 刷新，直接测 store + _img_gc + 文件。
   建第二个 Tk 会触发跨解释器 _PHOTO_CACHE 污染（见项目 MEMORY），全量跑必炸。
"""
import os
import sys
import shutil
import struct
import tempfile
import unittest
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import dpi                                              # noqa: E402
dpi.init()

import store                                            # noqa: E402
import imgstore                                         # noqa: E402
from app_actions import ActionsMixin                     # noqa: E402
from store import Store, seed                           # noqa: E402


def make_png(w, h):
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))
    raw = b"".join(b"\x00" + b"\xff\x00\x00\xff" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


class _App(object):
    """只实现回收站清空 + _img_gc 会碰到的协作面，不建任何窗口。"""

    recycle_clear = ActionsMixin.recycle_clear
    recycle_purge_item = ActionsMixin.recycle_purge_item
    recycle_purge_items = ActionsMixin.recycle_purge_items
    _img_gc = ActionsMixin._img_gc

    def __init__(self, notes):
        self.data = {"notes": notes, "folders": [],
                     "current_folder": "f_all", "current_note": None}
        self._note_sel = set()
        self._note_anchor = None

    def _ask_confirm(self, *a, **k):
        return True

    def _show_info(self, *a, **k):
        pass

    def trunc(self, s, n):
        return str(s)[:n]

    def title_of(self, item):
        return str((item or {}).get("content") or "")

    def refresh_notes(self):
        pass

    def load_editor(self):
        pass

    def _restyle_recycle_bar(self):
        pass


class RecycleImageGCTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="recycle_gc_")
        self.old_base, self.old_store = store.BASE, store._STORE
        store.BASE = self.tmp
        self.s = Store(os.path.join(self.tmp, "notes.db"))
        self.s.reset(seed())
        store._STORE = self.s
        self.block = imgstore.make_block(make_png(8, 6), alt="x", ident="im1")
        self.note = {"id": "n_img", "folder": "f_work",
                     "content": [["t", "带图"], self.block]}
        # 模拟真实删除：笔记本体已从全库移除，只剩回收站快照（下面各测试再 put）
        self.app = _App([])

    def tearDown(self):
        store.BASE, store._STORE = self.old_base, self.old_store
        try:
            self.s.close()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _put_note_snapshot(self):
        return store.store().recycle_put("note", dict(self.note))

    def _img_exists(self):
        return os.path.isfile(imgstore.src_path(self.block[1]["src"]))

    def test_recycle_snapshot_keeps_image(self):
        """删进回收站：快照仍引用 → 图片保留（还原还能用）。"""
        rid = self._put_note_snapshot()
        # 快照在，GC 认它为引用方 —— 这正是清空前图片不该没的原因
        self.app._img_gc(resave=False)
        self.assertEqual(store.store().recycle_count(), 1)
        self.assertTrue(self._img_exists(), "还在回收站里，图不能先删")

    def test_clear_all_removes_image(self):
        """整站清空：快照没了 → 图片被回收。"""
        self._put_note_snapshot()
        self.assertTrue(self._img_exists())
        self.app.recycle_clear()
        self.assertEqual(store.store().recycle_count(), 0)
        self.assertFalse(self._img_exists(), "清空后图片没被回收")

    def test_clear_single_removes_image(self):
        """单条清空：只回收这一条快照引用的图。"""
        rid = self._put_note_snapshot()
        self.app.recycle_purge_item(rid)
        self.assertFalse(self._img_exists())

    def test_clear_multi_removes_image(self):
        """批量清空：多选清掉的图一并回收。"""
        rid = self._put_note_snapshot()
        self.app.recycle_purge_items([rid])
        self.assertFalse(self._img_exists())

    def test_clear_keeps_image_referenced_elsewhere(self):
        """全库另有笔记引用同一张图 → 清空也不能删。"""
        self._put_note_snapshot()
        # 另一条**仍在全库**的笔记引用同一 src（图片被多篇共享 / 引用判定按全库）
        self.app.data["notes"].append(
            {"id": "n_keep", "folder": "f_work",
             "content": [["t", "也用这张"], self.block]})
        self.app.recycle_clear()
        self.assertTrue(self._img_exists(), "全库还引用着，清空不能误删")

    def test_clear_folder_snapshot_removes_child_image(self):
        """文件夹子树快照里的图，清空后同样回收。"""
        store.store().recycle_put(
            "folder", {"folders": [{"id": "f_temp", "name": "临时"}],
                       "notes": [dict(self.note)]}, origin="f_temp")
        self.assertTrue(self._img_exists(), "文件夹在回收站里，图要留着")
        self.app.recycle_clear()
        self.assertFalse(self._img_exists(), "文件夹快照清空后，子笔记的图要回收")


if __name__ == "__main__":
    unittest.main(verbosity=2)
