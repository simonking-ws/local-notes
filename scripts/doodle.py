# -*- coding: utf-8 -*-
"""素笺 —— 涂鸦标注层（临时模态，关闭即丢弃，绝不落库）。

设计要点（改动前先读）：

* **不碰正文数据**：笔迹全部画在一块独立的、色键透明的 `Toplevel` Canvas 上，
  不是 Text 里嵌 window / image。所以 `serialize()`、撤销栈、备份、数据库
  一律看不到它；`close()` 直接 `destroy()` 整层，笔画随之蒸发。
* **不能编辑**：进入时 `text.config(state=DISABLED)` + 蒙层窗口 `grab_set()`
  吃掉主窗全部输入；退出按快照逐项还原（state / 光标 / 选区 / yview / 焦点），
  包括「本来就是只读笔记」和「本来就没选中笔记」两种情况。
* **覆盖整个界面**：蒙层贴的是 `root` 的**客户区**，不是 `editor_frame`。
  顶栏 / 侧栏 / 列表 / 正文 / 状态条一起进网点，视觉上的「整个软件都被锁住」
  才和 grab 的实际范围一致（2026-11-06 用户要求）。
* **色键透明**：Canvas 底色设成 `KEY`，Windows 上 `-transparentcolor` 让它整体透明，
  正文透出来；薄雾只用 `stipple` 网点（实色点不透明、其余透），笔迹和工具条
  用实色画在上面。⚠️ 蒙层必须 `-topmost` + `lift()`，否则主窗置顶时压在底下，
  整层看起来「没出现」（预览阶段实测过）。
* **方案 C 图标**：工具条是纯图标的细线稿，1.7px 描边、16px 格；挖空的图标必须
  把**当前底片色**传进来填，写死工具条底色会在选中态留一块白洞。
* **序号工具**（参考截图 ①）：点一下放一个带编号的圆标记，撤销 / 清空会重算编号。

`DoodleLayer` 只依赖 dpi / theme / widgets，不反向 import 主程序，便于单测。
"""
import tkinter as tk

import dpi
from theme import FONT, _doodle_veil
from widgets import ICON_SIZE, ICON_STROKE, _draw_icon, _round_rect_points

# 色键：取一个几乎不可能被皮肤或笔画用到的颜色。深色主题里 ink 是白，也撞不上。
KEY = "#010203"

# 固定调色板（标注笔不跟着换肤走，跟截图工具一样）
COLORS = ("#e4483b", "#2f7cf6", "#e0a020", "#16a34a")
WIDTHS = (dpi.fx(2), dpi.fx(4), dpi.fx(6))       # 实际笔宽
WIDTH_ICONS = (1, 2.5, 4)                        # 粗细按钮里那根示意线

CELL = dpi.px(24)                 # 单枚按钮格
CELL_MIN = dpi.px(16)             # 窄窗口压缩下限
SEP_W = dpi.px(11)                # 分组竖线占的宽
PAD = dpi.px(7)                   # 工具条左右内边距
BAR_H = dpi.px(36)                # 工具条高
BOTTOM_GAP = dpi.px(22)           # 工具条底距蒙层底
HINT_H = dpi.px(24)               # 工具条上方那片提示文字高
HINT_GAP = dpi.px(6)
VEIL_STIPPLE = "gray12"

TOOLS = (
    ("pencil_line", "画笔", "按住拖动自由画"),
    ("line", "直线", "拖出一条直线"),
    ("arrow", "箭头", "拖出带箭头的线"),
    ("rect", "矩形", "拖出方框"),
    ("ellipse", "椭圆", "拖出椭圆"),
    ("number", "序号", "点一下放一个编号标记"),   # ← 参考截图 A 方案里的 ①
)
ACTIONS = (
    ("undo", "撤销", "撤销上一笔"),
    ("clear", "清空", "清掉全部涂鸦"),
    ("close", "退出", "退出涂鸦（不保存）"),
)
GLYPH_OF_ACTION = {"undo": "undo", "clear": "trash_line", "close": "close"}
DEFAULT_HINT = "涂鸦模式 · 按住工具条可拖动 · Esc 退出（不保存）"


