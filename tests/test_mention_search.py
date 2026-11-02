# -*- coding: utf-8 -*-
"""`@` 关联笔记：紧跟文字能弹 + 弹框搜索框的回归测试。

两件事分开测：

  A. 用桩驱动 `ActionsMixin`，锁「文字@ + 空格 → 真的会 new MentionPopup」，
     以及搜索框 / 正文两条查询词来源互不覆盖（2026-11-05 用户报障的完整链路）。
  B. 真实 Tk 里起一个 MentionPopup，锁搜索框看得见、输入能触发 on_query、键盘
     ↑↓ / Enter / Esc 能操作候选、关面板会把焦点还给正文，以及浮层是「一整张
     卡片」、标题 / 底部有留白、总高把外沿描边算进去（2026-11-06 版式回归）；
     再锁「全部候选都保留 + 一屏 8 行 + 滚动条，弹框不随笔记数长高」。

运行：
    python -m unittest tests.test_mention_search -v
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
    import app_actions                                          # noqa: E402
    from app_actions import ActionsMixin                        # noqa: E402
    from mention_ui import MentionPopup                         # noqa: E402
    from theme import LIGHT                                     # noqa: E402
else:
    app_actions = ActionsMixin = MentionPopup = LIGHT = None


class _Ev(object):
    """事件桩：只带方法里要读的字段。"""

    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeText(object):
    """只实现 _mention_at 用到的那两个 Text 方法。"""

    def __init__(self, line):
        self.line = line

    def index(self, spec="insert"):
        return "1.%d" % len(self.line)

    def get(self, a, b):
        return self.line


class _FakePopup(object):
    """替掉会建 Toplevel 的浮层，只记调用。"""

    instances = []

    def __init__(self, master, pal, on_pick=None, on_query=None,
                 on_close=None, focus_back=None):
        self.kwargs = {"on_pick": on_pick, "on_query": on_query,
                       "on_close": on_close, "focus_back": focus_back}
        self.opened = None
        self.queries = []
        self.item_sets = []
        self.focused = False
        self._open = False
        _FakePopup.instances.append(self)

    def is_open(self):
        return self._open

    def open(self, items, x, y, query=""):
        self._open = True
        self.opened = (items, x, y, query)
        return self

    def close(self):
        self._open = False

    def set_items(self, items):
        self.item_sets.append(list(items))

    def set_query(self, q):
        self.queries.append(q)

    def search_focused(self):
        return self.focused


class _Stub(object):
    """把 ActionsMixin 的几个方法绑到最小状态上。"""

    _mention_maybe_open = ActionsMixin._mention_maybe_open if ActionsMixin else None
    _mention_refilter = ActionsMixin._mention_refilter if ActionsMixin else None
    _mention_apply_query = ActionsMixin._mention_apply_query if ActionsMixin else None
    _mention_at = ActionsMixin._mention_at if ActionsMixin else None
    _mention_close = ActionsMixin._mention_close if ActionsMixin else None

    def __init__(self, line):
        self.root = object()
        self.pal = dict(LIGHT)
        self.text = _FakeText(line)
        self._mention_pop = None
        self._mention_start = None
        self.cand_queries = []

    def _mention_anchor_xy(self):
        return (100, 200)

    def _mention_candidates(self, query):
        self.cand_queries.append(query)
        return [{"id": "n1", "title": "会议记录"}]

    def _mention_pick(self, item):
        self.picked = item


@unittest.skipIf(tk is None, "tkinter / display unavailable")
class ActionsWiringTest(unittest.TestCase):
    def setUp(self):
        _FakePopup.instances = []
        self._orig = app_actions.MentionPopup
        app_actions.MentionPopup = _FakePopup

    def tearDown(self):
        app_actions.MentionPopup = self._orig

    def test_text_then_at_space_opens_popup(self):
        """回归：`今天开会@ ` 左邻是汉字，旧版 `_LEFT_OK` 会把它当邮箱挡掉。"""
        s = _Stub("今天开会@ ")
        s._mention_maybe_open()
        self.assertEqual(len(_FakePopup.instances), 1)
        pop = s._mention_pop
        self.assertTrue(pop.is_open())
        self.assertEqual(s._mention_start, "1.4")
        self.assertIsNotNone(pop.kwargs["on_query"])
        self.assertIsNotNone(pop.kwargs["on_close"])
        self.assertIs(pop.kwargs["focus_back"], s.text)
        self.assertEqual(pop.opened[0][0]["id"], "n1")

    def test_ascii_then_at_space_opens_popup(self):
        s = _Stub("abc@ ")
        s._mention_maybe_open()
        self.assertEqual(len(_FakePopup.instances), 1)
        self.assertEqual(s._mention_start, "1.3")

    def test_email_still_does_not_open(self):
        s = _Stub("zhang@163.com ")
        s._mention_maybe_open()
        self.assertEqual(_FakePopup.instances, [])

    def test_on_query_recomputes_candidates(self):
        s = _Stub("今天开会@ ")
        s._mention_maybe_open()
        s._mention_pop.kwargs["on_query"]("会议")
        self.assertIn("会议", s.cand_queries)
        self.assertEqual(s._mention_pop.item_sets[-1][0]["id"], "n1")

    def test_refilter_syncs_body_query_when_search_not_focused(self):
        s = _Stub("今天开会@ ")
        s._mention_maybe_open()
        s.text.line = "今天开会@ 会"
        s._mention_refilter()
        self.assertEqual(s._mention_pop.queries, ["会"])
        self.assertEqual(s._mention_start, "1.4")

    def test_refilter_keeps_search_query_when_focused(self):
        """焦点在搜索框时，正文里的空查询词不能盖掉用户已输入的搜索词。"""
        s = _Stub("今天开会@ ")
        s._mention_maybe_open()
        s._mention_pop.focused = True
        s._mention_refilter()
        self.assertEqual(s._mention_pop.queries, [])
        self.assertEqual(s._mention_pop.item_sets, [])
        self.assertEqual(s._mention_start, "1.4")

    def test_refilter_closes_when_at_disappears(self):
        s = _Stub("今天开会@ ")
        s._mention_maybe_open()
        s.text.line = "今天开会@ 甲 乙"      # 第二个空格 = 已经不在找笔记了
        s._mention_refilter()
        self.assertFalse(s._mention_pop.is_open())
        self.assertIsNone(s._mention_start)

    def test_on_close_goes_through_mention_close(self):
        s = _Stub("今天开会@ ")
        s._mention_maybe_open()
        s._mention_pop.kwargs["on_close"]()
        self.assertFalse(s._mention_pop.is_open())
        self.assertIsNone(s._mention_start)


@unittest.skipIf(tk is None, "tkinter / display unavailable")
class PopupSearchTkTest(unittest.TestCase):
    """真实 Toplevel：搜索框的可见性、输入回调、键盘与焦点收口。"""

    def setUp(self):
        try:
            self.root = tk.Tk()
            self.root.withdraw()
            self.root.geometry("600x400")
            self.root.update()
        except Exception:
            self.skipTest("tkinter / display unavailable")
        self.text = tk.Text(self.root)
        self.text.pack()
        self.root.update()
        self.picked = []
        self.queries = []
        self.closed = []
        self.pop = MentionPopup(
            self.root, LIGHT,
            on_pick=lambda it: self.picked.append(it),
            # 和真实接线一样：查询词一变就重算候选并重绘（表头也会跟着变）
            on_query=lambda q: (self.queries.append(q),
                                self.pop.set_items(self.items)),
            on_close=lambda: (self.closed.append(1), self.pop.close()),
            focus_back=self.text)
        self.items = [{"id": "n1", "title": "会议记录", "sub": "工作"},
                      {"id": "n2", "title": "周报", "sub": "生活"}]

    def tearDown(self):
        try:
            self.pop.close()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def _open(self):
        self.pop.open(self.items, 100, 100)
        self.root.update()
        return self.pop

    def test_search_entry_exists_and_is_visible(self):
        pop = self._open()
        self.assertTrue(pop.is_open())
        self.assertIsNotNone(pop._entry)
        self.assertIsNotNone(pop._ph)
        self.assertEqual(pop._query_var.get(), "")
        self.assertEqual(pop._ph.winfo_manager(), "place")   # 空框显示占位
        self.assertGreater(pop.total_h(), pop._height())     # 搜索行在列表之外
        self.assertIn("搜索笔记", pop._ph.cget("text"))

    def test_typing_fires_on_query_and_hides_placeholder(self):
        pop = self._open()
        pop._query_var.set("会议")
        self.root.update()
        self.assertEqual(self.queries, ["会议"])
        self.assertEqual(pop.query, "会议")
        self.assertEqual(pop._ph.winfo_manager(), "")

    def test_set_query_is_idempotent(self):
        pop = self._open()
        pop.set_query("")
        pop.set_query("会议")
        pop.set_query("会议")
        self.assertEqual(self.queries, ["会议"])

    def test_clear_button_empties_query(self):
        pop = self._open()
        pop._query_var.set("会议")
        pop._clear_btn.event_generate("<Button-1>")
        self.root.update()
        self.assertEqual(pop.query, "")
        self.assertEqual(self.queries, ["会议", ""])

    def test_arrow_keys_move_selection(self):
        pop = self._open()
        self.assertEqual(pop.current["id"], "n1")
        pop._entry.event_generate("<Down>")
        self.assertEqual(pop.current["id"], "n2")

    def test_return_picks_and_closes(self):
        pop = self._open()
        pop._entry.event_generate("<Return>")
        self.root.update()
        self.assertEqual([i["id"] for i in self.picked], ["n1"])
        self.assertFalse(pop.is_open())

    def test_escape_dismisses_via_on_close(self):
        pop = self._open()
        pop._entry.event_generate("<Escape>")
        self.root.update()
        self.assertEqual(self.closed, [1])
        self.assertFalse(pop.is_open())

    def test_empty_result_enter_dismisses(self):
        pop = self._open()
        pop.set_items([])
        pop._entry.event_generate("<Return>")
        self.root.update()
        self.assertEqual(self.closed, [1])
        self.assertEqual(self.picked, [])

    def test_close_returns_focus_to_editor(self):
        """焦点还留在搜索框时，销毁前必须还给正文（否则 Esc 后打不了字）。"""
        class _Recorder(object):
            def __init__(self):
                self.calls = 0

            def focus_set(self):
                self.calls += 1

        pop = self._open()
        rec = _Recorder()
        pop.focus_back = rec
        try:
            pop._entry.focus_set()
            self.root.update()
            had_focus = pop.search_focused()
        except Exception:
            had_focus = False
        pop.close()
        self.root.update()
        if had_focus:
            self.assertEqual(rec.calls, 1)

    def test_close_does_not_steal_focus_when_search_not_focused(self):
        """焦点本来就在正文（点了正文别处）：close 不该再多抢一次焦点。"""
        class _Recorder(object):
            def __init__(self):
                self.calls = 0

            def focus_set(self):
                self.calls += 1

        pop = self._open()
        rec = _Recorder()
        pop.focus_back = rec
        # 模拟「焦点已经不在浮层」：把应用焦点交给正文
        try:
            self.text.focus_set()
            self.root.update()
        except Exception:
            pass
        if not pop.search_focused():
            pop.close()
            self.assertEqual(rec.calls, 0)

    def test_header_switches_to_search_result(self):
        pop = self._open()
        pop._query_var.set("会议")
        self.root.update()
        texts = [pop._canvas.itemcget(i, "text")
                 for i in pop._canvas.find_all()
                 if pop._canvas.type(i) == "text"]
        self.assertIn("搜索结果", texts)

    def _text_at(self):
        """Canvas 上所有文字 -> {内容: (x, 中心 y)}。"""
        c = self.pop._canvas
        return dict((c.itemcget(i, "text"), tuple(c.coords(i)))
                    for i in c.find_all() if c.type(i) == "text")

    def test_one_continuous_card_no_second_panel_border(self):
        """搜索行与结果区同属一张卡片；结果区不许再画自己的带框矩形。"""
        pop = self._open()
        self.assertIsNotNone(pop._card)
        self.assertIs(pop._search_frame.master, pop._card)
        self.assertIs(pop._canvas.master, pop._card)
        self.assertEqual(pop._card.cget("bg"), LIGHT["editor"])
        self.assertEqual(pop._canvas.cget("bg"), LIGHT["editor"])
        self.assertEqual(pop._win.cget("bg").lower(), pop._border.lower())
        # 只剩整行选中高亮（outline=""），没有第二个面板的 outline
        for i in pop._canvas.find_all():
            if pop._canvas.type(i) == "rectangle":
                self.assertEqual(pop._canvas.itemcget(i, "outline"), "")

    def test_geometry_uses_outer_width_and_total_height(self):
        """窗口宽高必须含左右 / 上下各 1px 外沿；候选变少后也要跟上。"""
        pop = self._open()
        self.assertEqual(pop.OUTER_W, pop.WIDTH + 2 * pop.BORDER)
        self.assertEqual(pop.total_h(),
                         pop.SEARCH_H + pop._height() + 2 * pop.BORDER)
        self.assertEqual(pop._win.geometry().split("+")[0],
                         "%dx%d" % (pop.OUTER_W, pop.total_h()))
        pop.set_items([])
        self.root.update()
        self.assertEqual(pop._win.geometry().split("+")[0],
                         "%dx%d" % (pop.OUTER_W, pop.total_h()))

    def test_header_and_footer_have_breathing_room(self):
        """标题上下收紧后仍不能贴边（2026-11-06：42/20 → 30/15）。"""
        pop = self._open()
        at = self._text_at()
        head_y = at["关联笔记"][1]
        foot_y = at["↑↓ 选择 · Enter 插入 · Esc 取消"][1]
        self.assertEqual(head_y, pop.HEAD_TEXT_Y)
        self.assertLessEqual(pop.HEAD_H, 34)                 # 比旧版 42 收紧
        self.assertLessEqual(pop.HEAD_TEXT_Y, 16)
        self.assertGreaterEqual(head_y, 10)                  # 上方仍留白
        self.assertGreaterEqual(pop.HEAD_H - head_y, 12)    # 标题到首行
        last_row = pop.HEAD_H + len(pop._items[:pop.MAX_ROWS]) * pop.ROW_H
        self.assertGreaterEqual(foot_y - last_row, 16)      # 末行到提示
        self.assertGreaterEqual(pop._height() - foot_y, 16)  # 提示到下缘

    # ---------- 全部候选 + 滚动条（2026-11-06） ----------

    def _open_many(self, n=20):
        items = [{"id": "n%d" % i, "title": "笔记%02d" % i, "sub": "工作"}
                 for i in range(n)]
        self.pop.open(items, 100, 100)
        self.root.update()
        return self.pop

    def test_all_candidates_kept_and_popup_does_not_grow(self):
        """20 条全在 _items 里，只画 8 行；弹框高度和 8 条时完全一样。"""
        pop = self._open_many(20)
        self.assertEqual(len(pop._items), 20)
        self.assertEqual(len(pop._rows), pop.MAX_ROWS)
        self.assertEqual(pop._height(),
                         pop.HEAD_H + pop.MAX_ROWS * pop.ROW_H + pop.FOOT_H)
        self.assertEqual(pop._win.geometry().split("+")[0],
                         "%dx%d" % (pop.OUTER_W, pop.total_h()))
        self.assertIsNotNone(pop._thumb)                    # 有滚动条
        fixed_h = pop.total_h()
        pop.set_items(pop._items[:8])
        self.assertEqual(pop.total_h(), fixed_h)            # 不随条数长高
        self.assertIsNone(pop._thumb)                       # 不溢出就隐藏

    def test_wheel_can_reach_the_last_note(self):
        pop = self._open_many(20)
        first = [r[2] for r in pop._rows]
        pop._canvas.event_generate("<MouseWheel>", delta=-120)
        self.root.update()
        self.assertGreater(pop._top, 0)
        self.assertNotEqual([r[2] for r in pop._rows], first)
        pop._scroll_by(100)
        self.assertEqual(pop._top, pop._max_top())
        self.assertEqual([r[2] for r in pop._rows][-1], 19)  # 最后一条翻得到

    def test_keyboard_selection_follows_viewport(self):
        """一路按 ↓ 到第 12 条：视口要跟过去，Enter 插的是屏内那条。"""
        pop = self._open_many(20)
        for _ in range(12):
            pop.move(1)
        self.assertEqual(pop._sel, 12)
        self.assertGreaterEqual(pop._top, 1)
        self.assertIn(12, [r[2] for r in pop._rows])
        self.assertEqual(pop.current["id"], "n12")

    def test_click_uses_real_index_after_scrolling(self):
        """滚到第 10 条后点第一行，必须插入第 10 篇而不是屏内序号 0。"""
        pop = self._open_many(20)
        pop._scroll_by(10)
        y0, y1, _idx = pop._rows[0]
        pop._canvas.event_generate("<Button-1>", x=80, y=int((y0 + y1) / 2))
        self.root.update()
        self.assertEqual([i["id"] for i in self.picked], ["n10"])

    def test_scrollbar_drag_and_set_items_reset(self):
        pop = self._open_many(20)
        y0, _y1, track, _th = pop._track_geom
        pop._scroll_drag = 0
        pop._on_scroll_drag(_Ev(y=y0 + track))
        self.assertEqual(pop._top, pop._max_top())
        pop._on_scroll_release(_Ev())
        self.assertIsNone(pop._scroll_drag)
        pop.set_items(pop._items[:5])
        self.assertEqual(pop._top, 0)


if __name__ == "__main__":
    unittest.main()
