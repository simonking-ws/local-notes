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


class EditorBandsTest(unittest.TestCase):
    """编辑区两条横带：只读提示条 / 底部字数状态条。

    用户 2026-11-05 报障：说明笔记里那条「内置示例笔记」提示条直接吃
    pal["hover"]（列表行悬停色 #e3e9e0），和未选中标签 #e6e9e6 只差 6/255，
    两条灰带连成一片；底部状态条 #f4f4f4 与白稿纸反差 1.10，等于没画。
    把「至少差多少」写成断言，换肤、加主题时跑一遍就知道有没有退回旧坑。
    """

    def _tab_idle(self, p):
        return theme._tab_idle_bg(theme._toolbar_bg(p))

    def test_readonly_bar_is_not_an_inactive_tab(self):
        for name in PALETTES:
            p = pal_of(name)
            idle = self._tab_idle(p)
            ro = theme._readonly_bar_bg(p, idle)
            self.assertGreaterEqual(
                theme._color_dist(ro, idle), theme.RO_BAR_MIN_DIST,
                "%s：提示条 %s 与未选中标签 %s 撞色了" % (name, ro, idle))
            self.assertNotEqual(
                ro, p["hover"], "%s：提示条不该再用列表行悬停色" % name)

    def test_readonly_bar_is_visible_on_the_paper(self):
        """提示条是「一张纸片」，不能淡到和白稿纸分不出，也不能和引用块同色。"""
        for name in PALETTES:
            p = pal_of(name)
            ro = theme._readonly_bar_bg(p, self._tab_idle(p))
            self.assertGreaterEqual(
                theme._contrast_ratio(ro, p["editor"]), 1.12,
                "%s：提示条 %s 在稿纸上浮不出来" % (name, ro))
            self.assertGreater(
                theme._color_dist(ro, theme._quote_bg(p)), 6,
                "%s：提示条和引用块撞成同一张纸" % name)

    def test_status_bar_follows_the_theme_accent(self):
        """核心需求：换主题，状态条要变色。

        旧实现只往稿纸压中性墨，而浅色主题的稿纸都是白的 —— 于是绿皮肤、
        浅灰皮肤的状态条永远是同一个 #e3e3e3，换肤完全看不出来。
        """
        light, green = pal_of("light"), pal_of("green")
        self.assertEqual(light["editor"], green["editor"],
                         "稿纸同色才隔离得出「色相有没有跟着走」")
        a, b = theme._status_bar_bg(light), theme._status_bar_bg(green)
        self.assertGreater(
            theme._color_dist(a, b), 20,
            "浅灰 / 绿皮肤的状态条 %s / %s 几乎同色，等于没跟主题走" % (a, b))
        for name in PALETTES:
            c = theme._status_bar_bg(pal_of(name))
            r, g, bl = theme._hex_rgb(c)
            self.assertGreater(
                max(r, g, bl) - min(r, g, bl), 8,
                "%s：状态条 %s 仍是中性灰，没染上主题色相" % (name, c))

    def test_status_bar_is_distinct_from_the_other_bands(self):
        """两条带同色相最容易撞：提示条是淡纸片，状态条必须是更沉的一档。"""
        for name in PALETTES:
            p = pal_of(name)
            bg = theme._status_bar_bg(p)
            for other, label in (
                    (theme._readonly_bar_bg(p, self._tab_idle(p)), "只读提示条"),
                    (self._tab_idle(p), "未选中标签"),
                    (theme._quote_bg(p), "引用块")):
                self.assertGreaterEqual(
                    theme._color_dist(bg, other), theme.FOOTER_BAND_MIN_DIST,
                    "%s：状态条 %s 与%s %s 撞色" % (name, bg, label, other))

    def test_status_bar_contrasts_with_the_paper(self):
        """浅底压暗 / 深底提亮，方向由 _ink_target 定，但都得离稿纸够远。"""
        for name in PALETTES:
            p = pal_of(name)
            bg = theme._status_bar_bg(p)
            ratio = theme._contrast_ratio(bg, p["editor"])
            self.assertGreaterEqual(
                ratio, theme.FOOTER_MIN_RATIO,
                "%s：状态条 %s 与稿纸反差只有 %.3f" % (name, bg, ratio))
            # 上沿 1px 线要看得见（pal["line"] 只深 5% 的老坑）
            self.assertGreater(
                theme._color_dist(theme._status_bar_rule(p, bg), bg), 16,
                "%s：状态条上沿细线等于没画" % name)

    def test_status_bar_text_stays_readable(self):
        """底染了主题色又沉一档，文字得按底色动态提深（9pt 小字 ≥4.5）。"""
        for name in PALETTES:
            p = pal_of(name)
            bg = theme._status_bar_bg(p)
            fg = theme._status_bar_fg(p, bg)
            self.assertGreaterEqual(
                theme._contrast_ratio(fg, bg), theme.FOOTER_FG_MIN_RATIO,
                "%s：状态条小字 %s 在 %s 上读不清" % (name, fg, bg))
            self.assertEqual(fg, theme._status_bar_fg(p),
                             "%s：不传底色时也该算出同一个字色" % name)

    def test_toolbar_bg_single_source(self):
        """TabStrip 的常态色与提示条的参照色必须走同一个 _toolbar_bg 口径。"""
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import widgets                                          # noqa: E402
        self.assertEqual(widgets.TOOL_BG_MIX, theme.TOOL_BG_MIX)
        for name in PALETTES:
            p = pal_of(name)
            self.assertEqual(widgets._tab_idle_bg(widgets._toolbar_bg(p)),
                             self._tab_idle(p))


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