# ---------------------------------------------------------------- 图标
def _draw_glyph(cv, name, cx, cy, s, color, bg, stroke=None):
    """16px 统一细线稿图标。`bg` 是**此刻图标所在的那层底色**，用来挖空。

    复用项目 `_draw_icon("undo")`；其余是涂鸦专用分支，全矢量整数图元，
    不塞 emoji / 字体符号（Tcl/Tk 不支持 U+FFFF 以上字符）。
    """
    st = ICON_STROKE if stroke is None else stroke
    x0, y0 = cx - s / 2.0, cy - s / 2.0
    x1, y1 = x0 + s, y0 + s
    kw = dict(fill=color, width=st, capstyle=tk.ROUND, joinstyle=tk.ROUND)
    ln = lambda *pts: cv.create_line(*pts, **kw)

    if name == "undo":
        _draw_icon(cv, "undo", cx, cy, s, color, stroke=st)
    elif name == "pencil_line":
        # 参考截图里的空心铅笔。⚠️ 内孔填当前底片色，不能写死工具条底色
        cv.create_polygon(x0 + s * 0.62, y0 + s * 0.12,
                          x0 + s * 0.88, y0 + s * 0.38,
                          x0 + s * 0.44, y0 + s * 0.82,
                          x0 + s * 0.16, y0 + s * 0.88,
                          x0 + s * 0.18, y0 + s * 0.60,
                          fill=bg, outline=color, width=st)
        ln(x0 + s * 0.50, y0 + s * 0.24, x0 + s * 0.76, y0 + s * 0.50)
        ln(x0 + s * 0.16, y0 + s * 0.60, x0 + s * 0.44, y0 + s * 0.82)
    elif name == "line":
        ln(x0 + s * 0.08, y1 - s * 0.14, x1 - s * 0.08, y0 + s * 0.14)
    elif name == "arrow":
        ln(x0 + s * 0.08, y1 - s * 0.08, x1 - s * 0.08, y0 + s * 0.08)
        ln(x1 - s * 0.44, y0 + s * 0.08, x1 - s * 0.08, y0 + s * 0.08,
           x1 - s * 0.08, y0 + s * 0.44)
    elif name == "rect":
        cv.create_rectangle(x0 + s * 0.06, y0 + s * 0.16,
                            x1 - s * 0.06, y1 - s * 0.16,
                            outline=color, width=st)
    elif name == "ellipse":
        # ⚠️ 16px 下 oval 会光栅成略带棱角的圆，实测仍规整；块面八边形近似反而太重。
        cv.create_oval(x0 + s * 0.06, y0 + s * 0.16,
                       x1 - s * 0.06, y1 - s * 0.16,
                       outline=color, width=st)
    elif name == "number":
        # ① 的矢量写法：外圈 + 一个「1」，不用字形（5pt 数字会糊成一团）。
        # ⚠️ 「1」要按**墨迹**居中：斜笔起点与竖杆必须关于 cx 对称。早先是
        # 斜笔 -0.20s → 竖杆 -0.04s，整字明显偏左，16px 下一眼就看得出没居中对齐。
        cv.create_oval(x0 + s * 0.06, y0 + s * 0.06,
                       x1 - s * 0.06, y1 - s * 0.06,
                       outline=color, width=st)
        cv.create_line(cx - s * 0.13, cy - s * 0.07,
                       cx + s * 0.11, cy - s * 0.22,
                       cx + s * 0.11, cy + s * 0.22,
                       fill=color, width=st, capstyle=tk.ROUND,
                       joinstyle=tk.ROUND, tags=("num1",))
    elif name == "trash_line":
        ln(x0 + s * 0.18, y0 + s * 0.28, x0 + s * 0.82, y0 + s * 0.28)
        ln(x0 + s * 0.39, y0 + s * 0.17, x0 + s * 0.61, y0 + s * 0.17)
        ln(x0 + s * 0.27, y0 + s * 0.28, x0 + s * 0.33, y0 + s * 0.85,
           x0 + s * 0.67, y0 + s * 0.85, x0 + s * 0.73, y0 + s * 0.28)
    elif name == "close":
        ln(x0 + s * 0.20, y0 + s * 0.20, x1 - s * 0.20, y1 - s * 0.20)
        ln(x1 - s * 0.20, y0 + s * 0.20, x0 + s * 0.20, y1 - s * 0.20)


def _build_items():
    """工具条从左到右：6 工具 | 4 色 | 3 粗细 | 撤销 清空 | 退出。"""
    items = []
    for key, label, tip in TOOLS:
        items.append(dict(kind="tool", key=key, label=label, tip=tip, w=CELL))
    items.append(dict(kind="sep", w=SEP_W))
    for i, c in enumerate(COLORS):
        items.append(dict(kind="color", key=i, label="颜色",
                          tip="第 %d 色" % (i + 1), w=CELL))
    items.append(dict(kind="sep", w=SEP_W))
    for i in range(len(WIDTHS)):
        items.append(dict(kind="width", key=i, label="粗细",
                          tip=("细", "中", "粗")[i], w=CELL))
    for key, label, tip in ACTIONS[:2]:
        items.append(dict(kind="action", key=key, label=label, tip=tip, w=CELL))
    items.append(dict(kind="sep", w=SEP_W))
    key, label, tip = ACTIONS[2]
    items.append(dict(kind="action", key=key, label=label, tip=tip, w=CELL))
    return items


