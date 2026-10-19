# -*- coding: utf-8 -*-
"""素笺 —— 资源路径、字体与配色（读取 WorkBuddy 皮肤并映射为笔记调色板）。"""
import colorsys
import os
import re
import sys

from store import _app_dir


def _res_dir():
    """随程序一起打包的只读资源（图标）目录：打包后是 _MEIPASS/icon，源码运行是项目根 icon/。"""
    if getattr(sys, "frozen", False):
        return os.path.join(getattr(sys, "_MEIPASS", _app_dir()), "icon")
    return os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         os.pardir, "icon"))


_RES = _res_dir()

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

# 链接色：固定蓝，**不跟皮肤的 accent 走**。皮肤只取亮色块，多数皮肤的 accent 是绿系，
# 链接跟着 accent 就变成绿字绿下划线，和普通强调文字分不开，也不符合「蓝色链接」的直觉。
# 在 #ffffff 稿纸上对比 5.4，在侧栏/工具带那些浅色底上也都 ≥4.5。
LINK = "#1668d9"

# 仅浅色主题（已取消暗色主题）：对比更清晰、直角硬朗的 macOS 笔记风格
LIGHT = {
    "top": "#e9e9ec", "side": "#e9e9ec", "list": "#f4f4f6", "editor": "#ffffff",
    "line": "#cfcfd6", "text": "#1f1f24", "soft": "#76767e", "accent": "#2f7cf6",
    "hover": "#e0e0e6", "sel": "#cfe0fb", "selfg": "#1f6feb",
    "green": "#16a34a", "chk_off": "#c9c9d1", "star": "#f5a623", "link": LINK,
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
    "chk_off": "#c6d4c8",    # 未勾选的待办框
    "star": "#f5a623",       # 星标金色（沿用）
    "sel2": "#e6efe7",       # 失焦时的选中色
    "link": LINK,            # 链接蓝（同上，不随皮肤变）
}


def _hex_rgb(h):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


# ======================================================================
# 颜色运算 & 表面色（surface）
#
# 这一整节都是纯函数：只吃颜色字符串、只吐颜色字符串，不碰 tkinter。放到 theme
# 而不是 widgets，是为了让测试**不建 GUI 就能断言**「悬停和选中到底差多少」——
# 靠截图取色验证配色太慢，也说不清阈值。
#
# 核心约定：**状态色一律从「所在的那块底色」推出来**，不直接吃皮肤给的 hover/sel。
# 皮肤只有一个 hover 值，可它要铺在侧栏、笔记列表、工具带三种不同底色上；这版
# 绿皮肤给的 hover(#dde8df) 与 sel(#dbe8dc) 通道差只有 5/765 —— 列表里「鼠标放上去」
# 和「选中这条」长得一模一样。从底色推之后，语义由**推导方式**保证：
#   悬停 = 这块底暗一档（只动明度，保住色相）
#   选中 = 这块底 + 主题色（带色相，明显更实）
# 换主题、换底、换深浅都自动成立。
# ======================================================================

def _hex(r, g, b):
    """三通道数值 -> #rrggbb（自动夹到 0~255）。"""
    def cl(x):
        return max(0, min(255, int(round(x))))
    return "#%02x%02x%02x" % (cl(r), cl(g), cl(b))


def _mix_hex(c1, c2, t):
    """把 c2 按权重 t(0~1) 混入 c1（t=0 得 c1，t=1 得 c2）。"""
    r1, g1, b1 = _hex_rgb((c1 or "#000000"))
    r2, g2, b2 = _hex_rgb((c2 or "#000000"))
    return _hex(r1 + (r2 - r1) * t, g1 + (g2 - g1) * t, b1 + (b2 - b1) * t)


