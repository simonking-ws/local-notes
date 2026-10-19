# -*- coding: utf-8 -*-
"""素笺 —— PDF 导出：用 Windows GDI 排一遍版，再把页面位图拼成 PDF。

## 为什么是「页面位图」而不是嵌字体

零依赖是这个项目的底线，而 PDF 里要让中文像样，只有两条路：

1. **嵌 TrueType 子集**：得自己实现子集化（glyf / loca / cmap / hmtx 全部重算）；
   不做子集就得把整套 10MB 的雅黑塞进每一份导出 —— 一条便签变成 13MB 的 PDF。
2. **先把字画成图，再把图画进 PDF**：GDI 是系统自带的（ctypes 直接调，不算第三方），
   字体、字号、行距用的就是屏幕上那一套，看到什么样，PDF 里就是什么样。

这里走第 2 条。代价是 PDF 里没有文字层，复制/检索取不到字；换来的是
**零依赖、字形与界面一致、文件小**（144dpi 的一页 A4 压缩后约 100–300KB）。

## 层次

    blocks ──排版(单位 pt)──> 页面[绘制指令] ──GDI 绘制──> 位图 ──zlib──> PDF

排版与设备无关（全部按 pt 算），绘制时才乘 DPI。`build_pdf()` 是纯函数，
不碰 GDI —— 传几张现成的位图进去就能单测，Windows 之外也能跑。
"""
import os
import zlib

# A4（pt）。1pt = 1/72 inch，也是 PDF 的默认坐标系。
PAGE_W_PT = 595.28
PAGE_H_PT = 841.89
MARGIN_PT = 54.0          # 约 19mm，和 Word 的默认页边距接近
DPI = 144                 # 位图密度：屏幕上看清晰、打印也够（A4 ≈ 1190×1684）

# 版式比例。跟界面对得上：H1 1.35、H2 1.15（见 app_ui.H1_SCALE）
LINE_RATIO = 1.70         # 行高 = 字号 × 1.7（打印稿比屏幕更需要行间呼吸）
H1_RATIO, H2_RATIO = 1.35, 1.15
CODE_RATIO = 0.94         # 代码比正文小半号

# GDI 常量
ANTIALIASED_QUALITY = 4
DEFAULT_CHARSET = 1
TA_BASELINE = 24
TRANSPARENT = 1
DIB_RGB_COLORS = 0

# 行首禁则：这几个标点不许顶到行首（中文排版的基本要求）
NO_LINE_START = set("，。、；：？！）】》」』”’,.;:?!)]}%")
# 行尾禁则：开括号不许留在行尾
NO_LINE_END = set("（【《「『“‘([{")


def available():
    """本机能不能导出 PDF（只有 Windows 带 GDI）。"""
    return os.name == "nt"


# ---------- 颜色 ----------

def _rgb(hexv, default=(34, 34, 34)):
    s = (hexv or "").lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except Exception:
        return default


def _mix(c1, c2, t):
    return tuple(int(round(a + (b - a) * t)) for a, b in zip(c1, c2))


def _colorref(rgb):
    r, g, b = rgb
    return r | (g << 8) | (b << 16)


# ---------- GDI 薄封装 ----------

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    class _SIZE(ctypes.Structure):
        _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]

    class _RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class _BMIH(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                    ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD),
                    ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long),
                    ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

    class _BMI(ctypes.Structure):
        _fields_ = [("bmiHeader", _BMIH), ("bmiColors", wintypes.DWORD * 3)]

    # ⚠️ 两个 DLL 必须是**模块级单例**：ctypes 的函数原型（argtypes）挂在
    # 「DLL 实例的函数对象」上，每 new 一个 WinDLL 就是一套新函数，
    # 在 A 上设的原型对 B 不生效 —— 指针参数会被当成 32 位 int 传，直接溢出。
    _DLLS = {}

    def _dlls():
        if not _DLLS:
            _DLLS["gdi"] = ctypes.WinDLL("gdi32", use_last_error=True)
            _DLLS["user"] = ctypes.WinDLL("user32", use_last_error=True)
        return _DLLS["gdi"], _DLLS["user"]


