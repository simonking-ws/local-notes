# -*- coding: utf-8 -*-
"""DPI 层的测试：缩放比怎么定、像素怎么取整。

为什么值得测：`px()` 少乘一次、或者四舍五入退化成「截断」，界面上不会报错 ——
只会看到 1px 的描边糊成两条灰线、或者 125% 下顶栏比图标矮 1px。这类问题
要拿两台不同缩放比的机器并排看才发现，所以把「取整规则」钉在测试里。

不建 GUI：`dpi` 除了 `scale_canvas()` / `apply_tk_scaling()` 都是纯函数。

    python tests\\test_dpi.py
或：
    python -m unittest discover -s tests -v
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))      # 源码在 scripts/ 下

import dpi                                                       # noqa: E402


class TestDpi(unittest.TestCase):
    def setUp(self):
        self._saved = dpi.SCALE

    def tearDown(self):
        dpi.SCALE = self._saved

    # ---------- px / fx ----------

    def test_px_is_identity_at_100(self):
        dpi.SCALE = 1.0
        for n in (1, 2, 16, 30, 216, 1080):
            self.assertEqual(dpi.px(n), n)

    def test_px_scales_each_common_ratio(self):
        cases = {
            1.25: [(16, 20), (30, 38), (216, 270), (300, 375), (5, 6)],
            1.5:  [(16, 24), (30, 45), (216, 324), (300, 450), (1, 2)],
            2.0:  [(16, 32), (30, 60), (216, 432), (300, 600), (17, 34)],
        }
        for scale, pairs in cases.items():
            dpi.SCALE = scale
            for src, want in pairs:
                self.assertEqual(dpi.px(src), want,
                                 "px(%d) @ %s" % (src, scale))

    def test_px_rounds_half_away_from_zero(self):
        """必须四舍五入，不能截断 —— 0.5px 的偏移会把 1px 线摊成两条灰边。"""
        dpi.SCALE = 1.25
        self.assertEqual(dpi.px(1), 1)       # 1.25 → 1（向下）
        self.assertEqual(dpi.px(2), 3)       # 2.50 → 3（进位）
        self.assertEqual(dpi.px(6), 8)       # 7.50 → 8
        dpi.SCALE = 1.0
        self.assertEqual(dpi.px(0), 0)
        self.assertEqual(dpi.px(-3), -3)

    def test_px_always_returns_int(self):
        for scale in (1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 3.0):
            dpi.SCALE = scale
            for n in (1, 3, 7, 17, 34, 55, 89):
                self.assertIsInstance(dpi.px(n), int)

    def test_fx_keeps_fraction_for_strokes(self):
        dpi.SCALE = 1.5
        self.assertAlmostEqual(dpi.fx(1.7), 2.55)
        self.assertIsInstance(dpi.fx(1), float)

    # ---------- init ----------

    def test_init_reads_scale_argument(self):
        self.assertEqual(dpi.init(scale=1.5, awareness=False), 1.5)
        self.assertEqual(dpi.SCALE, 1.5)

    def test_init_clamps_extremes(self):
        """手改环境变量 / 系统 DPI 异常时，界面不能缩成一条缝或撑爆屏幕。"""
        self.assertEqual(dpi.init(scale=0.1, awareness=False), 0.75)
        self.assertEqual(dpi.init(scale=99, awareness=False), 4.0)

    def test_init_reads_env_var(self):
        old = os.environ.get("SUJIAN_UI_SCALE")
        try:
            os.environ["SUJIAN_UI_SCALE"] = "1.75"
            self.assertEqual(dpi.init(awareness=False), 1.75)
            os.environ["SUJIAN_UI_SCALE"] = "not-a-number"
            # 非法值要退回系统 DPI，而不是抛异常；本机至少是 0.75
            self.assertGreaterEqual(dpi.init(awareness=False), 0.75)
        finally:
            if old is None:
                os.environ.pop("SUJIAN_UI_SCALE", None)
            else:
                os.environ["SUJIAN_UI_SCALE"] = old


if __name__ == "__main__":
    unittest.main()
