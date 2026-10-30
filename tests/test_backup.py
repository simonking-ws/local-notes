# -*- coding: utf-8 -*-
"""备份格式测试（标准库 unittest，零第三方依赖）。

这里盯的是「备份能不能原样还回来」—— 出问题不会报错，只会悄悄少格式、少笔记、
多一份副本，所以每条都要钉死：

  * 正文 runs ⇄ text+spans+blocks 逐字符等价（含重叠标签、代码块、分割线、闹钟、
    老式待办窗口、以及不认识的未来元素）；
  * 编码 → 落盘 → 读回 → 解码，内容与外观一致；
  * 校验和能发现被改过的文件；
  * v1 老备份（含 pending 兜底）照样能导，且 state（tabs / 两栏宽度）不被采纳；
  * 合并是幂等的：同一个备份导两次不翻倍；
  * 合并时文件夹 id 冲突要建映射，笔记不能落错文件夹；
  * 不认识的字段要原样带回。

运行：
    python tests\\test_backup.py
"""
import json
import os
import sys
import shutil
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import backup                                                    # noqa: E402
from store import _t, SMART_FOLDERS                               # noqa: E402


def runs_of(*parts):
    """拼一段 runs（`_t` 产生逐字符 run，这里只做串接，便于读）。"""
    out = []
    for p in parts:
        out.extend(p)
    return out


def note(nid, text, folder="f_work", **kw):
    n = {"id": nid, "folder": folder, "content": _t(text),
         "created": 1000.0, "updated": 2000.0}
    n.update(kw)
    return n


def same_content(a, b):
    """两段 runs 是不是同一份正文。

    备份不记录 run 的切分方式（`[["t","标题",[]]]` 与逐字符三条等价），
    所以比较前统一压成 (text, spans, blocks) 再比 —— 这也是 rev_of 的口径。
    """
    return backup.content_to_spans(a) == backup.content_to_spans(b)


