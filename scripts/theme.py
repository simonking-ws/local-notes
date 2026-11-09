# -*- coding: utf-8 -*-
"""素笺 —— 资源路径、字体与配色（读取 WorkBuddy 皮肤并映射为笔记调色板）。"""
import colorsys
import json
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

# 云母白（白稿纸 + 中性浅灰）：对比更清晰、直角硬朗的 macOS 笔记风格
LIGHT = {
    "top": "#e9e9ec", "side": "#e9e9ec", "list": "#f4f4f6", "editor": "#ffffff",
    "line": "#cfcfd6", "text": "#1f1f24", "soft": "#76767e", "accent": "#2f7cf6",
    "hover": "#e0e0e6", "sel": "#cfe0fb", "selfg": "#1f6feb",
    "green": "#16a34a", "chk_off": "#c9c9d1", "star": "#f5a623", "link": LINK,
}

# ---------- 内置主题 base 调色板（多主题切换，见下方 THEMES / build_palette） ----------
# 每套都只是「基础色」：状态色（hover / sel / 选区 / 引用底 / 状态条 / 浮层…）仍由
# theme.py 那一整套纯函数从底色现推，不写死。换主题 = 换一份 base，所有派生色自动跟随，
# 不用再逐个界面调。派生函数靠 `_ink_target` / `_rel_lum` 按明度分叉，深浅两套都成立。
# 字段含义同 LIGHT；`link` 在深底上比固定蓝更亮才读得清，所以每套自带。

# 石墨灰：**中性炭灰**底 + 浅字，夜间 / 护眼。底色刻意不带任何色相（R=G≈B），
# 和「午夜蓝」（整窗藏蓝）一眼就能分开 —— 以前两者都是冷调深灰蓝、只差几档明度，
# 下拉里切过去几乎看不出变化。accent 取琥珀金（墨与灯下的纸），是这套唯一的有彩色，
# 选中行 = 灰底 + 一点暖金，和蓝调的午夜蓝彻底分家。
DARK = {
    "top": "#27272a", "side": "#27272a", "list": "#2f2f33", "editor": "#1c1c1f",
    "line": "#3f3f46", "text": "#e9e9ec", "soft": "#a1a1aa", "accent": "#e0a24a",
    "selfg": "#f0bd77", "green": "#3fb950", "chk_off": "#4a4a52", "star": "#f5c451",
    "link": "#7ab0ff",
}

# 羊皮纸：暖米色稿纸，柔和不刺眼，适合长文阅读。accent 用赤陶色，落到暖底上不突兀。
SEPIA = {
    "top": "#efe7d6", "side": "#efe7d6", "list": "#f3ecdd", "editor": "#f8f3e9",
    "line": "#ddd2bc", "text": "#43392b", "soft": "#6f6450", "accent": "#b06a3a",
    "selfg": "#8a4a26", "green": "#5a8a4a", "chk_off": "#d8cdb6", "star": "#d39a2e",
    "link": "#8a4a26",
}

# 森林绿：浅色主题 —— 底色走中性浅灰（同 LIGHT），只把 accent 换成主题色；
# 这样选中行（带色相）和列表底（中性灰）既有色相差、明度也拉开，比「整片绿」
# 更分得清。浅底 + 强调色才是「绿皮肤」原本的样子，不是「绿底」。
# ⚠️ 这里的 accent / selfg 只是**兜底值**：运行时 accent 换成 WorkBuddy 当前
# 主题色（见 `_forest_base`），selfg 由 accent 现推（见 `_accent_fg`）。读不到
# 皮肤（没装 / 解析失败）才用下面这个绿。
FOREST = {
    "top": "#e9e9ec", "side": "#e9e9ec", "list": "#f4f4f6", "editor": "#ffffff",
    "line": "#cfcfd6", "text": "#1f1f24", "soft": "#76767e", "accent": "#2f9e57",
    "selfg": "#1f7a3d", "green": "#16a34a", "chk_off": "#c9c9d1", "star": "#e0a32a",
    "link": "#1668d9",
}

