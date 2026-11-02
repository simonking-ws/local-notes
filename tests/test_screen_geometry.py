# -*- coding: utf-8 -*-
"""浮层摆位的测试：多屏下右键菜单 / 气泡 / 候选层不许跨屏。

为什么值得测：`win_frame.clamp_pos` 只做一件事 —— 把浮层的左上角夹进某块
显示器的工作区。写错了不会抛异常，只会让浮层出现在另一块屏上（副屏坐标是
负数，下限一旦夹到 0 就直接跨屏）。开发机是双屏，CI 上不一定有；所以把算式
钉成纯函数测试，不依赖真实显示器。

    python tests\\test_screen_geometry.py
或：
    python -m unittest discover -s tests -v
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))      # 源码在 scripts/ 下

import win_frame                                                 # noqa: E402

# 本机实测的两块屏（副屏在主屏左侧，所以坐标为负）
MAIN = (0, 0, 1920, 1050)          # 主屏 1920x1080，扣掉 30px 任务栏
SUB = (-1366, 0, 1366, 738)        # 副屏 1366x768，扣掉 30px 任务栏


class ClampPosTest(unittest.TestCase):
    def test_main_screen_untouched(self):
        """在工作区内部：位置原样返回。"""
        self.assertEqual(win_frame.clamp_pos(MAIN, 400, 300, 200, 150),
                         (400, 300))

    def test_sub_screen_middle_stays_negative(self):
        """副屏中部（x 为负）不能被拉到 0 —— 那就是「跨屏」的全部病根。"""
        self.assertEqual(win_frame.clamp_pos(SUB, -1000, 300, 200, 150),
                         (-1000, 300))

    def test_sub_screen_over_left_edge(self):
        """越出副屏左缘：贴在副屏左边，不是主屏左边。"""
        self.assertEqual(win_frame.clamp_pos(SUB, -1400, 300, 200, 150)[0], -1366)

    def test_sub_screen_over_right_edge(self):
        """越出副屏右缘：贴着副屏右边内收（副屏右缘是 0）。"""
        self.assertEqual(win_frame.clamp_pos(SUB, -50, 300, 200, 150)[0],
                         -200)

    def test_taskbar_zone_excluded(self):
        """底部按工作区算：菜单不压任务栏（主屏 1050、副屏 738 就是工作区底）。"""
        self.assertEqual(win_frame.clamp_pos(MAIN, 300, 1040, 200, 150)[1],
                         1050 - 150)
        self.assertEqual(win_frame.clamp_pos(SUB, -900, 700, 200, 150)[1],
                         738 - 150)

    def test_pad_keeps_margin(self):
        """pad 只作用在右/下边，左上不受影响。"""
        self.assertEqual(win_frame.clamp_pos(MAIN, 1900, 1040, 200, 150, pad=8),
                         (1920 - 200 - 8, 1050 - 150 - 8))
        self.assertEqual(win_frame.clamp_pos(MAIN, 20, 20, 200, 150, pad=8),
                         (20, 20))

    def test_negative_top_screen(self):
        """副屏在主屏上方时工作区 y 为负：同样按它夹，别退回 0。"""
        upper = (-100, -900, 1200, 900)          # y 从 -900 到 0
        self.assertEqual(win_frame.clamp_pos(upper, 200, -1200, 200, 150),
                         (200, -900))            # 越上边 → 贴副屏顶
        self.assertEqual(win_frame.clamp_pos(upper, 200, -880, 200, 150),
                         (200, -880))            # 屏内 → 原样

    def test_float_bigger_than_screen(self):
        """浮层比工作区还大：收在左上角，宁可右边溢出也不跑到另一块屏。"""
        x, y = win_frame.clamp_pos(SUB, -500, 100, 2000, 1000)
        self.assertEqual((x, y), (-1366, 0))

    def test_int_coordinates(self):
        """坐标一律取整：Tk 的 geometry 不接受小数，传 float 会拼出坏串。"""
        x, y = win_frame.clamp_pos(MAIN, 400.7, 300.2, 200.4, 150.9)
        self.assertEqual((x, y), (400, 300))
        self.assertIsInstance(x, int)
        self.assertIsInstance(y, int)

    def test_screen_edges_are_inclusive(self):
        """刚好贴住边缘（浮层右下角 = 工作区右下角）不算越界。"""
        x, y = win_frame.clamp_pos(SUB, -200, 588, 200, 150)
        self.assertEqual((x, y), (-200, 588))
        self.assertEqual(x + 200, 0)          # 副屏右缘
        self.assertEqual(y + 150, 738)        # 副屏工作区底


class FitDialogTest(unittest.TestCase):
    """对话框要按**整窗**（客户区 + 系统标题栏）夹进工作区。

    这张表钉的是「副屏 738 高、设置窗内容 735 高」那个 bug：客户区尺寸不缺，
    缺的是标题栏那 31px —— 老代码只夹客户区，窗口就从下沿漏出去一条，
    「确定 / 取消」跟着看不见。
    """

    # 实测：Win10/11 100% 缩放下标题栏 31px、左右边框各 8px
    DX, DY = 8, 31

    def test_short_screen_reports_shrinkable_height(self):
        """副屏 738 高：可用客户区高 = 738 − 标题栏 31 − 下边框 8 − 留白 6 = 693。"""
        _x, _y, avail = win_frame.fit_dialog(
            SUB, -963, 1, 560, 735, self.DX, self.DY, pad=6)
        self.assertEqual(avail, 693)
        self.assertLess(avail, 735)          # 735 的内容确实装不下 → 调用方该收滚区

    def test_short_screen_fits_after_shrink(self):
        """按 avail 压完再摆一次：整窗落在屏内，且贴着底边留 6px。"""
        _x, _y, avail = win_frame.fit_dialog(
            SUB, -963, 1, 560, 735, self.DX, self.DY, pad=6)
        _x2, y2, _a = win_frame.fit_dialog(
            SUB, -963, 1, 560, avail, self.DX, self.DY, pad=6)
        bottom = y2 + avail + self.DY + self.DX
        self.assertEqual(bottom, 738 - 6)
        self.assertEqual(y2, 0)              # 贴顶摆（屏就这么高）

    def test_main_screen_keeps_position(self):
        """装得下的屏：y 换成「窗口矩形」后，客户区顶端仍落在 1/3 处（105）。"""
        _x, y, avail = win_frame.fit_dialog(
            MAIN, 400, 105, 560, 735, self.DX, self.DY, pad=6)
        self.assertEqual(avail, 1050 - 31 - 8 - 6)
        self.assertEqual(y, 105 - self.DY)
        self.assertEqual(y + self.DY, 105)

    def test_returns_negative_x_on_sub_screen(self):
        """副屏在主屏左侧：x 必须是负的（等于客户区减去边框），不能被拽回 0。"""
        x, _y, _a = win_frame.fit_dialog(
            SUB, -963, 100, 560, 500, self.DX, self.DY, pad=6)
        self.assertEqual(x, -963 - self.DX)
        self.assertLess(x, 0)

    def test_tiny_screen_still_on_screen_horizontally(self):
        """极矮的屏：竖向放不下（只能靠调用方压），但左右仍要夹在屏内。"""
        x, _y, avail = win_frame.fit_dialog(
            (0, 0, 800, 400), 0, 0, 560, 735, self.DX, self.DY, pad=6)
        self.assertEqual(avail, 400 - 31 - 8 - 6)
        self.assertGreaterEqual(x, 0)
        self.assertLessEqual(x + 560 + self.DX * 2, 800)

    def test_default_padding_is_zero(self):
        """不传 dx/dy（无边框环境，如 Linux）：退化成原来的客户区夹取。"""
        x, y, avail = win_frame.fit_dialog(MAIN, 400, 300, 200, 150)
        self.assertEqual((x, y, avail), (400, 300, 1050))


class PanelCanvasHeightTest(unittest.TestCase):
    """全局设置窗与数据管理窗等高：反解「可滚内容区」的那条算式。

    实测（100% 缩放）：数据管理窗自然高 504，设置窗固定开销 73（按钮行 +
    内边距），内容 662。所以内容区该是 431，整窗 431 + 73 = 504 —— 两张窗
    连位置都完全重合。之前是按「屏幕给多少用多少」算，装得下就是 735 的长窗。
    """

    TARGET, FIXED, MIN_H = 504, 73, 120

    def test_main_screen_matches_neighbour(self):
        h = win_frame.panel_canvas_height(self.TARGET, 977, self.FIXED, self.MIN_H)
        self.assertEqual(h, 431)
        self.assertEqual(h + self.FIXED, self.TARGET)

    def test_short_screen_capped_by_available_height(self):
        """矮屏：宁可比邻居矮，也不能让整窗超出屏幕可用高。"""
        h = win_frame.panel_canvas_height(self.TARGET, 420, self.FIXED, self.MIN_H)
        self.assertEqual(h, 347)
        self.assertEqual(h + self.FIXED, 420)

    def test_tiny_screen_keeps_canvas_usable(self):
        """屏幕连下限都容不下时保住可用内容区，别压成一条缝。"""
        h = win_frame.panel_canvas_height(self.TARGET, 100, self.FIXED, self.MIN_H)
        self.assertEqual(h, self.MIN_H)
        self.assertLess(h + self.FIXED, self.TARGET)

    def test_no_fixed_cost_uses_full_target(self):
        """没有底部按钮行时，内容区就是整窗高度。"""
        self.assertEqual(win_frame.panel_canvas_height(self.TARGET, 977, 0, self.MIN_H),
                         self.TARGET)

    def test_negative_fixed_cost_is_ignored(self):
        """量出来的 fixed 若为负（还没布局完），按 0 处理，不能凭空把窗口加高。"""
        self.assertEqual(win_frame.panel_canvas_height(self.TARGET, 977, -20, self.MIN_H),
                         self.TARGET)

    def test_floats_are_truncated(self):
        """Canvas 高度要整数：Tk 收到 float 会拼出坏 geometry 串。"""
        h = win_frame.panel_canvas_height(504.7, 977.9, 73.4, self.MIN_H)
        self.assertEqual(h, 431)
        self.assertIsInstance(h, int)


class DialogCenterYTest(unittest.TestCase):
    """顶栏三张信息型弹窗的垂直居中：按**整窗**算，不是只按客户区。

    旧的 1/3 摆法（`(host_h - h)//3`）本来就偏上；就算改成按客户区 1/2，
    没把标题栏 / 下边框算进整窗高度也还是差半条标题栏。这里把算式钉死。
    """

    DX, DY = 8, 31

    def test_whole_window_center_matches_host_center(self):
        host_h, h = 800, 504
        y = win_frame.dialog_center_y(host_h, h, self.DY, self.DX)
        # 传给 fit_dialog 的是「客户区 y」，返回的是窗口矩形 y
        rect_y = win_frame.fit_dialog(
            MAIN, 0, y, 560, h, self.DX, self.DY, pad=6)[1]
        rect_h = h + self.DY + self.DX
        self.assertAlmostEqual(rect_y + rect_h / 2.0, host_h / 2.0, delta=1)

    def test_old_third_rule_sits_higher(self):
        """1/3 摆法比正中高 61px —— 这就是「弹框没有垂直居中」。"""
        third = (800 - 504) // 3
        center = win_frame.dialog_center_y(800, 504, self.DY, self.DX)
        self.assertEqual(third, 98)
        self.assertEqual(center, 159)
        self.assertEqual(center - third, 61)

    def test_customer_only_center_sits_higher(self):
        """只按客户区高居中仍偏上：标题栏 / 下边框没算进整窗高度。"""
        naive = (800 - 504) // 2
        self.assertEqual(naive, 148)
        self.assertLess(naive, win_frame.dialog_center_y(800, 504, self.DY, self.DX))

    def test_no_decorations_degenerates_to_plain_center(self):
        """无边框环境（dy=dx=0）：退化成客户区正中。"""
        self.assertEqual(win_frame.dialog_center_y(800, 504), 148)

    def test_returns_int(self):
        """geometry 不接受小数：浮点尺寸也要算出整数坐标。"""
        v = win_frame.dialog_center_y(800.7, 504.2, 31.4, 8.9)
        self.assertIsInstance(v, int)


if __name__ == "__main__":
    unittest.main(verbosity=2)
