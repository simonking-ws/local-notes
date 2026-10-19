# -*- coding: utf-8 -*-
"""数据层最小测试（零第三方依赖，标准库 unittest 即可跑）。

覆盖 store.py 里那些「改坏了不报错、只是悄悄出错」的地方：
序列化工具、旧数据改名、SQLite 读写往返、增量写、搜索（含 LIKE 通配转义）、
导入归一化与合并。

运行：
    python tests\\test_store.py
或：
    python -m unittest discover -s tests -v
"""
import os
import sys
import json
import shutil
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))      # 源码在 scripts/ 下

import store                                                    # noqa: E402
import backup                                                   # noqa: E402
from store import (Store, plain_of, _t, _cb, seed, _fix_sample_note,  # noqa: E402
                   _mark_sample_note, _upgrade_sample_note, like_escape,
                   folder_children, folder_depth, folder_ancestors, subtree_ids,
                   normalize_folder_parents, set_all_collapsed, MAX_FOLDER_DEPTH)
from md_conv import TODO_OFF, TODO_ON, TODO_GAP, TODO_GAP_TAG, LI_INDENT   # noqa: E402


def note(nid, text, folder="f_work", pinned=False, starred=False, updated=1.0):
    return {"id": nid, "folder": folder, "content": _t(text),
            "pinned": pinned, "starred": starred,
            "created": 1.0, "updated": updated}


