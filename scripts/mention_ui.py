# -*- coding: utf-8 -*-
"""`@` 关联笔记的候选浮层：无边框 Toplevel + Canvas 自绘。

与 PopupMenu（右键菜单）的区别只有一处但很关键：**不做 grab、不抢焦点**。
用户要在浮层弹出期间继续在正文里打字（那是过滤条件），grab 会把后续击键
派发到浮层上，正文就打不进字了。因此键盘完全交给正文：
↑ ↓ 移动、Enter 选中、Esc 取消，都由 app_actions 转发给这里的 move() / accept()。

代价是浮层拿不到键盘事件，鼠标点击仍然照常 —— 这是刻意的选择。
"""

import tkinter as tk
import tkinter.font as tkfont

from theme import FONT
from widgets import _draw_icon

__all__ = ["MentionPopup"]


class MentionPopup(object):
    """候选列表浮层。用法：open(items, x, y) → set_items(过滤后) → accept()。"""

    WIDTH = 340
    ROW_H = 30
    HEAD_H = 20
    FOOT_H = 18
    MAX_ROWS = 8
    PAD = 8
    ICON_S = 14
    EMPTY_H = 30

    def __init__(self, master, pal, on_pick=None):
        self.master = master
        self.pal = dict(pal)
        self.on_pick = on_pick
        self._items = []
        self._sel = 0
        self._hover = -1
        self._win = None
        self._canvas = None
        self._rows = []          # [(y0, y1, index)]
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

    # ---------- 打开 / 关闭 ----------

    def open(self, items, x, y):
        """在屏幕坐标 (x, y) 弹出；靠近屏幕下沿时翻到光标上方。"""
        self._items = list(items or [])
        self._sel = 0
        self._hover = -1
        p = self.pal
        win = tk.Toplevel(self.master)
        win.overrideredirect(True)
        try:
            win.attributes("-topmost", True)
        except Exception:
            pass
        win.configure(bg=p.get("line", "#cfcfd6"))
        self._win = win
        w = self.WIDTH
        h = self._height()
        c = tk.Canvas(win, width=w, height=h, bd=0, highlightthickness=0,
                      bg=p.get("line", "#cfcfd6"))
        c.pack()
        self._canvas = c
        self._render()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        px = max(0, min(int(x), sw - w - 4))
        py = int(y)
        if py + h > sh - 4:                 # 下方放不下 → 翻到光标上方
            py = max(0, py - h - 22)
        win.geometry("+%d+%d" % (px, py))
        c.bind("<Motion>", self._on_motion)
        c.bind("<Button-1>", self._on_click)
        c.bind("<Leave>", self._on_leave)
        return self

    def close(self):
        win, self._win = self._win, None
        self._canvas = None
        self._rows = []
        if win is None:
            return
        try:
            win.destroy()
        except Exception:
            pass

    def _height(self):
        n = len(self._items) or 1
        n = min(n, self.MAX_ROWS)
        body = self.EMPTY_H if not self._items else n * self.ROW_H
        return self.PAD * 2 + self.HEAD_H + body + self.FOOT_H

    # ---------- 内容 ----------

    def set_items(self, items):
        """过滤条件变了：换内容 + 重排尺寸（选中项回到第一行）。"""
        self._items = list(items or [])
        self._sel = 0
        self._hover = -1
        if self._win is None or self._canvas is None:
            return
        h = self._height()
        try:
            self._canvas.config(height=h)
            self._win.geometry("%dx%d" % (self.WIDTH, h))
        except Exception:
            pass
        self._render()

    def move(self, delta):
        """键盘上下：返回 True 表示这次按键被吃掉了。"""
        if self._win is None or not self._items:
            return False
        self._sel = (self._sel + delta) % len(self._items)
        self._hover = -1
        self._render()
        return True

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

    # ---------- 绘制 ----------

    def _render(self):
        c, p = self._canvas, self.pal
        if c is None:
            return
        w, h = self.WIDTH, self._height()
        c.delete("all")
        c.create_rectangle(1, 1, w - 1, h - 1,
                           fill=p.get("editor", "#ffffff"),
                           outline=p.get("line", "#cfcfd6"), width=1)
        y = self.PAD
        c.create_text(self.PAD, y, anchor="w", text="关联笔记",
                      font=(FONT[0], FONT[1] - 2), fill=p.get("soft", "#76767e"))
        y += self.HEAD_H
        self._rows = []
        if not self._items:
            c.create_text(self.PAD, y + self.EMPTY_H / 2.0, anchor="w",
                          text="没有匹配的笔记", font=(FONT[0], FONT[1] - 1),
                          fill=p.get("soft", "#76767e"))
        for idx, it in enumerate(self._items[:self.MAX_ROWS]):
            y0, y1 = y, y + self.ROW_H
            self._rows.append((y0, y1, idx))
            on = (idx == self._sel) or (idx == self._hover)
            if on:
                fg = p.get("selfg", p.get("text", "#1f1f24"))
                c.create_rectangle(4, y0 + 1, w - 4, y1 - 1,
                                   fill=p.get("sel", "#cfe0fb"), outline="")
            else:
                fg = p.get("text", "#1f1f24")
            cy = (y0 + y1) / 2.0
            ix = self.PAD + self.ICON_S / 2.0
            _draw_icon(c, "lock" if it.get("locked") else "pencil",
                       ix, cy, self.ICON_S, fg)
            tx = ix + self.ICON_S / 2.0 + 7
            sub = it.get("sub") or ""
            sub_w = self._measure(sub, True) + 12 if sub else 0
            avail = w - self.PAD - tx - sub_w
            c.create_text(tx, cy, anchor="w",
                          text=self._clip(it.get("title") or "", avail),
                          font=(FONT[0], FONT[1]), fill=fg)
            if sub:
                c.create_text(w - self.PAD, cy, anchor="e",
                              text=self._clip(sub, sub_w, small=True),
                              font=(FONT[0], FONT[1] - 2),
                              fill=p.get("soft", "#76767e"))
            y = y1
        c.create_text(self.PAD, h - self.PAD - 2, anchor="w",
                      text="↑↓ 选择 · Enter 插入 · Esc 取消",
                      font=(FONT[0], FONT[1] - 2), fill=p.get("soft", "#76767e"))

    def _measure(self, s, small=False):
        f = self._font_small if small else self._font
        try:
            return f.measure(s)
        except Exception:
            return len(s) * (FONT[1] + 1)

    def _clip(self, s, avail, small=False):
        """按像素宽度截断，超了补省略号（Tk 不会自动省略）。"""
        if self._font is None or avail <= 8:
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
        idx = self._row_at(event.y)
        if idx != self._hover:
            self._hover = idx
            if idx >= 0:
                self._sel = idx       # 鼠标指到哪就选到哪，和键盘共用同一份选中
            self._render()

    def _on_leave(self, event):
        if self._hover != -1:
            self._hover = -1
            self._render()

    def _on_click(self, event):
        idx = self._row_at(event.y)
        if idx < 0:
            return
        self._sel = idx
        self.accept()
