# -*- coding: utf-8 -*-
"""任务提醒（alarm.py）的单测：时间解析、悬浮文案、到点判定。

    python tests\\test_alarm.py
或：
    python -m unittest discover -s tests -v
"""
import datetime
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

import alarm


class TestParseWhen(unittest.TestCase):
    def setUp(self):
        self.now = datetime.datetime(2026, 10, 29, 12, 0)

    def test_empty_time_means_no_alarm(self):
        # 时间留空 = 只是个标记，不提醒
        self.assertEqual(alarm.parse_when("", "", self.now), (0.0, ""))

    def test_today_when_date_empty(self):
        ts, err = alarm.parse_when("", "18:00", self.now)
        self.assertEqual(err, "")
        self.assertEqual(datetime.datetime.fromtimestamp(ts),
                         datetime.datetime(2026, 10, 29, 18, 0))

    def test_month_day_shorthand(self):
        ts, err = alarm.parse_when("10-30", "09:00", self.now)
        self.assertEqual(err, "")
        self.assertEqual(datetime.datetime.fromtimestamp(ts),
                         datetime.datetime(2026, 10, 30, 9, 0))

    def test_full_date(self):
        ts, err = alarm.parse_when("2026-12-01", "7:05", self.now)
        self.assertEqual(err, "")
        self.assertEqual(datetime.datetime.fromtimestamp(ts),
                         datetime.datetime(2026, 12, 1, 7, 5))

    def test_chinese_date(self):
        ts, err = alarm.parse_when("10月30日", "09:00", self.now)
        self.assertEqual(err, "")
        self.assertEqual(datetime.datetime.fromtimestamp(ts).month, 10)

    def test_chinese_colon_in_time(self):
        ts, err = alarm.parse_when("", "18：30", self.now)
        self.assertEqual(err, "")
        self.assertEqual(datetime.datetime.fromtimestamp(ts).minute, 30)

    def test_bad_time(self):
        self.assertTrue(alarm.parse_when("", "abc", self.now)[1])
        self.assertTrue(alarm.parse_when("", "25:00", self.now)[1])
        self.assertTrue(alarm.parse_when("", "18:70", self.now)[1])

    def test_bad_date(self):
        self.assertTrue(alarm.parse_when("2月30日", "09:00", self.now)[1])
        self.assertTrue(alarm.parse_when("瞎写", "09:00", self.now)[1])


class TestDefaultWhen(unittest.TestCase):
    """新建对话框的默认时刻（原来写死在对话框里，跨零点会算出逾期任务）。"""

    def test_next_whole_hour(self):
        d, hh, mm = alarm.default_when(datetime.datetime(2026, 10, 29, 10, 5))
        self.assertEqual((d, hh, mm), ("", 11, 0))

    def test_fractional_hour_rounds_up(self):
        d, hh, mm = alarm.default_when(datetime.datetime(2026, 10, 29, 10, 59))
        self.assertEqual((d, hh, mm), ("", 11, 0))

    def test_cross_midnight_fills_date(self):
        # 23:30 新建 → 次日 00:00；日期必须一起给，否则「留空＝今天」会算成今天 00:00
        d, hh, mm = alarm.default_when(datetime.datetime(2026, 10, 29, 23, 30))
        self.assertEqual((d, hh, mm), ("2026-10-30", 0, 0))

    def test_cross_midnight_on_last_day_of_year(self):
        d, hh, mm = alarm.default_when(datetime.datetime(2026, 12, 31, 23, 40))
        self.assertEqual((d, hh, mm), ("2027-01-01", 0, 0))

    def test_default_is_always_a_valid_time(self):
        for h in range(24):
            for m in (0, 30, 59):
                now = datetime.datetime(2026, 10, 29, h, m)
                d, hh, mm = alarm.default_when(now)
                ts, err = alarm.parse_when(d, "%02d:%02d" % (hh, mm), now)
                self.assertEqual(err, "")
                self.assertGreater(ts, now.timestamp())    # 绝不落在过去


