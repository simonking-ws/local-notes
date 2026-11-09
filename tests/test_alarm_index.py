# -*- coding: utf-8 -*-
"""「任务提醒」智能分类 + 「任务提醒列表」汇总笔记的回归测试（纯数据层，不建窗口）。

锁住 2026-11-09 用户定下的五条语义：
  1. 分类排在「加密笔记」下面，不可改名 / 移动（rename_folder 的拦截在真窗口测）；
  2. 该分类下**只有一篇**自动生成的「任务提醒列表」；
  3. 每行四字段：什么时间 / 做什么任务 / 是否已完成 / 是否已确认，末尾
     `@源笔记` 是 note: 内链；
  4. 已完成 = 提醒时间已过（自动），已确认 = 用户点过弹窗「知道了」（独立状态，
     过期自动补记的 alarm_fired 不算）；
  5. 排版是**五列制表符表格**（含加粗表头），逐字符挂 ALARM_ROW_TAG；像素级
     对齐由 app_ui._config_tags() 配在该标签上的 tag 级制表位完成（真窗口测
     量列起点），不许退回「按字数补空格」。

运行：
    python tests/test_alarm_index.py
"""
import datetime
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import alarm                                                   # noqa: E402
import backup                                                  # noqa: E402
from store import (_t, SMART_FOLDERS, SMART_FOLDER_NAMES, smart_notes,
                   ALARM_INDEX_ID, ALARM_INDEX_TITLE,
                   ALARM_ROW_TAG, ALARM_TASK_COL, ALARM_HEADERS,
                   alarm_index_content, _ensure_alarm_index_note,
                   _ensure_smart_folders, seed, _disp_w)        # noqa: E402


def _ts(y, mo, d, hh, mm):
    return datetime.datetime(y, mo, d, hh, mm).timestamp()


NOW = _ts(2026, 11, 9, 12, 0)
PAST = _ts(2026, 11, 9, 9, 0)
FUTURE = _ts(2026, 11, 9, 18, 0)


def _note(nid, title, alarms=()):
    body = _t(title, ["title"]) + [["t", "\n", []]]
    for a in alarms:
        body.append(["alarm", dict(a)])
    return {"id": nid, "folder": "f_work", "content": body,
            "created": 0.0, "updated": 0.0}


def _data(notes, acked=None):
    return {"folders": [{"id": f, "name": SMART_FOLDER_NAMES[f], "smart": True}
                        for f in SMART_FOLDERS],
            "notes": list(notes),
            "settings": {"alarm_acked": dict(acked or {})}}


def _text(content):
    return "".join(it[1] for it in content if it[0] == "t")


def _linked(content, tag):
    """带 tag 的字符拼起来（验证内链真的落在 @源笔记 那截文字上）。"""
    return "".join(it[1] for it in content
                   if it[0] == "t" and len(it) > 2 and tag in it[2])


def _rows(content):
    """只取表格行（含制表符的行），保留列分隔以便逐格断言。"""
    return [ln for ln in _text(content).split("\n") if "\t" in ln]


class TestSmartFolder(unittest.TestCase):
    def test_alarm_folder_is_right_after_lock(self):
        self.assertEqual(tuple(SMART_FOLDERS[:3]), ("f_all", "f_star", "f_lock"))
        self.assertEqual(SMART_FOLDERS[3], "f_alarm",
                         "「任务提醒」必须紧接「加密笔记」")
        self.assertEqual(SMART_FOLDER_NAMES["f_alarm"], "任务提醒")

    def test_ensure_smart_folders_adds_it_in_order(self):
        d = {"folders": [{"id": "f_all", "name": "全部笔记", "smart": True},
                         {"id": "f_star", "name": "星标笔记", "smart": True},
                         {"id": "f_lock", "name": "加密笔记", "smart": True},
                         {"id": "f_work", "name": "工作"}]}
        self.assertTrue(_ensure_smart_folders(d))
        self.assertEqual([f["id"] for f in d["folders"]][:4], list(SMART_FOLDERS))
        self.assertEqual(d["folders"][3]["name"], "任务提醒")
        self.assertFalse(_ensure_smart_folders(d), "补齐后必须幂等")

    def test_view_only_shows_the_index_note(self):
        d = _data([_note("n1", "周会"), _note("n2", "读书")])
        _ensure_alarm_index_note(d, NOW)
        got = smart_notes("f_alarm", d["notes"])
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["id"], ALARM_INDEX_ID)
        # 普通文件夹视图不该冒出这一篇（它的 folder 指向智能分类，不是真实归属）
        self.assertNotIn(ALARM_INDEX_ID,
                         [n["id"] for n in smart_notes("f_work", d["notes"])])

    def test_seed_has_both(self):
        d = seed()
        self.assertIn("f_alarm", [f["id"] for f in d["folders"]])
        self.assertEqual(len(smart_notes("f_alarm", d["notes"])), 1)


