# -*- coding: utf-8 -*-
"""全局设置弹窗的硬约束（真窗口，临时笔记库）。

锁住用户反馈过的两件事：

1. **五节平级**：「字体设置 / 外观主题 / 工具栏按钮定制 / 图片与图床 / 标签页」
   五节标题必须是同一套字体、同一尺寸的图标。当初「全局设置」是 13pt 的窗口
   标题、图标 18px，底下几节是 12pt / 16px，看着像「一节套几节」。
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
    「image pyimage1 doesn't exist」。正文自动换行（工具栏第 15 枚）这组真窗口
    断言也放这里，同样复用本文件的 app / root；「任务提醒」分类与汇总表的
    真窗口断言同理。

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

TITLES = ("字体设置", "外观主题", "工具栏按钮定制", "图片与图床", "标签页")
PAL_KEYS = ("editor", "text", "soft", "hover", "line", "accent", "sel", "selfg")
COLOR_OPTS = ("background", "foreground", "activebackground", "activeforeground",
              "selectcolor", "highlightbackground", "readonlybackground")


def _walk(w):
    yield w
    for c in w.winfo_children():
        for x in _walk(c):
            yield x


def _mk_png(w, h, rgb):
    """造一张纯色 PNG（测试图，不落进用户目录）。"""
    import struct
    import zlib

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))

    px = bytes(rgb) + b"\xff"
    raw = b"".join(b"\x00" + px * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


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
        # 真实库里的 current_note 可能指向已删除的笔记（用户库当前就是这个状态），
        # 复制过来的临时库也带着它；很多动作一开头就 current_note() 判空返回，
        # 会让 Shift+Tab / 删整行这些用例静默失效。测试库统一指到一篇普通笔记。
        data = cls.app.data
        if data.get("current_note") not in {n["id"] for n in data["notes"]}:
            target = next((n for n in data["notes"]
                           if not n.get("sample") and n["id"] != store.ALARM_INDEX_ID),
                          None)
            if target:
                data["current_note"] = target["id"]
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
        self.assertEqual(len(icons), 5, "五节该有五枚图标，实际 %d" % len(icons))
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
        # 六枚：全不选 / 全选（工具栏节）+ 浏览 / 测试上传（图片节）+ 确定 / 取消
        self.assertEqual(len(rounds), 6, "六枚圆角按钮不全了")
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

    def test_find_bar_hover_uses_current_theme_after_switch(self):
        """查找浮条按钮的悬停色必须跟着当前主题走，不能闪回建窗时的旧板。

        `_find_open` 建浮条时按钮的 <Enter>/<Leave> 闭包若捕获旧 pal，换肤后
        `_restyle_find_bar` 只重配静态色、不重绑事件，一悬停就冒出旧主题色。
        """
        import widgets
        from theme import build_palette
        self._close_dialogs()
        self.app.set_theme("light")
        self.app._find_open()                 # 建浮条，按钮绑定此时捕获 light 板
        self.root.update_idletasks()
        bar = self.app._find_bar
        self.assertIsNotNone(bar, "查找浮条没建起来")
        # 找一枚有 hand2 光标的按钮 Label（✕ / ↓ / ↑）
        btn = next((w for w in _walk(bar) if isinstance(w, tk.Label)
                    and str(w.cget("cursor")) == "hand2"), None)
        self.assertIsNotNone(btn, "找不到查找浮条按钮")

        self.app.set_theme("dark")            # 换肤：pal 换成新对象
        self.root.update_idletasks()
        new_pal = dict(build_palette("dark"))
        btn.event_generate("<Enter>")
        self.root.update_idletasks()
        # 悬停用 _hover_bg(editor) 派生、文字用 text；两者都必须来自新板
        from app_ui import _hover_bg
        self.assertEqual(str(btn.cget("bg")).lower(),
                         _hover_bg(new_pal["editor"]).lower(),
                         "悬停底色闪回了旧主题")
        self.assertEqual(str(btn.cget("fg")).lower(), new_pal["text"].lower(),
                         "悬停字色闪回了旧主题")
        btn.event_generate("<Leave>")
        self.root.update_idletasks()
        self.assertEqual(str(btn.cget("bg")).lower(), new_pal["editor"].lower(),
                         "离开后浮条底色闪回了旧主题")
        self.app.set_theme("forest")
        self._close_dialogs()

    def test_cancel_rolls_back_theme_and_font(self):
        """主题 / 字体是点选即落库的；点「取消」必须把它们也还原。

        同窗的字号 / 勾选靠「确定」收尾，这两项却是即时生效 —— 若取消不回滚，
        用户点了取消还留着半路改掉的皮肤和字体，语义自相矛盾。
        """
        import widgets
        self.app.set_theme("forest")
        self.app.ui_font = ""
        self.app.data.setdefault("settings", {})["font_family"] = ""
        self.root.update_idletasks()
        dlg = self._open()

        # 在窗内改主题 + 改字体（都即时落库）
        self._switch(dlg, "石墨灰")
        fam_cb = next(w for w in _walk(dlg) if isinstance(w, widgets.ThemedComboBox))
        idx = fam_cb._values.index("微软雅黑")
        fam_cb._choose(idx)          # 走完整选择路径，触发 on_family 即时落库
        self.root.update_idletasks()

        self.assertEqual(self.app._current_theme(), "dark", "前置：主题没切成石墨灰")
        self.assertEqual(self.app.ui_font, "Microsoft YaHei", "前置：字体没切成微软雅黑")

        # 点「取消」
        rounds = [w for w in _walk(dlg) if isinstance(w, widgets.RoundButton)]
        cancel_btn = next(b for b in rounds if b.text == "取消")
        cancel_btn.command()          # command() 返回回调本身，直接调用
        self.root.update_idletasks()

        self.assertEqual(self.app._current_theme(), "forest", "取消后主题没回滚")
        self.assertEqual((self.app.ui_font or ""), "", "取消后字体没回滚")
        self.assertEqual(self.app.data["settings"].get("font_family", ""), "",
                         "取消后落库的字体没回滚")

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
        import theme
        self.app.set_theme("light")
        dlg = self._open()
        cards = self._theme_cards(dlg)
        self.assertEqual(len(cards), 6, "六套主题各一张色卡")

        # 展示顺序按 grid 行列读（就是用户看到的第一格 / 第二格 …）：
        # 默认主题森林绿占第一格，其余跟 list_themes() 一致。
        ordered = sorted(cards, key=lambda c: (int(c.grid_info()["row"]),
                                               int(c.grid_info()["column"])))
        self.assertEqual([c._theme_tid for c in ordered],
                         [t for t, _lb in theme.list_themes()],
                         "色卡没按 list_themes() 的展示顺序摆")
        self.assertEqual(ordered[0]._theme_tid, theme.DEFAULT_THEME,
                         "森林绿（默认主题）应是第一张色卡")

        # 不再有主题下拉框：字体族也换成了自绘的下拉框（跟设置窗同视觉语言、
        # 换肤跟着走），所以整张窗里一枚 ttk.Combobox 都不该剩。
        combos = [w for w in _walk(dlg) if isinstance(w, ttk.Combobox)]
        self.assertEqual(len(combos), 0, "还留着原生 ttk 下拉框")
        import widgets
        fam = [w for w in _walk(dlg) if isinstance(w, widgets.ThemedComboBox)]
        self.assertEqual(len(fam), 1, "字体族没换成主题化下拉框")
        self.assertNotIn("午夜蓝", fam[0]._values, "外观主题还挂在字体下拉框里")

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

    def _font_combo(self, dlg):
        import widgets
        return next(w for w in _walk(dlg)
                    if isinstance(w, widgets.ThemedComboBox))

    def test_font_combo_is_themed_not_system(self):
        """正文字体下拉框是自绘主题控件：稿纸底 + 派生描边 + 强调色箭头，
        每项用它自己的字体渲染；换肤后整只重画，不留系统白底旧色。
        """
        import widgets
        self.app.set_theme("light")
        dlg = self._open()
        cb = self._font_combo(dlg)
        self.assertIsInstance(cb, widgets.ThemedComboBox)
        self.assertEqual(cb._values, [lb for lb, _ in self.app.FONT_FAMILIES])
        # 每项一份字体对象，且按 font_map 用各自的字体渲染（字体选择器所见即所得）。
        # ⚠️ 不查 Font.cget("family")：Windows 上 PingFang SC 没装会回退成雅黑，
        # 断言解析后的族会误报；查传入的 font_map 才是「按哪个族渲染」的语义。
        self.assertEqual(set(cb._fonts), set(cb._values))
        self.assertIsNone(cb._font_map["默认"])
        self.assertEqual(cb._font_map["微软雅黑"], "Microsoft YaHei")
        self.assertEqual(cb._font_map["宋体"], "SimSun")

        # 闭合态画了圆角底 + 文字 + 箭头三类图元，颜色都来自当前调色板
        kinds = [cb.type(i) for i in cb.find_all()]
        self.assertIn("polygon", kinds, "缺圆角底板")
        self.assertIn("text", kinds, "缺当前字体名")

        # 换到深色主题：就地重刷后 Canvas 底色要跟新稿纸底一致（不是系统白）
        sky = self._theme_card(dlg, "石墨灰")
        sky.event_generate("<Button-1>", x=sky.winfo_width() // 2, y=8)
        self.root.update_idletasks()
        self.root.update()
        self.assertEqual(str(cb.cget("bg")).lower(), self.app.pal["editor"].lower(),
                         "换肤后下拉框底色没跟到新主题")
        self.assertEqual(cb._pal["accent"].lower(), self.app.pal["accent"].lower(),
                         "换肤后下拉框实例配色没更新")
        self._close_dialogs()
        self.app.set_theme("forest")

    def test_font_combo_width_and_caption_sizes(self):
        """正文字体下拉框固定 150 宽（切换项不跳宽）；图片与图床节的三条
        说明文案字号统一 —— 底部那条原来小一号（10 vs 11），很扎眼。
        """
        dlg = self._open()
        cb = self._font_combo(dlg)
        self.assertEqual(cb._fixed_w, dpi.px(150))
        # 选另一个字体后宽度不变（固定宽的意义：框不忽宽忽窄）
        cb.set("宋体")
        self.assertEqual(cb.winfo_width(), dpi.px(150))

        # cget("font") 是字符串 "{Microsoft YaHei} 11"，末段才是字号
        caps = {}
        for w in _walk(dlg):
            if isinstance(w, tk.Label):
                try:
                    t = w.cget("text")
                except tk.TclError:
                    continue
                caps[t] = w.cget("font")
        for txt in ("留空 PicList路径：图片保存在 data/media/，不联网。",
                    "填好路径：插图后自动上传图床，上传成功会删除本地副本。",
                    "路径只在本机生效，不随备份带走"):
            self.assertIn(txt, caps, "缺说明文案：%s" % txt)
        sizes = {int(caps[txt].split()[-1]) for txt in (
            "留空 PicList路径：图片保存在 data/media/，不联网。",
            "填好路径：插图后自动上传图床，上传成功会删除本地副本。",
            "路径只在本机生效，不随备份带走")}
        self.assertEqual(sizes, {11}, "图片与图床三条说明字号不一致：%s" % sizes)
        self._close_dialogs()

    def test_font_combo_selects_and_callbacks(self):
        """点选项 → 更新当前值、触发回调落库；弹出卡片贴控件下方且卡片内
        每行用它自己的字体；选完收起并把 grab 还给设置窗（否则模态失效）。
        """
        import widgets
        dlg = self._open()
        cb = self._font_combo(dlg)
        old_grab = self.root.grab_current()

        cb.event_generate("<Button-1>")
        self.root.update_idletasks()
        self.assertIsNotNone(cb._win, "点击没弹出下拉")
        self.assertEqual(
            [cb._cv.type(i) for i in cb._cv.find_all()].count("text"),
            len(cb._values), "弹出卡片没逐项渲染")
        # 弹出层抢了 grab，关掉要还回原来的设置窗
        self.assertIsNot(cb._win, old_grab)

        # 直接走选择逻辑（grab 下事件派发给弹层，坐标换算不入用例，测语义足够）
        target = "宋体"
        cb._choose(cb._values.index(target))
        self.root.update_idletasks()
        self.assertIsNone(cb._win, "选完没收起")
        self.assertEqual(cb.get(), target)
        self.assertEqual(self.app.data["settings"]["font_family"], "SimSun",
                         "回调没落库字体族")
        if old_grab is not None and old_grab.winfo_exists():
            self.assertIs(self.root.grab_current(), old_grab,
                          "关下拉后没把 grab 还给设置窗")
        self._close_dialogs()

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

    def test_import_mode_dialog_explanations_share_font_size(self):
        """导入弹窗三条说明文案（导出时间 / 当前数据 / 覆盖合并差别）字号一致。

        三者同为 fg=soft 的说明文字、在同一张窗里上下紧邻，字号必须同档。
        曾出现「11 / 11 / 10」——最后那条讲覆盖与合并差别的说明小一号，
        和图片与图床节那处是同一类问题。
        """
        import tkinter.font as tkfont

        def size_of(font):
            # cget("font") 形如 "{PingFang SC} 11"；字号在末段
            return int(str(font).split()[-1])

        def body(win, box):
            texts = {}
            for w in _walk(win):
                if isinstance(w, tk.Label):
                    try:
                        txt = str(w.cget("text"))
                    except tk.TclError:
                        continue
                    texts[txt] = size_of(w.cget("font"))
            box["fonts"] = texts
            # 关掉弹窗，让 wait_window 返回（「取消」是自绘 RoundButton，不是 tk.Button）
            try:
                win.grab_release()
            except Exception:
                pass
            win.destroy()

        box = self._drive_dialog(body)
        self.app._ask_import_mode("2 个文件夹、7 条笔记", exported_at="2026-11-20 09:00")
        self.assertIsNone(box.get("driver_error"), str(box.get("driver_error")))
        fonts = box.get("fonts", {})
        keys = [k for k in fonts if k.startswith("导出时间：")
                or k.startswith("当前数据：") or k.startswith("覆盖 = ")]
        self.assertEqual(len(keys), 3, "三条说明文案没找全：%s" % list(fonts))
        self.assertEqual({fonts[k] for k in keys}, {11},
                         "导入弹窗说明字号不一致：%s" % {k[:8]: fonts[k] for k in keys})

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

    # ---------- 下载更新：点「后台下载」关窗，任务继续 ----------
    def _about(self):
        self._close_dialogs()
        self.app.open_about()
        self.root.update_idletasks()
        self.root.update()
        return self._dialogs()[-1]

    def _rbtn(self, win, text):
        """按文字找圆角按钮（RoundButton 没有 -text 选项，只能比 `.text`）。"""
        import widgets
        return next((w for w in _walk(win)
                     if isinstance(w, widgets.RoundButton) and w.text == text), None)

    def _label_texts(self, win):
        out = []
        for w in _walk(win):
            if isinstance(w, tk.Label):
                try:
                    out.append(str(w.cget("text")))
                except tk.TclError:
                    pass
        return out

    def _pump(self, cond, timeout=8.0):
        """转事件循环直到 cond() 为真。下载在子线程里，进度靠 after 轮询推进。"""
        import time
        end = time.time() + timeout
        while time.time() < end:
            self.root.update_idletasks()
            self.root.update()
            if cond():
                return True
            time.sleep(0.01)
        return False

    def test_background_download_keeps_running_after_closing_about(self):
        """点「后台下载」关掉关于窗，下载不能跟着没；再开关于要能看到进度。

        锁的是「任务挂在哪」：任务和窗口同生命周期的话，窗一销毁、轮询撞上
        `winfo_exists()` 就静默退出，用户等于点了「取消」却看不出来 —— 过半小时
        也不知道包到底下好没有。
        """
        import threading
        from unittest import mock
        import updater

        release = {"state": "new", "version": "v9.9.9", "html_url": "",
                   "assets": [{"name": "x.zip", "size": 1000,
                               "url": "http://127.0.0.1:1/x.zip", "digest": ""}]}
        done_info = {"version": "v9.9.9", "dir": "", "src": "", "exe": "",
                     "sha256": "", "verified": True}
        gate = threading.Event()

        def fake_prepare(res, progress):
            progress["done"], progress["total"] = 500, 1000
            gate.wait(10)                   # 模拟"还在下载"
            progress["done"] = 1000
            return dict(done_info)

        self.app._upd_job = None
        try:
            with mock.patch.object(updater, "check", side_effect=lambda: dict(release)), \
                    mock.patch.object(updater, "prepare", side_effect=fake_prepare), \
                    mock.patch.object(self.app, "_update_blocked_reason",
                                      return_value=""):
                dlg = self._about()
                self._rbtn(dlg, "检查更新")._on_release(None)
                self.assertTrue(self._pump(lambda: self._rbtn(dlg, "下载更新")),
                                "检查更新没给出「下载更新」")
                bg = self._rbtn(dlg, "后台下载")
                self.assertIsNotNone(bg, "关于窗里没有「后台下载」按钮")
                self.assertFalse(bg.winfo_manager(),
                                 "还没开始下载就把「后台下载」摆出来了")

                self._rbtn(dlg, "下载更新")._on_release(None)
                self.assertTrue(
                    self._pump(lambda: any("正在下载" in t
                                           for t in self._label_texts(dlg))),
                    "点了下载没有显示进度")
                self.assertTrue(bg.winfo_manager(), "下载中没出现「后台下载」")

                bg._on_release(None)
                self.assertFalse(dlg.winfo_exists(), "点「后台下载」没关掉关于窗")
                self.assertTrue(self.app._upd_downloading(),
                                "关窗把下载任务一起弄没了")

                dlg2 = self._about()
                self.assertTrue(
                    self._pump(lambda: any("正在下载" in t
                                           for t in self._label_texts(dlg2))),
                    "重新打开关于窗没有显示下载进度")
                bg2 = self._rbtn(dlg2, "后台下载")
                self.assertIsNotNone(bg2, "重新打开后「后台下载」按钮没了")
                self.assertTrue(bg2.winfo_manager(),
                                "重新打开后「后台下载」没摆出来")

                gate.set()
                self.assertTrue(self._pump(lambda: self._rbtn(dlg2, "立即更新")),
                                "下载完成没变成「立即更新」")
                self.assertEqual(self.app._upd_job["state"], "ready")
        finally:
            gate.set()
            self.app._upd_job = None
            self._close_dialogs()

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

    # ---------- 正文自动换行（工具栏第 15 枚，真窗口） ----------
    def _wrap_guard(self):
        """记下测试前的开关 / 窗口尺寸，返回 restore()。"""
        app = self.app
        was_on = app._editor_wrap_on()
        geo = self.root.geometry()

        def restore():
            app.set_editor_wrap(was_on)
            try:
                self.root.geometry(geo)
            except Exception:
                pass
            self.root.update_idletasks()
            self.root.update()
        return restore

    def _long_line(self, content=None):
        """临时把正文换成一行探针文字（缺省 400 字），返回 restore()。

        ⚠️ 期间 `app._loading = True`：`<<Modified>>` 一律不算用户编辑，否则这段
        探针文字会被去抖保存写进临时库，污染后面的字数 / 只读相关用例。
        """
        text = self.app.text
        state = str(text.cget("state"))
        body = text.get("1.0", "end-1c")
        loading = getattr(self.app, "_loading", False)
        self.app._loading = True
        text.config(state=tk.NORMAL)
        text.delete("1.0", "end")
        text.insert("1.0", "字" * 400 if content is None else content)
        self.root.update_idletasks()
        self.root.update()

        def restore():
            text.delete("1.0", "end")
            text.insert("1.0", body)
            text.config(state=state)
            self.app._loading = loading
            self.root.update_idletasks()
            self.root.update()
        return restore

    def test_editor_wrap_default_is_off_and_button_present(self):
        """首次默认不换行；工具栏第 15 枚是「自动换行」，且默认不带选中态。"""
        restore = self._wrap_guard()
        try:
            self.app.data["settings"].pop("editor_wrap", None)
            self.assertFalse(self.app._editor_wrap_on(), "缺省 / 脏数据应默认不换行")
            ids = [i[0] for i in self.app.TOOL_ITEMS]
            self.assertIn("wrap", ids, "工具栏没登记自动换行按钮")
            self.assertEqual(len(ids), 16, "工具栏应有 16 项（含插入分割线）")
            self.assertEqual(self.app.TOOL_SHORT_NAMES["wrap"], "自动换行")
            self.assertNotIn("wrap", self.app.READONLY_LOCKED,
                             "自动换行是视图开关，只读笔记里也该能点")
            btn = self.app._wrap_btn
            self.assertIsNotNone(btn, "工具栏没建出自动换行按钮")
            btn.set_selected(False)
            self.assertFalse(btn.selected, "默认态按钮不该亮着")
            self.app.data["settings"]["editor_wrap"] = "true"
            self.assertFalse(self.app._editor_wrap_on(),
                             "脏数据（非布尔值）不能被当成「已开启」")
            self.app.data["settings"].pop("editor_wrap", None)
        finally:
            restore()

    def test_editor_wrap_toggle_persists_and_syncs_button(self):
        """切换写进 settings，正文 / 按钮选中态 / 落库三处一致。"""
        import store
        restore = self._wrap_guard()
        try:
            self.app.set_editor_wrap(True)
            self.assertEqual(str(self.app.text.cget("wrap")), "char",
                             "开启后正文没切到按字符软换行（别改回 word：长段汉字"
                             "会被当成一个词整块推到下一行）")
            self.assertTrue(self.app._wrap_btn.selected, "开启后按钮没亮")
            self.assertIs(store.load_data()["settings"]["editor_wrap"], True,
                          "开启状态没落库，重启会丢")
            self.app.set_editor_wrap(False)
            self.assertEqual(str(self.app.text.cget("wrap")), "none",
                             "再次关闭没回到不换行")
            self.assertFalse(self.app._wrap_btn.selected, "关闭后按钮还亮着")
            self.assertIs(store.load_data()["settings"]["editor_wrap"], False,
                          "关闭状态没落库")
        finally:
            restore()

    def test_editor_wrap_button_click_toggles(self):
        """点一下开、再点一下关：按钮命令真的接到全局开关上。"""
        restore = self._wrap_guard()
        try:
            self.app.set_editor_wrap(False)
            btn = self.app._wrap_btn
            for expected in (True, False):
                btn.event_generate("<ButtonPress-1>", x=5, y=5)
                btn.event_generate("<ButtonRelease-1>", x=5, y=5)
                self.root.update_idletasks()
                self.assertIs(self.app._editor_wrap_on(), expected,
                              "点按钮没切到 %s" % ("开" if expected else "关"))
                self.assertIs(btn.selected, expected, "按钮选中态没跟上")
            self.assertEqual(str(self.app.text.cget("wrap")), "none")
        finally:
            restore()

    def test_editor_wrap_reflows_when_window_widens(self):
        """软换行随窗口宽度自动重排（不往正文插换行符）；不换行时才有横向细条。"""
        geo_restore = self._wrap_guard()
        body_restore = None
        try:
            body_restore = self._long_line()
            self.app.set_editor_wrap(False)
            self.root.update_idletasks()
            self.root.update()
            self.assertTrue(bool(self.app._hscroll.winfo_ismapped()),
                            "不换行 + 超长行应浮出横向细条")
            self.app.set_editor_wrap(True)
            self.root.update_idletasks()
            self.root.update()
            self.assertFalse(bool(self.app._hscroll.winfo_ismapped()),
                             "自动换行时不该还留着横向细条")

            def lines():
                n = self.app.text.count("1.0", "1.end", "update", "displaylines")
                return int(n[0]) if isinstance(n, (tuple, list)) else int(n)

            self.root.geometry("900x700")
            self.root.update_idletasks()
            self.root.update()
            narrow = lines()
            self.root.geometry("1500x900")
            self.root.update_idletasks()
            self.root.update()
            wide = lines()
            self.assertLess(wide, narrow,
                            "窗口拉宽后折行数没变少 —— 换行不会自动补充")
            # 注意用 end-1c（全文末尾那个 Tk 自动补的换行之前），别用 1.end-1c：
            # 后者会再退一个字符，400 字读成 399 字。
            self.assertEqual(self.app.text.get("1.0", "end-1c"), "字" * 400,
                             "软换行竟然往正文里插了换行符")
        finally:
            if body_restore is not None:
                body_restore()
            geo_restore()

    def test_wrap_icon_and_selected_state_repaint(self):
        """16px 图标用整数矩形拼；选中底色随主题走，不把建窗那刻的色烤死。"""
        import theme
        import widgets
        cv = tk.Canvas(self.root)
        ids = widgets._draw_icon(cv, "wrap", 8, 8, 16, "#333333")
        self.assertTrue(ids, "自动换行图标没画出图元")
        self.assertTrue(any(cv.type(i) == "rectangle" for i in ids),
                        "16px 图标应是整数矩形块面，别用细描线")
        cv.destroy()

        pal = self.app.pal
        btn = widgets.RoundButton(self.root, pal, "", icon="wrap", base=pal["editor"])
        normal = btn._cur_fill()
        btn.set_selected(True)
        self.assertTrue(btn.selected)
        self.assertNotEqual(btn._cur_fill(), normal, "选中态没有视觉反馈")
        dark = theme.build_palette("dark")
        selected_light = btn._cur_fill()
        btn.apply_theme(dark, base=dark["editor"])
        self.assertTrue(btn.selected, "换肤把选中态弄丢了")
        self.assertNotEqual(btn._cur_fill(), selected_light,
                            "换主题后选中底色停在旧配色上")
        btn.destroy()

    def test_wrap_icon_arrow_centered_on_bottom_line(self):
        """箭头必须压在底线的中线上。

        工具栏按钮高 29px → cy=14.5、图标框 y0=6.5：四个坐标各自 round() 时
        底线成 [16,18)（中线 17）、箭头尖成 18，整枚箭头偏低 1px（用户实报）。
        现在箭头中线从底线取整后的实际坐标现推，整 / .5 / 高 DPI 都要对齐。
        """
        import widgets

        def check(cv, label, tol=0):
            items = list(cv.find_all())
            rects = [cv.coords(i) for i in items if cv.type(i) == "rectangle"]
            # 圆角底那张 polygon 有几十个顶点，箭头只有 6 个坐标
            polys = [cv.coords(i) for i in items
                     if cv.type(i) == "polygon" and len(cv.coords(i)) == 6]
            self.assertEqual(len(rects), 3, "%s：横线/折角应是三块整数矩形" % label)
            self.assertEqual(len(polys), 1, "%s：应只有一枚三角箭头" % label)
            bot = max(rects, key=lambda c: c[1])       # y 起点最大 = 底线
            center = (bot[1] + bot[3]) / 2.0
            p = polys[0]
            self.assertLessEqual(abs(p[1] - center), tol,
                                 "%s：箭头尖没压在底线中线上" % label)
            self.assertLessEqual(abs((p[3] + p[5]) / 2.0 - center), tol,
                                 "%s：箭头上下不对称" % label)

        for cy, size in ((8, 16), (8.5, 16), (14.5, 16)):
            cv = tk.Canvas(self.root)
            widgets._draw_icon(cv, "wrap", 8, cy, size, "#333333")
            check(cv, "cy=%s s=%s" % (cy, size))
            cv.destroy()
        # 高 DPI 档允许半个像素的取整误差，但不能再出现整像素级错位
        cv = tk.Canvas(self.root)
        widgets._draw_icon(cv, "wrap", 10, 10, 20, "#333333")
        check(cv, "s=20", tol=0.5)
        cv.destroy()
        check(self.app._wrap_btn, "真实工具栏按钮")

    def test_editor_wrap_fills_line_for_repeated_paragraph(self):
        """同一段文字复制多遍：按字符折行要排满，不能在段间空格处另起一行。

        旧实现 wrap=WORD 只认空格断点，一长串汉字算一个「词」，第二遍会从
        默认行首重新起排、上一行留一大截空白（用户 2026-11-09 实报）。
        """
        guard = self._wrap_guard()
        body_restore = None
        try:
            self.app.set_editor_wrap(True)
            t = self.app.text

            def first_row_chars():
                info = t.dlineinfo("1.0")
                self.assertTrue(info, "首行量不到行盒")
                end = t.index("@100000,%d" % (info[1] + info[3] // 2))
                return int(end.split(".")[1])

            def row_starts(n=3):
                cur, out = "1.0", []
                for _ in range(n):
                    out.append(int(cur.split(".")[1]))
                    nxt = t.index("%s +1 display line" % cur)
                    if nxt == cur or t.compare(nxt, ">=", "1.end"):
                        break
                    cur = nxt
                return out

            # 对照组：纯汉字无空格，量出这一宽度到底能排多少字
            body_restore = self._long_line("字" * 400)
            self.root.update_idletasks()
            self.root.update()
            ctrl = first_row_chars()

            # 同一段 17 字复制 20 遍，段间一个半角空格。
            # ⚠️ 段长别取 20：本机一行恰好放得下 4 段（84 字），21 整除 84，
            # CHAR 折行也会落在空格上 —— 断言会假阳性（看着像退回 WORD）。
            # 17 字的段长（周期 18）与常见行宽不整除，才真能区分两种折行。
            para = "自动换行效果检查用的中文示例文字啊"
            self.assertEqual(len(para), 17)
            period = len(para) + 1
            body_restore()
            body_restore = None
            body_restore = self._long_line(" ".join([para] * 20))
            self.root.update_idletasks()
            self.root.update()
            char_len = first_row_chars()
            starts = row_starts()

            self.assertGreaterEqual(char_len, ctrl,
                                    "按字符折行没把第一行排满（退回到空格断点了）")
            self.assertNotEqual(starts[1] % period, 0,
                                "第二显示行从段首重新起排 —— 又回到 WORD 的老毛病")
            # 同一段内容在旧 WORD 模式下的首屏行数，用来锁住「确实不一样」
            t.configure(wrap=tk.WORD)
            self.root.update_idletasks()
            self.root.update()
            word_len = first_row_chars()
            self.app.set_editor_wrap(True)     # 复原为 CHAR，别把状态留给别的用例
            self.assertGreater(char_len, word_len,
                               "CHAR 与 WORD 折点相同，重复段落的空白没被修掉")
        finally:
            if body_restore is not None:
                body_restore()
            guard()

    # ---------- 「任务提醒」分类 / 汇总表（真窗口） ----------
    # 三条都复用本文件的 root / app；改过视图与正文的用例各自 try/finally 复原。

    def _alarm_restore(self, old_f, old_n, src=None, saved=None):
        """把真窗口状态放回用例开始前，避免污染后面的设置窗用例。"""
        if src is not None and saved is not None:
            src["content"] = saved
        self.app.data["current_folder"] = old_f
        self.app.data["current_note"] = old_n
        self.app.refresh_all()
        self.root.update_idletasks()

    def test_alarm_folder_order_and_index_protection(self):
        import store
        app = self.app
        fids = [f["id"] for f in app.data["folders"]]
        self.assertEqual(fids[:4], ["f_all", "f_star", "f_lock", "f_alarm"],
                         "「任务提醒」必须紧接「加密笔记」")
        idx = next(n for n in app.data["notes"]
                   if n["id"] == store.ALARM_INDEX_ID)
        self.assertEqual(idx["folder"], "f_alarm")
        self.assertEqual(app.title_of(idx), store.ALARM_INDEX_TITLE)
        self.assertTrue(app.is_alarm_index(idx))
        self.assertTrue(app.is_protected_note(idx), "汇总表要受系统笔记保护")
        self.assertGreater(len(app._folder_icons["f_alarm"].find_all()), 0,
                           "新分类的闹钟图标没画出来")

    def test_alarm_view_has_one_readonly_note(self):
        import store
        app = self.app
        old_f, old_n = app.data.get("current_folder"), app.data.get("current_note")
        try:
            app.select_folder("f_alarm")
            self.root.update_idletasks()
            self.assertEqual(list(app._rows.keys()), [store.ALARM_INDEX_ID],
                             "分类下必须只有「任务提醒列表」这一篇")
            self.assertEqual(app.mid_head.cget("text"), "任务提醒")
            app.select_note(store.ALARM_INDEX_ID)
            self.root.update_idletasks()
            self.assertTrue(getattr(app, "_readonly", False))
            self.assertEqual(getattr(app, "_ro_kind", None), "alarm_index")
            self.assertIn("自动汇总", app._ro_label.cget("text"))
        finally:
            self._alarm_restore(old_f, old_n)

    def test_alarm_inline_link_jumps_to_source(self):
        import datetime
        import store
        app = self.app
        old_f, old_n = app.data.get("current_folder"), app.data.get("current_note")
        src = next(n for n in app.data["notes"]
                   if not n.get("sample") and n["id"] != store.ALARM_INDEX_ID)
        saved = list(src.get("content") or [])
        try:
            at = (datetime.datetime.now() - datetime.timedelta(hours=1)).timestamp()
            src["content"] = saved + [
                ["alarm", {"id": "w_probe", "text": "窗口测试提醒", "at": at}]]
            app._refresh_alarm_index()
            app.select_folder("f_alarm")
            app.select_note(store.ALARM_INDEX_ID)
            self.root.update_idletasks()
            body = app.text.get("1.0", "end")
            self.assertIn("窗口测试提醒", body)
            # 四列：时间 / 任务 / 状态 / 来源。状态用 BMP 符号 + 文案
            rows = [ln.split("\t") for ln in body.split("\n")
                    if len(ln.split("\t")) == 4]
            probe = [r for r in rows if r[1] == "窗口测试提醒"]
            self.assertEqual(len(probe), 1, "没找到刚插入的那条提醒")
            self.assertEqual(probe[0][2], "○已过期", "时间已过 = 已过期（未确认）")
            ranges = app.text.tag_ranges("link")
            self.assertTrue(ranges, "汇总表里没有可点的内链")
            # 库里可能本来就有别的提醒（按时间排在前面），别假定第一条就是 src。
            # 只要能找到指向 src 的那条内链，跳转语义就成立。
            urls = [app._link_url_at(str(r)) for r in ranges[::2]]
            self.assertIn("note:" + src["id"], urls,
                          "汇总表里没有指向提醒所在笔记的内链")
            app._goto_note(src["id"])     # 模拟点 @源笔记
            self.root.update_idletasks()
            self.assertEqual(app.data.get("current_note"), src["id"],
                             "内链没跳到提醒所在的笔记")
        finally:
            self._alarm_restore(old_f, old_n, src, saved)

    def test_tab_fallback_detects_shift_and_keeps_plain_tab(self):
        """Shift 位兜底能退缩进；普通 Tab 仍照常插入字面制表符，只读也挡得住。"""
        app = self.app
        self.assertTrue(app.text.bind("<Tab>"), "编辑器没绑 <Tab> 兜底")
        restore = self._long_line("\t甲")
        try:
            t = app.text
            t.mark_set("insert", "1.1")
            app._editor_tab_maybe_shift(type("E", (), {"state": 0x1})())
            self.assertEqual(t.get("1.0", "end-1c"), "甲")
        finally:
            restore()

        restore = self._long_line("甲")
        try:
            t = app.text
            t.mark_set("insert", "1.0")
            # 不靠 event_generate：合成事件的类绑定在全量测试里偶发不执行。
            # 兜底handler 放行 + Text 类绑定还在，就等价于普通 Tab 照常插字符。
            self.assertIsNone(app._editor_tab_maybe_shift(
                type("E", (), {"state": 0})()), "普通 Tab 不该被 break")
            self.assertTrue(t.bind_class("Text", "<Tab>"), "Text 的 Tab 类绑定丢了")
            old_guard = app._refuse_readonly
            app._refuse_readonly = lambda: True
            try:
                self.assertEqual(app._editor_tab_maybe_shift(
                    type("E", (), {"state": 0})()), "break")
            finally:
                app._refuse_readonly = old_guard
        finally:
            restore()

    def test_shift_tab_deletes_one_indent_before_caret(self):
        """Shift+Tab 删光标前的一档缩进：Tab / 4 半角 / 2 全角空格；没缩进就不动。"""
        app = self.app
        self.assertTrue(app.text.bind("<Shift-Tab>"), "编辑器没绑 Shift+Tab")
        for body, insert, expect in (
            ("\t甲", "1.1", "甲"),
            ("\u3000\u3000甲", "1.2", "甲"),
            ("    甲", "1.5", "甲"),
            ("甲", "1.0", "甲"),
        ):
            restore = self._long_line(body)
            try:
                t = app.text
                t.mark_set("insert", insert)
                self.assertEqual(app._editor_shift_tab(None), "break")
                self.assertEqual(t.get("1.0", "end-1c"), expect)
                if expect != body:
                    t.edit_undo()
                    self.assertEqual(t.get("1.0", "end-1c"), body,
                                     "Shift+Tab 的删除没进撤销栈")
            finally:
                restore()

    def test_shift_tab_dedents_selected_lines(self):
        """多行选区按行首退一格；选到下一行行首时，那一行不算被选中。"""
        app = self.app
        restore = self._long_line("\t甲\n\u3000\u3000乙\n丙")
        try:
            t = app.text
            t.tag_remove("sel", "1.0", "end")
            t.tag_add("sel", "1.0", "3.0")
            self.assertEqual(app._editor_shift_tab(None), "break")
            self.assertEqual(t.get("1.0", "end-1c"), "甲\n乙\n丙")
            spans = t.tag_ranges("sel")
            self.assertEqual((str(spans[0]), str(spans[1])), ("1.0", "2.1"),
                             "退完缩进后选区没跟着收")
        finally:
            restore()

    def test_shift_tab_respects_readonly_and_outdents_at_line_start(self):
        """光标在行首也退行首缩进；只读笔记由统一守卫拦下。"""
        app = self.app
        restore = self._long_line("\t甲")
        try:
            t = app.text
            t.mark_set("insert", "1.0")
            app._editor_shift_tab(None)
            self.assertEqual(t.get("1.0", "end-1c"), "甲")
            old_guard = app._refuse_readonly
            app._refuse_readonly = lambda: True
            try:
                self.assertEqual(app._editor_shift_tab(None), "break")
                self.assertEqual(t.get("1.0", "end-1c"), "甲")
            finally:
                app._refuse_readonly = old_guard
        finally:
            restore()

    def test_editor_tab_width_is_two_hanzi(self):
        """正文 widget 级 Tab = 2 个汉字宽，且真的是按制表位停位而非默认 8 半角。"""
        import tkinter.font as tkfont
        import app_ui
        from theme import FONT
        app = self.app
        fam = getattr(app, "ui_font", None) or FONT[0]
        char_w = tkfont.Font(family=fam, size=app.body_size).measure("中")
        expected = app_ui.EDITOR_TAB_HANZI * char_w
        tabs = tuple(float(str(x)) for x in app.text.cget("tabs"))
        self.assertEqual(tabs, (float(expected),),
                         "正文 Tab 没配成 %d 个汉字宽" % app_ui.EDITOR_TAB_HANZI)
        # 真量一次落点：单独的 Text 用同一组 tabs，首档就应落在 2 汉字处。
        probe = tk.Text(self.root, font=(fam, app.body_size), tabs=tabs)
        probe.pack()
        try:
            probe.insert("1.0", "\tX")
            self.root.update_idletasks()
            self.root.update()
            x0 = probe.bbox("1.0")[0]
            xx = probe.bbox("1.1")[0]
            self.assertLessEqual(abs((xx - x0) - expected), 1,
                                 "制表符实际落点不是 2 汉字宽")
        finally:
            probe.destroy()

    def test_alarm_table_columns_are_pixel_aligned(self):
        """长短不一的任务名 / 中英混排 / 未设时间，四列起点必须逐行相同。

        对齐靠 **tag 级制表位** 覆盖正文的 widget 级 tabs；既不能被正文的
        2 汉字 Tab 带歪，也不许退回按字数补空格（中文 15px / 半角空格 8px，
        会累积误差）。
        """
        import datetime
        import store
        app = self.app
        old_f, old_n = app.data.get("current_folder"), app.data.get("current_note")
        src = next(n for n in app.data["notes"]
                   if not n.get("sample") and n["id"] != store.ALARM_INDEX_ID)
        saved = list(src.get("content") or [])
        try:
            now = datetime.datetime.now()
            src["content"] = saved + [
                ["alarm", {"id": "c1", "text": "短",
                           "at": (now - datetime.timedelta(hours=2)).timestamp()}],
                ["alarm", {"id": "c2", "text": "订会议室并通知全体参会同事",
                           "at": (now + datetime.timedelta(hours=1)).timestamp()}],
                ["alarm", {"id": "c3", "text": "buy milk and eggs", "at": 0}],
            ]
            app._refresh_alarm_index()
            app.select_folder("f_alarm")
            app.select_note(store.ALARM_INDEX_ID)
            self.root.update_idletasks()
            self.root.update()
            t = app.text
            self.assertTrue(t.cget("tabs"),
                            "正文 widget 级制表位不见了（应为 2 汉字宽）")
            self.assertTrue(t.tag_cget(store.ALARM_ROW_TAG, "tabs"),
                            "汇总表行没配上 tag 级制表位")
            last = int(t.index("end-1c").split(".")[0])
            import alarm as _alarm
            symbols = set(_alarm.STATE_SYMBOLS.values())
            col_x = {1: [], 2: [], 3: []}
            for ln in range(1, last + 1):
                cells = t.get("%d.0" % ln, "%d.end" % ln).split("\t")
                if len(cells) != 4 or not cells[2] or cells[2][0] not in symbols:
                    continue            # 只量数据行（表头那行状态列是字段名）
                pos = 0
                for i, cell in enumerate(cells):
                    if i:
                        pos += 1
                    if i in col_x:
                        bb = t.bbox("%d.%d" % (ln, pos))
                        self.assertTrue(bb, "第 %d 行量不到列坐标" % ln)
                        col_x[i].append(bb[0])
                    pos += len(cell)
            self.assertGreaterEqual(len(col_x[2]), 3,
                                    "没凑齐三条长短不一的提醒")
            for col, xs in col_x.items():
                self.assertEqual(len(set(xs)), 1,
                                 "第 %d 列没垂直对齐：%r" % (col + 1, xs))
        finally:
            self._alarm_restore(old_f, old_n, src, saved)

    def test_alarm_clipped_cell_shows_full_text_on_hover(self):
        """超宽格子截成 …，指针压上去要浮出全文，移开就收起。

        这条路只做展示：汇总表是只读报表，别做成链接那种可就地编辑的小窗。
        """
        import datetime
        import store
        app = self.app
        old_f, old_n = app.data.get("current_folder"), app.data.get("current_note")
        src = next(n for n in app.data["notes"]
                   if not n.get("sample") and n["id"] != store.ALARM_INDEX_ID)
        saved = list(src.get("content") or [])
        long_task = ("这是一个特别长的任务名称用来验证超过列宽之后会被截断"
                     "显示省略号并能悬浮看到全文")
        try:
            src["content"] = saved + [
                ["alarm", {"id": "h1", "text": long_task,
                           "at": datetime.datetime.now().timestamp()}]]
            app._refresh_alarm_index()
            app.select_folder("f_alarm")
            app.select_note(store.ALARM_INDEX_ID)
            self.root.update_idletasks()
            self.root.update()

            tag = next((k for k, v in app._alarm_cell_tips.items()
                        if v == long_task), None)
            self.assertIsNotNone(tag, "超长任务没进 tips，悬浮就没有全文可看")
            r = app.text.tag_ranges(tag)
            self.assertTrue(r, "格子标签没挂到正文上")
            bb = app.text.bbox(r[0])
            self.assertTrue(bb, "格子不在可视区，量不到坐标")
            app.text.event_generate("<Motion>", x=bb[0] + 4,
                                    y=bb[1] + bb[3] // 2)
            self.root.update()
            win = getattr(app, "_cell_tip_win", None)
            self.assertIsNotNone(win, "悬停在截断的格子上没有浮出全文")
            self.assertIn(long_task,
                          [w.cget("text") for w in win.winfo_children()],
                          "浮出来的不是原文全文")

            app.text.event_generate("<Motion>", x=2, y=2)
            self.root.update()
            self.assertIsNone(getattr(app, "_cell_tip_win", None),
                              "指针移开后浮窗还挂着")
        finally:
            app._hide_cell_tip()
            self._alarm_restore(old_f, old_n, src, saved)

    def test_alarm_tab_stops_follow_body_size(self):
        """字号一变，正文 Tab 与汇总表制表位都得按新字体重算。"""
        import store
        app = self.app
        t = app.text
        before = tuple(str(x) for x in t.tag_cget(store.ALARM_ROW_TAG, "tabs"))
        widget_before = float(str(t.cget("tabs")[0]))
        old = app.body_size
        try:
            app.body_size = old + 3
            app._config_tags()
            after = tuple(str(x) for x in t.tag_cget(store.ALARM_ROW_TAG, "tabs"))
            self.assertNotEqual(before, after, "改字号后制表位没跟着变")
            self.assertGreater(int(after[0]), int(before[0]))
            self.assertGreater(float(str(t.cget("tabs")[0])), widget_before,
                               "改字号后正文 Tab 没跟着变")
        finally:
            app.body_size = old
            app._config_tags()

    # ---------- 每篇独立恢复码（真窗口） ----------
    # 同样复用本文件的 root / app：模态窗靠 after 在嵌套事件循环里驱动，
    # 驱动函数抛异常也必须把窗口销毁，否则 wait_window() 会把测试挂死。

    def _drive_dialog(self, body, delay=80):
        box = {}
        def run():
            wins = self._dialogs()
            win = wins[-1] if wins else None
            try:
                body(win, box)
            except Exception as e:
                box["driver_error"] = e
                if win is not None:
                    try:
                        win.grab_release()
                        win.destroy()
                    except Exception:
                        pass
        self.root.after(delay, run)
        return box

    def _button(self, win, text):
        return next(w for w in _walk(win)
                    if isinstance(w, tk.Button) and str(w.cget("text")) == text)

    def test_recovery_dialog_gates_on_checkbox(self):
        import secret
        self._close_dialogs()
        app = self.app
        code = secret.new_recovery_code()
        def body(win, box):
            ok = self._button(win, "启用恢复")
            chk = next(w for w in _walk(win) if isinstance(w, tk.Checkbutton))
            box["initial"] = str(ok.cget("state"))
            box["code_shown"] = any(
                isinstance(w, tk.Label) and str(w.cget("text")) == code
                for w in _walk(win))
            chk.invoke()
            box["after"] = str(ok.cget("state"))
            ok.invoke()
        box = self._drive_dialog(body)
        result = app._show_recovery_code(code, secret.recovery_id(code), "季度预算")
        self.assertIsNone(box.get("driver_error"), str(box.get("driver_error")))
        self.assertEqual(box.get("initial"), "disabled",
                         "没勾「已抄下」就能启用，会留下一把没人知道的钥匙")
        self.assertEqual(box.get("after"), "normal")
        self.assertTrue(box.get("code_shown"), "恢复码没渲染出来")
        self.assertTrue(result)

    def test_recovery_dialog_can_be_declined(self):
        import secret
        self._close_dialogs()
        code = secret.new_recovery_code()
        box = self._drive_dialog(
            lambda win, b: self._button(win, "暂不启用").invoke())
        result = self.app._show_recovery_code(code, secret.recovery_id(code), "季度预算")
        self.assertIsNone(box.get("driver_error"), str(box.get("driver_error")))
        self.assertFalse(result, "「暂不启用」不该留下恢复槽")

    def test_recovery_input_rejects_bad_code(self):
        import secret
        self._close_dialogs()
        code = secret.new_recovery_code()
        def body(win, box):
            entry = next(w for w in _walk(win) if isinstance(w, tk.Entry))
            ok = self._button(win, "验证")
            entry.insert(0, "BAD")
            ok.invoke()
            box["still_open"] = bool(win.winfo_exists())
            box["error"] = any(
                isinstance(w, tk.Label) and "格式不对" in str(w.cget("text"))
                for w in _walk(win))
            entry.delete(0, tk.END)
            entry.insert(0, code)
            ok.invoke()
        box = self._drive_dialog(body)
        got = self.app._ask_recovery_code("用恢复码恢复", "季度预算",
                                          secret.recovery_id(code))
        self.assertIsNone(box.get("driver_error"), str(box.get("driver_error")))
        self.assertTrue(box.get("still_open"), "乱码不该直接关窗")
        self.assertTrue(box.get("error"), "没给出格式错误提示")
        self.assertEqual(got, secret.norm_recovery_code(code))

    def test_password_dialog_recovery_entry_is_opt_in(self):
        self._close_dialogs()
        app = self.app
        flag = {}
        def body(win, box):
            box["has_link"] = any(
                isinstance(w, tk.Button) and "恢复码" in str(w.cget("text"))
                for w in _walk(win))
            next(w for w in _walk(win) if isinstance(w, tk.Button)
                 and "恢复码" in str(w.cget("text"))).invoke()
        box = self._drive_dialog(body)
        self.assertIsNone(app._ask_password("已加密笔记", "请输入口令：", out=flag))
        self.assertIsNone(box.get("driver_error"), str(box.get("driver_error")))
        self.assertTrue(box.get("has_link"))
        self.assertTrue(flag.get("recovery"))

        self._close_dialogs()
        def body2(win, box):
            box["has_link"] = any(
                isinstance(w, tk.Button) and "恢复码" in str(w.cget("text"))
                for w in _walk(win))
            self._button(win, "取消").invoke()
        box2 = self._drive_dialog(body2)
        self.assertIsNone(app._ask_password("导出备份", "口令："))
        self.assertIsNone(box2.get("driver_error"), str(box2.get("driver_error")))
        self.assertFalse(box2.get("has_link"),
                         "不传 out 的调用不该凭空多一条恢复入口")

    def test_empty_password_shows_hint_instead_of_silent_return(self):
        """空口令点「确定」不能静默无反应：要显示提示并保持弹窗。"""
        self._close_dialogs()

        def body(win, box):
            # 空着口令直接点确定
            self._button(win, "确定").invoke()
            self.root.update_idletasks()
            box["still_open"] = bool(win.winfo_exists()) if win else False
            # 不查 winfo_ismapped：unittest 里弹窗未真实 map 时它恒为 0
            box["hint"] = any(
                isinstance(w, tk.Label) and "口令不能为空" in str(w.cget("text"))
                for w in _walk(win)) if win else False
            # 再取消退出，让 wait_window 返回
            self._button(win, "取消").invoke()

        box = self._drive_dialog(body)
        result = self.app._ask_password("解锁", "请输入口令：")
        self.assertIsNone(box.get("driver_error"), str(box.get("driver_error")))
        self.assertTrue(box.get("still_open"), "空口令点确定把窗口关掉了")
        self.assertTrue(box.get("hint"), "空口令没显示提示，用户以为卡死")
        self.assertIsNone(result)

    def test_recovery_dialog_copies_full_record(self):
        """复制按钮给出【笔记名称】_【凭证编号】_【恢复码】，不是光一段码。"""
        import secret
        self._close_dialogs()
        app = self.app
        code = secret.new_recovery_code()
        rid = secret.recovery_id(code)
        want = secret.format_recovery_record("季度预算", rid, code)
        def body(win, box):
            box["format_hint"] = any(
                isinstance(w, tk.Label) and "[笔记名称]" in str(w.cget("text"))
                for w in _walk(win))
            self._button(win, "复制恢复码").invoke()
            try:
                box["clip"] = win.clipboard_get()
            except Exception as e:
                box["clip_error"] = e
            self._button(win, "暂不启用").invoke()
        box = self._drive_dialog(body)
        result = app._show_recovery_code(code, rid, "季度预算")
        self.assertIsNone(box.get("driver_error"), str(box.get("driver_error")))
        self.assertFalse(result)
        self.assertTrue(box.get("format_hint"), "弹窗里没印出复制格式")
        self.assertIsNone(box.get("clip_error"), str(box.get("clip_error")))
        self.assertEqual(box.get("clip"), want)
        # 关窗后剪贴板必须仍是这份凭证串，不能回退成编辑区之前复制过的正文 ——
        # 这正是「恢复时偶尔粘到旧正文」的根因：之前写到瞬时 Toplevel，窗口一
        # 销毁，Tk 的延迟提供回调失效，剪贴板就退回上一个所有者（很可能就是正文）。
        try:
            self.assertEqual(app.root.clipboard_get(), want)
        except Exception as e:
            self.fail("关窗后剪贴板丢失 / 回退成旧内容：%s" % e)

    def test_recovery_input_accepts_full_record(self):
        """复制出来的整条凭证能直接粘进输入框，自动取出末尾那段恢复码。"""
        import secret
        self._close_dialogs()
        app = self.app
        code = secret.new_recovery_code()
        rid = secret.recovery_id(code)
        record = secret.format_recovery_record("季度预算_备份", rid, code)
        def body(win, box):
            entry = next(w for w in _walk(win) if isinstance(w, tk.Entry))
            entry.insert(0, record)
            self._button(win, "验证").invoke()
            box["still_open"] = bool(win.winfo_exists())
        box = self._drive_dialog(body)
        got = app._ask_recovery_code("用恢复码恢复", "季度预算_备份", rid)
        self.assertIsNone(box.get("driver_error"), str(box.get("driver_error")))
        self.assertFalse(box.get("still_open"), "整条凭证没被自动拆出恢复码")
        self.assertEqual(got, secret.norm_recovery_code(code))

    def test_readonly_bar_shows_recovery_entry_only_with_slot(self):
        """2026-11-09 返修：未解锁提示条上必须常驻一条「用恢复码」入口。

        用户设完恢复码、切走再点回来时看到的就是这条提示条；只有口令框里
        藏一条小字按钮的话，等于「没有输入恢复码的入口」。无槽时则要藏起来，
        不能给一个点进去必定失败的按钮。
        """
        import secret
        app = self.app
        old_note = app.data.get("current_note")
        old_ro = getattr(app, "_readonly", False)
        old_kind = getattr(app, "_ro_kind", None)
        blob, key = secret.seal_and_key("[]", "probe-pw")
        secret.seal_recovery(blob, key, secret.new_recovery_code())
        n = {"id": "n_rc_bar_probe", "folder": "f_work", "content": [],
             "sec": blob, "created": 1, "updated": 2}
        app.data["notes"].insert(0, n)
        try:
            app.data["current_note"] = n["id"]
            app._set_readonly(True, "locked")
            self.assertEqual(app._ro_recovery.winfo_manager(), "pack",
                             "带槽的加密笔记必须常驻恢复码入口")
            self.assertIn("恢复码", app._ro_label.cget("text"))
            self.assertIn("恢复码", str(app._ro_recovery.cget("text")))
            secret.drop_recovery(n["sec"])
            app._update_readonly_bar()
            self.assertEqual(app._ro_recovery.winfo_manager(), "",
                             "没有恢复槽时入口必须隐藏")
            self.assertNotIn("恢复码", app._ro_label.cget("text"))
        finally:
            app.data["notes"].remove(n)
            app.data["current_note"] = old_note
            app._ro_kind = old_kind
            app._set_readonly(old_ro, old_kind)
            app._update_readonly_bar()

    # ---------- 撤销 / 重做后的待办样式 ----------
    def _todo_line(self, checked=False):
        """临时放一行带完整标签的待办并清空撤销栈，返回 (Text, restore)。

        ⚠️ 共享 root 是 withdraw 状态，`event_generate` 送不到按键绑定；这里直接
        调动作方法，并显式生成 <<Undo>>（Tk 的 edit_undo 自己不发这个虚拟事件）。
        """
        import store
        from md_conv import TODO_OFF, TODO_ON, TODO_GAP, TODO_GAP_TAG
        app, text = self.app, self.app.text
        state = str(text.cget("state"))
        body = text.get("1.0", "end-1c")
        loading = getattr(app, "_loading", False)
        old_note = app.data.get("current_note")
        target = next((x for x in app.data["notes"]
                       if not x.get("sample") and x["id"] != store.ALARM_INDEX_ID),
                      None)
        self.assertIsNotNone(target, "临时库里没有可编辑的普通笔记")
        app.data["current_note"] = target["id"]
        app._loading = True
        text.config(state=tk.NORMAL)
        text.delete("1.0", "end")
        text.insert("1.0", "标题\n")
        mark = TODO_ON if checked else TODO_OFF
        text.insert("2.0", mark, ("todo_done" if checked else "todo",))
        text.insert("2.1", TODO_GAP, (TODO_GAP_TAG,))
        text.insert("2.2", "待办内容")
        if checked:
            app._sync_todo_done()
        text.edit_reset()
        text.mark_set("insert", "2.2")
        self.root.update_idletasks()
        self.root.update()

        def restore():
            text.delete("1.0", "end")
            text.insert("1.0", body)
            text.config(state=state)
            app._loading = loading
            app.data["current_note"] = old_note
            text.edit_reset()
            self.root.update()

        return text, restore

    def _run_undo(self):
        """模拟 Ctrl+Z：Tk 的 edit_undo 不生成 <<Undo>>，补发一次再跑 idle 回调。"""
        text = self.app.text
        self.root.update()
        text.edit_undo()
        text.event_generate("<<Undo>>")
        for _ in range(8):
            self.root.update()

    def test_undo_after_delete_line_restores_todo_tag(self):
        """Ctrl+Y 删整行待办再 Ctrl+Z：方框字号标签与点击绑定必须一起回来。"""
        from md_conv import TODO_OFF
        text, restore = self._todo_line()
        try:
            self.assertIn("todo", text.tag_names("2.0"))
            self.app.delete_line()          # Ctrl+Y 的实际动作
            self.root.update()
            self.assertNotIn(TODO_OFF, text.get("1.0", "end-1c"))
            self._run_undo()
            self.assertIn(TODO_OFF, text.get("1.0", "end-1c"))
            self.assertIn("todo", text.tag_names("2.0"),
                          "撤销后方框丢了 todo 标签 → 按正文大字号渲染")
            self.assertEqual(tuple(str(x) for x in text.tag_ranges("todo")),
                             ("2.0", "2.1"))
        finally:
            restore()

    def test_undo_after_delete_line_restores_checked_style(self):
        """已勾待办删行再撤销：todo_done 与 done 删除线都要跟着字符回来。"""
        from md_conv import TODO_ON
        text, restore = self._todo_line(checked=True)
        try:
            self.assertIn("todo_done", text.tag_names("2.0"))
            self.assertTrue(text.tag_ranges("done"))
            self.app.delete_line()          # Ctrl+Y 的实际动作
            self.root.update()
            self._run_undo()
            self.assertIn(TODO_ON, text.get("1.0", "end-1c"))
            self.assertIn("todo_done", text.tag_names("2.0"))
            self.assertTrue(text.tag_ranges("done"), "撤销后删除线没回来")
        finally:
            restore()

    def test_undo_toggle_restores_done(self):
        """撤销一次勾选：字符回到 ✅ 时删除线不能丢，也不能停在 ⬜ 的样式上。"""
        from md_conv import TODO_ON
        text, restore = self._todo_line(checked=True)
        try:
            self.app.toggle_todo_at("2.0")       # ✅ → ⬜，同步摘掉 done
            self.assertFalse(text.tag_ranges("done"))
            self._run_undo()
            self.assertIn(TODO_ON, text.get("1.0", "end-1c"))
            self.assertIn("todo_done", text.tag_names("2.0"))
            self.assertTrue(text.tag_ranges("done"), "撤销勾选后删除线没恢复")
        finally:
            restore()

    # ---------- 粘贴后的待办样式 ----------
    def _paste_bare_todo_line(self, checked):
        """放一行「剪贴板形态」的裸待办字符并清空撤销栈，返回 (Text, restore)。

        Tk 的 <<Copy>> 只带走字符、不带 tag，所以粘进来的 ✅/⬜ 是裸字符：
        没有 todo / todo_done / todo_gap / done 任何一个标签。这里不挂 tag，
        精确复现刚粘完的那一瞬。
        """
        import store
        from md_conv import TODO_OFF, TODO_ON, TODO_GAP
        app, text = self.app, self.app.text
        state = str(text.cget("state"))
        body = text.get("1.0", "end-1c")
        loading = getattr(app, "_loading", False)
        old_note = app.data.get("current_note")
        target = next((x for x in app.data["notes"]
                       if not x.get("sample") and x["id"] != store.ALARM_INDEX_ID),
                      None)
        self.assertIsNotNone(target, "临时库里没有可编辑的普通笔记")
        app.data["current_note"] = target["id"]
        app._loading = True
        text.config(state=tk.NORMAL)
        text.delete("1.0", "end")
        text.insert("1.0", "标题\n")
        text.insert("2.0", (TODO_ON if checked else TODO_OFF) + TODO_GAP + "买牛奶")
        text.edit_reset()
        self.root.update_idletasks()
        self.root.update()

        def restore():
            text.delete("1.0", "end")
            text.insert("1.0", body)
            text.config(state=state)
            app._loading = loading
            app.data["current_note"] = old_note
            text.edit_reset()
            self.root.update()

        return text, restore

    def _run_paste_fix(self):
        """模拟 <<Paste>> 的修复钩子并跑完 idle 回调（withdraw 下无法 generate 按键）。"""
        self.app._on_paste_fix_todo()
        for _ in range(8):
            self.root.update()

    def test_paste_checked_todo_restores_strike(self):
        """粘进来的已勾待办：todo_done 标签与 done 删除线都要跟着回来。

        `_on_paste_fix_todo` 只补方框标签，done 由 `_sync_todo_done` 统一刷 ——
        少了后者，粘进来的 ✅ 就是「绿框但没删除线」。
        """
        from md_conv import TODO_ON
        text, restore = self._paste_bare_todo_line(checked=True)
        try:
            self._run_paste_fix()
            self.assertIn(TODO_ON, text.get("2.0", "2.1"))
            self.assertIn("todo_done", text.tag_names("2.0"))
            self.assertTrue(text.tag_ranges("done"), "粘贴后删除线没回来")
        finally:
            restore()

    def test_paste_unchecked_todo_has_no_strike(self):
        """粘进来的未勾待办不能被误加删除线。"""
        from md_conv import TODO_OFF
        text, restore = self._paste_bare_todo_line(checked=False)
        try:
            self._run_paste_fix()
            self.assertIn("todo", text.tag_names("2.0"))
            self.assertFalse(text.tag_ranges("done"), "未勾待办不该有删除线")
        finally:
            restore()

    # ---------- 图片与图床：B 版文案 ----------
    def _pic_badge(self, dlg):
        """标题右侧那枚状态徽标（按「当前：」前缀认，不按位置猜）。"""
        badges = [w for w in _walk(dlg) if isinstance(w, tk.Label)
                  and str(w.cget("text")).startswith("当前：")]
        self.assertEqual(len(badges), 1, "图片与图床的状态徽标应当只有一枚")
        return badges[0]

    def _pic_placeholder(self, dlg):
        """路径输入框里的占位 Label；它的 master 就是那个 Entry。"""
        return next(w for w in _walk(dlg) if isinstance(w, tk.Label)
                    and str(w.cget("text")) == "留空即使用本机保存")

    def test_piclist_copy_b_version(self):
        """B 版文案：两种模式拆两段、副作用单独强调，界面不再出现 PicList.exe。"""
        dlg = self._open()
        texts = [str(w.cget("text")) for w in _walk(dlg) if isinstance(w, tk.Label)]
        self.assertIn("留空 PicList路径：图片保存在 data/media/，不联网。", texts)
        self.assertIn("填好路径：插图后自动上传图床，上传成功会删除本地副本。", texts)
        self.assertIn("PicList路径", texts)
        self.assertIn("路径只在本机生效，不随备份带走", texts)
        self.assertNotIn("PicList.exe", texts, "界面上还留着旧标签")

    def test_piclist_badge_and_placeholder_follow_path(self):
        """状态徽标与占位文字跟着路径有无切换；纯空格不算配了图床。"""
        settings = self.app.data.setdefault("settings", {})
        saved = settings.get("piclist_exe")
        settings["piclist_exe"] = ""
        try:
            dlg = self._open()
            badge = self._pic_badge(dlg)
            ph = self._pic_placeholder(dlg)
            entry = ph.master
            self.assertIsInstance(entry, tk.Entry)
            vname = str(entry.cget("textvariable"))

            self.assertEqual(str(badge.cget("text")), "当前：本机保存")
            self.assertTrue(ph.place_info(), "空路径时占位文字没显示")

            self.root.setvar(vname, "D:\\Tools\\PicList.exe")
            self.root.update_idletasks()
            self.assertEqual(str(badge.cget("text")), "当前：图床上传")
            self.assertEqual(ph.place_info(), {}, "填了路径占位文字没隐藏")

            self.root.setvar(vname, "   ")
            self.root.update_idletasks()
            self.assertEqual(str(badge.cget("text")), "当前：本机保存",
                             "纯空格不该算配了图床")
        finally:
            settings["piclist_exe"] = saved

    def test_piclist_badge_repaints_on_theme_switch(self):
        """徽标浅绿底是派生色：换肤要按新板重算，不能留旧主题色斑。"""
        from app_ui import _mix_hex
        settings = self.app.data.setdefault("settings", {})
        saved = settings.get("piclist_exe")
        settings["piclist_exe"] = ""
        try:
            dlg = self._open()
            badge = self._pic_badge(dlg)
            old_pal = dict(self.app.pal)
            self.assertEqual(
                str(badge.cget("bg")).lower(),
                _mix_hex(old_pal["editor"], old_pal["accent"], 0.12).lower())

            self._switch(dlg, "石墨灰")
            new_pal = dict(self.app.pal)
            self.assertEqual(
                str(badge.cget("bg")).lower(),
                _mix_hex(new_pal["editor"], new_pal["accent"], 0.12).lower(),
                "状态徽标底色没跟着换肤重算")
            self.assertEqual(str(badge.cget("fg")).lower(), new_pal["accent"].lower())
        finally:
            settings["piclist_exe"] = saved

    def test_piclist_entry_border_not_eaten_by_placeholder(self):
        """边框要走外围 highlight：内侧 SOLID 会被占位 Label 的白底盖掉一截。"""
        dlg = self._open()
        entry = self._pic_placeholder(dlg).master
        self.assertIsInstance(entry, tk.Entry)
        # bd=0 + highlightthickness>0：边框画在控件外，子 Label 盖不到
        self.assertEqual(str(entry.cget("bd")), "0",
                         "输入框用了内侧边框，占位底会盖掉下边框左半段")
        self.assertGreater(int(entry.cget("highlightthickness")), 0,
                           "外围高亮边框没开，输入框会没有可见边界")
        self.assertEqual(str(entry.cget("relief")).lower(), "flat")

    # ---------- 后台任务回传（「测试上传点了没效果」） ----------
    def _pump_async(self, seconds):
        """让后台线程 + 主线程轮询链真正跑完（轮询间隔 40ms）。"""
        import time
        t0 = time.time()
        while time.time() - t0 < seconds:
            self.root.update_idletasks()
            self.root.update()
            time.sleep(0.02)

    def test_img_async_result_reaches_main_thread(self):
        """_img_async 必须把结果交回主线程执行 done。

        旧实现在子线程直接 root.after()，被 Tk 以
        "main thread is not in main loop" 拒绝 → done 永不执行，正是
        「测试上传点了没反应、按钮卡禁用」的根因。
        """
        import threading
        got = {}
        main = threading.current_thread().name

        self.app._img_async(lambda: "OK",
                            lambda res: (got.update(res=res,
                                                    thread=threading.current_thread().name)))
        self._pump_async(0.6)
        self.assertEqual(got.get("res"), "OK", "后台结果没回传")
        self.assertEqual(got.get("thread"), main, "done 没在主线程执行")

    def test_img_async_exception_reaches_done(self):
        """work 抛异常也要按 (False, msg) 协议回传，不能把线程崩掉吞掉。"""
        got = {}

        def boom():
            raise RuntimeError("boom")

        self.app._img_async(boom, lambda res: got.update(res=res))
        self._pump_async(0.6)
        res = got.get("res")
        self.assertTrue(isinstance(res, tuple) and len(res) == 2)
        self.assertFalse(res[0])
        self.assertIn("boom", res[1])

    def test_test_upload_uses_app_icon(self):
        """测试上传的样图必须是应用自带的 icon/app_icon.png（不再是 1×1 临时图）。"""
        import piclist
        path = piclist._default_test_image()
        self.assertTrue(os.path.isfile(path), "测试图不存在：%s" % path)
        self.assertEqual(os.path.basename(path), "app_icon.png")
        self.assertGreater(os.path.getsize(path), 1024, "测试图不该是占位小图")

        # test_upload 必须把这张图交给 upload（不是现拼临时 PNG）
        seen = {}
        saved = piclist.upload
        try:
            piclist.upload = lambda s, p, timeout=None: seen.update(path=p) or "https://x/y.png"
            piclist.test_upload({"piclist_exe": r"C:\fake\PicList.exe"})
        finally:
            piclist.upload = saved
        self.assertEqual(seen.get("path"), path)

    def test_piclist_upload_uses_http_server_not_cli(self):
        """上传必须走内置 Server 的 HTTP 接口，不能再调 `PicList.exe upload`。

        PicList 是 Electron 应用，`PicList.exe upload <路径>` 会被当成 JS 入口
        解析、报 Cannot find module —— 这正是「没有返回图片链接：无输出」的根因。
        """
        import piclist
        s = {"piclist_exe": r"C:\fake\PicList.exe"}
        self.assertEqual(piclist.server_port({}), 36677)
        self.assertEqual(piclist.server_port({"piclist_port": "8080"}), 8080)
        self.assertEqual(piclist.server_port({"piclist_port": "bad"}), 36677)

        sent = {}

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"success":true,"result":["https://example.com/a.png"]}'

        def fake_urlopen(req, timeout=None):
            sent["url"] = req.full_url if hasattr(req, "full_url") else req
            sent["data"] = req.data
            return _Resp()

        saved_alive = piclist.server_alive
        saved_open = piclist.urllib.request.urlopen
        try:
            piclist.server_alive = lambda s, timeout=2.0: True
            piclist.urllib.request.urlopen = fake_urlopen
            url = piclist.test_upload(s)
            self.assertEqual(url, "https://example.com/a.png")
            self.assertIn("127.0.0.1:36677/upload", str(sent["url"]))
            self.assertIn(b"app_icon.png", sent["data"])
        finally:
            piclist.server_alive = saved_alive
            piclist.urllib.request.urlopen = saved_open

    def test_piclist_upload_server_down_gives_hint(self):
        """Server 没开时，错误信息要指明「内置 Server」，不能只说无输出。"""
        import piclist
        saved = piclist.server_alive
        try:
            piclist.server_alive = lambda s, timeout=2.0: False
            with self.assertRaises(piclist.UploadError) as ctx:
                piclist.test_upload({"piclist_exe": r"C:\fake\PicList.exe"})
            msg = str(ctx.exception)
            self.assertIn("内置 Server", msg)
            self.assertIn("36677", msg)
        finally:
            piclist.server_alive = saved

    def test_test_upload_button_shows_result_and_reenables(self):
        """端到端：配了路径点「测试上传」，要弹结果框、按钮要复位。"""
        import piclist
        saved_upload = piclist.test_upload
        saved_info = self.app._show_info
        saved_error = self.app._show_error
        settings = self.app.data.setdefault("settings", {})
        saved_exe = settings.get("piclist_exe")
        infos = []
        try:
            piclist.test_upload = lambda s, timeout=None: "https://example.com/x.png"
            self.app._show_info = lambda t, m: infos.append((t, m))
            self.app._show_error = lambda t, m: infos.append((t, m))
            settings["piclist_exe"] = r"C:\fake\PicList.exe"

            dlg = self._open()
            btn = next(w for w in _walk(dlg)
                       if getattr(w, "_text", "") == "测试上传")
            btn.command()
            self.assertFalse(btn._enabled, "上传中按钮应禁用")
            self._pump_async(0.6)
            self.assertTrue(infos, "测试上传没弹结果提示")
            self.assertEqual(infos[-1][0], "测试上传")
            self.assertIn("https://example.com/x.png", infos[-1][1])
            self.assertTrue(btn._enabled, "结果回来后按钮没复位")
        finally:
            piclist.test_upload = saved_upload
            self.app._show_info = saved_info
            self.app._show_error = saved_error
            settings["piclist_exe"] = saved_exe


    # ---------- 整行图片语法 / 点图改地址（复用本文件的 app 与 root） ----------

    def _img_scratch_note(self, with_image=True):
        """临时笔记 + 临时 media 目录，返回 (note, block, cleanup)。

        ⚠️ `store.BASE` 必须临时指到空目录：渲染走 `imgstore.src_path()`，
        它是按 `store.BASE` 找文件的；不换就是往用户真实 `data/media` 里写测试图。
        """
        import store as _store
        import imgstore as _imgstore
        base = tempfile.mkdtemp(prefix="sj_img_ui_")
        old_base = _store.BASE
        _store.BASE = base
        block = _imgstore.make_block(_mk_png(40, 30, (200, 60, 60)), alt="旧图")
        content = [["t", "标题\n", ["title"]]]
        if with_image:
            content.append(block)
            content.append(["t", "\n尾巴", []])
        note = {"id": "n_img_ui", "folder": "f_all", "content": content}
        data = self.app.data
        old_cur = data.get("current_note")
        data["notes"].append(note)
        data["current_note"] = note["id"]
        self.app.select_note(note["id"])
        self.root.update_idletasks()
        self.root.update()

        def cleanup():
            data["notes"] = [n for n in data["notes"] if n is not note]
            data["current_note"] = old_cur
            _store.BASE = old_base
            if old_cur:
                try:
                    self.app.select_note(old_cur)
                except Exception:
                    pass
            shutil.rmtree(base, ignore_errors=True)

        return note, block, cleanup

    def _imgs(self):
        return [n for n in self.app.text.image_names() if n.startswith("img:")]

    def test_md_image_input_parsing(self):
        """地址输入框的解析口径：Markdown / 裸地址 / 空 / 残句。"""
        import imgstore
        self.assertEqual(imgstore.parse_md_image_input("![图](media/a.png)"),
                         ("图", "media/a.png"))
        self.assertEqual(imgstore.parse_md_image_input("  https://a/b.png  ", old_alt="旧"),
                         ("旧", "https://a/b.png"))
        self.assertEqual(imgstore.parse_md_image_input("<C:\\a b\\c.png>", old_alt="x"),
                         ("x", "C:\\a b\\c.png"))
        self.assertIsNone(imgstore.parse_md_image_input(""))
        self.assertIsNone(imgstore.parse_md_image_input("   "))
        # 只写了 ![]() 这种空地址，别把整串当路径丢给下载器
        self.assertIsNone(imgstore.parse_md_image_input("![]()"))

    def test_md_image_line_renders_without_enter(self):
        """敲完 `)` 与粘贴之后，整行 ![alt](src) 都要直接成图（不必回车）。"""
        import imgstore
        note, block, cleanup = self._img_scratch_note(with_image=False)
        try:
            src = block[1]["src"]
            # ⚠️ 夹具里这张图没被任何正文引用，载入笔记时 _img_gc 会把它当孤儿删掉。
            # 这里先把文件放回去，模拟真实场景：笔记里本来就有这张图，用户把它的
            # Markdown 复制出来再粘一次 —— 那时文件当然还在。
            with open(imgstore.src_path(src), "wb") as f:
                f.write(_mk_png(40, 30, (200, 60, 60)))
            self.assertFalse(self._imgs())
            # ① 手打整行语法，敲完 `)`（_md_after_paren 的 idle 链）
            self.app.text.insert("2.0", "![手打](%s)" % src)
            self.app.text.mark_set("insert", "2.end")
            self.app._md_after_paren()
            self.assertTrue(self._pump(lambda: len(self._imgs()) == 1, 5),
                            "敲完 ) 没有把整行图片语法变成图")
            self.assertTrue([it for it in self.app.serialize() if it[0] == "img"])

            # ② 粘贴整行语法（不带换行）
            self.root.clipboard_clear()
            self.root.clipboard_append("![粘贴](%s)" % src)
            self.app.text.mark_set("insert", "3.0")
            self.app.text.event_generate("<<Paste>>")
            self.assertTrue(self._pump(lambda: len(self._imgs()) == 2, 5),
                            "粘贴整行图片语法没有变成图")
            self.assertNotIn("![](", self.app.text.get("1.0", "end"))

            # ③ 普通文字粘贴不能被吞、也不该被当成图
            self.root.clipboard_clear()
            self.root.clipboard_append("普通一段话")
            self.app.text.mark_set("insert", "4.0")
            self.app.text.event_generate("<<Paste>>")
            self._pump(lambda: "普通一段话" in self.app.text.get("1.0", "end"), 5)
            self.assertIn("普通一段话", self.app.text.get("1.0", "end"))
            self.assertEqual(len(self._imgs()), 2)
        finally:
            cleanup()

    def test_image_source_dialog_swaps(self):
        """改地址就地换图并落库（入口从「点击」挪到右键菜单，逻辑不变）。"""
        import imgstore
        note, block, cleanup = self._img_scratch_note()
        try:
            name = "img:" + block[1]["id"]
            old_md = imgstore.format_md_image(block)   # 弹窗里应该预填这个
            bb = self.app.text.bbox(name)
            self.assertTrue(bb, "临时笔记里的图没渲染出来")
            cx, cy = bb[0] + bb[2] // 2, bb[1] + bb[3] // 2
            self.assertEqual(self.app._img_at_point(cx, cy), name)
            self.assertIsNone(self.app._img_at_point(2, 2), "点空白处不应算点中图片")

            new_src = imgstore.make_block(_mk_png(20, 50, (0, 120, 200)),
                                          alt="新图")[1]["src"]
            out = {}

            def inspect():
                dlg = self._dialogs()[-1]
                out["dlg"] = dlg
                entry = next(w for w in _walk(dlg) if isinstance(w, tk.Entry))
                out["md"] = entry.get()
                entry.delete(0, tk.END)
                entry.insert(0, "![新图](%s)" % new_src)
                next(w for w in _walk(dlg)
                     if isinstance(w, tk.Button)
                     and str(w.cget("text")) == "确定").invoke()

            def verify():
                dlg = out.get("dlg")
                out["closed"] = bool(dlg is not None and not dlg.winfo_exists())

            self._close_dialogs()
            self.root.after(400, inspect)
            self.root.after(150, lambda: self.app._img_source_dialog(name))
            self.root.after(1600, verify)
            self.assertTrue(self._pump(lambda: out.get("closed"), 8),
                            "换完地址对话框没有关闭")
            self.assertEqual(out.get("md"), old_md,
                             "弹窗里预填的不是这张图原来的 Markdown")
            self.assertEqual(block[1]["src"], new_src)
            self.assertEqual(block[1]["alt"], "新图")
            self.assertEqual((block[1]["w"], block[1]["h"]), (20, 50))
            self.assertTrue(self.app.text.bbox(name), "换地址后正文里没有图")
            saved = [it for it in note["content"] if it[0] == "img"]
            self.assertTrue(saved and saved[0][1]["src"] == new_src, "新地址没落到数据块")
        finally:
            cleanup()

    # ---------- 图片：悬停看源 / 右键菜单 / 上传图床 ----------
    def test_image_hover_shows_markdown(self):
        """悬停图片浮出 Markdown 源（含尺寸与操作提示），指针移开即收。

        用户定的交互：点击**不再**弹窗，改看浮层；点不动才是原本写正文的体验。
        """
        import imgstore
        note, block, cleanup = self._img_scratch_note()
        try:
            name = "img:" + block[1]["id"]
            bb = self.app.text.bbox(name)
            self.assertTrue(bb, "临时笔记里的图没渲染出来")
            self.app.text.event_generate("<Motion>", x=bb[0] + bb[2] // 2,
                                         y=bb[1] + bb[3] // 2)
            self.assertTrue(self._pump(lambda: self.app._img_hover_win is not None, 3),
                            "悬停在图片上没有浮出 Markdown")
            texts = [w.cget("text") for w in _walk(self.app._img_hover_win)
                     if isinstance(w, tk.Label)]
            self.assertIn(imgstore.format_md_image(block), texts,
                          "浮层里不是这张图的 Markdown：%s" % texts)
            self.assertTrue(any("右键可更换地址" in t for t in texts),
                            "浮层没告诉用户改地址的入口在哪：%s" % texts)

            self.app.text.event_generate("<Motion>", x=2, y=2)
            self.root.update()
            self.assertIsNone(self.app._img_hover_win, "指针移开后浮层还挂着")
        finally:
            self.app._img_hover_leave()
            cleanup()

    def test_image_click_does_not_pop_dialog(self):
        """点击图片不许再弹「图片地址」窗（浮层才是看源的入口）。"""
        note, block, cleanup = self._img_scratch_note()
        try:
            name = "img:" + block[1]["id"]
            bb = self.app.text.bbox(name)
            calls = []
            real = self.app._img_source_dialog
            self.app._img_source_dialog = lambda n: calls.append(n)
            try:
                self.app.text.event_generate("<Button-1>", x=bb[0] + bb[2] // 2,
                                             y=bb[1] + bb[3] // 2)
                self._pump_async(0.3)
            finally:
                self.app._img_source_dialog = real
            self.assertEqual(calls, [], "点击图片仍然弹了地址窗")
        finally:
            cleanup()

    def _img_menu_labels(self, name, settings_patch=None, remote=False):
        """跑一遍 _img_popup，把菜单项 (label, enabled) 捞出来（不真弹窗）。"""
        import app_actions
        from unittest import mock
        settings = self.app.data.setdefault("settings", {})
        old = {k: settings.get(k) for k in ("piclist_exe",)}
        if settings_patch:
            settings.update(settings_patch)
        ev = type("E", (), {"x_root": 100, "y_root": 100})()
        try:
            with mock.patch.object(app_actions, "PopupMenu") as PM:
                self.app._img_popup(ev, name)
                out = []
                for c in PM.return_value.add.call_args_list:
                    out.append((c[0][0], c[1].get("enabled", True)))
                return out
        finally:
            for k, v in old.items():
                if v is None:
                    settings.pop(k, None)
                else:
                    settings[k] = v

    def test_image_right_click_menu_local(self):
        """本地图右键：上传到图床 / 复制本地路径 / 更换地址。"""
        note, block, cleanup = self._img_scratch_note()
        try:
            name = "img:" + block[1]["id"]
            labels = self._img_menu_labels(name, {"piclist_exe": "D:/PicList/PicList.exe"})
            texts = [lbl for lbl, _ok in labels]
            self.assertIn("上传到图床", texts)
            self.assertIn("复制本地路径", texts)
            self.assertTrue(any(t.startswith("更换图片地址") for t in texts), texts)
            ok = dict(labels)["上传到图床"]
            self.assertTrue(ok, "配好 PicList 时「上传到图床」不该置灰")
        finally:
            cleanup()

    def test_image_right_click_menu_upload_reason(self):
        """没配 PicList / 只读笔记：「上传到图床」置灰，且把原因写进 label。

        一项点不动的菜单用户会以为程序坏了，所以原因必须在菜单上看得见。
        """
        note, block, cleanup = self._img_scratch_note()
        try:
            name = "img:" + block[1]["id"]
            labels = self._img_menu_labels(name, {"piclist_exe": ""})
            zero = labels[0]
            self.assertIn("PicList路径", zero[0], "置灰时没说清为什么：%s" % (zero,))
            self.assertFalse(zero[1], "没配 PicList 还能点上传")
        finally:
            cleanup()

    def test_image_right_click_menu_remote(self):
        """远程图右键：给复制地址，不出现「上传到图床」（它已经在上头了）。"""
        note, block, cleanup = self._img_scratch_note()
        try:
            block[1]["src"] = "https://cdn.test/a.png"
            name = "img:" + block[1]["id"]
            rec = self.app._imgs_get().get(name)
            if isinstance(rec, dict):
                rec["block"] = block          # 让菜单按远程图的口径判断
            labels = self._img_menu_labels(name, {"piclist_exe": "D:/PicList/PicList.exe"})
            texts = [lbl for lbl, _ok in labels]
            self.assertIn("复制图片地址", texts)
            self.assertFalse(any("上传到图床" in t for t in texts), texts)
        finally:
            cleanup()

    def test_image_upload_local_switches_src(self):
        """右键上传：成功后 src 换成 URL、本地文件删掉（与插入时自动上传同一落点）。"""
        import imgstore
        import piclist
        from unittest import mock
        note, block, cleanup = self._img_scratch_note()
        try:
            name = "img:" + block[1]["id"]
            old_src = block[1]["src"]
            path = imgstore.src_path(old_src)
            self.assertTrue(os.path.isfile(path), "夹具图没落到临时 media 目录")
            block[1]["src"] = old_src
            rec = self.app._imgs_get().get(name)
            if isinstance(rec, dict):
                rec["block"] = [block[0], dict(block[1])]

            settings = self.app.data.setdefault("settings", {})
            old_exe = settings.get("piclist_exe")
            settings["piclist_exe"] = "D:/PicList/PicList.exe"
            url = "https://cdn.test/uploaded.png"
            info, err = [], []
            real_info, real_err = self.app._show_info, self.app._show_alert
            self.app._show_info = lambda *a, **k: info.append(a)
            self.app._show_alert = lambda *a, **k: err.append(a)
            try:
                with mock.patch.object(piclist, "upload", return_value=url):
                    self.app._img_upload_local(name)
                    self._pump_async(1.2)
            finally:
                self.app._show_info, self.app._show_alert = real_info, real_err
                if old_exe is None:
                    settings.pop("piclist_exe", None)
                else:
                    settings["piclist_exe"] = old_exe

            self.assertEqual(err, [], "上传成功却弹了失败框：%s" % (err,))
            self.assertTrue(info, "上传成功没有任何提示")
            saved = [it for it in note["content"] if it[0] == "img"][0]
            self.assertEqual(saved[1]["src"], url, "src 没换成图床 URL")
            self.assertFalse(os.path.isfile(path), "上传后本地文件没删，会占着 media/")
        finally:
            cleanup()


    # ---------- 图床图：https 降级 / 后台下载 ----------
    def _fake_resp(self, data):
        class _R(object):
            def read(self_inner):
                return data

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False
        return _R()

    def test_remote_fetch_falls_back_to_http(self):
        """https 握手被掐（WinError 10054 / EOF in violation）→ 自动换 http 重试。

        实测环境里某图床就是这样：TCP 443 连得上、TLS 被重置，http 明文反而正常。
        """
        import urllib.request
        import imgstore
        png = _mk_png(4, 4, (0, 0, 255))
        real = urllib.request.urlopen
        calls = []

        def fake(req, timeout=None):
            u = getattr(req, "full_url", req)
            calls.append(u)
            if u.startswith("https://"):
                raise OSError("EOF occurred in violation of protocol (_ssl.c:997)")
            return self._fake_resp(png)

        urllib.request.urlopen = fake
        try:
            self.assertEqual(imgstore.fetch_bytes("https://img.test/a.png"), png)
            self.assertEqual(calls, ["https://img.test/a.png",
                                     "http://img.test/a.png"])
        finally:
            urllib.request.urlopen = real

    def test_remote_fetch_detects_block_page(self):
        """返回 200 的 HTML 拦截页不能被当成图片；换 http 拿到真图就用。

        只按状态码判断会一路走到 normalize_image 才报「cannot identify image
        file」—— 用户看到这句话完全没法自查。
        """
        import urllib.request
        import imgstore
        png = _mk_png(4, 4, (0, 255, 0))
        html = b"<html><title>URL\xe8\xbf\x87\xe6\xbb\xa4</title></html>"
        real = urllib.request.urlopen

        def fake(req, timeout=None):
            u = getattr(req, "full_url", req)
            return self._fake_resp(html if u.startswith("https://") else png)

        urllib.request.urlopen = fake
        try:
            self.assertEqual(imgstore.fetch_bytes("https://img.test/b.png"), png)
        finally:
            urllib.request.urlopen = real

        # 两个候选都是网页 → 文案要说清「不是图片」，别甩 SSL 报错
        urllib.request.urlopen = lambda req, timeout=None: self._fake_resp(html)
        try:
            with self.assertRaises(imgstore.UnsupportedImage) as ctx:
                imgstore.fetch_bytes("https://img.test/c.png")
            msg = str(ctx.exception)
            self.assertIn("网页而不是图片", msg)
            self.assertIn("网络拦截", msg)
        finally:
            urllib.request.urlopen = real

    def test_proxy_url_shape(self):
        """转存地址：host/path/query 整段交给它，query 里的 & 要编码，非 http 给空。"""
        import imgstore
        u = "https://files.seeusercontent.com/2026/11/18/Jkf2/a.png"
        self.assertEqual(
            imgstore._proxy_url(u),
            "https://images.weserv.nl/?url=files.seeusercontent.com/2026/11/18/Jkf2/a.png")
        self.assertEqual(
            imgstore._proxy_url("http://h/p.png?w=1&h=2"),
            "https://images.weserv.nl/?url=h/p.png?w%3D1%26h%3D2")
        self.assertEqual(imgstore._proxy_url("media/a.png"), "")
        self.assertEqual(imgstore._proxy_url(""), "")

    def test_remote_fetch_falls_back_to_image_proxy(self):
        """直连 https 被 RST、明文 http 被网关换成拦截页 → 第三条走公开图片转存。

        实测 files.seeusercontent.com 就是这样：按域名拦死，改协议、改 header 都
        没用，只能让第三方去取。直连能取到图时**绝不**经过它（那会把内网图床
        地址漏给外面），所以顺带锁住「成功即停」。
        """
        import urllib.request
        import imgstore
        png = _mk_png(8, 6, (30, 30, 200))
        html = b"<html><title>URL\xe8\xbf\x87\xe6\xbb\xa4</title></html>"
        real = urllib.request.urlopen
        calls = []

        def fake(req, timeout=None):
            u = getattr(req, "full_url", req)
            calls.append(u)
            if u.startswith("https://images.weserv.nl/"):
                return self._fake_resp(png)
            if u.startswith("https://"):
                raise OSError("Connection reset by peer")
            return self._fake_resp(html)

        urllib.request.urlopen = fake
        try:
            src = "https://files.seeusercontent.com/x/a.png"
            self.assertEqual(imgstore.fetch_bytes(src), png)
            self.assertEqual(len(calls), 3)
            self.assertEqual(calls[0], src)
            self.assertEqual(calls[1], "http://files.seeusercontent.com/x/a.png")
            self.assertTrue(calls[2].startswith("https://images.weserv.nl/?url="))
            self.assertIn("files.seeusercontent.com/x/a.png", calls[2])

            # 直连就通 → 不许多发一个请求（内网/私有图床不外泄）
            calls[:] = []
            urllib.request.urlopen = lambda req, timeout=None: (
                calls.append(getattr(req, "full_url", req)) or self._fake_resp(png))
            imgstore.fetch_bytes(src)
            self.assertEqual(calls, [src])
        finally:
            urllib.request.urlopen = real

    def test_remote_fetch_all_paths_fail_message(self):
        """三条路都死 → 明说「试过公开图片代理」，并给出换图床/存本机的出路。"""
        import urllib.request
        import imgstore
        real = urllib.request.urlopen

        def fake(req, timeout=None):
            raise OSError("Connection reset by peer")

        urllib.request.urlopen = fake
        try:
            with self.assertRaises(Exception) as ctx:
                imgstore.fetch_bytes("https://img.test/d.png")
            msg = str(ctx.exception)
            self.assertIn("公开图片代理", msg)
            self.assertIn("换", msg)
        finally:
            urllib.request.urlopen = real

        urllib.request.urlopen = lambda req, timeout=None: self._fake_resp(b"<html>x</html>")
        try:
            with self.assertRaises(imgstore.UnsupportedImage) as ctx:
                imgstore.fetch_bytes("https://img.test/e.png")
            self.assertIn("公开图片代理", str(ctx.exception))
        finally:
            urllib.request.urlopen = real

    def test_remote_image_line_is_async(self):
        """整行远程图必须**立刻**交出占位、后台下载，回来再换图。

        以前是主线程同步下载：慢网时整窗假死，https 被掐还要白等 20 秒超时。
        同时锁住两件事：src 保留 URL（不复制成本地副本）、图真的出来了。
        """
        import time
        note, _block, cleanup = self._img_scratch_note(with_image=False)
        real_fetch = self.app._img_cache_fetch
        try:
            url = "https://img.test/async.png"
            png_path = os.path.join(tempfile.mkdtemp(prefix="sj_async_"), "a.png")
            with open(png_path, "wb") as f:
                f.write(_mk_png(12, 9, (10, 120, 200)))
            self.app._img_cache_fetch = lambda u: (png_path, None)

            self.app.text.insert("2.0", "![远端](%s)" % url)
            self.app.text.mark_set("insert", "2.end")
            t0 = time.time()
            self.app._img_line_break("2", url, "远端")
            cost = time.time() - t0
            self.assertLess(cost, 0.5, "整行远程图还在主线程上同步下载（%.2fs）" % cost)
            self.assertEqual(self.app.text.get("2.0", "2.end"),
                             self.app.IMG_LOADING_TEXT, "没先放「加载中」占位")

            self._pump_async(0.8)
            self.assertTrue(self._imgs(), "后台下载回来后没有出图")
            blocks = [it for it in self.app.serialize() if it[0] == "img"]
            self.assertTrue(blocks, "图片数据块没进 serialize")
            self.assertEqual(blocks[0][1]["src"], url, "远程图不该复制成本地 media 副本")
        finally:
            self.app._img_cache_fetch = real_fetch
            cleanup()

    def test_remote_image_failure_restores_markdown(self):
        """下载失败：正文要退回原文，并把真实原因（不是「无输出」）报出来。"""
        note, _block, cleanup = self._img_scratch_note(with_image=False)
        real_fetch = self.app._img_cache_fetch
        real_err = self.app._show_error
        seen = []
        try:
            url = "https://img.test/bad.png"
            reason = "图床返回的是网页而不是图片：http://img.test/bad.png"
            self.app._img_cache_fetch = lambda u: (None, reason)
            self.app._show_error = lambda t, m: seen.append((t, m))

            self.app.text.insert("2.0", "![坏](%s)" % url)
            self.app.text.mark_set("insert", "2.end")
            self.app._img_line_break("2", url, "坏")
            self.assertEqual(self.app.text.get("2.0", "2.end"),
                             self.app.IMG_LOADING_TEXT)
            self._pump_async(0.8)

            self.assertEqual(self.app.text.get("2.0", "2.end"),
                             "![坏](%s)" % url, "失败后原文没回来")
            self.assertEqual(self._imgs(), [])
            self.assertTrue(seen, "失败没有任何提示")
            self.assertIn("网页而不是图片", seen[0][1], "没把真实原因告诉用户")
        finally:
            self.app._img_cache_fetch = real_fetch
            self.app._show_error = real_err
            cleanup()

    def test_remote_image_render_uses_placeholder(self):
        """打开笔记时远程图先放灰块占位（不联网、不卡打开），失败也不丢数据块。"""
        import time
        note, _block, cleanup = self._img_scratch_note(with_image=False)
        real_fetch = self.app._img_cache_fetch
        try:
            url = "https://img.test/placeholder.png"
            self.app._img_cache_fetch = lambda u: (None, "断网")
            content = [["t", "标题\n", ["title"]],
                       ["img", {"id": "i_ph", "src": url, "w": 12, "h": 9, "alt": ""}]]
            t0 = time.time()
            self.app._render_content(content)
            cost = time.time() - t0
            self.assertLess(cost, 0.5, "打开笔记还在同步下远程图（%.2fs）" % cost)
            rec = self.app._imgs_get().get("img:i_ph")
            self.assertIsNotNone(rec, "远程图没有先放占位块")
            self.assertNotEqual((rec["photo"].width(), rec["photo"].height()), (12, 9),
                                "占位块直接用真图尺寸了")

            self._pump_async(0.8)
            # ⚠️ 失败后**不能**把图元删掉：它是数据块在 serialize() 里的唯一凭据，
            # 删了下次落库就把这张图从正文里永久抹掉（一次断网 = 丢图）。
            self.assertIn("img:i_ph", self.app.text.image_names(),
                          "下载失败后图元被删了，数据块会跟着丢")
            blocks = [it for it in self.app.serialize() if it[0] == "img"]
            self.assertTrue(blocks, "占位撤掉了但数据块也跟着没了（等于丢内容）")
            self.assertEqual(blocks[0][1]["src"], url)
        finally:
            self.app._img_cache_fetch = real_fetch
            cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=2)