class DoodleLayer(object):
    """一层蒙窗口 = 一次涂鸦会话。`open()` / `close()` 幂等。"""

    def __init__(self, app, pal):
        self.app = app
        self.pal = dict(pal)
        self.colors = _doodle_veil(self.pal)
        self.win = None
        self.cv = None
        self.bar = None
        self._geom = None
        self._w = self._h = 0
        self._bar_w = 0
        self._bar_at = (0, 0)     # 工具条左上角（Canvas 坐标），提示片 / 落笔拦截都按它算
        self._bar_pos = None      # None = 默认（底部居中）；用户拖动过就是绝对坐标
        self._bar_drag = None     # {"x_root", "y_root", "x0", "y0", "moved"}
        self._hint_box = None     # 提示片矩形，点它不该起笔
        self._transparent = False   # relayout() 比 attributes 探测更早跑，先给默认值
        self._pad = PAD
        self._widths = []
        self._items = _build_items()
        self._strokes = []        # [([图元 id...], kind)]，撤销按「一笔」弹
        self._cur = None          # 正在画的那一笔的图元 id
        self._draw = None         # {"kind", "x0", "y0"}；非空=正在拖
        self._tool = TOOLS[0][0]
        self._color = 0
        self._width = 1
        self._seq = 1
        self._hover = None
        self._press = None
        self._hint = None
        self._hint_job = None
        self._map_job = None
        self._snap = None
        self._grab_prev = None
        self._grab_ok = False
        self._esc_id = None
        self._root_ids = []       # 主窗 Map / Unmap 的绑定（最小化跟随）
        self._fb_ids = []         # grab 没拿到时的鼠标兜底绑定

    @property
    def active(self):
        return self.win is not None

    # ------------------------------------------------------------ 生命周期
    def open(self):
        if self.win is not None:
            return False
        app = self.app
        # 会抢焦点 / 改正文的浮层先收掉，别让它们和蒙层叠在一起
        for name in ("_mention_close", "_find_close"):
            try:
                getattr(app, name)()
            except Exception:
                pass
        t = app.text
        try:
            self._snap = {
                "state": t.cget("state"),
                "insert": t.index(tk.INSERT),
                "sel": tuple(str(i) for i in t.tag_ranges("sel")),
                "yview": t.yview()[0],
            }
        except tk.TclError:
            return False
        # ⚠️ 锁正文放在**建窗全部成功之后**：项目里「只读笔记不设 DISABLED」是
        # 为了长期只读还能复制，这里是临时模态；万一上面哪步抛异常，正文还没被锁，
        # 不会留下一个 DISABLED 的编辑器。退出时按快照逐项还原。

        win = tk.Toplevel(app.root)
        win.overrideredirect(True)
        self.win = win
        # 每次进来都回到默认位置：蒙层是临时层，上次拖到哪不该影响下一次
        self._bar_pos = None
        self._bar_drag = None
        self._hint_box = None
        cv = tk.Canvas(win, bd=0, highlightthickness=0, bg=KEY)
        cv.pack(fill=tk.BOTH, expand=True)
        self.cv = cv
        bar = tk.Canvas(win, bd=0, highlightthickness=0, bg=self.colors["bar"])
        self.bar = bar

        self.relayout(force=True)
        win.update_idletasks()
        # ⚠️ 色键是 Windows 特性；非 Windows 降级成「薄雾不画、只留 1px 内沿 +
        # 笔迹」，功能不丢但正文会被底色盖住（Tk 无跨平台真透明窗口）。
        self._transparent = False
        try:
            win.attributes("-transparentcolor", KEY)
            self._transparent = True
        except Exception:
            pass
        try:
            win.attributes("-topmost", True)
            win.lift()
        except Exception:
            pass
        # 色键能不能用要等 attributes 试过才知道，按结果重画一次薄雾：
        # 不支持时退化成「只留 1px 内沿 + 笔迹」，功能不丢。
        self._draw_veil()
        win.update()
        self._bind()
        try:
            win.focus_force()
        except Exception:
            pass
        # grab 全局唯一：先记住原来的持有人，退出时还回去。
        try:
            self._grab_prev = app.root.grab_current()
            win.grab_set()
            self._grab_ok = True
        except Exception:
            self._grab_prev = None
            self._grab_ok = False
        # 没拿到 grab 时主窗照收鼠标，事件按命中测试落到各个控件上；
        # 这时用 bind_all 兜住 —— 它在每条事件 bindtags 的末尾，透明区也收得到。
        if not self._grab_ok:
            self._bind_mouse_fallback()
        # 最小化后蒙层得跟着收起，否则会孤零零留在桌面上
        self._watch_root()
        # bind_all 不受 grab 影响，Escape 得单独挂一条并在退出时按 funcid 解绑
        try:
            self._esc_id = app.root.bind_all(
                "<Escape>", lambda e: (self.close(), "break")[1], add="+")
        except Exception:
            self._esc_id = None
        t.config(state=tk.DISABLED)
        # 提示片压在稿纸底部，不能一直挂着：默认展示 2.2s 自动收起，
        # 之后只有 hover 到某枚按钮才临时说明。取消一律走 app._cancel_after。
        self._set_hint(DEFAULT_HINT)
        self._schedule_hint_hide()
        return True

    def close(self):
        if self.win is None:
            return
        win, self.win = self.win, None
        app = self.app
        self._cancel_hint()
        self._cancel_map_job()
        self._unwatch_root()
        self._unbind_mouse_fallback()
        if self._esc_id is not None:
            try:
                app.root.unbind("<Escape>", self._esc_id)
            except Exception:
                pass
            self._esc_id = None
        try:
            win.grab_release()
        except Exception:
            pass
        try:
            win.destroy()          # 所有笔画随这一层一起消失
        except Exception:
            pass
        self.cv = self.bar = None
        self._strokes = []
        self._cur = None
        self._draw = None
        self._hover = self._press = None

        snap = self._snap or {}
        t = app.text
        try:
            t.config(state=snap.get("state", "normal"))
            t.mark_set(tk.INSERT, snap.get("insert", "1.0"))
            if len(snap.get("sel", ())) == 2:
                t.tag_add("sel", snap["sel"][0], snap["sel"][1])
            t.yview_moveto(snap.get("yview", 0.0))
            t.focus_set()
        except tk.TclError:
            pass
        if self._grab_prev is not None:
            try:
                if self._grab_prev.winfo_exists():
                    self._grab_prev.grab_set()
            except Exception:
                pass
        self._grab_prev = None

    def apply_theme(self, pal):
        self.pal = dict(pal)
        self.colors = _doodle_veil(self.pal)
        if not self.active:
            return
        self._draw_veil()
        self._draw_bar()
        self._draw_hint()

    def relayout(self, force=False):
        """把蒙层对齐到**整个主窗口客户区**。

        ⚠️ 2026-11-06 用户要求「蒙层覆盖整个软件界面」：以前贴的是 `editor_frame`，
        顶栏、侧栏、列表、状态条都露在蒙层外，看着像只罩住了稿纸。改贴 `root` 后，
        网点与 grab 的范围才和「整个软件都不能动」这件事一致。
        """
        if not self.active:
            return
        rt = self.app.root
        try:
            x, y = rt.winfo_rootx(), rt.winfo_rooty()
            w, h = rt.winfo_width(), rt.winfo_height()
        except tk.TclError:
            return
        if w <= 1 or h <= 1:
            return
        # 最小化期间 Windows 会把窗口挪到 -32000 那种离屏位置，尺寸也常是 1；
        # 这种值不能用来摆蒙层，等 `<Configure>` 再对齐。注意**不能**拦负坐标：
        # 主窗真在屏幕左侧外时，蒙层跟着它走才是对的。
        if x < -10000 or y < -10000:
            return
        if w < dpi.px(120) or h < dpi.px(120):
            return
        geom = (x, y, w, h)
        if not force and geom == self._geom:
            return
        self._geom = geom
        self._w, self._h = w, h
        self.win.geometry("%dx%d+%d+%d" % (w, h, x, y))
        self.cv.config(width=w, height=h)
        self._fit(w - dpi.px(16))
        self.bar.config(width=self._bar_w, height=BAR_H,
                        bg=self.colors["bar"])
        self._place_bar()
        self._draw_veil()
        self._draw_bar()
        self._draw_hint()

    def _place_bar(self):
        """按 `_bar_pos` 摆工具条；None 时回到「水平居中 + 贴底」。

        ⚠️ 一律用**绝对 place**：可拖动之后不能再写 `relx=0.5` —— 那样每次
        `relayout()` 都会把用户拖到的位置拽回底部中间。
        """
        bar = self.bar
        if bar is None or self.win is None:
            return
        if self._bar_pos is None:
            x = (self._w - self._bar_w) // 2
            y = self._h - BOTTOM_GAP - BAR_H
        else:
            x, y = self._bar_pos
        x, y = self._clamp_bar(x, y)
        if self._bar_pos is not None:
            self._bar_pos = (x, y)
        self._bar_at = (x, y)
        try:
            bar.place(x=x, y=y)
        except tk.TclError:
            pass

    def _clamp_bar(self, x, y):
        """把工具条夹在蒙层里。

        拖出边界就贴边：**绝不能允许整条拖到窗口外** —— 那样退出按钮也一起
        丢掉，用户只能按 Esc（还得知道有 Esc）。
        """
        max_x = max(0, self._w - self._bar_w)
        max_y = max(0, self._h - BAR_H)
        return (min(max(int(x), 0), max_x), min(max(int(y), 0), max_y))

    def _fit(self, avail):
        """窄编辑区里把每格按比例压到 CELL_MIN，绝不让工具条溢出被 clip。

        ⚠️ `self._pad` 是**单侧**内边距：`_draw_bar()` 拿它当第一格左沿、
        `_hit()` 拿它当命中起点，两处必须同一个口径。早先这里存的是「两侧
        合计」，结果右边没有留白、整排按钮的命中区还右移了整整一个 PAD。
        """
        total = sum(it["w"] for it in self._items)
        side = PAD
        if total + 2 * side <= avail:
            ws = [it["w"] for it in self._items]
        else:
            k = max(0.0, (avail - 2 * side) / float(total))
            ws = [max(CELL_MIN, int(it["w"] * k)) for it in self._items]
            if sum(ws) + 2 * side > avail:
                side = max(0, (avail - sum(ws)) // 2)
        self._pad = side
        self._widths = ws
        self._bar_w = min(avail, sum(ws) + 2 * side)

    # ------------------------------------------------------------ 绘制层
    def _draw_veil(self):
        cv = self.cv
        if cv is None:
            return
        cv.delete("veil")
        c = self.colors
        if self._transparent:
            # 只有网点不透明，其余像素是色键 → 正文仍然看得见
            cv.create_rectangle(0, 0, self._w, self._h, fill=c["veil"],
                                stipple=VEIL_STIPPLE, outline="", tags=("veil",))
        cv.create_rectangle(0, 0, self._w - 1, self._h - 1,
                            outline=c["rule"], width=1, tags=("veil",))
        cv.tag_lower("veil")

    def _draw_hint(self):
        cv = self.cv
        if cv is None:
            return
        cv.delete("hint")
        self._hint_box = None
        text = self._hint
        if not text:
            return          # 自动收起 / 鼠标离开：真的删掉提示片，别兜回默认文案
        c = self.colors
        # 中文按 12px/字估宽；提示片跟着工具条走 —— 工具条能拖，提示片钉死在
        # 「蒙层底部」就会脱节，拖到上半屏时提示还在底下，看着像两个零件。
        tw = len(text) * dpi.px(12) + dpi.px(20)
        bx, by = self._bar_at
        y0 = by - HINT_GAP - HINT_H
        if y0 < 0:                      # 工具条拖到顶部了，提示翻到它下面
            y0 = by + BAR_H + HINT_GAP
        x0 = bx + (self._bar_w - tw) // 2
        # 两头都夹进蒙层，窄窗口里宁可压住工具条也不能被裁掉半截
        x0 = min(max(x0, 0), max(0, self._w - tw))
        y0 = min(max(y0, 0), max(0, self._h - HINT_H))
        x1, y1 = x0 + tw, y0 + HINT_H
        self._hint_box = (x0, y0, x1, y1)
        cv.create_rectangle(x0, y0, x1, y1,
                            fill=c["bar"], outline=c["line"], width=1,
                            tags=("hint",))
        cv.create_text((x0 + x1) // 2, (y0 + y1) // 2, text=text,
                       fill=c["soft"], font=(FONT[0], 9), tags=("hint",))
        cv.tag_raise("hint")

    def _cancel_hint(self):
        if self._hint_job is not None:
            try:
                self.app._cancel_after(self._hint_job)
            except Exception:
                pass
            self._hint_job = None

    def _schedule_hint_hide(self):
        self._cancel_hint()
        try:
            # ⚠️ 延时是毫秒，不是像素：不能过 dpi.px()，高分屏会被放大成 3.3s
            self._hint_job = self.app._after(
                2200, lambda: self._set_hint(None))
        except Exception:
            self._hint_job = None

    def _set_hint(self, text):
        if text == self._hint:
            return
        self._hint = text
        self._draw_hint()
        if text is not None:
            # hover 出来的说明不该被「进入时那次」的自动收起定时器掐掉
            self._cancel_hint()

    def _is_selected(self, it):
        k = it["kind"]
        if k == "tool":
            return it["key"] == self._tool
        if k == "color":
            return it["key"] == self._color
        if k == "width":
            return it["key"] == self._width
        return False

    def _draw_bar(self):
        bar = self.bar
        if bar is None:
            return
        bar.delete("all")
        c = self.colors
        w, h = self._bar_w, BAR_H
        bar.create_rectangle(0, 0, w - 1, h - 1, fill=c["bar"],
                             outline=c["line"], width=1)
        acc = self._pad
        for i, it in enumerate(self._items):
            cw = self._widths[i] if self._widths else it["w"]
            if it["kind"] == "sep":
                bar.create_line(acc + cw / 2.0, dpi.px(8),
                                acc + cw / 2.0, h - dpi.px(8),
                                fill=c["line"])
            else:
                sel = self._is_selected(it)
                hov = (i == self._hover)
                bg = c["sel"] if sel else (c["hover"] if hov else c["bar"])
                if sel or hov or i == self._press:
                    bar.create_polygon(
                        _round_rect_points(acc + 1, dpi.px(3),
                                           acc + cw - 2, h - dpi.px(3),
                                           dpi.px(5)),
                        fill=bg, outline="")
                fg = c["active_fg"] if sel else c["ink"]
                cx, cy = acc + cw / 2.0, h / 2.0
                s = min(ICON_SIZE, cw - dpi.px(4))
                self._paint_item(bar, it, cx, cy, s, fg, bg)
            acc += cw

    def _paint_item(self, bar, it, cx, cy, s, fg, bg):
        k, key = it["kind"], it["key"]
        c = self.colors
        if k == "color":
            r = s * 0.33
            bar.create_oval(cx - r, cy - r, cx + r, cy + r,
                            fill=COLORS[key], outline="")
            if self._is_selected(it):
                bar.create_oval(cx - r - dpi.fx(2), cy - r - dpi.fx(2),
                                cx + r + dpi.fx(2), cy + r + dpi.fx(2),
                                outline=c["active_fg"], width=dpi.fx(1.5))
        elif k == "width":
            bar.create_line(cx - s * 0.42, cy, cx + s * 0.42, cy,
                            fill=fg, width=WIDTH_ICONS[key])
        elif k == "action":
            _draw_glyph(bar, GLYPH_OF_ACTION[key], cx, cy, s, fg, bg)
        else:
            _draw_glyph(bar, key, cx, cy, s, fg, bg)

    def _hit(self, x):
        acc = self._pad
        for i, it in enumerate(self._items):
            cw = self._widths[i] if self._widths else it["w"]
            if it["kind"] != "sep" and acc <= x < acc + cw:
                return i
            acc += cw
        return None

    # ------------------------------------------------------------ 事件
    def _bind(self):
        bar, win = self.bar, self.win
        # ⚠️ 起笔必须挂在**窗口级**，不能只挂 Canvas：色键透明的像素不参与命中
        # 测试，鼠标落在那里时事件目标是 Toplevel 本身（不是 Canvas），Canvas 的
        # 绑定压根不触发 —— 症状正是「涂鸦画不出来，偶尔点在网点/已有笔迹上又能
        # 画」。挂在 win 上就三条路齐了：透明区直接命中 win、网点区的事件沿
        # bindtags 冒到 win、工具条先自己 break 掉不会误起一笔。
        #
        # ⚠️ 也不要同时在 Canvas 上挂一份：网点像素上会同时触发两次，一次点按起两笔。
        win.bind("<ButtonPress-1>", self._on_press)
        win.bind("<B1-Motion>", self._on_motion)
        win.bind("<ButtonRelease-1>", self._on_release)
        # 锁定滚动：滚轮 / Linux 的 4·5 号键都吃掉，别让 bind_all 的兜底滚正文
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            win.bind(seq, lambda e: "break")
        bar.bind("<ButtonPress-1>", self._on_bar_press)
        bar.bind("<ButtonRelease-1>", self._on_bar_release)
        bar.bind("<Motion>", self._on_bar_motion)
        bar.bind("<Leave>", self._on_bar_leave)

    # ------------------------------------------------------------ 主窗跟随
    def _watch_root(self):
        """盯住主窗的 Map / Unmap：最小化时把蒙层一起收起，恢复时再放出来。"""
        root = self.app.root
        for seq, fn in (("<Unmap>", self._on_root_unmap),
                        ("<Map>", self._on_root_map)):
            try:
                self._root_ids.append((seq, root.bind(seq, fn, add="+")))
            except Exception:
                pass

    def _unwatch_root(self):
        for seq, fid in self._root_ids:
            try:
                self.app.root.unbind(seq, fid)
            except Exception:
                pass
        self._root_ids = []

    def _on_root_unmap(self, e=None):
        """主窗最小化 → 蒙层跟着隐藏。

        Map / Unmap 会从每个子控件往上冒到 Toplevel（实测 `e.widget` 给的是真正
        的来源），所以只认主窗自己那一次，免得折叠个工具栏就把蒙层收起来。
        """
        if not self.active or e is None or e.widget is not self.app.root:
            return
        try:
            self.win.withdraw()
        except tk.TclError:
            pass
        # 窗口一隐藏 grab 就掉了，恢复时要补
        self._grab_ok = False

    def _on_root_map(self, e=None):
        if not self.active or e is None or e.widget is not self.app.root:
            return
        try:
            # ⚠️ 别只认 "normal"：主窗最大化时 state() 是 "zoomed"，
            # 最小化再还原回来还是 zoomed —— 判死 normal 会让蒙层再也出不来。
            if self.app.root.state() in ("iconic", "withdrawn"):
                return
            win = self.win
            if win.winfo_ismapped():
                return
            self.app.root.update_idletasks()
            win.deiconify()
            win.lift()
        except tk.TclError:
            return
        self._regrab()
        # ⚠️ 还原那一刻 Windows 还在跑窗口动画，`editor_frame` 的 root 坐标会是
        # 过渡值（最大化的窗口实测短暂报过 -840 这种负坐标，照它摆会把蒙层直接
        # 扔到屏幕外）。先亮出来占位，等定位稳定了再按真实几何对齐。
        self._cancel_map_job()
        try:
            self._map_job = self.app._after(120, self._relayout_after_map)
        except Exception:
            self._map_job = None

    def _relayout_after_map(self):
        self._map_job = None
        if not self.active or not self.win.winfo_ismapped():
            return
        try:
            self.relayout(force=True)
        except Exception:
            pass

    def _cancel_map_job(self):
        if self._map_job is not None:
            try:
                self.app._cancel_after(self._map_job)
            except Exception:
                pass
            self._map_job = None

    def _regrab(self):
        """隐藏过的蒙层 grab 会掉，回来后补上，否则主窗又能编辑了。

        ⚠️ 这里**无条件**重设：窗口隐藏期间 Tk 的 `grab_current()` 仍可能报着
        本窗口，但系统层的捕获其实已经释放，靠它判断会漏补。
        """
        win = self.win
        if win is None:
            return
        try:
            win.grab_set()
            self._grab_ok = True
            self._unbind_mouse_fallback()
        except Exception:
            self._grab_ok = False
            self._bind_mouse_fallback()

    def _bind_mouse_fallback(self):
        """grab 没拿到时的兜底：主窗照收鼠标，事件按命中测试落到各控件上。

        `bind_all` 在每条事件 bindtags 的末尾，所以不管落点是正文、侧栏还是
        蒙层透明区，都能收到；坐标仍走 `_pos()` 换算到 Canvas 口径。
        """
        root = self.app.root
        for seq, fn in (("<ButtonPress-1>", self._on_press),
                        ("<B1-Motion>", self._on_motion),
                        ("<ButtonRelease-1>", self._on_release)):
            try:
                self._fb_ids.append((seq, root.bind_all(seq, fn, add="+")))
            except Exception:
                pass

    def _unbind_mouse_fallback(self):
        for seq, fid in self._fb_ids:
            try:
                self.app.root.unbind(seq, fid)
            except Exception:
                pass
        self._fb_ids = []

    def _xy(self, e):
        """把任意控件上的事件坐标换算成蒙层 Canvas 坐标。"""
        try:
            return (e.x_root - self.cv.winfo_rootx(),
                    e.y_root - self.cv.winfo_rooty())
        except (tk.TclError, AttributeError):
            return None

    def _pos(self, e):
        """Canvas 坐标；换算不了（已销毁 / 合成事件没带 root 坐标）就退回窗口内坐标。"""
        xy = self._xy(e)
        return (e.x, e.y) if xy is None else xy

    def _on_bar_event(self, e):
        """事件是否落在工具条（含其子控件）上 —— 兜底路径靠它避免点按钮时起笔。"""
        bar = self.bar
        w = getattr(e, "widget", None)
        if bar is None or w is None:
            return False
        try:
            return str(w) == str(bar) or str(w).startswith(str(bar) + ".")
        except Exception:
            return False

    def _on_press(self, e):
        if not self.active:
            return
        if self._on_bar_event(e):
            return          # 兜底路径下工具条自己处理，别顺带起一笔
        x, y = self._pos(e)
        # 提示片画在 Canvas 上（不透明色块），点它也不该起笔。⚠️ 别再按「蒙层
        # 底部那一条」判断：工具条现在能拖到任意位置，底部带早就空了。
        b = self._hint_box
        if b is not None and b[0] <= x <= b[2] and b[1] <= y <= b[3]:
            return "break"
        if not (0 <= x <= self._w and 0 <= y <= self._h):
            return "break"
        kind = self._tool
        self._draw = {"kind": kind, "x0": x, "y0": y}
        col = COLORS[self._color]
        w = WIDTHS[self._width]
        cv = self.cv
        if kind == "number":
            self._cur = self._make_number(x, y)
        elif kind == "pencil_line":
            self._cur = [cv.create_line(x, y, x, y, fill=col,
                                        width=w, capstyle=tk.ROUND,
                                        joinstyle=tk.ROUND, smooth=True,
                                        tags=("stroke",))]
            self._draw["pts"] = [x, y]
        else:
            x0, y0 = x, y
            if kind == "line":
                self._cur = [cv.create_line(x0, y0, x0, y0, fill=col, width=w,
                                            capstyle=tk.ROUND, tags=("stroke",))]
            elif kind == "arrow":
                self._cur = [cv.create_line(x0, y0, x0, y0, fill=col, width=w,
                                            arrow=tk.LAST,
                                            arrowshape=(dpi.px(12), dpi.px(14), dpi.px(5)),
                                            tags=("stroke",))]
            elif kind == "rect":
                self._cur = [cv.create_rectangle(x0, y0, x0, y0, outline=col,
                                                 width=w, tags=("stroke",))]
            else:   # ellipse
                self._cur = [cv.create_oval(x0, y0, x0, y0, outline=col,
                                            width=w, tags=("stroke",))]
        return "break"

    def _make_number(self, x, y):
        r = dpi.px(11)
        col = COLORS[self._color]
        # 黄 / 绿底用深墨，红 / 蓝底用白，保证数字读得出来
        fg = "#ffffff" if col in (COLORS[0], COLORS[1]) else "#1f1f24"
        a = self.cv.create_oval(x - r, y - r, x + r, y + r,
                                fill=col, outline="", tags=("stroke",))
        b = self.cv.create_text(x, y, text=str(self._seq), fill=fg,
                                font=(FONT[0], 10, "bold"), tags=("stroke",))
        self._seq += 1
        return [a, b]

    def _on_motion(self, e):
        if not self._draw or self._cur is None or not self.active:
            return
        if self._on_bar_event(e):
            return
        x, y = self._pos(e)
        cv = self.cv
        if self._draw["kind"] == "pencil_line":
            pts = self._draw["pts"]
            # 隔掉过密的点，别让 coords 无限长
            if len(pts) >= 2 and abs(x - pts[-2]) + abs(y - pts[-1]) < dpi.px(2):
                return
            pts.extend((x, y))
            cv.coords(self._cur[0], *pts)
        elif self._draw["kind"] != "number":
            cv.coords(self._cur[0], self._draw["x0"], self._draw["y0"], x, y)

    def _on_release(self, e):
        if not self._draw or not self.active:
            return
        if self._on_bar_event(e):
            return
        if self._cur is not None \
                and self._draw["kind"] not in ("pencil_line", "number"):
            x, y = self._pos(e)
            self.cv.coords(self._cur[0], self._draw["x0"], self._draw["y0"], x, y)
        if self._cur is not None:
            # 一笔 = 一组图元（序号是「圆 + 数字」两个），撤销整组弹
            self._strokes.append((self._cur, self._draw["kind"]))
        self._cur = None
        self._draw = None
        return "break"

    def _on_bar_press(self, e):
        self._press = self._hit(e.x)
        # 在任何一枚按钮上按住都能拖走整条：工具条没有「空白把手」，7px 的左右
        # 内边距太窄了。用位移阈值区分「点一下」和「拖一把」，见 `_drag_bar()`。
        self._bar_drag = {"x_root": getattr(e, "x_root", 0),
                          "y_root": getattr(e, "y_root", 0),
                          "x0": self._bar_at[0], "y0": self._bar_at[1],
                          "moved": False}
        self._draw_bar()
        return "break"        # 拦住 Toplevel 的 release，别让它收一笔

    def _drag_bar(self, e):
        """拖动工具条。过了阈值才算拖 —— 否则手抖 1px 会把点击吞掉。"""
        d = self._bar_drag
        if d is None:
            return False
        dx = getattr(e, "x_root", 0) - d["x_root"]
        dy = getattr(e, "y_root", 0) - d["y_root"]
        if not d["moved"]:
            if abs(dx) < dpi.px(3) and abs(dy) < dpi.px(3):
                return True
            d["moved"] = True
            self._press = None
            self._hover = None
            self._set_hint(None)      # 拖的时候别让说明片跟着晃
            self._draw_bar()          # 撤掉「按住」底片
            try:
                self.bar.config(cursor="fleur")   # 四向箭头：明确「正在搬整条」
            except tk.TclError:
                pass
        self._bar_pos = self._clamp_bar(d["x0"] + dx, d["y0"] + dy)
        self._place_bar()
        self._draw_hint()
        return True

    def _on_bar_release(self, e):
        drag, self._bar_drag = self._bar_drag, None
        i = self._hit(e.x)
        press, self._press = self._press, None
        moved = drag is not None and drag["moved"]
        if moved:
            try:
                self.bar.config(cursor="")
            except tk.TclError:
                pass
        self._draw_bar()
        if moved:
            return "break"    # 拖过一把就不算点按钮
        if i is not None and i == press:
            self._activate(i)
        return "break"

    def _on_bar_motion(self, e):
        if self._bar_drag is not None:
            if self._drag_bar(e):
                return "break"
        i = self._hit(e.x)
        if i != self._hover:
            self._hover = i
            self._draw_bar()
            if i is None:
                self._set_hint(None)
            else:
                it = self._items[i]
                self._set_hint("%s · %s" % (it["label"], it["tip"]))

    def _on_bar_leave(self, _e=None):
        if self._bar_drag is not None:
            return            # 拖动中指针本来就会离开工具条，别把拖动状态清掉
        self._hover = None
        self._press = None
        self._set_hint(None)
        self._draw_bar()

    def _activate(self, i):
        it = self._items[i]
        k, key = it["kind"], it["key"]
        if k == "tool":
            self._tool = key
        elif k == "color":
            self._color = key
        elif k == "width":
            self._width = key
        elif key == "undo":
            self.undo()
        elif key == "clear":
            self.clear()
        elif key == "close":
            self.close()
            return
        self._draw_bar()

    # ------------------------------------------------------------ 笔画栈
    def undo(self):
        if not self._strokes:
            return
        ids, _kind = self._strokes.pop()
        try:
            self.cv.delete(*ids)
        except tk.TclError:
            pass
        self._seq = 1 + sum(1 for _ids, k in self._strokes if k == "number")

    def clear(self):
        cv = self.cv
        for ids, _kind in self._strokes:
            try:
                cv.delete(*ids)
            except tk.TclError:
                pass
        self._strokes = []
        self._seq = 1
        try:
            cv.delete("stroke")
        except tk.TclError:
            pass