FOREST_ACCENT_FALLBACK = FOREST["accent"]   # 读不到 WorkBuddy 主题色时用的绿
# 主题色与列表底的最大通道差下限：低于它（近白 / 近灰的主题色）就退回兜底绿 ——
# 那种色掺出来的选中行会和悬停撞在一起（_away 掺到 0.55 也拉不开距离）。
ACCENT_MIN_DIST = 24

# 午夜蓝：**整窗深藏蓝**（底色蓝通道明显高、更暗）+ 靛蓝 accent，比中性炭灰的
# 「石墨灰」更有颜色倾向。两套深色靠「灰 vs 蓝」和「冷暖 accent」区分，
# 别再往回调淡蓝度 —— 那样又和石墨灰撞在一起。
MIDNIGHT = {
    "top": "#141a2b", "side": "#141a2b", "list": "#1a2337", "editor": "#0e1421",
    "line": "#27334f", "text": "#dbe4f7", "soft": "#8895b3", "accent": "#6f86ff",
    "selfg": "#aebaff", "green": "#3fb950", "chk_off": "#2c3550", "star": "#f5c451",
    "link": "#8fb3ff",
}

# 晴空蓝：整窗浅天蓝（顶栏 / 侧栏同色）+ 近白列表 + 纯白稿纸，是六套里唯一
# 「带色的大块面」浅色主题。色值按用户给的参考图逐区取色：
#   top/side #ccebff、list #f7fcff、editor #ffffff、选中行 #bce4ff、
#   主字 #1e3b4f、次级字 #506776、强调色 #0099ff。
# 分区靠底色差而非描边，所以 `line` 比截图里那条极淡的 #e0f2fe 略加深一档：
# 弹窗外沿 / 输入框描边都是 1px 直接吃 `pal["line"]`，太淡会整圈看不见。
# ⚠️ accent 微调到 #0095f7（与截图只差 4/255 的绿通道）：#0099ff 上白字对比
#    2.9998，`on_accent` 会差一点点判成墨字，亮蓝按钮突然变黑字；压深后 3.16
#    稳稳用白字，选中行也仍推到 #bce3fd（与截图色距仅 2/765，肉眼无差）。
#    别改回 #0099ff —— 那会让「实心 accent 块上的文字」和整套蓝色调不搭。
SKY = {
    "top": "#ccebff", "side": "#ccebff", "list": "#f7fcff", "editor": "#ffffff",
    "line": "#a9c8dc", "text": "#1e3b4f", "soft": "#506776", "accent": "#0095f7",
    "selfg": "#125c96", "green": "#16a34a", "chk_off": "#9cc6e2",
    "star": "#f5a623", "link": LINK,
}

# ---------- 皮肤：供「森林绿」整窗跟随 WorkBuddy 外观 ----------
# 「跟随 WorkBuddy 外观」那个**独立主题**已下线（列表里不再多一项），但这套能力
# 没丢：合并进「森林绿」—— 顶栏 / 侧栏 / 列表 / 稿纸 / 线条 / 文字全部取自
# WorkBuddy 当前皮肤，accent 取它的主题色，选中行文字由 accent 现推。
# 读不到皮肤就退回 FOREST 自带的那套（中性浅灰 + 绿）。
# 开关：改为 False 即完全不读皮肤，森林绿固定用自带配色。
USE_SKIN = True
SKIN_DIR = os.path.join(os.path.expanduser("~"), ".workbuddy", "appearance-resources")
# WorkBuddy 记录「当前选中哪套皮肤」的偏好文件（在 ~/.workbuddy/storage/user-*/global/ 下）
SKIN_PREF_ROOT = os.path.join(os.path.expanduser("~"), ".workbuddy", "storage")
# 主题色对应的 CSS 变量，按顺序取第一个命中的
SKIN_ACCENT_VARS = ("--cb-button-primary", "--cb-text-link")
# 「款式色」：顶栏 / 侧栏 / 列表 / 稿纸 / 线条 / 文字，按顺序取第一个命中的 CSS 变量。
# 森林绿整窗跟 WorkBuddy 外观走，靠的就是这张表（缺哪个键就退回 FOREST 自带值）。
SKIN_BASE_VARS = {
    "top":    ("--cb-bg-secondary", "--cb-content-background"),
    "side":   ("--cb-bg-secondary", "--cb-content-background"),
    "list":   ("--cb-bg-primary",),
    "editor": ("--cb-main-area-background", "--cb-panel-bg-primary"),
    "line":   ("--cb-vscode-panel-border", "--cb-main-area-border-color",
               "--cb-input-border-color", "--cb-markdown-border-color"),
    "text":   ("--cb-text-primary",),
    "soft":   ("--cb-text-secondary",),
}
# 采用皮肤款式的必备键：缺一个就不整套套用（半套色混搭比不套更难看）
SKIN_REQUIRED = ("top", "side", "list", "editor", "text")