class TestMonthGrid(unittest.TestCase):
    """日历控件的网格：唯一要求是「日号落在正确的星期列上」。"""

    def test_always_42(self):
        # 固定 6 行：翻月时对话框高度不跳，‹ › 不会上下窜
        for y, m in ((2026, 1), (2026, 2), (2026, 8), (2024, 2), (1999, 12)):
            self.assertEqual(len(alarm.month_grid(y, m)), 42)

    def test_october_2026_starts_thursday(self):
        # 2026-10-01 是周四 → 前三个格子补白（周一/二/三）
        g = alarm.month_grid(2026, 10)
        self.assertEqual(g[:7], [0, 0, 0, 1, 2, 3, 4])
        self.assertEqual(g[7], 5)
        self.assertEqual(g[-1], 0)

    def test_february_leap(self):
        self.assertEqual(max(alarm.month_grid(2024, 2)), 29)
        self.assertEqual(max(alarm.month_grid(2026, 2)), 28)

    def test_all_days_present_once(self):
        for y, m in ((2026, 1), (2026, 7), (2027, 2)):
            g = alarm.month_grid(y, m)
            days = [d for d in g if d]
            self.assertEqual(days, list(range(1, len(days) + 1)))

    def test_weekday_order_is_monday_first(self):
        # 表头以周一开头，网格补白数就必须用 calendar 的周一=0
        self.assertEqual(alarm.WEEKDAYS[0], "一")
        self.assertEqual(len(alarm.WEEKDAYS), 7)
        g = alarm.month_grid(2026, 6)          # 2026-06-01 是周一
        self.assertEqual(g[0], 1)

    def test_shift_month_wraps(self):
        self.assertEqual(alarm.shift_month(2026, 1, -1), (2025, 12))
        self.assertEqual(alarm.shift_month(2026, 12, 1), (2027, 1))
        self.assertEqual(alarm.shift_month(2026, 10, 4), (2027, 2))
        self.assertEqual(alarm.shift_month(2026, 10, -22), (2024, 12))
        self.assertEqual(alarm.shift_month(2026, 10, 0), (2026, 10))


class TestFmtAt(unittest.TestCase):
    def test_no_time(self):
        self.assertEqual(alarm.fmt_at(0), "")

    def test_relative_words(self):
        today = datetime.date.today()
        t0 = datetime.datetime.combine(today, datetime.time(18, 0))
        self.assertEqual(alarm.fmt_at(t0.timestamp()), "今天 18:00")
        t1 = t0 + datetime.timedelta(days=1)
        self.assertEqual(alarm.fmt_at(t1.timestamp()), "明天 18:00")


class TestTips(unittest.TestCase):
    NOW = 1793300000.0

    def test_no_time(self):
        title, sub = alarm.tips({"text": "买菜", "at": 0}, now=self.NOW)
        self.assertEqual(title, "买菜")
        self.assertIn("未设时间", sub)

    def test_future(self):
        _, sub = alarm.tips({"text": "交周报", "at": self.NOW + 3600}, now=self.NOW)
        self.assertIn("提醒", sub)

    def test_past_not_fired(self):
        _, sub = alarm.tips({"text": "交周报", "at": self.NOW - 60}, now=self.NOW)
        self.assertIn("已到点", sub)

    def test_fired_wins(self):
        # 已经响过：状态优先于时间和内容
        _, sub = alarm.tips({"text": "交周报", "at": self.NOW + 3600},
                            fired=True, now=self.NOW)
        self.assertIn("已提醒", sub)


class TestDue(unittest.TestCase):
    def test_split(self):
        now = 1000000.0
        items = [("n", {"id": "a", "at": now - 60}),     # 刚过点 → 弹
                 ("n", {"id": "b", "at": now - 3600}),   # 过期太久 → 只记状态
                 ("n", {"id": "c", "at": now + 600}),    # 还没到
                 ("n", {"id": "d", "at": 0})]            # 没设时间
        fire, stale = alarm.due(items, now)
        self.assertEqual([d["id"] for _, d in fire], ["a"])
        self.assertEqual([d["id"] for _, d in stale], ["b"])

    def test_grace_boundary(self):
        now = 1000000.0
        edge = now - alarm.GRACE
        fire, stale = alarm.due([("n", {"id": "x", "at": edge})], now)
        self.assertEqual(len(fire), 1)                   # 正好卡在容差上 → 弹
        fire, stale = alarm.due([("n", {"id": "x", "at": edge - 1})], now)
        self.assertEqual((len(fire), len(stale)), (0, 1))


class TestStateOf(unittest.TestCase):
    """四态判定：正文卡片与汇总表共用，这里锁死优先级（2026-11-18）。"""

    NOW = 1793300000.0

    def test_four_states(self):
        self.assertEqual(alarm.state_of({"at": self.NOW + 60}, now=self.NOW),
                         alarm.STATE_PENDING)
        self.assertEqual(alarm.state_of({"at": self.NOW - 60}, now=self.NOW),
                         alarm.STATE_EXPIRED)
        self.assertEqual(alarm.state_of({"at": self.NOW - 60}, acked=True,
                                        now=self.NOW), alarm.STATE_ACKED)
        self.assertEqual(alarm.state_of({"at": 0}, now=self.NOW), alarm.STATE_MARK)

    def test_acked_beats_expired(self):
        # 点过「知道了」还显示已过期 = 用户以为没生效
        self.assertEqual(alarm.state_of({"at": self.NOW - 3600}, acked=True,
                                        now=self.NOW), alarm.STATE_ACKED)

    def test_mark_beats_acked(self):
        # 没设时间从不弹窗，误写 acked 也只算标记
        self.assertEqual(alarm.state_of({"at": 0}, acked=True, now=self.NOW),
                         alarm.STATE_MARK)

    def test_labels_and_symbols_are_bmp(self):
        self.assertEqual(set(alarm.STATES),
                         set(alarm.STATE_LABELS) | set(alarm.STATE_SYMBOLS))
        for ch in alarm.STATE_SYMBOLS.values():
            self.assertLess(ord(ch), 0x10000, "Tcl/Tk 不吃 U+FFFF 以上的字符")