class Gdi(object):
    """一个内存 DC + 字体缓存：测量和绘制共用（绘制时把页面 DIB 选进来）。"""

    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("GDI 只在 Windows 上可用")
        g, u = _dlls()
        self.g, self.u = g, u
        H = ctypes.c_void_p
        g.CreateCompatibleDC.argtypes = [H]
        g.CreateCompatibleDC.restype = H
        g.CreateFontW.argtypes = ([ctypes.c_int] * 5
                                  + [wintypes.DWORD] * 4
                                  + [wintypes.BYTE, wintypes.BYTE,
                                     wintypes.BYTE, wintypes.BYTE,
                                     ctypes.c_wchar_p])
        g.CreateFontW.restype = H
        g.SelectObject.argtypes = [H, H]
        g.SelectObject.restype = H
        g.DeleteObject.argtypes = [H]
        g.DeleteDC.argtypes = [H]
        g.CreateDIBSection.argtypes = [H, ctypes.POINTER(_BMI), wintypes.UINT,
                                       ctypes.POINTER(ctypes.c_void_p), H, wintypes.DWORD]
        g.CreateDIBSection.restype = H
        g.SetTextColor.argtypes = [H, wintypes.DWORD]
        g.SetBkMode.argtypes = [H, ctypes.c_int]
        g.SetTextAlign.argtypes = [H, wintypes.UINT]
        g.GetTextExtentPoint32W.argtypes = [H, ctypes.c_wchar_p, ctypes.c_int,
                                            ctypes.POINTER(_SIZE)]
        g.ExtTextOutW.argtypes = [H, ctypes.c_int, ctypes.c_int, wintypes.UINT,
                                  ctypes.c_void_p, ctypes.c_wchar_p,
                                  wintypes.UINT, ctypes.c_void_p]
        g.CreateSolidBrush.argtypes = [wintypes.DWORD]
        g.CreateSolidBrush.restype = H
        u.FillRect.argtypes = [H, ctypes.POINTER(_RECT), H]
        self.dc = g.CreateCompatibleDC(None)
        self._fonts = {}
        self._extents = {}
        g.SetBkMode(self.dc, TRANSPARENT)
        g.SetTextAlign(self.dc, TA_BASELINE)

    # -- 字体 --
    def font(self, family, size_pt, bold=False, italic=False,
             underline=False, strike=False):
        key = (family, round(size_pt, 2), bold, italic, underline, strike)
        h = self._fonts.get(key)
        if h:
            return h
        height = -int(round(size_pt * DPI / 72.0))
        weight = 700 if bold else 400
        h = self.g.CreateFontW(height, 0, 0, 0, weight, int(bool(italic)),
                               int(bool(underline)), int(bool(strike)),
                               DEFAULT_CHARSET, 0, 0, ANTIALIASED_QUALITY, 0,
                               family or None)
        self._fonts[key] = h
        return h

    # -- 测宽，返回 pt --
    def text_width(self, text, hfont):
        if not text:
            return 0.0
        key = (hfont, text)
        w = self._extents.get(key)
        if w is None:
            self.g.SelectObject(self.dc, hfont)
            sz = _SIZE()
            self.g.GetTextExtentPoint32W(self.dc, text, len(text), ctypes.byref(sz))
            w = sz.cx
            self._extents[key] = w
        return w * 72.0 / DPI

    # -- 画一页 --
    def render_page(self, px_w, px_h, draw, bg=(255, 255, 255)):
        """新建一张位图交给 draw(scale) 画，返回「顶行在前」的 RGB 字节。"""
        bmi = _BMI()
        bmi.bmiHeader.biSize = ctypes.sizeof(_BMIH)
        bmi.bmiHeader.biWidth = px_w
        bmi.bmiHeader.biHeight = -px_h          # 负高 = 顶行在前
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0
        bits = ctypes.c_void_p()
        hbmp = self.g.CreateDIBSection(self.dc, ctypes.byref(bmi), DIB_RGB_COLORS,
                                       ctypes.byref(bits), None, 0)
        old = self.g.SelectObject(self.dc, hbmp)
        try:
            self.fill_rect(0, 0, px_w, px_h, bg)
            draw(DPI / 72.0)
            raw = ctypes.string_at(bits, px_w * px_h * 4)
        finally:
            self.g.SelectObject(self.dc, old)
            self.g.DeleteObject(hbmp)
        # 32bpp 是 BGRA；PDF 的图片要 24bpp RGB
        buf = bytearray(px_w * px_h * 3)
        buf[0::3] = raw[2::4]
        buf[1::3] = raw[1::4]
        buf[2::3] = raw[0::4]
        return bytes(buf)

    def fill_rect(self, x0, y0, x1, y1, rgb):
        br = self.g.CreateSolidBrush(_colorref(rgb))
        r = _RECT(int(x0), int(y0), int(x1), int(y1))
        self.u.FillRect(self.dc, ctypes.byref(r), br)
        self.g.DeleteObject(br)

    def draw_text(self, x_pt, baseline_pt, text, hfont, rgb):
        if not text:
            return
        self.g.SetTextColor(self.dc, _colorref(rgb))
        self.g.SelectObject(self.dc, hfont)
        self.g.ExtTextOutW(self.dc, int(round(x_pt * DPI / 72.0)),
                           int(round(baseline_pt * DPI / 72.0)), 0, None,
                           text, len(text), None)

    def close(self):
        try:
            for h in list(self._fonts.values()):
                self.g.DeleteObject(h)
            self._fonts.clear()
            self.g.DeleteDC(self.dc)
        except Exception:
            pass


