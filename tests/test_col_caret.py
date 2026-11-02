# -*- coding: utf-8 -*-
"""列模式「每行一根光标」的回归测试（真实 Tk Text + UIMixin 方法桩）。

两轮用户反馈各锁一组不变式：

**第一轮「光标有时候会丢」**：旧实现在 `_col_caret_show_inner` 里只调
`_col_caret_blink(on=True)`，从没立刻把条刷成亮色 —— 新建的 Frame 是系统按钮灰，
拖动时每帧重摆又每帧重排 600ms 定时器，tick 永远轮不到，整段拖动都是灰线。

**第二轮「高度不对 / 选中后不闪，按退格才闪」**：
  * 条高取 bbox 的「字框」，比整行行盒矮 8~16px → 改成 `dlineinfo` 的行盒；
  * 旧实现**每次重绘**都 cancel + 重排 600ms 定时器，只要有一条路径以短间隔
    反复重绘，tick 就永远轮不到 → 光标一直常亮；按退格走 `_col_delete →
    _col_paint` 恰好把那条链断开了，才开始闪。现在重绘只 `ensure` 不重启。

灭相位也改成 `place_forget` 真隐藏：条有了整行高，跨到 spacing 区域时涂
`pal["sel"]` 会留一条 2px 残影。

运行：
    python -m unittest tests.test_col_caret -v
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import tkinter as tk                                            # noqa: E402
from app_ui import UIMixin                                      # noqa: E402


class _Host(object):
    """只实现 _col_caret_* 会碰到的属性与协作方法（照 test_sample_click 的路子）。

    `_after` 刻意不真排 Tk 定时器：本套测试要自己控制 tick 的时机。
    """

    _col_active = UIMixin._col_active
    _col_caret_show = UIMixin._col_caret_show
    _col_caret_show_inner = UIMixin._col_caret_show_inner
    _col_caret_origin = UIMixin._col_caret_origin
    _col_caret_bar = UIMixin._col_caret_bar
    _col_caret_char_w = UIMixin._col_caret_char_w
    _col_caret_paint = UIMixin._col_caret_paint
    _col_caret_cancel = UIMixin._col_caret_cancel
    _col_caret_ensure = UIMixin._col_caret_ensure
    _col_caret_start_blink = UIMixin._col_caret_start_blink
    _col_caret_tick = UIMixin._col_caret_tick
    _col_caret_recycle = UIMixin._col_caret_recycle
    _col_caret_hide_native = UIMixin._col_caret_hide_native
    _col_caret_restore_insert = UIMixin._col_caret_restore_insert
    _col_caret_hide = UIMixin._col_caret_hide

    def __init__(self, root, text):
        self.root = root
        self.text = text
        self.pal = {"accent": "#3c8c4e", "sel": "#c9decd",
                    "editor": "#ffffff"}
        self._col = None
        self._colsel = None                 # 非 None = 正在 Alt 拖动
        self._col_caret_used = []
        self._col_caret_pool = []
        self._col_caret_on = True
        self._col_caret_job = None
        self._col_caret_in_show = False
        self._col_caret_busy = False
        self._col_caret_off = None
        self._col_caret_cw = None
        self._col_native_iw = None
        self._jobs = []

    def _after(self, ms, fn):
        self._jobs.append(ms)
        return "job#%d" % len(self._jobs)

    def _cancel_after(self, job):
        return None

    def _col_caret_forward(self, seq, event):       # 测试不派发鼠标事件
        return "break"


class ColCaretTest(unittest.TestCase):

    def setUp(self):
        self.root = tk.Tk()
        # withdraw 的窗口要先 update 跑完几何协商，否则 bbox 会拿到 1x1 的假数据
        self.root.geometry("400x300+2000+2000")
        self.root.withdraw()
        self.root.update()
        self.text = tk.Text(self.root, wrap="word", width=40, height=10,
                            padx=25, pady=12, bd=0, highlightthickness=0)
        self.text.pack(fill=tk.BOTH, expand=True)
        self.text.insert("1.0", "第一行文字\n第二行文字\n第三行文字\n"
                                "第四行文字\n第五行文字")
        # 真实 App 的正文行带 spacing1/3：行盒比字框高，两者的差值正是本轮要锁的
        self.text.tag_configure("body", spacing1=2, spacing2=4, spacing3=2)
        self.text.tag_add("body", "1.0", "end")
        self.root.update()
        self.host = _Host(self.root, self.text)
        self.host._col = {"rows": [1, 2, 3], "c0": 1, "c1": 3}

    def tearDown(self):
        try:
            self.host._col_caret_hide()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def _colors(self):
        return [str(b.cget("bg")) for b in self.host._col_caret_used]

    def _visible(self):
        return [bool(b.place_info()) for b in self.host._col_caret_used]

    # ---------- 第一轮：立刻上色 ----------

    def test_first_show_is_immediately_bright(self):
        """旧实现：首次 show 的条全是 SystemButtonFace，要等 600ms tick 才亮。"""
        self.host._col_caret_show()
        self.root.update()
        self.assertEqual(len(self.host._col_caret_used), 3)
        self.assertEqual(self._colors(), [self.host.pal["accent"]] * 3,
                         "首次 show 必须立刻是主题亮色，不能是系统灰")
        self.assertEqual(self._visible(), [True] * 3)
        self.assertEqual(int(self.text.cget("insertwidth")), 0,
                         "列模式期间要藏掉系统那根光标")

    def test_show_paints_immediately(self):
        """锁住根因：show 必须自己上色，不能把上色推给 600ms 后的 tick。"""
        calls = []
        orig = self.host._col_caret_paint

        def counted():
            calls.append(1)
            orig()

        self.host._col_caret_paint = counted
        self.host._col_caret_show()
        self.root.update()
        self.assertTrue(calls, "_col_caret_show 没有立刻刷色，光标要靠 tick 才亮")

    def test_new_bar_is_born_bright(self):
        """paint 之外的双保险：新建的条本身就别是系统按钮灰。"""
        bar = self.host._col_caret_bar()
        try:
            self.assertEqual(str(bar.cget("bg")), self.host.pal["accent"])
        finally:
            bar.destroy()

    def test_hide_restores_native_caret(self):
        """退出列模式必须把 insertwidth 还回去，否则普通光标永久消失。"""
        self.host._col_caret_show()
        self.assertEqual(int(self.text.cget("insertwidth")), 0)
        self.host._col_caret_hide()
        self.assertNotEqual(int(self.text.cget("insertwidth")), 0)
        self.assertEqual(self.host._col_caret_used, [])
        self.assertIsNone(self.host._col_caret_job, "退出后不该留着闪烁定时器")

    # ---------- 本轮一：高度 = 行高 ----------

    def test_bar_height_is_the_whole_line_box(self):
        """bbox 只给字框；光标必须按 dlineinfo 的整行行盒画。"""
        self.host._col_caret_show()
        self.root.update()
        self.assertEqual(len(self.host._col_caret_used), 3)
        _, dy = self.host._col_caret_origin()   # place 坐标系原点偏移（见方法注释）
        for ln, bar in zip((1, 2, 3), self.host._col_caret_used):
            bb = self.text.bbox("%d.0" % ln)
            di = self.text.dlineinfo("%d.0" % ln)
            self.assertIsNotNone(di)
            self.assertGreater(di[3], bb[3],
                               "本测试必须让行盒与字框真的不同，否则测不出退化")
            self.assertEqual(bar._caret_geom[3], di[3],
                             "第 %d 行的光标没取行高" % ln)
            self.assertEqual(bar._caret_geom[1], di[1] - dy,
                             "第 %d 行的光标没从行盒顶起" % ln)
            self.assertEqual(int(bar.place_info()["height"]), di[3])

    def test_off_phase_really_hides_the_bars(self):
        """灭相位要 place_forget 真藏，不能涂 sel 色留一条 2px 残影。"""
        self.host._col_caret_show()
        self.root.update()
        self.assertEqual(self._visible(), [True] * 3)

        self.host._col_caret_on = False
        self.host._col_caret_paint()
        self.root.update()
        self.assertEqual(self._visible(), [False] * 3, "灭相位必须整条藏掉")

        self.host._col_caret_on = True
        self.host._col_caret_paint()
        self.root.update()
        self.assertEqual(self._visible(), [True] * 3, "亮相位要原样摆回来")
        self.assertEqual(self._colors(), [self.host.pal["accent"]] * 3)

    # ---------- 本轮二：闪烁不被重绘打断 ----------

    def test_redraw_never_restarts_the_blink_timer(self):
        """旧实现每次 show 都重排 600ms；连续重绘会让光标永远常亮。"""
        self.host._col_caret_show()
        job = self.host._col_caret_job
        self.assertIsNotNone(job, "选中后必须立刻有一条闪烁定时器")

        self.host._col_caret_on = False
        self.host._col_caret_paint()
        for c1 in (4, 5, 6):
            self.host._col = {"rows": [1, 2, 3], "c0": 1, "c1": c1}
            self.host._col_caret_show()
            self.root.update()
            self.assertEqual(self.host._col_caret_job, job,
                             "第 %d 次重绘把定时器重启了" % c1)
            self.assertFalse(self.host._col_caret_on,
                             "静止重绘不许把「灭」相位掰回亮")
        self.assertEqual(self._visible(), [False] * 3,
                         "灭相位重绘后不许把条又摆出来")

    def test_tick_toggles_phase_and_keeps_timer_alive(self):
        self.host._col_caret_show()
        self.assertTrue(self.host._col_caret_on)
        self.host._col_caret_tick()
        self.assertFalse(self.host._col_caret_on)
        self.assertEqual(self._visible(), [False] * 3)
        self.assertIsNotNone(self.host._col_caret_job)
        self.host._col_caret_tick()
        self.assertTrue(self.host._col_caret_on)
        self.assertEqual(self._visible(), [True] * 3)

    def test_drag_stays_bright_but_timer_keeps_running(self):
        """拖动中常亮；但定时器不能停 —— 窗口外松手时就没人重启它了。"""
        self.host._colsel = {"moved": True}
        for c1 in range(3, 10):
            self.host._col = {"rows": [1, 2, 3], "c0": 1, "c1": c1}
            self.host._col_caret_show()
            self.root.update()
            self.assertEqual(self._colors(), [self.host.pal["accent"]] * 3,
                             "第 %d 帧拖动时光标条丢了亮色" % c1)
        job = self.host._col_caret_job
        self.assertIsNotNone(job, "拖动中定时器必须还活着")
        self.host._col_caret_tick()
        self.assertTrue(self.host._col_caret_on, "拖动中 tick 不许翻到灭")
        self.assertIsNotNone(self.host._col_caret_job)

    def test_release_restarts_a_full_bright_phase(self):
        """松手要显式起搏：静止之后通常不会再有第二次 show。"""
        self.host._colsel = {"moved": True}
        self.host._col_caret_show()
        self.host._col_caret_on = False
        self.host._col_caret_paint()
        old = self.host._col_caret_job

        self.host._colsel = None
        self.host._col_caret_start_blink()
        self.root.update()
        self.assertTrue(self.host._col_caret_on)
        self.assertNotEqual(self.host._col_caret_job, old)
        self.assertIsNotNone(self.host._col_caret_job)
        self.assertEqual(self._visible(), [True] * 3)
        self.assertEqual(self._colors(), [self.host.pal["accent"]] * 3)


if __name__ == "__main__":
    unittest.main()