class TestIndexContent(unittest.TestCase):
    def test_four_fields_and_inline_link(self):
        d = _data([_note("n1", "周会待办", [
            {"id": "a1", "text": "交周报", "at": PAST}])],
            acked={"n1|a1": NOW})
        content, links = alarm_index_content(d, NOW)
        s = _text(content)
        self.assertIn(ALARM_INDEX_TITLE, s)
        self.assertIn("2026-11-09 09:00", s, "什么时间：完整年月日 + 时分")
        self.assertIn("交周报", s, "做什么任务")
        self.assertIn("已完成：是", s, "时间已过 = 已完成")
        self.assertIn("已确认：是", s, "点过弹窗 = 已确认")
        self.assertIn("@周会待办", s)
        self.assertEqual(links, {"lk_1": "note:n1"}, "内链必须指向源笔记 id")
        self.assertIn("@周会待办", _linked(content, "lk_1"))
        self.assertIn("@周会待办", _linked(content, "link"),
                      "少了共用的 link 标签，点击跳转不会生效")

    def test_done_is_pure_time_not_a_stored_flag(self):
        past = {"id": "a1", "text": "交周报", "at": PAST}
        future = {"id": "a2", "text": "买菜", "at": FUTURE}
        no_time = {"id": "a3", "text": "随手记", "at": 0}
        d = _data([_note("n1", "杂事", [past, future, no_time])])
        s = _text(alarm_index_content(d, NOW)[0])
        self.assertEqual(s.count("已完成：是"), 1)
        self.assertEqual(s.count("已完成：否"), 2, "未来 / 未设时间都不算完成")
        self.assertIn("未设时间", s)
        # 同一份数据，把「现在」推到 19:00，未来的那条自动翻成已完成
        later = _text(alarm_index_content(d, _ts(2026, 11, 9, 19, 0))[0])
        self.assertEqual(later.count("已完成：是"), 2)

    def test_acked_is_independent_from_fired(self):
        """过期自动补记只写 alarm_fired，绝不能算「已确认」。"""
        d = _data([_note("n1", "周会", [
            {"id": "a1", "text": "交周报", "at": PAST}])],
            acked={})
        d["settings"]["alarm_fired"] = {"n1|a1": NOW}
        s = _text(alarm_index_content(d, NOW)[0])
        self.assertIn("已完成：是", s)
        self.assertIn("已确认：否", s, "fired 不等于 acked")
        d["settings"]["alarm_acked"]["n1|a1"] = NOW
        self.assertIn("已确认：是", _text(alarm_index_content(d, NOW)[0]))

    def test_empty_state_and_sort(self):
        d = _data([_note("n1", "只有文字")])
        s = _text(alarm_index_content(d, NOW)[0])
        self.assertIn("还没有任务提醒", s)

        d = _data([
            _note("n1", "甲", [{"id": "a1", "text": "晚的", "at": FUTURE}]),
            _note("n2", "乙", [{"id": "a2", "text": "没时间", "at": 0}]),
            _note("n3", "丙", [{"id": "a3", "text": "早的", "at": PAST}]),
        ])
        s = _text(alarm_index_content(d, NOW)[0])
        self.assertLess(s.index("早的"), s.index("晚的"), "有时间的按时间升序")
        self.assertLess(s.index("晚的"), s.index("没时间"), "没设时间的沉底")
        links = alarm_index_content(d, NOW)[1]
        self.assertEqual(set(links.values()),
                         {"note:n1", "note:n2", "note:n3"})

    def test_link_targets_point_at_real_notes(self):
        """同一篇笔记里两条提醒 → 两条内链，都指回那篇。"""
        d = _data([_note("n1", "周会", [
            {"id": "a1", "text": "交周报", "at": PAST},
            {"id": "a2", "text": "订会议室", "at": FUTURE}])])
        links = alarm_index_content(d, NOW)[1]
        self.assertEqual(len(links), 2)
        self.assertEqual(set(links.values()), {"note:n1"})


