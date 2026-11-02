# -*- coding: utf-8 -*-
"""文件夹行首「展开 / 收起」箭头（方案 C）的回归测试。

2026-11-06 把 8px 细描线折线（`>` / `v`，常态 `soft` 灰）换成 9px 填充实心
三角（▶ / ▼，常态正文色），hover 底片从 20×24 收到 16×16。要锁住的红线：

  * 箭头是**填充多边形**，不是 `create_line` —— Tk 不抗锯齿，1.7px 描线在
    8~9px 格子里端点会撑成方角、两臂不等粗；
  * 两态共用正方形外接框、**互为 90° 转置**，否则展开时箭头会矮一截；
  * 顶点全整数，尖端截成 2px 平头（不留 1px 细「尾巴」）；
  * 点击区仍是 24×28（用户两次反馈「不好点」，视觉改了命中区不许缩）；
  * 常态 `text`、hover `selfg`，底片 16×16 居中；叶子整块隐身。

运行：
    python -m unittest tests.test_folder_twisty -v
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

try:
    import tkinter as tk                                       # noqa: E402
except ImportError:
    tk = None

if tk is not None:
    from theme import LIGHT                                    # noqa: E402
    from widgets import TwistyButton, _draw_chevron            # noqa: E402
else:
    LIGHT = TwistyButton = _draw_chevron = None


class _Ev(object):
    """只带 _on_release 要读的 x / y。"""

    def __init__(self, x, y):
        self.x, self.y = x, y


def _box(coords):
    xs = coords[0::2]
    ys = coords[1::2]
    return min(xs), min(ys), max(xs), max(ys)


@unittest.skipIf(tk is None, "tkinter / display unavailable")
class ChevronGeometryTest(unittest.TestCase):

    def setUp(self):
        try:
            self.root = tk.Tk()
            self.root.withdraw()
            self.root.update()
        except Exception:
            self.skipTest("tkinter / display unavailable")

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    def _draw(self, down):
        c = tk.Canvas(self.root, width=24, height=28)
        _draw_chevron(c, 11.5, 13.5, 9, "#293b30", down=down)
        found = [(c.type(i), c.coords(i)) for i in c.find_all()]
        c.destroy()
        return found

    def test_filled_polygon_instead_of_stroked_line(self):
        """一个图元、类型是 polygon；不许退回 create_line 那套圆帽 / 圆角。"""
        for down in (False, True):
            items = self._draw(down)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0][0], "polygon")

    def test_all_vertices_are_integers(self):
        """GLYPH 奇数 + 像素中心 → 顶点必须落在整像素上。"""
        for down in (False, True):
            for v in self._draw(down)[0][1]:
                self.assertEqual(v, int(v))

    def test_two_states_share_a_square_box(self):
        gt = _box(self._draw(False)[0][1])
        vv = _box(self._draw(True)[0][1])
        self.assertEqual(gt, vv)
        self.assertEqual((gt[2] - gt[0], gt[3] - gt[1]), (9, 9))

    def test_two_states_are_90_degree_transposes(self):
        """绕中心 (11.5, 13.5) 转置收起态的点集，必须正好是展开态的点集。

        历史坑：按 `>` 的常见比例取 0.28/0.42 会画成 7×11 vs 11×7，
        展开时箭头明显矮一截。这条断言把「半宽 = 半高」钉死。
        """
        gt = self._draw(False)[0][1]
        vv = self._draw(True)[0][1]

        def points(coords):
            return set(zip(coords[0::2], coords[1::2]))

        cx, cy = 11.5, 13.5
        spun = {(cx + (y - cy), cy + (x - cx)) for x, y in points(gt)}
        self.assertEqual(spun, points(vv))

    def test_tip_is_a_2px_flat_not_a_1px_tail(self):
        """等腰三角收到 1px 会拖出一根细尾巴（像水滴）；尖端面宽必须是 2。"""
        for down in (False, True):
            pts = list(zip(*[iter(self._draw(down)[0][1])] * 2))
            # 两个相邻顶点共享同一条边 = 截出来的平头
            flats = [abs(a[0] - b[0]) + abs(a[1] - b[1])
                     for a, b in zip(pts, pts[1:] + pts[:1])]
            self.assertIn(1, flats)          # 平头跨 2 个像素 → 端点相距 1
            self.assertNotIn(0, flats)       # 不许退化出重合顶点

    def _draw_at(self, cx, cy, size, down, w=40, h=48):
        c = tk.Canvas(self.root, width=w, height=h)
        _draw_chevron(c, cx, cy, size, "#293b30", down=down)
        co = c.coords(c.find_all()[0])
        c.destroy()
        return co

    def test_integer_center_gets_normalized(self):
        """125% / 175% / 225% 档：px(28)=35/49/63 是奇数，调用方传进来的
        中心会落在整数上。函数必须自己规整回 .5，否则三角高比宽少 1px。
        """
        gt = self._draw_at(14, 17, 11, False, w=30, h=35)
        vv = self._draw_at(14, 17, 11, True, w=30, h=35)
        self.assertEqual(_box(gt), _box(vv))
        self.assertEqual((_box(gt)[2] - _box(gt)[0],
                          _box(gt)[3] - _box(gt)[1]), (11, 11))
        self.assertTrue(all(v == int(v) for v in gt + vv))

    def test_half_pixel_center_is_not_bankers_rounded(self):
        """cx=14.5 / cy=17.5：round(14.5)=14 会把平头往回拽 1px。

        平头必须仍以 cx / cy 为中心 —— 取整只许用 int(v+0.5)。
        """
        gt = self._draw_at(14.5, 17.5, 11, False)
        vv = self._draw_at(14.5, 17.5, 11, True)

        def pts(coords):
            return list(zip(*[iter(coords)] * 2))

        # 收起态平头在右边 x=20 上，两点 y 应跨 17 / 18（中心 17.5 = cy）
        right = sorted(p[1] for p in pts(gt) if p[0] == 20)
        self.assertEqual(right, [17, 18])
        # 展开态平头在下边 y=23 上，两点 x 应跨 14 / 15（中心 14.5 = cx）
        bottom = sorted(p[0] for p in pts(vv) if p[1] == 23)
        self.assertEqual(bottom, [14, 15])


@unittest.skipIf(tk is None, "tkinter / display unavailable")
class TwistyButtonTest(unittest.TestCase):

    def setUp(self):
        try:
            self.root = tk.Tk()
            self.root.withdraw()
            self.holder = tk.Frame(self.root)
            self.holder.pack()
            self.root.update()
        except Exception:
            self.skipTest("tkinter / display unavailable")

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    def _make(self, **kw):
        return TwistyButton(self.holder, LIGHT, base=LIGHT["side"], **kw)

    def _items(self, tw):
        return [(tw.type(i), tw.coords(i), tw.itemcget(i, "fill"))
                for i in tw.find_all()]

    def test_hit_area_never_shrinks(self):
        """视觉怎么改都行，24×28 点击区一个像素都不许动。"""
        tw = self._make()
        self.assertEqual((tw.SIZE_W, tw.SIZE_H), (24, 28))
        self.assertEqual(tw.winfo_reqwidth(), 24)
        self.assertEqual(tw.winfo_reqheight(), 28)
        self.assertEqual(tw.PLATE, 16)
        self.assertEqual(tw.GLYPH % 2, 1)      # 奇数 → 整数顶点

    def test_normal_state_uses_text_color(self):
        tw = self._make()
        items = self._items(tw)
        self.assertEqual(len(items), 1)        # 常态不画底片
        self.assertEqual(items[0][2], LIGHT["text"])

    def test_hover_plate_is_16x16_and_centered(self):
        tw = self._make()
        tw._set_state("hover")
        items = self._items(tw)
        self.assertEqual(len(items), 2)        # 底片 + 箭头
        plate = _box(items[0][1])
        self.assertEqual((plate[2] - plate[0], plate[3] - plate[1]), (16, 16))
        self.assertAlmostEqual((plate[0] + plate[2]) / 2.0, 12.0, delta=0.6)
        self.assertAlmostEqual((plate[1] + plate[3]) / 2.0, 14.0, delta=0.6)
        self.assertEqual(items[1][2], LIGHT["selfg"])

    def test_press_uses_press_bg_and_selfg(self):
        tw = self._make()
        tw._set_state("hover")
        hover_fill = self._items(tw)[0][2]
        tw._set_state("press")
        press_fill = self._items(tw)[0][2]
        self.assertNotEqual(hover_fill, press_fill)
        self.assertEqual(self._items(tw)[1][2], LIGHT["selfg"])

    def test_leaf_hides_everything(self):
        tw = self._make(available=False)
        self.assertEqual(self._items(tw), [])
        self.assertNotEqual(str(tw.cget("cursor")), "hand2")
        tw._set_state("hover")
        self.assertEqual(self._items(tw), [])   # 叶子 hover 也不出底片

    def test_set_collapsed_redraws_a_different_shape(self):
        tw = self._make(collapsed=True)
        gt = self._items(tw)[0][1]
        tw.set_collapsed(False)
        vv = self._items(tw)[0][1]
        self.assertNotEqual(gt, vv)
        self.assertEqual(_box(gt), _box(vv))

    def test_click_fires_command_and_stays_hover(self):
        fired = []
        tw = self._make(command=lambda: fired.append(1))
        tw._on_release(_Ev(10, 10))
        self.assertEqual(fired, [1])
        self.assertEqual(tw._state, "hover")

    def test_apply_theme_repaints_with_new_color(self):
        tw = self._make()
        pal = dict(LIGHT)
        pal["text"] = "#123456"
        tw.apply_theme(pal)
        self.assertEqual(self._items(tw)[0][2], "#123456")


if __name__ == "__main__":
    unittest.main()
