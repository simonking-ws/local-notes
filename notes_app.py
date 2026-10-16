# -*- coding: utf-8 -*-
"""
素笺 —— 复刻 macOS 笔记（Apple Notes）的桌面应用（Python + Tkinter，零依赖，无需联网）。
运行：python notes_app.py
数据保存在同目录的 SQLite 数据库 notes_data.db（旧的 notes_data.json 首次启动会自动迁移）
"""
import os
import re
import math
import json
import sqlite3
import unicodedata
import tkinter as tk
from tkinter import ttk, filedialog

BASE = os.path.dirname(os.path.abspath(__file__))
# 旧版 JSON 数据文件（仍在：首次运行若发现它会自动迁移进 db，不会丢数据）
DATA_FILE = os.path.join(BASE, "notes_data.json")
# 现在的唯一数据源：SQLite 数据库
DB_FILE = os.path.join(BASE, "notes_data.db")

FONT = ("PingFang SC", 13) if True else None  # 中文优先，回退雅黑
# 若系统无 PingFang，则退回微软雅黑
try:
    import tkinter.font as tkfont
    _avail = [f.lower() for f in tkfont.families()]
    if "pingfang sc" not in _avail and "microsoft yahei" in _avail:
        FONT = ("Microsoft YaHei", 13)
    elif "pingfang sc" not in _avail and "microsoft yahei" not in _avail:
        FONT = ("SimHei", 13)
except Exception:
    FONT = ("Microsoft YaHei", 13)

# 仅浅色主题（已取消暗色主题）：对比更清晰、直角硬朗的 macOS 笔记风格
LIGHT = {
    "top": "#e9e9ec", "side": "#e9e9ec", "list": "#f4f4f6", "editor": "#ffffff",
    "line": "#cfcfd6", "text": "#1f1f24", "soft": "#76767e", "accent": "#2f7cf6",
    "hover": "#e0e0e6", "sel": "#cfe0fb", "selfg": "#1f6feb", "row_alt": "#efeff2",
    "green": "#16a34a", "chk_off": "#c9c9d1", "star": "#f5a623",
}

# ---------- 皮肤：套用 WorkBuddy 当前外观皮肤（方案A） ----------
# 开关：改为 False 即恢复内置浅灰配色（LIGHT），无需改动其它代码。
USE_SKIN = True
SKIN_DIR = os.path.join(os.path.expanduser("~"), ".workbuddy", "appearance-resources")

# 笔记调色板键 -> 皮肤 CSS 变量名（按顺序取第一个命中的变量）
SKIN_VAR_MAP = {
    "top":     ("--cb-bg-secondary", "--cb-content-background"),
    "side":    ("--cb-bg-secondary", "--cb-content-background"),
    "list":    ("--cb-bg-primary",),
    "editor":  ("--cb-main-area-background", "--cb-panel-bg-primary"),
    "line":    ("--cb-vscode-panel-border", "--cb-main-area-border-color",
                "--cb-input-border-color", "--cb-markdown-border-color"),
    "text":    ("--cb-text-primary",),
    "soft":    ("--cb-text-secondary",),
    "accent":  ("--cb-button-primary", "--cb-text-link"),
    "hover":   ("--cb-hover-bg", "--cb-dropdown-item-hover-bg-color", "--cb-input-hover-bg"),
    "sel":     ("--cb-dropdown-item-selected-bg-color", "--cb-list-item-selected-bg"),
    "green":   ("--cb-button-primary", "--cb-text-link"),
}

# 皮肤未提供的键，用森林绿体系兜底（CSS 变量缺失/改名时也不会崩）
SKIN_DEFAULT = {
    "selfg": "#256d37",      # 选中行文字（深绿，保证在浅绿选中底上可读）
    "row_alt": "#eef3ee",
    "chk_off": "#c6d4c8",    # 未勾选的待办框
    "star": "#f5a623",       # 星标金色（沿用）
    "sel2": "#e6efe7",       # 失焦时的选中色
}


def _hex_rgb(h):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _norm_hex(h):
    """#rgb / #rrggbb / #rrggbbaa -> #rrggbb（8 位按 alpha 混合到白色，Tk 不支持透明度）。"""
    s = str(h).strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) >= 6:
        r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
        if len(s) == 8:
            a = int(s[6:8], 16) / 255.0
            r = int(round(r * a + 255 * (1 - a)))
            g = int(round(g * a + 255 * (1 - a)))
            b = int(round(b * a + 255 * (1 - a)))
        return "#%02x%02x%02x" % (r, g, b)
    return None


def _parse_skin_css(path):
    """解析皮肤 CSS：只取亮色块的 --cb-* 变量（跳过含 dark 的选择器）。"""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            css = f.read()
    except Exception:
        return {}
    # 先剥离注释：注释里可能含 "Dark ..." 字样，会误判成暗色块导致整块变量被跳过
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out = {}
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        sel, body = m.group(1), m.group(2)
        # 跳过暗色块；但 :not(.vscode-dark) 这类是「非暗色」限定，不能跳过
        if "dark" in sel.lower():
            if "dark" in re.sub(r":not\([^)]*\)", "", sel).lower():
                continue
        for vm in re.finditer(r"(--cb-[a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{3,8})", body):
            c = _norm_hex(vm.group(2))
            if c:
                out[vm.group(1)] = c
    return out


def load_skin_palette():
    """读取 WorkBuddy 当前皮肤并映射为笔记调色板；失败返回 None（回退内置配色）。"""
    if not USE_SKIN:
        return None
    try:
        if not os.path.isdir(SKIN_DIR):
            return None
        dirs = [os.path.join(SKIN_DIR, d) for d in os.listdir(SKIN_DIR)]
        dirs = [d for d in dirs if os.path.isdir(d)]
        if not dirs:
            return None
        # 取最近修改的皮肤目录（皮肤以时间戳命名）
        latest = max(dirs, key=lambda p: os.path.getmtime(p))
        css_path = None
        for name in ("skin.v2.css", "skin.css"):
            p = os.path.join(latest, name)
            if os.path.isfile(p):
                css_path = p
                break
        if css_path is None:
            for root, _sub, files in os.walk(latest):
                for fn in files:
                    if fn.endswith(".css"):
                        css_path = os.path.join(root, fn)
                        break
                if css_path:
                    break
        if not css_path:
            return None
        variables = _parse_skin_css(css_path)
        if not variables:
            return None
        pal = dict(SKIN_DEFAULT)
        for key, names in SKIN_VAR_MAP.items():
            for n in names:
                if n in variables:
                    pal[key] = variables[n]
                    break
        # 必备键缺失则视为解析失败，回退内置配色
        missing = [k for k in ("top", "side", "list", "editor", "text", "accent")
                   if k not in pal]
        if missing:
            return None
        pal["_source"] = css_path
        return pal
    except Exception:
        return None


# 启动时决定配色：优先 WorkBuddy 皮肤，失败则用内置浅灰
PALETTE = load_skin_palette() or LIGHT

# 三角列表的项目符号（Office/Word 项目符号库风格：U+27A4 黑色右向箭头头）
TRI_MARK = "\u27a4  "
# 旧版符号（U+25B8）：存量笔记保持原样不迁移，但行首识别仍兼容，
# 保证老三角列表回车续接等交互不失效。
TRI_MARK_OLD = "\u25b8  "

# 智能文件夹（smart=True）用区别图标，普通文件夹用文件夹图标
SMART_ICON_ALL = "f_all"     # 全部笔记 → 清单三线
SMART_ICON_STAR = "f_star"   # 星标笔记 → 五角星（金色）

# 文件夹图标画布尺寸（原 16x14 偏小，圆角细节放不下）
FOLDER_ICON_W = 18
FOLDER_ICON_H = 16


