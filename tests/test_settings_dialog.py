# -*- coding: utf-8 -*-
"""全局设置弹窗的硬约束（真窗口，临时笔记库）。

锁住用户反馈过的两件事：

1. **四节平级**：「字体设置 / 外观主题 / 工具栏按钮定制 / 标签页」四节标题必须是
   同一套字体、同一尺寸的图标。当初「全局设置」是 13pt 的窗口标题、图标 18px，
   底下三节是 12pt / 16px，看着像「一节套三节」。
2. **切主题当场换肤，且不能闪**：这张窗的底色 / 字色 / Canvas 图标全是建窗时
   一次性刷上去的（不像主窗有 `apply_palette` 可以反复重刷），所以颜色必须逐控件
   重刷。但**不许 destroy + 重建** —— 窗口消失又出现的那一帧就是「闪一下」；
   焦点 / grab / 没点确定的字号与勾选 / 滚动位置也都得原地保留。
3. **顶栏三张信息窗垂直居中**：全局设置 / 数据管理 / 关于按整窗（含标题栏与下
   边框）居中于主窗，三张中心一致；小型输入对话框仍是 1/3 偏上摆法。
4. **工具栏换肤不留旧色**：「文字颜色 / 下划线颜色」按钮曾把建窗时的 accent 写死
   成字色，换到深色主题后只有它不跟着变；现在它和 B/I/U 共用一套字色推导，
   且主窗换肤会把显式写死的 fg / outline 一并翻译。
5. **外观主题是折行色卡、不是下拉框**：六套主题各一枚圆圈（内部按各自配色
   着色）+ 下方名称，点击即换；换主题只重画选中环 / 名称色，圆内预览色固定。
   ⚠️ 真窗口断言一律复用本文件的 root：`widgets._PHOTO_CACHE` 是模块级缓存，
   PhotoImage 绑在第一个 Tcl 解释器上，另建 `tk.Tk()` 会让后面的测试类报
   「image pyimage1 doesn't exist」。

    python tests\\test_settings_dialog.py -v
"""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import dpi                                              # noqa: E402
dpi.init()

import tkinter as tk                                    # noqa: E402
from tkinter import ttk                                 # noqa: E402

TITLES = ("字体设置", "外观主题", "工具栏按钮定制", "标签页")
PAL_KEYS = ("editor", "text", "soft", "hover", "line", "accent", "sel", "selfg")
COLOR_OPTS = ("background", "foreground", "activebackground", "activeforeground",
              "selectcolor", "highlightbackground", "readonlybackground")


def _walk(w):
    yield w
    for c in w.winfo_children():
        for x in _walk(c):
            yield x