# 内置 base 缺的键用它兜底（派生函数不会 KeyError）。颜色沿用森林绿体系。
PALETTE_DEFAULTS = {
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

# ---- 编辑区两条横带：只读提示条 / 底部状态条 ----
# 只读提示条贴在多标签条正下方。早先它直接吃 pal["hover"] —— 那是**列表行悬停色**
# （由 list 底推出来），语义不对，而且和「未选中标签」只差 6/255：两条灰带连成
# 一片，提示看着像多出来的一枚标签。现在改成「稿纸 + 主题色」的有色纸片，
# 并用 _away 兜住与未选中标签的最小色差。
RO_BAR_TINT = 0.18         # 只读提示条：从稿纸往主题色掺多少
RO_BAR_MIN_DIST = 20       # 与「未选中标签」至少拉开的最大通道差
# 未选中标签：条底压深一档（TabStrip 与提示条共用同一口径，别两处写 0.06）
TAB_IDLE_MIX = 0.06
# 工具带底色 = 侧栏色朝稿纸色提亮这个比例（0 = 侧栏，1 = 稿纸）。
# 方向是「提亮」不是「压暗」：浅色皮肤里任何压暗都显脏（试过 0.10 / 0.05 两版）。
TOOL_BG_MIX = 0.55
# 底部状态条原先只压 0.045 墨（#f4f4f4，与白稿纸反差 1.10），等于没画。
# 后来改成压 0.11 墨的中性灰 —— 边界有了，但浅色主题的稿纸都是白的，状态条
# 于是永远是 #e3e3e3：换主题它纹丝不动。现在在压深档上再染一层 accent，
# 绿皮肤得绿灰、蓝皮肤得蓝灰；同时它必须比上面那张「淡纸片」提示条沉一档，
# 否则两条同色相的浅带会撞成一对。
FOOTER_MIX = 0.11          # 状态条：稿纸往墨（深底则往白）压多少
FOOTER_MIN_RATIO = 1.25    # 与稿纸的反差下限
FOOTER_TINT = 0.20         # 状态条：压深档上再往主题色染多少（只借色相，别成彩条）
FOOTER_BAND_MIN_DIST = 22  # 与只读提示条 / 未选中标签至少拉开的最大通道差
FOOTER_RULE_MIX = 0.22     # 状态条上沿 1px 细线：由状态条底色自己再压一档
FOOTER_FG_MIX = 0.35       # 状态条文字：soft 往主文字色提的起点
FOOTER_FG_MIN_RATIO = 4.5  # 9pt 小字在状态条底上的对比下限（WCAG AA）


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


# ----------------------------------------------------------------------
# 编辑区两条横带
#
# 这两条都紧贴稿纸：上面是「内置示例笔记 / 未解锁」的只读提示条，下面是鼠标
# 移到底才浮出来的字数状态条。以前一条吃 pal["hover"]（列表行悬停色）、
# 一条只压 0.045 墨，结果一条撞标签、一条根本看不见。全部改成从稿纸推：
#   * 提示条 = 稿纸 + 主题色  → 属于「文档」，和属于「窗口装饰」的灰标签分家；
#   * 状态条 = 稿纸压深 + 主题色 → 同一色系里更沉的一档：色相跟着主题走，
#     明度又和上面那张淡纸片分开；文字色按底色反差动态提深。
# 纯函数，换肤 / 换主题自动成立，配色回归测试不建 GUI 就能断言。
# ----------------------------------------------------------------------

def _toolbar_bg(pal):
    """工具带（也是多标签条的底色）：侧栏 → 稿纸 提亮序列的中间一档。"""
    side = pal.get("side") or pal.get("editor") or "#ffffff"
    ed = pal.get("editor") or "#ffffff"
    bg = _mix_hex(side, ed, TOOL_BG_MIX)
    # 侧栏与稿纸本来就同色的皮肤：提亮拿不到层次，退到「稿纸压暗一点」
    if _contrast_ratio(bg, ed) < 1.025 and _contrast_ratio(bg, side) < 1.025:
        bg = _mix_hex(ed, _ink_target(ed), 0.05)
    return bg


def _tab_idle_bg(base):
    """未选中标签底色：条底压深一档（TabStrip 与提示条共用，别两处写常数）。"""
    return _mix_hex(base, _ink_target(base), TAB_IDLE_MIX)


def _readonly_bar_bg(pal, tab_idle=None):
    """只读提示条底色：**主题色的一张淡纸片**，不是窗口装饰的灰。

    传入未选中标签的底色，是为了把「两块近白色至少差 RO_BAR_MIN_DIST」写成
    推导而不是常数：浅色皮肤里 accent 再淡也会继续掺，绝不会悄悄撞回灰标签。
    极端情况（accent 本身接近白 / 接近灰）掺到 0.55 仍拉不开时，改往墨里压，
    至少保证有边界 —— 但正常皮肤不会走到这条兜底。
    """
    ed = pal.get("editor") or "#ffffff"
    acc = pal.get("accent") or "#2f7cf6"
    if tab_idle is None:
        tab_idle = _tab_idle_bg(_toolbar_bg(pal))
    c = _away(ed, tab_idle, acc, RO_BAR_TINT, RO_BAR_MIN_DIST)
    if _color_dist(c, tab_idle) < RO_BAR_MIN_DIST:
        c = _away(ed, tab_idle, _ink_target(ed), 0.10, RO_BAR_MIN_DIST)
    return c


def _status_bar_bg(pal):
    """底部字数状态条底色：**主题色的沉一档**，换主题时色相跟着走。

    三步推导：
      1. 稿纸往墨（深底则往白）压到反差达标 —— 先定住明度档位；
      2. 往 accent 染一层 —— 只借色相，绿皮肤得绿灰、蓝皮肤得蓝灰；
      3. 与「淡纸片」提示条 / 未选中标签不够远时，再往墨沉一点点。
    第 3 步不能省：提示条也是「稿纸 + accent」，只染色相两块会撞成同色
    （绿皮肤实测只差 9~16/255），上下两条带看着像一对。深底皮肤里 ink 是白，
    第 3 步自然变成「提亮一档」，方向不用分叉写。
    """
    ed = pal.get("editor") or "#ffffff"
    acc = pal.get("accent") or "#2f7cf6"
    ink = _ink_target(ed)
    t, bg = FOOTER_MIX, _mix_hex(ed, ink, FOOTER_MIX)
    while _contrast_ratio(bg, ed) < FOOTER_MIN_RATIO and t < 0.35:
        t += 0.02
        bg = _mix_hex(ed, ink, t)
    tinted = _mix_hex(bg, acc, FOOTER_TINT)
    ro = _readonly_bar_bg(pal)
    idle = _tab_idle_bg(_toolbar_bg(pal))
    k, c = 0.0, tinted
    while ((_color_dist(c, ro) < FOOTER_BAND_MIN_DIST or
            _color_dist(c, idle) < FOOTER_BAND_MIN_DIST) and k < 0.20):
        k += 0.02
        c = _mix_hex(tinted, ink, k)
    return c


def _status_bar_rule(pal, bg=None):
    """状态条上沿 1px 细线：从状态条自己的底色再压一档，不借 pal["line"]。

    pal["line"] 只比底色深 5%，1px 上等于没画（顶栏那条就栽在这上面）。
    底色带主题色相，细线也就自动跟着主题走，不用再单独染色。
    """
    if bg is None:
        bg = _status_bar_bg(pal)
    return _mix_hex(bg, _ink_target(bg), FOOTER_RULE_MIX)


def _status_bar_fg(pal, bg=None):
    """状态条文字：soft 往主文字色提，直到 9pt 小字在这块底上读得清。

    底染了主题色、又比原来沉一档，soft 直接铺上去只有 4.1~4.5 的对比
    （深色皮肤最差）。沿 soft→text 逐档提，达标即停；深色皮肤的 text 是
    浅色，方向自动反过来。
    """
    if bg is None:
        bg = _status_bar_bg(pal)
    soft = pal.get("soft") or "#76767e"
    text = pal.get("text") or "#1f1f24"
    t, fg = FOOTER_FG_MIX, _mix_hex(soft, text, FOOTER_FG_MIX)
    while _contrast_ratio(fg, bg) < FOOTER_FG_MIN_RATIO and t < 0.95:
        t += 0.05
        fg = _mix_hex(soft, text, t)
    return fg


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


def _current_skin_dir():
    """当前生效的皮肤目录（绝对路径）；没装 WorkBuddy / 读不到时返回 None。

    ⚠️ 不能只按目录 mtime 挑「最近那个」：WorkBuddy 会不时重写旧皮肤目录，
    实测三套皮肤里**最早**那套的 mtime 反而最新 —— 于是「当前皮肤」读到的是
    别套的主题色（绿皮肤读出蓝的主题色，整窗 accent 都错）。所以优先读偏好
    文件里记的那一套（`appearanceTheme` / `appearance.themeSelection.resourceKey`
    = 目录名前缀），只有读不到才退回 mtime。
    """
    try:
        if not os.path.isdir(SKIN_DIR):
            return None
        dirs = [os.path.join(SKIN_DIR, d) for d in os.listdir(SKIN_DIR)]
        dirs = [d for d in dirs if os.path.isdir(d)]
        if not dirs:
            return None
        key = _skin_pref_key()
        if key:
            for d in sorted(dirs, key=lambda p: os.path.getmtime(p), reverse=True):
                if os.path.basename(d) == key or os.path.basename(d).startswith(key + "-"):
                    return d
        # 兜底：最近修改的皮肤目录（皮肤目录名以时间戳结尾）
        return max(dirs, key=lambda p: os.path.getmtime(p))
    except Exception:
        return None


def _skin_pref_key():
    """从 WorkBuddy 的偏好文件里读「当前皮肤」的目录名（resourceKey），读不到返回 None。"""
    try:
        names = [n for n in os.listdir(SKIN_PREF_ROOT) if n.startswith("user-")]
    except Exception:
        return None
    paths = []
    for n in names:
        p = os.path.join(SKIN_PREF_ROOT, n, "global", "preferences.json")
        if os.path.isfile(p):
            paths.append(p)
    # 多账号时取最近写的那份
    paths.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    for p in paths:
        try:
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                d = json.load(f)
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        # 老版本平铺，新版本嵌在 appearance.themeSelection 里，两处都认
        for val in ((d.get("appearanceTheme") or ""),
                    ((d.get("appearance") or {}).get("themeSelection") or {})
                    .get("resourceKey") or ""):
            if isinstance(val, str) and val.startswith("theme-"):
                return val
    return None


def _skin_css_path(skin_dir):
    """皮肤目录里的 CSS 文件（优先 skin.v2.css / skin.css，否则任意 .css）。"""
    for name in ("skin.v2.css", "skin.css"):
        p = os.path.join(skin_dir, name)
        if os.path.isfile(p):
            return p
    for root, _sub, files in os.walk(skin_dir):
        for fn in files:
            if fn.endswith(".css"):
                return os.path.join(root, fn)
    return None


def skin_look():
    """WorkBuddy **当前**外观里对素笺有用的那几个色；读不到返回 {}。

    两条信息一起取（一次读文件）：
      * 款式色（top / side / list / editor / line / text / soft）—— 顶栏、侧栏、
        列表、稿纸这些「大块面」的底色与字色，森林绿整窗跟着它走；
      * accent —— 主题色（按钮 / 链接那个强调色）。

    ⚠️ 只采用「亮色块」里的变量：`_parse_skin_css` 会跳过含 dark 的选择器。
    """
    if not USE_SKIN:
        return {}
    try:
        cur = _current_skin_dir()
        if not cur:
            return {}
        css_path = _skin_css_path(cur)
        if not css_path:
            return {}
        variables = _parse_skin_css(css_path)
        if not variables:
            return {}
        out = {}
        for key, names in SKIN_BASE_VARS.items():
            for n in names:
                c = _norm_hex(variables.get(n) or "")
                if c:
                    out[key] = c
                    break
        for n in SKIN_ACCENT_VARS:
            c = _norm_hex(variables.get(n) or "")
            if c:
                out["accent"] = c
                break
        out["_source"] = css_path
        return out
    except Exception:
        return {}


def skin_accent(fallback=None):
    """WorkBuddy **当前**的主题色（按钮 / 链接用的那个强调色）；读不到返回 fallback。"""
    return skin_look().get("accent") or fallback


def _doodle_veil(pal):
    """涂鸦蒙层的整套配色，全部从**当前皮肤**现推（换肤时 apply_palette 重算）。

    ⚠️ 薄雾不是真半透明：色键窗口里只有 stipple 的网点是实色，其余像素全透。
    所以网点色得比「真半透明罩」更淡一档，别取 `pal["line"]`（只深 5%，
    1px 网点等于没画）；也别染 accent —— 会和只读提示条撞色。
    ⚠️ 深底皮肤里 `text` 是白，掺 ink 会自动变成提亮，不用另写暗色分支。
    """
    ed = pal.get("editor", "#ffffff")
    ink = pal.get("text", "#1f1f24")
    acc = pal.get("accent", "#333333")
    bar = _mix_hex(ed, ink, 0.03)
    return {
        "veil": _mix_hex(ed, ink, 0.18),
        "rule": _mix_hex(ed, ink, 0.30),
        "bar": bar,
        "line": _mix_hex(ed, ink, 0.22),
        "ink": ink,
        "soft": pal.get("soft", "#76767e"),
        "sel": _mix_hex(bar, acc, 0.20),
        "hover": _mix_hex(bar, ink, 0.07),
        "active_fg": pal.get("selfg", _mix_hex(ink, acc, 0.55)),
        "accent": acc,
    }


# ======================================================================
# 主题注册表 & 运行期切换
#
# 主题 = 一份 base 调色板（外加可选的强度微调 tuning）。所有「状态色」仍由上方
# 那一整套纯函数从 base 现推 —— 这里只负责「选哪份 base、要不要临时改强度、
# 最后跑一遍 _harmonize」。换主题不改任何配色逻辑，只换 base。
#
# spec 的键：
#   base          —— 该主题的基础调色板
#   tuning        —— 可选 dict，键名 = 上方模块级强度常数（HOVER_SHADE 等）
#   skin_surfaces —— 可选，True 表示整窗跟 WorkBuddy 当前外观走：顶栏 / 侧栏 /
#                    列表 / 稿纸 / 线条 / 文字取皮肤，accent 取主题色
#                    （目前只有「森林绿」。原来那个独立的「跟随 WorkBuddy 外观」
#                    主题已下线 —— 能力并进森林绿，列表里不再多一项）
# ======================================================================
THEMES = {
    "light":    {"base": LIGHT},
    "dark":     {"base": DARK},
    "sepia":    {"base": SEPIA},
    "forest":   {"base": FOREST, "tuning": {"SEL_TINT": 0.30},
                 "skin_surfaces": True},
    "midnight": {"base": MIDNIGHT},
    "sky":      {"base": SKY},
}

# 旧数据里存的 "skin"（已下线的「跟随 WorkBuddy 外观」）落到森林绿 —— 能力已经
# 并进森林绿（整窗跟着 WorkBuddy 外观走），观感完全一致，老用户升级不会换样。
# ⚠️ 首次打开软件的默认色 = 森林绿：全新库里没有 settings["theme"]，
# `_read_theme()` / `build_palette()` 都回落到它；`_ensure_default_theme()`
# 还会在首次启动时把这次默认写进库，之后只认用户自己换过的主题。
DEFAULT_THEME = "forest"

# 显示名一律按「色系意象」起，六套同一副腔调：森林绿（绿）/ 云母白（白稿纸 + 浅灰）
# / 石墨灰（中性炭灰 + 琥珀金）/ 羊皮纸（暖米）/ 午夜蓝（藏蓝）/ 晴空蓝
# （浅天蓝，和午夜蓝一浅一深成对）。
# ⚠️ 落库的是左边的 id，改文案不许动 id，老用户已选的主题才不会串。
# ⚠️ 这里只管 id → 显示名，**展示顺序见下面 THEME_ORDER**，别把字典插入顺序当
#    色卡顺序用；新主题仍追加在 THEMES / THEME_LABELS 末尾。
THEME_LABELS = {
    "light":    "云母白",
    "dark":     "石墨灰",
    "sepia":    "羊皮纸",
    "forest":   "森林绿",
    "midnight": "午夜蓝",
    "sky":      "晴空蓝",
}

# ----------------------------------------------------------------------
# 展示顺序（全局设置里那排色卡，以及任何按 list_themes() 列主题的地方）。
# ⚠️ 默认主题「森林绿」排第一格：首次打开就是它，色卡第一眼该看见正在用的这套。
#    注册表 THEMES 保持历史顺序（新主题继续追加末尾），展示顺序在这里单独调 ——
#    只动 UI 排列，不动任何主题 id / 配色 / 落库值。
# ⚠️ 加新主题要登记三处：THEMES + THEME_LABELS + 本元组末尾。忘登记也别慌：
#    list_themes() 会按 THEMES 顺序把漏掉的补在最后，不会静默少一套。
# ----------------------------------------------------------------------
THEME_ORDER = (
    "forest", "light", "dark", "sepia", "midnight", "sky",
)

# 森林绿里 accent 与「选中行文字」都是运行期算出来的（base 是静态表，跟不上皮肤）


def _forest_base():
    """森林绿的 base：**整窗跟 WorkBuddy 当前外观走**（顶栏 / 侧栏 / 列表 / 稿纸 /
    线条 / 文字），accent 用它的主题色，selfg 由 accent 现推。

    读不到皮肤、或皮肤给不出必备的款式键时，退回 FOREST 自带的那套（中性浅灰 +
    绿），保证任何机器上都有完整配色。
    """
    base = dict(FOREST)
    look = skin_look()
    if all(k in look for k in SKIN_REQUIRED):
        for k in SKIN_REQUIRED + ("line", "soft"):
            if k in look:
                base[k] = look[k]
    acc = look.get("accent") or FOREST_ACCENT_FALLBACK
    # 主题色太淡（近白 / 近灰）：掺出来的选中行会和悬停撞色，退回自带的绿
    if _color_dist(acc, base.get("list") or "#f4f4f6") < ACCENT_MIN_DIST:
        acc = FOREST_ACCENT_FALLBACK
    base["accent"] = acc
    return base


def _accent_fg(acc, bg):
    """「选中行文字 / 强调字」：主题色往墨里沉，直到在这块底上够读（AA 4.5）。

    只借主题色的色相 —— 主题色是蓝的，选中文字就是深蓝。不再写死深绿：
    那套值是「森林绿自带绿」时代的口径，accent 换成 WorkBuddy 的主题色后就不搭了。
    每轮往墨里混 16%，最多 11 轮（再深也到不了 4.5 就说明底太黑，交给调用方兜底）。
    """
    bg = bg or "#ffffff"
    target = _ink_target(bg)
    c = acc or FOREST_ACCENT_FALLBACK
    for _ in range(11):
        if _contrast_ratio(c, bg) >= 4.5:
            break
        c = _mix_hex(c, target, 0.16)
    return c


def on_accent(bg):
    """**实心 accent 块上的文字色**（按钮 / 日历选中日）：默认白字，白字糊了才换墨字。

    不能一律写死白字：accent 不再只有蓝 / 绿这类深色。石墨灰的 accent 是琥珀金
    （#e0a24a），白字压上去只有 2.2:1，糊成一片；换墨字立刻 7.7:1。阈值 3.0
    （按钮短文本按 WCAG 大字标准）——云母白（3.95）、羊皮纸（4.23）、森林绿
    （4.16）、午夜蓝（3.22）、晴空蓝（3.16）都仍然用白字，观感不变。
    """
    bg = bg or FOREST_ACCENT_FALLBACK
    if _contrast_ratio("#ffffff", bg) >= 3.0:
        return "#ffffff"
    return "#1b1b1f"


def build_palette(theme_id):
    """按主题 id 造出最终调色板（base + 可选 tuning + _harmonize 推导状态色）。

    取代原先「import 时算一次就定死」的 PALETTE：现在主题可枚举、可切换、可持久化。
    id 不认识（含已下线的 "skin"）一律走 DEFAULT_THEME，不让旧库读出一个空主题。
    """
    spec = THEMES.get(theme_id) or THEMES[DEFAULT_THEME]
    base = _forest_base() if spec.get("skin_surfaces") else dict(spec.get("base") or LIGHT)
    merged = dict(base)
    # 缺的键用 PALETTE_DEFAULTS 兜底，保证 _harmonize / 各派生函数不 KeyError
    for k, v in PALETTE_DEFAULTS.items():
        merged.setdefault(k, v)
    merged.setdefault("link", LINK)
    tuning = spec.get("tuning")
    if not tuning:
        pal = _harmonize(merged)
    else:
        # 临时替换模块级强度常数再推导，用完原样还原（不污染其它主题）
        saved, g = {}, globals()
        for k, v in tuning.items():
            if k in g:
                saved[k] = g[k]
                g[k] = v
        try:
            pal = _harmonize(merged)
        finally:
            for k, v in saved.items():
                g[k] = v
    if spec.get("skin_surfaces"):
        # 选中行文字跟着换过的 accent 走（_harmonize 已经算好 sel，直接拿它当底）
        pal["selfg"] = _accent_fg(pal.get("accent"), pal.get("sel"))
    return pal


def list_themes():
    """[(id, 显示名), ...]，按 THEME_ORDER 的展示顺序列出（默认主题森林绿在首位）。

    THEME_ORDER 里没登记的新主题按注册表顺序补在末尾；已下线却还留在顺序表里的
    id 直接跳过。所有枚举主题的地方都走这里，别自己遍历 THEMES 猜顺序。
    """
    order = [tid for tid in THEME_ORDER if tid in THEMES]
    order += [tid for tid in THEMES if tid not in order]
    return [(tid, THEME_LABELS.get(tid, tid)) for tid in order]


def theme_label(tid):
    return THEME_LABELS.get(tid, tid)


# 启动时决定配色：默认主题（森林绿 = 中性浅灰底 + WorkBuddy 当前主题色）。
# 最后再过一道 _harmonize：base 里那几个「状态色」一律改成由底色推导，
# 免得换主题时又出现「悬停和选中同色」这类问题（见上面 _harmonize 的说明）。
PALETTE = build_palette(DEFAULT_THEME)
