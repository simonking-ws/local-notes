# -*- coding: utf-8 -*-
"""笔记色条颜色的分配与往返（不建窗口）。

颜色的三条硬要求，坏掉都不会报错、只会「用着用着觉得哪里不对」：
  1. **稳定** —— 同一篇笔记的颜色不能因为排序 / 切文件夹 / 重开程序而变；
  2. **均匀** —— 相邻新建的笔记不该连着撞色（撞了「区分」就白做）；
  3. **不出界** —— 只认调色板里的值，脏数据（NULL / 空串 / 乱写）读出来当没有。

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

import backup                                                  # noqa: E402
from store import (Store, NOTE_COLORS, ensure_note_color,      # noqa: E402
                   pick_note_color, _ensure_note_colors, _t,
                   resolve_adjacent_colors)


def n(nid, color=None):
    d = {"id": nid, "folder": "f_work", "content": _t(nid),
         "pinned": False, "starred": False, "created": 1.0, "updated": 1.0}
    if color is not None:
        d["color"] = color
    return d


class PickColorTest(unittest.TestCase):
    def test_first_round_is_distinct(self):
        """连续新建 8 条：8 个颜色各一次，一条都不撞。"""
        notes = []
        for i in range(len(NOTE_COLORS)):
            got = pick_note_color(notes)
            self.assertNotIn(got, [x["color"] for x in notes])
            notes.append(n("n%d" % i, got))
        self.assertEqual(sorted(x["color"] for x in notes), sorted(NOTE_COLORS))

    def test_picks_least_used(self):
        """用得最少的优先：删掉一条后，新笔记补的正是被删掉的那个颜色。"""
        notes = [n("a", NOTE_COLORS[0]), n("b", NOTE_COLORS[1])]
        self.assertEqual(pick_note_color(notes), NOTE_COLORS[2])

    def test_prefers_unused_color_in_same_folder(self):
        """同一个文件夹里优先挑没用过的色 —— 列表一次只看一个文件夹，撞色最显眼。"""
        notes = [n("a", NOTE_COLORS[0]), n("b", NOTE_COLORS[1]),
                 n("c", NOTE_COLORS[2]), n("d", NOTE_COLORS[0])]
        notes[2]["folder"] = "f_life"
        got = pick_note_color(notes, "f_work")
        self.assertNotIn(got, (NOTE_COLORS[0], NOTE_COLORS[1]))
        # 换到 f_life（只用了 c2）时也要避开它，哪怕别的颜色全局用量更多
        self.assertNotEqual(pick_note_color(notes, "f_life"), NOTE_COLORS[2])
        # 不限定文件夹时退回全局规则：用得最少的那个（c3 及之后都是 0 次）
        self.assertEqual(pick_note_color(notes), NOTE_COLORS[3])

    def test_avoid_neighbors(self):
        """新建笔记时避开上下两位的颜色；实在避不开也不能返回空值。"""
        notes = [n("a", NOTE_COLORS[0]), n("b", NOTE_COLORS[1])]
        got = pick_note_color(notes, "f_work", [NOTE_COLORS[0], NOTE_COLORS[1]])
        self.assertNotIn(got, (NOTE_COLORS[0], NOTE_COLORS[1]))
        # 8 色全被避开时退回原规则（宁可撞色也不能没有颜色）
        self.assertIn(pick_note_color(notes, "f_work", list(NOTE_COLORS)),
                      NOTE_COLORS)

    def test_ensure_is_idempotent(self):
        """已有颜色一律照旧 —— 这是「稳定」的全部意思。"""
        x = n("a", NOTE_COLORS[5])
        self.assertEqual(ensure_note_color(x, [x]), NOTE_COLORS[5])
        self.assertEqual(x["color"], NOTE_COLORS[5])

    def test_ensure_fills_missing(self):
        x = n("a")
        self.assertIn(ensure_note_color(x, [x]), NOTE_COLORS)
        self.assertIn(x["color"], NOTE_COLORS)

    def test_rejects_unknown_value(self):
        """不是调色板里的值就当没有，重新分配（不让脏数据直接进 Canvas）。"""
        x = n("a", "not-a-color")
        self.assertIn(ensure_note_color(x, [x]), NOTE_COLORS)

    def test_batch_fill_balances(self):
        notes = [n("n%d" % i) for i in range(10)]
        self.assertTrue(_ensure_note_colors({"notes": notes}))
        got = [x["color"] for x in notes]
        self.assertTrue(all(c in NOTE_COLORS for c in got))
        # 8 色 10 条：必然有两个颜色各用两次，其余各一次
        self.assertEqual(len(got), 10)
        self.assertEqual(len(set(got)), 8)
        self.assertEqual(max(got.count(c) for c in set(got)), 2)

    def test_batch_fill_noop_when_all_colored(self):
        notes = [n("a", NOTE_COLORS[0]), n("b", NOTE_COLORS[1])]
        self.assertFalse(_ensure_note_colors({"notes": notes}))


class AdjacentColorTest(unittest.TestCase):
    """相邻两条不能同色 —— 8 色必有重复，但重复色挨在一起就分不出是两篇了。"""

    def colors(self, notes):
        return [x["color"] for x in notes]

    def assert_no_adjacent(self, notes):
        c = self.colors(notes)
        for i in range(1, len(c)):
            self.assertNotEqual(c[i], c[i - 1], "第 %d 条与上一条同色" % i)

    def test_empty_and_single(self):
        self.assertEqual(resolve_adjacent_colors([]), ({}, False))
        one = [n("a", NOTE_COLORS[3])]
        got, changed = resolve_adjacent_colors(one)
        self.assertFalse(changed)
        self.assertEqual(got["a"], NOTE_COLORS[3])

    def test_distinct_list_untouched(self):
        """本来就不同色的列表一个像素都不该动（否则每次刷新整列换色）。"""
        notes = [n("n%d" % i, NOTE_COLORS[i]) for i in range(len(NOTE_COLORS))]
        got, changed = resolve_adjacent_colors(notes)
        self.assertFalse(changed)
        self.assertEqual(got, {x["id"]: x["color"] for x in notes})

    def test_duplicate_pair_is_split(self):
        notes = [n("a", NOTE_COLORS[0]), n("b", NOTE_COLORS[0])]
        _got, changed = resolve_adjacent_colors(notes)
        self.assertTrue(changed)
        self.assert_no_adjacent(notes)
        # 只改后面那条，前面的不动
        self.assertEqual(notes[0]["color"], NOTE_COLORS[0])

    def test_all_same_color(self):
        notes = [n("n%d" % i, NOTE_COLORS[0]) for i in range(12)]
        resolve_adjacent_colors(notes)
        self.assert_no_adjacent(notes)

    def test_alternating_is_worst_case(self):
        """A,A,B,B,A,A… 成对重复：一半的条目都得换，规则要能一路走通不打转。"""
        notes = [n("n%d" % i, NOTE_COLORS[(i // 2) % 2]) for i in range(12)]
        resolve_adjacent_colors(notes)
        self.assert_no_adjacent(notes)

    def test_twenty_notes_two_rounds(self):
        """超过 8 条必然有重复色（调色板上限），但相邻的绝不允许相同。"""
        notes = [n("n%d" % i, NOTE_COLORS[i % len(NOTE_COLORS)])
                 for i in range(20)]
        resolve_adjacent_colors(notes)
        self.assert_no_adjacent(notes)

    def test_avoids_next_color(self):
        """A,A,B 里的中间那条要挑 C —— 挑成 B 会把冲突往后推、多改一次。"""
        notes = [n("a", NOTE_COLORS[0]), n("b", NOTE_COLORS[0]),
                 n("c", NOTE_COLORS[1])]
        _got, changed = resolve_adjacent_colors(notes)
        self.assertTrue(changed)
        self.assertEqual(self.colors(notes),
                         [NOTE_COLORS[0], NOTE_COLORS[2], NOTE_COLORS[1]])

    def test_fills_missing_color(self):
        notes = [n("a"), n("b"), n("a2", NOTE_COLORS[0])]
        resolve_adjacent_colors(notes)
        self.assertTrue(all(x["color"] in NOTE_COLORS for x in notes))
        self.assert_no_adjacent(notes)

    def test_no_persist(self):
        """搜索视图只算这一次显示用什么颜色，不许改到笔记本体。"""
        notes = [n("a", NOTE_COLORS[0]), n("b", NOTE_COLORS[0])]
        got, changed = resolve_adjacent_colors(notes, persist=False)
        self.assertTrue(changed)
        self.assertEqual(notes[0]["color"], NOTE_COLORS[0])
        self.assertEqual(notes[1]["color"], NOTE_COLORS[0])
        self.assertNotEqual(got["a"], got["b"])

    def test_idempotent(self):
        """消过一次之后再跑一遍应当毫无改动（否则每次刷新都会换色）。"""
        notes = [n("n%d" % i, NOTE_COLORS[(i // 2) % 5]) for i in range(12)]
        first, changed = resolve_adjacent_colors(notes)
        self.assertTrue(changed)
        second, changed2 = resolve_adjacent_colors(notes)
        self.assertFalse(changed2)
        self.assertEqual(first, second)

    def test_order_comes_from_caller(self):
        """同样的颜色、不同的行序，得到的结果不同 —— 说明判定用的是显示顺序。"""
        a, b, c = (n("a", NOTE_COLORS[0]), n("b", NOTE_COLORS[0]),
                   n("c", NOTE_COLORS[3]))
        resolve_adjacent_colors([a, b, c])
        self.assertNotEqual(a["color"], b["color"])
        # 换成 a,c,b：a 与 b 不再相邻，就不该被迫改色
        a2, b2, c2 = (n("a", NOTE_COLORS[0]), n("b", NOTE_COLORS[0]),
                      n("c", NOTE_COLORS[3]))
        got, changed = resolve_adjacent_colors([a2, c2, b2])
        self.assertFalse(changed)
        self.assertEqual(got["b"], NOTE_COLORS[0])


class StoreColorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sujian-color-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_roundtrip(self):
        s = Store(os.path.join(self.tmp, "t.db"))
        try:
            s.save({"folders": [{"id": "f_work", "name": "工作"}],
                    "notes": [n("a", NOTE_COLORS[3]), n("b")],
                    "settings": {}}, full=True)
            got = {x["id"]: x for x in s.read_all()["notes"]}
            self.assertEqual(got["a"]["color"], NOTE_COLORS[3])
            # 没有颜色的笔记读回来不该凭空多出 color 键（否则界面分不清「已定」和「待定」）
            self.assertNotIn("color", got["b"])
        finally:
            s.close()

    def test_old_row_gives_null(self):
        """老库补列后是 NULL：读出来当「还没定」，不是空串。"""
        s = Store(os.path.join(self.tmp, "t2.db"))
        try:
            s.con.execute("INSERT INTO notes(id,folder,content,plain,pinned,starred,"
                          "created,updated,pos) VALUES(?,?,?,?,0,0,1,1,0)",
                          ("old", "f_work", "[]", ""))
            s.con.commit()
            got = {x["id"]: x for x in s.read_all()["notes"]}
            self.assertNotIn("color", got["old"])
        finally:
            s.close()

    def test_color_change_is_saved(self):
        """只改颜色（不动正文、不动 updated）也要落库 —— 签名里必须带 color。"""
        s = Store(os.path.join(self.tmp, "t3.db"))
        try:
            d = {"folders": [{"id": "f_work", "name": "工作"}],
                 "notes": [n("a", NOTE_COLORS[0])], "settings": {}}
            s.save(d, full=True)
            d["notes"][0]["color"] = NOTE_COLORS[1]
            s.save(d)
            got = {x["id"]: x for x in s.read_all()["notes"]}
            self.assertEqual(got["a"]["color"], NOTE_COLORS[1])
        finally:
            s.close()


class BackupColorTest(unittest.TestCase):
    def test_export_import_keeps_color(self):
        """颜色跟着笔记走：换台机器导入回来，还是那条色条。"""
        d = {"settings": {}, "styles": {},
             "folders": [{"id": "f_work", "name": "工作"}],
             "notes": [n("a", NOTE_COLORS[6])]}
        payload = backup.encode(d)
        self.assertEqual(payload["notes"][0]["color"], NOTE_COLORS[6])
        out = backup.decode(payload)[0]
        self.assertEqual(out["notes"][0]["color"], NOTE_COLORS[6])
        clean = backup.overwrite(out)
        got = [x for x in clean["notes"] if x["id"] == "a"][0]
        self.assertEqual(got["color"], NOTE_COLORS[6])

    def test_bad_color_is_dropped(self):
        d = {"settings": {}, "styles": {},
             "folders": [{"id": "f_work", "name": "工作"}],
             "notes": [n("a", "rgb(1,2,3)")]}
        clean = backup.overwrite(backup.decode(backup.encode(d))[0])
        self.assertNotIn("color", clean["notes"][0])

    def test_rev_ignores_color(self):
        """rev 只由正文算：换个颜色不该被当成「内容改过」。"""
        a, b = n("a", NOTE_COLORS[0]), n("a", NOTE_COLORS[1])
        self.assertEqual(backup.rev_of(a), backup.rev_of(b))


if __name__ == "__main__":
    unittest.main()