class TestRepeatRules(unittest.TestCase):
    """重复提醒的规则与槽位（2026-11-18 用户定三种类型）。"""

    def test_old_data_defaults_to_once(self):
        d = {"id": "a", "text": "交周报", "at": 1793700000.0}
        self.assertEqual(alarm.repeat_kind(d), alarm.REPEAT_ONCE)
        self.assertEqual(alarm.fmt_repeat(d), alarm.fmt_at(d["at"]))
        self.assertEqual(alarm.state_key("n|a", d), "n|a")   # 旧键不变

    def test_daily_rule_text_and_next(self):
        now = 1793700000.0                      # 2026-11-03 前后
        anchor = datetime.datetime.fromtimestamp(now).replace(
            hour=0, minute=0, second=0, microsecond=0).timestamp()
        d = {"id": "a", "text": "吃药", "at": anchor,
             "repeat": "daily", "hm": "09:00"}
        self.assertEqual(alarm.fmt_repeat(d), "每天 09:00")
        self.assertIn("下次", alarm.fmt_when(d, now))
        self.assertGreater(alarm.next_occ(d, now), now)

    def test_weekly_rule_multi_select_and_text(self):
        anchor = datetime.datetime(2026, 11, 1).timestamp()
        d = {"id": "a", "text": "周会", "at": anchor,
             "repeat": "weekly", "hm": "09:00", "days": [0, 2, 4]}
        self.assertEqual(alarm.fmt_repeat(d), "每周一三五 09:00")
        nxt = alarm.next_occ(d, datetime.datetime(2026, 11, 18, 10).timestamp())
        self.assertTrue(datetime.datetime.fromtimestamp(nxt).weekday() in (0, 2, 4))

    def test_normalize_rule_no_weekday_selected_is_empty(self):
        self.assertEqual(alarm.normalize_rule("bogus", "9:5", [99]),
                         {"repeat": "once"})
        self.assertEqual(alarm.normalize_rule("weekly", "25:00", [1, 1, 7, 2]),
                         {"repeat": "weekly", "hm": "09:00", "days": [1, 2]})

    def test_due_all_fires_once_per_day_and_not_twice(self):
        base = datetime.datetime(2026, 11, 15).timestamp()
        d = {"id": "a", "text": "吃药", "at": base,
             "repeat": "daily", "hm": "09:00"}
        fired = {}
        key_of = lambda nid, x: "%s|%s" % (nid, x["id"])
        t0 = datetime.datetime(2026, 11, 18, 16).timestamp()
        f, s = alarm.due_all([("n1", d)], fired, key_of, t0)
        self.assertEqual(len(f), 1, "当天没响过就该弹，不受 GRACE 限制")
        self.assertEqual(len(s), 0)
        fired[f[0][2]] = t0
        f2, _ = alarm.due_all([("n1", d)], fired, key_of, t0 + 60)
        self.assertEqual(f2, [], "同一天不该弹两次")
        f3, _ = alarm.due_all([("n1", d)], fired, key_of, t0 + 86400)
        self.assertEqual(len(f3), 1, "第二天照常响")
        self.assertNotEqual(f3[0][2], f[0][2], "第二天的槽位键必须不同于今天")

    def test_acked_is_per_cycle(self):
        now = time.time()
        d = {"id": "a", "text": "吃药", "at": now - 7200,
             "repeat": "daily", "hm": "09:00"}
        self.assertEqual(alarm.state_of(d, acked=True, now=now), alarm.STATE_ACKED)
        # 明天同一个「今天确认过」的旧键不再生效
        k = alarm.state_key("n|a", d, now)
        self.assertTrue(k.endswith(datetime.date.today().isoformat()))
        self.assertNotEqual(k, alarm.state_key("n|a", d, now + 86400))


