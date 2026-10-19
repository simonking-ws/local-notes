# -*- coding: utf-8 -*-
"""表面色（surface）的测试：悬停 / 选中 / 引用 / 行内代码 / 浮层 能不能分开。

这个文件存在的意义：配色「差一点点」肉眼看不准，也说不清阈值。之前那版皮肤
给的 hover(#dde8df) 与 sel(#dbe8dc) 通道差只有 5/765 —— 列表里「鼠标放上去」
和「选中这一条」长得一模一样，是靠用户反馈才发现的；引用底与行内代码底同理
（#f2f2f2 vs #f2f3f3，差 2）。把「至少要差多少」写成断言，换皮肤、加主题时
跑一遍就知道有没有退回旧坑。

被测对象全是 theme 里的纯函数，不建 GUI，标准库 unittest 即可跑：

    python tests\\test_theme_surfaces.py
或：
    python -m unittest discover -s tests -v
"""
import colorsys
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))      # 源码在 scripts/ 下

import theme                                                    # noqa: E402

# 三套代表性主题：内置浅灰、当前绿皮肤、一套深色（将来要支持的）
PALETTES = {
    "light": dict(theme.LIGHT),
    "green": {
        "top": "#e8f0e9", "side": "#e8f0e9", "list": "#f6f8f5", "editor": "#ffffff",
        "line": "#dce7dd", "text": "#293b30", "soft": "#59675e", "accent": "#3c8c4e",
        "hover": "#dde8df", "sel": "#dbe8dc", "selfg": "#256d37",
        "green": "#3c8c4e", "chk_off": "#c6d4c8", "star": "#f5a623",
    },
    "dark": {
        "top": "#252525", "side": "#252525", "list": "#202020", "editor": "#1e1e1e",
        "line": "#333333", "text": "#e8e8e8", "soft": "#9a9a9a", "accent": "#4a9e5c",
        "hover": "#333333", "sel": "#333333", "selfg": "#7fc08f",
        "green": "#4a9e5c", "chk_off": "#555555", "star": "#f5a623",
    },
}


def pal_of(name):
    """按各主题的规则跑一遍 _harmonize（跟启动时 PALETTE 走同一条路）。"""
    return theme._harmonize(dict(PALETTES[name]))


class HoverVsSelectedTest(unittest.TestCase):
    """悬停与选中：必须是两种「感觉」，不能只差两三个色阶。"""

    def test_distinguishable_in_every_theme(self):
        for name in PALETTES:
            p = pal_of(name)
            d = theme._color_dist(p["hover"], p["sel"])
            self.assertGreaterEqual(
                d, 18, "%s：悬停 %s 与选中 %s 只差 %d，列表里会分不出"
                       % (name, p["hover"], p["sel"], d))

    def test_hover_is_visible_against_its_base(self):
        for name in PALETTES:
            p = pal_of(name)
            ratio = theme._contrast_ratio(p["hover"], p["list"])
            self.assertGreater(ratio, 1.10,
                               "%s：悬停底与列表底反差只有 %.3f，等于没反应"
                               % (name, ratio))

    def test_selection_is_stronger_than_hover(self):
        for name in PALETTES:
            p = pal_of(name)
            self.assertGreater(
                theme._contrast_ratio(p["sel"], p["list"]),
                theme._contrast_ratio(p["hover"], p["list"]),
                "%s：选中行没有比悬停更「实」" % name)

    def test_hover_keeps_the_local_hue(self):
        """悬停只压明度：色相/饱和度必须原样保留（不能把绿底压成灰块）。"""
        for base in ("#e8f0e9", "#f6f8f5", "#f5f8f5", "#202020"):
            hb = colorsys.rgb_to_hls(*[c / 255.0 for c in theme._hex_rgb(base)])
            hh = colorsys.rgb_to_hls(*[c / 255.0 for c in theme._hex_rgb(theme._hover_bg(base))])
            self.assertAlmostEqual(hb[0], hh[0], places=3, msg="%s 的悬停色相变了" % base)
            # 8bit 往返会带来千分级的误差，允许 0.02 以内的漂移，但绝不能「掉色」
            self.assertAlmostEqual(hb[2], hh[2], delta=0.02,
                                   msg="%s 的悬停饱和度被稀释了" % base)


class EditorSurfacesTest(unittest.TestCase):
    """正文里的三块底色：引用 / 行内代码 / 选中文字，两两都要分得开。"""

    def test_quote_and_icode_do_not_collide(self):
        for name in PALETTES:
            p = pal_of(name)
            d = theme._color_dist(theme._quote_bg(p), theme._icode_bg(p))
            self.assertGreaterEqual(
                d, theme.ICODE_MIN_DIST, "%s：引用底与行内代码底只差 %d" % (name, d))

    def test_quote_is_visible_but_light(self):
        for name in PALETTES:
            p = pal_of(name)
            ratio = theme._contrast_ratio(theme._quote_bg(p), p["editor"])
            self.assertGreater(ratio, 1.06, "%s：引用底和稿纸同色了" % name)
            self.assertLess(ratio, 1.30, "%s：引用底压得太重，像代码块" % name)

    def test_selection_overrides_every_static_surface(self):
        for name in PALETTES:
            p = pal_of(name)
            # 深色主题的色域被压扁：深底之间天生差得少，24 是仍能分辨的下限
            floor = 24 if theme._luminance(p["editor"]) < 0.5 else 40
            sel = theme._sel_bg(p)
            self.assertGreaterEqual(
                theme._color_dist(sel, theme._quote_bg(p)), floor,
                "%s：选中态与引用底太接近" % name)
            self.assertGreaterEqual(
                theme._color_dist(sel, theme._icode_bg(p)), floor,
                "%s：选中态与行内代码底太接近" % name)

    def test_text_stays_readable_on_selection(self):
        for name in PALETTES:
            p = pal_of(name)
            self.assertGreaterEqual(
                theme._contrast_ratio(p["text"], theme._sel_bg(p)), 4.5,
                "%s：选中文字在选区底色上读不清" % name)