# ---------- 排版 ----------

class _Font(object):
    """排版用的字体槽：族 + 字号 + 四个样式位（可哈希，做缓存键）。"""

    __slots__ = ("family", "size", "bold", "italic", "underline", "strike")

    def __init__(self, family, size, bold=False, italic=False,
                 underline=False, strike=False):
        self.family = family
        self.size = size
        self.bold = bold
        self.italic = italic
        self.underline = underline
        self.strike = strike

    def derive(self, **kw):
        d = dict(family=self.family, size=self.size, bold=self.bold,
                 italic=self.italic, underline=self.underline, strike=self.strike)
        d.update(kw)
        return _Font(**d)


def _is_wide(ch):
    o = ord(ch)
    return (0x2E80 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF
            or 0xFF00 <= o <= 0xFF60 or 0x3000 <= o <= 0x303F
            or 0x2018 <= o <= 0x201D or o == 0x2026)


def _chunks(runs, base, mono, pal, icode_bg=None):
    """把一行的 run 切成「不可再分的块」：汉字一字一块、西文单词一块、空格一块。

    只有切到块，折行才不会把 "hello" 拆成 "hel / lo"。
    返回 (文字, _Font, 颜色, 是否行内代码)。
    """
    out = []
    ink = _rgb(pal.get("text"))
    link_rgb = _rgb(pal.get("link") or pal.get("accent"), (43, 108, 214))
    for text, tags in runs:
        tags = tags or ()
        f, color, code = base, ink, False
        if "icode" in tags:
            f, code = mono, True
        else:
            if "bold" in tags and "italic" in tags:
                f = f.derive(bold=True, italic=True)
            elif "bold" in tags:
                f = f.derive(bold=True)
            elif "italic" in tags:
                f = f.derive(italic=True)
            if "strike" in tags:
                f = f.derive(strike=True, underline=f.underline or ("underline" in tags))
            elif "underline" in tags:
                f = f.derive(underline=True)
            lk = next((t for t in tags if str(t).startswith("lk_")), None)
            if lk or "link" in tags:
                f = f.derive(underline=True)
                color = link_rgb
        i, n = 0, len(text)
        while i < n:
            ch = text[i]
            if ch in (" ", "\u00a0"):
                out.append((" ", f, color, code))
                i += 1
            elif _is_wide(ch):
                out.append((ch, f, color, code))
                i += 1
            else:
                j = i
                while j < n and text[j] not in (" ", "\u00a0") and not _is_wide(text[j]):
                    j += 1
                out.append((text[i:j], f, color, code))
                i = j
    return out