class SpanRoundTripTest(unittest.TestCase):
    """正文压缩必须无损：任何一处对不上，导出再导入就掉格式。"""

    def assertRoundTrip(self, runs):
        """往返一致 —— 判据是「文本 + 区间 + 块」等价，不是 runs 逐条相等。

        备份里不记录 run 的切分方式（一段文字切成几条都合法），还原时按 serialize()
        的习惯把「相邻且标签相同」的字符合成一条，所以逐条比较会误报。真正要保证的
        是：还原出来的东西再压一次，与第一次的 (text, spans, blocks) 完全相同。
        """
        text, spans, blocks = backup.content_to_spans(runs)
        back = backup.spans_to_runs(text, spans, blocks)
        self.assertEqual(backup.content_to_spans(back), (text, spans, blocks))
        # 合并不彻底（还原成一条字一个 run）会让库内正文 JSON 胖十几倍，也钉一下
        for a, b in zip(back, back[1:]):
            if a[0] == "t" and b[0] == "t":
                self.assertNotEqual(a[2], b[2], "相邻同标签的 run 没合并：%r / %r" % (a, b))
        return text, spans, blocks

    def test_plain_text_and_newlines(self):
        self.assertRoundTrip(runs_of(_t("标题\n正文第二行\n")))

    def test_overlapping_and_adjacent_tags(self):
        # 相邻同标签要合成一条区间；重叠（body + bold）要各自成条且互不影响
        runs = [["t", "素", ["title"]], ["t", "笺", ["title"]],
                ["t", "\n", []],
                ["t", "粗", ["body", "bold"]], ["t", "体", ["body", "bold"]],
                ["t", "正", ["body"]], ["t", "\n", []]]
        text, spans, _blocks = self.assertRoundTrip(runs)
        self.assertEqual(text, "素笺\n粗体正\n")
        self.assertIn(["title", 0, 2], spans)
        self.assertIn(["bold", 3, 5], spans)
        self.assertIn(["body", 3, 6], spans)

    def test_tag_broken_then_resumed_makes_two_spans(self):
        runs = [["t", "a", ["bold"]], ["t", "b", []], ["t", "c", ["bold"]]]
        _text, spans, _blocks = self.assertRoundTrip(runs)
        self.assertEqual(sorted(sp for sp in spans if sp[0] == "bold"),
                         [["bold", 0, 1], ["bold", 2, 3]])

    def test_structural_elements_keep_their_position(self):
        runs = [["t", "前", []], ["code", "python", "print(1)"],
                ["t", "后", []], ["hr"],
                ["alarm", {"id": "a1", "text": "喝水", "at": 0}],
                ["t", "\n", []]]
        _text, _spans, blocks = self.assertRoundTrip(runs)
        self.assertEqual([b["i"] for b in blocks], [1, 3, 4])
        self.assertEqual([b["kind"] for b in blocks], ["code", "hr", "alarm"])
        self.assertEqual(blocks[0]["src"], "print(1)")

    def test_legacy_todo_window_survives(self):
        # 老数据里的待办是嵌入控件；虽然现在只读兼容，也不能在这一层丢掉
        runs = [["w", 1, ["body"]]] + _t("买菜\n")
        self.assertRoundTrip(runs)

    def test_unknown_element_is_carried_as_raw(self):
        # 未来版本新加的内嵌元素：老版本导出/导入都不该把它弄没了
        runs = [["t", "a", []], ["future_thing", 42, {"x": 1}], ["t", "b", []]]
        _text, _spans, blocks = self.assertRoundTrip(runs)
        self.assertEqual(blocks[0]["kind"], "raw")

    def test_empty_content(self):
        self.assertEqual(backup.content_to_spans([]), ("", [], []))
        self.assertEqual(backup.spans_to_runs("", [], []), [])

    def test_multi_char_runs_stay_multi_char(self):
        # 库里 serialize() 写的是「一条多字」（["t","标题",["title"]]）。还原时若一条字
        # 一条 run，正文 JSON 会胖十几倍，而且和导出前的笔记算不出同一个指纹。
        # 注意入参本身也得是「规范化」的样子：相邻且标签相同的 run 属于同一条，
        # 还原时必然合成一条（serialize() 也是这么攒的）。
        runs = [["t", "标题", ["title"]], ["t", "\n正文　　", []], ["t", "⬜", ["todo"]]]
        text, spans, blocks = backup.content_to_spans(runs)
        back = backup.spans_to_runs(text, spans, blocks)
        self.assertEqual(back, runs)

    def test_fingerprint_ignores_runs_segmentation(self):
        # 同一段正文按不同方式切 run，指纹必须相同 —— 否则「内容一字不差」的笔记
        # 在合并时会被当成新的，导两次就多一份。
        loose = [["t", "标题", ["title"]], ["t", "正文", []]]
        tight = [["t", "标", ["title"]], ["t", "题", ["title"]], ["t", "正", []],
                 ["t", "文", []]]
        self.assertEqual(backup.rev_of({"content": loose}),
                         backup.rev_of({"content": tight}))
        # 反过来：内容真的不同就必须不同
        self.assertNotEqual(backup.rev_of({"content": loose}),
                            backup.rev_of({"content": tight[:3] + [["t", "文", ["bold"]]]}))

    def test_encrypted_note_has_empty_text(self):
        text, spans, blocks = backup.content_to_spans([])
        self.assertEqual((text, spans, blocks), ("", [], []))


class EncodeDecodeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sujian-backup-")
        self.data = {
            "folders": [{"id": "f_all", "name": "全部笔记", "smart": True},
                        {"id": "f_work", "name": "工作"},
                        {"id": "f_sub", "name": "子", "parent": "f_work", "collapsed": True}],
            "notes": [note("n1", "标题\n正文", pinned=True, starred=True),
                      note("n2", "第二篇", folder="f_sub")],
            "settings": {"body_size": 15, "font_family": "", "toolbar": {"hidden": ["bold"]},
                         "tabs": ["n1"], "side_w": 260, "list_w": 320,
                         "alarm_fired": {"n1|a1": 123}},
            "styles": {"c_ff0000": {"fg": "#ff0000"}},
        }

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def path(self, name):
        return os.path.join(self.tmp, name)

    def test_roundtrip_json(self):
        p = self.path("b.json")
        backup.dump(p, backup.encode(self.data))
        d, warn = backup.decode(backup.load(p))
        self.assertEqual(warn, [])                       # 校验和对得上
        self.assertEqual([n["id"] for n in d["notes"]], ["n1", "n2"])
        self.assertTrue(same_content(d["notes"][0]["content"],
                                     self.data["notes"][0]["content"]))
        self.assertTrue(d["notes"][0]["pinned"])
        self.assertEqual(d["folders"][2]["parent"], "f_work")
        self.assertEqual(d["prefs"]["body_size"], 15)
        self.assertEqual(d["prefs"]["toolbar"], {"hidden": ["bold"]})
        self.assertEqual(d["styles"], {"c_ff0000": {"fg": "#ff0000"}})

    def test_dump_is_byte_stable(self):
        """同一份数据导出两次，字节必须完全一致。

        gzip 头里默认会写 `fileobj.name`（也就是完整路径）和当前时间，那样每导
        一次、换个目录就变成一个「新版本」，云盘/版本库会平白多出一堆改动。
        """
        a, b = self.path("a.json.gz"), self.path("sub-b.json.gz")
        payload = backup.encode(self.data, exported_at="2026-10-30T10:00:00+08:00")
        backup.dump(a, payload)
        backup.dump(b, payload)
        with open(a, "rb") as f1, open(b, "rb") as f2:
            self.assertEqual(f1.read(), f2.read())

    def test_state_is_not_exported(self):
        payload = backup.encode(self.data)
        blob = json.dumps(payload, ensure_ascii=False)
        for key in ("tabs", "side_w", "list_w", "alarm_fired"):
            self.assertNotIn('"%s"' % key, blob)
        d, _warn = backup.decode(payload)
        for key in backup.STATE_KEYS:
            self.assertNotIn(key, d["prefs"])

    def test_roundtrip_gzip_and_size(self):
        plain = self.path("b.json")
        packed = self.path("b.json.gz")
        payload = backup.encode(self.data)
        n_plain = backup.dump(plain, payload)
        n_packed = backup.dump(packed, payload)
        self.assertLess(n_packed, n_plain)               # 压缩确实更小
        self.assertTrue(same_content(
            backup.decode(backup.load(packed))[0]["notes"][1]["content"],
            self.data["notes"][1]["content"]))

    def test_gzip_is_detected_by_magic_not_suffix(self):
        # 用户把 .gz 改名成 .json（或反过来）也得能读
        p = self.path("weird.json")
        backup.dump(p.replace(".json", ".gz"), backup.encode(self.data))
        os.rename(p.replace(".json", ".gz"), p)
        self.assertEqual(len(backup.decode(backup.load(p))[0]["notes"]), 2)

    def test_foreign_format_is_refused(self):
        # 别的程序的 JSON 恰好也有 folders / notes：不许当自家备份读进来
        payload = backup.encode(self.data)
        payload["format"] = "other.app.backup"
        with self.assertRaises(backup.BackupError):
            backup.decode(payload)
        # 老备份（v1）没有 format 字段 —— 那是自家格式，照旧得能读
        legacy = {"_version": 1, "folders": [], "notes": []}
        backup.decode(legacy)

    def test_checksum_catches_tampering(self):
        p = self.path("b.json")
        backup.dump(p, backup.encode(self.data))
        raw = backup.load(p)
        raw["notes"][0]["text"] = "被改过的正文"
        d, warn = backup.decode(raw)
        self.assertTrue(warn)
        self.assertIn("校验和", warn[0])
        self.assertTrue(same_content(d["notes"][0]["content"],
                                     [["t", "被改过的正文", []]]))

    def test_schema_too_new_is_refused(self):
        payload = backup.encode(self.data)
        payload["schema"] = backup.SCHEMA + 1
        with self.assertRaises(backup.BackupError):
            backup.decode(payload)

    def test_not_a_backup_is_refused(self):
        for bad in ({}, {"folders": [], "notes": []} and [], "x", {"notes": []}):
            with self.assertRaises(backup.BackupError):
                backup.decode(bad)

    def test_unknown_note_field_survives(self):
        self.data["notes"][1]["color"] = "#00ff00"
        payload = backup.encode(self.data)
        d, _warn = backup.decode(payload)
        out = backup.overwrite(d)
        by = dict((n["id"], n) for n in out["notes"])
        self.assertEqual(by["n2"].get("color"), "#00ff00")

    def test_v2_record_keys_do_not_leak_into_data(self):
        d, _warn = backup.decode(backup.encode(self.data))
        out = backup.overwrite(d)
        for n in out["notes"]:
            for k in ("text", "spans", "blocks", "rev"):
                self.assertNotIn(k, n)