class TmpCase(unittest.TestCase):
    """每个用例一个临时目录，用完即删。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sujian-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def path(self, name="t.db"):
        return os.path.join(self.tmp, name)


class SerializeTest(unittest.TestCase):
    def test_plain_of_merges_text_todo_and_code(self):
        # 换行归为空格，待办框（⬜ + 间隙）顶掉一个空格 → 「标题」后是两个空格。
        # 待办行自带两格缩进（LI_INDENT，与列表标记同一条线），纯文本里留着 ——
        # 项目符号 / 三角 / 序号行本来也是这样。
        n = {"content": (_t("标题\n") + _cb(False) + _t("买菜\n")
                         + [["code", "python", "print('hi')"]])}
        self.assertEqual(plain_of(n), "标题 " + LI_INDENT + " 买菜 print('hi')")

    def test_cb_is_literal_char_form(self):
        """待办是**字面字符**形态：两格缩进 + ⬜ / ✅ + 两个不换行空格。

        两边必须一致：编辑器序列化出来的就是这个形态，seed 写的是同一个形态，
        不一致的话示例笔记重开后待办框就丢了。
        方框与间隙的标签也要分开：间隙只借字号（todo_gap），底色才不会铺过去。
        间隙用 TODO_GAP（= 项目符号 / 三角 / 序号尾部的同一串），四种列表
        「标记 → 正文」的距离才一样宽。
        """
        items = _cb(False)
        self.assertEqual([it[0] for it in items], ["t", "t", "t"])
        self.assertEqual([it[1] for it in items], [LI_INDENT, TODO_OFF, TODO_GAP])
        self.assertEqual(TODO_GAP, "\u00a0\u00a0")
        self.assertEqual(items[0][2], [])          # 缩进只占位，不挂标签
        self.assertIn("todo", items[1][2])
        self.assertEqual(items[2][2], [TODO_GAP_TAG])
        done = _cb(True)
        self.assertEqual(done[1][1], TODO_ON)
        self.assertIn("todo_done", done[1][2])

    def test_plain_of_empty(self):
        self.assertEqual(plain_of({"content": []}), "")
        self.assertEqual(plain_of({}), "")

    def test_t_keeps_tags_but_strips_them_on_newline(self):
        items = _t("a\nb", ["title"])
        self.assertEqual([i[1] for i in items], ["a", "\n", "b"])
        self.assertEqual(items[0][2], ["title"])
        self.assertEqual(items[1][2], [])      # 换行不带行级标签，避免多出空行


class SeedAndFixTest(unittest.TestCase):
    def test_seed_has_smart_folders_and_notes(self):
        d = seed()
        self.assertEqual([f["id"] for f in d["folders"]][:3],
                         ["f_all", "f_star", "f_lock"])
        self.assertTrue(all(f.get("smart") for f in d["folders"][:3]))
        self.assertEqual(len(d["notes"]), 4)


class SmartFolderTest(unittest.TestCase):
    """左栏「智能分类」：三个视图必须在、名字是现行文案、且排在真实文件夹前面。"""

    def test_adds_missing_lock_folder_to_old_library(self):
        d = {"folders": [{"id": "f_all", "name": "全部备忘录", "smart": True},
                         {"id": "f_work", "name": "工作"}]}
        self.assertTrue(store._ensure_smart_folders(d))
        self.assertEqual([f["id"] for f in d["folders"]],
                         ["f_all", "f_star", "f_lock", "f_work"])
        self.assertEqual([f["name"] for f in d["folders"][:3]],
                         ["全部笔记", "星标笔记", "加密笔记"])
        self.assertTrue(all(f.get("smart") for f in d["folders"][:3]))
        self.assertFalse(d["folders"][3].get("smart"))

    def test_is_idempotent(self):
        d = seed()
        self.assertFalse(store._ensure_smart_folders(d))
        before = [dict(f) for f in d["folders"]]
        self.assertFalse(store._ensure_smart_folders(d))
        self.assertEqual(d["folders"], before)

    def test_pulls_smart_folder_back_to_the_top(self):
        # 老库里星标被挪到了末尾（用户拖过、或导入过别的备份）→ 必须回到前面
        d = {"folders": [{"id": "f_all", "name": "全部笔记", "smart": True},
                         {"id": "f_work", "name": "工作"},
                         {"id": "f_star", "name": "星标笔记", "smart": True}]}
        self.assertTrue(store._ensure_smart_folders(d))
        self.assertEqual([f["id"] for f in d["folders"]],
                         ["f_all", "f_star", "f_lock", "f_work"])

    def test_smart_notes_filter(self):
        notes = [
            {"id": "a", "folder": "f_work", "content": _t("甲")},
            {"id": "b", "folder": "f_life", "content": _t("乙"), "starred": True},
            {"id": "c", "folder": "f_work", "content": [],
             "sec": {"salt": "s", "nonce": "n", "data": "d", "tag": "t"}},
        ]
        self.assertEqual([n["id"] for n in store.smart_notes("f_all", notes)],
                         ["a", "b", "c"])
        self.assertEqual([n["id"] for n in store.smart_notes("f_star", notes)], ["b"])
        # 加密笔记只看 sec 有没有密文：内容（明文）此时根本不在内存里
        self.assertEqual([n["id"] for n in store.smart_notes("f_lock", notes)], ["c"])
        self.assertEqual([n["id"] for n in store.smart_notes("f_work", notes)],
                         ["a", "c"])

    def test_smart_notes_of_all_does_not_alias_data(self):
        # f_all 返回副本：调用方（列表排序 / 过滤）不该意外改到库里那份列表
        notes = [{"id": "a", "folder": "f_work"}]
        self.assertIsNot(store.smart_notes("f_all", notes), notes)

    def test_fix_sample_note_renames_builtin_and_body(self):
        d = {"notes": [{"content": (_t("欢迎使用备忘录", ["title"])
                                    + _t("这是备忘录应用\n"))}]}
        self.assertTrue(_fix_sample_note(d))
        text = plain_of(d["notes"][0])
        self.assertIn("欢迎使用素笺", text)
        self.assertIn("这是笔记应用", text)
        self.assertNotIn("备忘录", text)

    def test_fix_sample_note_leaves_user_notes_alone(self):
        # 用户自己改过标题的笔记，哪怕正文里有「备忘录」也不动
        mine = {"content": _t("我的读书笔记", ["title"]) + _t("我的备忘录\n")}
        self.assertFalse(_fix_sample_note({"notes": [mine]}))
        self.assertIn("我的备忘录", plain_of(mine))
        self.assertIn("我的读书笔记", plain_of(mine))


class StoreTest(TmpCase):
    def test_first_load_seeds_initial_data(self):
        s = Store(self.path())
        d = s.load()
        self.assertEqual(len(d["folders"]), 5)      # 三个智能分类 + 工作 / 个人
        self.assertEqual(len(d["notes"]), 4)
        s.close()

    def test_first_load_migrates_legacy_json(self):
        legacy = {"folders": [{"id": "f_all", "name": "全部笔记", "smart": True}],
                  "notes": [note("n1", "老数据")]}
        json_path = os.path.join(self.tmp, "notes_data.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(legacy, f)
        old = store.DATA_FILE
        store.DATA_FILE = json_path          # load() 读的是模块级常量，临时指向它
        try:
            s = Store(self.path())
            d = s.load()
        finally:
            store.DATA_FILE = old
        self.assertEqual([n["id"] for n in d["notes"]], ["n1"])
        s.close()

    def test_roundtrip_keeps_content_and_flags(self):
        s = Store(self.path())
        d = {"folders": [{"id": "f_work", "name": "工作"}],
             "notes": [note("n1", "带格式", pinned=True, starred=True)],
             "settings": {"body_size": 15}}
        s.save(d, full=True)
        got = s.read_all()
        self.assertEqual(len(got["notes"]), 1)
        self.assertEqual(got["notes"][0]["content"], d["notes"][0]["content"])
        self.assertTrue(got["notes"][0]["pinned"])
        self.assertTrue(got["notes"][0]["starred"])
        self.assertEqual(got.get("settings"), {"body_size": 15})
        s.close()

    def test_incremental_write_skips_unchanged_notes(self):
        s = Store(self.path())
        d = {"folders": [], "notes": [note("a", "hello", updated=1.0)]}
        s.save(d, full=True)
        s.con.execute("UPDATE notes SET plain='SENTINEL' WHERE id='a'")
        s.con.commit()
        s.save(d)                             # 内容没变 → 跳过，不覆盖该行
        self.assertEqual(s.con.execute(
            "SELECT plain FROM notes WHERE id='a'").fetchone()[0], "SENTINEL")
        d["notes"][0]["updated"] = 2.0        # 真改动 → 写回
        s.save(d)
        self.assertEqual(s.con.execute(
            "SELECT plain FROM notes WHERE id='a'").fetchone()[0], "hello")
        s.close()

    def test_deletes_notes_missing_from_memory(self):
        s = Store(self.path())
        d = {"folders": [], "notes": [note("a", "1"), note("b", "2"), note("c", "3")]}
        s.save(d, full=True)
        d["notes"] = [d["notes"][0]]
        s.save(d)
        self.assertEqual(sorted(r[0] for r in s.con.execute("SELECT id FROM notes")), ["a"])
        s.close()

    def test_reset_replaces_everything(self):
        s = Store(self.path())
        s.save({"folders": [], "notes": [note("a", "旧")]}, full=True)
        s.reset({"folders": [{"id": "f_x", "name": "新"}],
                 "notes": [note("b", "新")], "settings": {}})
        d = s.read_all()
        self.assertEqual([f["id"] for f in d["folders"]], ["f_x"])
        self.assertEqual([n["id"] for n in d["notes"]], ["b"])
        self.assertIsNone(d.get("settings"))  # meta 也一起清掉了

    def test_sample_flag_survives_roundtrip(self):
        """示例笔记的 sample 标记必须落库 —— 它决定这篇笔记能不能删。"""
        s = Store(self.path())
        n = note("n1", "欢迎使用素笺")
        n["sample"] = True
        s.save({"folders": [], "notes": [n, note("n2", "我的笔记")]}, full=True)
        got = {x["id"]: x for x in s.read_all()["notes"]}
        self.assertTrue(got["n1"].get("sample"))
        self.assertFalse(got["n2"].get("sample"))
        s.close()

    def test_sample_flag_written_incrementally(self):
        """标记是加载时补上的（updated 没变），增量写也必须能写进去。"""
        s = Store(self.path())
        s.save({"folders": [], "notes": [note("a", "1", updated=1.0)]}, full=True)
        d = s.read_all()
        d["notes"][0]["sample"] = True        # 只改标记，不改 updated
        s.save(d)
        self.assertTrue(s.read_all()["notes"][0].get("sample"))
        s.close()

    def test_mark_sample_note_only_matches_builtin_title(self):
        """老库补标记：标题整串等于内置示例标题才认，改过标题的不动。"""
        d = seed()
        for n in d["notes"]:
            n.pop("sample", None)
        self.assertTrue(_mark_sample_note(d))
        self.assertTrue(d["notes"][0].get("sample"))
        self.assertFalse(any(n.get("sample") for n in d["notes"][1:]))

        d2 = seed()
        for n in d2["notes"]:
            n.pop("sample", None)
        d2["notes"][0]["content"][0][1] = "改"     # 用户改了标题
        self.assertFalse(_mark_sample_note(d2))

    # ---- 内置说明笔记的「快捷键」小节 ----

    def _plain(self, n):
        return "".join(it[1] for it in n["content"] if it[0] == "t")

    def _h2s(self, n):
        """所有 H2 标题文字（按行取，一行一个）。

        说明笔记尾部现在有两节：markdown 快捷输入 + 快捷键，所以不能再把所有
        带 h2 的字项拼成一个字符串——那会拼成「markdown 快捷输入快捷键」。
        """
        lines, cur = [], []
        for it in n["content"]:
            if it[0] != "t":
                continue
            tags = it[2] if len(it) > 2 else []
            for ch in it[1]:
                if ch == "\n":
                    lines.append(cur)
                    cur = []
                else:
                    cur.append((ch, tags))
        if cur:
            lines.append(cur)
        return ["".join(c for c, _ in ln) for ln in lines
                if ln and "h2" in ln[0][1]]

    def test_seed_has_shortcut_section(self):
        """新库的说明笔记直接带着快捷键表（不用等升级逻辑补）。"""
        n = [x for x in seed()["notes"] if x.get("sample")][0]
        self.assertIn("快捷键", self._h2s(n))
        self.assertIn("markdown 快捷输入", self._h2s(n))
        txt = self._plain(n)
        for key in ("Ctrl+N", "Ctrl+S", "Ctrl+1", "Ctrl+Shift+K", "Ctrl+Y",
                    "Ctrl+E", "Ctrl+K", "Ctrl+D", "Ctrl+Shift+Z"):
            self.assertIn(key, txt)
        # Ctrl+F（聚焦搜索框）已按需求撤掉：搜索改用鼠标点搜索框，表里不能再出现
        self.assertNotIn("Ctrl+F", txt)
        # 引用块的按钮和快捷键（Ctrl+Shift+Q）已按需求撤掉，只保留 `> ` 前缀输入
        self.assertNotIn("Ctrl+Shift+Q", txt)
        self.assertEqual(txt.count("Ctrl+Shift+K"), 1)

    def _legacy_sample(self):
        """构造一份「升级前」的说明笔记：只有标题和一句正文，没有快捷键表。"""
        d = seed()
        n = [x for x in d["notes"] if x.get("sample")][0]
        n["content"] = (_t("欢迎使用素笺", ["title"]) + [["t", "\n", []]]
                        + _t("这是一款复刻 macOS 笔记的桌面程序。") + [["t", "\n", []]])
        return d, n

    def test_upgrade_appends_once_and_is_idempotent(self):
        """老库（没有这一节）补一次就够，再跑一次不能再追加。

        内容是按「逐字符一项」存的，判断有没有这一节必须把字项拼起来——
        早期版本按单项文本比对，结果每次启动都追加一份。
        """
        d, n = self._legacy_sample()
        self.assertNotIn("Ctrl+Shift+K", self._plain(n))
        self.assertTrue(_upgrade_sample_note(d))
        self.assertIn("Ctrl+Shift+K", self._plain(n))
        self.assertIn("快捷键", self._h2s(n))
        self.assertFalse(_upgrade_sample_note(d))          # 幂等
        self.assertEqual(self._plain(n).count("Ctrl+Shift+K"), 1)

    def test_upgrade_refreshes_existing_section(self):
        """老库已经有快捷键节、但缺新键时，整节要被刷新（只追加永远补不上）。

        构造的是「上一版」的说明笔记：有 markdown 节和快捷键节，但表里没有
        Ctrl+E / Ctrl+Shift+Q —— 这正是从旧版升级上来的真实状态。
        """
        d = seed()
        n = [x for x in d["notes"] if x.get("sample")][0]
        old = (_t("欢迎使用素笺", ["title"]) + [["t", "\n", []]]
               + _t("这是一款复刻 macOS 笔记的桌面程序。") + [["t", "\n", []]]
               + _t("快捷键", ["h2"]) + [["t", "\n", []]]
               + _t("Ctrl+N", ["bold"]) + _t("　新建笔记") + [["t", "\n", []]]
               + _t("Ctrl+Shift+K", ["bold"]) + _t("　插入代码块") + [["t", "\n", []]])
        n["content"] = old
        self.assertTrue(_upgrade_sample_note(d))
        txt = self._plain(n)
        self.assertIn("Ctrl+E", txt)
        self.assertEqual(txt.count("Ctrl+Shift+K"), 1)     # 没有重复一份
        self.assertIn("这是一款复刻", txt)                   # 前面的正文没动
        self.assertFalse(_upgrade_sample_note(d))          # 再跑一次仍然幂等

    def test_upgrade_drops_legacy_one_liner(self):
        """旧版那行速记（"快捷键：Ctrl+N 新建、…"）要摘掉，否则和新表重复。"""
        d, n = self._legacy_sample()
        legacy = "快捷键：Ctrl+N 新建、Ctrl+F 搜索。"
        n["content"] = ([["t", c, []] for c in legacy] + [["t", "\n", []]]
                        + n["content"])
        self.assertTrue(_upgrade_sample_note(d))
        txt = self._plain(n)
        self.assertNotIn("快捷键：Ctrl+N", txt)
        self.assertIn("快捷键", self._h2s(n))
        self.assertIn("这是一款复刻", txt)      # 原有正文没被清掉

    def test_upgrade_leaves_other_notes_alone(self):
        """只动带 sample 标记的那篇；用户自己写的笔记一个字都不碰。"""
        d = seed()
        other = [x for x in d["notes"] if not x.get("sample")][0]
        before = self._plain(other)
        _upgrade_sample_note(d)
        self.assertEqual(self._plain(other), before)


class SearchTest(TmpCase):
    def setUp(self):
        TmpCase.setUp(self)
        self.s = Store(self.path())
        self.s.save({
            "folders": [],
            "notes": [
                note("pinned", "置顶的苹果 note", updated=1.0, pinned=True),
                note("star", "星标的香蕉 note", folder="f_life", starred=True, updated=9.0),
                note("percent", "折扣 50% 起", updated=5.0),
                note("under", "变量 a_1 已定义", updated=6.0),
                note("plain", "普通文本 SQLite note", updated=7.0),
            ],
        }, full=True)

    def tearDown(self):
        self.s.close()
        TmpCase.tearDown(self)

    def test_hits_and_folder_filter(self):
        self.assertEqual(self.s.search_ids("f_all", "苹果"), ["pinned"])
        self.assertEqual(self.s.search_ids("f_life", "香蕉"), ["star"])
        self.assertEqual(self.s.search_ids("f_life", "苹果"), [])

    def test_smart_star_folder(self):
        self.assertEqual(self.s.search_ids("f_star", "香蕉"), ["star"])
        self.assertEqual(self.s.search_ids("f_star", "苹果"), [])

    def test_smart_lock_folder_never_matches_keywords(self):
        """「加密笔记」里搜不到东西是**正确**的：加密笔记落库时 plain 是空的。

        这里同时锁住一件事：f_lock 不能掉进「按 folder 字段筛」的老分支
        （那样会拿 'f_lock' 去比 folder 列，同样是空结果，但语义全错）。
        """
        self.assertEqual(self.s.search_ids("f_lock", "苹果"), [])
        self.assertEqual(self.s.search_ids("f_lock", "note"), [])

    def test_pinned_first(self):
        ids = self.s.search_ids("f_all", "note")       # 命中多条，看排序
        self.assertEqual(ids[:2], ["pinned", "star"])  # 置顶优先，其次星标

    def test_case_insensitive_for_ascii(self):
        self.assertEqual(self.s.search_ids("f_all", "sqlite"), ["plain"])

    def test_percent_is_literal_not_wildcard(self):
        # 不转义的话 % 是「任意长度通配」，会把所有笔记都命中
        self.assertEqual(self.s.search_ids("f_all", "50%"), ["percent"])
        self.assertEqual(self.s.search_ids("f_all", "%"), ["percent"])

    def test_underscore_is_literal_not_wildcard(self):
        self.assertEqual(self.s.search_ids("f_all", "a_1"), ["under"])
        self.assertEqual(self.s.search_ids("f_all", "_"), ["under"])

    def test_backslash_is_escaped_too(self):
        self.assertEqual(self.s.search_ids("f_all", "\\"), [])
        self.assertEqual(like_escape("a\\%b"), "a\\\\\\%b")


class FtsTest(TmpCase):
    """FTS5（可用时）与 LIKE 必须给出同样的结果，且索引要跟着写操作走。"""

    def setUp(self):
        TmpCase.setUp(self)
        self.s = Store(self.path())
        self.s.save({"folders": [], "notes": [
            note("a", "数据库设计与优化"),
            note("b", "数据备份策略"),
        ]}, full=True)

    def tearDown(self):
        self.s.close()
        TmpCase.tearDown(self)

    def test_long_query_hits_and_agrees_with_like(self):
        self.assertEqual(self.s.search_ids("f_all", "数据库"), ["a"])
        self.s._fts = False                       # 强制走 LIKE，结果应一致
        self.assertEqual(self.s.search_ids("f_all", "数据库"), ["a"])

    def test_short_query_falls_back_to_like(self):
        self.assertEqual(self.s.search_ids("f_all", "备份"), ["b"])
        self.s._fts = False
        self.assertEqual(self.s.search_ids("f_all", "备份"), ["b"])

    def test_index_follows_updates_and_deletes(self):
        d = {"folders": [], "notes": [note("a", "数据库设计与优化")]}
        self.s.save(d, full=True)
        self.assertEqual(self.s.search_ids("f_all", "设计与"), ["a"])
        d["notes"] = [note("a", "改成了别的说法", updated=2.0)]
        self.s.save(d)
        self.assertEqual(self.s.search_ids("f_all", "数据库"), [])
        self.assertEqual(self.s.search_ids("f_all", "别的说法"), ["a"])
        d["notes"] = []
        self.s.save(d)
        self.assertEqual(self.s.search_ids("f_all", "别的说法"), [])

    def test_fts_state(self):
        import sqlite3
        print('\n    [sqlite %s | fts5=%s]' % (sqlite3.sqlite_version, self.s._fts))


class ImportTest(unittest.TestCase):
    """导入语义的完整形状（含 current_folder 这类落库字段）。

    `decode` + `overwrite`/`merge` 这两步原来由 store 里两个同名薄壳代劳，
    薄壳已于清理时删除 —— 测试直接调 backup，免得又出现「测试专用入口」。
    """

    @staticmethod
    def _overwrite(d):
        data, _warn = backup.decode(d)
        return backup.overwrite(data)

    @staticmethod
    def _merge(d, cur):
        data, _warn = backup.decode(d)
        return backup.merge(data, cur)

    def test_normalize_import_fills_smart_folders_and_rescues_orphans(self):
        d = {"folders": [{"id": "f_mine", "name": "我的"}],
             "notes": [{"id": "n1", "folder": "f_gone", "content": _t("孤儿")},
                       {"id": "n2", "content": "坏数据"}]}
        out = self._overwrite(d)
        self.assertEqual([f["id"] for f in out["folders"]][:4],
                         ["f_all", "f_star", "f_lock", "f_mine"])
        self.assertEqual([n["id"] for n in out["notes"]], ["n1"])
        self.assertEqual(out["notes"][0]["folder"], "f_work")

    def test_merge_import_keeps_existing_and_renames_conflicts(self):
        cur = {"folders": [{"id": "f_work", "name": "工作"}],
               "notes": [note("dup", "现有")], "current_folder": "f_work"}
        d = {"folders": [{"id": "f_work", "name": "工作"},
                         {"id": "f_new", "name": "新文件夹"}],
             "notes": [note("dup", "备份里的同名 id"), note("other", "备份里新的")]}
        out = self._merge(d, cur)
        # 合并后的文件夹列表同样以三个智能分类打头（它们是视图，不在备份里也补）
        self.assertEqual([f["id"] for f in out["folders"]],
                         ["f_all", "f_star", "f_lock", "f_work", "f_new"])
        ids = [n["id"] for n in out["notes"]]
        self.assertEqual(ids[0], "dup")
        self.assertNotIn("dup", ids[1:])                # 冲突的另起新 id
        self.assertIn("other", ids)
        self.assertEqual(out["current_folder"], "f_work")

    def test_merge_import_drops_smart_flag(self):
        out = self._merge({"folders": [{"id": "f_x", "name": "X", "smart": True}],
                           "notes": []},
                          {"folders": [], "notes": []})
        self.assertNotIn("smart", out["folders"][-1])


class SaveFailureTest(unittest.TestCase):
    """保存失败不能静默：要么抛给调用方，要么交给钩子，且必须留下兜底快照。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._base, self._hook = store.BASE, store._SAVE_ERROR_HOOK
        store.BASE = self.tmp

    def tearDown(self):
        store.BASE, store._SAVE_ERROR_HOOK = self._base, self._hook
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _broken(self):
        class Boom(object):
            def save(self, d):
                raise RuntimeError("模拟写入失败")
        real, store.store = store.store, lambda: Boom()
        self.addCleanup(lambda: setattr(store, "store", real))

    def test_save_data_raises_when_no_hook(self):
        """没注册钩子时保持老行为：异常照常抛，不吞。"""
        self._broken()
        store.set_save_error_hook(None)
        with self.assertRaises(RuntimeError):
            store.save_data({"folders": [], "notes": []})

    def test_save_data_returns_false_and_calls_hook(self):
        self._broken()
        seen = []
        store.set_save_error_hook(lambda e, d: seen.append(e))
        self.assertFalse(store.save_data({"folders": [], "notes": []}))
        self.assertEqual(len(seen), 1)

    def test_hook_that_raises_still_surfaces(self):
        """钩子自己也崩了，就不能假装成功——异常要继续往上抛。"""
        self._broken()

        def bad_hook(e, d):
            raise ValueError("钩子炸了")
        store.set_save_error_hook(bad_hook)
        with self.assertRaises(ValueError):
            store.save_data({"folders": [], "notes": []})

    def test_dump_pending_is_importable(self):
        """兜底快照必须是「导出备份」同格式，事后能用导入捞回来。"""
        d = {"folders": [{"id": "f_all", "name": "全部笔记", "smart": True}],
             "notes": [note("n1", "还没存上的内容")], "settings": {"body_size": 14}}
        path = store.dump_pending(d, reason="退出时保存失败")
        self.assertTrue(path and os.path.exists(path))
        self.assertEqual(store.pending_files(), [path])
        with open(path, "r", encoding="utf-8") as f:
            snap = json.load(f)
        back, _warn = backup.decode(snap)
        back = backup.overwrite(back)
        self.assertEqual([n["id"] for n in back["notes"]], ["n1"])

    def test_close_clears_singleton(self):
        """close() 之后 store() 必须重新连库，不能返回已关闭的连接。"""
        p = os.path.join(self.tmp, "notes_data.db")
        store._STORE = store.Store(p)
        store._STORE.close()
        self.assertIsNone(store._STORE)