class Layout(object):
    """把块列表排成「页 → 绘制指令」，坐标全用 pt。

    指令是三种元组：
        ("rect", x0, y0, x1, y1, rgb)
        ("text", x, baseline, 文字, _Font, rgb)
        ("mark", 类型, x, y中心, 边长, rgb)      # 列表/待办的几何标记
    """

    def __init__(self, gdi, opts):
        self.gdi = gdi
        self.opts = opts
        self.pages = []
        self._ops = None
        self._new_page()

    # -- 页面游标 --
    def _new_page(self):
        self._ops = []
        self.pages.append(self._ops)
        self.y = self.opts["top"]

    def _ensure(self, need):
        if self.y + need > self.opts["bottom"]:
            self._new_page()
            return True
        return False

    def _font(self, f):
        return self.gdi.font(f.family, f.size, f.bold, f.italic, f.underline, f.strike)

    def _width(self, text, f):
        return self.gdi.text_width(text, self._font(f))

    def _emit(self, op):
        self._ops.append(op)

    # -- 折行：返回 [[(x, 文字, font, 颜色, code)…]…] --
    def _wrap(self, chunks, x0, width):
        limit = x0 + width
        rows, cur, cx = [], [], x0
        for text, f, color, code in chunks:
            w = self._width(text, f)
            if cur and cx + w > limit:
                if text[:1] in NO_LINE_START:
                    # 行首禁则：标点不顶行首，宁可让这一行溢出半个字
                    cur.append((cx, text, f, color, code))
                    rows.append(cur)
                    cur, cx = [], x0
                    continue
                if cur[-1][1][-1:] in NO_LINE_END:
                    moved = cur.pop()
                    rows.append(cur)
                    cur = [(x0, moved[1], moved[2], moved[3], moved[4])]
                    cx = x0 + self._width(moved[1], moved[2])
                else:
                    rows.append(cur)
                    cur, cx = [], x0
            if text == " " and not cur:
                continue
            cur.append((cx, text, f, color, code))
            cx += w
        rows.append(cur)
        return rows

    # -- 一段文字（普通段落 / 标题 / 引用 / 列表正文） --
    def para(self, chunks, x0, width, size, ratio=LINE_RATIO,
             before=0.0, after=0.0, bg=None, bar=None, hang=0.0,
             marker=None, first_size=None):
        lh = size * ratio
        if before:
            self._ensure(before + lh)
            self.y += before
        rows = self._wrap(chunks, x0 + hang, width - hang)
        first = True
        for row in rows:
            self._ensure(lh)
            asc = (first_size or size) * 0.86
            baseline = self.y + asc
            if bg is not None:
                self._emit(("rect", self.opts["left"] - 6, self.y,
                            self.opts["right"] + 6, self.y + lh, bg))
            if bar is not None:
                self._emit(("rect", self.opts["left"] - 6, self.y,
                            self.opts["left"] - 3, self.y + lh, bar))
            if marker and first:
                kind, text = marker
                if kind == "num":
                    mw = self._width(text, chunks[0][1] if chunks else
                                     _Font(self.opts["font"], size))
                    self._emit(("text", x0 + hang - 6 - mw, baseline, text,
                                chunks[0][1] if chunks else _Font(self.opts["font"], size),
                                _rgb(self.opts["pal"].get("soft"))))
                else:
                    self._emit(("mark", kind, x0, self.y + lh * 0.5,
                                size * 0.92, _rgb(self.opts["pal"].get("soft"))))
            for (cx, text, f, color, code) in row:
                if code:
                    pw = self._width(text, f)
                    self._emit(("rect", cx - 0.8, self.y + lh * 0.12,
                                cx + pw + 0.8, self.y + lh * 0.88,
                                self.opts["icode_bg"]))
                self._emit(("text", cx, baseline, text, f, color))
            self.y += lh
            first = False
        if after:
            self.y += after

    # -- 代码块 --
    def code_block(self, lang, code, size):
        pad = size * 0.8
        lh = size * 1.5
        x0 = self.opts["left"]
        width = self.opts["width"] - pad * 2
        f = _Font(self.opts["mono"], size)
        ink = _rgb(self.opts["pal"].get("text"))
        if lang:
            self._ensure(lh)
            self._emit(("rect", x0, self.y, x0 + self.opts["width"],
                        self.y + lh * 0.9, self.opts["code_bg"]))
            lf = _Font(self.opts["mono"], size * 0.82)
            lw = self._width(lang, lf)
            self._emit(("text", x0 + self.opts["width"] - pad - lw,
                        self.y + lh * 0.66, lang, lf,
                        _rgb(self.opts["pal"].get("soft"))))
            self.y += lh * 0.9
        for src in (code.split("\n") if code else [""]):
            for row in self._wrap(_chunks([(src, ())], f, f, self.opts["pal"]),
                                  x0 + pad, width):
                self._ensure(lh)
                self._emit(("rect", x0, self.y, x0 + self.opts["width"],
                            self.y + lh, self.opts["code_bg"]))
                for (cx, text, ff, color, _c) in row:
                    self._emit(("text", cx, self.y + size * 1.12, text, ff, ink))
                self.y += lh
        self._emit(("rect", x0, self.y, x0 + self.opts["width"],
                    self.y + size * 0.5, self.opts["code_bg"]))
        self.y += size * 0.5

    # -- 分割线 --
    def hr(self):
        gap = 14
        self._ensure(gap)
        self.y += gap * 0.6
        self._emit(("rect", self.opts["left"], self.y,
                    self.opts["right"], self.y + 0.7, self.opts["rule"]))
        self.y += gap * 0.4