class TestSettingsDialog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="settings_test_")
        tmpdb = os.path.join(cls.tmpdir, "notes.db")
        src = os.path.join(ROOT, "data", "notes_data.db")
        if os.path.isfile(src):
            shutil.copy2(src, tmpdb)

        import store
        store._STORE = store.Store(tmpdb)
        import notes_app
        cls.root = tk.Tk()
        cls.root.withdraw()
        cls.app = notes_app.NotesApp(cls.root)
        cls.root.update_idletasks()
        cls.root.update()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()
        try:
            import store
            store.store().close()
        except Exception:
            pass
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    # ---------- 工具 ----------
    def _open(self):
        # 每个用例都自己开一张：先把上一条留下的关掉，不然弹窗会越堆越多
        self._close_dialogs()
        self.app.open_settings()
        self.root.update_idletasks()
        self.root.update()
        dlgs = self._dialogs()
        return dlgs[-1]

    def _dialogs(self):
        return [w for w in self.root.winfo_children()
                if isinstance(w, tk.Toplevel) and w.winfo_exists()]

    def _close_dialogs(self):
        for w in self._dialogs():
            try:
                w.grab_release()
            except Exception:
                pass
            w.destroy()
        self.root.update_idletasks()

    def _section_titles(self, dlg):
        out = {}
        for w in _walk(dlg):
            if isinstance(w, tk.Label):
                try:
                    txt = w.cget("text")
                except tk.TclError:
                    continue
                if txt in TITLES:
                    out[txt] = w.cget("font")
        return out

    def _section_icons(self, dlg):
        # 按 `sec_icon` 打的标记认，不按「小 Canvas」的尺寸猜：窗口真正映射之后
        # 「全选 / 全不选」这类矮按钮的实测高也会落到 22px 以内，按尺寸挑会多挑四枚。
        return [c for c in _walk(dlg) if isinstance(c, tk.Canvas)
                and hasattr(c, "_sec_icon")]

    def _theme_cards(self, dlg):
        """六张主题色卡。按 `cv._theme_tid` 标记认，不按 Canvas 尺寸猜。"""
        return [w for w in _walk(dlg) if isinstance(w, tk.Canvas)
                and hasattr(w, "_theme_tid")]

    def _theme_card(self, dlg, label):
        import theme
        tid = next(t for t, lb in theme.list_themes() if lb == label)
        return next(c for c in self._theme_cards(dlg) if c._theme_tid == tid)

    def _card_text(self, card):
        """色卡下方那行名称：(文字, 字色, 是否加粗)。"""
        for i in card.find_all():
            if card.type(i) == "text":
                return (str(card.itemcget(i, "text")),
                        str(card.itemcget(i, "fill")).lower(),
                        "bold" in str(card.itemcget(i, "font")))
        return None

    def _card_ring(self, card):
        """(色, 线宽)。色卡现在是位图，读渲染时留下的语义状态。"""
        color, width = card._card_state["ring"]
        return (str(color).lower(), float(width))

    def _card_swatch_fills(self, card):
        """圆内的三色预览（顶栏 / 稿纸 / 强调），不含选中底板 / 对勾。"""
        return sorted(set(str(c).lower() for c in card._card_state["swatch"]))

    def _switch(self, dlg, label):
        card = self._theme_card(dlg, label)
        card.event_generate("<Button-1>", x=card.winfo_width() // 2, y=8)
        self.root.update_idletasks()
        self.root.update()
        return card

    def _pal_colors(self, pal):
        return {str(pal[k]).lower() for k in PAL_KEYS if k in pal}

    def _stale_colors(self, dlg, old_pal, new_pal):
        """还留在控件颜色 option 上的「旧主题专属色」。

        只追旧板里有、新板里没有的颜色：两板共有的色（如两个浅色主题都用
        #ffffff 稿纸）没换也看不出来，不该算漏刷。
        """
        old_only = self._pal_colors(old_pal) - self._pal_colors(new_pal)
        out = []
        for w in _walk(dlg):
            for opt in COLOR_OPTS:
                try:
                    v = str(w.cget(opt)).lower()
                except tk.TclError:
                    continue
                if v in old_only:
                    out.append((w.winfo_class(), opt, v))
        return out

    def _icon_fills(self, dlg):
        out = []
        for c in self._section_icons(dlg):
            for i in c.find_all():
                try:
                    out.append(str(c.itemcget(i, "fill")).lower())
                except tk.TclError:
                    pass
        return out

    def _big_canvas(self, dlg):
        return max((c for c in _walk(dlg) if isinstance(c, tk.Canvas)),
                   key=lambda c: c.winfo_width() * c.winfo_height())

    def _window_center(self, dlg):
        """弹窗**整窗矩形**的垂直中心：geometry 的 y + (客户区高 + 标题栏 + 下边框)/2。"""
        dlg.update_idletasks()
        dx, dy = self.app._dlg_frame_pad(dlg)
        g = dlg.geometry()                     # "WxH+X+Y"，W/H 是客户区
        wh = int(g.split("x")[1].split("+")[0])
        rect_y = int(g.split("+")[-1])
        return rect_y + (wh + dy + dx) / 2.0

    def _with_visible_host(self, fn):
        """临时显示主窗、摆进工作区，把主窗中心横坐标交给 fn，结束再退回 withdraw。

        ⚠️ 刻意**不另建 Tk 解释器**：`widgets._PHOTO_CACHE` 是模块级缓存，PhotoImage
        绑在第一个解释器上；全量 discover 里再建一个 tk.Tk()，后面的测试类就会拿到
        「image pyimage1 doesn't exist」。所以居中这类真窗口断言只能复用本文件这个 root。
        """
        import win_frame
        self.root.deiconify()
        try:
            ax, ay, aw, ah = win_frame.work_area(self.root)
            w = max(dpi.px(640), min(dpi.px(1080), aw - dpi.px(60)))
            h = max(dpi.px(480), min(dpi.px(700), ah - dpi.px(60)))
            self.root.geometry("%dx%d+%d+%d" % (
                w, h, ax + (aw - w) // 2, ay + (ah - h) // 2))
            self.root.update_idletasks()
            self.root.update()
            fn(self.root.winfo_rooty() + self.root.winfo_height() / 2.0)
        finally:
            self._close_dialogs()
            self.root.withdraw()
            self.root.update_idletasks()

    # ---------- 断言 ----------
    def test_sections_share_one_font(self):
        dlg = self._open()
        titles = self._section_titles(dlg)
        self.assertEqual(sorted(titles), sorted(TITLES),
                         "四节标题不齐（找到 %s）" % sorted(titles))
        fonts = set(titles.values())
        self.assertEqual(len(fonts), 1,
                         "四节标题字体不一致：%s" % titles)
        font = fonts.pop()
        self.assertIn("bold", font, "四节标题都该是粗体，别只给其中几节加粗")

    def test_sections_share_one_icon_size(self):
        dlg = self._open()
        icons = self._section_icons(dlg)
        self.assertEqual(len(icons), 4, "四节该有四枚图标，实际 %d" % len(icons))
        sizes = {(c.winfo_width(), c.winfo_height()) for c in icons}
        self.assertEqual(len(sizes), 1, "四节图标尺寸不一致：%s" % sizes)
        for c in icons:
            self.assertGreaterEqual(len(c.find_all()), 3,
                                    "有图标几乎是空的（%d 个元素）" % len(c.find_all()))

    def test_title_is_font_section_with_glyph(self):
        dlg = self._open()
        self.assertIn("字体设置", self._section_titles(dlg),
                      "标题得叫「字体设置」（窗口名已经在标题栏了）")
        head = self._section_icons(dlg)[0]
        kinds = [head.type(i) for i in head.find_all()]
        self.assertNotIn("oval", kinds, "标题图标应换成字形 A（块面拼的），别再是圆形齿轮")

    def test_theme_switch_restyles_dialog_in_place(self):
        """换主题不得重建窗口（那一下就是「闪」），且所有控件 / 笔画都换色。"""
        self.app.set_theme("light")
        self.root.update_idletasks()
        dlg = self._open()
        cards = self._theme_cards(dlg)
        self.assertEqual(len(cards), 6, "设置窗里应有六张主题色卡")
        old_pal = dict(self.app.pal)

        # 造一批「还没点确定」的临时状态：字号拖到 18、勾掉一枚工具栏按钮、滚到底
        import widgets
        slider = next(w for w in _walk(dlg) if isinstance(w, widgets.FontSizeSlider))
        slider._var.set(18)
        check = next(w for w in _walk(dlg) if isinstance(w, tk.Checkbutton))
        var = str(check.cget("variable"))
        # getvar 拿到的是 Tcl 字符串 "0"/"1"，bool("0") 会是 True，必须先转 int
        before = int(check.getvar(var))
        check.invoke()
        checked = int(check.getvar(var))
        self.assertNotEqual(before, checked, "勾选没有真的被改掉")
        big = self._big_canvas(dlg)
        big.yview_moveto(1.0)
        dlg.update_idletasks()
        dlg.update()
        scroll_before = big.yview()[0]
        geom_before = dlg.geometry()
        grab_before = dlg.grab_current()

        self._switch(dlg, "石墨灰")

        new_pal = dict(self.app.pal)
        self.assertEqual(dlg.geometry(), geom_before,
                         "换主题动了弹窗几何 —— 即便不重建，窗口跳一下也还是「闪」")
        self.assertEqual(dlg.grab_current(), grab_before,
                         "换主题把模态 grab 弄丢了")
        self.assertEqual(new_pal["editor"], "#1c1c1f", "主窗没切到石墨灰")
        dlgs = self._dialogs()
        self.assertEqual(len(dlgs), 1, "弹窗数量不对（剩 %d 张）" % len(dlgs))
        # ⚠️ 核心：同一张窗、同一个对象。destroy + create 会换对象，也就必然闪一下
        self.assertIs(dlgs[0], dlg, "换主题把设置窗重建了 —— 这就是那一帧闪")
        self.assertEqual(self._theme_cards(dlg), cards, "主题色卡被重建了")
        self.assertEqual(dlg.cget("bg"), new_pal["editor"],
                         "弹窗底色没跟着主题走 —— 就是「主窗变了、弹窗没变」那个 bug")
        self.assertEqual(self._stale_colors(dlg, old_pal, new_pal), [],
                         "窗内还留着旧主题的控件颜色")

        # Canvas 图元不是 tk option：图标 / 滑杆 / 按钮也得按新板重画
        fills = self._icon_fills(dlg)
        self.assertIn(new_pal["accent"].lower(), fills, "四枚小节图标没按新主题色重画")
        if old_pal["accent"].lower() != new_pal["accent"].lower():
            self.assertNotIn(old_pal["accent"].lower(), fills, "图标还画着旧主题色")
        slider2 = next((w for w in _walk(dlg) if isinstance(w, widgets.FontSizeSlider)), None)
        self.assertIs(slider2, slider, "字号滑杆被重建了")
        track = [str(slider.itemcget(i, "fill")).lower() for i in slider.find_all()]
        self.assertIn(new_pal["accent"].lower(), track, "滑杆轨道还画着旧主题色")
        rounds = [w for w in _walk(dlg) if isinstance(w, widgets.RoundButton)]
        self.assertEqual(len(rounds), 4, "四枚圆角按钮不全了")
        for b in rounds:
            self.assertEqual(str(b.cget("bg")).lower(), new_pal["editor"].lower(),
                             "按钮画布底色没换")
            self.assertEqual(str(b._pal["accent"]).lower(), new_pal["accent"].lower(),
                             "按钮自绘配色没换")
        # 显式传过 fg / outline 的按钮：那两个值写死在实例上，最容易被漏掉
        cancel_btn = next(b for b in rounds if b.text == "取消")
        self.assertEqual(str(cancel_btn._fg).lower(), new_pal["soft"].lower(),
                         "「取消」字色还停在旧主题")
        self.assertEqual(str(cancel_btn._outline).lower(), new_pal["line"].lower(),
                         "「取消」描边还停在旧主题")

        # 未确认状态全在原地
        self.assertEqual(int(slider._var.get()), 18, "换主题把没确认的字号丢了")
        self.assertEqual(int(check.getvar(var)), checked, "换主题把没确认的勾选丢了")
        self.assertEqual(self._card_text(self._theme_card(dlg, "石墨灰"))[0], "石墨灰",
                         "色卡名称不对")
        self.assertEqual(self._card_ring(self._theme_card(dlg, "石墨灰")),
                         (new_pal["accent"].lower(), float(dpi.px(3))),
                         "换主题后选中环没落到刚点的那张色卡")
        self.assertNotEqual(self._card_ring(self._theme_card(dlg, "云母白"))[1],
                            float(dpi.px(3)), "旧主题的选中环没撤掉")
        # 位图的像素不在 tk 颜色 option 里，_stale_colors 扫不到：单独确认
        # 六张卡都按新主题重算过，且选中环用的就是新 accent。
        cards_after = self._theme_cards(dlg)
        self.assertEqual(len(cards_after), 6)
        self.assertTrue(all(c.find_withtag("bitmap") for c in cards_after),
                        "换主题后有卡没贴抗锯齿位图")
        self.assertEqual({c._card_state["cur_id"] for c in cards_after}, {"dark"},
                         "还有色卡停在建窗那套主题上没重算")
        self.assertEqual(self._card_ring(self._theme_card(dlg, "石墨灰"))[0],
                         new_pal["accent"].lower(), "位图选中环还画着旧主题色")
        self.assertAlmostEqual(self._big_canvas(dlg).yview()[0], scroll_before, places=2,
                               msg="换主题把滚动位置甩回顶部了")
        self.app.set_theme("forest")
        self._close_dialogs()

    def test_consecutive_theme_switches_do_not_go_stale(self):
        """连着切好几轮：每轮都得拿「上一轮的新板」当旧板，不能一直用建窗那套。"""
        self.app.set_theme("light")
        dlg = self._open()
        for label in ("石墨灰", "午夜蓝", "羊皮纸", "云母白", "森林绿", "晴空蓝"):
            old_pal = dict(self.app.pal)
            self._switch(dlg, label)
            new_pal = dict(self.app.pal)
            self.assertIs(self._dialogs()[0], dlg, "第 %s 轮把窗口重建了" % label)
            self.assertEqual(dlg.cget("bg"), new_pal["editor"],
                             "切到「%s」后弹窗底色没跟上" % label)
            self.assertEqual(self._stale_colors(dlg, old_pal, new_pal), [],
                             "切到「%s」后还有旧主题色残留在控件上" % label)
            self.assertIn(new_pal["accent"].lower(), self._icon_fills(dlg),
                          "切到「%s」后小节图标没重画" % label)
        self._close_dialogs()

    def test_theme_click_selects_before_repaint(self):
        """点击色卡：选中态先上屏，整窗换肤渲染才跟上。

        色卡重画原本排在 `repaint_settings()` 末尾，也就是 `apply_palette` 刷完
        主窗之后 —— 点下去要等整窗渲染完才看到「选中」，像没点中。现在选中是
        第一阶段（只重画色卡 + `update_idletasks` 上屏），渲染是第二阶段。
        """
        self.app.set_theme("light")
        dlg = self._open()
        self.assertFalse(self._theme_card(dlg, "石墨灰")._card_state["selected"])

        events = []
        orig_set_theme = self.app.set_theme
        orig_flush = dlg.update_idletasks

        def spy_set_theme(tid):
            # 整窗渲染刚开始这一刻，窗内色卡必须已经把新主题标成选中
            states = {c._theme_tid: c._card_state["selected"]
                      for c in self._theme_cards(dlg)}
            events.append(("render", tid, states, dict(self.app.pal)))
            return orig_set_theme(tid)

        def spy_flush():
            events.append(("flush",))
            return orig_flush()

        self.app.set_theme = spy_set_theme
        dlg.update_idletasks = spy_flush
        try:
            self._switch(dlg, "石墨灰")
        finally:
            del self.app.set_theme
            del dlg.update_idletasks

        self.assertEqual([e[0] for e in events], ["flush", "render"],
                         "顺序应是「先选中上屏 → 再渲染」：%s" % [e[0] for e in events])
        _ev, tid, states, pal_at_render = events[1]
        self.assertEqual(tid, "dark")
        self.assertTrue(states["dark"], "渲染开始时新主题还没标成选中")
        self.assertFalse(states["light"], "渲染开始时旧主题还标着选中")
        self.assertNotEqual(pal_at_render["accent"], self.app.pal["accent"],
                            "渲染开始时调色板已是新板 —— 选中没走在渲染前面")

        # 拆成两阶段不能把「不闪」这条红线弄丢
        self.assertIs(self._dialogs()[0], dlg, "两阶段把设置窗重建了")
        self.assertEqual(dlg.cget("bg"), self.app.pal["editor"], "整窗没渲染完")
        self.assertEqual(self.app._current_theme(), "dark", "主题没落库")
        self.assertTrue(self._theme_card(dlg, "石墨灰")._card_state["selected"])
        self.assertEqual(self._card_ring(self._theme_card(dlg, "石墨灰")),
                         (self.app.pal["accent"].lower(), float(dpi.px(3))),
                         "渲染完成后选中环没落到刚点的那张卡")
        self._close_dialogs()
        self.app.set_theme("forest")

    def test_theme_cards_replace_the_dropdown(self):
        """外观主题改成折行色卡：圈内按主题着色、名称在下方、点击即换。

        列数按窗口宽度自适应：默认宽度一行摆得下 6 个，窄了自动折行。圆内是
        **每套主题自己**的固定配色，换主题只该换选中环与名称色，不能把六张
        预览一起洗掉 —— 那就没有「横向比较」的意义了。
        """
        self.app.set_theme("light")
        dlg = self._open()
        cards = self._theme_cards(dlg)
        self.assertEqual(len(cards), 6, "六套主题各一张色卡")

        # 不再有主题下拉框：设置窗里只剩字体族那一枚 Combobox
        combos = [w for w in _walk(dlg) if isinstance(w, ttk.Combobox)]
        self.assertEqual(len(combos), 1, "除字体族外还留着别的上拉框")
        self.assertNotIn("午夜蓝", [str(v) for v in combos[0].cget("values")],
                         "外观主题还挂在下拉框里")

        # 折行网格：每张卡都落在独立的 grid 格子里，名称画在圆的下沿之下
        cells = [(int(c.grid_info()["row"]), int(c.grid_info()["column"]))
                 for c in cards]
        self.assertEqual(len(set(cells)), 6, "色卡格子撞位了")
        self.assertEqual(len({c._theme_tid for c in cards}), 6, "色卡主题 id 重复")
        for c in cards:
            # 圆 / 环现在是一张位图（不是 oval 图元），名称仍是 Canvas 文字
            self.assertTrue(c.find_withtag("bitmap"),
                            "%s 没贴色卡位图" % c._theme_tid)
            texts = [c.coords(i) for i in c.find_withtag("name")]
            self.assertTrue(texts, "%s 缺名称" % c._theme_tid)
            self.assertGreater(texts[0][1], c._card_state["swatch_bottom"] - 1,
                               "%s 的名称没画在圆下方" % c._theme_tid)
            self.assertEqual(len(self._card_swatch_fills(c)), 3,
                             "%s 的圆应是顶栏 / 稿纸 / 强调三色" % c._theme_tid)

        import theme
        for c in cards:
            q = theme.build_palette(c._theme_tid)
            fills = self._card_swatch_fills(c)
            for key in ("editor", "top", "accent"):
                self.assertIn(q[key].lower(), fills,
                              "%s 的圆里少了 %s 色" % (c._theme_tid, key))
        before = {c._theme_tid: self._card_swatch_fills(c) for c in cards}

        sky = self._theme_card(dlg, "晴空蓝")
        sky.event_generate("<Button-1>", x=sky.winfo_width() // 2, y=8)
        self.root.update_idletasks()
        self.root.update()
        self.assertEqual(self.app._current_theme(), "sky", "点色卡没切主题")
        self.assertEqual(self._card_ring(sky),
                         (self.app.pal["accent"].lower(), float(dpi.px(3))),
                         "选中环没落到刚点的那张")
        self.assertTrue(self._card_text(sky)[2], "选中主题的名称没加粗")
        self.assertEqual({c._theme_tid: self._card_swatch_fills(c) for c in cards},
                         before, "换主题把其它色卡的预览色洗掉了")
        self._close_dialogs()
        self.app.set_theme("forest")

    def test_section_title_and_tool_labels(self):
        """小节叫「工具栏按钮定制」，两个列表项叫「无序 / 有序列表」。

        「列表 / 序号」是图标名，放在复选框里会被当成「总开关 / 编号工具」；
        换成正式叫法后一眼知道对应工具栏的哪两枚按钮。
        """
        dlg = self._open()
        self.assertIn("工具栏按钮定制", self._section_titles(dlg),
                      "小节标题没改成「工具栏按钮定制」")
        labels = [str(w.cget("text")) for w in _walk(dlg)
                  if isinstance(w, tk.Checkbutton)]
        self.assertIn("无序列表", labels, "项目符号按钮没叫「无序列表」")
        self.assertIn("有序列表", labels, "数字列表按钮没叫「有序列表」")
        self.assertIn("三角列表", labels, "三角列表不该被顺手改名")
        self.assertNotIn("列表", labels, "还留着含糊的「列表」")
        self.assertNotIn("序号", labels, "还留着含糊的「序号」")
        self._close_dialogs()

    def test_selected_theme_card_is_unmistakable(self):
        """选中态不能只靠一圈与圆心同色的 accent 细环 —— 那像色卡自带装饰。

        选中卡要有整张底板 / 圆外光晕 / 对勾徽标 / 粗环 / 加粗换色名称五层提示，
        未选中卡一层都不许有；换主题时这些标记整体搬家，圆内预览色一张都不洗。
        """
        self.app.set_theme("light")
        dlg = self._open()
        cur = self._theme_card(dlg, "云母白")
        other = self._theme_card(dlg, "石墨灰")

        st = cur._card_state
        self.assertTrue(st["selected"], "选中卡没被标记成选中")
        self.assertIsNotNone(st["cardbg"], "选中卡没有整张底板")
        self.assertIsNotNone(st["halo"], "选中卡没有圆外光晕")
        self.assertTrue(st["check"], "选中卡缺对勾徽标")
        self.assertEqual(self._card_ring(cur),
                         (self.app.pal["accent"].lower(), float(dpi.px(3))),
                         "选中环不是 accent 粗环")
        text = self._card_text(cur)
        self.assertTrue(text[2], "选中主题名称没加粗")
        self.assertEqual(text[1], self.app.pal["accent"].lower(),
                         "选中主题名称没换成强调色")
        ost = other._card_state
        self.assertFalse(ost["selected"], "未选中卡被标成选中")
        self.assertIsNone(ost["cardbg"], "未选中卡不该有底板")
        self.assertIsNone(ost["halo"], "未选中卡不该有光晕")
        self.assertFalse(ost["check"], "未选中卡不该有对勾")
        self.assertNotEqual(self._card_ring(other)[1], float(dpi.px(3)),
                            "未选中卡也套了粗环")

        # 鼠标移到别的卡上：只给一层可点的淡底板，选中标记必须仍留在原处
        other.event_generate("<Enter>")
        self.root.update_idletasks()
        self.root.update()
        self.assertIsNotNone(other._card_state["cardbg"], "悬停没给可点的底板")
        self.assertFalse(other._card_state["selected"], "悬停被误标成「选中」")
        self.assertTrue(cur._card_state["selected"], "悬停别的卡把当前选中冲掉了")
        other.event_generate("<Leave>")
        self.root.update_idletasks()
        self.root.update()
        self.assertIsNone(other._card_state["cardbg"], "移开后悬停底板没撤掉")

        before = {c._theme_tid: self._card_swatch_fills(c)
                  for c in self._theme_cards(dlg)}
        self._switch(dlg, "石墨灰")
        self.assertTrue(self._theme_card(dlg, "石墨灰")._card_state["selected"],
                        "新选中的卡没接到选中标记")
        self.assertTrue(self._theme_card(dlg, "石墨灰")._card_state["check"],
                        "新选中的卡没接到对勾")
        self.assertFalse(self._theme_card(dlg, "云母白")._card_state["selected"],
                         "旧选中的卡还留着选中标记")
        self.assertEqual({c._theme_tid: self._card_swatch_fills(c)
                          for c in self._theme_cards(dlg)}, before,
                         "换主题把色卡圆内的预览色洗掉了")
        self._close_dialogs()
        self.app.set_theme("forest")

    def test_aa_rasterizer_produces_soft_edges(self):
        """栅格化器本身：圆边缘要有中间灰阶，且圆心两侧严格对称。

        这条不依赖窗口，直接锁住「修复毛刺」的核心：Tk 的 create_oval 只有
        非此即彼两种颜色，覆盖率混色必须产生一批过渡色。
        """
        import widgets
        pic = widgets.AAImage(24, 24, "#ffffff")
        sdf, bb = widgets.aa_disc(12.0, 12.0, 8.0)
        pic.fill("#000000", sdf, bb)
        shades = {c for row in pic.rows for c in row}
        self.assertIn("#ffffff", shades, "圆外底色没了")
        self.assertIn("#000000", shades, "圆内实色没了")
        self.assertGreater(len(shades), 4,
                          "边缘只有 %d 种颜色 → 仍是硬边台阶" % len(shades))
        self.assertEqual(pic.rows[8], list(reversed(pic.rows[8])),
                         "圆心两侧不对称（整数包围盒吃掉了 1px）")

    def test_theme_card_bitmap_tracks_theme(self):
        """真窗口：色卡位图有过渡色，换主题后同一张卡的像素确实重算过。"""
        self.app.set_theme("light")
        dlg = self._open()
        card = self._theme_card(dlg, "云母白")

        def colors(c):
            img = c._theme_photo
            return {img.get(x, y)
                    for y in range(0, img.height(), 2)
                    for x in range(0, img.width(), 2)}

        before = colors(card)
        self.assertGreater(len(before), 8,
                           "色卡只有 %d 种颜色，抗锯齿没生效" % len(before))
        self._switch(dlg, "石墨灰")
        self.assertNotEqual(colors(card), before,
                            "换主题后色卡位图没重算（还是旧主题的像素）")
        self._close_dialogs()
        self.app.set_theme("forest")

    def test_info_dialogs_are_vertically_centered(self):
        """顶栏三张信息窗（设置 / 数据管理 / 关于）整窗垂直居中，且中心一致。

        高度各不相同的三张窗若都按 1/3 摆，会明显偏上、来回切换时上下边缘乱跳；
        按整窗（含标题栏 / 下边框）居中后，中心对齐，切换只有上下边缘对称伸缩。
        """
        centers = []

        def check(host):
            for name, opener in (("全局设置", self.app.open_settings),
                                 ("数据管理", self.app.open_data_manager),
                                 ("关于", self.app.open_about)):
                self._close_dialogs()
                opener()
                self.root.update_idletasks()
                self.root.update()
                dlgs = self._dialogs()
                self.assertEqual(len(dlgs), 1, "%s 打开后弹窗数量不对" % name)
                dlg = dlgs[0]
                self.assertTrue(bool(dlg._dlg_center_y),
                                "%s 没走垂直居中分支" % name)
                c = self._window_center(dlg)
                self.assertAlmostEqual(c, host, delta=2,
                                       msg="%s 中心 %.1f 与主窗中心 %.1f 不符"
                                           % (name, c, host))
                centers.append(c)
            self.assertAlmostEqual(max(centers) - min(centers), 0, delta=2,
                                   msg="三张窗中心不一致，切换时会上下跳：%s" % centers)

        self._with_visible_host(check)

    def test_relayout_keeps_center_small_dialogs_keep_third(self):
        """关于窗内容变高后重排仍居中；小型输入窗仍是 1/3 偏上摆法。"""
        def check(host):
            self._close_dialogs()
            self.app.open_about()
            self.root.update_idletasks()
            dlg = self._dialogs()[-1]
            self.app._relayout_dialog(dlg)
            self.root.update_idletasks()
            self.assertTrue(bool(dlg._dlg_center_y), "重排把居中标记弄丢了")
            self.assertAlmostEqual(self._window_center(dlg), host, delta=2,
                                   msg="重排后关于窗又偏上了")

            self._close_dialogs()
            win = self.app._dlg_body("测试")
            tk.Label(win, text="占位").pack(padx=20, pady=20)
            self.app._center_dialog(win)
            self.root.update_idletasks()
            self.assertFalse(bool(win._dlg_center_y), "小窗被误设成垂直居中")
            self.assertLess(self._window_center(win), host - 10,
                            "小窗该比正中偏上（1/3 摆法）")

        self._with_visible_host(check)

    def test_toolbar_color_button_shares_fg_with_peers_in_every_theme(self):
        """工具栏「文字颜色 / 下划线颜色」(A) 的字色必须和 B/I/U 一模一样。

        以前这枚按钮单独传了 fg=pal["accent"]，把建窗那一刻的强调色写死在实例上：
        换主题时旁边按钮按新板从 #404046 变 #ceced3，它却永远是旧蓝 #6f86ff，
        深色主题下看着像被选中/高亮。
        """
        for tid in ("light", "dark", "midnight", "sepia", "forest", "sky"):
            self.app.set_theme(tid)
            self.root.update_idletasks()
            a = self.app._tool_btns["color"]
            b = self.app._tool_btns["bold"]
            self.assertIsNone(a._fg, "%s：颜色按钮又把 accent 写死在实例上了" % tid)
            self.assertEqual(a._cur_fg(), b._cur_fg(),
                             "%s：颜色按钮字色 %s 与旁边按钮 %s 不一致"
                             % (tid, a._cur_fg(), b._cur_fg()))
            # 画布图元也要真的按这个色画出来，不能只改属性忘了重绘
            fills = set()
            for i in a.find_all():
                try:
                    c = str(a.itemcget(i, "fill")).lower()
                except tk.TclError:
                    continue
                if c:
                    fills.add(c)
            self.assertIn(a._cur_fg().lower(), fills,
                          "%s：按钮上的 A / 下拉箭头没按新字色重画" % tid)
        self.app.set_theme("forest")

    def test_toolbar_explicit_fg_is_translated_on_theme_change(self):
        """兜底：万一还有按钮显式写了调色板字色，换肤也得按「旧板 → 新板」翻译。"""
        self.app.set_theme("light")
        btn = self.app._tool_btns["color"]
        old_acc = self.app.pal["accent"]
        btn._fg = old_acc
        try:
            btn.redraw()
            self.app.set_theme("dark")
            self.assertNotEqual(btn._fg, old_acc, "换主题后字色还停在旧 accent 上")
            self.assertEqual(btn._fg, self.app.pal["accent"],
                             "显式字色没被翻译到新主题的同名角色")
        finally:
            btn._fg = None
            btn.redraw()
            self.app.set_theme("forest")


if __name__ == "__main__":
    unittest.main(verbosity=2)
