# -*- coding: utf-8 -*-
"""`@` 关联笔记的候选浮层：无边框 Toplevel + 搜索框 + Canvas 自绘列表。

与 PopupMenu（右键菜单）最关键的一处区别是**不做 grab**：grab 会把后续击键
全派发到浮层上，用户就没法在正文里继续打字了。现在浮层里自带搜索 Entry，
键盘因此分两路，共用同一份 `_items` / `_sel`：

  * 焦点在搜索框（`open()` 里 focus_force + focus_set，默认如此）→ 敲的字进搜索框，
    ↑ ↓ / Enter / Tab / Esc 由搜索框自己的绑定转给 move() / accept() / on_close；
  * 焦点没能落到搜索框（各平台 overrideredirect 窗口不一定抢得到焦点），或用户
    点回正文 → 敲的字仍进正文，由 app_actions 的 `_mention_refilter` 同步进搜索框；
    ↑ ↓ / Esc 仍由正文上那组绑定转发。

两条路都只是「给一个查询词」，候选由 `on_query` 回调统一重算，互不覆盖：
正文里打字时若 focus 在搜索框，`_mention_refilter` 不会拿正文的空查询词盖掉它。

`MAX_ROWS` 是**可见行数**而不是候选上限：全部候选都留在 `_items` 里，Canvas
每帧只画 `_top` 起的一屏，右侧自绘细滚动条（滚轮 / 点轨道翻页 / 拖拇指）。
这样弹框尺寸不随笔记总数增长，又能翻到任意一篇。
"""

import tkinter as tk
import tkinter.font as tkfont

import dpi
import win_frame
from theme import FONT, _mix_hex
from widgets import _draw_icon

__all__ = ["MentionPopup"]