# ---------- 第二批 GDI 图元（圆点 / 三角 / 方框 / 折线） ----------

if os.name == "nt":
    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    def _add_primitives():
        """给 Gdi 补上几何图元（列表标记要用）。"""
        g, _u = _dlls()
        H = ctypes.c_void_p
        g.Ellipse.argtypes = [H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        g.CreatePen.argtypes = [ctypes.c_int, ctypes.c_int, wintypes.DWORD]
        g.CreatePen.restype = H
        g.Polyline.argtypes = [H, ctypes.POINTER(_POINT), ctypes.c_int]
        g.Rectangle.argtypes = [H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        g.GetStockObject.argtypes = [ctypes.c_int]
        g.GetStockObject.restype = H
        g.Polygon.argtypes = [H, ctypes.POINTER(_POINT), ctypes.c_int]

    def _fill_ellipse(self, x0, y0, x1, y1, rgb):
        br = self.g.CreateSolidBrush(_colorref(rgb))
        old_b = self.g.SelectObject(self.dc, br)
        pen = self.g.CreatePen(0, 0, _colorref(rgb))     # PS_SOLID, 0 宽 = 1px
        old_p = self.g.SelectObject(self.dc, pen)
        self.g.Ellipse(self.dc, int(x0), int(y0), int(x1), int(y1))
        self.g.SelectObject(self.dc, old_p)
        self.g.SelectObject(self.dc, old_b)
        self.g.DeleteObject(pen)
        self.g.DeleteObject(br)

    def _stroke(self, points, rgb, w_px=2):
        """折线（不闭合）。points 是像素坐标。"""
        if len(points) < 2:
            return
        arr = (_POINT * len(points))()
        for i, (x, y) in enumerate(points):
            arr[i].x, arr[i].y = int(round(x)), int(round(y))
        pen = self.g.CreatePen(0, int(w_px), _colorref(rgb))
        old_p = self.g.SelectObject(self.dc, pen)
        self.g.Polyline(self.dc, arr, len(points))
        self.g.SelectObject(self.dc, old_p)
        self.g.DeleteObject(pen)

    def _frame_rect(self, x0, y0, x1, y1, rgb, w_px=2):
        """空心矩形（描边）。"""
        pen = self.g.CreatePen(0, int(w_px), _colorref(rgb))
        old_p = self.g.SelectObject(self.dc, pen)
        old_b = self.g.SelectObject(self.dc, self.g.GetStockObject(5))   # NULL_BRUSH
        self.g.Rectangle(self.dc, int(x0), int(y0), int(x1), int(y1))
        self.g.SelectObject(self.dc, old_b)
        self.g.SelectObject(self.dc, old_p)
        self.g.DeleteObject(pen)

    def _fill_polygon(self, points, rgb):
        """实心多边形（三角列表标记）。points 是像素坐标。"""
        if len(points) < 3:
            return
        arr = (_POINT * len(points))()
        for i, (x, y) in enumerate(points):
            arr[i].x, arr[i].y = int(round(x)), int(round(y))
        br = self.g.CreateSolidBrush(_colorref(rgb))
        pen = self.g.CreatePen(0, 0, _colorref(rgb))
        old_b = self.g.SelectObject(self.dc, br)
        old_p = self.g.SelectObject(self.dc, pen)
        self.g.Polygon(self.dc, arr, len(points))
        self.g.SelectObject(self.dc, old_p)
        self.g.SelectObject(self.dc, old_b)
        self.g.DeleteObject(pen)
        self.g.DeleteObject(br)

    Gdi.fill_ellipse = _fill_ellipse
    Gdi.stroke = _stroke
    Gdi.frame_rect = _frame_rect
    Gdi.fill_polygon = _fill_polygon
    _add_primitives()


# ---------- 文档：块 → 页面指令 ----------

def _draw_mark(gdi, op, scale, green=(22, 163, 74)):
    """画列表 / 待办的几何标记（不依赖字体里有没有 ● ➤ ⬜ 这些字形）。

    `x` 是标记格的左缘（pt），标记在格里**居中**：列表项的文字由 hang 统一
    推到右边，标记要是贴着格左缘，字和点之间会空出一大截。
    """
    _k, kind, x, cy, size, rgb = op[:6]
    checked = op[6] if len(op) > 6 else False
    s = size * scale
    X = x * scale
    Y = cy * scale
    cx = X + 4.4 * scale          # 标记中心（约半个字宽）
    if kind == "bullet":
        r = max(2.6, s * 0.19)
        gdi.fill_ellipse(cx - r, Y - r, cx + r, Y + r, rgb)
    elif kind == "triangle":
        w, h = s * 0.22, s * 0.26
        gdi.fill_polygon([(cx - w * 0.7, Y - h), (cx + w * 0.8, Y),
                          (cx - w * 0.7, Y + h)], rgb)
    elif kind == "check":
        box = s * 0.66
        y0 = Y - box / 2.0
        x0 = cx - box / 2.0
        if checked:
            gdi.fill_rect(x0, y0, x0 + box, y0 + box, green)
            gdi.stroke([(x0 + box * 0.22, y0 + box * 0.52),
                        (x0 + box * 0.42, y0 + box * 0.74),
                        (x0 + box * 0.80, y0 + box * 0.28)], (255, 255, 255),
                       max(1, int(round(box * 0.15))))
        else:
            gdi.frame_rect(x0, y0, x0 + box, y0 + box, rgb,
                           max(1, int(round(s * 0.06))))


def _text_color(pal, key, default=(34, 34, 34)):
    return _rgb(pal.get(key), default)


def make_opts(pal, body_pt, font_family, mono_family):
    """把主题色和字号折成一份排版参数。"""
    text_rgb = _text_color(pal, "text")
    white = (255, 255, 255)
    accent = _rgb(pal.get("accent"), (47, 124, 246))
    return {
        "left": MARGIN_PT,
        "right": PAGE_W_PT - MARGIN_PT,
        "top": MARGIN_PT,
        "bottom": PAGE_H_PT - MARGIN_PT,
        "width": PAGE_W_PT - MARGIN_PT * 2,
        "body": float(body_pt),
        "font": font_family,
        "mono": mono_family,
        "pal": pal,
        # 代码块底色：极浅的灰；行内代码再浅一点，免得和代码块混成一片
        "code_bg": _mix(white, text_rgb, 0.055),
        "icode_bg": _mix(white, text_rgb, 0.045),
        "rule": _mix(white, text_rgb, 0.30),
        "quote_bar": _mix(white, accent, 0.55),
        "quote_bg": _mix(white, accent, 0.05),
        "green": _text_color(pal, "green", (22, 163, 74)),
    }


def build_document(gdi, blocks, opts):
    """块列表 → 页面指令。返回页面列表（每页是绘制指令数组）。"""
    import exporter as ex

    lay = Layout(gdi, opts)
    body = opts["body"]
    base = _Font(opts["font"], body)
    mono = _Font(opts["mono"], max(7.5, body * CODE_RATIO))
    pal = opts["pal"]

    for b in blocks:
        kind_b = b["kind"]
        if kind_b == "code":
            lay.code_block(b["lang"], b["code"], max(7.5, body * CODE_RATIO))
            continue
        if kind_b == "hr":
            lay.hr()
            continue
        text = ex.line_text(b)
        if not text.strip():
            step = body * 0.95
            if lay.y + step > opts["bottom"]:
                lay._new_page()
            else:
                lay.y += step
            continue
        tags = ex.line_tags(b)
        mi = ex.marker_info(b)
        runs = ex._slice_runs(b["runs"], mi["cut"]) if mi["cut"] else b["runs"]
        if "title" in tags:
            # ⚠️ 字号长在**字体槽**上，段落级只是传了个 size 给行高 ——
            # 标题必须用标题号的 base 重新切块，否则印出来仍是一号到底。
            hf = _Font(opts["font"], body * H1_RATIO, bold=True)
            chunks = _chunks(runs, hf, hf, pal)
            lay.para(chunks, opts["left"], opts["width"], body * H1_RATIO,
                     ratio=1.34, after=body * 0.75, first_size=body * H1_RATIO)
        elif "h2" in tags:
            hf = _Font(opts["font"], body * H2_RATIO, bold=True)
            chunks = _chunks(runs, hf, hf, pal)
            lay.para(chunks, opts["left"], opts["width"], body * H2_RATIO,
                     ratio=1.42, before=body * 0.7, after=body * 0.35,
                     first_size=body * H2_RATIO)
        elif "quote" in tags:
            chunks = _chunks(runs, base, mono, pal)
            lay.para(chunks, opts["left"] + 12, opts["width"] - 14, body,
                     bg=opts["quote_bg"], bar=opts["quote_bar"])
        elif mi["kind"]:
            chunks = _chunks(runs, base, mono, pal)
            x0 = opts["left"] + mi["level"] * 18
            hang = 12.5
            marker = ("num", mi["text"]) if mi["kind"] == "numbered" else \
                     (("check" if mi["kind"] == "check" else mi["kind"]), "")
            op_index = len(lay._ops)
            lay.para(chunks, x0, opts["width"] - mi["level"] * 18, body,
                     hang=hang, marker=marker)
            # 已勾的待办：把标记补成「实心绿 + 白勾」
            if mi["kind"] == "check" and mi["checked"]:
                for i in range(op_index, len(lay._ops)):
                    if lay._ops[i][0] == "mark":
                        lay._ops[i] = lay._ops[i][:6] + (True,)
                        break
        else:
            chunks = _chunks(runs, base, mono, pal)
            lay.para(chunks, opts["left"], opts["width"], body)
    return lay.pages


def paint(gdi, pages, opts):
    """把每页的绘制指令画成位图，返回 [(px_w, px_h, rgb_bytes)]。"""
    px_w = int(round(PAGE_W_PT * DPI / 72.0))
    px_h = int(round(PAGE_H_PT * DPI / 72.0))
    out = []
    for ops in pages:
        def draw(scale, ops=ops):
            for op in ops:
                if op[0] == "rect":
                    gdi.fill_rect(op[1] * scale, op[2] * scale,
                                  op[3] * scale, op[4] * scale, op[5])
                elif op[0] == "text":
                    _kind, x, baseline, text, f, rgb = op
                    gdi.draw_text(x, baseline, text,
                                  gdi.font(f.family, f.size, f.bold, f.italic,
                                           f.underline, f.strike), rgb)
                elif op[0] == "mark":
                    _draw_mark(gdi, op, scale, opts.get("green", (22, 163, 74)))
        out.append((px_w, px_h, gdi.render_page(px_w, px_h, draw)))
    return out


# ---------- PDF 文件本体（纯函数） ----------

def _pdf_text_hex(s):
    """PDF 的文本串要写 UTF-16BE（带 BOM）才放得下中文 —— 直接塞 UTF-8 会乱码。"""
    return b"<FEFF" + str(s or "").encode("utf-16-be").hex().upper().encode("ascii") + b">"


def build_pdf(page_bitmaps, title="", producer=b"SuJian"):
    """位图页 → PDF 字节。**不依赖 GDI**，可以脱机单测。

    每页就是一个全幅图片 XObject：`q W 0 0 H 0 0 cm /Im0 Do Q`。
    PDF 的图片坐标系原点在左下、内容随 CTM 铺满，图片第 0 行落在页顶，
    所以「顶行在前」的位图原样塞进去就是正的，不需要翻转。
    """
    w_pt, h_pt = PAGE_W_PT, PAGE_H_PT
    n = len(page_bitmaps)
    objs = []                    # 下标 0 → 对象 1

    def add(body):
        objs.append(body)
        return len(objs)

    catalog = add(b"")           # 占位，最后回填
    pages_obj = add(b"")
    info = add(b"<< /Title " + _pdf_text_hex(title)
               + b" /Producer " + _pdf_text_hex(producer.decode())
               + b" /Creator " + _pdf_text_hex(producer.decode()) + b" >>")
    kids = []
    for (px_w, px_h, rgb) in page_bitmaps:
        data = zlib.compress(rgb, 6)
        img = add(b"<< /Type /XObject /Subtype /Image /Width %d /Height %d "
                  b"/ColorSpace /DeviceRGB /BitsPerComponent 8 "
                  b"/Filter /FlateDecode /Length %d >>\nstream\n"
                  % (px_w, px_h, len(data)) + data + b"\nendstream")
        content = b"q %.2f 0 0 %.2f 0 0 cm /Im0 Do Q\n" % (w_pt, h_pt)
        cont = add(b"<< /Length %d >>\nstream\n" % len(content) + content
                   + b"endstream")
        page = add(("<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %.2f %.2f] "
                    "/Resources << /XObject << /Im0 %d 0 R >> >> /Contents %d 0 R >>"
                    % (pages_obj, w_pt, h_pt, img, cont)).encode("latin-1"))
        kids.append(page)
    objs[catalog - 1] = ("<< /Type /Catalog /Pages %d 0 R >>"
                         % pages_obj).encode("latin-1")
    objs[pages_obj - 1] = ("<< /Type /Pages /Kids [%s] /Count %d >>"
                           % (" ".join("%d 0 R" % k for k in kids), n)
                           ).encode("latin-1")

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % (i + 1) + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n" % (len(objs) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (b"trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objs) + 1, catalog, info, xref_at))
    return bytes(out)


# ---------- 对外入口 ----------

def export_pdf(path, blocks, title, pal, body_pt, font_family, mono_family):
    """把块列表写成一份 PDF，返回 (页数, 字节数)。"""
    gdi = Gdi()
    try:
        opts = make_opts(pal, body_pt, font_family, mono_family)
        pages = build_document(gdi, blocks, opts)
        bitmaps = paint(gdi, pages, opts)
    finally:
        gdi.close()
    data = build_pdf(bitmaps, title=title)
    with open(path, "wb") as f:
        f.write(data)
    return len(bitmaps), len(data)