class LegacyV1Test(unittest.TestCase):
    """v1 快照（老备份 / data/pending_*.json 兜底）必须还能导。"""

    def legacy(self):
        return {
            "_app": "notes-app", "_version": 1,
            "_exported_at": "2026-10-29 22:36:21",
            "folders": [{"id": "f_work", "name": "工作"}],
            "notes": [{"id": "old1", "folder": "f_work",
                       "content": [["t", "旧", ["title"]], ["t", "\n", []]],
                       "pinned": False, "starred": True,
                       "created": 1.0, "updated": 2.0}],
            "settings": {"body_size": 13, "tabs": ["old1"], "side_w": 210},
            "styles": {},
        }

    def test_v1_imports_and_only_prefs_are_adopted(self):
        d, warn = backup.decode(self.legacy())
        self.assertEqual(warn, [])
        self.assertEqual(d["notes"][0]["content"], [["t", "旧", ["title"]], ["t", "\n", []]])
        self.assertEqual(d["prefs"], {"body_size": 13})      # tabs / side_w 被丢掉
        out = backup.overwrite(d, {"settings": {"body_size": 12, "tabs": ["x"]},
                                   "styles": {}})
        self.assertEqual(out["settings"]["body_size"], 13)
        self.assertEqual(out["settings"]["tabs"], ["x"])      # 本机会话状态不动

    def test_human_time_accepts_both_formats(self):
        # v1 的裸串（无时区）也能解析；两种格式统一显示到分钟
        self.assertEqual(backup.human_time("2026-10-29 22:36:21"), "2026-10-29 22:36")
        got = backup.human_time("2026-10-30T10:52:16+08:00")
        self.assertTrue(got.startswith("2026-10-30 10:52"))
        self.assertEqual(backup.human_time("不是时间"), "不是时间")


