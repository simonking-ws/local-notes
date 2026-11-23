# -*- coding: utf-8 -*-
"""顶栏点击 / 拖动的回归测试。

病根：WindowFrame._drag_start 过去在 <ButtonPress-1> 时就 restore()，
导致最大化状态下只要单击顶栏空白（甚至只是按下鼠标）整窗就缩回去。
Windows 的规矩是「拖动」标题栏才还原，单击不动、双击还原。

    python tests\\test_titlebar_click.py
或：
    python -m unittest discover -s tests -v
"""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))      # 源码在 scripts/ 下

import win_frame                                                 # noqa: E402


class FakeRoot(object):
    """只提供拖动逻辑碰得到的那几个 winfo / bind 方法。"""

    def __init__(self, x=0, y=0, w=1920, h=1050):
        self.x, self.y = x, y
        self.w, self.h = w, h

    def bind(self, *args, **kwargs):
        pass

    def winfo_rootx(self):
        return self.x

    def winfo_rooty(self):
        return self.y

    def winfo_width(self):
        return self.w

    def winfo_height(self):
        return self.h

    def update_idletasks(self):
        pass


class FakeEvent(object):
    def __init__(self, x_root, y_root):
        self.x_root = x_root
        self.y_root = y_root


def make_frame(maximized=True, root=None):
    root = root or FakeRoot()
    frame = win_frame.WindowFrame(root)
    frame.enabled = True
    frame.is_maximized = mock.Mock(return_value=maximized)
    frame.restore = mock.Mock()
    frame.toggle_max = mock.Mock()
    frame._move_to = mock.Mock()
    return frame, root


class TitlebarDragTest(unittest.TestCase):
    def test_single_click_does_not_restore(self):
        """最大化时单击顶栏空白：不还原、不挪窗。"""
        frame, _root = make_frame(True)
        frame._drag_start(FakeEvent(500, 20))
        frame._drag_end(FakeEvent(500, 20))
        frame.restore.assert_not_called()
        frame._move_to.assert_not_called()

    def test_click_with_tiny_jitter_does_not_restore(self):
        """单击常带 1~2px 手抖，低于拖拽阈值也不能还原。"""
        frame, _root = make_frame(True)
        frame._drag_start(FakeEvent(500, 20))
        frame._drag_move(FakeEvent(502, 21))
        frame._drag_end(FakeEvent(502, 21))
        frame.restore.assert_not_called()
        frame._move_to.assert_not_called()

    def test_drag_restores_and_follows_mouse(self):
        """最大化时真正拖动：还原一次，窗口抓在鼠标底下跟着走。"""
        frame, root = make_frame(True)

        def do_restore():
            root.w = 1080                      # 还原后的客户区宽度
            frame._maxed = False

        frame.restore.side_effect = do_restore
        frame._drag_start(FakeEvent(500, 20))  # 按下点距窗口左缘 500、顶缘 20
        frame._drag_move(FakeEvent(560, 25))   # 移动 60px，越过阈值
        frame.restore.assert_called_once_with()
        frame._move_to.assert_called_once()
        x, y = frame._move_to.call_args[0]
        # 500/1920 ≈ 0.26 → 560 - 0.26*1080 ≈ 279；y = 25 - 20 = 5
        self.assertAlmostEqual(x, 560 - (500.0 / 1920) * 1080, delta=1)
        self.assertEqual(y, 5)

        # 后续 motion 只跟手，不再重复还原
        frame._drag_move(FakeEvent(580, 30))
        frame.restore.assert_called_once_with()

    def test_drag_when_normal_just_moves(self):
        """未最大化时拖动：正常挪窗，绝不调 restore。"""
        frame, _root = make_frame(False)
        frame._drag_start(FakeEvent(500, 20))
        frame._drag_move(FakeEvent(560, 25))
        frame.restore.assert_not_called()
        frame._move_to.assert_called_once_with(60, 5)

    def test_double_click_still_toggles(self):
        """双击顶栏空白仍然切换最大化（还原），且不会因按下就先还原。"""
        frame, _root = make_frame(True)
        frame._drag_start(FakeEvent(500, 20))
        self.assertEqual(frame._drag_toggle(FakeEvent(500, 20)), "break")
        frame.toggle_max.assert_called_once_with()
        frame.restore.assert_not_called()

    def test_double_click_after_drag_restore_does_not_toggle_back(self):
        """拖动已经还原过时，随后的双击事件不许立刻又最大化回去。"""
        frame, root = make_frame(True)
        frame.restore.side_effect = lambda: setattr(root, "w", 1080)
        frame._drag_start(FakeEvent(500, 20))
        frame._drag_move(FakeEvent(560, 25))
        frame._drag_toggle(FakeEvent(560, 25))
        frame.toggle_max.assert_not_called()


if __name__ == "__main__":
    unittest.main()