class TestEnsureIndex(unittest.TestCase):
    def test_idempotent_and_repairs_drift(self):
        d = _data([_note("n1", "周会", [
            {"id": "a1", "text": "交周报", "at": PAST}])])
        self.assertTrue(_ensure_alarm_index_note(d, NOW))
        me = next(n for n in d["notes"] if n["id"] == ALARM_INDEX_ID)
        self.assertEqual(me["folder"], "f_alarm")
        self.assertFalse(me.get("sample"), "别被当成内置说明走进升级流程")
        self.assertFalse(_ensure_alarm_index_note(d, NOW), "同一时刻重算必须幂等")
        # 用户从任何入口把它挪走 / 上锁，启动自检都要拉回来
        me["folder"] = "f_work"
        me["sample"] = True
        self.assertTrue(_ensure_alarm_index_note(d, NOW))
        self.assertEqual(me["folder"], "f_alarm")
        self.assertNotIn("sample", me)

    def test_content_follows_new_alarms(self):
        d = _data([_note("n1", "周会")])
        _ensure_alarm_index_note(d, NOW)
        src = next(n for n in d["notes"] if n["id"] == "n1")
        src["content"].append(
            ["alarm", {"id": "a1", "text": "新提醒", "at": FUTURE}])
        self.assertTrue(_ensure_alarm_index_note(d, NOW))
        me = next(n for n in d["notes"] if n["id"] == ALARM_INDEX_ID)
        self.assertIn("新提醒", _text(me["content"]))

    def test_updated_is_not_touched(self):
        """重算内容不该把汇总笔记顶到「全部笔记」最前面。"""
        d = _data([_note("n1", "周会")])
        _ensure_alarm_index_note(d, NOW)
        me = next(n for n in d["notes"] if n["id"] == ALARM_INDEX_ID)
        me["updated"] = 123.0
        src = next(n for n in d["notes"] if n["id"] == "n1")
        src["content"].append(
            ["alarm", {"id": "a1", "text": "新提醒", "at": FUTURE}])
        _ensure_alarm_index_note(d, NOW)
        self.assertEqual(me["updated"], 123.0)


class TestBackup(unittest.TestCase):
    def test_encode_excludes_index_and_state(self):
        d = _data([_note("n1", "周会", [
            {"id": "a1", "text": "交周报", "at": PAST}])],
            acked={"n1|a1": NOW})
        d["settings"]["alarm_fired"] = {"n1|a1": NOW}
        _ensure_alarm_index_note(d, NOW)
        payload = backup.encode(d)
        self.assertNotIn(ALARM_INDEX_ID, [n.get("id") for n in payload["notes"]])
        self.assertNotIn("alarm_acked", payload["prefs"])
        self.assertNotIn("alarm_fired", payload["prefs"])

    def test_overwrite_and_merge_rebuild_it(self):
        d = _data([_note("n1", "周会", [
            {"id": "a1", "text": "交周报", "at": PAST}])])
        _ensure_alarm_index_note(d, NOW)
        payload, _warn = backup.decode(backup.encode(d))
        for rebuilt in (backup.overwrite(payload), backup.merge(payload, d)):
            got = smart_notes("f_alarm", rebuilt["notes"])
            self.assertEqual(len(got), 1, "导入后必须补回且只补回一篇")
            self.assertEqual(got[0]["id"], ALARM_INDEX_ID)
            self.assertIn("交周报", _text(got[0]["content"]),
                          "补回的那篇要按导入后的全库提醒现算，不能是空表")

    def test_acked_state_is_kept_on_this_machine(self):
        d = _data([_note("n1", "周会", [
            {"id": "a1", "text": "交周报", "at": PAST}])],
            acked={"n1|a1": NOW})
        _ensure_alarm_index_note(d, NOW)
        payload, _warn = backup.decode(backup.encode(d))
        out = backup.overwrite(payload, d)
        self.assertEqual(out["settings"].get("alarm_acked"), {"n1|a1": NOW})


