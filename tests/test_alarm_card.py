# -*- coding: utf-8 -*-
"""任务提醒卡片（2026-11-18 改版）的回归测试：纯逻辑 + 假 app，不建第二个 Tk。

锁住：
  1. 卡片是 window 结构项，serialize() 从 window 分支认领成 ["alarm", d]；
  2. 四态与汇总表共用 alarm.state_of()，已确认优先于已过期；
  3. 只读笔记里编辑 / 删除 / 知道了都拦住；
  4. 汇总表四列总宽与旧五列一致、状态格带着色 tag。
"""
import os
import sys
import time
import unittest
from types import MethodType

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import alarm                                              # noqa: E402
import app_actions                                        # noqa: E402
import store                                              # noqa: E402
import theme                                              # noqa: E402
import widgets                                            # noqa: E402
from store import ALARM_COLS, ALARM_HEADERS, ALARM_STATE_TAG_PREFIX  # noqa: E402


class TestAlarmCardContract(unittest.TestCase):
    def test_serialize_recognizes_alarm_card_window(self):
        """serialize 的 window 分支必须认领 AlarmCard，否则重开笔记图元凭空消失。"""
        FakeAlarmCard = type("AlarmCard", (), {})
        FakeCard = type("FakeCard", (FakeAlarmCard,), {
            "snapshot": lambda self: {"id": "a1", "text": "交周报", "at": 123.0}})
        fake = type("F", (), {})()
        fake._locked_view = False
        fake._is_ephemeral_tag = lambda self, t: False
        fake._alarms_get = lambda self: {}
        fake._imgs_get = lambda self: {}
        # 用真类做 isinstance 判定：临时替换模块的 AlarmCard 引用
        orig = app_actions.AlarmCard
        try:
            app_actions.AlarmCard = FakeAlarmCard
            fake.text = type("T", (), {
                "dump": lambda self, *a, **k: [("window", "card", "1.0")],
                "nametowidget": lambda self, v: FakeCard(),
            })()
            out = app_actions.ActionsMixin.serialize(fake)
            self.assertIn(["alarm", {"id": "a1", "text": "交周报", "at": 123.0}], out)
        finally:
            app_actions.AlarmCard = orig

    def test_state_priority(self):
        now = time.time()
        self.assertEqual(alarm.state_of({"at": now - 60}, acked=True, now=now),
                         alarm.STATE_ACKED)
        self.assertEqual(alarm.state_of({"at": now - 60}, acked=False, now=now),
                         alarm.STATE_EXPIRED)
        self.assertEqual(alarm.state_of({"at": 0}, acked=True, now=now),
                         alarm.STATE_MARK)

    def test_summary_table_four_columns(self):
        self.assertEqual(len(ALARM_COLS), 4)
        self.assertEqual(sum(ALARM_COLS), 61, "列宽总和变了会让窗口横向滚动")
        self.assertEqual(ALARM_HEADERS[2], "状态")
        self.assertTrue(ALARM_STATE_TAG_PREFIX)


class TestReadonlyGuards(unittest.TestCase):
    def _fake(self):
        fake = type("F", (), {})()
        # 这些是实例属性（不是类方法），调用时不会自动传 self
        fake._alarms_get = lambda: {"alarm:a1": {"id": "a1", "text": "x", "at": 0}}
        fake._alarm_cards_get = lambda: {}
        fake._refuse_readonly = lambda: True
        fake._alarm_dialog = lambda data: ("y", 0)
        fake.mark_modified = lambda: None
        return fake

    def test_edit_blocked(self):
        fake = self._fake()
        fake._alarm_edit = MethodType(app_actions.ActionsMixin._alarm_edit, fake)
        fake._alarm_edit("alarm:a1")
        self.assertEqual(fake._alarms_get()["alarm:a1"]["text"], "x")

    def test_remove_blocked(self):
        fake = self._fake()
        fake._alarm_tip_hide = lambda self: None
        fake._alarm_remove = MethodType(app_actions.ActionsMixin._alarm_remove, fake)
        fake._alarm_remove("alarm:a1")
        self.assertIn("alarm:a1", fake._alarms_get())

    def test_ack_blocked(self):
        fake = self._fake()
        fake._alarm_key = lambda d: "n|a1"
        fake._alarm_acked_map = lambda: {}
        fake._alarm_ack_card = MethodType(app_actions.ActionsMixin._alarm_ack_card, fake)
        fake._alarm_ack_card("alarm:a1")
        self.assertEqual(fake._alarm_acked_map(), {})