class PopoverTest(unittest.TestCase):
    """浮层：要「浮得出来」，还要带上主题色（不是一块没有归属的灰纸）。"""

    def test_floats_above_the_paper(self):
        for name in PALETTES:
            p = pal_of(name)
            bg = theme._popover_bg(p)
            ratio = theme._contrast_ratio(bg, p["editor"])
            self.assertGreaterEqual(
                ratio, 1.30, "%s：浮层与稿纸反差只有 %.3f，边界全靠描边撑" % (name, ratio))

    def test_border_is_darker_than_the_shell(self):
        for name in PALETTES:
            p = pal_of(name)
            bg, bd = theme._popover_bg(p), theme._popover_border(p)
            self.assertGreater(theme._contrast_ratio(bd, bg), 1.10,
                               "%s：浮层描边和浮层底分不开" % name)

    def test_shell_carries_a_hint_of_accent(self):
        """浮层底应比稿纸更「靠近主题色」（色相被主题色拉动），而不是纯灰。"""
        p = pal_of("green")
        ed, shell = p["editor"], theme._popover_bg(p)
        er, eg, eb = theme._hex_rgb(ed)
        sr, sg, sb = theme._hex_rgb(shell)
        ar, ag, ab = theme._hex_rgb(p["accent"])
        # 绿主题下：浮层的 g 通道应该比 r/b 掉得少（保留绿色调）
        self.assertLess(eg - sg, er - sr, "浮层没有向主题色靠")
        self.assertLess(eg - sg, eb - sb, "浮层没有向主题色靠")


class SearchHitTest(unittest.TestCase):
    """搜索命中的黄底：要「一眼看见」，还要和正文里已有的浅色块分得开。"""

    def test_visible_against_the_paper(self):
        for name in PALETTES:
            p = pal_of(name)
            bg = theme._search_hit_bg(p)
            ratio = theme._contrast_ratio(bg, p["editor"])
            self.assertGreaterEqual(
                ratio, 1.30, "%s：命中底色与稿纸反差只有 %.3f，等于没标" % (name, ratio))

    def test_differs_from_every_static_surface(self):
        """命中黄底与引用 / 行内代码 / 正文选区都不能撞色（它叠在最上面）。"""
        for name in PALETTES:
            p = pal_of(name)
            hit = theme._search_hit_bg(p)
            floor = 24 if theme._luminance(p["editor"]) < 0.5 else 30
            for label, other in (("引用", theme._quote_bg(p)),
                                 ("行内代码", theme._icode_bg(p)),
                                 ("选区", theme._sel_bg(p))):
                self.assertGreaterEqual(
                    theme._color_dist(hit, other), floor,
                    "%s：命中底色与%s太接近（%s vs %s）" % (name, label, hit, other))

    def test_keeps_the_text_readable(self):
        for name in PALETTES:
            p = pal_of(name)
            self.assertGreaterEqual(
                theme._contrast_ratio(p["text"], theme._search_hit_bg(p)), 4.5,
                "%s：命中词在黄底上读不清（正文色没跟着主题走？）" % name)


class ShadeTest(unittest.TestCase):
    """_shade / _away 这两个底层工具本身的行为。"""

    def test_shade_moves_only_lightness(self):
        # 浅底压暗、深底提亮（判据是 HLS 的 L 是否过半），这里两侧各测一个
        base = "#e8f0e9"
        c = theme._shade(base, 0.07, 1.12)
        hb = colorsys.rgb_to_hls(*[x / 255.0 for x in theme._hex_rgb(base)])
        hc = colorsys.rgb_to_hls(*[x / 255.0 for x in theme._hex_rgb(c)])
        self.assertLess(abs(hb[0] - hc[0]), 0.01)
        self.assertLess(hc[1], hb[1])           # 浅底 → 压暗

    def test_shade_lifts_dark_bases(self):
        base = "#202020"
        c = theme._shade(base, 0.07, 1.12)
        self.assertGreater(theme._luminance(c), theme._luminance(base))

    def test_away_stops_when_far_enough(self):
        c = theme._away("#ffffff", "#f0f0f0", "#000000", 0.05, 16)
        self.assertGreaterEqual(theme._color_dist(c, "#f0f0f0"), 16)


if __name__ == "__main__":
    unittest.main(verbosity=2)
