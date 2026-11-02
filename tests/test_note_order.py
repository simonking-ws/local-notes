# -*- coding: utf-8 -*-
"""笔记列表「手动顺序」的纯逻辑测试（不建窗口）。

覆盖三件改坏了不会报错、只会悄悄排错的事：
  1. `note_sort_key` 的分组优先级 —— 置顶 > 拖过（手动顺序）> 星标 > 没拖过 > 修改时间；
  2. `ensure_note_order` 只在第一次拖拽时固化顺序，且相对顺序一点都不许变；
  3. `order` 在库里的往返（列名是 `ord`，老库补列必须允许 NULL）。

运行：
    python -m unittest discover -s tests -v
"""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from store import Store, _t                                  # noqa: E402
from notes_app import NotesApp                               # noqa: E402


def n(nid, pinned=False, starred=False, updated=1.0, order=None, folder="f_work"):
    d = {"id": nid, "folder": folder, "content": _t(nid),
         "pinned": pinned, "starred": starred, "created": 1.0, "updated": updated}
    if order is not None:
        d["order"] = order
    return d


class _Fake(object):
    """只提供排序那几支需要的 self.data。

    规则本身一律借 NotesApp 上的实现（下面四个绑定），不在测试里另抄一份 ——
    抄一份就等于测了个平行宇宙。
    """

    def __init__(self, notes):
        self.data = {"notes": notes}


_Fake._note_order = staticmethod(NotesApp._note_order)
_Fake.note_sort_key = NotesApp.note_sort_key
_Fake.ensure_note_order = NotesApp.ensure_note_order
_Fake.top_note_order = NotesApp.top_note_order


def ids_by(notes):
    fake = _Fake(notes)
    return [x["id"] for x in sorted(notes, key=lambda x: fake.note_sort_key(x))]


class SortKeyTest(unittest.TestCase):
    def test_pinned_then_manual_then_starred_then_updated(self):
        """置顶 > 手动顺序 > 星标 > 修改时间。

        星标**排在手动顺序之后**：新建的笔记会拿到一个最小的 order，用户要求
        它排到列表第一个（置顶笔记除外），不能被几条星标笔记压在下面。
        """
        notes = [n("plain_new", updated=900),
                 n("manual", updated=10, order=5000),
                 n("star", starred=True, updated=5),
                 n("pin", pinned=True, updated=1)]
        self.assertEqual(ids_by(notes), ["pin", "manual", "star", "plain_new"])

    def test_star_still_beats_untouched_notes(self):
        """没设过 order 的老数据：星标仍然优先（这一支的行为一点没变）。"""
        notes = [n("plain", updated=900), n("star", starred=True, updated=5)]
        self.assertEqual(ids_by(notes), ["star", "plain"])

    def test_new_note_goes_above_starred(self):
        """新建笔记（order 被赋成最小）要排在星标笔记之前、置顶笔记之后。"""
        notes = [n("pin", pinned=True, order=-9000.0),
                 n("star", starred=True, order=2048.0),
                 n("plain", order=4096.0),
                 n("fresh", order=-1024.0)]
        self.assertEqual(ids_by(notes), ["pin", "fresh", "star", "plain"])

    def test_manual_order_beats_updated(self):
        notes = [n("a", updated=10, order=200), n("b", updated=999, order=100)]
        self.assertEqual(ids_by(notes), ["b", "a"])

    def test_without_order_falls_back_to_recent_first(self):
        notes = [n("old", updated=10), n("new", updated=99)]
        self.assertEqual(ids_by(notes), ["new", "old"])