class TestAlarmCardWidth(unittest.TestCase):
    """卡片固定 400 设计像素，不随编辑区铺满（用户 2026-11-18 定）。"""

    def test_fixed_width_ignores_wide_editor(self):
        from dpi import px
        self.assertEqual(px(400), widgets.AlarmCard.CARD_W)
        # 编辑区再宽也只给 400，右侧留白
        for w in (500, 900, 2000):
            self.assertEqual(widgets.AlarmCard.fit_width(w), px(400))

    def test_shrinks_only_when_editor_narrower(self):
        from dpi import px
        self.assertEqual(widgets.AlarmCard.fit_width(px(300)), px(300))
        self.assertEqual(widgets.AlarmCard.fit_width(0), widgets.AlarmCard.MIN_W)


class TestRepeatSerialization(unittest.TestCase):
    """重复规则必须随卡片序列化往返，否则重开笔记就退回一次性。"""

    def test_snapshot_keeps_repeat_fields(self):
        fake = type("F", (), {})()
        fake.data = {"id": "a1", "text": "吃药", "at": 123.0,
                     "repeat": "daily", "hm": "09:00"}
        snap = widgets.AlarmCard.snapshot(fake)
        self.assertEqual(snap["repeat"], "daily")
        self.assertEqual(snap["hm"], "09:00")
        fake.data["repeat"] = "weekly"
        fake.data["days"] = [0, 2, 4]
        snap2 = widgets.AlarmCard.snapshot(fake)
        self.assertEqual(snap2["days"], [0, 2, 4])

    def test_backup_roundtrip_keeps_repeat_fields(self):
        import backup
        block = {"i": 0, "kind": "alarm", "id": "a1", "text": "吃药",
                 "at": 123.0, "repeat": "weekly", "hm": "09:00", "days": [1, 3]}
        decoded = backup._block_decode(block)
        self.assertEqual(decoded[1]["repeat"], "weekly")
        self.assertEqual(decoded[1]["days"], [1, 3])
        encoded = backup._block_encode(decoded, 0)
        self.assertEqual(encoded["repeat"], "weekly")
        self.assertEqual(encoded["hm"], "09:00")

    def test_old_backup_without_repeat_is_once(self):
        import backup
        decoded = backup._block_decode({"i": 0, "kind": "alarm",
                                        "id": "a1", "text": "交周报", "at": 123.0})
        self.assertNotIn("repeat", decoded[1])
        self.assertEqual(alarm.repeat_kind(decoded[1]), alarm.REPEAT_ONCE)


class TestAlarmCardBackground(unittest.TestCase):
    """设计稿（2026-11-18 用户给图）：**空心卡片** —— 底与稿纸同色，靠细描边勾形。

    曾经走过「填满主题背景色」的弯路，用户明确否决：卡片就该是白底 + 浅灰
    圆角描边，像设计稿那样。这里锁住：底=稿纸、描边可见、徽章两态分明。
    """

    def test_card_body_is_paper_color_and_border_visible(self):
        for tid in theme.THEME_ORDER:
            pal = theme.build_palette(tid)
            paper = pal["editor"]
            for state in ("pending", "expired", "acked", "mark"):
                c = widgets.AlarmCard._palette_for(None, state, pal)
                self.assertEqual(c["base"].lower(), paper.lower())
                # 描边要比稿纸深一点才看得见，但不能深成黑框
                self.assertGreaterEqual(theme._contrast_ratio(c["border"], paper), 1.20,
                                        "%s/%s 描边太浅看不见" % (tid, state))
                self.assertLess(theme._contrast_ratio(c["border"], paper), 2.3,
                                "%s/%s 描边太深成黑框" % (tid, state))

    def test_badge_filled_vs_outline_and_state_label_plain_text(self):
        for tid in theme.THEME_ORDER:
            pal = theme.build_palette(tid)
            pending = widgets.AlarmCard._palette_for(None, "pending", pal)
            expired = widgets.AlarmCard._palette_for(None, "expired", pal)
            self.assertTrue(pending["filled"], "待提醒徽章应是淡彩实心胶囊")
            self.assertFalse(expired["filled"], "已过期徽章应是描边空心胶囊")
            self.assertEqual(expired["pill_bg"].lower(), pal["editor"].lower())
            self.assertEqual(widgets._alarm_state_label("acked"), "已确认",
                             "徽章是纯文字胶囊，勾只在图标位上")


if __name__ == "__main__":
    unittest.main(verbosity=2)