class FolderTreeTest(unittest.TestCase):
    """多级文件夹：层级判据、子树遍历、脏数据的自我修复、以及落库往返。"""

    def tree(self):
        return [
            {"id": "f_work", "name": "工作"},
            {"id": "f_proj", "name": "项目", "parent": "f_work"},
            {"id": "f_deep", "name": "2026", "parent": "f_proj"},
            {"id": "f_life", "name": "个人"},
        ]

    def test_children_keep_array_order(self):
        folders = self.tree()
        self.assertEqual([f["id"] for f in folder_children("f_work", folders)],
                         ["f_proj"])
        self.assertEqual([f["id"] for f in folder_children(None, folders)],
                         ["f_work", "f_life"])

    def test_subtree_includes_self_and_descendants(self):
        ids = set(subtree_ids("f_work", self.tree()))
        self.assertEqual(ids, {"f_work", "f_proj", "f_deep"})

    def test_subtree_stops_on_cycle(self):
        """成环时每个 id 只算一次，且绝不无限循环（subtree_ids 靠 visited 兜住）。"""
        folders = [{"id": "a", "name": "A", "parent": "b"},
                   {"id": "b", "name": "B", "parent": "a"}]
        self.assertEqual(set(subtree_ids("a", folders)), {"a", "b"})

    def test_depth_counts_levels(self):
        folders = self.tree()
        self.assertEqual(folder_depth("f_work", folders), 1)
        self.assertEqual(folder_depth("f_proj", folders), 2)
        self.assertEqual(folder_depth("f_deep", folders), 3)

    def test_ancestors_run_root_to_parent(self):
        """全程自上而下：展开祖先必须从根开始，否则下一级还没渲染出来。"""
        folders = self.tree()
        self.assertEqual([f["id"] for f in folder_ancestors("f_deep", folders)],
                         ["f_work", "f_proj"])
        self.assertEqual([f["id"] for f in folder_ancestors("f_proj", folders)],
                         ["f_work"])
        self.assertEqual(folder_ancestors("f_work", folders), [])

    def test_ancestors_stop_on_dangling_and_cycle(self):
        """父引用悬空 / 成环都就地停下，绝不返回自己、也绝不死循环。"""
        dangling = [{"id": "x", "name": "孤儿", "parent": "没有这个"}]
        self.assertEqual([f["id"] for f in folder_ancestors("x", dangling)], [])
        ring = [{"id": "a", "name": "A", "parent": "b"},
                {"id": "b", "name": "B", "parent": "a"}]
        got = [f["id"] for f in folder_ancestors("a", ring)]
        self.assertEqual(got, ["b"])            # 不含自己，且只走一步就停
        self.assertLessEqual(len(folder_ancestors("b", ring)), 1)
        self.assertEqual(folder_ancestors("不存在", ring), [])

    def test_normalize_drops_dangling_parent(self):
        folders = [{"id": "x", "name": "孤儿", "parent": "不存在"}]
        self.assertTrue(normalize_folder_parents(folders))
        self.assertIsNone(folders[0].get("parent"))

    def test_normalize_drops_self_and_cycle(self):
        mine = [{"id": "a", "name": "A"}]
        mine[0]["parent"] = "a"
        self.assertTrue(normalize_folder_parents(mine))
        self.assertIsNone(mine[0].get("parent"))
        ring = [{"id": "a", "name": "A", "parent": "b"},
                {"id": "b", "name": "B", "parent": "a"}]
        self.assertTrue(normalize_folder_parents(ring))
        # 环必须被拆掉：整理完之后顺着父链往上走，一定能走到根（且步数有界）
        by = {f["id"]: f for f in ring}
        for f in ring:
            steps, cur, seen = 0, f, set()
            while cur.get("parent") and steps < len(ring) + 2:
                self.assertNotIn(cur["id"], seen)     # 再出现同一个点 = 环还在
                seen.add(cur["id"])
                cur = by[cur["parent"]]
                steps += 1
            self.assertLess(steps, len(ring) + 2)

    def test_normalize_refuses_smart_parent_and_too_deep(self):
        folders = [{"id": "f_all", "name": "全部笔记", "smart": True},
                   {"id": "x", "name": "X", "parent": "f_all"}]
        self.assertTrue(normalize_folder_parents(folders))
        self.assertIsNone(folders[1].get("parent"))
        chain = [{"id": "f%d" % i, "name": str(i)}
                 for i in range(MAX_FOLDER_DEPTH + 2)]
        for i, f in enumerate(chain):
            if i:
                f["parent"] = chain[i - 1]["id"]
        self.assertTrue(normalize_folder_parents(chain))
        self.assertTrue(max(folder_depth(f["id"], chain) for f in chain)
                        <= MAX_FOLDER_DEPTH)

    def test_normalize_is_idempotent(self):
        folders = self.tree()
        self.assertFalse(normalize_folder_parents(folders))
        self.assertEqual(folders, self.tree())

    # ---------- 整列收 / 放（「文件夹」标题右侧那两枚） ----------
    def test_collapse_all_marks_only_parents(self):
        """只有「有子文件夹」的才写标记：叶子写了标记，以后给它建子文件夹会凭空不显示。"""
        folders = self.tree()
        folders.append({"id": "f_all", "name": "全部笔记", "smart": True})
        self.assertTrue(set_all_collapsed(folders, True))
        marked = {f["id"] for f in folders if f.get("collapsed")}
        self.assertEqual(marked, {"f_work", "f_proj"})
        self.assertNotIn("collapsed", folders[-1])      # 智能分类不参与

    def test_collapse_all_is_idempotent(self):
        """已经在全收起状态再点，不产生「变化」——界面据此跳过整列重建（不白闪）。"""
        folders = self.tree()
        self.assertTrue(set_all_collapsed(folders, True))
        self.assertFalse(set_all_collapsed(folders, True))

    def test_expand_all_clears_even_leaf_marks(self):
        """展开一律清干净，包括老数据里留在叶子上的历史标记。"""
        folders = self.tree()
        folders[0]["collapsed"] = True
        folders[3]["collapsed"] = True         # 叶子（个人）上没人该有的标记
        self.assertTrue(set_all_collapsed(folders, False))
        self.assertFalse(any(f.get("collapsed") for f in folders))
        self.assertFalse(set_all_collapsed(folders, False))

    def test_collapse_all_tolerates_junk(self):
        """导入来的脏数据里可能混进非 dict，别让整列收 / 放顺手抛异常。"""
        folders = self.tree() + [None, "x", {}]
        self.assertTrue(set_all_collapsed(folders, True))
        self.assertTrue(set_all_collapsed(folders, False))

    def test_parent_and_collapsed_survive_roundtrip(self):
        p = os.path.join(tempfile.mkdtemp(prefix="sujian-tree-"), "t.db")
        try:
            s = Store(p)
            data = {"folders": self.tree(), "notes": []}
            data["folders"][1]["collapsed"] = True
            s.save(data, full=True)
            got = s.read_all()
            by = {f["id"]: f for f in got["folders"]}
            self.assertEqual(by["f_proj"]["parent"], "f_work")
            self.assertEqual(by["f_deep"]["parent"], "f_proj")
            self.assertTrue(by["f_proj"]["collapsed"])
            self.assertIsNone(by["f_work"].get("parent"))     # 根层不写脏字段
            self.assertFalse(by["f_life"].get("collapsed"))
            s.close()
        finally:
            shutil.rmtree(os.path.dirname(p), ignore_errors=True)

    def test_import_keeps_tree_but_drops_broken_links(self):
        snap = {"folders": [{"id": "f_work", "name": "工作"},
                            {"id": "f_p", "name": "子", "parent": "f_work"},
                            {"id": "f_lost", "name": "悬空", "parent": "没了"}],
                "notes": []}
        out, _warn = backup.decode(snap)
        out = backup.overwrite(out)
        by = {f["id"]: f for f in out["folders"]}
        self.assertEqual(by["f_p"]["parent"], "f_work")
        self.assertIsNone(by["f_lost"].get("parent"))

    def test_search_covers_whole_subtree(self):
        p = os.path.join(tempfile.mkdtemp(prefix="sujian-sub-"), "t.db")
        try:
            s = Store(p)
            data = {"folders": self.tree(),
                    "notes": [note("n1", "藏在深层", folder="f_deep"),
                              note("n2", "在根层", folder="f_life"),
                              note("n3", "也在子树", folder="f_proj")]}
            s.save(data, full=True)
            # 传整棵子树 = 父视图下能看到子孙里的笔记
            ids = s.search_ids("f_work", "藏在", ["f_work", "f_proj", "f_deep"])
            self.assertEqual(ids, ["n1"])
            deep = subtree_ids("f_work", self.tree())
            self.assertEqual(s.search_ids("f_work", "", deep),
                             [i for i in ("n1", "n3")])   # 排序：按 updated 倒序保持原顺序
            s.close()
        finally:
            shutil.rmtree(os.path.dirname(p), ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