class TestAckedSuppressesPopup(unittest.TestCase):
    """正文卡片上点过「完成」的提醒，到点不许再弹（用户 2026-11-18 要求）。

    历史 bug：`_alarm_check` 只查 `alarm_fired`，而卡片「完成」写的是
    `alarm_acked` —— 两条各管一件事，于是一边显示「已确认」、一边照样弹窗。
    """

    KEY = staticmethod(lambda nid, x: "%s|%s" % (nid, x["id"]))

    def test_once_acked_before_due_never_fires(self):
        at = datetime.datetime(2026, 11, 18, 18).timestamp()
        d = {"id": "a", "text": "交周报", "at": at}
        key = "n1|a"
        # 提前点「完成」：时间还没到，ack 已写
        f, s = alarm.due_all([("n1", d)], {}, self.KEY, at + 30,
                             acked={key: at - 600})
        self.assertEqual(f, [], "已确认的提醒不该弹窗")
        self.assertEqual(s, [], "也不该被补记成 stale/fired")

    def test_once_acked_still_suppresses_when_long_overdue(self):
        """过期很久也只能靠 acked 挡住 —— 否则用户确认过的会被补记一波。"""
        at = datetime.datetime(2026, 11, 18, 9).timestamp()
        d = {"id": "a", "text": "交周报", "at": at}
        f, s = alarm.due_all([("n1", d)], {}, self.KEY, at + 8 * 3600,
                             acked={"n1|a": at})
        self.assertEqual((f, s), ([], []))

    def test_repeat_ack_only_blocks_current_slot(self):
        base = datetime.datetime(2026, 11, 15).timestamp()
        d = {"id": "a", "text": "吃药", "at": base,
             "repeat": "daily", "hm": "09:00"}
        t0 = datetime.datetime(2026, 11, 18, 16).timestamp()
        today_key = alarm.state_key("n1|a", d, t0)
        f, s = alarm.due_all([("n1", d)], {}, self.KEY, t0,
                             acked={today_key: t0})
        self.assertEqual((f, s), ([], []), "今天已确认，不弹")
        f2, _ = alarm.due_all([("n1", d)], {}, self.KEY, t0 + 86400,
                              acked={today_key: t0})
        self.assertEqual(len(f2), 1, "明天是新槽位，照常提醒")

    def test_acked_does_not_swallow_other_reminders(self):
        at = datetime.datetime(2026, 11, 18, 18).timestamp()
        a = {"id": "a", "text": "已确认的", "at": at}
        b = {"id": "b", "text": "没确认的", "at": at}
        f, _ = alarm.due_all([("n1", a), ("n1", b)], {}, self.KEY, at + 10,
                             acked={"n1|a": at})
        self.assertEqual([x[1]["id"] for x in f], ["b"])

    def test_ack_before_first_slot_targets_the_upcoming_one(self):
        """到点前点「完成」必须预确认今天那一格，不能退成静态 @start。

        历史 bug：`slot_date()` 在今天的时刻还没到、且昨天早于起始日时返回
        None，`state_key()` 于是写成 `nb|b1@start`；到点扫描算的是
        `nb|b1@2026-11-18`，两边对不上 → 点了完成照样弹窗。
        """
        base = datetime.datetime(2026, 11, 18).timestamp()   # 起始日 = 今天
        d = {"id": "b1", "text": "吃药", "at": base,
             "repeat": "daily", "hm": "09:00"}
        early = datetime.datetime(2026, 11, 18, 8, 0).timestamp()
        due = datetime.datetime(2026, 11, 18, 9, 5).timestamp()
        self.assertIsNone(alarm.slot_date(d, early), "8 点还没到今天的槽位")
        self.assertEqual(alarm.next_slot_date(d, early),
                         datetime.date(2026, 11, 18))
        early_key = alarm.state_key("n1|b1", d, early)
        self.assertEqual(early_key, alarm.state_key("n1|b1", d, due),
                         "提前确认的键必须等于到点时的槽位键")
        # 用提前写下的 acked 去扫，到点这一轮就该静默
        f, s = alarm.due_all([("n1", d)], {}, self.KEY, due,
                             acked={early_key: early})
        self.assertEqual((f, s), ([], []))

    def test_weekly_next_slot_skips_unselected_days(self):
        anchor = datetime.datetime(2026, 11, 16).timestamp()   # 周一
        d = {"id": "w", "text": "周会", "at": anchor,
             "repeat": "weekly", "hm": "09:00", "days": [0, 2]}
        wed_early = datetime.datetime(2026, 11, 18, 8, 0).timestamp()  # 周三早上
        self.assertEqual(alarm.next_slot_date(d, wed_early),
                         datetime.date(2026, 11, 18), "周三就是下一个")
        thu = datetime.datetime(2026, 11, 19, 8, 0).timestamp()
        self.assertEqual(alarm.next_slot_date(d, thu),
                         datetime.date(2026, 11, 23), "周四之后跳到下周一")


class TestText(unittest.TestCase):
    def test_collapse_whitespace(self):
        self.assertEqual(alarm.normalize_text("  交\n周报  "), "交 周报")

    def test_truncate(self):
        self.assertEqual(len(alarm.normalize_text("字" * 500)), alarm.MAX_TEXT)

    def test_none(self):
        self.assertEqual(alarm.normalize_text(None), "")


if __name__ == "__main__":
    unittest.main()