def _round_rect_points(x1, y1, x2, y2, r, seg=4):
    """生成圆角矩形的多边形顶点（顺时针，从顶边右端起）。

    Tk 的 Canvas 没有圆角矩形，且不能用多个形状拼（各自描边会在内部留下多余线条），
    所以用一圈顶点交给 create_polygon 一次性填充 + 描边。
    """
    pts = []
    corners = ((x2 - r, y1 + r, -90, 0),    # 右上
               (x2 - r, y2 - r, 0, 90),     # 右下
               (x1 + r, y2 - r, 90, 180),   # 左下
               (x1 + r, y1 + r, 180, 270))  # 左上
    for cx, cy, a0, a1 in corners:
        for i in range(seg + 1):
            a = math.radians(a0 + (a1 - a0) * float(i) / seg)
            pts.extend((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def _star_points(cx, cy, r_out, r_in, n=5):
    """生成 n 角星的顶点（外/内半径交替）。"""
    pts = []
    for i in range(n * 2):
        rr = r_out if i % 2 == 0 else r_in
        a = -math.pi / 2 + i * math.pi / n
        pts.extend((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    return pts


def _mix_hex(c1, c2, t):
    """把 c2 按权重 t(0~1) 混入 c1（t=0 得 c1，t=1 得 c2）。用于推导悬停/按下色。"""
    def rgb(h):
        h = (h or "#000000").lstrip("#")
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        try:
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
        except Exception:
            return (0, 0, 0)
    r1, g1, b1 = rgb(c1)
    r2, g2, b2 = rgb(c2)
    return "#%02x%02x%02x" % (int(round(r1 + (r2 - r1) * t)),
                              int(round(g1 + (g2 - g1) * t)),
                              int(round(b1 + (b2 - b1) * t)))


# 应用中文名（窗口标题 / 示例笔记 / 导出备份文件名统一取这里）
APP_NAME = "素笺"

# 圆角按钮：hover/pressed 底色由强调色按不同权重混出，跟随任意皮肤自动推导
BTN_HOVER_MIX = 0.12
BTN_PRESS_MIX = 0.26
BTN_RADIUS = 8
TOOLBAR_H = 46          # 工具栏高度；下方还有 1px 分隔线
TOOL_LINE_MIX = 0.28    # 工具栏内分隔线：底色向次级文字色混合的比例

# 编辑区右侧居中的浮动导航（回顶部 / 回底部）
JUMP_W = 26             # 胶囊宽度（连同与滚动条的间隙，正好落在正文右侧留白里）
JUMP_H = 62             # 胶囊高度（上下各一枚双箭尖）
JUMP_GAP = 8            # 与右侧滚动条之间留出的间隙
# 正文左右留白：右侧要容下浮动导航（JUMP_W + JUMP_GAP），所以比默认略宽
TEXT_PADX = JUMP_W + JUMP_GAP + 8

# 列表圆角卡片：左右留白 + 行间留白，选中/悬停时行会浮起成一张卡片
CARD_RADIUS = 9
CARD_PADX = 8
CARD_GAP = 2

# 左侧两栏宽度：折叠条 + 文件夹列 + 分隔线 + 笔记列表列
COLLAPSE_W = 22         # 最左缘折叠细条（宽度需容纳圆角小片 + 双箭尖）
SIDEBAR_W = 216         # 文件夹列
LIST_W = 300            # 笔记列表列
# 搜索框初始宽度 = 文件夹列 + 分隔线 + 笔记列表列（展开时与下方两栏左右对齐）
SEARCH_W = SIDEBAR_W + 1 + LIST_W

# 主题化确认对话框正文的最大行宽（像素），超出则截断名称，避免多折一行
DLG_TEXT_W = 360


class RoundButton(tk.Canvas):
    """自绘圆角按钮（补齐 Tk Button 只有直角、且按下会闪系统灰的问题）。

    Tk 的 Frame/Label/Button 天生是矩形，无法圆角；本类用 Canvas 画圆角背景 +
    create_text 画文字，自己接管 normal / hover / pressed 三态配色，
    因此按下时不会被系统默认灰覆盖。
    """

    def __init__(self, master, pal, text, command=None, font=None,
                 padx=11, pady=6, radius=BTN_RADIUS, primary=False,
                 base=None, fg=None, cursor="hand2", stretch=False, outline=None):
        tk.Canvas.__init__(self, master, bd=0, highlightthickness=0, cursor=cursor)
        self._pal = dict(pal)
        self._text = text
        self._command = command
        self._font = font or (FONT[0], 12)
        self._padx = padx
        self._pady = pady
        self._radius = radius
        self._primary = primary
        self._base = base if base is not None else self._pal["editor"]
        self._fg = fg
        self._outline = outline
        self._state = "normal"
        self._stretch = stretch
        self._drawn_w = 0
        self.build()
        if stretch:
            # pack(fill=X) 会把画布拉宽，需按实际宽度重画，否则圆角色块短于按钮
            self.bind("<Configure>", self._on_cfg)

    def _on_cfg(self, e=None):
        aw = self.winfo_width()
        if aw > 4 and abs(aw - self._drawn_w) > 1:
            self.redraw()

    # ---------- 对外只读属性（替代 cget("text") 的用法） ----------
    @property
    def text(self):
        return self._text

    @property
    def command(self):
        return self._command

    def build(self):
        """按当前文字/字体计算尺寸并重画。"""
        try:
            import tkinter.font as _f
        except Exception:
            _f = tkfont
        fam, size = self._font[0], self._font[1]
        # tkfont.Font 的样式只能按关键字传（位置参数第一位是 root，不是 family）
        kw = {}
        for s in tuple(self._font[2:]):
            s = str(s).lower()
            if s == "bold":
                kw["weight"] = "bold"
            elif s == "italic":
                kw["slant"] = "italic"
            elif s == "underline":
                kw["underline"] = 1
            elif s == "overstrike":
                kw["overstrike"] = 1
        f = _f.Font(family=fam, size=size, **kw)
        self._tkfont = f
        w = int(f.measure(self._text)) + self._padx * 2
        h = int(f.metrics("linespace")) + self._pady * 2
        if self._stretch:
            aw = self.winfo_width()
            if aw > 4:
                w = aw
        if self._stretch:
            self.configure(height=h)
        else:
            self.configure(width=w, height=h)
        self._drawn_w = w
        self.delete("all")
        self.create_polygon(_round_rect_points(1, 1, w - 1, h - 1, self._radius, 6),
                            fill=self._cur_fill(), outline=self._cur_outline(), width=1)
        self.create_text(w / 2.0, h / 2.0, text=self._text, font=f,
                         fill=self._cur_fg())

    # ---------- 三态配色 ----------
    def _cur_fill(self):
        """常态/悬停/按下三档底色：由基础色向目标色按权重混合，随皮肤自动变化。"""
        p = self._pal
        if self._state == "normal":
            return p["accent"] if self._primary else self._base
        if self._primary:
            # 主按钮：向深绿递进
            t = 0.20 if self._state == "hover" else BTN_PRESS_MIX + 0.14
            return _mix_hex(p["accent"], p["selfg"], t)
        t = BTN_HOVER_MIX if self._state == "hover" else BTN_PRESS_MIX
        return _mix_hex(self._base, p["accent"], t)

    def _cur_outline(self):
        """描边色：常态用传入的描边色，悬停/按下向强调色靠拢，让「可点」感更明确。"""
        if not self._outline:
            return ""
        if self._primary or self._state == "normal":
            return self._outline
        t = 0.45 if self._state == "hover" else 0.7
        return _mix_hex(self._outline, self._pal["accent"], t)

    def _cur_fg(self):
        p = self._pal
        if self._primary:
            return "#ffffff"
        if self._fg:
            return self._fg
        if self._state == "normal":
            return p["soft"]
        return p["accent"] if self._state == "hover" else p["selfg"]

    def redraw(self):
        self.build()

    def apply_theme(self, pal):
        self._pal = dict(pal)
        self.redraw()

    # ---------- 交互 ----------
    def bind_events(self):
        self.bind("<Enter>", lambda e: self._set_state("hover"))
        self.bind("<Leave>", lambda e: self._set_state("normal"))
        self.bind("<ButtonPress-1>", lambda e: self._set_state("press"))
        self.bind("<ButtonRelease-1>", self._on_release)

    def _set_state(self, st):
        if self._state == st:
            return
        self._state = st
        self.redraw()

    def _on_release(self, e):
        self._state = "hover"
        self.redraw()
        if self._command:
            self._command()

    def set_text(self, text):
        self._text = text
        self.redraw()


class FontSizeSlider(tk.Canvas):
    """字号滑杆：强调色填充轨道 + 圆形滑钮，拖动/点击/滚轮/方向键调整。

    ttk.Scale 是系统方眼样式，和自绘圆角语言不搭，这里整条自己画：
    常态滑钮白底强调色描边，悬停略放大，拖动时实心并浮出数值气泡。
    取值 1pt 连续步进，不再有预设字号。
    """

    HEIGHT = 70
    PAD = 12              # 轨道两端留白，正好容纳滑钮半径
    TRACK_Y = 38

    def __init__(self, master, pal, variable, minimum=8, maximum=25,
                 command=None, command_end=None, width=340):
        tk.Canvas.__init__(self, master, width=width, height=self.HEIGHT,
                           bd=0, highlightthickness=0, bg=pal["editor"],
                           cursor="hand2", takefocus=1)
        self._pal = dict(pal)
        self._var = variable
        self._min, self._max = int(minimum), int(maximum)
        self._command = command
        self._command_end = command_end
        self._state = "normal"      # normal / hover / drag
        self._bubble = False
        # 字体建一次（实例级，绑定当前 Tk 解释器）
        import tkinter.font as _tf
        self._font_val = _tf.Font(family=FONT[0], size=11, weight="bold")
        self._font_small = _tf.Font(family=FONT[0], size=9)
        self._font_bub = _tf.Font(family=FONT[0], size=10, weight="bold")
        self._var.trace_add("write", lambda *a: self.build())
        self.bind("<Configure>", lambda e: self.build())
        self.bind("<Enter>", lambda e: self._set_state("hover"))
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<MouseWheel>", self._on_wheel)
        self.bind("<Button-4>", lambda e: self._nudge(1))     # Linux 上滚
        self.bind("<Button-5>", lambda e: self._nudge(-1))    # Linux 下滚
        self.bind("<Left>", lambda e: self._nudge(-1))
        self.bind("<Right>", lambda e: self._nudge(1))
        self.bind("<Home>", lambda e: self._set_value(self._min, end=True))
        self.bind("<End>", lambda e: self._set_value(self._max, end=True))
        self.bind("<FocusIn>", lambda e: self.build())
        self.bind("<FocusOut>", lambda e: self.build())
        self.build()

    # ---------- 取值 / 坐标换算 ----------
    def value(self):
        try:
            v = int(float(self._var.get()))
        except Exception:
            v = self._min
        return min(self._max, max(self._min, v))

    def _width(self):
        w = self.winfo_width()
        if w <= 1:
            w = int(float(self.cget("width")))
        return max(w, self.PAD * 2 + 10)

    def _x_for(self, value):
        w = self._width()
        t = (value - self._min) / float(max(1, self._max - self._min))
        return self.PAD + (w - self.PAD * 2) * t

    def _value_for(self, x):
        w = self._width()
        t = (x - self.PAD) / float(max(1, w - self.PAD * 2))
        t = min(1.0, max(0.0, t))
        return int(round(self._min + t * (self._max - self._min)))

    # ---------- 交互 ----------
    def _set_state(self, st):
        if self._state == st:
            return
        self._state = st
        self.build()

    def _on_leave(self, e):
        # 拖动中指针滑出控件也继续跟随，松手前不退出拖动态
        if self._state != "drag":
            self._state = "normal"
            self.build()

    def _on_press(self, e):
        self.focus_set()
        self._state = "drag"
        self._bubble = True
        self._set_value(self._value_for(e.x))

    def _on_drag(self, e):
        if self._state != "drag":
            self._state = "drag"
            self._bubble = True
        self._set_value(self._value_for(e.x))

    def _on_release(self, e):
        if self._state != "drag":
            return
        self._state = "hover"
        self._bubble = False
        self.build()
        if self._command_end:
            self._command_end(self.value())

    def _on_wheel(self, e):
        self._set_value(self.value() + (1 if e.delta > 0 else -1), end=True)

    def _nudge(self, delta):
        self._set_value(self.value() + delta, end=True)

    def _set_value(self, value, end=False):
        v = min(self._max, max(self._min, int(value)))
        if v != self.value():
            self._var.set(v)
        self.build()
        if self._command:
            self._command(v)
        if end and self._command_end:
            self._command_end(v)

    # ---------- 绘制 ----------
    def build(self):
        self.delete("all")
        p = self._pal
        w = self._width()
        v = self.value()
        x0, x1, cy = self.PAD, w - self.PAD, self.TRACK_Y
        tx = self._x_for(v)

        self.create_polygon(_round_rect_points(x0, cy - 3, x1, cy + 3, 3, 6),
                            fill=p["line"], outline="")
        if tx > x0 + 1:
            self.create_polygon(_round_rect_points(x0, cy - 3, tx, cy + 3, 3, 6),
                                fill=p["accent"], outline="")

        self.create_text(x1, 10, anchor="ne", text="%d pt" % v,
                         font=self._font_val, fill=p["accent"])
        self.create_text(x0, self.HEIGHT - 10, anchor="w", text=str(self._min),
                         font=self._font_small, fill=p["soft"])
        self.create_text(x1, self.HEIGHT - 10, anchor="e", text=str(self._max),
                         font=self._font_small, fill=p["soft"])

        if self._bubble:
            text = "%d pt" % v
            bw = self._font_bub.measure(text) + 16
            bh = 20
            bx = min(max(tx, self.PAD + bw / 2.0), w - self.PAD - bw / 2.0)
            by = cy - 12
            self.create_polygon(
                _round_rect_points(bx - bw / 2.0, by - bh, bx + bw / 2.0, by,
                                   bh / 2.0, 6),
                fill=p["accent"], outline="")
            self.create_text(bx, by - bh / 2.0, text=text,
                             font=self._font_bub, fill="#ffffff")

        r = 9 if self._state == "normal" else 10
        if self._state == "drag":
            fill, outline = p["accent"], _mix_hex(p["accent"], p["selfg"], 0.2)
        else:
            fill, outline = p["editor"], p["accent"]
        if self.focus_get() is self and self._state != "drag":
            self.create_oval(tx - r - 3, cy - r - 3, tx + r + 3, cy + r + 3,
                             outline=_mix_hex(p["editor"], p["accent"], 0.25),
                             width=1)
        self.create_oval(tx - r, cy - r, tx + r, cy + r,
                         fill=fill, outline=outline, width=2)
        if self._state == "drag":
            self.create_oval(tx - 3, cy - 3, tx + 3, cy + 3,
                             fill="#ffffff", outline="")


def _scroll_chip_geom(size):
    """按小片尺寸推导双箭尖参数，大小片（工具栏 24 / 折叠条 16）视觉一致。"""
    k = size / 24.0
    return 3.0 * k, 4.6 * k, 3.2 * k, (2.0 if size >= 20 else 1.8)


def _draw_scroll_chip(cv, pal, base, cx, cy, direction, state, size=24):
    """圆角小片 + 双箭尖：工具栏滑动箭头、列表折叠按钮共用同一套画法与三态配色。

    常态是比底色略深的浅灰片，悬停加深并描边，按下转强调色；
    direction 约定与自绘控件一致：-1 向左、1 向右。
    """
    if state == "press":
        fill = _mix_hex(pal["hover"], pal["accent"], 0.22)
        outline = _mix_hex(pal["line"], pal["accent"], 0.55)
        fg = pal["accent"]
    elif state == "hover":
        fill = pal["hover"]
        outline = _mix_hex(base, pal["line"], 0.85)
        fg = pal["selfg"]
    else:
        fill = _mix_hex(base, pal["line"], 0.32)
        outline = ""
        fg = pal["soft"]
    radius = int(round(min(BTN_RADIUS, size * 0.34)))
    cv.create_polygon(
        _round_rect_points(cx - size / 2.0, cy - size / 2.0,
                           cx + size / 2.0, cy + size / 2.0, radius, 6),
        fill=fill, outline=outline, width=1)
    a, b, gap, lw = _scroll_chip_geom(size)
    for off in (-gap, gap):
        ax = cx + off
        cv.create_line(ax - a * direction, cy - b, ax + a * direction, cy,
                       ax - a * direction, cy + b, fill=fg, width=lw,
                       capstyle=tk.ROUND, joinstyle=tk.ROUND)


class ArrowButton(tk.Canvas):
    """工具栏两端的滑动箭头：圆角小片 + 双箭尖，垂直居中，自带三态。

    和 RoundButton 一样用 Canvas 自绘（Tk 的 Button 是直角，且按下会闪系统灰）。
    画布被 pack(fill=Y) 拉到整条工具栏高，所以按实际尺寸居中绘制，
    窗口变小、箭头出现时也能稳稳落在垂直中线上。
    """

    def __init__(self, master, pal, direction, command=None, size=26, base=None):
        tk.Canvas.__init__(self, master, width=size, height=size, bd=0,
                           highlightthickness=0, cursor="hand2")
        self._pal = dict(pal)
        self._dir = -1 if direction == "left" else 1
        self._command = command
        self._size = size
        self._state = "normal"
        # 常态底色 = 工具栏底色（默认取编辑区色），常态才能「隐身」不露白块
        self._base = base if base is not None else pal.get("editor", "#ffffff")
        # 实际尺寸随布局变化（fill=Y 会拉高），尺寸一变就重画，保持垂直居中
        self.bind("<Configure>", lambda e: self.build())
        self.build()

    def build(self):
        self.delete("all")
        w = max(self.winfo_width(), self._size)
        h = max(self.winfo_height(), self._size)
        _draw_scroll_chip(self, self._pal, self._base, w / 2.0, h / 2.0,
                          self._dir, self._state, size=24)

    def redraw(self):
        self.build()

    def apply_theme(self, pal, base=None):
        self._pal = dict(pal)
        if base is not None:
            self._base = base
        self.redraw()

    def bind_events(self):
        self.bind("<Enter>", lambda e: self._set_state("hover"))
        self.bind("<Leave>", lambda e: self._set_state("normal"))
        self.bind("<ButtonPress-1>", lambda e: self._set_state("press"))
        self.bind("<ButtonRelease-1>", self._on_release)

    def _set_state(self, st):
        if self._state == st:
            return
        self._state = st
        self.redraw()

    def _on_release(self, e):
        self._state = "hover"
        self.redraw()
        if self._command:
            self._command()


class CollapseButton(tk.Canvas):
    """列表折叠/展开按钮：窄条上的圆角小片 + 双箭尖（向左收起 / 向右展开）。

    与工具栏滑动箭头同一套画法和三态配色（共用 _draw_scroll_chip），
    只是尺寸收到折叠条宽度，放在窗口最左缘的竖条中央，不抢眼。
    """

    def __init__(self, master, pal, command=None, base=None):
        tk.Canvas.__init__(self, master, width=COLLAPSE_W, height=44, bd=0,
                           highlightthickness=0, cursor="hand2")
        self._pal = dict(pal)
        self._command = command
        self._dir = -1            # -1 = 向左箭头（收起），1 = 向右箭头（展开）
        self._state = "normal"
        self._base = base if base is not None else pal.get("side", "#ececec")
        self.bind_events()
        self.build()

    def set_dir(self, d):
        """切换箭头方向：收起后显示向右（1），展开时显示向左（-1）。"""
        if self._dir != d:
            self._dir = d
            self.build()

    def build(self):
        self.delete("all")
        _draw_scroll_chip(self, self._pal, self._base,
                          COLLAPSE_W / 2.0, 22, self._dir, self._state,
                          size=COLLAPSE_W - 2)

    def redraw(self):
        self.build()

    def apply_theme(self, pal, base=None):
        self._pal = dict(pal)
        if base is not None:
            self._base = base
        self.redraw()

    def bind_events(self):
        self.bind("<Enter>", lambda e: self._set_state("hover"))
        self.bind("<Leave>", lambda e: self._set_state("normal"))
        self.bind("<ButtonPress-1>", lambda e: self._set_state("press"))
        self.bind("<ButtonRelease-1>", self._on_release)

    def _set_state(self, st):
        if self._state == st:
            return
        self._state = st
        self.redraw()

    def _on_release(self, e):
        self._state = "hover"
        self.redraw()
        if self._command:
            self._command()


class ScrollJumpNav(tk.Canvas):
    """编辑区右侧居中的浮动小导航：上下两枚双箭尖，点一下回顶部 / 回底部。

    只在这篇正文确实溢出（出现滚动条）时出现，已经到顶/到底的那一头会变淡、点了不动。
    视觉与工具栏滑动箭头、列表折叠条同族：圆角片 + 双箭尖 + 悬停/按下三态，
    底色取编辑区色再略微加深，浮在正文上不抢眼。
    """

    def __init__(self, master, pal, command=None, base=None):
        tk.Canvas.__init__(self, master, width=JUMP_W, height=JUMP_H, bd=0,
                           highlightthickness=0, cursor="hand2",
                           bg=(base if base is not None else pal.get("editor", "#ffffff")))
        self._pal = dict(pal)
        self._base = base if base is not None else pal.get("editor", "#ffffff")
        self._command = command
        self._state = "normal"     # normal / press
        self._part = None          # 当前指向哪一半：None / "top" / "bottom"
        self._top_on = True        # 该端是否还能滚（已到头就变淡）
        self._bottom_on = True
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.build()

    # ---------- 绘制 ----------
    def build(self):
        self.delete("all")
        self.create_polygon(
            _round_rect_points(0.5, 0.5, JUMP_W - 0.5, JUMP_H - 0.5, JUMP_W / 2.0, 8),
            fill=_mix_hex(self._base, self._pal["line"], 0.30),
            outline=_mix_hex(self._base, self._pal["line"], 0.55), width=1)
        # 两枚图标之间的细分隔线
        self.create_line(7, JUMP_H / 2.0, JUMP_W - 7, JUMP_H / 2.0,
                         fill=_mix_hex(self._base, self._pal["line"], 0.5), width=1)
        self._draw_half("top", JUMP_H * 0.25)
        self._draw_half("bottom", JUMP_H * 0.75)

    def _draw_half(self, part, cy):
        """画一半：悬停/按下时在图标后面铺一块圆角片，禁用时图标变淡。"""
        p = self._pal
        on = self._top_on if part == "top" else self._bottom_on
        focused = (self._part == part) and on
        if focused and self._state == "press":
            fill = _mix_hex(p["hover"], p["accent"], 0.22)
            fg = p["accent"]
            outline = _mix_hex(p["line"], p["accent"], 0.55)
        elif focused:
            fill = p["hover"]
            fg = p["selfg"]
            outline = _mix_hex(self._base, p["line"], 0.75)
        else:
            fill = outline = ""
            fg = p["soft"] if on else _mix_hex(self._base, p["soft"], 0.34)
        if fill:
            cx = JUMP_W / 2.0
            self.create_polygon(
                _round_rect_points(cx - 11, cy - 11, cx + 11, cy + 11, 8, 6),
                fill=fill, outline=outline, width=1)
        up = (part == "top")
        # 上下箭尖自成一档：比左右滑动箭头更宽、更平，小尺寸下也看得清朝向
        a, b, gap, lw = 4.6, 3.1, 3.2, 2.0
        for off in (-gap, gap):
            apex = cy + off - (b / 2.0 if up else -b / 2.0)
            wing = b if up else -b
            self.create_line(JUMP_W / 2.0 - a, apex + wing, JUMP_W / 2.0, apex,
                             JUMP_W / 2.0 + a, apex + wing, fill=fg, width=lw,
                             capstyle=tk.ROUND, joinstyle=tk.ROUND)

    def redraw(self):
        self.build()

    def apply_theme(self, pal, base=None):
        self._pal = dict(pal)
        if base is not None:
            self._base = base
        self.configure(bg=self._base)
        self.redraw()

    # ---------- 状态 ----------
    def set_ends(self, top_on, bottom_on):
        """同步「还能往上/往下滚」——顶/底那端变淡，避免点了没反应。"""
        if (top_on, bottom_on) == (self._top_on, self._bottom_on):
            return
        self._top_on, self._bottom_on = top_on, bottom_on
        self.redraw()

    def _hit(self, ev):
        return "top" if ev.y < JUMP_H / 2.0 else "bottom"

    def _on_motion(self, ev):
        part = self._hit(ev)
        if part != self._part:
            self._part = part
            self.redraw()

    def _on_leave(self, ev):
        if self._part is not None or self._state != "normal":
            self._part = None
            self._state = "normal"
            self.redraw()

    def _on_press(self, ev):
        self._part = self._hit(ev)
        self._state = "press"
        self.redraw()

    def _on_release(self, ev):
        part = self._hit(ev)
        self._state = "normal"
        self._part = part
        self.redraw()
        on = self._top_on if part == "top" else self._bottom_on
        if on and self._command:
            self._command(part)


class MarqueeLabel(tk.Canvas):
    """文件夹名用的「可按像素滚动」的文字控件（替代 tk.Label）。

    为什么不用 Label：Label 只能整串换文本，跑马灯只能按**字符**一格一格跳
    （中文一个字符十几像素、空格只有几像素），跳变间隔又长 → 看起来一顿一顿。
    这里用 Canvas 的 create_text 按**像素偏移**绘制，可以每帧挪 1~2px，
    配合较短的定时器就是连续平滑的滚动。
    参考接口刻意与 Label 保持相似（set_text / set_style），便于替换。
    """

    def __init__(self, master, bg, fg, font, gap=48):
        tk.Canvas.__init__(self, master, bd=0, highlightthickness=0, bg=bg)
        self._fg = fg
        self._font = font
        self._text = ""
        self._offset = 0.0
        self._gap = gap
        self._tw = 0
        self._items = []      # 复用的文字图元（避免每帧 delete all → 消闪、更清晰）
        self._dirty = True    # 文本/字体/颜色是否变化（变化才重设图元属性）
        self._build_font()
        self.bind("<Configure>", lambda e: self.redraw())

    # ---------- 字体测量（本控件私有，随字体变化重建） ----------
    def _build_font(self):
        import tkinter.font as _tf
        fam, size = self._font[0], self._font[1]
        kw = {}
        for s in tuple(self._font[2:]):
            s = str(s).lower()
            if s == "bold":
                kw["weight"] = "bold"
            elif s == "italic":
                kw["slant"] = "italic"
        self._tkf = _tf.Font(family=fam, size=size, **kw)
        self.configure(height=self._tkf.metrics("linespace"))
        self._tw = self._tkf.measure(self._text) if self._text else 0
        self._dirty = True

    def measure(self, s):
        return self._tkf.measure(s)

    # ---------- 对外接口 ----------
    def set_text(self, s, offset=0.0):
        self._text = s or ""
        self._offset = float(offset)
        self._tw = self._tkf.measure(self._text)
        self._dirty = True
        self.redraw()

    def set_offset(self, px):
        loop = self.loop_len()
        self._offset = (float(px) % loop) if loop > 0 else 0.0
        self.redraw()

    def get_offset(self):
        return self._offset

    def set_style(self, fg=None, font=None, bg=None):
        if fg is not None:
            self._fg = fg
        if font is not None:
            self._font = font
            self._build_font()
            self._tw = self._tkf.measure(self._text)
        if bg is not None:
            try:
                self.configure(bg=bg)
            except Exception:
                pass
        self._dirty = True
        self.redraw()

    def loop_len(self):
        """一个循环的总长度（文字宽 + 尾部留白），偏移在这个范围内回绕。"""
        return self._tw + self._gap if self._text else 0

    def redraw(self):
        """复用图元、只更新坐标：无整块清屏 → 不闪烁；坐标取整 → 文字不发虚。"""
        w, h = self.winfo_width(), self.winfo_height()
        if not self._text or w < 2 or h < 2:
            for it in self._items:
                self.itemconfigure(it, text="")
            return
        # 偏移 > 0 时需要两份文字无缝衔接，否则一份即可
        n = 2 if self._offset > 0 else 1
        while len(self._items) > n:
            self.delete(self._items.pop())
        created = False
        while len(self._items) < n:
            self._items.append(self.create_text(0, 0, text="", anchor="w"))
            created = True
        x = -int(round(self._offset))
        y = int(round(h / 2.0))
        loop = self.loop_len()
        need_style = self._dirty or created
        for idx, it in enumerate(self._items):
            if need_style:
                self.itemconfigure(it, text=self._text, font=self._font, fill=self._fg)
            self.coords(it, x + (loop if idx else 0), y)
        self._dirty = False


# 自绘右键菜单的尺寸与配色
MENU_ROW_H = 30         # 单项高度
MENU_SEP_H = 9          # 分隔线占高
MENU_PAD_X = 14         # 卡片内左右留白
MENU_GUTTER = 22        # 文字左缩进（留出图标/勾选位，视觉不贴边）
MENU_DANGER = "#e5484d"  # 危险项（删除）红色

# ---------- 代码块 ----------
# 支持插入的语言（显示名 -> 高亮规则名；空串 = 纯文本，不做高亮）
CODE_LANGS = [
    ("纯文本", ""),
    ("JSON", "json"),
    ("XML", "xml"),
    ("HTML", "html"),
    ("Java", "java"),
    ("Python", "python"),
    ("Bash", "bash"),
    ("SQL", "sql"),
]
CODE_LANG_LABEL = dict((k, l) for l, k in CODE_LANGS)

# 代码块用的等宽字体候选（按顺序取第一个系统里装了的）
CODE_FONTS = ["Consolas", "Cascadia Mono", "JetBrains Mono", "Courier New", "monospace"]

# 高亮分词颜色：浅底一套、深底一套（跟随皮肤明暗自动选，见 CodeBlock._scheme）
CODE_TOKEN_COLORS = {
    "light": {"kw": "#0b6bcb", "tag": "#0b6bcb", "str": "#0a7d3f", "num": "#b3541e",
              "lit": "#b3541e", "var": "#b3541e", "key": "#8250df", "attr": "#8250df",
              "dec": "#8250df", "ann": "#8250df", "com": "#8a909c"},
    "dark": {"kw": "#79b8ff", "tag": "#79b8ff", "str": "#8fd98f", "num": "#ffb86b",
             "lit": "#ffb86b", "var": "#ffb86b", "key": "#c9a2ff", "attr": "#c9a2ff",
             "dec": "#c9a2ff", "ann": "#c9a2ff", "com": "#8a909c"},
}

# 各语言的分词规则：[(标签, 正则, flags), ...]，列表顺序 = 标签栈顺序（越靠后越优先），
# 所以「注释 / 字符串 / 键名」总能盖住「关键字 / 数字」。纯文本没有规则。
_STR_D = r'"(?:[^"\\\n]|\\.)*"'          # 双引号字符串（不跨行）
_STR_S = r"'(?:[^'\\\n]|\\.)*'"          # 单引号字符串
_NUM = r"\b\d+(?:\.\d+)?\b"
_SQL_STR = r"'(?:[^']|'')*'"             # SQL 字符串：'' 表示一个引号
CODE_RULES = {
    "": [],
    "json": [
        ("num", _NUM, 0),
        ("lit", r"\b(?:true|false|null)\b", 0),
        ("str", _STR_D, 0),
        ("key", r'"[^"\n]*"(?=\s*:)', 0),
    ],
    "python": [
        ("num", _NUM, 0),
        ("kw", r"\b(?:def|class|return|if|elif|else|for|while|in|not|and|or|is|None|True"
               r"|False|import|from|as|with|try|except|finally|raise|lambda|yield|global"
               r"|nonlocal|pass|break|continue|assert|del|async|await|self)\b", 0),
        ("str", r'"""(?:.|\n)*?"""', 0),
        ("str", _STR_D, 0),
        ("str", _STR_S, 0),
        ("dec", r"(?m)^\s*@[\w.]+", 0),
        ("com", r"(?m)#[^\n]*", 0),
    ],
    "java": [
        ("num", _NUM, 0),
        ("kw", r"\b(?:public|private|protected|class|interface|enum|extends|implements"
               r"|static|final|abstract|void|new|return|if|else|for|while|do|switch|case"
               r"|default|break|continue|try|catch|finally|throw|throws|import|package"
               r"|this|super|null|true|false|int|long|short|double|float|boolean|char"
               r"|byte|String|var|record|instanceof|synchronized)\b", 0),
        ("str", _STR_D, 0),
        ("str", _STR_S, 0),
        ("ann", r"@\w+", 0),
        ("com", r"(?m)//[^\n]*", 0),
        ("com", r"/\*(?:.|\n)*?\*/", 0),
    ],
    "bash": [
        ("kw", r"\b(?:if|then|elif|else|fi|for|while|until|do|done|case|esac|function"
               r"|in|return|export|local|readonly|source|exit|set|unset|trap|echo|printf"
               r"|read|cd|pwd|ls|mv|cp|rm|mkdir|touch|cat|grep|sed|awk|find|xargs|curl"
               r"|wget|git|sudo|apt|yum|brew|chmod|chown|kill|ps|tar|zip|unzip)\b", 0),
        ("num", _NUM, 0),
        ("var", r"\$\{[A-Za-z_][A-Za-z0-9_]*\}|\$[A-Za-z_][A-Za-z0-9_]*|\$[0-9@#?*!$-]", 0),
        ("str", _STR_D, 0),
        ("str", _STR_S, 0),
        ("com", r"(?m)#[^\n]*", 0),
    ],
    "sql": [
        ("num", _NUM, 0),
        ("kw", r"\b(?:select|from|where|insert|into|values|update|set|delete|create|table"
               r"|alter|drop|index|view|join|left|right|inner|outer|full|cross|on|group|by"
               r"|order|having|limit|offset|distinct|as|and|or|not|null|is|in|like|between"
               r"|union|all|exists|case|when|then|else|end|primary|key|foreign|references"
               r"|default|count|sum|avg|min|max|asc|desc|with|begin|commit|rollback|if)\b",
         re.I),
        ("str", _SQL_STR, 0),
        ("com", r"(?m)--[^\n]*", 0),
        ("com", r"/\*(?:.|\n)*?\*/", 0),
    ],
    "html": [
        ("tag", r"</?[A-Za-z][\w:.-]*", 0),
        ("tag", r"/?>", 0),
        ("attr", r"[\w:.-]+(?==)", 0),
        ("str", _STR_D, 0),
        ("str", _STR_S, 0),
        ("com", r"(?s)<!--.*?-->", 0),
    ],
    "xml": [
        ("tag", r"</?[A-Za-z_][\w:.-]*", 0),
        ("tag", r"/?>", 0),
        ("attr", r"[\w:.-]+(?==)", 0),
        ("str", _STR_D, 0),
        ("str", _STR_S, 0),
        ("com", r"(?s)<!--.*?-->", 0),
    ],
}


# ---------- 代码格式化 ----------
# 纯标准库实现。格式化只重排「行首缩进」与结构化换行，绝不改写行内的标识符、
# 运算符或字符串——拿不准时宁可少做，也不要把用户的代码改坏；实在无法安全处理
# 就抛 _FormatError，由界面弹提示（原内容保持不动）。

class _FormatError(Exception):
    """格式化失败（如语法错误、暂不支持的语言），消息直接面向用户。"""


# 各语言的注释 / 字符串标记：用来把字符串与注释「掩码」成空格，
# 免得统计括号与冒号时被注释或字符串里的符号带偏。
_FMT_SYNTAX = {
    "python": {"line": ["#"], "block": [], "quotes": ["'", '"'], "triple": True},
    "java":   {"line": ["//"], "block": [("/*", "*/")], "quotes": ["'", '"'], "triple": False},
    "bash":   {"line": ["#"], "block": [], "quotes": ["'", '"'], "triple": False},
    "sql":    {"line": ["--"], "block": [("/*", "*/")], "quotes": ["'"], "triple": False},
    "json":   {"line": [], "block": [], "quotes": ['"'], "triple": False},
    "html":   {"line": [], "block": [("<!--", "-->")], "quotes": ['"', "'"], "triple": False},
    "xml":    {"line": [], "block": [("<!--", "-->")], "quotes": ['"', "'"], "triple": False},
    "":       {"line": [], "block": [], "quotes": [], "triple": False},
}

# 关键字成块的语言：行尾命中「开」就多缩进一级，行首命中「合」就先退一级
_FMT_KEYWORD_BLOCKS = {
    "bash": (r"(?:\bthen|\bdo|\belse|\{)\s*$", r"^(?:fi|done|esac|\})"),
}

_FMT_VOID_TAGS = set(
    "area base br col embed hr img input link meta param source track wbr".split())

# 标签「一行展示」的显示宽度上限（列数）：纯文本内容的标签整块拼接后不超过它就
# 保持一行，超过（或内容本身含换行）才拆成多行。中文按 2 列宽计算（见 _display_width）。
_FMT_MARKUP_MAX = 88


def _display_width(s):
    """按终端习惯估算显示宽度：全角 / 东亚宽字符算 2 列，其余算 1 列。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
               for ch in s)


def _mask_strings(text, lang):
    """把字符串与注释替换成空格（保留括号、冒号与换行），返回等长结构的文本。"""
    syn = _FMT_SYNTAX.get(lang, _FMT_SYNTAX[""])
    line_marks, blocks, quotes, triple = \
        syn["line"], syn["block"], syn["quotes"], syn["triple"]
    out = []
    i, n = 0, len(text)
    block_end = None
    str_end = None
    while i < n:
        c = text[i]
        if block_end is not None:                 # 处于块注释中
            if text.startswith(block_end, i):
                out.append(" " * len(block_end))
                i += len(block_end)
                block_end = None
            else:
                out.append("\n" if c == "\n" else " ")
                i += 1
            continue
        if str_end is not None:                   # 处于字符串中
            if len(str_end) == 1 and c == "\\":   # 转义：连同下一个字符一起掩掉
                out.append(" ")
                out.append(" " if i + 1 < n and text[i + 1] != "\n" else "\n")
                i += 2
                continue
            if text.startswith(str_end, i):
                out.append(" " * len(str_end))
                i += len(str_end)
                str_end = None
            else:
                out.append("\n" if c == "\n" else " ")
                i += 1
            continue
        handled = False
        for lm in line_marks:                     # 行注释：吃到行尾
            if text.startswith(lm, i):
                j = text.find("\n", i)
                if j < 0:
                    j = n
                out.append(" " * (j - i))
                i = j
                handled = True
                break
        if handled:
            continue
        for bs, be in blocks:                     # 块注释起始
            if text.startswith(bs, i):
                out.append(" " * len(bs))
                i += len(bs)
                block_end = be
                handled = True
                break
        if handled:
            continue
        if triple and (text.startswith('"""', i) or text.startswith("'''", i)):
            out.append("   ")
            str_end = text[i:i + 3]
            i += 3
            continue
        if c in quotes:                           # 普通单行字符串
            out.append(" ")
            i += 1
            str_end = c
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _fmt_reindent(text, lang):
    """按括号层级（以及关键字成块语言）重排行首缩进，逐行保留原内容。"""
    lines = text.split("\n")
    masked = _mask_strings(text, lang).split("\n")
    if len(masked) != len(lines):                 # 掩码不该改变行数，保险起见对齐
        masked = (masked + [""] * len(lines))[:len(lines)]
    open_pat, ded_pat = _FMT_KEYWORD_BLOCKS.get(lang, (None, None))
    kb_open = re.compile(open_pat) if open_pat else None
    kb_ded = re.compile(ded_pat) if ded_pat else None
    out = []
    depth = 0
    for raw, m in zip(lines, masked):
        body = raw.rstrip()
        stripped = body.lstrip()
        ms = m.strip()
        if not stripped:
            out.append("")
            continue
        opens = len(re.findall(r"[(\[{]", ms))
        closes = len(re.findall(r"[)\]}]", ms))
        lead_close = 1 if ms[:1] in ")]}" else 0
        ded_kw = 1 if (kb_ded and kb_ded.match(ms)) else 0
        indent = max(0, depth - max(lead_close, ded_kw))
        out.append("    " * indent + stripped)
        depth += opens - closes
        if ded_kw:
            depth -= 1
        if kb_open and kb_open.search(ms):
            depth += 1
        if depth < 0:
            depth = 0
    return "\n".join(out)


def _fmt_python(code):
    """规范化 Python 缩进（Tab → 4 空格、层级对齐），保留原有的块结构。

    Python 的块边界只体现在缩进里，逐行「重算」会误判（如与 if 同级、却不再
    带关键字收尾的语句），所以这里以原文缩进为准，只把每级缩进统一成 4 空格。
    """
    stack = [0]
    out = []
    for raw in code.split("\n"):
        if not raw.strip():
            out.append("")
            continue
        body = raw.rstrip()
        lead = body[:len(body) - len(body.lstrip())]
        width = len(lead.expandtabs(4))
        stripped = body.lstrip()
        while len(stack) > 1 and width < stack[-1]:
            stack.pop()
        if width > stack[-1]:
            stack.append(width)
        out.append("    " * (len(stack) - 1) + stripped)
    return "\n".join(out)


def _format_json(code):
    try:
        obj = json.loads(code)
    except Exception as e:
        raise _FormatError("JSON 解析失败：%s" % e)
    return json.dumps(obj, indent=4, ensure_ascii=False)


def _parse_markup(code):
    """把 HTML/XML 解析成节点树：("raw", 文本) / ("text", 文本) / ("el", 开标签, tag, 子节点)。"""
    parts = re.findall(
        r"<!--.*?-->|<!\[CDATA\[.*?\]\]>|<(?:[^>\"']|\"[^\"]*\"|'[^']*')*?>|[^<]+",
        code, re.S)
    root = []
    stack = [root]
    for part in parts:
        s = part.strip()
        if not s:
            continue
        if s.startswith("<!--") or s.startswith("<!["):     # 注释 / CDATA / 声明
            stack[-1].append(("raw", s))
            continue
        if s.startswith("</"):                              # 闭合标签：回到上一层
            if len(stack) > 1:
                stack.pop()
            continue
        if s.startswith("<"):
            mt = re.match(r"<\s*([A-Za-z][\w:.-]*)", s)
            tag = mt.group(1) if mt else ""
            self_close = s.endswith("/>") or tag.lower() in _FMT_VOID_TAGS or not tag
            node = ("el", s, tag, [])
            stack[-1].append(node)
            if not self_close:
                stack.append(node[3])
            continue
        stack[-1].append(("text", s))                       # 文本节点
    return root


def _render_markup(nodes, depth, out, max_len):
    for node in nodes:
        if node[0] in ("raw", "text"):
            out.append("    " * depth + node[1])
            continue
        _, open_str, tag, children = node
        if not children:
            out.append("    " * depth + open_str)
            continue
        # 子节点全是文本、且拼成一行不超长 → 整块一行展示（如 <groupId>xxx</groupId>）
        if all(c[0] == "text" for c in children):
            text = "".join(c[1] for c in children)
            close = "</%s>" % tag
            oneline = open_str + text + close
            if "\n" not in text and _display_width(oneline) <= max_len:
                out.append("    " * depth + oneline)
                continue
            out.append("    " * depth + open_str)
            for ln in text.split("\n"):
                if ln.strip():
                    out.append("    " * (depth + 1) + ln.strip())
            out.append("    " * depth + close)
            continue
        # 含子标签 → 逐层展开
        out.append("    " * depth + open_str)
        _render_markup(children, depth + 1, out, max_len)
        out.append("    " * depth + "</%s>" % tag)


def _format_markup(code):
    """缩进 HTML/XML：纯文本内容的标签尽量一行展示，含子标签的才逐层展开。"""
    out = []
    _render_markup(_parse_markup(code), 0, out, _FMT_MARKUP_MAX)
    return "\n".join(out)


def _format_code(lang, code):
    """按语言格式化代码；成功返回新文本，失败抛 _FormatError。"""
    lang = lang or ""
    if not code.strip():
        return code
    if lang == "json":
        return _format_json(code)
    if lang in ("html", "xml"):
        return _format_markup(code)
    if lang == "python":
        return _fmt_python(code)
    if lang in ("java", "bash", "sql"):
        return _fmt_reindent(code, lang)
    raise _FormatError("纯文本没有可格式化的结构")


class PopupMenu(object):
    """自绘的右键弹出菜单。

    原生 tk.Menu 在 Windows 上是方角灰底、悬停高亮生硬，和本应用自绘的控件风格
    割裂。这里用一个无边框 Toplevel + Canvas 画菜单卡片：直角底 + 1px 描边、
    整行悬停高亮、危险项标红，支持禁用项与分隔线，取色全部走当前调色板（含皮肤）。
    用直角而不是圆角：Tk 无边框窗口是矩形、无法裁剪圆角，圆角弧外会露出窗口直角
    底色（四个小尖角），直角卡片则与窗口严丝合缝。
    """

    def __init__(self, master, pal):
        self.master = master
        self.pal = dict(pal)
        self._items = []     # {"label","command","danger","enabled"}；label 为 None 表示分隔线
        self._hit = []       # 可点击行 (y0, y1, index)
        self._seps = []      # 分隔线纵坐标
        self._hover = -1
        self._win = None
        self._canvas = None
        self._width = 0
        self._height = 0
        try:
            self._font = tkfont.Font(family=FONT[0], size=FONT[1] - 1)
        except Exception:
            self._font = None

    # ---------- 组装 ----------
    def add(self, label, command=None, danger=False, enabled=True):
        self._items.append({"label": label, "command": command,
                            "danger": danger, "enabled": enabled})
        return self

    def add_separator(self):
        self._items.append({"label": None})
        return self

    def _measure(self, s):
        """量文字宽度；量不了就按全角字符估宽（宁可偏宽也不截断）。"""
        if self._font is not None:
            try:
                return self._font.measure(s)
            except Exception:
                pass
        return len(s) * (FONT[1] + 1)

    def _layout(self):
        """算卡片尺寸与每行命中区域（先量宽，再定位，避免弹出后跳大小）。"""
        text_w = max([self._measure(it["label"]) for it in self._items
                      if it["label"] is not None] or [0])
        self._width = MENU_PAD_X * 2 + MENU_GUTTER + text_w
        y = 6
        self._hit = []
        self._seps = []
        for idx, it in enumerate(self._items):
            if it["label"] is None:
                self._seps.append(y + MENU_SEP_H // 2)
                y += MENU_SEP_H
            else:
                self._hit.append((y, y + MENU_ROW_H, idx))
                y += MENU_ROW_H
        self._height = y + 6

    # ---------- 绘制 ----------
    def _render(self):
        c = self._canvas
        p = self.pal
        w, h = self._width, self._height
        line = p.get("line", "#cfcfd6")
        c.delete("all")
        # 卡片底：直角矩形 + 1px 描边，与无边框窗口严丝合缝（圆角会露出窗口直角底色）
        c.create_rectangle(1, 1, w - 1, h - 1,
                           fill=p.get("editor", "#ffffff"), outline=line, width=1)
        for y0, y1, idx in self._hit:
            it = self._items[idx]
            if idx == self._hover and it["enabled"]:
                c.create_rectangle(4, y0 + 1, w - 4, y1 - 1,
                                   fill=(MENU_DANGER if it["danger"] else p.get("sel", "#cfe0fb")),
                                   outline="")
                fg = "#ffffff" if it["danger"] else p.get("selfg", p.get("text", "#1f1f24"))
            elif not it["enabled"]:
                fg = p.get("soft", "#76767e")
            elif it["danger"]:
                fg = MENU_DANGER
            else:
                fg = p.get("text", "#1f1f24")
            c.create_text(MENU_PAD_X + MENU_GUTTER, (y0 + y1) // 2,
                          text=it["label"], anchor="w",
                          font=(FONT[0], FONT[1] - 1), fill=fg)
        # 分隔线：左右缩进，比通栏更克制
        for y in self._seps:
            c.create_line(MENU_PAD_X, y, w - MENU_PAD_X, y, fill=line)

    # ---------- 弹出 / 关闭 ----------
    def popup(self, x, y):
        """在屏幕坐标 (x, y) 弹出菜单；贴近屏幕边缘时自动内收，保证整框可见。"""
        self._layout()
        p = self.pal
        win = tk.Toplevel(self.master)
        win.overrideredirect(True)
        try:
            win.attributes("-topmost", True)
        except Exception:
            pass
        win.configure(bg=p.get("line", "#cfcfd6"))
        self._win = win
        c = tk.Canvas(win, width=self._width, height=self._height, bd=0,
                      highlightthickness=0, bg=p.get("line", "#cfcfd6"))
        c.pack()
        self._canvas = c
        self._render()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        px = max(0, min(int(x), sw - self._width))
        py = max(0, min(int(y), sh - self._height))
        win.geometry("+%d+%d" % (px, py))
        c.bind("<Motion>", self._on_motion)
        c.bind("<Button-1>", self._on_click)
        # grab 生效后事件是派发给「grab 窗口」即这个 Toplevel 的（坐标相对它），
        # 所以拦截点击/移动必须绑在 win 上；只绑 Canvas 时点菜单外毫无反应。
        win.bind("<Motion>", self._on_motion)
        win.bind("<Leave>", lambda e: self._set_hover(-1))
        win.bind("<Button-1>", self._on_click)
        win.bind("<Button-3>", lambda e: self.close())
        win.bind("<Escape>", lambda e: self.close())
        # 全局 grab：应用外（别的窗口 / 桌面）的点击也会落到菜单上，
        # 与原生右键菜单一致——点任何地方都消失。
        try:
            win.grab_set_global()
        except Exception:
            try:
                win.grab_set()
            except Exception:
                pass
        try:
            win.focus_force()
        except Exception:
            pass

    def close(self):
        win, self._win = self._win, None
        self._canvas = None
        if win is None:
            return
        try:
            win.grab_release()
        except Exception:
            pass
        try:
            win.destroy()
        except Exception:
            pass

    # ---------- 事件 ----------
    def _row_at(self, ey):
        for y0, y1, idx in self._hit:
            if y0 <= ey <= y1:
                return idx
        return -1

    def _set_hover(self, idx):
        if idx != self._hover:
            self._hover = idx
            if self._canvas is not None:
                self._render()

    def _on_motion(self, e):
        self._set_hover(self._row_at(e.y))

    def _on_click(self, e):
        # 点到卡片外（局部 grab 下坐标可能落在窗口外）→ 直接收起
        if not (0 <= e.x <= self._width and 0 <= e.y <= self._height):
            self.close()
            return
        idx = self._row_at(e.y)
        if idx < 0:
            self.close()
            return
        it = self._items[idx]
        if not it["enabled"]:
            return
        cmd = it["command"]
        self.close()
        # 先收菜单再执行：避免命令里的对话框抢焦点时菜单还挂着
        if cmd:
            self.master.after(10, cmd)


def uid():
    import random, string
    return "x" + "".join(random.choices(string.ascii_letters + string.digits, k=9))


def _t(s, tags=()):
        """把字符串拆成逐字符项，便于带格式序列化/还原。

        换行符自身不继承任何行级标签（title/h2/body）：若 title 的
        spacing1/spacing3 落在换行符上，会在标题与正文之间额外插出
        不可见空行，导致新笔记开头出现多余间距。普通标签（加粗/颜色/
        字体/下划线）仍按原样逐字符继承。
        """
        out = []
        for c in s:
            if c == "\n":
                out.append(["t", c, []])
            else:
                out.append(["t", c, list(tags)])
        return out

def _cb(checked, tags=()):
    return ["w", checked, list(tags)]

def seed():
    now = __import__("time").time() * 1000
    return {
        "folders": [
            {"id": "f_all", "name": "全部笔记", "smart": True},
            {"id": "f_star", "name": "星标笔记", "smart": True},
            {"id": "f_work", "name": "工作"},
            {"id": "f_life", "name": "个人"},
        ],
        "notes": [
            {"id": uid(), "folder": "f_work",
             "content": (_t("欢迎使用" + APP_NAME, ["title"]) + [["t", "\n", []]]
                         + _t("这是一款复刻 macOS 笔记的桌面程序，用你电脑自带的 Python 即可运行。")
                         + [["t", "\n", []]] + _t("试着用顶部工具栏：加粗、标题、待办清单都支持。")
                         + [["t", "\n", []]] + _t("所有内容自动保存在本地 notes_data.db。")
                         + [["t", "\n", []]]),
             "pinned": True, "starred": False, "created": now, "updated": now},
            {"id": uid(), "folder": "f_work",
             "content": (_t("周会待办", ["title"]) + [["t", "\n", []]]
                         + [_cb(False)] + _t(" 整理上周进度\n")
                         + [_cb(False)] + _t(" 同步需求文档\n")),
             "pinned": False, "starred": True, "created": now - 3600e3 * 3, "updated": now - 3600e3 * 3},
            {"id": uid(), "folder": "f_life",
             "content": (_t("读书清单", ["title"]) + [["t", "\n", []]]
                         + _t("《人类简史》\n《思考，快与慢》\n")),
             "pinned": False, "starred": False, "created": now - 3600e3 * 26, "updated": now - 3600e3 * 26},
            {"id": uid(), "folder": "f_life",
             "content": (_t("周末计划", ["title"]) + [["t", "\n", []]]
                         + _t("爬山 + 看一部电影\n")),
             "pinned": False, "starred": False, "created": now - 3600e3 * 50, "updated": now - 3600e3 * 50},
        ],
        "current_folder": "f_all",
        "current_note": None,
    }


def _fix_sample_note(data):
    """旧库里内置的示例笔记随应用改名。

    只认“标题整串恰好等于历史示例标题”的笔记：标题换成「欢迎使用素笺」，
    并把该篇正文里的“备忘录”措辞统一成“笔记”。用户自己改过标题的笔记一概不动。
    返回是否改动过（改动则调用方落库，plain 搜索列会一起重建）。
    """
    want = "欢迎使用" + APP_NAME
    old_titles = ("欢迎使用备忘录", "欢迎使用笔记")
    changed = False
    for n in data.get("notes", []):
        content = n.get("content") or []
        idxs = [i for i, it in enumerate(content)
                if len(it) > 2 and it[0] == "t" and it[2] and "title" in it[2]]
        if not idxs:
            continue
        if "".join(content[i][1] for i in idxs) not in old_titles:
            continue
        tags = list(content[idxs[0]][2])
        mark = set(idxs)
        start, end = idxs[0], idxs[0]
        while end < len(content) and end in mark:     # 标题字符是连续的一段
            end += 1
        head, tail = content[:start], content[end:]

        def _fixed(items):                            # 正文措辞同步（仅这一篇示例）
            # 内容是按字符存的（每个字一项），所以替换要先把同标签的连续文字合并成串，
            # 再按字符拆回去，否则“备忘录”跨项永远匹配不到。
            out = []
            buf, tags_run = [], None

            def flush():
                if buf:
                    for c in "".join(buf).replace("备忘录", "笔记"):
                        out.append(["t", c, list(tags_run)])
                    del buf[:]

            for it in items:
                it = list(it)
                if it[0] == "t":
                    if buf and it[2] != tags_run:
                        flush()
                    tags_run = it[2]
                    buf.append(it[1])
                else:
                    flush()
                    tags_run = None
                    out.append(it)
            flush()
            return out

        n["content"] = _fixed(head) + [["t", c, tags] for c in want] + _fixed(tail)
        changed = True
    return changed


def plain_of(n):
    """笔记纯文本（搜索用）：待办框记作一个空格，换行归为空格。"""
    s = []
    for it in n.get("content") or []:
        if it[0] == "t":
            s.append(it[1])
        elif it[0] == "w":
            s.append(" ")
        elif it[0] == "code":          # 代码块整段计入搜索文本
            s.append(it[2] if len(it) > 2 else "")
    return "".join(s).replace("\n", " ").strip()


class Store(object):
    """SQLite 数据层：所有数据落在 notes_data.db。

    为什么从 JSON 换成 SQLite：
      - 原来每次保存都整份 json.dump 整个数据集，且搜索要把每条笔记的 content
        逐字符拼成纯文本再逐个比对 —— 笔记一多就越用越卡。
      - 现在笔记正文只写「真正变过的那一条」（用签名比对），搜索交给 SQL：
        plain 列 + LIKE + ORDER BY，数据库自己过滤排序，Python 不再全表扫描。
    内存里仍然保持 self.data 这个 dict，界面代码无需跟着改。
    """

    def __init__(self, path=DB_FILE):
        self.path = path
        self.con = sqlite3.connect(path)
        self._sig = {}          # id -> 签名：跳过未变动的笔记，避免全量重写
        self._pos = {}          # id -> 列表位置：位置变了（如新建插到首位）也要回写
        self._init_db()

    # ---------- 建表 ----------
    def _init_db(self):
        c = self.con
        c.executescript("""
            CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
            CREATE TABLE IF NOT EXISTS folders (
                id    TEXT PRIMARY KEY,
                name  TEXT,
                smart INTEGER DEFAULT 0,
                pos   INTEGER);
            CREATE TABLE IF NOT EXISTS notes (
                id      TEXT PRIMARY KEY,
                folder  TEXT,
                content TEXT,
                plain   TEXT,
                pinned  INTEGER DEFAULT 0,
                starred INTEGER DEFAULT 0,
                created REAL,
                updated REAL,
                pos     INTEGER DEFAULT 0);
            CREATE INDEX IF NOT EXISTS idx_notes_folder ON notes(folder);
            CREATE INDEX IF NOT EXISTS idx_notes_star   ON notes(starred);
        """)
        # 老库补列（本版新增 pos）：没有它读出来的顺序不稳定，导出/新建顺序会漂
        cols = {r[1] for r in c.execute("PRAGMA table_info(notes)")}
        if "pos" not in cols:
            c.execute("ALTER TABLE notes ADD COLUMN pos INTEGER DEFAULT 0")
        c.commit()

    # ---------- 读 ----------
    def is_empty(self):
        return (self.con.execute("SELECT COUNT(*) FROM folders").fetchone()[0] == 0 and
                self.con.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 0)

    def load(self):
        """读库；空库（首次运行）时先从旧 notes_data.json 迁移，否则写入初始数据。"""
        if self.is_empty():
            d = None
            if os.path.exists(DATA_FILE):
                try:
                    with open(DATA_FILE, "r", encoding="utf-8") as f:
                        d = json.load(f)
                except Exception:
                    d = None
            if not (isinstance(d, dict) and d.get("folders")):
                d = seed()
            self.save(d, full=True)
        return self.read_all()

    def read_all(self):
        d = {"folders": [], "notes": [], "current_folder": "f_all", "current_note": None}
        for fid, name, smart in self.con.execute(
                "SELECT id, name, smart FROM folders ORDER BY pos"):
            f = {"id": fid, "name": name}
            if smart:
                f["smart"] = True
            d["folders"].append(f)
        for nid, folder, content, plain, pinned, starred, created, updated in self.con.execute(
                "SELECT id, folder, content, plain, pinned, starred, created, updated"
                " FROM notes ORDER BY pos, id"):
            try:
                body = json.loads(content) if content else []
            except Exception:
                body = []
            d["notes"].append({"id": nid, "folder": folder, "content": body,
                               "pinned": bool(pinned), "starred": bool(starred),
                               "created": created or 0.0, "updated": updated or 0.0})
        d["current_folder"] = self._meta("current_folder", "f_all")
        d["current_note"] = self._meta("current_note", None)
        for key in ("settings", "styles"):
            v = self._meta(key, None)
            if v:
                d[key] = v
        return d

    def _meta(self, k, default):
        row = self.con.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        if row is None or row[0] in (None, "", "null"):
            return default
        try:
            return json.loads(row[0])
        except Exception:
            return default

    # ---------- 写 ----------
    def save(self, d, full=False):
        """把内存数据落库。默认只写签名变了的笔记（full=True 时全量重写）。"""
        c = self.con
        with c:
            c.execute("DELETE FROM folders")
            for i, f in enumerate(d.get("folders") or []):
                c.execute("INSERT INTO folders(id,name,smart,pos) VALUES(?,?,?,?)",
                          (f["id"], f.get("name", ""),
                           1 if f.get("smart") else 0, i))
            seen = set()
            for i, n in enumerate(d.get("notes") or []):
                nid = n["id"]
                seen.add(nid)
                pos = i
                sig = self._sig_of(n)
                if not full and self._sig.get(nid) == sig and self._pos.get(nid) == pos:
                    continue          # 没变过 → 不写，省掉一次 JSON 序列化 + 磁盘写
                c.execute("INSERT OR REPLACE INTO notes"
                          "(id,folder,content,plain,pinned,starred,created,updated,pos)"
                          " VALUES(?,?,?,?,?,?,?,?,?)",
                          (nid, n.get("folder"),
                           json.dumps(n.get("content") or [], ensure_ascii=False),
                           plain_of(n),
                           1 if n.get("pinned") else 0,
                           1 if n.get("starred") else 0,
                           n.get("created") or 0.0, n.get("updated") or 0.0, pos))
                self._sig[nid] = sig
                self._pos[nid] = pos
            for k in [k for k in self._sig if k not in seen]:
                self._sig.pop(k, None)
                self._pos.pop(k, None)
            # 删掉内存里已不存在的笔记。注意不能写 "id NOT IN (全部 id)"：
            # SQLite 的占位符数量有上限（本项目 Python 自带的版本是 999），
            # 笔记一多就会报 "too many SQL variables"，所以先取出库里的 id
            # 算出差集，再按批（每批 400）删。
            gone = [r[0] for r in c.execute("SELECT id FROM notes")
                    if r[0] not in seen]
            for i in range(0, len(gone), 400):
                chunk = gone[i:i + 400]
                c.execute("DELETE FROM notes WHERE id IN (%s)" %
                          ",".join("?" * len(chunk)), chunk)
            for k in ("current_folder", "current_note", "settings", "styles"):
                if k in d:
                    c.execute("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)",
                              (k, json.dumps(d[k], ensure_ascii=False)))

    def _sig_of(self, n):
        """「这条笔记是否变过」的廉价判据（不序列化全文）。

        编辑必然刷新 updated，星标/置顶/移动也都在签名里，足以覆盖所有改动路径。
        """
        return (n.get("updated"), n.get("starred"), n.get("pinned"),
                n.get("folder"), len(n.get("content") or []))

    # ---------- 搜索 ----------
    def search_ids(self, folder, q):
        """关键词检索：SQL 里过滤 + 排序，直接返回排好序的 id 列表。

        SQLite 的 LIKE 默认对 ASCII 不区分大小写（case_sensitive_like 关闭），
        中文本来也无大小写问题，所以直接用即可。
        """
        where, args = ["plain LIKE ?"], ["%" + q + "%"]
        if folder == "f_star":
            where.append("starred=1")
        elif folder and folder != "f_all":
            where.append("folder=?")
            args.append(folder)
        sql = ("SELECT id FROM notes WHERE " + " AND ".join(where) +
               " ORDER BY pinned DESC, starred DESC, updated DESC")
        return [r[0] for r in self.con.execute(sql, args)]

    # ---------- 清空 / 重置 ----------
    def reset(self, d=None):
        """清空整库并写入给定（或初始）数据 —— 清空数据 / 导入时用它。"""
        d = d if d is not None else seed()
        with self.con:
            self.con.execute("DELETE FROM notes")
            self.con.execute("DELETE FROM folders")
            self.con.execute("DELETE FROM meta")
        self._sig = {}
        self._pos = {}
        self.save(d, full=True)

    def close(self):
        try:
            self.con.commit()
            self.con.close()
        except Exception:
            pass


_STORE = None


def store():
    """全局唯一的 Store（懒创建，避免 import 时就动数据库文件）。"""
    global _STORE
    if _STORE is None:
        _STORE = Store(DB_FILE)
    return _STORE


def load_data():
    return store().load()


def save_data(d):
    store().save(d)


class SlimScrollbar(tk.Canvas):
    """列表右侧的细滚动条（自绘）：只在内容超出时出现，可拖、可点轨道、可滚轮。

    为什么不用 tk.Scrollbar：系统滚动条是直角色块 + 两端箭头，和本项目自绘的圆角
    控件不搭，还会占掉 17px 把两列挤窄。这里只用 8px，正好落在卡片左右留白
    （CARD_PADX）里，拇指是圆角小条，颜色随皮肤由面板底色向次级文字色混合而来。
    """

    W = 8          # 占位宽度（画 6px 拇指，两侧各留 1px，不压到卡片）
    INSET = 4      # 上下留白，拇指不贴面板边缘
    THUMB_MIN = 26

    def __init__(self, master, bg, palette, target):
        tk.Canvas.__init__(self, master, width=self.W, bd=0, highlightthickness=0,
                           bg=bg, cursor="arrow", takefocus=0)
        self._bg = bg
        self._pal = dict(palette)
        self._target = target          # 要控制的 Canvas（内容画布）
        self._first, self._last = 0.0, 1.0
        self._hover = False
        self._drag = None              # 拖动时记住「鼠标 y − 拇指顶 y」
        self._thumb = (self.INSET, self.THUMB_MIN)
        self.bind("<Configure>", lambda e: self.sync(self._first, self._last))
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Motion>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<MouseWheel>", lambda e: self._scroll_wheel(e.delta))
        self.bind("<Button-4>", lambda e: self._target.yview_scroll(-3, "units"))
        self.bind("<Button-5>", lambda e: self._target.yview_scroll(3, "units"))

    # ---------- 同步与绘制 ----------
    def sync(self, first, last):
        """由内容画布的 yscrollcommand 驱动：位置/长度变了就重画，不溢出则隐藏。"""
        self._first, self._last = float(first), float(last)
        page = self._last - self._first
        if page >= 0.999:                 # 内容没占满：整条隐藏
            if self.winfo_ismapped():
                self.place_forget()
            return
        if not self.winfo_ismapped():     # 需要时浮在列表右侧（叠在留白里，不动布局）
            self.place(relx=1.0, rely=0.0, anchor="ne", width=self.W, relheight=1.0)
            tk.Misc.lift(self)
        # 刚 place 时自身高度还是 1，退回面板高度算拇指（下一次 Configure 会再校准）
        h = self.winfo_height()
        if h <= 4:
            h = self.master.winfo_height()
        if h <= 4:
            return
        track = h - self.INSET * 2
        th = max(self.THUMB_MIN, int(round(page * track)))
        th = min(th, track)
        span = max(1e-6, 1.0 - page)
        top = self.INSET + (track - th) * (self._first / span)
        self._thumb = (top, th)
        self._draw()

    def _draw(self):
        self.delete("all")
        top, th = self._thumb
        if self._drag is not None:
            mix = 0.66
        elif self._hover:
            mix = 0.54
        else:
            mix = 0.36
        fill = _mix_hex(self._bg, self._pal.get("soft", "#76767e"), mix)
        self.create_polygon(_round_rect_points(1, top, self.W - 1, top + th, 3, 6),
                            fill=fill, outline="")

    def apply_theme(self, pal, bg):
        self._pal = dict(pal)
        self._bg = bg
        self.configure(bg=bg)
        self._draw()

    def _set_hover(self, on):
        if self._hover != on:
            self._hover = on
            self._draw()

    # ---------- 交互 ----------
    def _scroll_wheel(self, delta):
        if not delta:
            return
        d = -1 * (delta // 40)
        self._target.yview_scroll(d or (-1 if delta > 0 else 1), "units")

    def _on_press(self, e):
        top, th = self._thumb
        if top <= e.y <= top + th:        # 按在拇指上 → 开始拖动
            self._drag = e.y - top
            self._draw()
            return
        # 按在轨道上 → 翻一页（朝点击方向）
        self._target.yview_scroll(-1 if e.y < top else 1, "pages")

    def _on_drag(self, e):
        if self._drag is None:
            return
        h = self.winfo_height()
        track = h - self.INSET * 2
        th = self._thumb[1]
        room = max(1, track - th)
        top = min(max(self.INSET, e.y - self._drag), self.INSET + room)
        frac = (top - self.INSET) / float(room)
        self._target.yview_moveto(min(1.0, max(0.0, frac)))

    def _on_release(self, e):
        self._drag = None
        self._draw()


class ScrollPanel(tk.Frame):
    """带滚动条的容器，内部用 Frame 摆放自定义行。"""
    def __init__(self, parent, bg, palette):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        # 每个滚轮“单位”对应 20px，保证一次滚动有明显位移（避免“像没反应”）
        self.canvas.configure(yscrollincrement=20)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self.inner_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        # 右侧细滚动条：叠在卡片右侧留白上，只在内容超出时出现
        self.sb = SlimScrollbar(self, bg, palette, self.canvas)
        self.canvas.configure(yscrollcommand=self._on_yscroll)
        self._inner_kids = -1               # 上次结算时的行数（用于判断要不要重算高度）
        self.inner.bind("<Configure>", lambda e: self._update())
        self.canvas.bind("<Configure>", self._on_cfg)
        # 滚轮双保险（两条路径，靠 _is_own 去重，避免一次滚动被处理两次）：
        # A) 子控件逐个绑定：Tk 里子控件不会把事件冒泡给父级 Canvas（父级不在其 bindtags 中），
        #    所以必须逐个绑定，指针停在行/图标/文字上才滚得动。
        # B) bind_all 全局兜底：Windows 把滚轮事件发给「焦点控件」而非指针下方控件，
        #    焦点在右侧编辑器时，靠指针位置路由回本面板。
        self._wheel_widgets = set()
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        self.canvas.bind_all("<Button-4>", lambda e: self._on_wheel_dir(1, e))
        self.canvas.bind_all("<Button-5>", lambda e: self._on_wheel_dir(-1, e))
        self.bind_wheel()

    def bind_wheel(self):
        """给画布与内部所有子控件递归绑定滚轮（内容重建后必须重新调用）。"""
        self._wheel_widgets = set()

        def _bind(w):
            self._wheel_widgets.add(str(w))
            try:
                w.bind("<MouseWheel>", self._on_wheel_child)
                w.bind("<Button-4>", lambda e, d=1: self._on_wheel_child_dir(d))
                w.bind("<Button-5>", lambda e, d=-1: self._on_wheel_child_dir(d))
            except Exception:
                pass
            for c in w.winfo_children():
                _bind(c)

        _bind(self.canvas)
        _bind(self.inner)
        if self.sb is not None:
            # 滚动条自己处理滚轮，登记进本面板避免 bind_all 兜底再滚一次
            self._wheel_widgets.add(str(self.sb))

    def _on_yscroll(self, first, last):
        """内容画布的 yscrollcommand：交给右侧细滚动条同步拇指。"""
        self.sb.sync(first, last)

    def _is_own(self, widget):
        """判断事件来源控件是否属于本面板（用于两条滚轮路径去重）。"""
        try:
            return str(widget) in self._wheel_widgets
        except Exception:
            return False

    def _in_region(self, e):
        x1 = self.canvas.winfo_rootx()
        y1 = self.canvas.winfo_rooty()
        x2 = x1 + self.canvas.winfo_width()
        y2 = y1 + self.canvas.winfo_height()
        return x1 <= e.x_root <= x2 and y1 <= e.y_root <= y2

    def _nudge_reqheight(self):
        """强制内部 Frame 重算「请求高度」。

        这台机器的 Tk 有个坑：销毁子控件后 Frame 的 winfo_reqheight 不失效
        （仍是销毁前的值），于是列表清空/筛没之后滚动区停在旧高度、滚动条不消失。
        关掉传播→写死 1px→再打开传播，就是一次廉价的重算触发。
        """
        try:
            self.inner.pack_propagate(False)
            self.inner.configure(width=1, height=1)
            self.inner.pack_propagate(True)
        except Exception:
            pass

    def _update(self):
        """刷新可滚动区域（内容增减后调用，确保滚动条启用、可拖）。

        采用双重兜底，保证任何时序下 scrollregion 都正确，彻底解决
        “文件夹/笔记铺满后无法滚动”：
        1) 优先用内部 Frame 的「自然高度」winfo_reqheight——由几何管理器同步算出，
           不依赖画布实际渲染像素（bbox("all") 在窗口未完全映射时常返回过小值）；
        2) 若自然高度为 0（如尚未布局），回退到 bbox("all") 的像素高度。
        """
        kids = len(self.inner.winfo_children())
        if kids != self._inner_kids:        # 行数变了：先让 Frame 重算请求高度（见上）
            self._inner_kids = kids
            self._nudge_reqheight()
        w = self.canvas.winfo_width() or 200
        h = self.inner.winfo_reqheight() or 0
        if h == 0:
            b = self.canvas.bbox("all")
            if b:
                h = b[3] - b[1]
        # 列表没占满可视区时：滚动区锁定为可视区高度 → 完全不产生滚动（滚轮无位移）
        vis = self.canvas.winfo_height()
        if vis and h < vis:
            h = vis
        self.canvas.configure(scrollregion=(0, 0, w, h))
        # 滚动区域变了，拇指的显隐/长度要立刻跟上（不能等下一次滚动）
        if self.sb is not None:
            first, last = self.canvas.yview()
            self.sb.sync(first, last)

    def _on_cfg(self, e):
        self.canvas.itemconfig(self.inner_id, width=e.width)
        # 窗口尺寸变化后重新计算滚动区域（避免“铺满后无法滚动”）
        self._update()

    def _scroll(self, delta):
        """按滚轮增量滚动（Windows: delta 为 ±120 的倍数，触控板更小）。"""
        if not delta:
            return
        d = -1 * (delta // 40)              # 每格约 3 单位（×20px ≈ 60px）
        if d == 0:                          # 小步长（触控板）时至少滚 1 单位
            d = -1 if delta > 0 else 1
        self.canvas.yview_scroll(d, "units")

    def _on_wheel_child(self, e):
        """子控件上的滚轮：事件必然来自本面板，直接滚动（无需区域判定）。"""
        self._scroll(getattr(e, "delta", 0))

    def _on_wheel_child_dir(self, direction):
        """子控件上的 Linux 滚轮（Button-4/5）。direction: 1=上, -1=下。"""
        self.canvas.yview_scroll(-direction * 3, "units")

    def _on_wheel(self, e):
        """全局兜底：焦点在别处时按指针位置把滚轮路由到本面板。"""
        if self._is_own(e.widget):          # 已由子控件路径处理 → 跳过，避免双倍
            return
        if not self._in_region(e):
            return
        self._scroll(getattr(e, "delta", 0))

    def _on_wheel_dir(self, direction, e=None):
        """Linux / 旧平台全局兜底：Button-4 向上、Button-5 向下。"""
        if e is not None:
            if self._is_own(e.widget):
                return
            if not self._in_region(e):
                return
        self.canvas.yview_scroll(-direction * 3, "units")


class TodoBox(tk.Canvas):
    """自绘的待办勾选框（方形：未勾=浅灰方块，勾选=绿底白勾，对勾居中）。"""
    SIZE = 22

    def __init__(self, parent, app, checked=False):
        super().__init__(parent, width=self.SIZE, height=self.SIZE,
                         bg=app.pal["editor"], bd=0, highlightthickness=0, cursor="hand2")
        self.app = app
        self.var = tk.BooleanVar(value=checked)
        self._draw()
        self.bind("<Button-1>", lambda e: self.app._on_todo_toggle(self))

    def _draw(self):
        self.delete("all")
        p = self.app.pal
        pad = 3
        s = self.SIZE - pad * 2
        fill = p["green"] if self.var.get() else p["chk_off"]
        self.create_rectangle(pad, pad, pad + s, pad + s, fill=fill, outline=fill)
        if self.var.get():
            cx = pad + s / 2
            cy = pad + s / 2
            x1, y1 = cx - 5.5, cy + 1.5
            x2, y2 = cx - 1.0, cy + 5.0
            x3, y3 = cx + 6.0, cy - 5.0
            self.create_line(x1, y1, x2, y2, x3, y3, fill="white", width=2.5,
                             capstyle="round", joinstyle="round")


def _has_cjk(s):
    """是否含中日韩字符 / 全角符号（这些字走中文字体，行高比等宽字体高）。"""
    for ch in s:
        if ord(ch) > 0x2E7F:
            return True
    return False


def _luminance(hexv):
    """颜色亮度（0~1），用来判断当前皮肤是浅底还是深底。"""
    h = (hexv or "#000000").lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except Exception:
        r, g, b = 0, 0, 0
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0


def _sel_fg(pal):
    """文字被选中时的前景色。

    Windows 上的 Tk 默认把「选中文字」画成白色（SystemHighlightText，标签色也会被
    盖掉），而皮肤给的选区底色是浅色（#dbe8dc 之类）→ 选中一段代码就白成一片，
    根本看不清。这里按选区底色算一个对比够大的前景色，显式设给 -selectforeground。
    """
    sel = pal.get("sel", "#cfe0fb")
    text = pal.get("text", "#1f1f24")
    if abs(_luminance(sel) - _luminance(text)) >= 0.34:
        return text
    return "#ffffff" if _luminance(sel) < 0.5 else "#1f2328"


def _code_palette(pal):
    """由调色板推导代码块配色：(底色, 语言条底色, 描边, 分词色表)。

    不写死颜色，底色由编辑区底色向文字色微混，换皮肤时整块自动跟着走；
    浅底/深底各有一套分词色，保证任何一种皮肤下关键字都读得清。
    """
    editor = pal.get("editor", "#ffffff")
    text = pal.get("text", "#000000")
    # 深底皮肤用另一套分词色：判据看「编辑区底色」——代码块底色就是从它掺出来的
    dark = _luminance(editor) < 0.5
    bg = _mix_hex(editor, text, 0.07 if dark else 0.045)
    head = _mix_hex(bg, text, 0.04)
    border = _mix_hex(bg, text, 0.14)
    return bg, head, border, CODE_TOKEN_COLORS["dark" if dark else "light"]


class CodeBlock(tk.Frame):
    """笔记里的一段代码块：顶部语言条 + 等宽代码区（带轻量语法高亮）。

    为什么在正文里嵌一个真正的小 Text（而不是给正文加样式标签）：
      - 代码需要「成块」：统一底色、等宽字体、语言标签，还能整块删除；
      - 用 Text 的 window_create 嵌进去后，光标、选择、撤销、复制粘贴、缩进
        全是系统原生行为，用户不用学一套新的输入方式；
      - 语言可随时切换（点顶部标签），高亮跟着重算。
    高度按代码实际占用的像素自适应，超过 MAX_H 就不再长（改为内部滚动 + 右侧
    细滚动条），宽度跟随编辑区宽度，窗口缩放时不跑版。
    """

    HEADER_H = 26
    PADX = 10
    PADY = 7
    MAX_H = 560             # 代码区最高像素（约 33 行）；超过就内部滚动 + 右侧细滚动条

    def __init__(self, parent, app, lang="", code=""):
        tk.Frame.__init__(self, parent, bd=0, highlightthickness=1)
        self.app = app
        self.lang = lang if lang in CODE_LANG_LABEL else ""
        self._loading = True
        self._hl_job = None
        self._width = 0
        self._body_h = 0
        self._scrolls = False       # 内容是否高过 MAX_H（决定右侧细滚动条出不出现）
        self._build()
        self.set_code(code)
        self.set_lang(self.lang, notify=False)
        self.apply_theme(app.pal)
        self._loading = False

    # ---------- 组装 ----------
    def _build(self):
        # 高度由 set_width/fit 显式给定（按代码行数算），所以关掉传播：
        # 否则「请求高度」由子控件决定，代码区会被压成一条线。
        self.pack_propagate(False)
        self._head = tk.Frame(self, height=self.HEADER_H, bd=0, highlightthickness=0)
        self._head.pack(fill=tk.X)
        self._head.pack_propagate(False)
        # 语言标签：点了弹出语言菜单（可随时改语言 / 复制 / 删除这一块）
        self._chip = tk.Label(self._head, bd=0, padx=7, pady=1, cursor="hand2")
        self._chip.pack(side=tk.LEFT, padx=(8, 0), pady=3)
        self._chip.bind("<Button-1>", self._open_menu)
        self._copy = tk.Label(self._head, bd=0, padx=6, pady=1, text="复制", cursor="hand2")
        self._copy.pack(side=tk.RIGHT, padx=(0, 8), pady=3)
        self._copy.bind("<Button-1>", lambda e: self.copy_code())
        self._copy.bind("<Enter>", lambda e: self._copy.configure(fg=self.app.pal["accent"]))
        self._copy.bind("<Leave>", lambda e: self._copy.configure(fg=self.app.pal["soft"]))
        # 格式化：先 pack 复制、再 pack 格式化，RIGHT 侧从右往左叠 → 格式化落在复制左边
        self._fmt = tk.Label(self._head, bd=0, padx=6, pady=1, text="格式化", cursor="hand2")
        self._fmt.pack(side=tk.RIGHT, padx=(0, 2), pady=3)
        self._fmt.bind("<Button-1>", lambda e: self.format_code())
        self._fmt.bind("<Enter>", lambda e: self._fmt.configure(fg=self.app.pal["accent"]))
        self._fmt.bind("<Leave>", lambda e: self._fmt.configure(fg=self.app.pal["soft"]))
        self._sep = tk.Frame(self, height=1, bd=0, highlightthickness=0)
        self._sep.pack(fill=tk.X)
        self._body = tk.Frame(self, bd=0, highlightthickness=0)
        self._body.pack(fill=tk.BOTH, expand=True)
        self._body.pack_propagate(False)
        fam, size = self.app.code_font()
        self._mono, self._fsize = fam, size
        self._char_w = float(size) * 0.6
        self._line_h = self._measure_line()
        self.inner = tk.Text(self._body, bd=0, highlightthickness=0, wrap=tk.WORD,
                            undo=True, exportselection=False,
                            padx=self.PADX, pady=self.PADY, font=(fam, size))
        self.inner.place(x=0, y=0, relwidth=1.0, relheight=1.0)
        # 右侧细滚动条：代码超过 MAX_H 才出现（浮在代码区右留白里，不占宽度）
        self.sb = SlimScrollbar(self._body, self.app.pal.get("editor", "#ffffff"),
                                self.app.pal, self.inner)
        self.inner.configure(yscrollcommand=self._on_yscroll)
        self._configure_tags()
        self.inner.bind("<<Modified>>", self._on_edit)
        self.inner.bind("<MouseWheel>", self._on_wheel)
        self.inner.bind("<Tab>", self._on_tab)
        self.inner.bind("<Shift-Tab>", self._on_tab)
        try:                        # X11 下 Shift+Tab 是 ISO_Left_Tab（Windows 没这个 keysym）
            self.inner.bind("<ISO_Left_Tab>", self._on_tab)
        except Exception:
            pass
        self.inner.bind("<BackSpace>", self._on_backspace)
        # 滚进视野后才真正被布局（Text 只映射可视区里的嵌入窗口）→ 这时重新量一次高度
        self.inner.bind("<Map>", lambda e: self.app._after_idle(self.fit))
        self.inner.bind("<Escape>", lambda e: (self.app.text.focus_set(), "break")[1])
        self._head.bind("<Button-1>", lambda e: self.focus_code())
        self.configure(height=self.HEADER_H + 1 + self._line_h + 2 * self.PADY)

    def _measure_line(self):
        """量出「纯等宽行」和「含中文行」的行高 + 单字符宽度（等宽字体，可直接算换行）。"""
        try:
            import tkinter.font as _f
            f = _f.Font(root=self.inner, family=self._mono, size=self._fsize)
            cjk = _f.Font(root=self.inner, family=FONT[0], size=self._fsize)
            self._char_w = max(1.0, float(f.measure("0")))
            self._line_h_cjk = max(int(f.metrics("linespace")),
                                   int(cjk.metrics("linespace")))
            return int(f.metrics("linespace"))
        except Exception:
            self._char_w = max(1.0, self._fsize * 0.6)
            self._line_h_cjk = self._fsize + 8
            return self._fsize + 6

    def _configure_tags(self):
        """分词标签：字重/字号随代码字体，颜色由主题给（关键词加粗更醒目）。"""
        cols = self._scheme()[3]
        f = (self._mono, self._fsize)
        self.inner.tag_configure("c_kw", font=f + ("bold",), foreground=cols["kw"])
        self.inner.tag_configure("c_tag", font=f + ("bold",), foreground=cols["tag"])
        self.inner.tag_configure("c_com", font=f + ("italic",), foreground=cols["com"])
        for name in ("str", "num", "lit", "var", "key", "attr", "dec", "ann"):
            self.inner.tag_configure("c_" + name, font=f, foreground=cols[name])
        self._token_tags = ["c_" + n for n in
                           ("num", "kw", "tag", "str", "key", "attr", "var", "lit",
                            "dec", "ann", "com")]

    def _scheme(self):
        if getattr(self, "_cols", None) is None:
            self._cols = _code_palette(self.app.pal)
        return self._cols

    # ---------- 主题 / 字号 ----------
    def apply_theme(self, pal):
        """换肤：底色、描边、语言标签、分词颜色全部重算并重画。"""
        self._cols = None
        bg, head, border, _cols = self._scheme()
        accent = pal["accent"]
        self.configure(bg=bg, highlightbackground=border, highlightcolor=border)
        self._head.configure(bg=head)
        self._sep.configure(bg=border)
        self._body.configure(bg=bg)
        self._chip.configure(bg=_mix_hex(head, accent, 0.16), fg=accent,
                             font=(FONT[0], 10, "bold"))
        self._copy.configure(bg=head, fg=pal["soft"], font=(FONT[0], 10))
        self._fmt.configure(bg=head, fg=pal["soft"], font=(FONT[0], 10))
        self.inner.configure(bg=bg, fg=pal.get("text", "#1f1f24"),
                             insertbackground=accent,
                             selectbackground=pal.get("sel", "#cfe0fb"),
                             selectforeground=_sel_fg(pal),
                             inactiveselectbackground=pal.get("sel2", "#e0e9f7"))
        try:
            self.sb.apply_theme(pal, bg)
        except Exception:
            pass
        self._configure_tags()
        self._refresh_chip()
        self._highlight()

    def apply_size(self, size):
        """正文字号变化：代码字号跟着变（比正文小 1 号），高度重算。"""
        self._fsize = max(8, int(size))
        self.inner.configure(font=(self._mono, self._fsize))
        self._line_h = self._measure_line()
        self._configure_tags()
        self.fit()

    def _refresh_chip(self):
        label = CODE_LANG_LABEL.get(self.lang, "纯文本")
        self._chip.configure(text=label)

    # ---------- 语言 / 内容 ----------
    def set_lang(self, key, notify=True):
        self.lang = key if key in CODE_LANG_LABEL else ""
        self._refresh_chip()
        self._highlight()
        if notify:
            self.app.mark_modified()

    def set_code(self, code):
        self._loading = True
        self.inner.delete("1.0", tk.END)
        if code:
            self.inner.insert("1.0", code)
        self.inner.edit_reset()
        self.inner.edit_modified(False)
        self._loading = False
        self._highlight()
        self.fit()

    def snapshot(self):
        """取回代码文本（Tk 的 Text 末尾恒有一个换行，去掉它再存）。"""
        return self.inner.get("1.0", "end-1c")

    def focus_code(self):
        try:
            self.inner.focus_set()
            self.inner.mark_set("insert", "end-1c")
            self.inner.see(tk.INSERT)
        except Exception:
            pass

    def copy_code(self):
        code = self.snapshot()
        if not code:
            return
        try:
            self.app.root.clipboard_clear()
            self.app.root.clipboard_append(code)
        except Exception:
            return
        self._copy.configure(text="已复制", fg=self.app.pal["green"])
        self.app._after(1100, lambda: self._copy.configure(
            text="复制", fg=self.app.pal["soft"]))

    def format_code(self):
        """按当前语言格式化代码块：JSON/HTML/XML 走结构化重排，其余重排缩进。"""
        code = self.snapshot()
        if not code.strip():
            return
        try:
            out = _format_code(self.lang, code)
        except _FormatError as e:
            self.app._show_error("格式化失败", str(e))
            return
        except Exception as e:
            self.app._show_error("格式化失败", "无法格式化这段代码：%s" % e)
            return
        if out == code:
            self._flash_fmt("已是最新")
            return
        self.set_code(out)
        self.app.mark_modified()
        self._flash_fmt("已格式化")

    def _flash_fmt(self, text):
        """在「格式化」标签上短暂反馈结果，随后复原。"""
        try:
            self._fmt.configure(text=text, fg=self.app.pal["green"])
        except Exception:
            return
        self.app._after(1100, lambda: self._fmt.configure(
            text="格式化", fg=self.app.pal["soft"]))

    def delete_block(self):
        self.app._remove_code_block(self)

    def _open_menu(self, event=None):
        p = self.app.pal
        m = PopupMenu(self.app.root, p)
        for label, key in CODE_LANGS:
            m.add(("# " if key == self.lang else "") + label,
                  command=lambda k=key: self.set_lang(k))
        m.add_separator()
        m.add("复制代码", command=self.copy_code)
        m.add("格式化代码", command=self.format_code)
        m.add("删除代码块", danger=True, command=self.delete_block)
        x = self._chip.winfo_rootx()
        y = self._chip.winfo_rooty() + self._chip.winfo_height() + 2
        m.popup(x, y)

    # ---------- 编辑 ----------
    def _on_edit(self, ev=None):
        if not self.inner.edit_modified():
            return
        self.inner.edit_modified(False)
        if self._loading:
            return
        self._schedule_highlight()
        self.fit()
        self.app.mark_modified()
        # 敲到看不见的地方（块内有滚动条时）把光标带回视野
        try:
            self.inner.see(tk.INSERT)
        except Exception:
            pass

    def _sel_lines(self):
        """选区覆盖的行号范围（1 起，闭区间）；没有选区时就是光标所在行。"""
        try:
            if self.inner.tag_ranges("sel"):
                a, b = self.inner.index("sel.first"), self.inner.index("sel.last")
                # 选区正好停在行首时，那一行不算（和多数编辑器一致）
                if self.inner.compare(b, ">", a) and \
                        self.inner.compare(b, "==", self.inner.index(b + " linestart")):
                    b = self.inner.index("%s -1c" % b)
                return (int(a.split(".")[0]), int(b.split(".")[0]))
        except Exception:
            pass
        ln = int(self.inner.index(tk.INSERT).split(".")[0])
        return ln, ln

    def _indent_sel(self, delta):
        """把选区（或光标所在行）整体缩进/反缩进 4 空格，并保持选中状态。"""
        ln0, ln1 = self._sel_lines()
        if delta > 0:
            for ln in range(ln0, ln1 + 1):
                self.inner.insert("%d.0" % ln, "    ")
        else:
            for ln in range(ln0, ln1 + 1):
                line = self.inner.get("%d.0" % ln, "%d.end" % ln)
                n = 0
                while n < 4 and n < len(line) and line[n] == " ":
                    n += 1
                if n == 0 and line[:1] == "\t":
                    n = 1
                if n:
                    self.inner.delete("%d.0" % ln, "%d.%d" % (ln, n))
        self.inner.edit_separator()
        self.inner.tag_remove("sel", "1.0", tk.END)
        self.inner.tag_add("sel", "%d.0" % ln0, "%d.end" % ln1)
        self._schedule_highlight()
        self.fit()
        self.app.mark_modified()
        try:
            self.inner.see(tk.INSERT)
        except Exception:
            pass
        return "break"

    def _on_tab(self, event=None):
        """Tab 缩进（选中多行时整块缩进），Shift+Tab 反缩进。

        代码里缩进比切换焦点常用得多，所以 Tab 一律自己吃掉。
        """
        shift = bool(getattr(event, "state", 0) & 0x1) or \
            getattr(event, "keysym", "") in ("ISO_Left_Tab", "Shift-Tab")
        if shift or self.inner.tag_ranges("sel"):
            return self._indent_sel(-1 if shift else 1)
        self.inner.insert(tk.INSERT, "    ")
        try:
            self.inner.see(tk.INSERT)
        except Exception:
            pass
        return "break"

    def _on_backspace(self, event=None):
        """空代码块里按退格 = 删掉这一块（和有内容的退格一样符合直觉）。"""
        if self.snapshot().strip() == "" and \
                self.inner.compare("insert", "==", "1.0"):
            self.delete_block()
            return "break"
        return None

    def _on_wheel(self, event=None):
        """滚轮：代码块内部还能滚就滚内部，否则把滚动交给外层笔记（别卡住不滚）。"""
        delta = getattr(event, "delta", 0)
        if not delta:
            return None
        go = -1 if delta > 0 else 1
        try:
            first, last = self.inner.yview()
        except Exception:
            first, last = 0.0, 1.0
        if (go < 0 and first > 0.0) or (go > 0 and last < 1.0):
            self.inner.yview_scroll(go, "units")
        else:
            self.app.text.yview_scroll(go, "units")
        return "break"

    # ---------- 高亮 / 高度 ----------
    def _schedule_highlight(self):
        """输入时不必每键重扫全文：合并成一次（120ms 内的连续输入只算一遍）。"""
        if self._hl_job is not None:
            try:
                self.app._cancel_after(self._hl_job)
            except Exception:
                pass
        self._hl_job = self.app._after(120, self._highlight)

    def _highlight(self):
        self._hl_job = None
        try:
            code = self.inner.get("1.0", "end-1c")
        except Exception:
            return
        for t in getattr(self, "_token_tags", []):
            self.inner.tag_remove(t, "1.0", tk.END)
        rules = CODE_RULES.get(self.lang) or []
        if not rules or not code or len(code) > 20000:
            return
        for tag, pat, flags in rules:                 # 列表顺序 = 标签栈顺序
            try:
                rx = re.compile(pat, flags)
            except Exception:
                continue
            for m in rx.finditer(code):
                a = "1.0 + %dc" % m.start()
                b = "1.0 + %dc" % m.end()
                self.inner.tag_add("c_" + tag, a, b)
        for t in getattr(self, "_token_tags", []):     # 后加的规则压在上面
            try:
                self.inner.tag_raise(t)
            except Exception:
                pass

    def set_width(self, px, fit=True):
        px = max(120, int(px))
        if px == self._width:
            return
        self._width = px
        try:
            self.configure(width=px)
        except Exception:
            pass
        if fit:
            self.fit()

    def _estimate_height(self):
        """按字体度量估算内容像素高（含中文的行更高，按当前宽度算折行）。"""
        avail = max(40, (self._width or 400) - 2 * self.PADX)
        per = max(1, int(avail / max(1.0, self._char_w)))
        total = 0
        for line in self.inner.get("1.0", "end-1c").split("\n"):
            wraps = max(1, -(-len(line) // per))        # 向上取整
            total += wraps * (self._line_h_cjk if _has_cjk(line) else self._line_h)
        return max(self._line_h, total)

    def _content_height(self):
        """代码内容在**当前宽度**下占用的像素高度（算上自动换行）。

        取「字体度量估算」和 Tk 的 -ypixels 里大的那个：
          - -ypixels 最准（连中文行更高都算进去了），但视野外的行还没被排版，
            长代码时给的值偏小（块越矮算得越小，反馈环），不能单独信它；
          - 估算值不随控件高度变化，偏大时只是多留一点空白，绝不会裁掉代码。
        """
        est = self._estimate_height()
        try:
            if self.inner.winfo_ismapped() and self.inner.winfo_width() > 20:
                n = self.inner.count("1.0", "end-1c", "ypixels")
                n = n[0] if isinstance(n, (tuple, list)) else n
                if n:
                    est = max(est, int(n))
        except Exception:
            pass
        return est

    def fit(self):
        """按代码实际占用的高度自适应，超过 MAX_H 就定在 MAX_H（改为内部滚动）。"""
        content = self._content_height()
        # 该不该出滚动条只看「内容高过上限没有」：Tk 的 yview 比例在内容正好装下时
        # 也可能给个 0.83 之类的值（末尾空行的算法），拿它判断会误判、误显示。
        self._scrolls = content > self.MAX_H + 1
        self._body_h = min(content, self.MAX_H) + 2 * self.PADY
        want = self.HEADER_H + 1 + self._body_h
        try:
            if abs(int(self.cget("height")) - want) > 1:
                self.configure(height=want)
        except Exception:
            pass
        self._sync_scrollbar()

    def _on_yscroll(self, first, last):
        """Tk 通报 yview 变化：按「内容有没有超过 MAX_H」决定右侧细滚动条显不显示。

        不能直接把滚动条挂成 yscrollcommand：Tk 在内容正好装下时也会给个
        0.83 之类的比例（末尾空行的算法），只按比例判断会误显示滚动条。
        """
        try:
            self._sb_view = (float(first), float(last))
        except Exception:
            self._sb_view = (0.0, 1.0)
        if not getattr(self, "_scrolls", False):
            if self.sb.winfo_manager():
                self.sb.place_forget()
            return
        self.sb.sync(*self._sb_view)

    def _sync_scrollbar(self):
        """让滚动条按当前状态刷新一次（内容超出才出现）。"""
        if not getattr(self, "_scrolls", False):
            try:
                if self.sb.winfo_manager():
                    self.sb.place_forget()
            except Exception:
                pass
            return
        try:
            self.sb.sync(*self.inner.yview())
        except Exception:
            pass


class NotesApp:
    ROW_H = 66

    def __init__(self, root):
        self.root = root
        self.data = load_data()
        # 兼容旧数据：移除“我的 Mac”智能文件夹
        self.data["folders"] = [f for f in self.data["folders"] if f["id"] != "f_mac"]
        if self.data.get("current_folder") == "f_mac":
            self.data["current_folder"] = "f_all"
        # 兼容旧数据：确保“星标笔记”智能文件夹存在
        if not any(f["id"] == "f_star" for f in self.data["folders"]):
            self.data["folders"].insert(1, {"id": "f_star", "name": "星标笔记", "smart": True})
            save_data(self.data)
        # 兼容旧数据：两个内置智能文件夹的显示名统一成“笔记”（老库里叫“全部备忘录/星标备忘录”）
        _renamed = False
        for f in self.data["folders"]:
            if f.get("smart") and f["id"] in ("f_all", "f_star"):
                want = "全部笔记" if f["id"] == "f_all" else "星标笔记"
                if f.get("name") != want:
                    f["name"] = want
                    _renamed = True
        if _renamed:
            save_data(self.data)
        # 兼容旧数据：应用内置的示例笔记随应用改名（标题整串等于示例标题才认，
        # 用户自己改过标题的笔记不动）；顺带把正文里的“备忘录”措辞统一成“笔记”
        if _fix_sample_note(self.data):
            save_data(self.data)
        # 配色：优先 WorkBuddy 皮肤（PALETTE），解析失败时 PALETTE 即内置浅灰 LIGHT
        self.pal = PALETTE
        self._checks = []
        self._codes = []          # 正文里的代码块（换笔记时连同 Text 里的窗口一起销毁）
        self._code_family = None
        self._code_btn = None
        self._active_title = self._active_preview = self._active_date = None
        self.fmt_buttons = []
        self._star_buttons = {}
        self._tool_seps = []
        self._rows = {}
        self._folder_rows = {}
        self._folder_icons = {}
        self._folder_count_labels = {}
        self._folder_name_labels = {}
        self._drag = None
        self._drag_win = None
        self._drag_target = None
        # 多选状态：文件夹 / 笔记（Shift 区间、Ctrl 切换）
        self._folder_sel = set()
        self._folder_anchor = None
        self._note_sel = set()
        self._note_anchor = None
        # 全局设置（正文字号等），持久化在 notes_data.json 的 settings 里
        self.data.setdefault("settings", {})
        self.body_size = self._read_body_size()
        # 工具栏横向滑动状态（窗口变窄时按钮展示不全）
        self._tool_x = 0
        self._marquee = {}
        self._font_cache = {}      # 测量用字体（绑定本实例的 Tk 解释器）
        # 布局事务：折叠/展开这类「一改牵动全身」的操作期间抑制各处 Configure 联动，
        # 先一次性算完几何再统一刷新，避免中间态来回重排、重绘出可见的顿挫
        self._layout_txn = False
        self._vscroll_place_fn = None    # 编辑区滚动条重新定位（事务结束后统一调一次）
        # 延时任务登记表：主窗口销毁时统一取消，否则 Tk 会打印 invalid command name
        self._alive = True
        self._after_jobs = set()
        self._build_ui()
        self.apply_palette()
        if not self.data.get("current_note"):
            first = self.visible_notes()
            if first:
                self.data["current_note"] = first[0]["id"]
        self.refresh_all()
        # 主窗口销毁时把挂起的延时任务全部取消
        try:
            self.root.bind("<Destroy>", self._on_root_destroy, add="+")
        except Exception:
            pass

    def _on_root_destroy(self, e):
        if getattr(e, "widget", None) is not self.root:
            return
        self._alive = False
        try:
            self._stop_all_marquee()
        except Exception:
            pass
        # 关闭数据库连接（否则进程退出时可能留下 -wal/-journal 文件）
        try:
            store().close()
        except Exception:
            pass
        for job in list(self._after_jobs):
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
        self._after_jobs.clear()

    # ---------- 延时任务（可集中取消） ----------
    def _after(self, ms, fn):
        """登记一个延时任务，返回 job id；窗口销毁后不再执行。"""
        job = [None]

        def run():
            self._after_jobs.discard(job[0])
            if self._alive:
                try:
                    fn()
                except tk.TclError:
                    pass

        job[0] = self.root.after(ms, run)
        self._after_jobs.add(job[0])
        return job[0]

    def _after_idle(self, fn):
        return self._after(0, fn)

    def _cancel_after(self, job):
        self._after_jobs.discard(job)
        try:
            self.root.after_cancel(job)
        except Exception:
            pass

    # ---------- 数据 ----------
    def folder_notes(self, fid):
        if fid == "f_all":
            return self.data["notes"]
        if fid == "f_star":
            return [n for n in self.data["notes"] if n.get("starred", False)]
        return [n for n in self.data["notes"] if n["folder"] == fid]

    def visible_notes(self):
        q = (self.search_var.get() if hasattr(self, "search_var") else "").strip().lower()
        if q:
            # 搜索交给 SQLite：note 表里有 plain 列，直接在 SQL 里过滤 + 排序，
            # 不再把每条笔记的 content 逐字符拼成纯文本再比对（原来每敲一个字都全表扫）。
            idx = {n["id"]: n for n in self.data["notes"]}
            ids = store().search_ids(self.data["current_folder"], q)
            return [idx[i] for i in ids if i in idx]
        lst = self.folder_notes(self.data["current_folder"])
        return sorted(lst, key=lambda n: (0 if n.get("pinned") else 1,
                                          0 if n.get("starred", False) else 1,
                                          -n["updated"]))

    def plain(self, n):
        return plain_of(n)

    def _lines(self, n):
        """把笔记内容按换行拆成多行（待办框记作一个空格，代码块按自己的换行拆）。"""
        lines = [""]
        for it in n["content"]:
            if it[0] == "t":
                for ch in it[1]:
                    if ch == "\n":
                        lines.append("")
                    else:
                        lines[-1] += ch
            elif it[0] == "w":
                lines[-1] += " "
            elif it[0] == "code":
                for ch in (it[2] if len(it) > 2 else ""):
                    if ch == "\n":
                        lines.append("")
                    else:
                        lines[-1] += ch
        return lines

    def title_of(self, n):
        lines = self._lines(n)
        t = (lines[0] if lines else "").strip()
        return t or "新建笔记"

    def preview_of(self, n):
        lines = self._lines(n)
        return (lines[1].strip() if len(lines) > 1 else "") or ""

    def fmt_date(self, ts):
        import time
        d = time.localtime(ts / 1000)
        n = time.localtime()
        if d.tm_yday == n.tm_yday and d.tm_year == n.tm_year:
            return time.strftime("%H:%M", d)
        if (n.tm_yday - d.tm_yday) == 1 and d.tm_year == n.tm_year:
            return "昨天"
        if d.tm_year == n.tm_year:
            return "%d月%d日" % (d.tm_mon, d.tm_mday)
        return "%d/%d/%d" % (d.tm_year, d.tm_mon, d.tm_mday)

    def trunc(self, s, n):
        s = (s or "").strip()
        return s if len(s) <= n else s[:n] + "…"

    def _date_text(self, n):
        """列表右侧时间：置顶显示「置顶」，否则按时间格式。"""
        return "置顶" if n.get("pinned") else self.fmt_date(n["updated"])

    # ---------- UI ----------
    def _build_ui(self):
        self.root.title(APP_NAME)
        self.root.geometry("1080x700")
        self.root.minsize(820, 540)
        try:
            self.root.state("zoomed")
        except Exception:
            pass
        # 窗口/任务栏/弹窗图标：笔记本+铅笔（替换 Tk 默认的羽毛）
        self._set_app_icon()

        # 顶部栏
        self.top = tk.Frame(self.root, height=46, bg=self.pal["top"])
        self.top.pack(fill=tk.X)
        # 搜索框：自绘圆角底 + 放大镜图标，宽度与下方「文件夹栏 + 笔记列表」两栏对齐
        self.search_canvas = tk.Canvas(self.top, width=SEARCH_W, height=32, bd=0,
                                       highlightthickness=0, bg=self.pal["top"])
        self.search_canvas.pack(side=tk.LEFT, padx=(COLLAPSE_W, 0), pady=7)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *a: self.refresh_notes())
        self.search_ent = tk.Entry(self.search_canvas, textvariable=self.search_var, bd=0,
                                   bg=self.pal["list"], fg=self.pal["text"],
                                   selectbackground=self.pal.get("sel", "#cfe0fb"),
                                   selectforeground=_sel_fg(self.pal),
                                   font=(FONT[0], 12), insertbackground=self.pal["accent"])
        # 占位文字：真实 Label（为什么不用画布文字图元，见 _style_search_box 注释）
        self.search_ph_lbl = tk.Label(self.search_canvas, text="搜索",
                                      bg=self.pal["list"], fg=self.pal["soft"],
                                      font=(FONT[0], 12), anchor="w")
        self.search_ph_lbl.bind("<Button-1>",
                                lambda e: self.search_ent.focus_set())
        self._style_search_box()
        # 点到图标/圆角区域也能把焦点给输入框
        self.search_canvas.bind("<Button-1>", lambda e: (self.search_ent.focus_set(),
                                                         self._update_search_ph()))
        self.search_ent.bind("<FocusIn>", lambda e: self._update_search_ph())
        self.search_ent.bind("<FocusOut>", lambda e: self._update_search_ph())
        self.search_var.trace_add("write", lambda *a: self._update_search_ph())
        # 主操作按钮：实心强调色圆角胶囊，hover/press 向深绿递进（不再闪系统灰）
        self.new_btn = RoundButton(self.top, self.pal, "新建笔记",
                                   command=self.new_note, font=(FONT[0], 12, "bold"),
                                   primary=True, padx=14, pady=7)
        self.new_btn.pack(side=tk.RIGHT, padx=10)
        self.new_btn.bind_events()
        # 设置按钮（正文字号等全局项），排在新建按钮左侧
        self.settings_btn = RoundButton(self.top, self.pal, "设置",
                                        command=self.open_settings, font=(FONT[0], 12))
        self.settings_btn.pack(side=tk.RIGHT, padx=(0, 2))
        self.settings_btn.bind_events()

        # 三栏
        body = tk.Frame(self.root, bg=self.pal["top"])
        body.pack(fill=tk.BOTH, expand=True)

        # 最左缘：列表折叠条（点击收起/展开「文件夹 + 笔记列表」，编辑区随之撑开）
        self.collapse_bar = tk.Frame(body, width=COLLAPSE_W, bg=self.pal["side"])
        self.collapse_bar.pack(side=tk.LEFT, fill=tk.Y)
        self.collapse_bar.pack_propagate(False)
        self.collapse_btn = CollapseButton(self.collapse_bar, self.pal,
                                           command=self.toggle_lists, base=self.pal["side"])
        self.collapse_btn.place(relx=0.5, rely=0.5, anchor="center")
        self._lists_collapsed = False

        # 左：文件夹
        self.side = tk.Frame(body, width=SIDEBAR_W, bg=self.pal["side"])
        self.side.pack(side=tk.LEFT, fill=tk.Y)
        self.side.pack_propagate(False)
        # 左栏标题行：左边「文件夹」，右边显示文件夹总数
        head = tk.Frame(self.side, bg=self.pal["side"])
        head.pack(fill=tk.X, padx=16, pady=(7, 3))
        self.folder_head_label = tk.Label(head, text="文件夹", anchor="w", bg=self.pal["side"],
                                          fg=self.pal["soft"], font=(FONT[0], 11, "bold"))
        self.folder_head_label.pack(side=tk.LEFT)
        self.folder_total_label = tk.Label(head, text="", anchor="e", bg=self.pal["side"],
                                           fg=self.pal["soft"], font=(FONT[0], 11))
        self.folder_total_label.pack(side=tk.RIGHT)
        self.folder_panel = ScrollPanel(self.side, self.pal["side"], self.pal)
        self.folder_panel.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 6))
        fbtn = tk.Frame(self.side, bg=self.pal["side"])
        fbtn.pack(fill=tk.X, pady=(0, 4))
        # 侧栏动作按钮：白底 + 描边圆角胶囊（一眼看出是可点的按钮），
        # 悬停时描边和文字向强调色靠拢
        nf = RoundButton(fbtn, self.pal, "＋ 新建文件夹", command=self.new_folder,
                         base=self.pal["editor"], fg=self.pal["accent"],
                         outline=self.pal["line"], stretch=True)
        nf.pack(fill=tk.X, padx=12, pady=3)
        nf.bind_events()
        df = RoundButton(fbtn, self.pal, "删除文件夹", command=self.del_folder,
                         base=self.pal["editor"],
                         outline=self.pal["line"], stretch=True)
        df.pack(fill=tk.X, padx=12, pady=3)
        df.bind_events()
        self.folder_action_buttons = [nf, df]

        # 中：笔记列表（列间硬竖线分隔，直角硬朗）
        self._vsep1 = tk.Frame(body, width=1, bg=self.pal["line"])
        self._vsep1.pack(side=tk.LEFT, fill=tk.Y)
        self.list_frame = tk.Frame(body, width=LIST_W)
        self.list_frame.pack(side=tk.LEFT, fill=tk.Y)
        self.list_frame.pack_propagate(False)
        # 列宽变化时让顶部搜索框跟着对齐（宽度 = 文件夹列 + 分隔线 + 列表列）
        self.list_frame.bind("<Configure>", lambda e: self._sync_search_width(), add="+")
        self.side.bind("<Configure>", lambda e: self._sync_search_width(), add="+")
        self.mid_head = tk.Label(self.list_frame, anchor="w", font=(FONT[0], 12, "bold"))
        self.mid_head.pack(fill=tk.X, padx=16, pady=(7, 4))
        self.note_panel = ScrollPanel(self.list_frame, self.pal["list"], self.pal)
        self.note_panel.pack(fill=tk.BOTH, expand=True)

        # 右：编辑区（列间硬竖线分隔）
        self._vsep2 = tk.Frame(body, width=1, bg=self.pal["line"])
        self._vsep2.pack(side=tk.LEFT, fill=tk.Y)
        self.editor_frame = tk.Frame(body)
        self.editor_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        # 工具栏：底色跟随皮肤侧栏色，与白色编辑区明确区分（bar 存成 self 以便换肤时重配色）
        bar = tk.Frame(self.editor_frame, height=TOOLBAR_H, bg=self.pal["side"])
        bar.pack(fill=tk.X)
        self.toolbar = bar
        self._build_toolbar(bar)
        # 工具栏与编辑区之间的 1px 分隔线（与三栏竖线同色）
        self._tool_bottom = tk.Frame(self.editor_frame, height=1, bg=self.pal["line"])
        self._tool_bottom.pack(fill=tk.X)

        self.text = tk.Text(self.editor_frame, bd=0, padx=TEXT_PADX, pady=22, wrap=tk.WORD,
                            undo=True, font=(FONT[0], self.body_size), spacing1=4, spacing3=4,
                            highlightthickness=0, insertbackground=self.pal["accent"],
                            selectbackground=self.pal.get("sel", "#cfe0fb"),
                            inactiveselectbackground=self.pal.get("sel2", "#e0e9f7"),
                            selectforeground=_sel_fg(self.pal), exportselection=False)
        self.text.pack(fill=tk.BOTH, expand=True)
        self._config_tags()
        self.text.bind("<<Modified>>", self.on_edit)
        self.text.config(state=tk.DISABLED)
        self.text.bind("<Return>", self._editor_return)
        # 编辑区宽度变化 → 代码块重新对齐宽度并重算高度（否则换行行数会算错）
        self.text.bind("<Configure>", lambda e: self._sync_code_width(), add="+")
        sy = tk.Scrollbar(self.editor_frame, command=self.text.yview)
        self._place_vscroll(sy)
        self._vscroll = sy
        # 正文滚动时同步滚动条 + 浮动导航（回顶部/回底部）的显隐与可用端
        self.text.config(yscrollcommand=self._on_text_scroll)
        # 右侧居中的浮动导航：浮在正文之上，只在这篇能滚时才出现
        self.jump_nav = ScrollJumpNav(self.editor_frame, self.pal,
                                      command=self._jump_scroll, base=self.pal["editor"])
        self._jump_placed = False
        self._jump_job = None
        self._sync_jump_nav()

    def _on_text_scroll(self, first, last):
        """Text 的 yscrollcommand 钩子：滚动条照旧，顺带更新浮动导航。"""
        try:
            self._vscroll.set(first, last)
        except Exception:
            pass
        self._sync_jump_nav(float(first), float(last))

    def _sync_jump_nav(self, first=None, last=None):
        """正文能滚才显示右侧浮动导航；已经到顶/到底的那一头变淡、点了不动。"""
        if not hasattr(self, "jump_nav"):
            return
        if self._layout_txn:      # 事务期间不联动，结束后统一刷
            return
        try:
            if first is None:
                first, last = self.text.yview()
            overflow = (float(last) - float(first)) < 0.999
        except Exception:
            return
        if not overflow:
            if self._jump_placed:
                self.jump_nav.place_forget()
                self._jump_placed = False
            return
        if not self._jump_placed:
            # 右侧垂直居中，落在竖向滚动条左边一点，别压住滚动条
            self.jump_nav.place(relx=1.0, rely=0.5, anchor="e",
                                x=-(int(self._vscroll.winfo_reqwidth()) + JUMP_GAP))
            # 浮在正文之上（Canvas 的 lift 被重定向到 tag_raise，这里要走窗口层级的 raise）
            tk.Misc.lift(self.jump_nav)
            self._jump_placed = True
        self.jump_nav.set_ends(float(first) > 0.001, float(last) < 0.999)

    def _jump_scroll(self, part):
        """平滑滚到顶部/底部（约 0.15s 缓出），比瞬间跳过去更容易看清落点。"""
        try:
            first, _last = self.text.yview()
            first = float(first)
        except Exception:
            return
        target = 0.0 if part == "top" else 1.0
        if abs(target - first) < 0.002:
            return
        if self._jump_job is not None:       # 连点：丢弃上一段动画
            self._cancel_after(self._jump_job)
            self._jump_job = None
        steps = 9
        seq = []
        for i in range(1, steps + 1):
            t = i / float(steps)
            ease = 1 - (1 - t) ** 3           # 缓出：起步快、落点稳
            seq.append(first + (target - first) * ease)
        seq[-1] = target

        def step(i=[0]):
            self._jump_job = None
            if i[0] >= len(seq):
                return
            try:
                self.text.yview_moveto(seq[i[0]])
            except Exception:
                return
            i[0] += 1
            if i[0] < len(seq):
                self._jump_job = self._after(16, step)

        step()

    def _place_vscroll(self, sy):
        """编辑区竖向滚动条从工具栏下缘开始（原先是整列高度，会压住工具栏右端的滑动箭头）。"""
        def place(_e=None):
            if self._layout_txn:   # 事务期间跳过，事务结束后统一调一次
                return
            try:
                h = self.editor_frame.winfo_height() - (TOOLBAR_H + 1)
            except Exception:
                return
            if h > 40:
                sy.place(relx=1.0, y=TOOLBAR_H + 1, width=17, height=h, anchor="ne")
        self._vscroll_place_fn = place
        self.editor_frame.bind("<Configure>", place, add="+")
        place()

    # ---------- 圆角搜索框（Canvas 自绘：圆角底 + 放大镜 + 分隔竖线） ----------
    def _style_search_box(self):
        """画搜索框的圆角底、放大镜图标与分隔竖线，并把输入框/占位文字摆进画布。

        换肤时也会调这里（apply_palette），所以所有颜色都从 self.pal 现取。

        输入框与占位文字都是「真实控件 place 在画布上」而不用 create_window 嵌控件：
        canvas 的 window 图元在 delete("all") 重建后，嵌入控件会被提到最顶层，
        盖住后创建的占位文字图元（本函数启动时要跑两次，改列宽/换肤还会再跑 →
        占位文字时有时无、tag_raise 也救不回来）。place 的真实控件之间用
        lift 固定层级，行为确定。
        """
        c = self.search_canvas
        p = self.pal
        W, H = int(float(c["width"])), int(float(c["height"]))
        c.delete("all")
        # 圆角底：fill 与 Entry 同色 → 输入框与底无缝；描边用分隔线色
        self._search_bg_item = c.create_polygon(
            _round_rect_points(1, 1, W - 1, H - 1, 9, 6),
            fill=p["list"], outline=p["line"], width=1)
        # 放大镜：圆环 + 右下手柄（Tk 不能用 emoji 字符，直接画）
        icx, icy, r = 19, 14, 5.5
        c.create_oval(icx - r, icy - r, icx + r, icy + r,
                      outline=p["soft"], width=2)
        c.create_line(icx + r * 0.72, icy + r * 0.72, icx + r + 4.0, icy + r + 4.0,
                      fill=p["soft"], width=2, capstyle=tk.ROUND)
        # 图标与输入区之间的分隔竖线：把要输入的关键字和放大镜隔开
        c.create_line(34, 8, 34, H - 8, fill=p["line"], width=1)
        # 输入框摆在分隔线右侧（place 按像素定位、垂直居中，右侧留白给圆角）
        self.search_ent.place(in_=c, x=42, rely=0.5, anchor="w", width=W - 56)
        # 占位文字跟随换色；可见性（place/place_forget）交给 _update_search_ph
        self.search_ph_lbl.configure(bg=p["list"], fg=p["soft"])
        self._update_search_ph()

    def _resize_search_box(self):
        """只按新宽度更新圆角底与输入框：不 delete/重建图元，避免搜索框重绘时闪一下。

        与 _style_search_box 的区别：那个负责首次绘制和换肤（颜色变了必须重画），
        这个只处理列宽变化（形状和配色都不变，改坐标就够）。
        """
        c = self.search_canvas
        W = int(float(c["width"]))
        H = int(float(c["height"]))
        item = getattr(self, "_search_bg_item", None)
        if item:
            c.coords(item, *_round_rect_points(1, 1, W - 1, H - 1, 9, 6))
        self.search_ent.place_configure(x=42, rely=0.5, anchor="w", width=W - 56)
        self._update_search_ph()

    def _update_search_ph(self):
        """占位文字可见性：无内容且未聚焦时显示，否则隐藏。"""
        try:
            focused = (self.root.focus_get() is self.search_ent)
        except Exception:
            focused = False
        show = (not self.search_var.get()) and not focused
        try:
            if show:
                self.search_ph_lbl.place(in_=self.search_canvas, x=43,
                                         rely=0.5, anchor="w")
                self.search_ph_lbl.lift()   # 压在输入框上层，真实控件叠放稳定
            else:
                self.search_ph_lbl.place_forget()
        except Exception:
            pass

    def _sync_search_width(self):
        """搜索框宽度对齐左侧两栏（文件夹列 + 分隔线 + 笔记列表列）。

        展开时宽度取两栏实际宽度之和；折叠时两栏被 pack_forget，退化为
        单列宽度（LIST_W），避免搜索框缩成一条线。
        """
        if self._layout_txn:      # 事务期间不联动，结束后由 _end_layout_txn 统一刷
            return
        try:
            if self._lists_collapsed:
                w = LIST_W
            else:
                w = self._widget_width(self.side) + self._widget_width(self._vsep1) \
                    + self._widget_width(self.list_frame)
            if w > 50 and abs(w - self.search_canvas.winfo_width()) > 1:
                self.search_canvas.configure(width=w)
                # 只改宽度：复用已有图元移动/改宽即可，重建图元会看到搜索框闪一下
                self._resize_search_box()
        except Exception:
            pass

    @staticmethod
    def _widget_width(widget):
        """取控件实际宽度，尚未布局完成时退回请求宽度。"""
        w = widget.winfo_width()
        return w if w > 1 else widget.winfo_reqwidth()

    # ---------- 列表折叠 / 展开 ----------
    def toggle_lists(self):
        """收起/展开「文件夹栏 + 笔记列表」两栏，编辑区随之向左撑开或还原。

        一次折叠会牵动整行：编辑区变宽 → 正文重新换行、工具栏重算箭头、搜索框跟着
        缩。这些联动若各自在中间的半成品宽高上跑一遍，就会看到「停一下再跳」。
        所以这里开一个布局事务：事务期间抑制各处 Configure 联动，先让 Tk 把几何
        一次算完，再统一刷新，最后一次性画出最终状态 —— 全程只重排、只重绘一次。
        """
        self._lists_collapsed = not self._lists_collapsed
        self._layout_txn = True
        try:
            if self._lists_collapsed:
                self.side.pack_forget()
                self._vsep1.pack_forget()
                self.list_frame.pack_forget()
            else:
                # 按原顺序插回 _vsep2 之前：side | 竖线 | 列表
                self.side.pack(side=tk.LEFT, fill=tk.Y, before=self._vsep2)
                self._vsep1.pack(side=tk.LEFT, fill=tk.Y, before=self._vsep2)
                self.list_frame.pack(side=tk.LEFT, fill=tk.Y, before=self._vsep2)
            # 收起后箭头朝右（点它展开），展开时朝左（点它收起）
            self.collapse_btn.set_dir(1 if self._lists_collapsed else -1)
            # 事务内：只结算几何，Configure 联动被抑制，不会产生中间态重排
            self.root.update_idletasks()
        finally:
            self._layout_txn = False
        # 统一刷新：搜索框宽度 / 工具栏箭头显隐 / 编辑区滚动条位置 / 浮动回顶回底按钮
        self._sync_search_width()
        self._sync_toolbar()
        if self._vscroll_place_fn:
            self._vscroll_place_fn()
        self._sync_jump_nav()
        # 一次性把最终状态画出来，点击返回时画面已经是收好的样子
        self.root.update_idletasks()

    # ---------- 应用图标 ----------
    def _set_app_icon(self):
        """把窗口/任务栏/弹窗图标换成笔记本+铅笔 PNG（替换 Tk 默认羽毛）。"""
        path = os.path.join(BASE, "app_icon.png")
        if not os.path.exists(path):
            return
        img = None
        try:
            # PIL 可用时先缩到 64x64，小尺寸下比让系统缩 512x512 更清晰
            from PIL import Image, ImageTk
            resample = getattr(Image, "LANCZOS", getattr(Image, "BICUBIC", None))
            im = Image.open(path).convert("RGBA").resize((64, 64), resample)
            img = ImageTk.PhotoImage(im, master=self.root)
        except Exception:
            try:
                img = tk.PhotoImage(file=path)
            except Exception:
                img = None
        if img is not None:
            self._app_icon = img          # 持引用：PhotoImage 被 GC 图标会消失
            try:
                self.root.iconphoto(True, img)   # True = 以后新建的弹窗都继承
            except Exception:
                pass

    # ---------- 工具栏（内容超出时可用两端箭头横向滑动） ----------
    def _tool_bg(self):
        """工具栏底色：跟随皮肤侧栏色（与白色编辑区区分），换肤自动同步。"""
        return self.pal.get("side") or self.pal.get("editor", "#ffffff")

    def _tool_line(self):
        """工具栏内分隔线色：由工具栏底色向次级文字色混合，保证在底色上仍看得见。"""
        return _mix_hex(self._tool_bg(), self.pal.get("soft", "#76767e"), TOOL_LINE_MIX)

    def _build_toolbar(self, bar):
        """把工具栏放进可横向滚动的 Canvas 里，窗口变窄时用两端箭头滑动查看。

        做法：Canvas 里塞一个内部 Frame 装所有按钮（create_window），
        两端各一个自绘圆形箭头；只有内容真的溢出时才出现箭头，滑到端点对应箭头自动隐藏。
        """
        p = self.pal
        tbg = self._tool_bg()
        self.tool_canvas = tk.Canvas(bar, height=TOOLBAR_H, bd=0, highlightthickness=0,
                                     bg=tbg)
        # 先 pack 画布并撑满，箭头按需插到它左右两侧
        self.tool_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tool_inner = tk.Frame(self.tool_canvas, bg=tbg)
        self.tool_win = self.tool_canvas.create_window((0, 0), window=self.tool_inner,
                                                       anchor="nw")
        # 箭头常态色 = 工具栏底色，否则常态会露出一个白圆
        self.tool_left = ArrowButton(bar, p, "left", command=lambda: self._scroll_toolbar(-1),
                                     base=tbg)
        self.tool_right = ArrowButton(bar, p, "right", command=lambda: self._scroll_toolbar(1),
                                      base=tbg)
        self.tool_left.bind_events()
        self.tool_right.bind_events()

        # 所有格式按钮都挂到 tool_inner（原来是直接挂 bar）
        self._mk_fmt(self.tool_inner, "标题", lambda: self.apply_line_tag("title"))
        self._mk_fmt(self.tool_inner, "正文", lambda: self.apply_line_tag("body"))
        self._sep(self.tool_inner)
        self._mk_fmt(self.tool_inner, "B", lambda: self.toggle_sel_tag("bold"), bold=True)
        self._mk_fmt(self.tool_inner, "I", lambda: self.toggle_sel_tag("italic"), italic=True)
        self._mk_fmt(self.tool_inner, "U", lambda: self.toggle_sel_tag("underline"), underline=True)
        # 下划线颜色紧跟下划线按钮
        self._mk_underline_color_ui(self.tool_inner)
        self._mk_fmt(self.tool_inner, "S", lambda: self.toggle_sel_tag("strike"), strike=True)
        self._sep(self.tool_inner)
        self._mk_fmt(self.tool_inner, "• 列表", self.insert_bullet)
        self._mk_fmt(self.tool_inner, "1. 序号", self.insert_numbered)
        self._mk_fmt(self.tool_inner, TRI_MARK.strip() + " 三角", self.insert_triangle)
        # 待办放在三角之后
        self._mk_fmt(self.tool_inner, "☑ 待办", self.insert_check)
        # 代码块紧跟在待办之后（点开选语言）
        self._mk_code_ui(self.tool_inner)
        self._sep(self.tool_inner)
        self._mk_font_ui(self.tool_inner)
        self._mk_color_ui(self.tool_inner)
        self._sep(self.tool_inner)
        self._mk_fmt(self.tool_inner, "还原默认", lambda: self.reset_format())
        self._mk_fmt(self.tool_inner, "置顶", lambda: self.toggle_pin())
        self._mk_fmt(self.tool_inner, "星标", lambda: self.toggle_star())
        self._mk_fmt(self.tool_inner, "删除", lambda: self.delete_note())

        # 内容宽 / 可视宽变化时重算溢出与箭头显隐
        self.tool_inner.bind("<Configure>", lambda e: self._sync_toolbar())
        self.tool_canvas.bind("<Configure>", lambda e: self._sync_toolbar())
        # 滚轮横向滑动（工具栏上滚轮 = 左右移动）
        for w in (self.tool_canvas, self.tool_inner):
            w.bind("<MouseWheel>", self._toolbar_wheel)

    def _toolbar_metrics(self):
        """返回 (内容总宽, 可视宽, 当前偏移像素)。"""
        total = max(1, self.tool_inner.winfo_reqwidth())
        view = max(1, self.tool_canvas.winfo_width())
        return total, view, self._tool_x

    def _scroll_toolbar(self, direction, ratio=0.35):
        total, view, cur = self._toolbar_metrics()
        step = max(60, int(view * ratio))
        self._tool_x = min(max(0, total - view), max(0, cur + direction * step))
        self.tool_canvas.xview_moveto(self._tool_x / float(total))
        self._sync_toolbar()

    def _toolbar_wheel(self, e):
        self._scroll_toolbar(-1 if e.delta > 0 else 1, ratio=0.20)
        return "break"

    def _sync_toolbar(self):
        """内容溢出才显示箭头；已滑到最左/最右时隐藏对应箭头。"""
        if self._layout_txn:      # 事务期间不联动，结束后由 _end_layout_txn 统一刷
            return
        try:
            total, view, cur = self._toolbar_metrics()
        except Exception:
            return
        overflow = total > view + 2
        # 窗口变宽后可能不再需要滚动，把偏移夹回合法范围
        if not overflow:
            self._tool_x = 0
            self.tool_canvas.xview_moveto(0)
            cur = 0
        else:
            self._tool_x = min(self._tool_x, total - view)
            cur = self._tool_x
        self.tool_canvas.configure(scrollregion=(0, 0, total, TOOLBAR_H))
        self._show_arrow(self.tool_left, overflow and cur > 1, side=tk.LEFT)
        self._show_arrow(self.tool_right, overflow and cur < total - view - 1, side=tk.RIGHT)

    def _show_arrow(self, arrow, show, side=tk.LEFT):
        try:
            mapped = bool(arrow.winfo_ismapped())
        except Exception:
            mapped = False
        if show and not mapped:
            # before=canvas：插到打包顺序里画布前面，先把自己的宽度占住，
            # 否则 expand 的画布会把箭头挤成几像素宽
            arrow.pack(side=side, fill=tk.Y, before=self.tool_canvas)
        elif not show and mapped:
            arrow.pack_forget()

    def apply_palette(self):
        p = self.pal
        # 纯色背景（已移除渐变）：根/顶栏/侧栏用浅色实底，列表与编辑区保持纯色确保文字可读
        self.root.configure(bg=p["top"])
        self.top.configure(bg=p["top"])
        self.side.configure(bg=p["side"])
        self.list_frame.configure(bg=p["list"])
        self.editor_frame.configure(bg=p["editor"])
        # 三栏之间的硬竖线分隔
        self._vsep1.configure(bg=p["line"])
        self._vsep2.configure(bg=p["line"])
        # 侧栏/列表面板：纯色实底（无渐变）
        self.folder_panel.configure(bg=p["side"])
        self.folder_panel.canvas.configure(bg=p["side"])
        self.folder_panel.inner.configure(bg=p["side"])
        self.folder_panel.sb.apply_theme(p, p["side"])
        self.note_panel.canvas.configure(bg=p["list"])
        self.note_panel.inner.configure(bg=p["list"])
        self.note_panel.sb.apply_theme(p, p["list"])
        # 左栏标题与文件夹总数
        self.folder_head_label.configure(bg=p["side"], fg=p["soft"])
        self.folder_total_label.configure(bg=p["side"], fg=p["soft"])
        # 顶部搜索框：Canvas 自绘圆角 + 放大镜，整体重画一遍
        self.search_canvas.configure(bg=p["top"])
        self.search_ent.configure(bg=p["list"], fg=p["text"], insertbackground=p["accent"])
        self._style_search_box()
        # 列表折叠条与按钮
        self.collapse_bar.configure(bg=p["side"])
        self.collapse_btn.apply_theme(p, base=p["side"])
        # 编辑区右侧浮动导航（回顶部 / 回底部）
        self.jump_nav.apply_theme(p, base=p["editor"])
        self.new_btn.apply_theme(p)
        self.settings_btn.apply_theme(p)
        # 工具栏：外层条 / 滚动画布 / 内层 / 两端箭头 / 内部分隔线 / 底部分割线 统一跟随皮肤
        tbg = self._tool_bg()
        self.toolbar.configure(bg=tbg)
        self._tool_bottom.configure(bg=p["line"])
        self.tool_canvas.configure(bg=tbg)
        self.tool_inner.configure(bg=tbg)
        self.tool_left.apply_theme(p, base=tbg)
        self.tool_right.apply_theme(p, base=tbg)
        for s in self._tool_seps:
            try:
                s.configure(bg=self._tool_line())
            except Exception:
                pass
        self.text.configure(bg=p["editor"], fg=p["text"], insertbackground=p["accent"],
                             selectbackground=p.get("sel", "#cfe0fb"),
                             inactiveselectbackground=p.get("sel2", "#e0e9f7"),
                             selectforeground=_sel_fg(p))
        self._config_tags()
        for cb in self._checks:
            try:
                cb.config(bg=p["editor"])
                cb._draw()
            except Exception:
                pass
        for blk in self._codes:
            try:
                blk.apply_theme(p)
            except Exception:
                pass
        for b in self.fmt_buttons:
            b.apply_theme(p)
        for b in getattr(self, "folder_action_buttons", []):
            b.apply_theme(p)
        self.refresh_folders()
        self.refresh_notes()
        try:
            self._sync_toolbar()
        except Exception:
            pass

    def _config_tags(self):
        p = self.pal
        bs = self.body_size          # 全局正文字号（设置里改，重启保持）
        # 加粗 / 斜体 / 粗斜体：三个互斥标签。
        # Tk 里两个都设 font 的标签会「优先级高的整体覆盖低的」，所以粗+斜必须合成
        # 一个 bold_italic 标签，否则二者只能二选一（后建的 italic 会把 bold 顶掉）。
        self.text.tag_configure("bold", font=(FONT[0], bs, "bold"))
        self.text.tag_configure("italic", font=(FONT[0], bs, "italic"))
        self.text.tag_configure("bold_italic", font=(FONT[0], bs, "bold italic"))
        self.text.tag_configure("underline", underline=True)
        self.text.tag_configure("strike", overstrike=True)
        # 标题 / 正文 之间留出间距（spacing3 在标签行下方，spacing1 在其上方）
        self.text.tag_configure("title", font=(FONT[0], bs + 4, "bold"), spacing1=8, spacing3=8)
        self.text.tag_configure("body", font=(FONT[0], bs), spacing1=2, spacing3=2)
        self.text.tag_configure("done", overstrike=True, foreground=p["soft"])
        # 动态样式：用户自选的文字颜色 / 字体（家族+字号），存入 data["styles"] 以便重开还原
        self.data.setdefault("styles", {})
        for name, spec in self.data["styles"].items():
            kw = {}
            if "fg" in spec:
                kw["foreground"] = spec["fg"]
            if "ufg" in spec:
                # underline 与 underlinefg 必须同 tag，否则颜色不生效
                kw["underlinefg"] = spec["ufg"]
                kw["underline"] = True
            if "font" in spec:
                kw["font"] = tuple(spec["font"]) if isinstance(spec["font"], list) else spec["font"]
            try:
                self.text.tag_configure(name, **kw)
            except Exception:
                pass
        # 字体标签的优先级（Tk 里后 raise 的胜出）：body < bold < italic < bold_italic < title，
        # 让「加粗」能盖住 body 的普通字重，而 title 又不会被加粗改成正文大小。
        # f_* 动态字体标签在本函数里更晚配置，天然压在最上面（用户显式选字体优先）。
        for t in ("body", "bold", "italic", "bold_italic", "title"):
            try:
                self.text.tag_raise(t)
            except Exception:
                pass

    # ---------- 全局设置：正文字号 ----------

    def _read_body_size(self):
        try:
            sz = int(self.data.get("settings", {}).get("body_size", 12))
        except Exception:
            sz = 12
        return min(25, max(8, sz))

    def _apply_body_size(self, size):
        """套用全局正文字号：编辑器默认字体 + 正文/加粗/斜体/标题标签一起缩放。

        标签字号改了，已存在于文本里的这些标签会自动跟着变（Tk tag_configure 即时生效）。
        """
        self.body_size = min(25, max(8, int(size)))
        self.text.configure(font=(FONT[0], self.body_size))
        self._config_tags()
        # 代码块字号跟着正文缩放（比正文小 1 号），高度需按新字号重算
        for blk in self._codes:
            try:
                blk.apply_size(max(9, self.body_size - 1))
            except Exception:
                pass
        # 字号变了 → 正文高度变了，浮动导航的显隐/可用端跟着重算
        self._sync_jump_nav()

    def _save_body_size(self, size):
        self.data.setdefault("settings", {})["body_size"] = int(size)
        save_data(self.data)
        self._apply_body_size(size)

    def open_settings(self):
        """全局设置：正文字号（改后全局生效并写入 notes_data.json，重启保持）。"""
        p = self.pal
        dlg = tk.Toplevel(self.root)
        dlg.title("设置")
        dlg.transient(self.root)
        dlg.resizable(False, False)
        dlg.configure(bg=p["editor"])
        tk.Label(dlg, text="全局设置", bg=p["editor"], fg=p["text"],
                 font=(FONT[0], 13, "bold")).pack(anchor="w", padx=20, pady=(16, 2))
        tk.Label(dlg, text="正文字体大小（对所有笔记的正文、加粗、斜体、标题生效）",
                 bg=p["editor"], fg=p["soft"], font=(FONT[0], 11)).pack(anchor="w", padx=20)

        cur = tk.IntVar(value=self.body_size)

        def preview(_v=None):
            sample.configure(font=(FONT[0], int(cur.get())))

        # 字号滑杆：8–25pt 连续步进，拖动时气泡显示当前值
        # 画布本身固定 340 宽，靠 anchor="center" 整体水平居中（轨道、滑钮、
        # 右上角 pt 数值、两端 8/25 标签都跟着居中，不再缩在左侧）
        slider = FontSizeSlider(dlg, p, cur, minimum=8, maximum=25,
                                command=preview, width=340)
        slider.pack(anchor="center", pady=(12, 2))

        # 预览盒按最大字号 25pt 一次性算好宽高并锁死：
        # 文字始终单行、盒高固定，滑动字号时对话框不会变宽变高（不抖动）
        import tkinter.font as _tf
        _max_font = _tf.Font(family=FONT[0], size=25)
        _sample_text = "正文预览：笔记内容 Abc"
        _sample_w = _max_font.measure(_sample_text)
        box = tk.Frame(dlg, bg=p["hover"], bd=1, relief=tk.SOLID,
                       height=_max_font.metrics("linespace") + 16)
        box.pack(fill=tk.X, padx=20, pady=(8, 4))
        box.pack_propagate(False)
        sample = tk.Label(box, text=_sample_text, bg=p["hover"],
                          fg=p["text"], anchor="w", justify="left",
                          font=(FONT[0], self.body_size))
        sample.pack(side=tk.LEFT, padx=10)

        # ---- 数据管理：导出 / 导入 / 清空（清空是危险操作，文字用警示色）----
        tk.Frame(dlg, height=1, bg=p["line"]).pack(fill=tk.X, padx=20, pady=(16, 10))
        tk.Label(dlg, text="数据管理（数据保存在 notes_data.db）", bg=p["editor"],
                 fg=p["text"], font=(FONT[0], 12, "bold")).pack(anchor="w", padx=20)
        tk.Label(dlg, text="导出可做备份；导入支持覆盖或合并；清空会删掉全部数据。",
                 bg=p["editor"], fg=p["soft"], font=(FONT[0], 11)).pack(
                     anchor="w", padx=20, pady=(2, 6))
        row = tk.Frame(dlg, bg=p["editor"])
        row.pack(anchor="w", padx=16, pady=(0, 6))
        for label, cmd, fg in (("导出备份", self.export_data, p["accent"]),
                               ("导入数据", self.import_data, p["text"]),
                               ("清空所有数据", self.clear_all_data, "#c0392b")):
            b = RoundButton(row, p, label, command=lambda c=cmd: (dlg.destroy(), c()),
                            base=p["editor"], fg=fg, outline=p["line"])
            b.pack(side=tk.LEFT, padx=4)
            b.bind_events()

        def ok(_=None):
            self._save_body_size(int(cur.get()))
            dlg.destroy()

        def cancel(_=None):
            dlg.destroy()

        # 底部按钮行：两个圆角胶囊整体居中（原生 tk.Button 只能右对齐摆着）。
        # 按钮宽度按文字测量生成，不随字号/对话框宽度变化，永远不会被挤压
        btn_row = tk.Frame(dlg, bg=p["editor"])
        btn_row.pack(fill=tk.X, padx=16, pady=(14, 16))
        btn_mid = tk.Frame(btn_row, bg=p["editor"])
        btn_mid.pack(anchor="center")   # 无 side/fill 时 anchor=center 即水平居中
        b_ok = RoundButton(btn_mid, p, "确定", command=ok, primary=True,
                           font=(FONT[0], 12, "bold"), padx=22, pady=7)
        b_ok.pack(side=tk.LEFT)
        b_ok.bind_events()
        b_cancel = RoundButton(btn_mid, p, "取消", command=cancel,
                               base=p["editor"], fg=p["soft"], outline=p["line"],
                               padx=22, pady=7)
        b_cancel.pack(side=tk.LEFT, padx=(10, 0))
        b_cancel.bind_events()

        dlg.bind("<Escape>", cancel)
        dlg.protocol("WM_DELETE_WINDOW", cancel)
        self._center_dialog(dlg, min_w=max(380, _sample_w + 76))
        try:
            dlg.grab_set()
        except Exception:
            pass

    # ---------- 数据管理：导出 / 导入 / 清空 ----------
    def export_data(self):
        """把文件夹 + 笔记 + 设置导出成一份 JSON 备份。"""
        import datetime
        p = self.pal
        default = "%s备份_%s.json" % (APP_NAME, datetime.datetime.now().strftime("%Y%m%d-%H%M"))
        path = filedialog.asksaveasfilename(
            parent=self.root, title="导出备份", defaultextension=".json",
            initialfile=default, initialdir=os.path.expanduser("~"),
            filetypes=[("JSON 备份文件", "*.json"), ("所有文件", "*.*")])
        if not path:
            return
        snap = {
            "_app": "notes-app",
            "_version": 1,
            "_exported_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "folders": self.data["folders"],
            "notes": self.data["notes"],
            "settings": self.data.get("settings", {}),
            "styles": self.data.get("styles", {}),
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(snap, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._show_error("导出失败", "写入文件失败：\n%s" % e)
            return
        self._show_info("导出完成",
                        "已导出 %d 个文件夹、%d 条笔记\n%s" %
                        (len(self.data["folders"]), len(self.data["notes"]), path))

    def import_data(self):
        """从 JSON 备份导入：可选「覆盖现有数据」或「合并追加」。"""
        path = filedialog.askopenfilename(
            parent=self.root, title="选择备份文件", initialdir=os.path.expanduser("~"),
            filetypes=[("JSON 备份文件", "*.json"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception as e:
            self._show_error("导入失败", "文件无法解析为 JSON：\n%s" % e)
            return
        if not isinstance(d, dict) or not isinstance(d.get("folders"), list) \
                or not isinstance(d.get("notes"), list):
            self._show_error("导入失败",
                             "不是有效的笔记备份文件（缺少 folders / notes 列表）。")
            return
        mode = self._ask_import_mode(len(d["folders"]), len(d["notes"]),
                                     d.get("_exported_at") or "")
        if not mode:
            return
        new = (self._normalize_import(d) if mode == "overwrite"
               else self._merge_import(d))
        store().reset(new)
        self._apply_new_data(new)
        self._show_info("导入完成",
                        "导入方式：%s\n现有 %d 个文件夹、%d 条笔记" %
                        ("覆盖" if mode == "overwrite" else "合并",
                         len(new["folders"]), len(new["notes"])))

    def _ask_import_mode(self, nf, nn, exported_at=""):
        """导入方式选择：覆盖 / 合并 / 取消。"""
        p = self.pal
        win = self._dlg_body("导入数据")
        res = {"v": None}
        tk.Label(win, text="备份文件内容：%d 个文件夹、%d 条笔记" % (nf, nn),
                 bg=p["top"], fg=p["text"], font=(FONT[0], 12)).pack(
                     anchor="w", padx=18, pady=(16, 4))
        if exported_at:
            tk.Label(win, text="导出时间：%s" % exported_at, bg=p["top"],
                     fg=p["soft"], font=(FONT[0], 11)).pack(anchor="w", padx=18)
        tk.Label(win, text="当前数据：%d 个文件夹、%d 条笔记。请选择导入方式：" %
                 (len(self.data["folders"]), len(self.data["notes"])),
                 bg=p["top"], fg=p["soft"], font=(FONT[0], 11)).pack(
                     anchor="w", padx=18, pady=(6, 2))
        tk.Label(win, text="覆盖 = 用备份完全替换现在的数据；合并 = 保留现有内容并追加备份内容。",
                 bg=p["top"], fg=p["soft"], font=(FONT[0], 10)).pack(anchor="w", padx=18)

        def choose(v):
            res["v"] = v
            win.destroy()

        row = tk.Frame(win, bg=p["top"])
        row.pack(fill=tk.X, padx=14, pady=(12, 4))
        ow = RoundButton(row, p, "覆盖现有数据", command=lambda: choose("overwrite"),
                         base=p["editor"], fg=p["accent"], outline=p["line"])
        ow.pack(side=tk.LEFT, padx=4)
        ow.bind_events()
        mg = RoundButton(row, p, "合并追加", command=lambda: choose("merge"),
                         base=p["editor"], outline=p["line"])
        mg.pack(side=tk.LEFT, padx=4)
        mg.bind_events()
        cc = RoundButton(row, p, "取消", command=lambda: choose(None),
                         base=p["editor"], outline=p["line"])
        cc.pack(side=tk.RIGHT, padx=4)
        cc.bind_events()
        win.bind("<Escape>", lambda e: choose(None))
        win.protocol("WM_DELETE_WINDOW", lambda: choose(None))
        self._center_dialog(win, min_w=430)
        try:
            win.grab_set()
        except Exception:
            pass
        win.wait_window()
        return res["v"]

    def _normalize_import(self, d):
        """整理一份「可直接落库」的数据：补齐智能文件夹、字段与归属。"""
        folders = [dict(f) for f in d["folders"]
                   if isinstance(f, dict) and f.get("id") and f.get("name")]
        ids = {f["id"] for f in folders}
        if "f_all" not in ids:
            folders.insert(0, {"id": "f_all", "name": "全部笔记", "smart": True})
            ids.add("f_all")
        if "f_star" not in ids:
            folders.insert(1, {"id": "f_star", "name": "星标笔记", "smart": True})
            ids.add("f_star")
        if "f_work" not in ids:
            folders.append({"id": "f_work", "name": "工作"})
            ids.add("f_work")
        import time
        now = time.time() * 1000
        notes = []
        for n in d["notes"]:
            if not isinstance(n, dict) or not isinstance(n.get("content"), list):
                continue
            notes.append({
                "id": n.get("id") or uid(),
                # 备份里的文件夹若已不存在，落到「工作」，避免笔记变成孤儿
                "folder": n.get("folder") if n.get("folder") in ids else "f_work",
                "content": n["content"],
                "pinned": bool(n.get("pinned")),
                "starred": bool(n.get("starred")),
                "created": n.get("created") or now,
                "updated": n.get("updated") or now,
            })
        return {"folders": folders, "notes": notes, "current_folder": "f_all",
                "current_note": None,
                "settings": d.get("settings") or self.data.get("settings", {}),
                "styles": d.get("styles") or self.data.get("styles", {})}

    def _merge_import(self, d):
        """合并：保留现有文件夹/笔记，追加备份里没有的（id 冲突则生成新 id）。"""
        folders = [dict(f) for f in self.data["folders"]]
        by_id = {f["id"]: f for f in folders}
        by_name = {f.get("name"): f for f in folders}
        for f in d["folders"]:
            if not isinstance(f, dict) or not f.get("id"):
                continue
            if f["id"] in by_id:
                continue
            if f.get("name") in by_name:      # 同名文件夹视为同一个，不重复建
                continue
            nf = dict(f)
            nf.pop("smart", None)             # 导入的普通文件夹不保留智能标记
            folders.append(nf)
            by_id[nf["id"]] = nf
            by_name[nf.get("name")] = nf
        ids = {f["id"] for f in folders}
        if "f_work" not in ids:
            folders.append({"id": "f_work", "name": "工作"})
            ids.add("f_work")
        import time
        now = time.time() * 1000
        notes = [dict(n) for n in self.data["notes"]]
        have = {n["id"] for n in notes}
        for n in d["notes"]:
            if not isinstance(n, dict) or not isinstance(n.get("content"), list):
                continue
            nid = n.get("id") or uid()
            if nid in have:                   # id 撞了：换一个新 id，两边都保留
                nid = uid()
            notes.append({
                "id": nid,
                "folder": n.get("folder") if n.get("folder") in ids else "f_work",
                "content": n["content"],
                "pinned": bool(n.get("pinned")),
                "starred": bool(n.get("starred")),
                "created": n.get("created") or now,
                "updated": n.get("updated") or now,
            })
            have.add(nid)
        return {"folders": folders, "notes": notes,
                "current_folder": self.data.get("current_folder", "f_all"),
                "current_note": self.data.get("current_note"),
                "settings": self.data.get("settings", {}),
                "styles": self.data.get("styles", {})}

    def clear_all_data(self):
        """清空所有数据并回到初次打开的状态（4 个文件夹 + 示例笔记）。"""
        mine = [f for f in self.data["folders"] if not f.get("smart")]
        n_notes = len(self.data["notes"])
        if not self._ask_confirm(
                "清空数据",
                "确定要清空所有数据吗？\n\n"
                "将删除 %d 个自建文件夹、%d 条笔记。" % (len(mine), n_notes),
                ok_text="清空", danger=True):
            return
        if not self._ask_confirm(
                "再次确认",
                "这是最后一次确认：\n"
                "所有笔记与文件夹都会被永久删除，无法恢复，\n"
                "程序会回到初次打开时的状态（4 个文件夹）。\n\n"
                "确认清空？",
                ok_text="确认清空", danger=True):
            return
        new = seed()
        store().reset(new)
        self._apply_new_data(new)
        self._show_info("已完成", "数据已清空，界面已恢复到初始状态。")

    def _apply_new_data(self, new):
        """导入/清空后统一收尾：重置选中态、搜索词、重建界面。"""
        self.data = new
        self._folder_sel = set()
        self._folder_anchor = None
        self._note_sel = set()
        self._note_anchor = None
        self._note_idx = None
        try:
            self.search_var.set("")
        except Exception:
            pass
        self.body_size = self._read_body_size()
        self.refresh_all()
        self.load_editor()

    # ---------- 字体 / 颜色选择 ----------
    FONT_FAMILIES = [
        ("默认", None), ("微软雅黑", "Microsoft YaHei"), ("黑体", "SimHei"),
        ("宋体", "SimSun"), ("楷体", "KaiTi"), ("等宽 Consolas", "Consolas"),
    ]
    FONT_SIZES = [10, 12, 14, 16, 18, 20, 24, 28, 32]

    def _mk_font_ui(self, parent):
        fam = ttk.Combobox(parent, width=10, state="readonly", height=8)
        fam["values"] = [f[0] for f in self.FONT_FAMILIES]
        fam.set("默认")
        fam.pack(side=tk.LEFT, padx=5, pady=8)
        sz = ttk.Combobox(parent, width=4, state="readonly", height=10)
        sz["values"] = [str(s) for s in self.FONT_SIZES]
        sz.set("12")
        sz.pack(side=tk.LEFT, padx=(0, 6), pady=8)
        self.font_fam, self.font_sz = fam, sz
        fam.bind("<<ComboboxSelected>>", lambda e: self._apply_font_ui())
        sz.bind("<<ComboboxSelected>>", lambda e: self._apply_font_ui())

    def _mk_color_ui(self, parent):
        b = RoundButton(parent, self.pal, "A 颜色", command=self._pick_color,
                        fg=self.pal["accent"])
        b.pack(side=tk.LEFT, padx=3, pady=8)
        b.bind_events()
        self.fmt_buttons.append(b)

    def _apply_font_ui(self):
        if not self.current_note():
            return
        fam_label = self.font_fam.get()
        size = int(self.font_sz.get())
        fam = dict(self.FONT_FAMILIES).get(fam_label, None)
        self.apply_font(fam, size)

    def apply_font(self, family, size):
        """仅对当前选中的文字设置字体家族+字号；未选中任何文字则提示，不改动。"""
        if not self.current_note():
            return
        sel = self.text.tag_ranges("sel")
        if not sel:
            self._show_info("提示", "请先选中要设置字体的文字。")
            return
        self.text.config(state=tk.NORMAL)
        a, b = sel[0], sel[1]
        # 清除同类型动态字体标签，避免叠加
        for t in list(self.text.tag_names()):
            if t.startswith("f_"):
                self.text.tag_remove(t, a, b)
        fam = family or FONT[0]
        slug = (family or "def").replace(" ", "")
        tag = "f_%s_%d" % (slug, size)
        self.data.setdefault("styles", {})[tag] = {"font": [fam, size]}
        self.text.tag_configure(tag, font=(fam, size))
        self.text.tag_add(tag, a, b)
        self.mark_modified()

    def _pick_color(self):
        if not self.current_note():
            self._show_info("提示", "请先选择一条笔记。")
            return
        if not self.text.tag_ranges("sel"):
            self._show_info("提示", "请先选中要设置颜色的文字。")
            return
        self._open_color_dialog()

    def _open_color_dialog(self, title="选择文字颜色", on_pick=None):
        """居中的颜色选择框（预设色板 + 自定义），相对主窗口居中。

        title: 弹框标题；on_pick: 选色后的回调（默认 apply_color 设置文字颜色，
        传入 apply_underline_color 则设置下划线颜色）。
        """
        on_pick = on_pick or self.apply_color
        dlg = tk.Toplevel(self.root)
        dlg.title(title)
        dlg.transient(self.root)
        dlg.resizable(False, False)
        dlg.configure(bg=self.pal["editor"])
        # 先布局并居中，再尝试模态 grab（未映射时 grab 可能失败，需容错）
        dlg.update_idletasks()
        w, h = 348, 268
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - w) // 2)
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - h) // 2)
        dlg.geometry("%dx%d+%d+%d" % (w, h, x, y))
        try:
            dlg.grab_set()
        except Exception:
            pass

        tk.Label(dlg, text=title, bg=self.pal["editor"], fg=self.pal["text"],
                 font=(FONT[0], 13, "bold")).pack(pady=(12, 4))

        colors = [
            "#000000", "#434343", "#76767e", "#a0a0a8", "#c0392b", "#e67e22",
            "#f1c40f", "#27ae60", "#16a34a", "#2980b9", "#2f7cf6", "#8e44ad",
            "#e84393", "#ffffff", "#7f8c8d", "#d35400", "#16a085", "#2c3e50",
        ]
        grid = tk.Frame(dlg, bg=self.pal["editor"])
        grid.pack(padx=16, pady=4)
        cols = 6

        def choose(hexv):
            on_pick(hexv)
            dlg.destroy()

        r = c = 0
        for hexv in colors:
            # activebackground 必须与自身同色：不设的话按下会被系统默认灰覆盖
            b = tk.Button(grid, width=3, height=1, bg=hexv, relief=tk.RAISED, bd=1,
                          activebackground=hexv, activeforeground=hexv,
                          cursor="hand2", command=lambda hv=hexv: choose(hv))
            b.grid(row=r, column=c, padx=5, pady=5)
            c += 1
            if c >= cols:
                c = 0
                r += 1

        row2 = tk.Frame(dlg, bg=self.pal["editor"])
        row2.pack(pady=(8, 4))

        def custom():
            from tkinter import colorchooser
            rgb, hexv = colorchooser.askcolor(title="选择自定义颜色", parent=dlg)
            if hexv:
                choose(hexv)

        tk.Button(row2, text="自定义颜色…", bg=self.pal["editor"], fg=self.pal["accent"],
                  bd=0, relief=tk.FLAT, font=(FONT[0], 12), command=custom,
                  activebackground=self.pal["hover"], activeforeground=self.pal["selfg"],
                  cursor="hand2").pack(side=tk.LEFT, padx=10)
        tk.Button(row2, text="取消", bg=self.pal["editor"], fg=self.pal["soft"],
                  bd=0, relief=tk.FLAT, font=(FONT[0], 12), command=dlg.destroy,
                  activebackground=self.pal["hover"], activeforeground=self.pal["text"],
                  cursor="hand2").pack(side=tk.LEFT, padx=10)

    def apply_color(self, hexv):
        """仅对当前选中的文字设置颜色；未选中任何文字则提示，不改动。"""
        if not self.current_note():
            return
        sel = self.text.tag_ranges("sel")
        if not sel:
            self._show_info("提示", "请先选中要设置颜色的文字。")
            return
        self.text.config(state=tk.NORMAL)
        a, b = sel[0], sel[1]
        for t in list(self.text.tag_names()):
            if t.startswith("c_"):
                self.text.tag_remove(t, a, b)
        tag = "c_" + str(hexv).lstrip("#").lower()
        self.data.setdefault("styles", {})[tag] = {"fg": hexv}
        self.text.tag_configure(tag, foreground=hexv)
        self.text.tag_add(tag, a, b)
        # 注意：这里不再设置下划线颜色。Tk 默认「未指定 underlinefg 时下划线的颜色 = 文字颜色」，
        # 所以下划线自动跟随文字颜色；而通过「U 颜色」显式设置的下划线颜色也不会被文字颜色覆盖。
        self.mark_modified()

    def _apply_underline_color_range(self, a, b, hexv):
        """给区间设置下划线颜色。

        关键：Tk 要求 `-underline` 与 `-underlinefg` 必须写在**同一个 tag** 上，
        下划线才会用该颜色绘制；分成两个 tag（一个只给 underlinefg）时颜色不生效。
        """
        for t in list(self.text.tag_names()):
            if t.startswith("u_"):
                self.text.tag_remove(t, a, b)
        tag = "u_" + str(hexv).lstrip("#").lower()
        self.data.setdefault("styles", {})[tag] = {"ufg": hexv}
        self.text.tag_configure(tag, underline=True, underlinefg=hexv)
        self.text.tag_add(tag, a, b)

    def _mk_underline_color_ui(self, parent):
        b = RoundButton(parent, self.pal, "U 颜色", command=self._pick_underline_color,
                        font=(FONT[0], 12, "underline"), fg=self.pal["accent"])
        b.pack(side=tk.LEFT, padx=3, pady=8)
        b.bind_events()
        self.fmt_buttons.append(b)

    def _pick_underline_color(self):
        if not self.current_note():
            self._show_info("提示", "请先选择一条笔记。")
            return
        if not self.text.tag_ranges("sel"):
            self._show_info("提示", "请先选中要设置下划线颜色的文字。")
            return
        self._open_color_dialog("选择下划线颜色", self.apply_underline_color)

    def apply_underline_color(self, hexv):
        """仅对选中文字设置「下划线颜色」（underlinefg），与文字颜色互相独立。"""
        if not self.current_note():
            return
        sel = self.text.tag_ranges("sel")
        if not sel:
            self._show_info("提示", "请先选中要设置下划线颜色的文字。")
            return
        self.text.config(state=tk.NORMAL)
        a, b = sel[0], sel[1]
        # u_ 标签自带 underline=True，因此无需再单独加 underline 标签
        self._apply_underline_color_range(a, b, hexv)
        self.mark_modified()

    def reset_format(self):
        """把当前笔记恢复为默认格式：清空行内样式（加粗/颜色/字体/删除线等），
        但保留行级标签（title/h2/body）——标题仍还原为标题。纯文本与待办框保留。"""
        n = self.current_note()
        if not n:
            self._show_info("提示", "请先选择一条笔记。")
            return
        if not self._ask_confirm(
                "还原默认格式",
                "确定将当前笔记恢复为默认格式吗？\n加粗、颜色、字体、删除线等行内样式会被清除，"
                "标题/正文格式会保留。", ok_text="还原"):
            return
        # 只清除行内样式标签，保留行级标签 title/h2/body（代码块没有样式标签，跳过）
        keep = {"title", "h2", "body"}
        for it in n["content"]:
            if len(it) > 2 and it[0] in ("t", "w"):
                it[2] = [t for t in it[2] if t in keep]
        save_data(self.data)
        self.load_editor()
        self._update_active_row()

    def _mk_fmt(self, parent, label, cmd, **kw):
        st = []
        if kw.get("bold"): st.append("bold")
        if kw.get("italic"): st.append("italic")
        if kw.get("underline"): st.append("underline")
        if kw.get("strike"): st.append("overstrike")
        f = (FONT[0], 12) + tuple(st)
        # 圆角按钮（Canvas 自绘）：按下不会出现系统默认灰，圆角/间距也可控
        b = RoundButton(parent, self.pal, label, command=cmd, font=f)
        b.pack(side=tk.LEFT, padx=3, pady=8)
        b.bind_events()
        self.fmt_buttons.append(b)
        return b

    def _sep(self, parent):
        s = tk.Frame(parent, width=1, bg=self._tool_line(), height=20)
        s.pack(side=tk.LEFT, padx=6, pady=8)
        self._tool_seps.append(s)

    # ---------- 文件夹行 ----------
    def refresh_folders(self):
        p = self.pal
        for w in list(self.folder_panel.inner.children.values()):
            w.destroy()
        self._stop_all_marquee()     # 行即将销毁，先停掉跑马灯计时器
        self._folder_rows = {}
        self._folder_count_labels = {}
        self._folder_name_labels = {}
        self._folder_icons = {}
        for f in self.data["folders"]:
            self._make_folder_row(f)
        self._update_folder_total()
        # 显式刷新滚动区，确保文件夹过多时滚动条启用、可拖（多重延迟兜底窗口时序）
        self.folder_panel.inner.update_idletasks()
        self.folder_panel._update()
        self.folder_panel.canvas.after(30, self.folder_panel._update)
        self.folder_panel.canvas.after(120, self.folder_panel._update)
        # 行被重建后需重新绑定滚轮（否则指针停在行/图标上滚不动）
        self.folder_panel.bind_wheel()
        # 行重建 = 新控件：指针若正停在某个名字上，收不到 Enter，这里补一次
        self._after(120, self._resume_marquee_under_pointer)

    def _update_folder_total(self):
        """左栏顶部显示文件夹总数（新建/删除文件夹后同步刷新）。"""
        lbl = getattr(self, "folder_total_label", None)
        if lbl is None:
            return
        try:
            if not lbl.winfo_exists():
                return
            lbl.configure(text="共 %d 个文件夹" % len(self.data["folders"]))
        except Exception:
            pass

    def _refresh_folder_counts(self):
        """就地刷新左栏每个文件夹的条数。

        新建/删除/移动笔记时调用：文件夹行不重建（避免闪烁），只改计数文本。
        """
        for fid, lbl in list(self._folder_count_labels.items()):
            if lbl is None:
                continue
            try:
                if not lbl.winfo_exists():
                    continue
                lbl.configure(text=str(len(self.folder_notes(fid))))
            except Exception:
                pass

    # ---------- 圆角卡片行背景 ----------
    def _card_layer(self, parent, color, outside):
        """在行容器内铺一层圆角背景（Canvas + place），返回该 Canvas。

        Tk 的 Frame 是矩形、无法圆角，所以背景交给 Canvas 画：画布底色是「卡片外」
        的面板色，只有圆角多边形内部才是卡片色 —— 两者不能同色，否则圆角会被填满、
        看上去还是方块。
        """
        cv = tk.Canvas(parent, bg=outside, bd=0, highlightthickness=0)
        cv.place(relx=0, rely=0, relwidth=1, relheight=1)
        # 压到最底层，内容控件画在它上面。
        # 注意：Canvas.lower() 是「画布内图元降层」，widget 降层必须走 Tk 原生命令
        tk.Misc.lower(cv)
        cv._color = color

        def draw(ev=None):
            w, h = cv.winfo_width(), cv.winfo_height()
            if w < 3 or h < 3:
                return
            cv.delete("all")
            cv.create_polygon(_round_rect_points(0.5, 0.5, w - 0.5, h - 0.5,
                                                 CARD_RADIUS, 6),
                              fill=cv._color, outline="")

        cv._draw = draw
        cv.bind("<Configure>", draw)
        return cv

    def _paint_card(self, row, color):
        """把行的圆角背景换成指定底色（选中/悬停/普通切换时调用）。"""
        cv = getattr(row, "_card", None)
        if cv is None or not cv.winfo_exists():
            return
        cv._color = color
        cv._draw()

    def _card_base(self, row):
        """行背景层的「卡片外」底色（圆角之外露出的面板色）。"""
        return getattr(row, "_card_outside", self.pal["list"])

    def _make_folder_row(self, f):
        """创建并追加单个文件夹行（新建文件夹时只调用这个，避免整列重建导致闪烁）。"""
        p = self.pal
        cnt = len(self.folder_notes(f["id"]))
        active = (f["id"] == self.data["current_folder"])
        base = p["sel"] if active else p["side"]
        # 同样用实底色，避免透明 Frame 不重绘导致的高亮残留
        wrap = tk.Frame(self.folder_panel.inner, bg=p["side"])
        wrap.pack(fill=tk.X, padx=CARD_PADX, pady=(CARD_GAP, 0))
        row = tk.Frame(wrap, bg=base)
        row.pack(fill=tk.BOTH, expand=True)
        row._card_outside = p["side"]
        row._card = self._card_layer(row, base, p["side"])
        # 左侧图标（自绘；智能文件夹用区别图标，普通文件夹用圆角文件夹）
        icon = tk.Canvas(row, width=FOLDER_ICON_W, height=FOLDER_ICON_H,
                         bg=base, bd=0, highlightthickness=0)
        icon.pack(side=tk.LEFT, padx=(12, 5), pady=6)
        self._folder_icons[f["id"]] = icon
        self._draw_folder_icon(icon, f["id"])
        # 右侧数量先 pack(side=RIGHT)：先把宽度占死，名字再长也压不到它
        cl = tk.Label(row, text=str(cnt), bg=base, fg=p["soft"], anchor="e",
                      font=(FONT[0], 11))
        cl.pack(side=tk.RIGHT, padx=(6, 12), pady=6)
        # 名字装进「固定宽度 + 不随内容扩张」的盒子：超长部分被盒子裁掉，
        # 不会把右侧数字挤走（Tk 的 pack 会按请求宽度挤压邻居，必须靠盒子兜住）
        name_h = self._measure_font(FONT[0], 12).metrics("linespace")
        name_box = tk.Frame(row, bg=base, height=name_h)
        name_box.pack(side=tk.LEFT, fill=tk.X, expand=True, pady=6)
        name_box.pack_propagate(False)
        # 名字用自绘 Canvas（可按像素滚动）：撑满盒子宽度，超长部分由画布天然裁掉
        name_label = MarqueeLabel(name_box, bg=base,
                                  fg=(p["selfg"] if active else p["text"]),
                                  font=(FONT[0], 12, "bold" if active else "normal"))
        name_label.set_text(f["name"])
        name_label.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        row._full_name = f["name"]
        row._name_box = name_box
        row._name_clipped = False
        row._base_bg = base
        row.bind("<Enter>", lambda e, r=row, p=p: self._row_hover(r, p["hover"] if r._base_bg != p["sel"] else p["sel"], fb=p["side"]))
        row.bind("<Leave>", lambda e, r=row: self._row_hover(r, r._base_bg, fb=p["side"]))
        row.bind("<Button-1>", lambda e, fid=f["id"]: self._folder_click(e, fid))
        row.bind("<Button-3>", lambda e, fid=f["id"]: self._folder_popup(e, fid))
        for ch in list(row.winfo_children()) + [name_label]:
            # 背景层 Canvas 也要绑定：Canvas 不把鼠标事件冒泡给父 Frame
            ch.bind("<Button-1>", lambda e, fid=f["id"]: self._folder_click(e, fid))
            ch.bind("<Button-3>", lambda e, fid=f["id"]: self._folder_popup(e, fid))
        # 悬停标题：被截断过的名字用跑马灯滚动展示完整内容
        for w in (name_box, name_label):
            w.bind("<Enter>", lambda e, fid=f["id"]: self._start_marquee(fid), add="+")
            w.bind("<Leave>", lambda e, fid=f["id"]: self._stop_marquee(fid, restore=True), add="+")
        # 宽度变化时重新按像素截断。这一条不能省：首帧布局常常还没定型（此时
        # name_box 宽度仍是 1px），after_idle 里那一次会直接放弃，若没有 Configure
        # 兜底，_name_clipped 就一直停在错误值上 → 悬停跑马灯不触发，
        # 表现成「有时悬停没反应，切一次文件夹又好了」。
        name_box.bind("<Configure>", lambda e, fid=f["id"]: self._fit_folder_name(fid), add="+")
        self._folder_rows[f["id"]] = row
        self._folder_name_labels[f["id"]] = name_label
        self._folder_count_labels[f["id"]] = cl
        # 行宽要等布局完成才算得出来，延后一拍做「按像素截断」
        self._after_idle(lambda fid=f["id"]: self._fit_folder_name(fid))
        return row

    # ---------- 文件夹名：按像素截断 + 悬停跑马灯 ----------
    MARQUEE_MS = 33      # 定时器间隔（≈30fps）
    MARQUEE_PX = 1.0     # 每帧位移像素 → 约 30px/s，慢速可读；整像素步进不发虚

    def _folder_name_font_tuple(self, active):
        return (FONT[0], 12, "bold" if active else "normal")

    def _measure_font(self, family, size, weight="normal", slant="roman"):
        """按像素测量文字宽度用的 tkfont.Font（按 App 实例缓存）。

        注意：tkfont.Font 绑定在创建它的 Tk 解释器上，缓存绝不能做成模块级全局 ——
        否则主窗口销毁后重建时，会拿到已失效的 font 对象并抛
        "application has been destroyed"。
        """
        key = (family, size, weight, slant)
        f = self._font_cache.get(key)
        if f is None:
            import tkinter.font as _tf
            f = _tf.Font(family=family, size=size, weight=weight, slant=slant)
            self._font_cache[key] = f
        return f

    def _pointer_in(self, w):
        """指针此刻是否落在控件 w 内部（含其子孙）。"""
        try:
            if not w.winfo_exists() or not w.winfo_ismapped():
                return False
            x, y = self.root.winfo_pointerxy()
            c = self.root.winfo_containing(x, y)
            while c is not None:
                if c is w:
                    return True
                c = getattr(c, "master", None)
        except Exception:
            return False
        return False

    def _refit_and_keep_hover(self, key, row, lbl, refit):
        """重排后若指针还停在名字上（如正在悬停时缩放窗口/列表重排），把跑马灯续上。

        否则会出现「跑马灯忽然停住且不再动」——refit 把它还原成静止省略号，
        而指针没离开，不会再产生 Enter 事件去重新触发。
        """
        try:
            if row is None or lbl is None or not lbl.winfo_exists():
                return
            if row._name_clipped and self._pointer_in(row._name_box):
                self._start_marquee_at(key, row, lbl, refit)
        except Exception:
            pass

    def _fit_folder_name(self, fid):
        """把文件夹名按可用像素截断成「…」；空间不够时随后重试（布局尚未完成）。"""
        row = self._folder_rows.get(fid)
        lbl = self._folder_name_labels.get(fid)
        if row is None or lbl is None:
            return
        self._stop_marquee(fid)
        try:
            if not lbl.winfo_exists() or not row._name_box.winfo_exists():
                return
            avail = row._name_box.winfo_width()
        except Exception:
            return
        if avail < 20:
            # 首次布局还没量出宽度：最多重试几次，避免死循环
            tries = getattr(row, "_fit_tries", 0)
            if tries < 8:
                row._fit_tries = tries + 1
                self._after(60, lambda fid=fid: self._fit_folder_name(fid))
            return
        row._fit_tries = 0
        active = (fid == self.data["current_folder"])
        f = self._measure_font(FONT[0], 12, "bold" if active else "normal")
        text, clipped = self._ellipsize(row._full_name, avail, f)
        row._name_clipped = clipped
        try:
            lbl.set_text(text, 0)
        except Exception:
            pass
        self._refit_and_keep_hover(fid, row, lbl, lambda: self._fit_folder_name(fid))

    def _fit_note_title(self, nid):
        """把笔记标题按可用像素截断成「…」；空间尚未量出时随后重试。

        与文件夹名同一套逻辑：标题装在固定宽盒子里，超长被裁掉，右侧的
        星标 / 日期永远不会被挤走。
        """
        rec = self._rows.get(nid)
        if not rec:
            return
        row, lbl = rec["row"], rec["tl"]
        self._stop_marquee("n:" + nid)
        try:
            if not lbl.winfo_exists() or not row._name_box.winfo_exists():
                return
            avail = row._name_box.winfo_width()
        except Exception:
            return
        if avail < 20:
            # 首次布局还没量出宽度：最多重试几次，避免死循环
            tries = getattr(row, "_fit_tries", 0)
            if tries < 8:
                row._fit_tries = tries + 1
                self._after(60, lambda nid=nid: self._fit_note_title(nid))
            return
        row._fit_tries = 0
        f = self._measure_font(FONT[0], 13, "bold")
        text, clipped = self._ellipsize(row._full_name, avail, f)
        row._name_clipped = clipped
        try:
            lbl.set_text(text, 0)
        except Exception:
            pass
        key = "n:" + nid
        self._refit_and_keep_hover(key, row, lbl, lambda: self._fit_note_title(nid))

    def _ellipsize(self, s, maxpx, font):
        """二分找最长可容纳前缀，末尾接「…」；能放下则原样返回。"""
        if not s:
            return s, False
        if font.measure(s) <= maxpx:
            return s, False
        ell = "…"
        lo, hi = 0, len(s)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if font.measure(s[:mid] + ell) <= maxpx:
                lo = mid
            else:
                hi = mid - 1
        return (s[:lo] + ell) if lo > 0 else ell, True

    # ---------- 跑马灯（文件夹名 / 笔记标题共用） ----------
    # 行上约定三个字段：_name_box（固定宽盒子）、_full_name（完整文字）、
    # _name_clipped（是否已被截断）。滚动表以 key 区分：文件夹用 fid，笔记用 "n:"+nid。

    def _start_marquee(self, fid):
        """悬停被截断的文件夹名。"""
        self._start_marquee_at(fid, self._folder_rows.get(fid),
                               self._folder_name_labels.get(fid),
                               lambda: self._fit_folder_name(fid))

    def _start_note_marquee(self, nid):
        """悬停被截断的笔记标题（与文件夹名同一套滚动实现）。"""
        rec = self._rows.get(nid)
        self._start_marquee_at("n:" + nid, (rec or {}).get("row"),
                               (rec or {}).get("tl"),
                               lambda: self._fit_note_title(nid))

    def _start_marquee_at(self, key, row, lbl, refit):
        """对「被截断的文字」启动按像素平滑循环滚动，展示完整内容。

        是否被截断按**悬停当时的真实宽度**判断，不全信缓存的 _name_clipped：
        首帧布局迟到、窗口缩放、计数位数变宽都可能让缓存过期，而缓存为 False 时
        悬停就完全没反应（要等到下一次 reconstruction 才恢复）。
        """
        if key in self._marquee:
            return
        if row is None or lbl is None:
            return
        try:
            if not lbl.winfo_exists() or not row._name_box.winfo_exists():
                return
            avail = row._name_box.winfo_width()
        except Exception:
            return
        if avail < 20:
            return
        full = row._full_name or ""
        try:
            overflow = lbl.measure(full) > avail + 1
        except Exception:
            overflow = bool(getattr(row, "_name_clipped", False))
        row._name_clipped = overflow
        if not overflow:
            # 没被截断：把可能残留的省略号还原成完整原文
            try:
                if lbl._text != full:
                    lbl.set_text(full, 0)
            except Exception:
                pass
            return
        try:
            # 换成完整文字，从头部开始按像素滚动（0 偏移即完整文字的开头）
            lbl.set_text(full, 0.0)
        except Exception:
            return
        self._marquee[key] = {"lbl": lbl, "pos": 0.0, "job": None, "refit": refit}
        self._marquee_step(key)

    def _marquee_step(self, key):
        j = self._marquee.get(key)
        if not j:
            return
        try:
            lbl = j["lbl"]
            if not lbl.winfo_exists():
                self._marquee.pop(key, None)
                return
            j["pos"] += self.MARQUEE_PX
            lbl.set_offset(j["pos"])
            j["job"] = self._after(self.MARQUEE_MS,
                                   lambda key=key: self._marquee_step(key))
        except Exception:
            self._marquee.pop(key, None)

    def _stop_marquee(self, key, restore=False):
        j = self._marquee.pop(key, None)
        if j and j.get("job"):
            self._cancel_after(j["job"])
        if restore and j and j.get("refit"):
            j["refit"]()          # 还原成省略号静态显示

    def _stop_all_marquee(self):
        for key in list(self._marquee.keys()):
            self._stop_marquee(key)

    def _resume_marquee_under_pointer(self):
        """列表重建后补一次触发：行是新造的，指针停着不动就收不到 Enter 事件，
        于是跑马灯一直不出现，直到鼠标移开再移回来。这里直接把该滚的那行续上。"""
        try:
            for fid, row in list(self._folder_rows.items()):
                lbl = self._folder_name_labels.get(fid)
                box = getattr(row, "_name_box", None)
                if lbl is None or box is None or not box.winfo_exists():
                    continue
                if self._pointer_in(box):
                    self._start_marquee(fid)
                    return
            for nid, rec in list(self._rows.items()):
                row = rec.get("row")
                box = getattr(row, "_name_box", None)
                if row is None or box is None or not box.winfo_exists():
                    continue
                if self._pointer_in(box):
                    self._start_note_marquee(nid)
                    return
        except Exception:
            pass

    def _draw_folder_icon(self, c, fid=None):
        """在 18x16 画布上自绘文件夹图标。

        - 智能文件夹：全部笔记 = 清单三线；星标笔记 = 五角星（金色）。
        - 普通文件夹：圆角双色（白底 + 主题色描边 + 左上小舌，macOS Finder 风）。
          主体用 editor 白而非 sel 浅绿，因为选中行的底色就是 sel，浅绿填充会和行底糊在一起。
        """
        p = self.pal
        c.delete("all")

        if fid == SMART_ICON_ALL:
            # 清单：三条等长横线
            for y in (3.6, 7.1, 10.6):
                c.create_rectangle(3, y, 15, y + 1.8,
                                   fill=p["accent"], outline=p["accent"], width=0)
            return

        if fid == SMART_ICON_STAR:
            # 五角星：外半径 6 / 内半径 2.6，居中于 18x16
            c.create_polygon(_star_points(9.0, 8.0, 6.0, 2.6),
                             fill=p["star"], outline=p["star"], width=0)
            return

        # --- 普通文件夹：圆角双色 ---
        col = p["accent"]
        # 左上小舌（先画，主体压在其下缘，视觉上是一体伸出的）
        # 注意：关键边用整数坐标，否则 1px 描边落在半像素上会被 Tk 挪到相邻行，底边被挤出画布
        c.create_polygon(_round_rect_points(2.0, 2.5, 8.0, 6.2, 1.4),
                         fill=col, outline=col, width=0)
        # 主体：圆角矩形，白底 + 主题色 1px 描边
        c.create_polygon(_round_rect_points(2.0, 6.0, 16.0, 14.0, 2.0),
                         fill=p["editor"], outline=col, width=1)

    def _row_hover(self, widget, bg, fb=None):
        """递归重绘行背景。Frame 可用透明("")；Label/Button 不接受 ""，自动回退实色。"""
        fb = fb or self.pal["list"]
        try:
            widget.configure(bg=bg)
        except Exception:
            try:
                widget.configure(bg=(bg if bg not in ("", None) else fb))
            except Exception:
                pass
        for ch in widget.winfo_children():
            # 背景层 Canvas 例外：它的底色是「卡片外」的面板色，不能跟着一起改，
            # 否则圆角外面也被填满，圆角等于白画（卡片色由 _paint_card 单独控制）
            if ch is getattr(widget, "_card", None):
                continue
            self._row_hover(ch, bg, fb)
        # 行若带圆角背景层（Canvas），子控件改完色后要把那一层也重画
        cv = getattr(widget, "_card", None)
        if cv is not None and cv is not widget:
            self._paint_card(widget, bg)

    def select_folder(self, fid):
        if self.data["current_folder"] == fid:
            return
        self.data["current_folder"] = fid
        self.data["current_note"] = None
        # 切换文件夹时清空笔记多选，并就地更新文件夹行高亮，不重建整列 → 避免“全部/星标”闪烁
        self._note_sel = set()
        self._note_anchor = None
        self._restyle_folders()
        self.refresh_notes()
        self.load_editor()

    def _set_folder_active(self, fid):
        """就地切换文件夹选中态（背景 + 文字色），不销毁重建行。"""
        p = self.pal
        for f in self.data["folders"]:
            row = self._folder_rows.get(f["id"])
            if not row:
                continue
            active = (f["id"] == fid)
            base = p["sel"] if active else p["side"]
            row._base_bg = base
            self._row_hover(row, base, fb=p["side"])
            self._style_folder_labels(f["id"], active or False)

    def _style_folder_labels(self, fid, hl):
        """设置文件夹行的标题 / 数量文字色与字重（标题已不在 row 的直接子控件里，需按引用改）。"""
        p = self.pal
        nl = self._folder_name_labels.get(fid)
        if nl is not None:
            try:
                if nl.winfo_exists():
                    nl.set_style(fg=(p["selfg"] if hl else p["text"]),
                                 font=self._folder_name_font_tuple(hl))
            except Exception:
                pass
        cl = self._folder_count_labels.get(fid)
        if cl is not None:
            try:
                if cl.winfo_exists():
                    cl.configure(fg=(p["selfg"] if hl else p["soft"]))
            except Exception:
                pass

    def _restyle_folders(self):
        """统一刷新文件夹行样式：当前文件夹(active) > 多选高亮 > 普通；不重建行、不闪烁。"""
        p = self.pal
        cur = self.data["current_folder"]
        valid = {f["id"] for f in self.data["folders"]}
        self._folder_sel &= valid
        for f in self.data["folders"]:
            row = self._folder_rows.get(f["id"])
            if not row:
                continue
            active = (f["id"] == cur)
            seld = (f["id"] in self._folder_sel)
            base = p["sel"] if active else (p["hover"] if seld else p["side"])
            row._base_bg = base
            self._row_hover(row, base, fb=p["side"])
            self._style_folder_labels(f["id"], active or seld)
            # 字重变了 → 可用宽度里的字数也变，重新按像素截断
            self._fit_folder_name(f["id"])
            # 图标随调色板重绘（换主题/切选中态时颜色同步）
            cv = self._folder_icons.get(f["id"])
            if cv is not None and cv.winfo_exists():
                self._draw_folder_icon(cv, f["id"])

    def _folder_click(self, e, fid):
        """左键：普通=导航并单选；Shift=从锚点区间多选；Ctrl=切换单个。"""
        shift = (e.state & 0x0001) != 0
        ctrl = (e.state & 0x0004) != 0
        ids = [f["id"] for f in self.data["folders"]]
        if shift and self._folder_anchor and self._folder_anchor in ids and fid in ids:
            a = ids.index(self._folder_anchor)
            b = ids.index(fid)
            lo, hi = (a, b) if a <= b else (b, a)
            self._folder_sel = set(ids[lo:hi + 1])
        elif ctrl:
            if fid in self._folder_sel:
                self._folder_sel.discard(fid)
            else:
                self._folder_sel.add(fid)
            self._folder_anchor = fid
        else:
            self._folder_sel = {fid}
            self._folder_anchor = fid
            self.select_folder(fid)
        self._restyle_folders()

    def _sync_folder_sel_to_note(self, nid):
        """「普通点击」笔记时：取消文件夹 Shift 多选，且不切换文件夹视图。

        - 智能文件夹（全部 / 星标）视图：只清掉文件夹多选，保留当前视图，
          点哪条笔记都不会跳转到它所属的文件夹。
        - 普通文件夹视图：同步高亮该文件夹为唯一选中（视图本来就是它，无需切换）。
        - 仅由普通点击触发；Shift/Ctrl 多选笔记时调用会破坏区间多选，切勿挂上去。
        """
        n = next((x for x in self.data["notes"] if x["id"] == nid), None)
        fid = (n.get("folder") if n else None)
        cur = self.data["current_folder"]
        if cur in ("f_all", "f_star"):
            self._folder_sel = set()
            self._folder_anchor = None
        elif fid and fid not in ("f_all", "f_star"):
            self._folder_sel = {fid}
            self._folder_anchor = fid
        else:
            self._folder_sel = set()
            self._folder_anchor = None
        # 必须重绘：否则文件夹行的多选高亮会残留在界面上，看起来“没生效”
        self._restyle_folders()

    def _delete_folders(self, fids):
        """批量删除选中的普通文件夹，其中的笔记一起删除。"""
        real = [i for i in fids if i not in ("f_all", "f_star")]
        if not real:
            self._show_info("提示", "智能文件夹（全部笔记 / 星标笔记）不可删除。")
            return
        realset = set(real)
        cnt = sum(1 for n in self.data["notes"] if n["folder"] in realset)
        if cnt:
            msg = ("选中的 %d 个文件夹下共有 %d 条笔记。\n"
                   "删除后这些笔记会一起删除，是否继续？" % (len(real), cnt))
        else:
            msg = "确定删除选中的 %d 个文件夹？" % len(real)
        if not self._ask_confirm("删除文件夹", msg, ok_text="删除", danger=True):
            return
        if cnt:
            dropped = {n["id"] for n in self.data["notes"] if n["folder"] in realset}
            self.data["notes"] = [x for x in self.data["notes"] if x["folder"] not in realset]
            self._note_sel -= dropped
            if self.data.get("current_note") in dropped:
                self.data["current_note"] = None
        self.data["folders"] = [x for x in self.data["folders"] if x["id"] not in realset]
        self._folder_sel = set()
        self._folder_anchor = None
        if self.data["current_folder"] in realset:
            self.data["current_folder"] = "f_all"
        save_data(self.data)
        self.refresh_all()

    # ---------- 笔记行 ----------
    def refresh_notes(self):
        p = self.pal
        self._star_buttons = {}
        self._rows = {}
        cur = next((f for f in self.data["folders"] if f["id"] == self.data["current_folder"]), None)
        self.mid_head.configure(text=cur["name"] if cur else "笔记", fg=p["soft"], bg=p["list"])
        self._stop_all_marquee()     # 行即将销毁，先停掉跑马灯计时器
        for w in list(self.note_panel.inner.children.values()):
            w.destroy()
        notes = self.visible_notes()
        self._active_title = self._active_preview = self._active_date = None
        for i, n in enumerate(notes):
            self._make_note_row(n, i, n["id"] == self.data.get("current_note"))
        # 布局完成后按可用像素截断标题（此时盒子宽度才量得出来）
        for n in notes:
            self._after_idle(lambda nid=n["id"]: self._fit_note_title(nid))
        # 显式刷新滚动区，确保内容溢出时滚动条启用、可拖（避免“铺满也滚不动”）
        self.note_panel.inner.update_idletasks()
        self.note_panel._update()
        self.note_panel.canvas.after(30, self.note_panel._update)
        self.note_panel.canvas.after(120, self.note_panel._update)
        # 行被重建后需重新绑定滚轮（否则指针停在行/文字上滚不动）
        self.note_panel.bind_wheel()
        self._restyle_notes()
        # 行重建 = 新控件：指针若正停在某个标题上，收不到 Enter，这里补一次
        self._after(120, self._resume_marquee_under_pointer)

    def _make_note_row(self, n, index, active, before=None):
        """创建并追加单条笔记行，返回它的外层 wrap。

        与文件夹行（_make_folder_row）同一套思路：新建笔记时只调用这个，把新行
        插到正确位置，避免整列重建——否则已存在的行被销毁再重建会闪一下。
        before 给定时插到该控件之前（用于把新笔记放到列表中间）。
        """
        p = self.pal
        starred = n.get("starred", False)
        # 行 Frame 用实底色（不能用透明 ""：Windows 下透明 Frame 不会触发父级重绘，
        # 取消选中时会残留上一次的高亮像素，表现为“背景色没消失”）
        row_frame_bg = p["sel"] if active else p["list"]
        child_bg = p["sel"] if active else p["list"]
        # 圆角悬浮卡片：外层 wrap 负责左右留白与行间距，内层 row 自带圆角背景层
        wrap = tk.Frame(self.note_panel.inner, bg=p["list"])
        opts = dict(fill=tk.X, padx=CARD_PADX, pady=(CARD_GAP if index else 2, 0))
        if before is not None:
            opts["before"] = before
        wrap.pack(**opts)
        row = tk.Frame(wrap, bg=row_frame_bg)
        row.pack(fill=tk.BOTH, expand=True)
        row._card_outside = p["list"]
        row._card = self._card_layer(row, row_frame_bg, p["list"])
        top = tk.Frame(row, bg=row_frame_bg)
        top.pack(fill=tk.X, padx=14, pady=(2, 0))
        # 右侧（星标 + 日期）先 pack：先把宽度占死，标题再长也压不到它
        right = tk.Frame(top, bg=row_frame_bg)
        right.pack(side=tk.RIGHT)
        sb = tk.Button(right, text="★" if starred else "☆",
                       fg=(p["star"] if starred else p["soft"]),
                       bg=child_bg, bd=0, relief=tk.FLAT, cursor="hand2",
                       font=(FONT[0], 17),
                       activebackground=child_bg, activeforeground=(p["star"] if starred else p["soft"]),
                       command=lambda nid=n["id"]: self.toggle_star(nid))
        sb.pack(side=tk.RIGHT, padx=(0, 6))
        self._star_buttons[n["id"]] = sb
        dl = tk.Label(right, text=self._date_text(n), bg=child_bg, fg=p["soft"],
                       anchor="e", font=(FONT[0], 11))
        dl.pack(side=tk.RIGHT)
        # 标题装进「固定高度 + 不随内容扩张」的盒子：超长部分被盒子裁掉，
        # 不会把右侧的星标/日期挤走（与文件夹名同一套做法）
        t_h = self._measure_font(FONT[0], 13, "bold").metrics("linespace")
        tl_box = tk.Frame(top, bg=child_bg, height=t_h)
        tl_box.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        tl_box.pack_propagate(False)
        tl = MarqueeLabel(tl_box, bg=child_bg,
                          fg=(p["selfg"] if active else p["text"]),
                          font=(FONT[0], 13, "bold"))
        tl.set_text(self.title_of(n))
        tl.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        row._full_name = self.title_of(n)
        row._name_box = tl_box
        row._name_clipped = False
        row._fit_tries = 0
        prev = self.trunc(self.preview_of(n), 22) or "暂无更多文字"
        pl = tk.Label(row, text=prev, bg=child_bg, fg=p["soft"], anchor="w",
                       font=(FONT[0], 12))
        pl.pack(fill=tk.X, padx=14, pady=(0, 2))
        if active:
            self._active_title, self._active_preview, self._active_date = tl, pl, dl
        self._rows[n["id"]] = {"row": row, "top": top, "right": right,
                                "starred": starred, "active": active,
                                "tl": tl, "pl": pl, "dl": dl, "sb": sb,
                                "wrap": wrap}
        row._base_bg = row_frame_bg
        row.bind("<Enter>", lambda e, r=row, p=p: self._row_hover(r, p["hover"] if r._base_bg != p["sel"] else p["sel"]))
        row.bind("<Leave>", lambda e, r=row: self._row_hover(r, r._base_bg))
        # 拖拽移动：按下记录起点，移动超过阈值进入拖拽态，松手落到目标文件夹；
        # 未超过阈值则视为普通点击 → 选中该笔记。
        row.bind("<ButtonPress-1>", lambda e, nid=n["id"]: self._note_press(e, nid))
        row.bind("<B1-Motion>", lambda e, nid=n["id"]: self._note_motion(e, nid))
        row.bind("<ButtonRelease-1>", lambda e, nid=n["id"]: self._note_release(e, nid))
        row.bind("<Button-3>", lambda e, nid=n["id"]: self._popup(e, nid))
        # 子控件也要绑定拖拽/右键：父级不在子控件的 bindtags 中，事件不会自动冒泡
        # （背景层 Canvas 同样要绑定，它覆盖了行四周的内边距区域）
        for w in (top, right, tl_box, tl, dl, pl, row._card):
            w.bind("<ButtonPress-1>", lambda e, nid=n["id"]: self._note_press(e, nid))
            w.bind("<B1-Motion>", lambda e, nid=n["id"]: self._note_motion(e, nid))
            w.bind("<ButtonRelease-1>", lambda e, nid=n["id"]: self._note_release(e, nid))
            w.bind("<Button-3>", lambda e, nid=n["id"]: self._popup(e, nid))
        # 悬停标题：被截断过的标题用跑马灯滚动展示完整内容（同文件夹名）
        for w in (tl_box, tl):
            w.bind("<Enter>", lambda e, nid=n["id"]: self._start_note_marquee(nid), add="+")
            # 注意：这里必须用默认参数 nid，不能写 n["id"]——n 是循环变量的
            # 闭包引用，事件真正触发时它已指向最后一条笔记，会停错对象。
            w.bind("<Leave>", lambda e, nid=n["id"]: self._stop_marquee("n:" + nid, restore=True), add="+")
        # 窗口宽度变化后盒子可用宽度变了 → 重新按像素截断
        tl_box.bind("<Configure>", lambda e, nid=n["id"]: self._fit_note_title(nid), add="+")
        # 星标按钮只触发 toggle_star（就地切换，不重建列表、不整页闪烁）；
        # 若也绑定 select_note，会因「点击→整列重建→按钮被销毁→command 来不及触发」导致无法取消星标。
        # 卡片之间靠间距分隔，不再插 1px 分割线（分割线会横穿圆角区域的留白）
        return wrap

    def _insert_note_row(self, n):
        """新建笔记时只把新行插到它应在的位置，不重建整列（避免其它行闪烁）。

        位置由 visible_notes() 的排序（置顶 > 星标 > 更新时间）决定；若新笔记
        因搜索过滤等原因不在可见列表里，则退回整列重建。
        """
        notes = self.visible_notes()
        ids = [x["id"] for x in notes]
        if n["id"] not in ids:
            self.refresh_notes()
            return
        idx = ids.index(n["id"])
        before = None
        if idx + 1 < len(notes):
            nxt = self._rows.get(notes[idx + 1]["id"])
            if nxt:
                before = nxt["wrap"]
        self._make_note_row(n, idx, True, before=before)
        # 插到最前面时，原来的首行从「首位小间距」变成「行间距」，同步一下
        if idx == 0 and len(notes) > 1:
            nxt = self._rows.get(notes[1]["id"])
            if nxt:
                nxt["wrap"].pack_configure(pady=(CARD_GAP, 0))
        self._after_idle(lambda nid=n["id"]: self._fit_note_title(nid))
        self.note_panel.inner.update_idletasks()
        self.note_panel._update()
        self.note_panel.canvas.after(30, self.note_panel._update)
        self.note_panel.bind_wheel()

    def _remove_note_rows(self, ids):
        """就地移除若干笔记行，不重建整列（文件夹行与其余笔记都不闪）。

        只销毁被删笔记自己的 wrap（连同它的跑马灯状态），再把新的首行上间距
        收回「首位小间距」——否则删掉第一行后，第二行会多出一段行间距。
        """
        ids = set(ids)
        for nid in list(ids):
            rec = self._rows.pop(nid, None)
            self._star_buttons.pop(nid, None)
            self._note_sel.discard(nid)
            try:
                self._stop_marquee("n:" + nid)
            except Exception:
                pass
            if rec is not None:
                try:
                    rec["wrap"].destroy()
                except Exception:
                    pass
        vis = self.visible_notes()
        if vis:
            first = self._rows.get(vis[0]["id"])
            if first is not None:
                first["wrap"].pack_configure(pady=(2, 0))
        self.note_panel.inner.update_idletasks()
        self.note_panel._update()
        self.note_panel.bind_wheel()

    def select_note(self, nid):
        prev = self.data.get("current_note")
        if prev == nid:
            # 点中「已是当前项」的笔记：同样要取消文件夹多选（否则这里直接 return 会漏掉同步）
            self._note_sel = {nid}
            self._note_anchor = nid
            self._sync_folder_sel_to_note(nid)
            self._restyle_notes()
            self.load_editor()
            return
        self.data["current_note"] = nid
        self._note_sel = {nid}
        self._note_anchor = nid
        # 点单条笔记：取消文件夹 Shift 多选，单独选中其对应文件夹
        self._sync_folder_sel_to_note(nid)
        # 先把上一条选中行强制还原为普通底色（防旧高亮残留），再统一刷新当前行
        if prev:
            self._restore_note_row(prev)
        # 就地高亮当前行，不重建整列 → 避免其它笔记闪烁
        self._restyle_notes()
        # 空闲时再刷一次（兜底：若后续事件又改动了行背景，这里会按最新状态纠正）
        try:
            self._after_idle(self._restyle_notes)
        except Exception:
            pass
        self.load_editor()

    def _restore_note_row(self, nid):
        """把指定笔记行就地还原为「未选中」底色（背景 + 文字色），不重建行、不闪烁。"""
        rec = self._rows.get(nid)
        if not rec:
            return
        p = self.pal
        rec["row"].configure(bg=p["list"])
        rec["row"]._base_bg = p["list"]
        rec["top"].configure(bg=p["list"])
        rec["right"].configure(bg=p["list"])
        rec["tl"].set_style(bg=p["list"], fg=p["text"])
        rec["pl"].configure(bg=p["list"], fg=p["soft"])
        rec["dl"].configure(bg=p["list"])
        star = rec["starred"]
        rec["sb"].configure(bg=p["list"], activebackground=p["list"],
                            fg=(p["star"] if star else p["soft"]),
                            activeforeground=(p["star"] if star else p["soft"]))
        self._paint_card(rec["row"], p["list"])

    def _set_note_active(self, nid):
        """就地切换笔记选中态（背景 + 文字色），不销毁重建行。"""
        p = self.pal
        for rid, rec in self._rows.items():
            is_act = (rid == nid)
            rbg = p["sel"] if is_act else p["list"]
            cbg = p["sel"] if is_act else p["list"]
            rec["row"].configure(bg=rbg)
            rec["row"]._base_bg = rbg
            rec["top"].configure(bg=rbg)
            rec["right"].configure(bg=rbg)
            rec["tl"].set_style(bg=cbg, fg=(p["selfg"] if is_act else p["text"]))
            rec["pl"].configure(bg=cbg, fg=p["soft"])
            rec["dl"].configure(bg=cbg)
            rec["sb"].configure(bg=cbg, activebackground=cbg,
                                fg=(p["star"] if rec["starred"] else p["soft"]),
                                activeforeground=(p["star"] if rec["starred"] else p["soft"]))
            self._paint_card(rec["row"], rbg)
            if is_act:
                self._active_title, self._active_preview, self._active_date = rec["tl"], rec["pl"], rec["dl"]

    def _restyle_notes(self):
        """统一刷新笔记行样式：当前(active) > 多选高亮 > 普通；不重建行、不闪烁。"""
        p = self.pal
        cur = self.data.get("current_note")
        valid = {n["id"] for n in self.data["notes"]}
        self._note_sel &= valid
        for rid, rec in self._rows.items():
            active = (rid == cur)
            seld = (rid in self._note_sel)
            rbg = p["sel"] if active else (p["hover"] if seld else p["list"])
            cbg = p["sel"] if active else (p["hover"] if seld else p["list"])
            rec["row"].configure(bg=rbg)
            rec["row"]._base_bg = rbg
            rec["top"].configure(bg=rbg)
            rec["right"].configure(bg=rbg)
            rec["tl"].set_style(bg=cbg, fg=(p["selfg"] if (active or seld) else p["text"]))
            rec["pl"].configure(bg=cbg, fg=p["soft"])
            rec["dl"].configure(bg=cbg)
            rec["sb"].configure(bg=cbg, activebackground=cbg,
                                fg=(p["star"] if rec["starred"] else p["soft"]),
                                activeforeground=(p["star"] if rec["starred"] else p["soft"]))
            self._paint_card(rec["row"], rbg)
            if active:
                self._active_title, self._active_preview, self._active_date = rec["tl"], rec["pl"], rec["dl"]

    def _note_select(self, e, nid):
        """左键+Shift：从锚点区间多选；+Ctrl：切换单个；否则单选（不加载）。"""
        shift = (e.state & 0x0001) != 0
        ctrl = (e.state & 0x0004) != 0
        ids = [n["id"] for n in self.visible_notes()]
        if shift and self._note_anchor and self._note_anchor in ids and nid in ids:
            a = ids.index(self._note_anchor)
            b = ids.index(nid)
            lo, hi = (a, b) if a <= b else (b, a)
            self._note_sel = set(ids[lo:hi + 1])
        elif ctrl:
            if nid in self._note_sel:
                self._note_sel.discard(nid)
            else:
                self._note_sel.add(nid)
            self._note_anchor = nid
        else:
            self._note_sel = {nid}
            self._note_anchor = nid
        # 注意：笔记自身的 Shift/Ctrl 多选不要同步文件夹。
        # 一旦同步会切换文件夹视图 → 列表重建 → 跨文件夹的选中项被「&= valid」过滤掉，
        # 表现为 Shift 区间多选只剩 1 条。文件夹侧的同步只由「普通点击」触发（见 select_note）。
        self._restyle_notes()

    def _delete_notes(self, nids):
        """批量删除选中的笔记。"""
        if not nids:
            return
        if not self._ask_confirm("删除", "确定删除选中的 %d 条笔记？" % len(nids),
                                 ok_text="删除", danger=True):
            return
        ids = set(nids)
        self.data["notes"] = [x for x in self.data["notes"] if x["id"] not in ids]
        self._note_sel = set()
        self._note_anchor = None
        cur_removed = self.data.get("current_note") in ids
        if cur_removed:
            self.data["current_note"] = None
        save_data(self.data)
        # 只移除被删的行 + 就地刷新计数：文件夹行与其余笔记都不重建 → 不闪烁
        self._remove_note_rows(ids)
        if cur_removed:
            self._active_title = self._active_preview = self._active_date = None
        self._refresh_folder_counts()
        self._restyle_notes()
        if cur_removed:
            self.load_editor()

    def current_note(self):
        nid = self.data.get("current_note")
        return next((n for n in self.data["notes"] if n["id"] == nid), None)

    # ---------- 拖拽移动笔记到文件夹 ----------
    def _release_stale_grab(self):
        """释放可能残留的局部 grab（只释放我们记录过的那一个控件，不动系统/菜单的 grab）。"""
        gw = getattr(self, "_grab_widget", None)
        if gw is None:
            return
        self._grab_widget = None
        try:
            if gw.winfo_exists():
                gw.grab_release()
        except Exception:
            pass

    def _note_press(self, e, nid):
        # 按住 Shift / Ctrl 进行多选（批量删除），不进入拖拽逻辑
        if (e.state & 0x0001) or (e.state & 0x0004):
            self._note_select(e, nid)
            return
        # 普通按下：**立刻**取消其它选中项，只留当前这一条。
        # 放在「按下」而不是「松手」做，是因为松手事件可能丢失（拖出窗口、被 grab 吞掉、
        # 中途又触发了别的流程），那样多选高亮就会残留；Finder / 资源管理器也是按下即收拢。
        if self._note_sel != {nid}:
            self._note_sel = {nid}
            self._note_anchor = nid
            self._restyle_notes()
        # 防呆：上一次拖拽若没走完释放流程，会残留局部 grab 占住鼠标事件，导致后续点击全部失效
        self._release_stale_grab()
        self._drag = {"nid": nid, "x": e.x_root, "y": e.y_root, "started": False, "grab": e.widget}
        # 本地 grab：拖拽过程中即使指针移出笔记行，也能持续收到 B1-Motion / 释放事件
        try:
            e.widget.grab_set()
            self._grab_widget = e.widget     # 记下来，下次按下时可兜底释放
        except Exception:
            pass

    def _note_motion(self, e, nid):
        d = getattr(self, "_drag", None)
        if not d or d["nid"] != nid:
            return
        if not d["started"]:
            # 阈值提高到 8px：人手点击常有轻微抖动，太小会误判成拖拽导致点不中
            if abs(e.x_root - d["x"]) > 8 or abs(e.y_root - d["y"]) > 8:
                d["started"] = True
                self._drag_start(nid)
        if d["started"]:
            self._drag_move(e.x_root, e.y_root)
            self._drag_highlight(e.x_root, e.y_root)

    def _note_release(self, e, nid):
        d = getattr(self, "_drag", None)
        if d and d["nid"] != nid:
            # 按下与松开落在不同行（或残留状态）→ 丢弃，避免卡住后续点击
            d = None
        gw = d.get("grab") if d else None
        try:
            if gw:
                gw.grab_release()
        except Exception:
            pass
        self._grab_widget = None
        moved = False
        if d and d.get("started"):
            fid = self._folder_under(e.x_root, e.y_root)
            self._drag_end()
            if fid and fid not in ("f_all", "f_star"):
                self._move_note_to_folder(nid, fid)
                moved = True
        # 关键：只有真正把笔记拖进了某个文件夹才跳过选中。
        # Shift / Ctrl 多选时：保留已选集合，只把当前笔记同步为编辑器加载对象（不重建多选）
        if (e.state & 0x0001) or (e.state & 0x0004):
            self.data["current_note"] = nid
            # 笔记 Shift/Ctrl 多选：只同步编辑器加载对象，保留多选集合、不动文件夹列表
            self._restyle_notes()
            self.load_editor()
        # 普通点击 / 拖拽中途松手：视为选中该笔记
        elif not moved:
            self.select_note(nid)
        self._drag = None

    def _drag_start(self, nid):
        n = next((x for x in self.data["notes"] if x["id"] == nid), None)
        title = self.trunc(self.title_of(n) if n else "笔记", 16)
        dlg = tk.Toplevel(self.root)
        dlg.overrideredirect(True)
        dlg.configure(bg=self.pal["sel"], bd=1, relief=tk.RAISED)
        tk.Label(dlg, text="   " + title, bg=self.pal["sel"], fg=self.pal["text"],
                 font=(FONT[0], 12, "bold")).pack(padx=10, pady=6)
        dlg.geometry("+%d+%d" % (self._drag["x"], self._drag["y"]))
        self._drag_win = dlg

    def _drag_move(self, x, y):
        if self._drag_win:
            self._drag_win.geometry("+%d+%d" % (x + 12, y + 8))

    def _drag_highlight(self, x, y):
        fid = self._folder_under(x, y)
        prev = getattr(self, "_drag_target", None)
        if prev == fid:
            return
        if prev and prev in self._folder_rows:
            self._folder_restore(prev)
        if fid and fid in self._folder_rows:
            row = self._folder_rows[fid]
            self._row_hover(row, self.pal["hover"])
            for ch in row.winfo_children():
                if isinstance(ch, tk.Label):
                    ch.configure(fg=self.pal["accent"])
            nl = self._folder_name_labels.get(fid)   # 名字是自绘控件，不在 row 直接子控件里
            if nl is not None:
                try:
                    nl.set_style(fg=self.pal["accent"])
                except Exception:
                    pass
        self._drag_target = fid

    def _folder_restore(self, fid):
        row = self._folder_rows.get(fid)
        if not row:
            return
        base = row._base_bg
        self._row_hover(row, base, fb=self.pal["side"])
        hl = (base == self.pal["sel"])
        for ch in row.winfo_children():
            if isinstance(ch, tk.Label):
                ch.configure(fg=(self.pal["selfg"] if hl else self.pal["text"]))
        self._style_folder_labels(fid, hl)

    def _drag_end(self):
        if getattr(self, "_drag_win", None):
            self._drag_win.destroy()
            self._drag_win = None
        prev = getattr(self, "_drag_target", None)
        if prev:
            self._folder_restore(prev)
        self._drag_target = None

    def _folder_under(self, x, y):
        for fid, row in self._folder_rows.items():
            if fid in ("f_all", "f_star"):
                continue
            ry = row.winfo_rooty()
            rh = row.winfo_height()
            rx = row.winfo_rootx()
            rw = row.winfo_width()
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                return fid
        return None

    def _move_note_to_folder(self, nid, fid):
        n = next((x for x in self.data["notes"] if x["id"] == nid), None)
        if not n:
            return
        n["folder"] = fid
        n["updated"] = __import__("time").time() * 1000
        save_data(self.data)
        # 就地刷新列表与所有文件夹计数（不重建文件夹行 → 不闪烁）
        self.refresh_notes()
        for f in self.data["folders"]:
            lbl = self._folder_count_labels.get(f["id"])
            if lbl:
                lbl.configure(text=str(len(self.folder_notes(f["id"]))))

    def _popup(self, event, nid):
        n = next((x for x in self.data["notes"] if x["id"] == nid), None)
        if not n:
            return
        # 右键时若该项不在当前多选集合中，则以它为唯一选中
        if nid not in self._note_sel:
            self._note_sel = {nid}
            self._note_anchor = nid
            self._restyle_notes()
        m = PopupMenu(self.root, self.pal)
        if len(self._note_sel) > 1:
            m.add("删除选中的 %d 条笔记" % len(self._note_sel), danger=True,
                  command=lambda: self._delete_notes(list(self._note_sel)))
            m.popup(event.x_root, event.y_root)
            return
        m.add("置顶" if not n["pinned"] else "取消置顶",
              command=lambda: self._popup_pin(nid))
        m.add("星标" if not n.get("starred", False) else "取消星标",
              command=lambda: self.toggle_star(nid))
        m.add_separator()
        m.add("删除笔记", danger=True, command=lambda: self._popup_delete(nid))
        m.popup(event.x_root, event.y_root)

    def _popup_pin(self, nid):
        self.data["current_note"] = nid
        self.toggle_pin()

    def _popup_delete(self, nid):
        self.data["current_note"] = nid
        self.delete_note()

    # ---------- 编辑器 ----------
    def refresh_all(self):
        self.refresh_folders()
        self.refresh_notes()
        self.load_editor()

    def load_editor(self):
        self.text.config(state=tk.NORMAL)
        for cb in self._checks:
            try:
                cb.destroy()
            except Exception:
                pass
        self._checks = []
        for blk in self._codes:
            try:
                blk.destroy()
            except Exception:
                pass
        self._codes = []
        self.text.delete("1.0", tk.END)
        n = self.current_note()
        if not n:
            self.text.config(state=tk.DISABLED)
            self._sync_jump_nav()
            return
        self._render_content(n["content"])
        # 换一篇就回到开头，浮动导航同步成「一开始只有向下可用」
        self.text.yview_moveto(0.0)
        self._after_idle(self._sync_jump_nav)
        self.text.edit_reset()
        self.text.edit_modified(False)
        self.text.config(state=tk.NORMAL)
        self.text.focus_set()

    def _render_content(self, content):
        for it in content:
            if it[0] == "t":
                self.text.insert(tk.END, it[1])
                tags = it[2] if len(it) > 2 else []
                for tg in tags:
                    self.text.tag_add(tg, "insert-1c", "insert")
            elif it[0] == "w":
                self._add_checkbutton(checked=bool(it[1]))
            elif it[0] == "code":
                self._add_code_block(lang=(it[1] if len(it) > 1 else ""),
                                     code=(it[2] if len(it) > 2 else ""))
        # 勾选项整行应用 done（删除线 + 变灰），由勾选态派生
        for cb in self._checks:
            if cb.var.get():
                idx = self.text.index(cb)
                line = idx.split(".")[0]
                self.text.tag_add("done", line + ".0", line + ".end")

    def _add_checkbutton(self, checked=False, index=tk.END):
        box = TodoBox(self.text, self, checked=checked)
        self._checks.append(box)
        self.text.window_create(index, window=box)
        return box

    def _on_todo_toggle(self, box):
        box.var.set(not box.var.get())
        box._draw()
        idx = self.text.index(box)
        line = idx.split(".")[0]
        ls, le = line + ".0", line + ".end"
        if box.var.get():
            self.text.tag_add("done", ls, le)
        else:
            self.text.tag_remove("done", ls, le)
        self.mark_modified()

    def on_edit(self, ev=None):
        if self.text.edit_modified():
            # 先补行级样式再序列化，保证续写出来的标题/正文标签被正确保存
            self._sync_line_tags()
            self.mark_modified()
            self.text.edit_modified(False)

    # 行级（整行生效）样式标签
    LINE_TAGS = ("title", "h2", "body")

    def _tag_covers_line(self, tag, ls, le):
        """标签是否连续覆盖 [ls, le) 整段（无缺口）。"""
        pos = ls
        while True:
            rng = self.text.tag_nextrange(tag, pos, le)
            if not rng:
                return False
            if self.text.compare(rng[0], ">", pos):
                return False                    # 中间有缺口
            pos = rng[1]
            if self.text.compare(pos, ">=", le):
                return True

    def _sync_line_tags(self):
        """把光标所在行「已有的行级样式」补满整行，解决续写掉样式的问题。

        Tk 只在**行中间**插入时继承复选框/标签；在**行尾或行首**插入的新字符
        不带任何标签（行尾的前一个字符是换行符，而换行符刻意不带 title，
        免得 title 的 spacing 在标题与正文之间插出空行）。
        所以「在标题后面继续打字」会掉成正文样式。这里每次编辑后把该行已存在的
        行级标签补齐到整行 —— 标题行续写仍是标题，正文行续写仍是正文。

        用户手动把某行设成别的行级样式（如标题行点「正文」）时，该行只有那一个
        行级标签，补齐的也是它自己，不会把用户的显式选择覆盖掉。
        回车换行由 _title_line_break 显式切正文，走的是新行，不受影响。
        """
        if not self.current_note():
            return
        ln = self.text.index("insert").split(".")[0]
        ls, le = ln + ".0", ln + ".end"
        if self.text.compare(ls, "==", le):
            return                              # 空行无需处理
        for tag in self.LINE_TAGS:
            if not self.text.tag_nextrange(tag, ls, le):
                continue                        # 该行没有这个行级样式
            if not self._tag_covers_line(tag, ls, le):
                self.text.tag_add(tag, ls, le)
            return                              # 只按该行已有的那一种补齐

    def mark_modified(self):
        n = self.current_note()
        if not n:
            return
        n["content"] = self.serialize()
        n["updated"] = __import__("time").time() * 1000
        save_data(self.data)
        self.text.edit_modified(False)
        self._update_active_row()

    def _update_active_row(self):
        """编辑时只就地更新当前笔记那一行的标题/预览/时间，避免整列重建导致闪烁。"""
        n = self.current_note()
        if not n or not self._active_title:
            return
        # 标题是自绘 Canvas（可按像素滚动），不能用 configure(text=...)：
        # 更新完整文字后重新按可用像素截断（编辑标题时列表标题实时跟随）
        rec = self._rows.get(n["id"])
        if rec:
            rec["row"]._full_name = self.title_of(n)
            self._fit_note_title(n["id"])
        self._active_preview.configure(
            text=self.trunc(self.preview_of(n), 22) or "暂无更多文字")
        self._active_date.configure(text=self._date_text(n))

    def serialize(self):
        # 逐字符记录当前标签，确保重开后格式精确还原
        active = set()
        out = []
        for key, value, index in self.text.dump("1.0", tk.END, window=True, tag=True, text=True, mark=False):
            if key == "tagon":
                if value not in ("sel", "done"):
                    active.add(value)
            elif key == "tagoff":
                if value not in ("sel", "done"):
                    active.discard(value)
            elif key == "text":
                for ch in value:
                    out.append(["t", ch, sorted(active)])
            elif key == "window":
                w = self.text.nametowidget(value)
                if isinstance(w, CodeBlock):
                    out.append(["code", w.lang, w.snapshot()])
                    continue
                checked = bool(w.var.get()) if hasattr(w, "var") else False
                out.append(["w", checked, sorted(active)])
        return out

    # ---------- 格式 ----------
    EMPH_TAGS = ("bold", "italic", "bold_italic")

    def _emph_state(self, a, b):
        """读范围内当前的「加粗 / 斜体」状态（粗斜体标签同时代表两者）。"""
        bold = bool(self.text.tag_nextrange("bold", a, b)) or \
            bool(self.text.tag_nextrange("bold_italic", a, b))
        italic = bool(self.text.tag_nextrange("italic", a, b)) or \
            bool(self.text.tag_nextrange("bold_italic", a, b))
        return bold, italic

    def _apply_emph(self, a, b, bold, italic):
        """把范围统一成「一种」强调标签，从根上避免 Tk 两个 font 标签互相覆盖。"""
        for t in self.EMPH_TAGS:
            self.text.tag_remove(t, a, b)
        if bold and italic:
            self.text.tag_add("bold_italic", a, b)
        elif bold:
            self.text.tag_add("bold", a, b)
        elif italic:
            self.text.tag_add("italic", a, b)

    def toggle_sel_tag(self, tag):
        if not self.current_note():
            return
        self.text.config(state=tk.NORMAL)
        sel = self.text.tag_ranges("sel")
        if sel:
            a, b = sel[0], sel[1]
            if tag in ("bold", "italic"):
                # 加粗 / 斜体：按「粗+斜」组合态处理，互不覆盖
                has_b, has_i = self._emph_state(a, b)
                nb = (not has_b) if tag == "bold" else has_b
                ni = (not has_i) if tag == "italic" else has_i
                self._apply_emph(a, b, nb, ni)
            elif tag == "underline":
                # 下划线颜色标签 u_* 自带 underline=True，因此「是否有下划线」
                # 要把它们一起算上，否则设过 U 颜色后点 U 关不掉。
                underlined = bool(self.text.tag_nextrange("underline", a, b)) or any(
                    self.text.tag_nextrange(t, a, b)
                    for t in self.text.tag_names() if t.startswith("u_"))
                if underlined:
                    self.text.tag_remove("underline", a, b)
                    for t in list(self.text.tag_names()):
                        if t.startswith("u_"):
                            self.text.tag_remove(t, a, b)
                else:
                    self.text.tag_add("underline", a, b)
            elif self.text.tag_nextrange(tag, a, b):
                self.text.tag_remove(tag, a, b)
            else:
                self.text.tag_add(tag, a, b)
        else:
            # 未选中文字：切换「接下来输入」的强调态，同样支持叠加
            if tag in ("bold", "italic"):
                cur = set(self.text.tag_names("insert"))
                has_b = bool(cur & {"bold", "bold_italic"})
                has_i = bool(cur & {"italic", "bold_italic"})
                nb = (not has_b) if tag == "bold" else has_b
                ni = (not has_i) if tag == "italic" else has_i
                for t in self.EMPH_TAGS:
                    self.text.tag_remove(t, "insert")
                if nb and ni:
                    self.text.tag_add("bold_italic", "insert")
                elif nb:
                    self.text.tag_add("bold", "insert")
                elif ni:
                    self.text.tag_add("italic", "insert")
            else:
                self.text.tag_add(tag, "insert")
        self.mark_modified()

    def apply_line_tag(self, tag):
        if not self.current_note():
            return
        self.text.config(state=tk.NORMAL)
        line = self.text.index("insert").split(".")[0]
        ls, le = line + ".0", line + ".end"
        for t in ("title", "h2", "body"):
            self.text.tag_remove(t, ls, le)
        if tag != "body":
            self.text.tag_add(tag, ls, le)
        self.mark_modified()

    def insert_bullet(self):
        if not self.current_note():
            return
        self.text.config(state=tk.NORMAL)
        line = self.text.index("insert").split(".")[0]
        self.text.insert(line + ".0", "•  ")
        self.mark_modified()

    def insert_check(self):
        if not self.current_note():
            return
        self.text.config(state=tk.NORMAL)
        sel = self.text.tag_ranges("sel")
        # 选中文字时，勾选框插在选区起点（文字前面）；否则插在当前光标处
        pos = sel[0] if sel else tk.INSERT
        pos_s = str(pos)
        self.text.mark_set("insert", pos)
        self._add_checkbutton(checked=False, index=pos)
        # 勾选框后面补一个空格，再接原文字
        self.text.insert(self.text.index(pos_s + " +1c"), " ")
        self.mark_modified()

    # ---------- 代码块 ----------
    def code_font(self):
        """代码块的等宽字体与字号：优先系统里装了的第一款等宽字体，字号比正文小 1 号。"""
        if not self._code_family:
            fam = FONT[0]
            try:
                import tkinter.font as _f
                avail = set(x.lower() for x in _f.families())
                for c in CODE_FONTS:
                    if c.lower() in avail:
                        fam = c
                        break
            except Exception:
                pass
            self._code_family = fam
        return self._code_family, max(9, int(self.body_size) - 1)

    def _code_width(self):
        """代码块可用宽度：编辑区宽度减去左右留白。

        右侧要按「滚动条 + 间距 + 浮动导航」的实际占位来留（正文只靠默认右留白，
        文字行看不出边界；代码块有底色和边框，被浮动导航压住会一眼看到）。
        """
        w = self.text.winfo_width()
        if w <= 1:
            w = self.editor_frame.winfo_width() or 900
        right = TEXT_PADX
        try:
            sb = self._vscroll.winfo_reqwidth() or 12
            right = max(right, sb + JUMP_GAP + JUMP_W + 6)
        except Exception:
            pass
        return max(120, w - TEXT_PADX - right - 2)

    def _sync_code_width(self, fit=True):
        """窗口缩放后让每个代码块重新对齐编辑区宽度（并按新宽度重算高度）。"""
        if not self._codes:
            return
        w = self._code_width()
        for b in list(self._codes):
            try:
                if b.winfo_exists():
                    b.set_width(w, fit=fit)
            except Exception:
                pass

    def _mk_code_ui(self, parent):
        """工具栏的「代码」按钮：点开选语言，选完在光标处插入代码块。"""
        b = RoundButton(parent, self.pal, "{} 代码 ▾", command=self._pick_code_lang)
        b.pack(side=tk.LEFT, padx=3, pady=8)
        b.bind_events()
        self.fmt_buttons.append(b)
        self._code_btn = b

    def _pick_code_lang(self):
        if not self.current_note():
            self._show_info("提示", "请先选择一条笔记。")
            return
        btn = self._code_btn
        try:
            if btn is not None and btn.winfo_exists():
                x = btn.winfo_rootx()
                y = btn.winfo_rooty() + btn.winfo_height() + 2
            else:
                x, y = self.root.winfo_pointerx(), self.root.winfo_pointery()
        except Exception:
            return
        m = PopupMenu(self.root, self.pal)
        for label, key in CODE_LANGS:
            m.add(label, command=lambda k=key: self.insert_code(k))
        m.popup(x, y)

    def insert_code(self, lang=""):
        """在当前光标处插入一个代码块：整块独占若干行，插好即把光标放进代码里。

        若当前有选中文字，则把选中的内容直接变成代码（免得先删再粘）。
        """
        if not self.current_note():
            self._show_info("提示", "请先选择一条笔记。")
            return
        self.text.config(state=tk.NORMAL)
        picked = ""
        sel = self.text.tag_ranges("sel")
        if sel:
            picked = self.text.get(sel[0], sel[1]).rstrip("\n")
            self.text.delete(sel[0], sel[1])
        # 代码块要独占行：光标不在行首时先补个换行，免得和前面的正文挤在同一行
        if self.text.index("insert").split(".")[1] != "0":
            self.text.insert(tk.INSERT, "\n")
        blk = self._add_code_block(lang=lang, code=picked, index=tk.INSERT)
        self.text.insert(tk.INSERT, "\n")     # 块后留一行，方便继续写正文
        self.mark_modified()
        blk.focus_code()

    def _add_code_block(self, lang="", code="", index=tk.END):
        blk = CodeBlock(self.text, self, lang=lang, code=code)
        self._codes.append(blk)
        self.text.window_create(index, window=blk, padx=0, pady=6)
        # 宽度必须先定：高度是按「这个宽度下代码的实际显示行数」算出来的
        blk.set_width(self._code_width(), fit=False)
        self._after_idle(blk.fit)
        return blk

    def _remove_code_block(self, blk):
        """删除一个代码块（连同它后面那个换行，免得留下一整行空白）。"""
        try:
            idx = self.text.index(blk)
        except Exception:
            return
        self.text.config(state=tk.NORMAL)
        self.text.delete(idx)          # 删掉嵌入式窗口本身（Tk 会销毁该控件）
        try:
            if self.text.get(idx, "%s +1c" % idx) == "\n":
                self.text.delete(idx)
        except Exception:
            pass
        try:
            self._codes.remove(blk)
        except ValueError:
            pass
        self.mark_modified()

    def insert_numbered(self):
        if not self.current_note():
            return
        self.text.config(state=tk.NORMAL)
        line = self.text.index("insert").split(".")[0]
        self.text.insert(line + ".0", "1.  ")
        self.mark_modified()

    def insert_triangle(self):
        if not self.current_note():
            return
        self.text.config(state=tk.NORMAL)
        line = self.text.index("insert").split(".")[0]
        self.text.insert(line + ".0", TRI_MARK)
        self.mark_modified()

    def _title_line_break(self):
        """首行（标题行）回车：新行自动切成正文样式，标题只留第一行。

        Tk 的插入规则：插入点落在标签范围「中间」会继承该标签，落在「末尾」不会。
        所以光标在首行末尾回车时新行本来就没有 title；但光标停在标题中间时，
        后半段文字会连同行级标签一起落到新行 —— 这里显式把新行的标题标签换成正文。
        """
        sel = self.text.tag_ranges("sel")
        if sel:
            self.text.delete(sel[0], sel[1])
        self.text.insert(tk.INSERT, "\n")
        ls, le = "2.0", "2.end"
        self.text.tag_remove("title", ls, le)
        self.text.tag_remove("h2", ls, le)
        self.text.tag_add("body", ls, le)
        self.mark_modified()
        return "break"

    def _line_prefix(self, ln):
        """返回当前行开头的列表标记类型与文本：('bullet'/'triangle'/'numbered'/'check', marker)。"""
        try:
            st = self.text.dump(ln + ".0", ln + ".end", window=True, text=True)
        except Exception:
            st = []
        first = None
        for key, value, _idx in st:
            if key in ("text", "window"):
                first = (key, value)
                break
        if first is None:
            return (None, "")
        if first[0] == "window":
            # 代码块也是 window，但它不是待办：回车交给默认行为（块内换行由代码区自己处理）
            if isinstance(self._window_at_line_start(ln), CodeBlock):
                return (None, "")
            return ("check", "")
        txt = first[1]
        if txt.startswith("•  "):
            return ("bullet", "•  ")
        # 新符号优先；旧符号（存量笔记）同样识别为三角列表
        if txt.startswith(TRI_MARK):
            return ("triangle", TRI_MARK)
        if txt.startswith(TRI_MARK_OLD):
            return ("triangle", TRI_MARK_OLD)
        m = re.match(r"^(\d+)\.\s+", txt)
        if m:
            return ("numbered", m.group(0))
        return (None, "")

    def _window_at_line_start(self, ln):
        target = ln + ".0"
        for wname in self.text.window_names():
            try:
                if self.text.index(wname) == target:
                    return self.text.nametowidget(wname)
            except Exception:
                pass
        return None

    def _editor_return(self, event):
        """回车：若当前行是列表/待办，自动续接同格式；空列表项回车则结束列表。"""
        if not self.current_note():
            return
        self.text.config(state=tk.NORMAL)
        ln = self.text.index("insert").split(".")[0]
        kind, marker = self._line_prefix(ln)
        if kind is None:
            # 首行是标题行：回车后新行自动切换为正文样式
            if ln == "1":
                return self._title_line_break()
            return  # 普通回车：交给 Tk 默认行为
        line_txt = self.text.get(ln + ".0", ln + ".end")
        if kind == "check":
            box = self._window_at_line_start(ln)
            body = line_txt.strip()
            if body == "" and box is not None:
                # 空待办行 → 结束列表：删掉勾选框，保留普通换行
                self.text.delete(self.text.index(box))
                self.mark_modified()
                return
            # 续接待办
            self.text.insert(tk.INSERT, "\n")
            self._add_checkbutton(checked=False, index=tk.INSERT)
            self.text.insert(tk.INSERT, " ")
            self.mark_modified()
            return "break"
        # 去掉行首标记，判断是否为空列表项
        strip_body = line_txt[len(marker):] if (marker and line_txt.startswith(marker)) else line_txt
        if strip_body.strip() == "":
            # 空列表项 → 结束列表：删除标记，保留普通换行
            if marker:
                self.text.delete(ln + ".0", ln + ".%d" % len(marker))
            self.mark_modified()
            return
        # 续接同格式
        if kind == "bullet":
            self.text.insert(tk.INSERT, "\n•  ")
        elif kind == "triangle":
            # 沿用当前行的标记：新符号续新符号，存量旧符号仍续旧符号（不改动存量）
            self.text.insert(tk.INSERT, "\n" + (marker or TRI_MARK))
        elif kind == "numbered":
            cur = int(marker.split(".")[0])
            self.text.insert(tk.INSERT, "\n%d.  " % (cur + 1))
        self.mark_modified()
        return "break"

    # ---------- 操作 ----------
    def new_note(self):
        f = self.data["current_folder"]
        # 智能文件夹不能当真实归属：在"星标笔记"下新建时若记成 f_star，
        # 这条笔记会不属于任何真实文件夹（只在"全部"里出现），所以一律落到"工作"。
        folder = "f_work" if f in ("f_all", "f_mac", "f_star") else f
        import time
        now = time.strftime("%Y-%m-%d", time.localtime())
        n = {"id": uid(), "folder": folder,
             "content": _t(now, ["title"]) + [["t", "\n", []]],
             "pinned": False, "created": time.time() * 1000,
             "updated": time.time() * 1000}
        self.data["notes"].insert(0, n)
        switched = f in ("f_all", "f_mac", "f_star")
        if switched:
            self.data["current_folder"] = folder
            self.refresh_folders()
        # 新建即选中：同时重置多选集合，否则上一条笔记仍留在 _note_sel 里，
        # _restyle_notes 会把它刷成「多选高亮」，看起来就是"旧的没还原、还是选中"。
        self.data["current_note"] = n["id"]
        self._note_sel = {n["id"]}
        self._note_anchor = n["id"]
        save_data(self.data)
        if switched:
            # 智能文件夹里新建会切换视图（列表内容整体变了）→ 只能整列重建
            self.refresh_notes()
        else:
            # 普通文件夹：只把新行插到队首，其余笔记原地保留，不再整列重建闪烁
            self._insert_note_row(n)
        # 文件夹行未重建，计数不会自动更新 → 就地刷新（否则新笔记不计入条数）
        self._refresh_folder_counts()
        self._restyle_notes()
        self.load_editor()
        self.text.config(state=tk.NORMAL)
        self.text.focus_set()
        self.text.mark_set("insert", "2.0")

    def delete_note(self):
        n = self.current_note()
        if not n:
            self._show_info("提示", "请先在中间列表选择一条笔记。")
            return
        if not self._ask_confirm("删除", "确定删除这条笔记？", ok_text="删除", danger=True):
            return
        nid = n["id"]
        self.data["notes"] = [x for x in self.data["notes"] if x["id"] != nid]
        self.data["current_note"] = None
        self._note_sel.discard(nid)
        save_data(self.data)
        # 只移除这一行 + 就地刷新计数：文件夹行与其余笔记都不重建 → 不闪烁
        if nid in self._rows:
            self._remove_note_rows([nid])
        else:
            self.refresh_notes()
        self._active_title = self._active_preview = self._active_date = None
        self._refresh_folder_counts()
        self._restyle_notes()
        self.load_editor()

    def toggle_pin(self):
        n = self.current_note()
        if not n:
            return
        n["pinned"] = not n["pinned"]
        n["updated"] = __import__("time").time() * 1000
        save_data(self.data)
        self.refresh_notes()
        self.load_editor()

    def toggle_star(self, nid=None):
        n = self.current_note() if nid is None else next(
            (x for x in self.data["notes"] if x["id"] == nid), None)
        if not n:
            self._show_info("提示", "请先选择一条笔记。")
            return
        n["starred"] = not n.get("starred", False)
        save_data(self.data)
        # 就地更新文件夹计数（不重建文件夹行 → 不闪烁）
        self._refresh_folder_counts()
        # 在“星标笔记”文件夹下取消星标 → 该条需移出列表，必须重建
        if self.data["current_folder"] == "f_star" and not n["starred"]:
            self.refresh_notes()
            return
        # 其余情况：只就地更新那一行星标按钮的图标，避免整列重建导致闪烁
        sb = self._star_buttons.get(n["id"])
        if sb:
            starred = n["starred"]
            sb.configure(text="★" if starred else "☆",
                         fg=(self.pal["star"] if starred else self.pal["soft"]))

    def new_folder(self):
        name = self._ask_text("新建文件夹", "文件夹名称：", "新文件夹")
        if not name:
            return
        f = {"id": uid(), "name": name.strip()}
        self.data["folders"].append(f)
        save_data(self.data)
        # 只追加新行，不整列重建 → 已存在的行（全部笔记/星标…）不会闪烁
        self._make_folder_row(f)
        self.folder_panel.inner.update_idletasks()
        self.folder_panel._update()
        self.folder_panel.bind_wheel()
        self._restyle_folders()
        self._update_folder_total()

    def del_folder(self, fid=None):
        target = fid or self.data["current_folder"]
        f = next((x for x in self.data["folders"] if x["id"] == target), None)
        if not f or f.get("smart"):
            self._show_info("提示", "智能文件夹「全部笔记 / 星标笔记」不能删除。")
            return
        cnt = len(self.folder_notes(f["id"]))
        # 提示固定两行：名称按可用宽度截断（过长用省略号），避免折出第三行
        try:
            fnt = tkfont.Font(family=FONT[0], size=12)
            def fit(head, tail):
                return self._ellipsize(
                    f["name"], DLG_TEXT_W - fnt.measure(head) - fnt.measure(tail), fnt)[0]
        except Exception:
            fnt = None
            def fit(head, tail):
                return f["name"]
        if cnt:
            head, tail = "文件夹「", "」下有 %d 条笔记。" % cnt
            msg = "%s%s%s\n连同笔记一起删除，是否继续？" % (head, fit(head, tail), tail)
        else:
            head, tail = "确定删除文件夹「", "」？"
            msg = "%s%s%s" % (head, fit(head, tail), tail)
        if not self._ask_confirm("删除文件夹", msg, ok_text="删除", danger=True):
            return
        if cnt:
            dropped = {n["id"] for n in self.data["notes"] if n["folder"] == f["id"]}
            self.data["notes"] = [x for x in self.data["notes"] if x["folder"] != f["id"]]
            self._note_sel -= dropped
            if self.data.get("current_note") in dropped:
                self.data["current_note"] = None
        self.data["folders"] = [x for x in self.data["folders"] if x["id"] != f["id"]]
        if self.data["current_folder"] == f["id"]:
            self.data["current_folder"] = "f_all"
        save_data(self.data)
        self.refresh_all()

    def rename_folder(self, fid):
        f = next((x for x in self.data["folders"] if x["id"] == fid), None)
        if not f:
            return
        name = self._ask_text("重命名文件夹", "文件夹名称：", f["name"])
        if not name:
            return
        f["name"] = name.strip()
        save_data(self.data)
        # 就地改该行文字，不整列重建 → 不闪烁
        row = self._folder_rows.get(fid)
        lbl = self._folder_name_labels.get(fid)
        if row is not None and lbl is not None and lbl.winfo_exists():
            row._full_name = f["name"]
            row._name_clipped = False
            lbl.set_text(f["name"], 0)
            self._fit_folder_name(fid)      # 新名字重新按像素截断
        else:
            self.refresh_folders()

    def _folder_popup(self, event, fid):
        f = next((x for x in self.data["folders"] if x["id"] == fid), None)
        if not f:
            return
        # 右键时若该项不在当前多选集合中，则以它为唯一选中
        if fid not in self._folder_sel:
            self._folder_sel = {fid}
            self._folder_anchor = fid
            self._restyle_folders()
        m = PopupMenu(self.root, self.pal)
        if len(self._folder_sel) > 1:
            m.add("删除选中的 %d 个文件夹" % len(self._folder_sel), danger=True,
                  command=lambda: self._delete_folders(list(self._folder_sel)))
        else:
            m.add("重命名", command=lambda: self.rename_folder(fid))
            if not f.get("smart"):
                m.add("移动笔记到…", command=lambda: self._move_notes_out(fid))
                m.add_separator()
                m.add("删除文件夹", danger=True, command=lambda: self.del_folder(fid))
            else:
                m.add("删除文件夹（智能文件夹不可删）", enabled=False)
        m.popup(event.x_root, event.y_root)

    def _move_notes_out(self, fid):
        """右键 → 移动笔记：把该文件夹下的笔记全部移到另一个文件夹。"""
        f = next((x for x in self.data["folders"] if x["id"] == fid), None)
        if not f:
            return
        if f.get("smart"):
            self._show_info("提示", "智能文件夹（全部笔记 / 星标笔记）不能整体移动。")
            return
        notes = self.folder_notes(fid)
        if not notes:
            self._show_info("提示", "文件夹「%s」下没有笔记。" % f["name"])
            return
        targets = [x for x in self.data["folders"] if not x.get("smart") and x["id"] != fid]
        if not targets:
            self._show_info("提示", "没有其他文件夹可以移入。")
            return
        tid = self._ask_choose_folder(
            "移动笔记",
            "把「%s」下的 %d 条笔记全部移动到：" % (f["name"], len(notes)),
            targets)
        if not tid:
            return
        now = __import__("time").time() * 1000
        for n in notes:
            n["folder"] = tid
            n["updated"] = now
        # 正在看源文件夹时，移走的条目已不在列表里 → 清掉选中，避免高亮指向看不见的条目；
        # 在其它文件夹（含"全部"）下这些条目仍可见，保持原选中不动。
        if self.data.get("current_folder") == fid:
            self.data["current_note"] = None
        save_data(self.data)
        self.refresh_all()

    # ---------- 自定义对话框 ----------
    def _center_dialog(self, win, min_w=360):
        """对话框居中于主窗口，并保证最小宽度。

        Windows 的标题栏文字会按窗口宽度裁剪：simpledialog 只按 prompt 计算宽度，
        窗口太窄时「新建文件夹」会被裁成只剩一个「新」字，所以这里自己定宽。

        可重复调用：内容高度变化后（如设置里预览文字换行变高）再调一次，
        窗口会跟着伸缩并保持居中——否则固定高度的窗口会把底部按钮挤出画面。
        先放开 minsize 下限，否则上一次的大尺寸会把窗口卡住、缩不回来。
        """
        try:
            win.update_idletasks()
            win.minsize(1, 1)
            ww = max(win.winfo_reqwidth(), min_w)
            wh = win.winfo_reqheight()
            rx = self.root.winfo_rootx() or 0
            ry = self.root.winfo_rooty() or 0
            rw = self.root.winfo_width() or 900
            rh = self.root.winfo_height() or 620
            x = max(rx + (rw - ww) // 2, 0)
            y = max(ry + (rh - wh) // 3, 0)
            scr_h = win.winfo_screenheight() or 0
            if scr_h:
                # 别让对话框底缘（含确定/取消按钮）伸出屏幕
                y = min(y, max(scr_h - wh, 0))
            win.geometry("%dx%d+%d+%d" % (ww, wh, x, y))
            win.minsize(ww, wh)
        except Exception:
            pass

    def _dlg_body(self, title):
        """建一个主题化的模态对话框外壳（不含内容）。"""
        p = self.pal
        win = tk.Toplevel(self.root)
        win.title(title)
        win.configure(bg=p["top"])
        win.resizable(False, False)
        win.transient(self.root)
        return win

    def _dlg_buttons(self, win, ok_cmd, cancel_cmd=None, ok_text="确定", danger=False):
        """对话框底部的按钮行；cancel_cmd 为 None 时只显示一个确定按钮。"""
        p = self.pal
        btns = tk.Frame(win, bg=p["top"])
        btns.pack(fill=tk.X, padx=16, pady=(12, 14))
        if cancel_cmd is not None:
            tk.Button(btns, text="取消", width=8, bg=p["editor"], fg=p["soft"], bd=1,
                      relief=tk.FLAT, font=(FONT[0], 11), cursor="hand2",
                      activebackground=p["hover"], activeforeground=p["selfg"],
                      command=cancel_cmd).pack(side=tk.RIGHT, padx=(8, 0))
        # 确定：默认强调色实底（危险操作改红色），悬停/按下向深色递进（不闪系统灰）
        ok_bg = MENU_DANGER if danger else p["accent"]
        ok_active = (_mix_hex(ok_bg, "#000000", 0.12) if danger
                     else _mix_hex(p["accent"], p["selfg"], BTN_PRESS_MIX))
        tk.Button(btns, text=ok_text, width=8, bg=ok_bg, fg="#ffffff", bd=0,
                  relief=tk.FLAT, font=(FONT[0], 11, "bold"), cursor="hand2",
                  activebackground=ok_active, activeforeground="#ffffff",
                  command=ok_cmd).pack(side=tk.RIGHT)
        return btns

    def _ask_confirm(self, title, message, ok_text="确定", danger=False):
        """主题化确认对话框（替代系统 messagebox.askyesno）。

        与「新建/重命名文件夹」用同一套外壳与按钮样式；Enter 确认、Esc/关窗取消，
        返回 True/False。
        """
        p = self.pal
        win = self._dlg_body(title)
        res = {"v": False}
        # 按最长一行的实际像素宽度设置换行宽度：短提示不会被硬拉宽，
        # 长提示（名称已按 DLG_TEXT_W 截断）也不会再折出第三行。
        try:
            fnt = tkfont.Font(family=FONT[0], size=12)
            wrap = max([fnt.measure(line) for line in message.split("\n")] + [1])
        except Exception:
            wrap = DLG_TEXT_W
        tk.Label(win, text=message, bg=p["top"], fg=p["text"], anchor="w",
                 justify="left", font=(FONT[0], 12), wraplength=wrap).pack(
                     fill=tk.X, padx=16, pady=(16, 6))

        def ok(_=None):
            res["v"] = True
            win.destroy()

        def cancel(_=None):
            res["v"] = False
            win.destroy()

        self._dlg_buttons(win, ok, cancel, ok_text=ok_text, danger=danger)
        win.bind("<Return>", ok)
        win.bind("<Escape>", cancel)
        win.protocol("WM_DELETE_WINDOW", cancel)
        self._center_dialog(win)
        win.grab_set()
        win.wait_window()
        return res["v"]

    def _show_alert(self, title, message, kind="info"):
        """主题化提示 / 错误框（替代 messagebox.showinfo / showerror）。

        单个「确定」按钮；信息用强调色圆标，错误用红色感叹号。正文超过
        DLG_TEXT_W 时自动换行（备份路径等长文本不会把窗口撑到屏幕外）。
        """
        p = self.pal
        win = self._dlg_body(title)
        body = tk.Frame(win, bg=p["top"])
        body.pack(fill=tk.X, padx=16, pady=(16, 6))
        color = MENU_DANGER if kind == "error" else p["accent"]
        ic = tk.Canvas(body, width=20, height=20, bd=0,
                       highlightthickness=0, bg=p["top"])
        ic.pack(side=tk.LEFT, anchor="n", padx=(0, 10), pady=(1, 0))
        ic.create_oval(1, 1, 19, 19, fill=color, outline="")
        ic.create_text(10, 10, text=("!" if kind == "error" else "i"),
                       fill="#ffffff", font=(FONT[0], 11, "bold"))
        tk.Label(body, text=message, bg=p["top"], fg=p["text"], anchor="w",
                 justify="left", font=(FONT[0], 12), wraplength=DLG_TEXT_W).pack(
                     side=tk.LEFT, fill=tk.X, expand=True)

        def ok(_=None):
            win.destroy()

        self._dlg_buttons(win, ok)
        win.bind("<Return>", ok)
        win.bind("<Escape>", ok)
        win.protocol("WM_DELETE_WINDOW", ok)
        self._center_dialog(win)
        win.grab_set()
        win.wait_window()

    def _show_info(self, title, message):
        self._show_alert(title, message, "info")

    def _show_error(self, title, message):
        self._show_alert(title, message, "error")

    def _ask_text(self, title, prompt, initial=""):
        """输入文本对话框；返回去掉首尾空格的字符串，取消返回 None。"""
        p = self.pal
        win = self._dlg_body(title)
        res = {"v": None}
        tk.Label(win, text=prompt, bg=p["top"], fg=p["text"], anchor="w",
                 font=(FONT[0], 12)).pack(fill=tk.X, padx=16, pady=(16, 6))
        var = tk.StringVar(value=initial)
        e = tk.Entry(win, textvariable=var, font=(FONT[0], 12), bg=p["editor"],
                     fg=p["text"], insertbackground=p["accent"], relief=tk.FLAT,
                     highlightthickness=1, highlightbackground=p["line"],
                     highlightcolor=p["accent"], width=30)
        e.pack(fill=tk.X, padx=16, ipady=5)

        def ok(_=None):
            res["v"] = var.get().strip()
            win.destroy()

        def cancel(_=None):
            res["v"] = None
            win.destroy()

        self._dlg_buttons(win, ok, cancel)
        win.bind("<Return>", ok)
        win.bind("<Escape>", cancel)
        win.protocol("WM_DELETE_WINDOW", cancel)
        self._center_dialog(win)
        e.select_range(0, tk.END)
        e.focus_set()
        win.grab_set()
        win.wait_window()
        return res["v"]

    def _ask_choose_folder(self, title, prompt, options):
        """从文件夹列表里选一个；返回其 id，取消返回 None。"""
        p = self.pal
        win = self._dlg_body(title)
        res = {"v": None}
        tk.Label(win, text=prompt, bg=p["top"], fg=p["text"], anchor="w", justify="left",
                 font=(FONT[0], 12), wraplength=340).pack(fill=tk.X, padx=16, pady=(16, 8))
        lb = tk.Listbox(win, bg=p["editor"], fg=p["text"], font=(FONT[0], 12), bd=0,
                        highlightthickness=1, highlightbackground=p["line"],
                        highlightcolor=p["accent"], selectbackground=p["sel"],
                        selectforeground=p["selfg"], activestyle="none",
                        exportselection=False, height=min(max(len(options), 4), 9))
        for o in options:
            lb.insert(tk.END, "%s（%d 条）" % (o["name"], len(self.folder_notes(o["id"]))))
        lb.pack(fill=tk.X, padx=16)
        lb.selection_set(0)
        lb.activate(0)

        def ok(_=None):
            sel = lb.curselection()
            if not sel:
                return
            res["v"] = options[sel[0]]["id"]
            win.destroy()

        def cancel(_=None):
            res["v"] = None
            win.destroy()

        self._dlg_buttons(win, ok, cancel)
        lb.bind("<Double-Button-1>", ok)
        win.bind("<Return>", ok)
        win.bind("<Escape>", cancel)
        win.protocol("WM_DELETE_WINDOW", cancel)
        self._center_dialog(win)
        lb.focus_set()
        win.grab_set()
        win.wait_window()
        return res["v"]


def main():
    root = tk.Tk()
    NotesApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