class EnsureOrderTest(unittest.TestCase):
    def test_freezes_current_order_and_is_idempotent(self):
        notes = [n("a", updated=10), n("b", updated=30), n("c", updated=20)]
        fake = _Fake(notes)
        fake.ensure_note_order()
        before = [x["id"] for x in sorted(notes, key=lambda x: fake.note_sort_key(x))]
        self.assertEqual(before, ["b", "c", "a"])          # 修改时间倒序
        vals = {x["id"]: x["order"] for x in notes}
        self.assertEqual(len(set(vals.values())), 3)       # 各不相同，才有插空余地
        # 再调一次不许改值（每次保存都调用它）
        fake.ensure_note_order()
        self.assertEqual({x["id"]: x["order"] for x in notes}, vals)

    def test_skips_when_every_note_already_has_order(self):
        notes = [n("a", order=0.0), n("b", order=1024.0)]
        _Fake(notes).ensure_note_order()
        self.assertEqual([x["order"] for x in notes], [0.0, 1024.0])

    def test_mixed_library_keeps_relative_order(self):
        """一半有 order、一半没有：补编号之后相对顺序必须与补之前一致。"""
        notes = [n("m1", order=500.0), n("u1", updated=900), n("m2", order=100.0)]
        fake = _Fake(notes)
        before = [x["id"] for x in sorted(notes, key=lambda x: fake.note_sort_key(x))]
        fake.ensure_note_order()
        after = [x["id"] for x in sorted(notes, key=lambda x: fake.note_sort_key(x))]
        self.assertEqual(before, after)
        self.assertTrue(all("order" in x for x in notes))


class TopOrderTest(unittest.TestCase):
    def test_new_note_goes_above_same_group(self):
        notes = [n("a", order=0.0), n("b", order=1024.0)]
        fresh = n("new")
        notes.append(fresh)
        fake = _Fake(notes)
        self.assertEqual(fake.top_note_order(fresh), -1024.0)

    def test_only_same_pin_group_counts(self):
        notes = [n("pin", pinned=True, order=-5000.0),
                 n("star", starred=True, order=-3000.0),
                 n("plain", order=2048.0)]
        fresh = n("new")
        notes.append(fresh)
        fake = _Fake(notes)
        # 只按「置顶」分组：星标笔记现在也参与比较（排序键里手动顺序压在星标之上），
        # 所以取 star 的 -3000 再减一格 —— 结果要能压过星标，这正是用户要的。
        self.assertEqual(fake.top_note_order(fresh), -4024.0)

    def test_empty_group_starts_at_zero(self):
        from notes_app import NotesApp
        fresh = n("only")
        self.assertEqual(_Fake([fresh]).top_note_order(fresh), 0.0)

    def test_folder_filter(self):
        notes = [n("other", order=-9000.0, folder="f_life")]
        fresh = n("new", folder="f_work")
        notes.append(fresh)
        fake = _Fake(notes)
        self.assertEqual(fake.top_note_order(fresh, folder="f_work"), 0.0)


class StoreOrderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sujian-ord-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_order_roundtrip(self):
        s = Store(os.path.join(self.tmp, "t.db"))
        try:
            d = {"folders": [{"id": "f_work", "name": "工作"}],
                 "notes": [n("a", order=1536.5), n("b")],
                 "settings": {}}
            s.save(d, full=True)
            got = {x["id"]: x for x in s.read_all()["notes"]}
            self.assertEqual(got["a"]["order"], 1536.5)
            # 没设过顺序的笔记读回来不该凭空多出 order 键（否则会被排到「拖过」那档）
            self.assertNotIn("order", got["b"])
        finally:
            s.close()

    def test_null_order_column_on_old_rows(self):
        """老库补列后是 NULL：读回来就是「没设过」，不是 0。"""
        s = Store(os.path.join(self.tmp, "t2.db"))
        try:
            s.con.execute("INSERT INTO notes(id,folder,content,plain,pinned,starred,"
                          "created,updated,pos) VALUES(?,?,?,?,0,0,1,1,0)",
                          ("old", "f_work", "[]", ""))
            s.con.commit()
            got = {x["id"]: x for x in s.read_all()["notes"]}
            self.assertNotIn("order", got["old"])
        finally:
            s.close()


if __name__ == "__main__":
    unittest.main()