class MergeTest(unittest.TestCase):
    def cur(self):
        return {"folders": [{"id": "f_all", "name": "全部笔记", "smart": True},
                            {"id": "f_work", "name": "工作"}],
                "notes": [note("n1", "同一篇")],
                "current_folder": "f_work", "current_note": "n1",
                "settings": {"body_size": 12, "tabs": ["n1"]}, "styles": {}}

    def backup_data(self):
        return {"folders": [{"id": "f_work", "name": "工作"},
                            {"id": "f_other", "name": "另一台机器的文件夹"}],
                "notes": [note("n1", "同一篇"),            # 内容相同 → 跳过
                          note("n2", "新增的", folder="f_other")],
                "prefs": {"body_size": 20}, "styles": {"c_00ff00": {"fg": "#00ff00"}},
                "exported_at": "2026-10-30T10:00:00+08:00"}

    def test_merge_is_idempotent(self):
        once = backup.merge(self.backup_data(), self.cur())
        twice = backup.merge(self.backup_data(), once)
        self.assertEqual([n["id"] for n in once["notes"]], ["n1", "n2"])
        self.assertEqual([n["id"] for n in twice["notes"]], ["n1", "n2"])
        self.assertEqual(len(twice["notes"]), 2)

    def test_merge_keeps_local_prefs_and_adds_styles(self):
        out = backup.merge(self.backup_data(), self.cur())
        self.assertEqual(out["settings"]["body_size"], 12)     # 外观不跟着备份变
        self.assertEqual(out["settings"]["tabs"], ["n1"])      # 会话状态保留
        self.assertIn("c_00ff00", out["styles"])               # 样式表取并集

    def test_same_id_different_content_gets_new_id(self):
        d = {"folders": [], "notes": [note("n1", "被改过的另一份")], "prefs": {}, "styles": {}}
        out = backup.merge(d, self.cur())
        ids = [n["id"] for n in out["notes"]]
        self.assertEqual(len(ids), 2)
        self.assertIn("n1", ids)
        self.assertNotEqual(ids[1], "n1")

    def test_folder_id_conflict_is_remapped(self):
        """备份里的 f_dup 与本机同名不同内容 → 笔记要落在**映射后**的那个文件夹。"""
        cur = {"folders": [{"id": "f_all", "name": "全部笔记", "smart": True},
                           {"id": "f_dup", "name": "工作"}],
               "notes": [], "settings": {}, "styles": {}}
        d = {"folders": [{"id": "f_dup", "name": "工作"},
                         {"id": "f_child", "name": "子", "parent": "f_dup"}],
             "notes": [note("n9", "落对文件夹", folder="f_child")],
             "prefs": {}, "styles": {}}
        out = backup.merge(d, cur)
        by = dict((f["id"], f) for f in out["folders"])
        self.assertEqual(by["f_child"]["parent"], "f_dup")   # 父引用跟着映射
        self.assertEqual(out["notes"][0]["folder"], "f_child")

    def test_same_name_folder_is_reused_not_duplicated(self):
        cur = {"folders": [{"id": "f_all", "name": "全部笔记", "smart": True},
                           {"id": "f_mine", "name": "工作"}],
               "notes": [], "settings": {}, "styles": {}}
        d = {"folders": [{"id": "f_theirs", "name": "工作"}],
             "notes": [note("n5", "进已有的文件夹", folder="f_theirs")],
             "prefs": {}, "styles": {}}
        out = backup.merge(d, cur)
        names = [f["name"] for f in out["folders"]]
        self.assertEqual(names.count("工作"), 1)
        self.assertEqual(out["notes"][0]["folder"], "f_mine")

    def test_orphan_note_falls_back_to_work_folder(self):
        d = {"folders": [], "notes": [note("n7", "游民", folder="f_ghost")],
             "prefs": {}, "styles": {}}
        out = backup.merge(d, self.cur())
        self.assertEqual(out["notes"][-1]["folder"], "f_work")

    def test_smart_folders_are_not_duplicated(self):
        out = backup.merge(self.backup_data(), self.cur())
        ids = [f["id"] for f in out["folders"]]
        for fid in SMART_FOLDERS:
            self.assertEqual(ids.count(fid), 1)


class OverwriteTest(unittest.TestCase):
    def test_overwrite_adopts_prefs_and_resets_view(self):
        d = {"folders": [{"id": "f_work", "name": "工作"}],
             "notes": [note("n1", "x")], "prefs": {"body_size": 18}, "styles": {}}
        out = backup.overwrite(d, {"settings": {"body_size": 12, "tabs": ["old"]},
                                   "styles": {"c_1": {"fg": "#111111"}}})
        self.assertEqual(out["settings"]["body_size"], 18)
        self.assertEqual(out["settings"]["tabs"], ["old"])
        self.assertEqual(out["current_folder"], "f_all")
        self.assertIsNone(out["current_note"])
        self.assertIn("c_1", out["styles"])
        self.assertIn("f_work", [f["id"] for f in out["folders"]])

    def test_encrypted_note_keeps_ciphertext_only(self):
        blob = {"v": 1, "iter": 200000, "salt": "s", "nonce": "n",
                "data": "AAAA", "tag": "t"}
        d = {"folders": [], "notes": [{"id": "n1", "content": [["t", "明文残留", []]],
                                      "sec": blob}],
             "prefs": {}, "styles": {}}
        out = backup.overwrite(d)
        n = out["notes"][0]
        self.assertEqual(n["content"], [])       # 以密文为准
        self.assertEqual(n["sec"], blob)


if __name__ == "__main__":
    unittest.main(verbosity=2)
