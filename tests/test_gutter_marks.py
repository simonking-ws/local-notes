# -*- coding: utf-8 -*-
"""行号栏标签（gmark）的核心不变量。

标签挂在**正文行首字符的 gmark tag** 上，不是记一串行号 —— 这是整套设计的命门：
  · 行号是等距刻度尺，插删行 / 代码块都会让它漂移，记行号必然错行；
  · 挂在字符上，增删改文字时 tag 跟着那一行走，隐藏 / 再显示行号、切笔记、
    重启程序后都还在（靠正文 serialize 一起落库）。

这些用例用「假 app」直接驱动 `_toggle_gmark` / `_gmark_lines` / `serialize`，
不建第二个 tk.Tk()（真窗口断言在 test_settings_dialog.py 里复用同一个 root）。
    python tests\\test_gutter_marks.py -v
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import types                                            # noqa: E402
import tkinter as tk                                    # noqa: E402
import app_ui                                            # noqa: E402
import app_actions                                       # noqa: E402
import store                                             # noqa: E402

GMARK = app_ui.GMARK_TAG


class _Harness(object):
    """把需要的 mixin 方法拼到一个对象上，配一块真实 Text（不需要主窗口渲染）。"""

    def __init__(self, root, content=None):
        self.root = root
        self.text = tk.Text(root)
        self._readonly = False
        self._locked_view = False
        self._gutter_job = None
        self.pal = {"accent": "#4a7", "editor": "#fff", "soft": "#888", "text": "#222"}
        self.body_size = 12
        self._gutter_font = None
        self._gutter_base_fill = {}
        self._gmark_photo = None
        self._gmark_photos = None
        self._gmark_icon_key = None
        self._gmark_blink_idx = 0
        self._gmark_blink_dir = 1
        self._gmark_blink_job = None
        self.data = {"settings": {"line_numbers": True}}
        # 真 canvas（不显示）：验证三位数行号时徽标仍画得出来、闪烁切的是图元图。
        self.gutter = tk.Canvas(root, width=45, height=60)
        self.after_calls = []
        self._after = lambda ms, fn: (self.after_calls.append((ms, fn)) or len(self.after_calls))
        self._cancel_after = lambda job: None
        self.LI_TAG_PREFIX = app_actions.ActionsMixin.LI_TAG_PREFIX
        self._locked_view = False
        # 绑 mixin 方法（不实例化整个 NotesApp，避免连数据 / 建一堆控件）。
        # dir() 取到的是函数描述符，必须 MethodType 绑到本实例，否则 self 丢失。
        for cls in (app_ui.UIMixin, app_actions.ActionsMixin):
            for name in dir(cls):
                if name.startswith("__"):
                    continue
                if hasattr(self, name):
                    continue
                fn = getattr(cls, name)
                if callable(fn):
                    setattr(self, name, types.MethodType(fn, self))
        if content:
            self._load(content)

    def _load(self, runs):
        self.text.delete("1.0", tk.END)
        for it in runs:
            if it[0] == "t":
                self.text.insert(tk.END, it[1])
                for tg in it[2] if len(it) > 2 else ():
                    self.text.tag_add(tg, "insert-1c", "insert")

    # 落库相关的桩：只记录"该存了"，不碰真实数据库
    def mark_modified(self):
        self.modified_calls = getattr(self, "modified_calls", 0) + 1

    def _gutter_schedule(self, delay=8):
        self.redraw_calls = getattr(self, "redraw_calls", 0) + 1

    def _refuse_readonly(self):
        if self._readonly:
            self.refused = True
            return True
        return False

    def _flash_readonly_hint(self):
        self.refused = True

    def is_readonly(self):
        return self._readonly


class GutterMarksTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.h = _Harness(self.root)

    def tearDown(self):
        self.h.text.destroy()
        self.h.gutter.destroy()

    def _five_lines(self):
        self.h._load([["t", "第一行\n"], ["t", "第二行\n"], ["t", "第三行\n"],
                      ["t", "第四行\n"], ["t", "第五行"]])

    def test_tag_name_stable(self):
        """tag 名固定，改它等于让所有旧笔记的标签集体消失。"""
        self.assertEqual(GMARK, "gmark")

    def test_toggle_on_off(self):
        self._five_lines()
        self.h._toggle_gmark(3)
        self.assertEqual(self.h._gmark_lines(), {3})
        self.h._toggle_gmark(3)
        self.assertEqual(self.h._gmark_lines(), set())

    def test_multiple_marks(self):
        self._five_lines()
        for ln in (1, 3, 5):
            self.h._toggle_gmark(ln)
        self.assertEqual(self.h._gmark_lines(), {1, 3, 5})

    def test_mark_follows_text_on_insert(self):
        """在标记行上方插入文字，行号从 3 变 4，标签必须跟着那一行文字走。"""
        self._five_lines()
        self.h._toggle_gmark(3)
        self.h.text.insert("1.0", "插到最前\n")
        self.assertEqual(self.h._gmark_lines(), {4})
        self.assertEqual(self.h.text.get("4.0", "4.end"), "第三行")

    def test_mark_follows_text_on_delete(self):
        """删掉标记行上方一行，标签跟着文字上移。"""
        self._five_lines()
        self.h._toggle_gmark(3)
        self.h.text.delete("1.0", "2.0")     # 删第一行
        self.assertEqual(self.h._gmark_lines(), {2})

    def test_out_of_range_is_ignored(self):
        """末尾空白区的行号（没有对应文字）不该打上标签。"""
        self._five_lines()
        self.h._toggle_gmark(99)
        self.assertEqual(self.h._gmark_lines(), set())

    def test_readonly_refuses(self):
        self._five_lines()
        self.h._readonly = True
        self.h._toggle_gmark(2)
        self.assertEqual(self.h._gmark_lines(), set())
        self.assertTrue(getattr(self.h, "refused", False))

    def test_serialize_roundtrip(self):
        """标签随正文落库：序列化 → 重新渲染，标记位置不变。"""
        self._five_lines()
        self.h._toggle_gmark(3)
        runs = self.h.serialize()
        # 标记 tag 必须出现在序列化结果里（没被 _is_ephemeral_tag 过滤掉）。
        # 只锚行首**一个字符**（"第"），这样插删行 / 改动行尾文字都不影响跟随。
        # 每行的 tag 名是独立的（gmark_N），所以按前缀认，不能只比 "gmark"。
        def marked(ch):
            return any(str(t) == GMARK or str(t).startswith("gmark_")
                       for t in (ch[2] if len(ch) > 2 else []))
        tagged = [c for c in runs if c[0] == "t" and marked(c)]
        self.assertEqual([c[1] for c in tagged], ["第"])
        # 重新装回一块干净的 Text
        h2 = _Harness(self.root)
        h2._load(runs)
        self.assertEqual(h2._gmark_lines(), {3})
        h2.text.destroy()

    def test_clear_removes_all(self):
        self._five_lines()
        for ln in (1, 3, 5):
            self.h._toggle_gmark(ln)
        self.h._gmark_clear()
        self.assertEqual(self.h._gmark_lines(), set())

    def test_empty_line_can_be_marked(self):
        """空行也能打标签（锚在那个换行符上）。"""
        self.h._load([["t", "甲\n"], ["t", "\n"], ["t", "乙"]])
        self.h._toggle_gmark(2)
        self.assertEqual(self.h._gmark_lines(), {2})

    # ---------- 连续空行：一行一个 tag，绝不合并 ----------
    # 空行只有一个换行符，标记区间恰好是 [N.0, N+1.0)；若多行共用一个 tag，
    # Tk 会把首尾相接的区间合并成一段 —— 点第 3 个空行会牵连整片空白。

    def _blank_block(self):
        """甲 / 空 / 空 / 空 / 乙 / 空 / 丙 共 7 行。"""
        self.h._load([["t", "甲\n"], ["t", "\n"], ["t", "\n"], ["t", "\n"],
                      ["t", "乙\n"], ["t", "\n"], ["t", "丙"]])

    def test_adjacent_blank_lines_are_independent(self):
        self._blank_block()
        for ln in (2, 3, 4):
            self.h._toggle_gmark(ln)
        self.assertEqual(self.h._gmark_lines(), {2, 3, 4})
        # 每行必须是**不同的 tag 名**，共用就会在 Tk 里被合并
        names = []
        for ln in (2, 3, 4):
            names.extend(t for t in self.h.text.tag_names("%d.0" % ln)
                         if t == GMARK or t.startswith("gmark_"))
        self.assertEqual(len(names), 3, "每行应各自持有一个标记 tag")
        self.assertEqual(len(set(names)), 3, "标记 tag 名不能复用")

    def test_untoggle_one_blank_line_keeps_the_rest(self):
        """取消中间那个空行，前后两个标记都还在（旧实现会整段跟着动）。"""
        self._blank_block()
        for ln in (2, 3, 4):
            self.h._toggle_gmark(ln)
        self.h._toggle_gmark(3)
        self.assertEqual(self.h._gmark_lines(), {2, 4})
        self.h._toggle_gmark(2)
        self.assertEqual(self.h._gmark_lines(), {4})

    def test_repeated_toggle_is_idempotent(self):
        """连点同一空白行两次 = 取消，不多出一个 tag。"""
        self._blank_block()
        self.h._toggle_gmark(3)
        self.h._toggle_gmark(3)
        self.assertEqual(self.h._gmark_lines(), set())

    def test_blank_marks_survive_serialize(self):
        """连续空行的标记落库再装回来，仍是三行独立标记。"""
        self._blank_block()
        for ln in (2, 3, 4):
            self.h._toggle_gmark(ln)
        runs = self.h.serialize()
        h2 = _Harness(self.root)
        h2._load(runs)
        self.assertEqual(h2._gmark_lines(), {2, 3, 4})
        h2.text.destroy()

    def test_legacy_shared_tag_is_normalized(self):
        """旧库把所有标记塞在一个 gmark tag 里（连续空行已被合并成一段）——
        载入后要拆成一行一个 tag，否则点一行会牵连整段。"""
        self._blank_block()
        self.h.text.tag_add(GMARK, "2.0", "5.0")     # 模拟旧数据：三行合并
        self.assertEqual(self.h._gmark_lines(), {2}, "合并态只能看到段首")
        self.h._gmark_normalize()
        self.assertEqual(self.h._gmark_lines(), {2, 3, 4}, "归一后三行都在")
        names = []
        for ln in (2, 3, 4):
            names.extend(t for t in self.h.text.tag_names("%d.0" % ln)
                         if t == GMARK or t.startswith("gmark_"))
        self.assertEqual(len(set(names)), 3, "归一后每行一个独立 tag")
        # 取消其中一行不应带走另外两行
        self.h._toggle_gmark(3)
        self.assertEqual(self.h._gmark_lines(), {2, 4})

    def test_normalize_keeps_text_line_marks(self):
        """归一不能碰有字行的标记（它们本来就不相邻、不会合并）。"""
        self._five_lines()
        for ln in (1, 3, 5):
            self.h._toggle_gmark(ln)
        self.h._gmark_normalize()
        self.assertEqual(self.h._gmark_lines(), {1, 3, 5})

    def test_clear_removes_every_tag(self):
        self._blank_block()
        for ln in (2, 3, 5):
            self.h._toggle_gmark(ln)
        self.h._gmark_clear()
        self.assertEqual(self.h._gmark_lines(), set())
        left = [t for t in self.h.text.tag_names() if t.startswith("gmark")]
        self.assertEqual(left, [], "清空后不该留下空 tag 名")

    def test_icon_is_fill_sequence(self):
        """图标是「空心→实心」的多帧序列（>2 帧），缓存按主题色失效。"""
        frames = self.h._gmark_icon(self.h.pal)
        self.assertGreater(len(frames), 2)
        self.assertTrue(all(a is not b for i, a in enumerate(frames)
                            for b in frames[i + 1:]), "每帧应各不相同")
        self.assertIs(self.h._gmark_icon(self.h.pal), frames)   # 同主题命中缓存
        old = frames
        self.h.pal = dict(self.h.pal, accent="#ff0000")
        self.assertIsNot(self.h._gmark_icon(self.h.pal), old)   # 换主题重画

    def test_mark_drawn_right_of_digits(self):
        """徽标画在数字右缘的右侧（标签槽），与位数无关；垂直与行号格居中。"""
        g = self.h.gutter
        g.delete("all")
        w = 64                      # 加宽后的栏（4 位预算 + 标签槽）
        digit_right = 40            # 数字右缘 x
        item = g.create_text(digit_right, 15, text="105", anchor="se", tags=("gnum",))
        self.h._gutter_mark(g, digit_right, 0, 20, item, self.h.pal, 105)
        marks = g.find_withtag("gmark")
        self.assertEqual(len(marks), 1, "3 位数时徽标必须画出来")
        x, y = g.coords(marks[0])
        icon = self.h._gmark_size()
        self.assertLessEqual(icon, 13, "标签调小后不应超过 13px")
        self.assertGreaterEqual(x, digit_right, "徽标须在数字右侧，不压数字")
        self.assertLess(x + icon, w)   # 落在加宽后的栏内
        # 垂直：行号格中心(y=10) 再下移 GMARK_Y_NUDGE（视觉居中）
        expect_cy = 10 + app_ui.dpi.px(app_ui.GMARK_Y_NUDGE)
        self.assertAlmostEqual(y + icon / 2.0, expect_cy, delta=1)
        self.assertTrue(g.find_withtag("gnum"), "数字需保留")

    def test_blink_advances_fill_and_pingpongs(self):
        """闪烁逐帧推进填充、到头反向，并排下一拍（110ms）。"""
        g = self.h.gutter
        g.delete("all")
        photo_item = g.create_image(20, 5, tags=("gmark",))
        frames = self.h._gmark_icon(self.h.pal)
        n = len(frames)
        before = g.itemcget(photo_item, "image")
        self.h._gmark_blink_tick()
        self.assertNotEqual(g.itemcget(photo_item, "image"), before, "应推进到下一帧")
        self.assertEqual(self.h._gmark_blink_idx, 1)
        self.assertTrue(self.h.after_calls)
        self.assertEqual(self.h.after_calls[-1][0], 110)
        # 一路推进到末帧，方向应翻转为 -1（收空）。
        self.h._gmark_blink_idx = n - 2
        self.h._gmark_blink_dir = 1
        self.h._gmark_blink_tick()
        self.assertEqual(self.h._gmark_blink_idx, n - 1)
        self.assertEqual(self.h._gmark_blink_dir, -1)


    # ---------- 点击分区：实号 vs 正文下方的虚号 ----------
    def _hits(self, rows):
        self.h._gutter_hit = [tuple(r) for r in rows]
        self.h._gutter_digit_right = 20
        self.h._folds = []

    def _click(self, y, x=20):
        return self.h._on_gutter_click(
            types.SimpleNamespace(y=y, x=x, x_root=0, y_root=0))

    def test_padding_slot_does_not_mark(self):
        """正文下方虚号格不会给**末行**打行标记（它打自己的 gpad_N，另测）。

        以前直接 toggle 末行 —— 标记只挂了一个，整片虚号却同时亮起来，看起来
        就是「点空白行号，后面其他行号都显示标签」（用户报的现象）。
        """
        self._five_lines()
        self._hits([(0, 30, 5, "i5", False), (30, 60, 5, "i6", True),
                    (60, 90, 5, "i7", True)])
        self.assertEqual(self._click(45), "break")
        self.assertEqual(self.h._gmark_lines(), set())

    def test_real_slot_still_marks(self):
        """实号那格行为不变：点一下给这一行挂标签。"""
        self._five_lines()
        self._hits([(0, 30, 2, "i2", False), (30, 60, 5, "i5", True)])
        self.assertEqual(self._click(10), "break")
        self.assertEqual(self.h._gmark_lines(), {2})

    def test_padding_click_does_not_toggle_existing_mark(self):
        """末行已有标记时点虚号也不能把它取消（那儿不是这一行的地盘）。"""
        self._five_lines()
        self.h._toggle_gmark(5)
        self._hits([(0, 30, 5, "i5", False), (30, 60, 5, "i6", True)])
        self._click(45)
        self.assertEqual(self.h._gmark_lines(), {5})


class GutterPadMarksTest(unittest.TestCase):
    """正文下方虚号的独立标记（gpad_N）。

    虚号在正文里没有对应的行，反查 @0 一律得到末行。以前直接 toggle 末行，
    结果是「点一个虚号，整片虚号一起亮」。现在一个虚号一个 gpad_N tag，挂在
    正文最后一个字符上 —— 与行标记互不干扰，也跟着正文一起落库。
    """

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.h = _Harness(self.root)
        self.h._load([["t", "标题\n"], ["t", "正文"]])

    def tearDown(self):
        self.h.text.destroy()
        self.h.gutter.destroy()

    def test_independent_marks(self):
        """点 4、6 各挂各的；再点 4 只取消 4，不牵连 6。"""
        self.h._toggle_gpad(4)
        self.h._toggle_gpad(6)
        self.assertEqual(self.h._gpad_marks(), {4, 6})
        self.h._toggle_gpad(4)
        self.assertEqual(self.h._gpad_marks(), {6})

    def test_pad_marks_do_not_touch_line_marks(self):
        self.h._toggle_gmark(1)
        self.h._toggle_gpad(6)
        self.assertEqual(self.h._gmark_lines(), {1})
        self.assertEqual(self.h._gpad_marks(), {6})

    def test_serialize_keeps_pad_marks(self):
        self.h._toggle_gpad(7)
        runs = self.h.serialize()
        tags = {t for c in runs if len(c) > 2 and isinstance(c[2], list)
                for t in c[2]}
        self.assertIn("gpad_7", tags, "虚号标记要随正文落库，重启后还在")

    def test_roundtrip_restores_pad_marks(self):
        self.h._toggle_gpad(7)
        runs = self.h.serialize()
        self.h._load(runs)
        self.assertEqual(self.h._gpad_marks(), {7}, "存库再载入后标记还在")

    def test_clear_removes_pad_marks(self):
        self.h._toggle_gpad(4)
        self.h._gmark_clear()
        self.assertEqual(self.h._gpad_marks(), set())

    def test_stale_empty_tag_name_is_unmarked(self):
        """换笔记后旧空 tag 名可能还注册着：按区间判有无，别误走进取消分支。"""
        self.h.text.tag_add("gpad_7", "end-1c", "end")
        self.h.text.tag_remove("gpad_7", "1.0", "end")
        self.assertIn("gpad_7", self.h.text.tag_names())
        self.assertEqual(self.h._gpad_marks(), set())
        self.h._toggle_gpad(7)
        self.assertEqual(self.h._gpad_marks(), {7})

    def test_readonly_blocks_pad_marks(self):
        self.h._readonly = True
        self.h._toggle_gpad(4)
        self.assertEqual(self.h._gpad_marks(), set())
        self.assertTrue(getattr(self.h, "refused", False))

    def test_click_pad_slot_marks_its_own_number(self):
        """真实点击路径：虚号格读自己的显示号，不打末行的行标记。"""
        item = self.h.gutter.create_text(20, 10, text="9")
        self.h._gutter_hit = [(0, 30, 2, item, True)]
        self.h._gutter_digit_right = 20
        self.h._folds = []
        self.h._on_gutter_click(types.SimpleNamespace(
            y=15, x=12, x_root=0, y_root=0))
        self.assertEqual(self.h._gpad_marks(), {9})
        self.assertEqual(self.h._gmark_lines(), set())


if __name__ == "__main__":
    unittest.main(verbosity=2)