def _rel_lum(color):
    """相对亮度（WCAG 公式）。用来量化「两个颜色能不能看出差别」，不靠肉眼猜。"""
    h = (color or "#000000").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        ch = [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    except Exception:
        return 0.0

    def f(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * f(ch[0]) + 0.7152 * f(ch[1]) + 0.0722 * f(ch[2])


def _contrast_ratio(c1, c2):
    a, b = _rel_lum(c1), _rel_lum(c2)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def _luminance(hexv):
    """感知亮度（0~1），判断浅底/深底用。"""
    r, g, b = _hex_rgb((hexv or "#000000"))
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0


def _color_dist(c1, c2):
    """三通道最大差（0~255）：判断「这两个色块是不是同一种颜色」够用了。"""
    a, b = _hex_rgb((c1 or "#000000")), _hex_rgb((c2 or "#000000"))
    return max(abs(x - y) for x, y in zip(a, b))


def _away(base, other, toward, t0, min_dist):
    """从 base 往 toward 混，混到与 other 拉开 min_dist 为止（够近就停在 t0）。

    近白色之间「差一点点」是最难调的地方：引用底和行内代码底都从稿纸掺出来，
    权重差 0.02 就可能撞成同色（实测 #f2f2f2 vs #f2f3f3，差 2/765）。与其反复
    手调常数，不如把「至少要差多少」写成断言式的推导，换主题也不用重来。
    """
    t = t0
    c = _mix_hex(base, toward, t)
    while _color_dist(c, other) < min_dist and t < 0.55:
        t += 0.03
        c = _mix_hex(base, toward, t)
    return c


def _ink_target(base):
    """中性叠加的目标色：浅底压黑、深底提白。"""
    return "#000000" if _rel_lum(base) > 0.35 else "#ffffff"


# ---- 各语义色相对「底色」的强度（一处调，全局生效） ----
HOVER_SHADE = 0.07         # 悬停：明度降/升多少（HLS 的 L 绝对值，逐档放大直到达标）
HOVER_MIN_RATIO = 1.12     # 悬停底与所在底色至少差这么多
PRESS_SHADE = 0.105        # 按下
PRESS_MIN_RATIO = 1.28
SEL_TINT = 0.24            # 选中行：底色往主题色混多少
SEL2_TINT = 0.11           # 失焦 / 多选的选中行
SEL_MIN_DIST = 20          # 选中行与「悬停」的最小通道差（深色底下靠它兜底）
SEL_TEXT_TINT = 0.38       # 正文选区（要压过所有静态底色）
QUOTE_TINT = 0.10          # 引用块：主题色淡淡一层 = 「有色纸片」
QUOTE_MIN_RATIO = 1.07     # 与稿纸的最小反差（再淡就看不见这块纸）
ICODE_MIX = 0.11           # 行内代码：中性灰（不带主题色）
ICODE_MIN_DIST = 16        # 行内代码底与引用底至少要拉开这么多
SEARCH_HIT_MIX = 0.40      # 搜索命中：琥珀色掺多少
SEARCH_HIT_MIN_RATIO = 1.35  # 命中底与稿纸的反差下限（再淡就成「没标上」）
POPOVER_TINT = 0.18        # 浮层：主题色掺多少（再淡就看不出是这套主题的浮层）
POPOVER_MIN_RATIO = 1.34   # 浮层与稿纸的反差下限（浮得出来）


def _shade(base, dl, min_ratio):
    """只挪明度、不动色相：浅底压暗 / 深底提亮，逐档放大直到对比达标。

    早先的悬停色是 `_mix_hex(base, "#000000", t)`：往纯黑混会把饱和度一起稀释，
    绿皮肤的工具带一悬停就变成「蒙了层灰」的暗块，看着像色不对而不是暗一档。
    HLS 的 L 轴只改明度，色相和饱和度原样保留 —— 悬停色永远属于这块底自己。
    """
    base = base or "#ffffff"
    h, l, s = colorsys.rgb_to_hls(*[c / 255.0 for c in _hex_rgb(base)])
    up = l < 0.5                        # 深底提亮，浅底压暗
    c = base
    for k in (1.0, 1.9, 3.0, 4.4, 6.2, 8.0):
        nl = min(1.0, l + dl * k) if up else max(0.0, l - dl * k)
        c = _hex(*[v * 255.0 for v in colorsys.hls_to_rgb(h, nl, s)])
        if _contrast_ratio(c, base) >= min_ratio:
            return c
        if nl in (0.0, 1.0):            # 已经到顶/底，再加档也没用
            return c
    return c


def _hover_bg(base):
    """某块底色上的悬停色：这块底自己暗（或暗底则亮）一档。"""
    return _shade(base, HOVER_SHADE, HOVER_MIN_RATIO)


def _press_bg(base):
    """某块底色上的按下色：比悬停再深一档。"""
    return _shade(base, PRESS_SHADE, PRESS_MIN_RATIO)


def _sel_row_bg(pal, base=None, active=True):
    """列表 / 侧栏里「选中这一行」的底色：所在底色 + 主题色。

    带主题色相是关键：它和 `_hover_bg`（纯明度变化）天生不是一类色，
    换任何主题都不会再撞回「悬停≈选中」。深色底下这一点尤其要紧 —— 悬停是
    「亮一档」，若选中也只是「亮一档」，两者就只剩色相之差（实测差 12），
    所以还用 `_away` 兜一个最小距离：不够就继续往主题色里掺，直到拉开为止。
    """
    b = base or pal.get("list") or "#f4f4f6"
    acc = pal.get("accent") or "#2f7cf6"
    if not active:
        return _mix_hex(b, acc, SEL2_TINT)
    return _away(b, _hover_bg(b), acc, SEL_TINT, SEL_MIN_DIST)


def _sel_bg(pal, active=True):
    """正文选区底色：把 accent 压深一档，明显压过引用这类静态底色。

    列表行的选中色走 `pal["sel"]` / `_sel_row_bg`（那是整行高亮，语义不同），
    这里只管正文里的「选中一段文字」。
    """
    ed = pal.get("editor") or "#ffffff"
    t = SEL_TEXT_TINT if active else SEL_TEXT_TINT * 0.52
    return _mix_hex(ed, pal.get("accent") or "#2f7cf6", t)


def _quote_bg(pal):
    """引用块的底色：**主题色的淡淡一层** —— 稿纸上一块「有色纸片」。

    走过三段路：
      1. `_mix_hex(editor, accent, 0.12)`：和选区（同样 accent）撞色，对比 1.10；
      2. 改成中性灰：确实不和选区撞了，可它和行内代码的灰底（0.06 墨）几乎同色
         （实测 #f2f2f2 vs #f2f3f3，差 2/765），两种「浅色块」还是分不开；
      3. 现在：引用吃主题色（0.10）+ 行内代码吃中性墨（见 `_icode_bg`），
         一个有色相、一个没色相，同时比选区（0.38）浅得多 —— 三条路各走各的。

    反差下限 QUOTE_MIN_RATIO 是给「深底主题」和「主题色本身很淡」兜底的。
    """
    ed = pal.get("editor") or "#ffffff"
    acc = pal.get("accent") or "#2f7cf6"
    c = ed
    for t in (QUOTE_TINT, QUOTE_TINT + 0.03, QUOTE_TINT + 0.07, QUOTE_TINT + 0.12):
        c = _mix_hex(ed, acc, t)
        if _contrast_ratio(c, ed) >= QUOTE_MIN_RATIO:
            return c
    return c


def _icode_bg(pal):
    """行内代码的底色：**中性灰**，不带主题色。

    代码是「机械的、内联的」，用一块灰色小凸起表示；引用是「纸面上的摘录」，
    用主题色的有色纸片表示。两者同处正文，必须能一眼分开，所以刻意一有色一中性。
    若主题色本身接近灰（accent 饱和度≈0），`_quote_bg` 会退化成灰 → 这里就加深
    代码底，直到两者拉开 ICODE_MIN_DIST 为止。
    """
    ed = pal.get("editor") or "#ffffff"
    tx = pal.get("text") or "#1f1f24"
    return _away(ed, _quote_bg(pal), tx, ICODE_MIX, ICODE_MIN_DIST)


def _search_hit_bg(pal):
    """搜索命中词的底色：琥珀色。

    为什么不用主题色：绿皮肤下 accent 就是绿色，和引用块（accent 0.10）、
    选中行（accent 渐变）同属一族 —— 高亮一出现会被读成「这段被选中了」。
    琥珀在浅色和深色稿纸上都天然属于「荧光笔」，和正文里任何既有底色都不撞。

    深浅两套主题都用同一个色相，只按「与稿纸的反差」决定要往墨里压还是往白里提
    （见 `_ink_target`）：深色主题下掺出来本身就是暗琥珀，已经够看，不再动。
    **字色一律不动** —— 浅稿纸上深字、深稿纸上浅字，琥珀底两头都读得清；
    换字色反而会把加粗/标题这些既有观感一起盖掉。
    """
    ed = pal.get("editor") or "#ffffff"
    base = _mix_hex(ed, "#f0b429", SEARCH_HIT_MIX)
    c = base
    for k in (0.0, 0.06, 0.12, 0.20, 0.30):
        c = _mix_hex(base, _ink_target(ed), k)
        if _contrast_ratio(c, ed) >= SEARCH_HIT_MIN_RATIO:
            return c
    return c


def _popover_bg(pal):
    """浮层（悬停小窗 / 提示气泡）的底色：稿纸色 + 一层主题色，再压到「浮得出来」。

    早先这里是纯中性灰（#d6d6d6）：在绿皮肤下它就是一块**没有主题感的灰纸**，
    和整个界面不像一家人。现在先掺 POPOVER_TINT 的主题色（换主题时小窗自动
    跟着走），再往墨色压，直到与**稿纸**的反差达到 POPOVER_MIN_RATIO ——
    浮层必须有边界，不能靠 1px 描边硬撑。

    注意反差是跟「稿纸」比，不是跟掺过色之后的那个中间色比：拿中间色当基准的话
    永远「已经够深了」，小窗就浮不起来。

    内部输入框仍用稿纸色：「有色外壳 + 稿纸输入区」两层对比，顺带标出能打字的地方。
    """
    ed = pal.get("editor") or "#ffffff"
    acc = pal.get("accent") or "#2f7cf6"
    base = _mix_hex(ed, acc, POPOVER_TINT)
    c = base
    for k in (0.06, 0.10, 0.16, 0.22, 0.30, 0.40):
        c = _mix_hex(base, _ink_target(ed), k)
        if _contrast_ratio(c, ed) >= POPOVER_MIN_RATIO:
            return c
    return c


def _popover_border(pal):
    """浮层描边：比浮层底色再压一档（保留浮层自己的色相，不是纯灰）。"""
    bg = _popover_bg(pal)
    # 0.24：1px 边线在有色壳上看得清就行，再深就成了「重边框」，抢输入框的戏
    return _mix_hex(bg, _ink_target(bg), 0.24)


def _harmonize(pal):
    """按上面的规则重算「会互相打架」的几个状态色（原地改并返回）。

    皮肤只给一两个近似的 hover/sel，它不知道这两个色会分别铺在哪块底上。
    这里统一改成推导值，保证：
      hover ≠ sel   （一个是明度变化，一个带主题色相）
      sel 在 list / side 上都压得住底
    换皮肤、换主题都自动成立，不必再逐个界面调。
    """
    lst = pal.get("list") or "#f4f4f6"
    pal["hover"] = _hover_bg(lst)
    pal["sel"] = _sel_row_bg(pal, lst, active=True)
    pal["sel2"] = _sel_row_bg(pal, lst, active=False)
    return pal


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


# 启动时决定配色：优先 WorkBuddy 皮肤，失败则用内置浅灰。
# 最后再过一道 _harmonize：皮肤/内置表里那几个「状态色」一律改成由底色推导，
# 免得换主题时又出现「悬停和选中同色」这类问题（见上面 _harmonize 的说明）。
PALETTE = _harmonize(load_skin_palette() or dict(LIGHT))
