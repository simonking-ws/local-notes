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


class TestText(unittest.TestCase):
    def test_collapse_whitespace(self):
        self.assertEqual(alarm.normalize_text("  交\n周报  "), "交 周报")

    def test_truncate(self):
        self.assertEqual(len(alarm.normalize_text("字" * 500)), alarm.MAX_TEXT)

    def test_none(self):
        self.assertEqual(alarm.normalize_text(None), "")


if __name__ == "__main__":
    unittest.main()