class MentionPopup(object):
    """候选列表浮层。用法：open(items, x, y) → set_items(过滤后) → accept()。"""

    WIDTH = dpi.px(340)
    ROW_H = dpi.px(30)
    # 整张浮层是**一块连续卡片**：搜索行和结果区共用同一张稿纸底色，
    # 只有窗口外沿一圈 1px 描边。以前结果区自己又画了一个带 outline 的
    # 矩形，加上输入框自己的描边，两段像两个零件拼在一起（视觉割裂）。
    # HEAD_H / FOOT_H 各自把「标题上下留白」「提示上下留白」包进来。
    HEAD_H = dpi.px(30)          # 结果区顶 → 第一行（标题上下留白，2026-11-06 收紧）
    FOOT_H = dpi.px(40)          # 末行 → 卡片底（含提示文字与底部留白）
    HEAD_TEXT_Y = dpi.px(15)     # 「关联笔记」文字中心，相对结果区顶
    FOOT_TEXT_DY = dpi.px(22)    # 底部提示文字中心距末行
    SEARCH_H = dpi.px(40)
    MAX_ROWS = 8                 # 一屏可见行数（不是候选上限！）
    PAD = dpi.px(12)             # 左右统一留白（输入框 / 标题 / 行 / 提示）
    ICON_S = dpi.px(14)
    EMPTY_H = dpi.px(30)
    BORDER = dpi.px(1)
    OUTER_W = WIDTH + BORDER * 2
    # 细滚动条：落在右侧 PAD 这条留白里，不挤窄文字列
    SCROLL_W = dpi.px(6)
    SCROLL_GAP = dpi.px(4)
    SCROLL_INSET = dpi.px(2)
    SCROLL_MIN = dpi.px(24)
    SB_TEXT_GAP = dpi.px(5)  # 有滚动条时文字右缘再往左让一点，别贴着拇指

    def __init__(self, master, pal, on_pick=None, on_query=None,
                 on_close=None, focus_back=None):
        self.master = master
        self.pal = dict(pal)
        self.on_pick = on_pick
        self.on_query = on_query
        self.on_close = on_close
        self.focus_back = focus_back
        self._items = []
        self._sel = 0
        self._hover = -1
        self._top = 0            # 视口第一条在 _items 里的下标
        self._thumb = None       # (x0, y0, x1, y1) 滚动条拇指，None = 没溢出
        self._track_geom = None  # (轨道顶, 轨道底, 轨道高, 拇指高)
        self._scroll_drag = None
        self._scroll_hover = False
        self._win = None
        self._card = None
        self._canvas = None
        self._rows = []          # [(y0, y1, 真实 index)]
        self._search_frame = None
        self._entry = None
        self._query_var = None
        self._ph = None
        self._clear_btn = None
        # 弹层配色一次算好：外沿 1px 不能吃 pal["line"]（只比底色深 5%，
        # 1px 上等于没画），输入框描边也压到很淡，避免又形成一块「独立框」。
        ed = self.pal.get("editor", "#ffffff")
        ink = self.pal.get("text", "#1f1f24")
        soft = self.pal.get("soft", "#76767e")
        acc = self.pal.get("accent", "#2f7cf6")
        self._border = _mix_hex(ed, ink, 0.22)
        # 输入框底色掺一点点 accent（不是中性墨），和卡片 / 选中行同色系；
        # 聚焦描边只比它深一档，不再是一整圈饱和的绿框。
        self._field_bg = _mix_hex(ed, acc, 0.05)
        self._field_line = _mix_hex(ed, ink, 0.14)
        self._focus_line = _mix_hex(ed, acc, 0.24)
        self._sb_track = _mix_hex(ed, soft, 0.14)
        self._sb_thumb = _mix_hex(ed, soft, 0.42)
        self._sb_thumb_hi = _mix_hex(ed, soft, 0.58)
        self._sb_thumb_drag = _mix_hex(ed, soft, 0.70)
        try:
            self._font = tkfont.Font(family=FONT[0], size=FONT[1])
            self._font_small = tkfont.Font(family=FONT[0], size=FONT[1] - 2)
        except Exception:
            self._font = self._font_small = None

    # ---------- 状态 ----------

    def is_open(self):
        return self._win is not None

    @property
    def current(self):
        if not self._items or not (0 <= self._sel < len(self._items)):
            return None
        return self._items[self._sel]

    @property
    def query(self):
        """搜索框当前内容（正文打字那条路会把它同步过来）。"""
        try:
            return self._query_var.get() if self._query_var is not None else ""
        except Exception:
            return ""

    def search_focused(self):
        """焦点此刻是不是在搜索框里 —— app_actions 靠它决定要不要同步正文查询词。"""
        if self._win is None or self._entry is None:
            return False
        try:
            return self._win.focus_get() is self._entry
        except Exception:
            return False

    def _visible_rows(self):
        """当前一屏能放几行（候选不足就按实际条数收缩，空态另有空态高度）。"""
        return min(len(self._items), self.MAX_ROWS)

    def _max_top(self):
        return max(0, len(self._items) - self._visible_rows())

    # ---------- 打开 / 关闭 ----------

    def open(self, items, x, y, query=""):
        """在屏幕坐标 (x, y) 弹出；靠近屏幕下沿时翻到光标上方。"""
        self._items = list(items or [])
        self._sel = 0
        self._hover = -1
        self._top = 0
        self._thumb = None
        self._track_geom = None
        self._scroll_drag = None
        self._scroll_hover = False
        p = self.pal
        win = tk.Toplevel(self.master)
        win.overrideredirect(True)
        try:
            win.attributes("-topmost", True)
        except Exception:
            pass
        win.configure(bg=self._border)
        self._win = win
        # card 的 1px 外边距就是整张浮层唯一的描边；搜索行与结果 Canvas
        # 都装在它里面，底色相同、中间没有任何边框或分隔线。
        card = tk.Frame(win, bg=p.get("editor", "#ffffff"), bd=0,
                        highlightthickness=0)
        card.pack(fill=tk.BOTH, expand=True,
                  padx=self.BORDER, pady=self.BORDER)
        self._card = card
        self._build_search(query)
        w = self.WIDTH
        h = self._height()
        c = tk.Canvas(card, width=w, height=h, bd=0, highlightthickness=0,
                      bg=p.get("editor", "#ffffff"))
        c.pack()
        self._canvas = c
        self._render()
        # 边界取**光标所在那块屏**：Tk 的 winfo_screenwidth 只报主屏，
        # 多屏时副屏（坐标为负）弹出的候选层会被夹到 x=0，跨到主屏去
        total = self.total_h()
        area = win_frame.work_area_at(x, y, fallback=win)
        py = int(y)
        if py + total > area[1] + area[3] - 4:  # 下方放不下 → 翻到光标上方
            py = py - total - 22
        px, py = win_frame.clamp_pos(area, x, py, self.OUTER_W, total, pad=4)
        win.geometry("%dx%d+%d+%d" % (self.OUTER_W, total, px, py))
        c.bind("<Motion>", self._on_motion)
        c.bind("<Button-1>", self._on_click)
        c.bind("<Leave>", self._on_leave)
        c.bind("<MouseWheel>", self._on_wheel)
        c.bind("<Button-4>", lambda e: self._wheel_dir(-3))
        c.bind("<Button-5>", lambda e: self._wheel_dir(3))
        c.bind("<B1-Motion>", self._on_scroll_drag)
        c.bind("<ButtonRelease-1>", self._on_scroll_release)
        # ⚠️ 先激活浮层的焦点上下文、再把焦点交给 Entry：顺序反了的话
        # focus_force 会把焦点留在 Toplevel 上，键盘仍然打不进搜索框。
        # 抢不到也不致命 —— 正文那条打字过滤路径仍然完整可用。
        try:
            win.update_idletasks()
            win.focus_force()
            self._entry.focus_set()
        except Exception:
            pass
        return self

    def _build_search(self, query):
        """顶部搜索行：浅底 + 淡描边，空时给占位提示。

        输入框不再用整块 accent 描边 —— 那会和下面结果区形成两个「各自描边
        的框」。现在描边比卡片外沿还淡，聚焦时只提亮一档，整片看起来是同一张卡。
        """
        p = self.pal
        editor = p.get("editor", "#ffffff")
        ink = p.get("text", "#1f1f24")
        field_bg = self._field_bg
        soft = p.get("soft", "#76767e")
        ph_fg = _mix_hex(field_bg, soft, 0.9)
        bar = tk.Frame(self._card, bg=editor, height=self.SEARCH_H)
        bar.pack(fill=tk.X)
        bar.pack_propagate(False)
        self._search_frame = bar
        field = tk.Frame(bar, bg=field_bg, bd=0,
                         highlightthickness=self.BORDER,
                         highlightbackground=self._field_line,
                         highlightcolor=self._focus_line)
        field.pack(fill=tk.X, padx=self.PAD, pady=dpi.px(7))
        var = tk.StringVar(value=query or "")
        self._query_var = var
        ent = tk.Entry(field, textvariable=var, bd=0, relief="flat",
                       highlightthickness=0, bg=field_bg, fg=ink,
                       insertbackground=p.get("accent", "#333333"),
                       font=(FONT[0], FONT[1] - 1))
        ent.pack(side=tk.LEFT, fill=tk.X, expand=True,
                 padx=(dpi.px(8), 0), pady=dpi.px(2))
        self._entry = ent
        # 占位是 Entry 的**子 Label**：Tk 的 Entry 没有 placeholder，塞进
        # textvariable 会污染 query（查找词、候选过滤全读这个变量）。
        # 只看变量空不空、不看焦点 —— 空框一直要看得见提示。
        ph = tk.Label(ent, text="搜索笔记（标题 / 文件夹）", bg=field_bg,
                      fg=ph_fg, bd=0, font=(FONT[0], FONT[1] - 2),
                      cursor="xterm")
        self._ph = ph
        clear = tk.Label(field, text="✕", bg=field_bg, fg=soft, bd=0,
                         font=(FONT[0], FONT[1] - 3), cursor="hand2")
        clear.pack(side=tk.RIGHT, padx=(0, dpi.px(8)))
        self._clear_btn = clear

        def sync(*_a):
            try:
                if var.get():
                    ph.place_forget()
                else:
                    ph.place(x=dpi.px(5), rely=0.5, anchor="w")
            except Exception:
                pass

        var.trace_add("write", sync)
        var.trace_add("write", lambda *_a: self._on_query_changed())
        clear.bind("<Button-1>",
                   lambda e: (self.set_query(""), ent.focus_set(), "break")[1])
        ph.bind("<Button-1>", lambda e: (ent.focus_set(), "break")[1])
        # 浮层不做 grab，键盘事件要自己认领：焦点在这里时正文那组绑定收不到。
        for seq in ("<Return>", "<KP_Enter>", "<Tab>"):
            ent.bind(seq, lambda e: (self._confirm(), "break")[1])
        ent.bind("<Up>", lambda e: (self.move(-1), "break")[1])
        ent.bind("<Down>", lambda e: (self.move(1), "break")[1])
        ent.bind("<Escape>", lambda e: (self._dismiss(), "break")[1])
        # 浮层不 grab、Tk 事件也不冒泡：滚轮绑在结果 Canvas 上就够了，但鼠标
        # 停在搜索行时同样应该能翻列表，所以搜索行里每个控件都补一份。
        for wdg in (bar, field, ent, clear, ph):
            wdg.bind("<MouseWheel>", self._on_wheel)
        sync()

    def _on_query_changed(self):
        cb = self.on_query
        if cb is not None and self.is_open():
            cb(self.query)

    def _confirm(self):
        """搜索框里按 Enter / Tab：有候选就插入，没有就收起（不留着空面板）。"""
        if self._items:
            self.accept()
        else:
            self._dismiss()

    def _dismiss(self):
        """Esc / 空列表回车：走 app_actions 的统一收口，顺带清掉插入起点。"""
        cb = self.on_close
        if cb is not None:
            cb()
        else:
            self.close()

    def close(self):
        win, self._win = self._win, None
        ent, self._entry = self._entry, None
        self._canvas = None
        self._card = None
        self._rows = []
        self._thumb = None
        self._track_geom = None
        self._scroll_drag = None
        self._scroll_hover = False
        self._search_frame = None
        self._query_var = None
        self._ph = None
        self._clear_btn = None
        if win is None:
            return
        # 焦点若还留在即将销毁的 Entry 上，先还给正文，否则用户接着打字会
        # 全部落进一个已经不存在的输入框（表现是「Esc 之后打不了字」）。
        try:
            if ent is not None and self.focus_back is not None \
                    and win.focus_get() is ent:
                self.focus_back.focus_set()
        except Exception:
            pass
        try:
            win.destroy()
        except Exception:
            pass

    def _height(self):
        """结果 Canvas 的高度（不含顶部搜索行、不含外沿描边）。

        只按**一屏可见行数**算，不按候选总数算 —— 笔记再多弹框也不长高，
        多出来的靠滚动条翻。少于 8 条时仍按实际条数收缩，空态走 EMPTY_H。
        """
        n = len(self._items)
        body = self.EMPTY_H if not n else min(n, self.MAX_ROWS) * self.ROW_H
        return self.HEAD_H + body + self.FOOT_H

    def total_h(self):
        """整窗高度 = 搜索行 + 结果区 + 上下各 1px 外沿描边。

        clamp_pos / geometry 必须用这个值，只算结果区会把底缘顶出屏幕。
        """
        return self.SEARCH_H + self._height() + self.BORDER * 2

    # ---------- 内容 ----------

    def set_items(self, items):
        """过滤条件变了：换内容 + 重排尺寸（选中项与滚动位置都回到第一行）。"""
        self._items = list(items or [])
        self._sel = 0
        self._hover = -1
        self._top = 0
        self._thumb = None
        self._track_geom = None
        self._scroll_drag = None
        self._scroll_hover = False
        if self._win is None or self._canvas is None:
            return
        h = self._height()
        try:
            self._canvas.config(height=h)
            self._win.geometry("%dx%d" % (self.OUTER_W, self.total_h()))
        except Exception:
            pass
        self._render()

    def set_query(self, q):
        """正文里打了字：把查询词同步进搜索框（显示 + 触发 on_query 重算候选）。"""
        if self._query_var is None:
            return
        q = q or ""
        if self._query_var.get() != q:
            self._query_var.set(q)

    def move(self, delta):
        """键盘上下：返回 True 表示这次按键被吃掉了。

        选中项可能跑出视口（比如一路按 ↓ 到第 12 条），这里顺手把视口
        跟过去，否则高亮看不见、Enter 却插了屏外那篇。
        """
        if self._win is None or not self._items:
            return False
        self._sel = (self._sel + delta) % len(self._items)
        self._hover = -1
        self._ensure_visible()
        self._render()
        return True

    def _ensure_visible(self):
        vis = self._visible_rows()
        if vis <= 0:
            return
        if self._sel < self._top:
            self._top = self._sel
        elif self._sel >= self._top + vis:
            self._top = self._sel - vis + 1
        self._top = max(0, min(self._top, self._max_top()))

    def _set_top(self, value):
        """滚动到某一行；选中项若被滚出视口，就落在新的首行上。"""
        value = max(0, min(int(value), self._max_top()))
        if value == self._top:
            return
        self._top = value
        self._hover = -1
        vis = self._visible_rows()
        if self._items and not (self._top <= self._sel < self._top + vis):
            self._sel = self._top
        self._render()

    def accept(self):
        """确认选择：回调 on_pick(item)，随后自行关闭。"""
        item = self.current
        if item is None:
            return None
        cb = self.on_pick
        self.close()
        if cb is not None:
            cb(item)
        return item

    # ---------- 滚动条几何 ----------

    def _scroll_geom(self):
        """拇指 / 轨道的像素位置；没溢出时返回 None。"""
        total, vis = len(self._items), self._visible_rows()
        if total <= vis:
            return None
        w = self.WIDTH
        x0 = w - self.SCROLL_GAP - self.SCROLL_W
        x1 = w - self.SCROLL_GAP
        y0 = self.HEAD_H + self.SCROLL_INSET
        y1 = self.HEAD_H + vis * self.ROW_H - self.SCROLL_INSET
        track = max(1, y1 - y0)
        th = min(track, max(self.SCROLL_MIN,
                            int(round(track * vis / float(total)))))
        room = max(1, track - th)
        ty0 = y0 + room * (self._top / float(self._max_top()))
        return (x0, ty0, x1, ty0 + th, y0, y1, track, th)

    def _in_thumb(self, x, y):
        t = self._thumb
        return t is not None and t[0] <= x <= t[2] and t[1] <= y <= t[3]

    def _scroll_hit_x(self):
        """右侧留白都算滚动条命中区（文字右对齐停在 PAD 处，不会误伤）。"""
        return self.WIDTH - self.PAD + dpi.px(2)

    def _scroll_by(self, delta):
        self._set_top(self._top + delta)

    # ---------- 绘制 ----------

    def _render(self):
        c, p = self._canvas, self.pal
        if c is None:
            return
        w = self.WIDTH
        c.delete("all")
        # 不再画第二个带 outline 的矩形：Canvas 底色就是卡片底色，和上面的
        # 搜索行连成一片；整张浮层只有窗口外沿那一圈描边。
        head = "关联笔记" if not self.query else "搜索结果"
        c.create_text(self.PAD, self.HEAD_TEXT_Y, anchor="w", text=head,
                      font=(FONT[0], FONT[1] - 2), fill=p.get("soft", "#76767e"))
        y = self.HEAD_H
        vis = self._visible_rows()
        need_scroll = len(self._items) > vis
        # 有滚动条时文字右缘、高亮右界都再往左让一点，别贴着拇指
        if need_scroll:
            text_right = row_right = w - self.PAD - self.SB_TEXT_GAP
        else:
            text_right = w - self.PAD
            row_right = w - dpi.px(6)
        self._rows = []
        if not self._items:
            c.create_text(self.PAD, y + self.EMPTY_H / 2.0, anchor="w",
                          text="没有匹配的笔记", font=(FONT[0], FONT[1] - 1),
                          fill=p.get("soft", "#76767e"))
            y += self.EMPTY_H
        for off in range(vis):
            idx = self._top + off          # _rows 存的是**真实下标**，不是屏内序号
            it = self._items[idx]
            y0, y1 = y, y + self.ROW_H
            self._rows.append((y0, y1, idx))
            on = (idx == self._sel) or (idx == self._hover)
            if on:
                fg = p.get("selfg", p.get("text", "#1f1f24"))
                c.create_rectangle(dpi.px(6), y0 + dpi.px(1),
                                   row_right, y1 - dpi.px(1),
                                   fill=p.get("sel", "#cfe0fb"), outline="")
            else:
                fg = p.get("text", "#1f1f24")
            cy = (y0 + y1) / 2.0
            ix = self.PAD + self.ICON_S / 2.0
            _draw_icon(c, "lock" if it.get("locked") else "pencil",
                       ix, cy, self.ICON_S, fg)
            tx = ix + self.ICON_S / 2.0 + dpi.px(7)
            sub = it.get("sub") or ""
            sub_w = self._measure(sub, True) + dpi.px(12) if sub else 0
            avail = text_right - tx - sub_w
            c.create_text(tx, cy, anchor="w",
                          text=self._clip(it.get("title") or "", avail),
                          font=(FONT[0], FONT[1]), fill=fg)
            if sub:
                c.create_text(text_right, cy, anchor="e",
                              text=self._clip(sub, sub_w, small=True),
                              font=(FONT[0], FONT[1] - 2),
                              fill=p.get("soft", "#76767e"))
            y = y1
        c.create_text(self.PAD, y + self.FOOT_TEXT_DY, anchor="w",
                      text="↑↓ 选择 · Enter 插入 · Esc 取消",
                      font=(FONT[0], FONT[1] - 2), fill=p.get("soft", "#76767e"))
        self._draw_scrollbar(c)

    def _draw_scrollbar(self, c):
        g = self._scroll_geom()
        if g is None:
            self._thumb = None
            self._track_geom = None
            return
        x0, ty0, x1, ty1, y0, y1, track, th = g
        self._thumb = (x0, ty0, x1, ty1)
        self._track_geom = (y0, y1, track, th)
        c.create_rectangle(x0, y0, x1, y1, fill=self._sb_track, outline="")
        fill = (self._sb_thumb_drag if self._scroll_drag is not None
                else self._sb_thumb_hi if self._scroll_hover
                else self._sb_thumb)
        c.create_rectangle(x0, ty0, x1, ty1, fill=fill, outline="")

    def _measure(self, s, small=False):
        f = self._font_small if small else self._font
        try:
            return f.measure(s)
        except Exception:
            return len(s) * (FONT[1] + 1)

    def _clip(self, s, avail, small=False):
        """按像素宽度截断，超了补省略号（Tk 不会自动省略）。"""
        if self._font is None or avail <= dpi.px(8):
            return s
        if self._measure(s, small) <= avail:
            return s
        lo = 0
        for i in range(1, len(s) + 1):
            if self._measure(s[:i] + "…", small) > avail:
                break
            lo = i
        return s[:lo] + "…"

    # ---------- 鼠标 ----------

    def _row_at(self, ey):
        for y0, y1, idx in self._rows:
            if y0 <= ey <= y1:
                return idx
        return -1

    def _on_motion(self, event):
        if self._scroll_drag is not None:
            return
        idx = self._row_at(event.y)
        over = self._in_thumb(event.x, event.y)
        changed = False
        if idx != self._hover:
            self._hover = idx
            if idx >= 0:
                self._sel = idx       # 鼠标指到哪就选到哪，和键盘共用同一份选中
            changed = True
        if over != self._scroll_hover:
            self._scroll_hover = over
            changed = True
        if changed:
            self._render()

    def _on_leave(self, event):
        if self._hover != -1 or self._scroll_hover:
            self._hover = -1
            self._scroll_hover = False
            self._render()

    def _on_wheel(self, event):
        """Windows：一格 120，按 40 折算成 3 行（和主界面细滚动条同一手感）。"""
        d = getattr(event, "delta", 0)
        if d:
            step = -int(d // 40)
            self._scroll_by(step or (-1 if d > 0 else 1))
        return "break"

    def _wheel_dir(self, delta):
        """Linux / 旧平台的 Button-4 / Button-5。"""
        self._scroll_by(delta)
        return "break"

    def _on_scroll_drag(self, event):
        if self._scroll_drag is None or self._track_geom is None:
            return
        y0, _y1, track, th = self._track_geom
        room = max(1, track - th)
        frac = (event.y - self._scroll_drag - y0) / float(room)
        self._set_top(int(round(frac * self._max_top())))

    def _on_scroll_release(self, event):
        if self._scroll_drag is not None:
            self._scroll_drag = None
            self._render()
        return "break"

    def _on_click(self, event):
        if self._thumb is not None and event.x >= self._scroll_hit_x():
            x0, ty0, _x1, ty1 = self._thumb
            if ty0 <= event.y <= ty1:
                self._scroll_drag = event.y - ty0
                self._render()
            else:                       # 点轨道 = 朝那个方向翻一页
                vis = self._visible_rows()
                self._scroll_by(-vis if event.y < ty0 else vis)
            return "break"
        idx = self._row_at(event.y)
        if idx < 0:
            return
        self._sel = idx
        self.accept()