class TestAlarmHelpers(unittest.TestCase):
    def test_fmt_full_and_is_done(self):
        self.assertEqual(alarm.fmt_full(PAST), "2026-11-09 09:00")
        self.assertEqual(alarm.fmt_full(0), "")
        self.assertTrue(alarm.is_done({"at": PAST}, NOW))
        self.assertFalse(alarm.is_done({"at": FUTURE}, NOW))
        self.assertFalse(alarm.is_done({"at": 0}, NOW))
        self.assertFalse(alarm.is_done({}, NOW))


class TestTableLayout(unittest.TestCase):
    """五列表格：制表符分列 + ALARM_ROW_TAG 逐字符挂标签（像素对齐在真窗口测）。"""

    def test_header_plus_five_columns_every_row(self):
        d = _data([
            _note("n1", "甲", [
                {"id": "a1", "text": "短", "at": PAST},
                {"id": "a2", "text": "订会议室并通知全体参会同事", "at": FUTURE},
                {"id": "a3", "text": "buy milk and eggs", "at": 0}]),
        ])
        content, links = alarm_index_content(d, NOW)
        rows = _rows(content)
        self.assertEqual(len(rows), 4, "1 行表头 + 3 行数据")
        self.assertEqual(tuple(rows[0].split("\t")), ALARM_HEADERS)
        for r in rows[1:]:
            cells = r.split("\t")
            self.assertEqual(len(cells), 5, "每行必须是五列：%r" % r)
            self.assertTrue(cells[0] == "未设时间" or len(cells[0]) == 16)
            self.assertTrue(cells[2].startswith("已完成："))
            self.assertTrue(cells[3].startswith("已确认："))
            self.assertTrue(cells[4].startswith("@"))
        self.assertEqual(set(links.values()), {"note:n1"})

    def test_every_table_character_carries_the_row_tag(self):
        """少了 ALARM_ROW_TAG，那一行的 tag 级制表位就不生效、整行会错位。"""
        d = _data([_note("n1", "周会", [
            {"id": "a1", "text": "交周报", "at": PAST}])])
        content, _links = alarm_index_content(d, NOW)
        tagged = _linked(content, ALARM_ROW_TAG)
        for r in _rows(content):
            self.assertIn(r, tagged, "这一行有字符漏挂 %s：%r" % (ALARM_ROW_TAG, r))
        # 内链那格三个标签一个都不能少
        linked = _linked(content, "link")
        self.assertIn("@周会", linked)
        self.assertIn("@周会", _linked(content, "lk_1"))

    def test_long_task_is_clipped_without_breaking_columns(self):
        long = ("这是一个特别长的任务名称用来验证超过任务列宽度之后会被截断"
                "显示省略号而不会把后面的列挤歪")
        d = _data([_note("n1", "周会", [
            {"id": "a1", "text": long, "at": PAST}])])
        content, links = alarm_index_content(d, NOW)
        cells = _rows(content)[1].split("\t")
        self.assertEqual(len(cells), 5, "截断不能多切或少切列")
        self.assertTrue(cells[1].endswith("…"))
        self.assertLessEqual(_disp_w(cells[1]), ALARM_TASK_COL)
        self.assertNotIn("挤歪", cells[1], "超长尾巴不该漏进表格")
        self.assertEqual(cells[2], "已完成：是")
        self.assertEqual(links, {"lk_1": "note:n1"})

    def test_control_chars_cannot_split_a_column(self):
        d = _data([_note("n1", "周\t会\n待办", [
            {"id": "a1", "text": "买菜\t做饭\n顺便", "at": PAST}])])
        content, _links = alarm_index_content(d, NOW)
        cells = _rows(content)[1].split("\t")
        self.assertEqual(len(cells), 5, "任务 / 标题里的制表符不能凭空多切一列")
        self.assertNotIn("\t", cells[1])
        self.assertNotIn("\n", cells[1])
        self.assertTrue(cells[4].startswith("@"))

    def test_empty_state_has_no_header(self):
        content, links = alarm_index_content(
            _data([_note("n1", "只有文字")]), NOW)
        self.assertEqual(_rows(content), [], "空表不该挂个孤零零的表头")
        self.assertEqual(links, {})
        self.assertIn("还没有任务提醒", _text(content))


if __name__ == "__main__":
    unittest.main(verbosity=2)
