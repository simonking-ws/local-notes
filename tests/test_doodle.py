# -*- coding: utf-8 -*-
"""涂鸦标注（方案 C 图标工具条 + 序号）回归测试。

在真实 Tk 里起一个最小宿主，锁住这几件容易回退的事：

  A. 进入锁正文、退出按**快照**逐项还原（state / 光标 / 选区 / yview / 内容 /
     grab / Escape 绑定），涂鸦绝不写进笔记。
  B. 笔画栈：一笔 = 一组图元（序号是「圆 + 数字」两个），撤销 / 清空 / 编号重算。
  C. 方案 C 图标：全矢量、挖空用当前底片色、工具条在窄编辑区里不溢出。
  D. 图层隔离：veil / hint / stroke 三个 tag 互不误删，换肤只重画装饰层。
  E. 提示片自动收起（延时是毫秒，不能被 DPI 放大）。

运行：
    python -m unittest tests.test_doodle -v
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

try:
    import tkinter as tk                                        # noqa: E402
except ImportError:
    tk = None

if tk is not None:
    import dpi                                                  # noqa: E402
    dpi.init(awareness=False)
    from theme import PALETTE, LIGHT, _doodle_veil              # noqa: E402
    from doodle import (DoodleLayer, COLORS, WIDTHS, TOOLS,     # noqa: E402
                        CELL_MIN, KEY, DEFAULT_HINT, BAR_H, BOTTOM_GAP,
                        _build_items, _draw_glyph)


class _Ev(object):
    """最小事件对象：探针里同样用它喂内部回调。"""

    def __init__(self, **kw):
        self.__dict__.update(kw)


@unittest.skipIf(tk is None, "tkinter / display unavailable")
class _DoodleCase(unittest.TestCase):

    def setUp(self):
        self.root = tk.Tk()
        self.root.geometry("560x460+40+40")
        self.app = _FakeApp(self.root)
        self.root.update()
        self.layer = DoodleLayer(self.app, self.app.pal)
        self.assertTrue(self.layer.open())
        self.root.update()

    def tearDown(self):
        try:
            self.layer.close()
        finally:
            try:
                self.root.destroy()
            except Exception:
                pass

    # ------------------------------------------------------------ 工具
    def _stroke(self, kind, pts, color=None):
        d, cv = self.layer, self.layer.cv
        d._tool = kind
        if color is not None:
            d._color = color
        d._on_press(_Ev(x=pts[0][0], y=pts[0][1]))
        for x, y in pts[1:]:
            d._on_motion(_Ev(x_root=cv.winfo_rootx() + x,
                             y_root=cv.winfo_rooty() + y))
        d._on_release(_Ev(x_root=cv.winfo_rootx() + pts[-1][0],
                          y_root=cv.winfo_rooty() + pts[-1][1]))

    def _texts(self, tag=None):
        cv = self.layer.cv
        ids = cv.find_withtag(tag) if tag else cv.find_all()
        return [cv.itemcget(i, "text") for i in ids
                if cv.type(i) == "text"]


class _FakeApp(object):
    """只给 DoodleLayer 用到的那几个宿主接口。

    `chrome` 在编辑器上下各塞一条假「工具栏 / 状态条」：蒙层要盖**整个窗口**
    而不是只盖 editor_frame，没有这段就不出区别。
    """

    def __init__(self, root, w=520, h=420, chrome=0):
        self.root = root
        self.pal = dict(PALETTE)
        if chrome:
            tk.Frame(root, height=chrome, bg="#e9e9ec").pack(side=tk.TOP, fill=tk.X)
            tk.Frame(root, height=chrome, bg="#e9e9ec").pack(side=tk.BOTTOM, fill=tk.X)
        self.editor_frame = tk.Frame(root, width=w, height=h, bg="#ffffff")
        self.editor_frame.pack(fill=tk.BOTH, expand=True)
        self.editor_frame.pack_propagate(False)
        self.text = tk.Text(self.editor_frame, wrap="word", undo=True)
        self.text.insert("1.0", "标题\n\n" + "\n".join(
            "第 %d 行正文，用来把编辑区撑到可以滚动。" % i for i in range(60)))
        self.text.pack(fill=tk.BOTH, expand=True)
        self._jobs = set()
        self.delays = []
        self.closed = []

    def _after(self, ms, fn):
        self.delays.append(ms)
        j = self.root.after(ms, fn)
        self._jobs.add(j)
        return j

    def _cancel_after(self, j):
        self._jobs.discard(j)
        try:
            self.root.after_cancel(j)
        except Exception:
            pass

    def _mention_close(self):
        self.closed.append("mention")

    def _find_close(self):
        self.closed.append("find")
        return "break"


@unittest.skipIf(tk is None, "tkinter / display unavailable")
class TestSnapshot(_DoodleCase):

    def test_open_locks_text_and_close_restores_everything(self):
        # setUp 已经开了一层（正文被锁），先退出再取「未进入涂鸦」的干净快照
        self.layer.close()
        self.root.update()
        t = self.app.text
        t.mark_set("insert", "3.2")
        t.tag_add("sel", "2.0", "2.6")
        t.yview_moveto(0.25)
        self.root.update()
        snap = {
            "text": t.get("1.0", "end-1c"),
            "state": t.cget("state"),
            "insert": t.index("insert"),
            "sel": tuple(str(i) for i in t.tag_ranges("sel")),
            "yview": t.yview()[0],
        }
        self.layer.close()
        self.root.update()
        self.layer = DoodleLayer(self.app, self.app.pal)
        self.assertTrue(self.layer.open())
        self.assertEqual(t.cget("state"), "disabled")
        self.assertEqual(t.get("1.0", "end-1c"), snap["text"])
        # 蒙层期间连「光标被挪走」也要在退出时回滚
        t.mark_set("insert", "1.0")
        t.yview_moveto(0.6)
        self.layer.close()
        self.root.update()
        self.assertEqual(t.cget("state"), snap["state"])
        self.assertEqual(t.index("insert"), snap["insert"])
        self.assertEqual(tuple(str(i) for i in t.tag_ranges("sel")), snap["sel"])
        self.assertAlmostEqual(t.yview()[0], snap["yview"], places=5)
        self.assertEqual(t.get("1.0", "end-1c"), snap["text"])

    def test_grab_and_escape_binding_are_released(self):
        self.assertIs(self.root.grab_current(), self.layer.win)
        self.assertIsNotNone(self.layer._esc_id)
        self.layer.close()
        self.root.update()
        self.assertIsNone(self.root.grab_current())
        self.assertIsNone(self.layer._esc_id)
        self.assertFalse(self.layer.active)
        self.layer.close()          # 幂等，二次 close 不许抛

    def test_open_closes_competing_popovers(self):
        self.assertEqual(self.app.closed, ["mention", "find"])

    def test_hint_delay_is_milliseconds_not_dpi_pixels(self):
        self.assertEqual(self.app.delays, [2200])
        self.assertEqual(self.layer._hint, DEFAULT_HINT)


@unittest.skipIf(tk is None, "tkinter / display unavailable")
class TestStrokes(_DoodleCase):

    def test_each_stroke_is_tagged_and_undo_pops_a_whole_group(self):
        cv = self.layer.cv
        self._stroke("pencil_line", [(20, 40), (40, 50), (70, 45)])
        self._stroke("rect", [(90, 60), (160, 110)])
        self._stroke("number", [(200, 80)])
        self.assertEqual(len(self.layer._strokes), 3)
        # 1 线 + 1 框 + (圆 + 数字) = 4 个图元，全带 stroke
        items = cv.find_withtag("stroke")
        self.assertEqual(len(items), 4)
        for i in cv.find_all():
            if cv.type(i) == "text" or i in items:
                continue
            self.assertNotIn("stroke", cv.gettags(i))
        self.assertEqual(len(cv.find_withtag("veil")) >= 1, True)
        self.assertEqual(len(cv.find_withtag("hint")) >= 1, True)
        # 重画装饰层不许抹掉笔迹
        self.layer._draw_veil()
        self.layer._draw_hint()
        self.assertEqual(len(cv.find_withtag("stroke")), 4)
        self.layer.undo()
        self.assertEqual(len(self.layer._strokes), 2)
        self.assertEqual(len(cv.find_withtag("stroke")), 2)
        self.assertEqual(self.layer._seq, 1)
        self.layer.clear()
        self.assertEqual(self.layer._strokes, [])
        self.assertEqual(len(cv.find_withtag("stroke")), 0)
        self.assertEqual(self.layer._seq, 1)

    def test_number_tool_keeps_sequential_labels(self):
        for x in (100, 140, 180):
            self._stroke("number", [(x, 80)])
        self.assertEqual(self.layer._seq, 4)
        self.assertEqual(self._texts("stroke")[-3:], ["1", "2", "3"])
        self.layer.undo()
        self.assertEqual(self.layer._seq, 3)
        self.assertEqual(self._texts("stroke")[-2:], ["1", "2"])
        self._stroke("number", [(180, 80)])
        self.assertEqual(self._texts("stroke")[-3:], ["1", "2", "3"])
        self.layer.clear()
        self.assertEqual(self.layer._seq, 1)
        self.assertEqual(self._texts("stroke"), [])

    def test_tool_selection_color_width_and_close_button(self):
        d = self.layer
        items = _build_items()
        def idx(kind, key):
            return next(i for i, it in enumerate(items)
                        if it["kind"] == kind and it["key"] == key)
        i_tool = idx("tool", "arrow")
        d._activate(i_tool)
        self.assertEqual(d._tool, "arrow")
        self.assertTrue(d._is_selected(items[i_tool]))
        i_color = idx("color", 2)
        d._activate(i_color)
        self.assertEqual(d._color, 2)
        i_width = idx("width", 0)
        d._activate(i_width)
        self.assertEqual(d._width, 0)
        # 分隔线不是按钮：点它的中心不该命中任何项
        i_sep = next(i for i, it in enumerate(items) if it["kind"] == "sep")
        acc = d._pad + sum(d._widths[:i_sep]) + d._widths[i_sep] / 2.0
        self.assertIsNone(d._hit(acc))
        d._activate(idx("action", "close"))
        self.assertFalse(d.active)

    def test_undo_and_clear_on_empty_stack_do_nothing(self):
        self.layer.undo()
        self.layer.clear()
        self.assertEqual(self.layer._strokes, [])
        self.assertEqual(self.layer._seq, 1)


@unittest.skipIf(tk is None, "tkinter / display unavailable")
class TestLookAndLayout(_DoodleCase):

    def test_icons_are_vector_and_holes_use_current_bg(self):
        cv = tk.Canvas(self.root)
        names = [t[0] for t in TOOLS] + ["undo", "trash_line", "close"]
        for name in names:
            before = set(cv.find_all())
            _draw_glyph(cv, name, 20, 20, 16, "#123456", "#abcdef")
            made = set(cv.find_all()) - before
            self.assertTrue(made, name)
            for i in made:
                self.assertNotEqual(cv.type(i), "text", name)
                self.assertNotEqual(cv.itemcget(i, "fill"), KEY, name)
        # ⚠️ 选中态挖空用当前底片色，不能写死工具条底色
        before = set(cv.find_all())
        _draw_glyph(cv, "pencil_line", 40, 20, 16, "#123456", "#abcdef")
        holes = [i for i in set(cv.find_all()) - before
                 if cv.itemcget(i, "fill") == "#abcdef"]
        self.assertTrue(holes)

    def test_key_color_is_never_used_by_any_layer_color(self):
        c = self.layer.colors
        for name in ("veil", "rule", "bar", "line", "ink", "soft",
                     "sel", "hover", "active_fg", "accent"):
            self.assertNotEqual(c[name], KEY, name)
        for col in COLORS:
            self.assertNotEqual(col, KEY)

    def test_bar_never_overflows_narrow_editor(self):
        d = self.layer
        for avail in (260, 300, 320, 400, 431, 600):
            d._fit(avail)
            self.assertLessEqual(d._bar_w, avail)
            # 分隔线本身只有 11px，不按按钮格的下限要求它
            for it, w in zip(d._items, d._widths):
                floor = min(CELL_MIN, it["w"]) if it["kind"] == "sep" else CELL_MIN
                self.assertGreaterEqual(w, floor)
            self.assertEqual(len(d._widths), len(d._items))
        # 宽编辑区用原尺寸，不压缩
        d._fit(600)
        self.assertEqual(d._bar_w, sum(it["w"] for it in d._items) + 2 * d._pad)

    def test_relayout_dedups_same_geometry(self):
        d = self.layer
        geom = d._geom
        d.relayout()
        self.assertEqual(d._geom, geom)

    def test_apply_theme_recomputes_colors_but_keeps_strokes(self):
        d = self.layer
        self._stroke("rect", [(30, 30), (120, 90)])
        old = d.colors["veil"]
        dark = dict(LIGHT)
        dark.update({"editor": "#202124", "text": "#f2f2f2",
                     "soft": "#b0b0b0", "accent": "#66bb6a"})
        d.apply_theme(dark)
        self.assertNotEqual(d.colors["veil"], old)
        self.assertEqual(d.colors["veil"], _doodle_veil(dark)["veil"])
        self.assertEqual(len(d.cv.find_withtag("stroke")), 1)
        self.assertTrue(d.active)

    def test_hint_hides_and_hover_replaces_it(self):
        d = self.layer
        self.assertEqual(d._hint, DEFAULT_HINT)
        self.assertIn(DEFAULT_HINT, self._texts())
        d._set_hint("序号 · 点一下放一个编号标记")
        self.assertIn("序号 · 点一下放一个编号标记", self._texts())
        self.assertIsNone(d._hint_job)      # hover 说明不被自动收起掐掉
        d._on_bar_leave()
        self.assertIsNone(d._hint)
        self.assertNotIn(DEFAULT_HINT, self._texts())

    def test_widths_and_colors_are_four_and_three(self):
        self.assertEqual(len(COLORS), 4)
        self.assertEqual(len(WIDTHS), 3)
        self.assertEqual(len([t for t in TOOLS if t[0] == "number"]), 1)


@unittest.skipIf(tk is None, "tkinter / display unavailable")
class TestEventRouting(_DoodleCase):
    """起笔事件必须挂在**窗口级**（2026-11-06 用户报障「画不出来，偶尔可以」）。

    色键透明的像素不参与命中测试，鼠标落在那里时事件目标是 Toplevel 本身，
    Canvas 的绑定压根不触发 —— 只有点在不透明的网点 / 已有笔迹上才碰巧能画。
    """

    def _stroke_via_window(self, pts):
        """向蒙层窗口投递事件，模拟「点在透明像素上」的真实路径。"""
        d, win, cv = self.layer, self.layer.win, self.layer.cv
        win.event_generate("<ButtonPress-1>", x=pts[0][0], y=pts[0][1])
        win.update()
        for x, y in pts[1:]:
            win.event_generate("<B1-Motion>", x=x, y=y)
        win.update()
        win.event_generate("<ButtonRelease-1>", x=pts[-1][0], y=pts[-1][1])
        win.update()
        self.root.update()
        return d

    def test_press_is_bound_on_window_not_canvas(self):
        cv, win = self.layer.cv, self.layer.win
        self.assertFalse(cv.bind("<ButtonPress-1>"),
                         "Canvas 上不该再挂起笔：网点像素会触发两次、一次点按起两笔")
        for seq in ("<ButtonPress-1>", "<B1-Motion>", "<ButtonRelease-1>"):
            self.assertTrue(win.bind(seq), "%s 必须挂在蒙层窗口上" % seq)

    def test_window_level_press_starts_and_finishes_a_stroke(self):
        d = self._stroke_via_window([(120, 200), (140, 210), (170, 230), (200, 250)])
        self.assertEqual(len(d._strokes), 1)
        self.assertEqual(d._strokes[0][1], "pencil_line")
        self.assertIsNone(d._draw)
        self.assertEqual(len(d.cv.find_withtag("stroke")), 1)
        pts = d.cv.coords(d.cv.find_withtag("stroke")[0])
        self.assertEqual(tuple(pts[:2]), (120.0, 200.0))
        self.assertGreaterEqual(len(pts) // 2, 3)

    def test_shape_tool_also_uses_window_level(self):
        d = self.layer
        d._tool = "rect"
        self._stroke_via_window([(60, 120), (90, 140), (140, 180)])
        self.assertEqual(d._strokes[-1][1], "rect")
        co = d.cv.coords(d.cv.find_withtag("stroke")[-1])
        self.assertEqual(tuple(co), (60.0, 120.0, 140.0, 180.0))

    def test_click_on_toolbar_does_not_start_a_stroke(self):
        d, bar = self.layer, self.layer.bar
        y = max(4, bar.winfo_height() // 2)
        bar.event_generate("<ButtonPress-1>", x=20, y=y)
        bar.event_generate("<ButtonRelease-1>", x=20, y=y)
        self.root.update()
        self.assertEqual(d.cv.find_withtag("stroke"), ())
        self.assertIsNone(d._draw)

    def test_press_outside_the_viewport_is_ignored(self):
        d = self.layer
        d._on_press(_Ev(x=-5, y=100))
        self.assertIsNone(d._draw)
        d._on_press(_Ev(x=10, y=d._h + 30))     # 越出蒙层下沿
        self.assertIsNone(d._draw)
        # 工具条能拖走之后，底部那一条**不再**是禁区：那里现在就是普通稿纸
        d._on_press(_Ev(x=10, y=d._h - 1))
        self.assertIsNotNone(d._draw)

    def test_fallback_bindings_only_when_grab_fails(self):
        """grab 没拿到时用 bind_all 兜底；拿到就一定要解掉，否则一次点按起两笔。"""
        d = self.layer
        self.assertTrue(d._grab_ok)
        self.assertEqual(d._fb_ids, [])
        d._grab_ok = False
        d._bind_mouse_fallback()
        self.assertEqual(len(d._fb_ids), 3)
        d._unbind_mouse_fallback()
        self.assertEqual(d._fb_ids, [])


@unittest.skipIf(tk is None, "tkinter / display unavailable")
class TestMinimizeFollow(_DoodleCase):
    """主窗最小化时蒙层必须跟着收起，还原后回到原位并补回 grab。"""

    def _pump(self, ms=260):
        import time
        end = time.time() + ms / 1000.0
        while time.time() < end:
            self.root.update()
            time.sleep(0.02)

    def test_watches_root_map_and_unmap(self):
        self.assertEqual([s for s, _ in self.layer._root_ids],
                         ["<Unmap>", "<Map>"])

    def test_iconify_hides_overlay_and_deiconify_restores(self):
        d, win = self.layer, self.layer.win
        self.assertTrue(win.winfo_viewable())
        self.root.iconify()
        self._pump(420)
        self.assertEqual(self.root.state(), "iconic")
        self.assertFalse(win.winfo_ismapped(), "主窗最小化后蒙层必须一起收起")
        self.root.deiconify()
        self._pump(480)
        self.assertTrue(win.winfo_ismapped(), "还原后蒙层要回来")
        self.assertEqual(win.grab_current(), win, "回来必须补回 grab，否则主窗能编辑")
        self.assertEqual(d._geom, (self.app.editor_frame.winfo_rootx(),
                                   self.app.editor_frame.winfo_rooty(),
                                   self.app.editor_frame.winfo_width(),
                                   self.app.editor_frame.winfo_height()))

    def test_restore_works_from_zoomed_too(self):
        """⚠️ 主窗最大化时 state() 是 "zoomed"，判死 "normal" 会让蒙层再也出不来。"""
        win = self.layer.win
        self.root.state("zoomed")
        self._pump(320)
        self.root.iconify()
        self._pump(420)
        self.assertFalse(win.winfo_ismapped())
        self.root.deiconify()
        self._pump(520)
        self.assertEqual(self.root.state(), "zoomed")
        self.assertTrue(win.winfo_ismapped(), "zoomed 状态下也要能还原")

    def test_strokes_survive_minimize(self):
        d = self.layer
        d._tool = "pencil_line"
        d._on_press(_Ev(x=80, y=100))
        d._on_motion(_Ev(x_root=d.cv.winfo_rootx() + 120,
                         y_root=d.cv.winfo_rooty() + 140))
        d._on_release(_Ev(x_root=d.cv.winfo_rootx() + 120,
                          y_root=d.cv.winfo_rooty() + 140))
        n = len(d.cv.find_withtag("stroke"))
        self.root.iconify()
        self._pump(360)
        self.root.deiconify()
        self._pump(420)
        self.assertEqual(len(d.cv.find_withtag("stroke")), n,
                         "隐藏只是 withdraw，笔迹不该丢")

    def test_close_unhooks_root_bindings(self):
        d = self.layer
        d.close()
        self.assertEqual(d._root_ids, [])
        self.assertIsNone(self.root.grab_current())


@unittest.skipIf(tk is None, "tkinter / display unavailable")
class TestDraggableBar(_DoodleCase):
    """工具条可任意拖动（2026-11-06）。

    锁住四件事：默认贴底居中不动、拖一把就跟着走、永远夹在蒙层里（拖丢了就
    连退出按钮都找不回来）、以及「拖」不能顺手把按钮点掉。
    """

    def _grab_point(self):
        d = self.layer
        return dpi.px(10), max(4, d.bar.winfo_height() // 2)

    def _drag(self, dx, dy, release=True):
        d = self.layer
        x, y = self._grab_point()
        bx, by = d._bar_at
        d._on_bar_press(_Ev(x=x, y=y, x_root=bx + x, y_root=by + y))
        d._on_bar_motion(_Ev(x=x, y=y,
                             x_root=bx + x + dx, y_root=by + y + dy))
        if release:
            d._on_bar_release(_Ev(x=x, y=y,
                                  x_root=bx + x + dx, y_root=by + y + dy))

    def test_default_position_is_bottom_center(self):
        d = self.layer
        self.assertIsNone(d._bar_pos)
        self.assertEqual(d._bar_at,
                         ((d._w - d._bar_w) // 2, d._h - BOTTOM_GAP - BAR_H))

    def test_drag_moves_the_whole_bar(self):
        d = self.layer
        x0, y0 = d._bar_at
        self._drag(-40, -70)
        self.assertEqual(d._bar_at, (x0 - 40, y0 - 70))
        self.assertEqual(d._bar_pos, (x0 - 40, y0 - 70))

    def test_drag_is_clamped_inside_the_veil(self):
        d = self.layer
        self._drag(-9999, -9999)
        self.assertEqual(d._bar_at, (0, 0))
        self._drag(9999, 9999)
        self.assertEqual(d._bar_at, (max(0, d._w - d._bar_w),
                                     max(0, d._h - BAR_H)))
        # 贴边之后按钮仍然点得到：夹取的是整条，不是逐格
        self.assertEqual(len(d._items), len(d._widths))

    def test_relayout_keeps_where_the_user_put_it(self):
        d = self.layer
        self._drag(-60, -80)
        pos = d._bar_at
        d.relayout(force=True)
        self.assertEqual(d._bar_at, pos)

    def test_tiny_jitter_still_counts_as_a_click(self):
        """手抖 1px 不能把「点按钮」变成「拖工具条」。"""
        d = self.layer
        x, y = self._grab_point()
        # 第二格 = 直线
        x += d._widths[0]
        bx, by = d._bar_at
        d._on_bar_press(_Ev(x=x, y=y, x_root=bx + x, y_root=by + y))
        d._on_bar_motion(_Ev(x=x, y=y, x_root=bx + x + 1, y_root=by + y + 1))
        d._on_bar_release(_Ev(x=x, y=y, x_root=bx + x + 1, y_root=by + y + 1))
        self.assertEqual(d._tool, "line")
        self.assertEqual(d._bar_at, (bx, by))

    def test_drag_does_not_activate_the_button(self):
        d = self.layer
        before = d._tool
        x, y = self._grab_point()
        x += d._widths[0]          # 从「直线」上起手，拖出去松手
        bx, by = d._bar_at
        d._on_bar_press(_Ev(x=x, y=y, x_root=bx + x, y_root=by + y))
        d._on_bar_motion(_Ev(x=x, y=y, x_root=bx + x - 40, y_root=by + y - 40))
        d._on_bar_release(_Ev(x=x, y=y, x_root=bx + x - 40, y_root=by + y - 40))
        self.assertEqual(d._tool, before)
        self.assertEqual(d._bar_at, (bx - 40, by - 40))

    def test_hint_follows_the_bar(self):
        d = self.layer
        self._drag(-50, -90)
        d._set_hint("涂鸦模式 · Esc 退出")
        hx0, _hy0, hx1, _hy1 = d._hint_box
        self.assertAlmostEqual((hx0 + hx1) / 2.0,
                               d._bar_at[0] + d._bar_w / 2.0, delta=1.5)
        # 工具条拖到顶部时提示翻到它下面，不会顶出蒙层（拖动本身会把提示收起）
        self._drag(0, -9999)
        d._set_hint("涂鸦模式 · Esc 退出")
        self.assertGreaterEqual(d._hint_box[1], 0)

    def test_hint_is_not_a_drawing_area(self):
        d = self.layer
        d._set_hint("涂鸦模式 · Esc 退出")
        x0, y0, x1, y1 = d._hint_box
        d._on_press(_Ev(x=(x0 + x1) // 2, y=(y0 + y1) // 2))
        self.assertIsNone(d._draw)
        self.assertEqual(d.cv.find_withtag("stroke"), ())

    def test_number_icon_glyph_is_ink_centered(self):
        """工具栏 ①：「1」按墨迹居中，不能像早先那样整字偏左。"""
        cv = tk.Canvas(self.root, width=60, height=60)
        _draw_glyph(cv, "number", 30, 30, 16, "#111111", "#ffffff")
        bb = cv.bbox("num1")
        self.assertIsNotNone(bb)
        self.assertAlmostEqual((bb[0] + bb[2]) / 2.0, 30.0, delta=1.0)
        self.assertAlmostEqual((bb[1] + bb[3]) / 2.0, 30.0, delta=1.0)
        cv.destroy()


@unittest.skipIf(tk is None, "tkinter / display unavailable")
class TestVeilCoversWholeWindow(unittest.TestCase):
    """蒙层盖**整个软件界面**（2026-11-06 用户要求）。

    以前贴 editor_frame，顶栏 / 侧栏 / 列表 / 状态条都露在外面；这条用「上下各
    40px 假 chrome」的宿主把两者区分开，防止有人改回去。
    """

    def test_overlay_matches_root_not_editor_frame(self):
        root = tk.Tk()
        root.geometry("640x460+40+40")
        app = _FakeApp(root, chrome=40)
        root.update()
        d = DoodleLayer(app, app.pal)
        self.assertTrue(d.open())
        root.update_idletasks()
        root.update()
        try:
            self.assertEqual(d._w, root.winfo_width())
            self.assertEqual(d._h, root.winfo_height())
            self.assertLess(app.editor_frame.winfo_height(), root.winfo_height())
            self.assertEqual(d.win.winfo_rootx(), root.winfo_rootx())
            self.assertEqual(d.win.winfo_rooty(), root.winfo_rooty())
            self.assertEqual(d.win.winfo_width(), root.winfo_width())
            self.assertEqual(d.win.winfo_height(), root.winfo_height())
            # 工具条默认仍然贴底居中（相对整窗，不是相对稿纸）
            self.assertEqual(d._bar_at,
                             ((d._w - d._bar_w) // 2, d._h - BOTTOM_GAP - BAR_H))
            # 顶部那条假工具栏也在落笔范围内
            d._tool = "pencil_line"
            d._on_press(_Ev(x=200, y=12))
            self.assertIsNotNone(d._draw)
        finally:
            d.close()
            root.destroy()


if __name__ == "__main__":
    unittest.main()
