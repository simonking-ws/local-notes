# -*- coding: utf-8 -*-
"""素笺 —— 自绘控件层：圆角按钮、滑杆、滚动条、弹出菜单、待办框、代码块，
以及代码块用到的格式化与语法高亮配色。

这一层只画控件、不管业务：需要的东西（app / pal）都由外部传进来。
"""
import os
import re
import math
import json
import unicodedata
import tkinter as tk
import tkinter.font as tkfont

# DPI 缩放：下面的像素常量都要经过 dpi.px()，否则 125% / 150% 下容器不动、
# 字体却按系统比例变大，整排控件会互相挤。SCALE 在 notes_app.py 导入本模块
# **之前**就由 dpi.init() 定好，所以这里写 `dpi.px(30)` 是安全的一次性换算。
import dpi
import win_frame

# 颜色运算与表面色统一在 theme 里（纯函数，测试不建 GUI 也能断言）。
# 其中下划线那批是转出口：app_ui 从 widgets 这里引它们（历史沿革），别顺手删。
from theme import (FONT, _RES, _mix_hex, _hex_rgb, _contrast_ratio, _ink_target,
                   _luminance, _hover_bg, _press_bg,
                   _sel_bg, _quote_bg, _icode_bg,
                   _search_hit_bg, _popover_bg, _popover_border,
                   TOOL_BG_MIX, _toolbar_bg, _tab_idle_bg,
                   _readonly_bar_bg, _status_bar_bg, _status_bar_rule,
                   _status_bar_fg, on_accent)

# 列表标记 / 缩进常量在 md_conv 里（纯字符串逻辑，不依赖 tkinter，能直接测）——
# 谁要用就 `from md_conv import`，这里不再代转。
import sql_fmt
import punct


# 智能分类（smart=True）用区别图标，普通文件夹用文件夹图标
SMART_ICON_ALL = "f_all"     # 全部笔记 → 清单三线
SMART_ICON_STAR = "f_star"   # 星标笔记 → 五角星（金色）
SMART_ICON_LOCK = "f_lock"   # 加密笔记 → 挂锁

# 文件夹图标画布尺寸（原 16x14 偏小，圆角细节放不下）
FOLDER_ICON_W = dpi.px(18)
FOLDER_ICON_H = dpi.px(16)


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


# ---------- 抗锯齿栅格化（Tk Canvas 走 GDI，完全没有 AA） ----------
# `create_oval` / `create_arc` 在几十像素的小圆上必然是硬边台阶，椭圆描边更会被
# 光栅掰成一圈粗细不均的方块 —— 全局设置里六张主题色卡的「毛刺」就是这个。
# 项目里 ≤18px 的小图标早改成整数矩形拼，但「圆」拼不出圆滑边缘，所以这里换一条
# 路：自己按像素算覆盖率，最后一次性写进 PhotoImage 贴到 Canvas。
#
# 形状统一用**有符号距离函数** sdf(x, y) 描述（负 = 内部，|∇sdf| ≤ 1）。
# 这样内部 / 外部像素可以用「中心距离 ±0.707」一次判定跳过，只有边界一圈像素
# 才做 4×4 子采样 —— 一张 76×60 的卡约两万次判定，换肤时重算也不卡。
AA_SS = 4
_AA_OFF = tuple((i + 0.5) / AA_SS for i in range(AA_SS))
_AA_DIAG = math.sqrt(0.5)          # 像素中心到角 ≈ 0.7071
_AA_SS2 = float(AA_SS * AA_SS)


class AAImage(object):
    """一张按覆盖率混色的 RGB 位图；画完用 `to_photo()` 交给 Tk。

    逐层 `fill()` 用 painter 模型叠加：`底色 → 形状色`，边缘像素按覆盖率混色。
    整个过程纯 Python + Tk，不依赖 Pillow（文档承诺零依赖）；颜色都是运行时的
    主题色，换主题 / 悬停能即时重算，不用预渲染一套资源。
    """

    def __init__(self, w, h, bg):
        self.w, self.h = int(w), int(h)
        self.rows = [[bg] * self.w for _ in range(self.h)]

    @classmethod
    def from_rows(cls, w, h, rows):
        """复用一份已栅格化的行数据（纯字符串，不绑定 Tk 解释器）。

        色卡的悬停 / 换主题状态是有限组合，把栅格化结果按状态缓存起来，
        高频的 `<Enter>/<Leave>` 就只剩一次 `to_photo()` 的像素写入。
        """
        obj = cls.__new__(cls)
        obj.w, obj.h, obj.rows = int(w), int(h), rows
        return obj

    def fill(self, color, sdf, bbox=None):
        """把 sdf < 0 的区域涂成 color；bbox 只用来少遍历，不必精确。"""
        x0, y0, x1, y1 = bbox if bbox else (0, 0, self.w, self.h)
        xa, xb = max(0, int(math.floor(x0))), min(self.w, int(math.ceil(x1)))
        ya, yb = max(0, int(math.floor(y0))), min(self.h, int(math.ceil(y1)))
        for y in range(ya, yb):
            row = self.rows[y]
            py = y + 0.5
            for x in range(xa, xb):
                d = sdf(x + 0.5, py)
                if d >= _AA_DIAG:
                    continue                       # 整像素都在形状外
                if d <= -_AA_DIAG:
                    row[x] = color                  # 整像素都在形状内
                    continue
                hit = 0
                for oy in _AA_OFF:
                    yy = y + oy
                    for ox in _AA_OFF:
                        if sdf(x + ox, yy) < 0.0:
                            hit += 1
                if hit:
                    a = hit / _AA_SS2
                    row[x] = color if a >= 1.0 else _mix_hex(row[x], color, a)
        return self

    def to_photo(self, master, target=None):
        """一次性写入 PhotoImage。target 复用时只清空重填，不新建对象。"""
        data = " ".join("{" + " ".join(row) + "}" for row in self.rows)
        if target is None:
            target = tk.PhotoImage(width=self.w, height=self.h, master=master)
        else:
            target.blank()
        target.put(data, to=(0, 0))
        return target


def aa_disc(cx, cy, r):
    """圆盘。返回 (sdf, bbox)。半径用浮点，不再交给 Tk 的整数包围盒。"""
    r = float(r)

    def sdf(x, y):
        return math.hypot(x - cx, y - cy) - r

    return sdf, (cx - r - 1, cy - r - 1, cx + r + 1, cy + r + 1)


def aa_ring(cx, cy, r_outer, width):
    """圆环：外半径 r_outer，向内占 width（与 Tk 描边向内的行为一致）。"""
    ro, ri = float(r_outer), max(0.0, float(r_outer) - float(width))
    mid, half = (ro + ri) / 2.0, (ro - ri) / 2.0

    def sdf(x, y):
        return abs(math.hypot(x - cx, y - cy) - mid) - half

    return sdf, (cx - ro - 1, cy - ro - 1, cx + ro + 1, cy + ro + 1)


def aa_left_half(cx, cy, r):
    """左半圆：圆内且 x < cx。用来画色卡里顶栏色的那一半。"""
    disc, bb = aa_disc(cx, cy, r)

    def sdf(x, y):
        return max(disc(x, y), x - cx)

    return sdf, (bb[0], bb[1], cx + 1, bb[3])


def aa_round_rect(x0, y0, x1, y1, radius):
    """圆角矩形的精确 SDF（四个角是圆，直身段是矩形）。"""
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    hw, hh = (x1 - x0) / 2.0 - radius, (y1 - y0) / 2.0 - radius

    def sdf(x, y):
        dx, dy = abs(x - cx) - hw, abs(y - cy) - hh
        return (min(max(dx, dy), 0.0)
                + math.hypot(max(dx, 0.0), max(dy, 0.0)) - radius)

    return sdf, (x0 - 1, y0 - 1, x1 + 1, y1 + 1)


def aa_outline(sdf, width):
    """把任意形状的 SDF 变成「沿边界一圈」的带状 SDF，用于描边。"""
    half = float(width) / 2.0
    return lambda x, y: abs(sdf(x, y)) - half


def aa_segment(x0, y0, x1, y1, width):
    """圆头线段（点到线段的距离 - 半线宽），用来画对勾这类折线。"""
    vx, vy = x1 - x0, y1 - y0
    seg2, half = vx * vx + vy * vy, float(width) / 2.0

    def sdf(x, y):
        t = 0.0 if seg2 == 0 else max(
            0.0, min(1.0, ((x - x0) * vx + (y - y0) * vy) / seg2))
        return math.hypot(x - (x0 + t * vx), y - (y0 + t * vy)) - half

    return sdf, (min(x0, x1) - half - 1, min(y0, y1) - half - 1,
                 max(x0, x1) + half + 1, max(y0, y1) + half + 1)


# 颜色运算与状态色（_mix_hex / _shade / _hover_bg / _quote_bg / _popover_bg ...）
# 全部在 theme 里实现：纯函数、不依赖 tkinter，测试可以直接断言色差。
# 这里只是 import 进来 re-export，调用方（widgets / app_ui）的写法不用变。


# ---------- 弹窗就地换肤 ----------
# 模态弹窗（全局设置 / 数据管理 / 关于）的底色 / 字色都是**建窗时**逐控件写死的，
# 主窗的 apply_palette 管不到它们。以前在弹窗里换主题只能 destroy + 重建整张窗，
# 窗口消失又出现的那一帧正是用户说的「闪一下」。下面两个函数让这类弹窗能留着
# 窗口原地重刷：把每个颜色 option 按「旧色 → 调色板角色 → 新色」翻译一遍即可，
# 勾选、字号、焦点、滚动位置全都在控件身上，自然一个都不丢。
DIALOG_COLOR_OPTS = (
    "background", "foreground", "activebackground", "activeforeground",
    "selectcolor", "selectbackground", "selectforeground",
    "disabledforeground", "insertbackground", "readonlybackground",
    "highlightbackground", "highlightcolor", "troughcolor",
)


def remap_palette_color(color, old_pal, new_pal, keys=None):
    """把「按旧调色板写死的颜色」翻译成新调色板里的对应色。

    `keys` 可以限定参与翻译的键，避开 top / side 这类同色键的歧义；不在旧板里的
    颜色（系统色、由底色现推的派生色、空值）一律原样返回 —— 别把控件自己的
    状态色误当成主题色改掉。
    """
    if not color or not isinstance(color, str):
        return color
    low = color.lower()
    for k in (keys if keys is not None else old_pal.keys()):
        v = old_pal.get(k)
        if isinstance(v, str) and v.lower() == low:
            return new_pal.get(k, color)
    return color


def repaint_widget_colors(root, old_pal, new_pal, keys=None):
    """就地重刷一棵控件树的颜色 option；**不销毁、不重建**任何控件。

    只认 remap_palette_color 认得出来的颜色，所以 ttk 控件、系统默认色会自动跳过
    （控件不支持的选项也一样）。Canvas 上的图元不归这里管 —— 自绘控件各自有
    apply_theme / build，由调用方在重刷控件之后再调一次，把笔画也画成新色。
    """
    todo = [root]
    while todo:
        w = todo.pop()
        try:
            todo.extend(w.winfo_children())
        except tk.TclError:
            pass
        for opt in DIALOG_COLOR_OPTS:
            try:
                cur = w.cget(opt)
            except (tk.TclError, AttributeError):
                continue
            new = remap_palette_color(cur, old_pal, new_pal, keys)
            if new != cur:
                try:
                    w.configure(**{opt: new})
                except tk.TclError:
                    pass


# 应用中文名 APP_NAME 已随数据层迁到 store.py（窗口标题 / 示例笔记 / 导出文件名共用）

# 编辑去抖：连续输入时把「序列化落库」和「列表行刷新」合并成一次，避免每敲一键都
# 同步做全篇 serialize + SQLite 事务提交（长笔记下可达数百毫秒），把 Tk 的重绘挤到
# 后面造成打字滞后。内存里的 self.data 仍然每次立即更新，只是落盘延后。
SAVE_DEBOUNCE_MS = 600          # 停顿这么久没有任何输入就把数据写进数据库
ROW_REFRESH_DEBOUNCE_MS = 160   # 列表行标题/预览的刷新间隔（仍快到来得及跟手）
READONLY_BAR_H = dpi.px(26)     # 「内置示例笔记 · 不可编辑」提示条的高度

# 主按钮（实心强调色）往深色压多少。普通按钮的悬停/按下没有固定权重了：
# 由 theme._hover_bg / _press_bg 从「按钮所在区域的底色」推（HLS 里只动明度），
# 绿工具带悬停就还是绿、灰侧栏悬停就还是灰，换皮肤也不用逐处调。
BTN_PRESS_MIX = 0.18
BTN_RADIUS = dpi.px(6)      # 圆角收小一档：更接近编辑器工具条的「方中带圆」
TOOLBAR_H = dpi.px(36)      # 工具栏高度；下方还有 1px 分隔线
# 工具栏内分隔线：底色向次级文字色混合的比例。工具带本身压得很轻，分区感主要交给
# 这条线和底部的收口线来交代，所以线要够实，不能跟着底色一起变淡。
TOOL_LINE_MIX = 0.42
# 工具带底色 = 侧栏色朝稿纸色提亮 TOOL_BG_MIX（常量与推导都在 theme 里，
# 这里 re-export 只是让 app_ui 沿用的老写法不动）。方向是「提亮」而不是「压暗」：
# 工具带排版的是正文，底色该站在「侧栏 → 工具带 → 稿纸」这条渐亮序列的中间。
# 压暗这条路试过两版——0.10 在浅绿皮肤下被压成一条深色横杠，0.05 用户仍反馈偏暗。
# TOOL_BG_MIX 由上面的 theme import 提供（转出口，别在这里另写一份 0.55）。

# 编辑区右侧竖向滚动条的宽度（place 时显式给宽，不取 ttk 默认值）。
# 浮动导航要靠它算自己的右边界，所以抽成常量 —— 两处必须同源。
VSCROLL_W = dpi.px(17)

# 编辑区右侧居中的浮动导航（回顶部 / 回底部）
JUMP_W = dpi.px(26)     # 胶囊宽度
JUMP_H = dpi.px(62)     # 胶囊高度（上下各一枚双箭尖）
JUMP_GAP = dpi.px(2)    # 胶囊与竖向滚动条之间的间隙（胶囊在滚动条**左侧**，不压滚动条）
# 正文左右留白：对称的 25（设计稿口径）。滚动条（17）和浮动导航胶囊都摆在这条留白里，
# 留白**不给它们划车位** —— 它只服务正文的行宽和观感，所以胶囊会压到行尾一个字。
# ⚠️ 别改回 `JUMP_W + JUMP_GAP + px(8)`：那版为了让胶囊不压字，把右留白撑到 42，
# 左右不对称。用户明确要求「胶囊悬浮、不受边距限制」，2026-11-05 又确认接受压字。
TEXT_PADX = dpi.px(25)
# 正文上下留白。顶部口径：「正文区上沿 → 首行字盒顶」= TEXT_PADY + title 的
# spacing1(8) = 12 + 8 = 20，与 2026-11-05 改版前（20 + 0）**完全一致**。
# ⚠️ 首行现在完整保留 title 的 8/8 段间距：旧版挂 firstline 把 spacing1 压成 0，
# 于是选中标题时色块上方 0、下方 8，上下看着不一致。改成「让内边距让出这 8px」后，
# 行距属性对称、离顶距离又没变。别再退回 firstline，更别把 title 的 spacing1
# 直接改成 0 —— 那会连全文所有 H1 的段前距一起砍掉（见 app_ui._config_tags）。
# 底部留白 = TEXT_PADY(12) + Text 的 pack 外边距 FOOTER_GAP(8) = 20，与改版前
# 一致；状态条高度 FOOTER_H 已从这里解耦（见 app_ui 顶部），别再把两者绑在一起。
TEXT_PADY = dpi.px(12)


def line_spacing(up, down, wrap=None):
    """行级标签的 spacing1/2/3 —— 三件套必须一起给，缺一件行距就不齐。

    Tk 的语义：`spacing1` 加在**逻辑行**（一个 \\n 结尾的段落）首条显示行的上方，
    `spacing3` 加在末条显示行的下方，两条逻辑行之间的基线差因此是「行高 + 上 + 下」；
    而段内**自动折行**的显示行之间吃的是 `spacing2`（默认 0），基线差只有行高。
    结果就是「回车换行比自动折行松 (上+下)」—— 用户 2026-11-05 反馈的正是这个。

    所以默认让 `spacing2 = up + down`：折行与回车的行距完全一致（正文实测 24 vs 24）。
    `wrap` 用来给标题类开小灶 —— H1 的段间距是 8+8，两行标题之间塞 16 会像两个段落，
    给一半（wrap=8）就够了。
    """
    wrap = up + down if wrap is None else wrap
    return {"spacing1": dpi.px(up), "spacing2": dpi.px(wrap), "spacing3": dpi.px(down)}

# 列表圆角卡片：左右留白 + 行间留白，选中/悬停时行会浮起成一张卡片
CARD_RADIUS = dpi.px(9)
CARD_PADX = dpi.px(8)
CARD_GAP = dpi.px(2)

# 多级文件夹：每深一层整张卡片往右挪这么多像素（层级靠缩进读出来）。
# 14 是「比文件夹图标窄一点」的量：缩进一眼可见，又不至于把深层的名字挤到没处站。
FOLDER_TREE_INDENT = dpi.px(14)
# 树节点前面的展开箭头所占的一格（**点击区**，不是箭头本身的宽度）。
# 原来这里是 13（宽度）：画布只有 13×16、箭头是 8px 的实心三角，用户反馈
# 「太小、不知道哪儿能点」。先放到 18×18，后又反馈「还是不好点」—— 现在
# 高 28（= 左栏一行的可点高度，行高 38~39 由文字撑起来，这一格正好填满）、宽 24，
# 面积是 18×18 时的两倍多。⚠️ 点击区**不许缩回去**（两次反馈都指向「不好点」）。
# 箭头本身是 9px 填充实心三角（2026-11-06 方案 C），常态就看得出可点，
# 不再全靠 hover 底片交代。叶子这一列留空占位，名字起点才对齐。
FOLDER_TWISTY_W = dpi.px(24)
FOLDER_TWISTY_H = dpi.px(28)

# 「移动笔记到…」的选择树：一行 28px（比左栏卡片矮一档，弹窗里更紧凑），
# 每层缩进 18px —— 要同时容下「竖引导线 + 三角 + 图标」，比左栏的 14 宽一点才不挤。
PICK_ROW_H = dpi.px(28)
PICK_INDENT = dpi.px(18)

# 左侧两栏宽度：文件夹列 + 分隔线 + 笔记列表列
SIDEBAR_W = dpi.px(216)     # 文件夹列
LIST_W = dpi.px(300)        # 笔记列表列
# 搜索框：住在**文件夹列表顶部**（不再是顶栏），宽度跟着文件夹列走。
# 左右留白 12 与列表内容对齐 —— 上下同宽，侧栏看起来才是一条直上直下的竖边。
SEARCH_PADX = dpi.px(12)
# 两栏折叠时整列（含搜索框）一起收起，顶栏不留搜索入口，所以没有「折叠态宽度」。
SEARCH_H = dpi.px(32)

# 两栏可拖拽调宽：分隔线不再只是一根 1px 的装饰，而是一条 5px 的「抓取带」。
# 视觉上仍只画中间那 1px（粗细语言不变），两侧各 2px 是留给鼠标的余量 ——
# 纯 1px 的线在 125% 缩放下基本点不中，5px 是「够粗好抓、又看不出来」的折中。
SEP_HIT_W = dpi.px(5)
# 可拖范围。上限还得再受「编辑区至少留 EDITOR_MIN」约束（见 _split_limits），
# 否则两栏都拉满时正文会被挤成一条缝。下限取「名字还能读出来」的宽度。
SIDE_MIN, SIDE_MAX = dpi.px(150), dpi.px(360)
LIST_MIN, LIST_MAX = dpi.px(180), dpi.px(520)
EDITOR_MIN = dpi.px(320)

# 主题化确认对话框正文的最大行宽（像素），超出则截断名称，避免多折一行
DLG_TEXT_W = dpi.px(360)

# 工具栏图标按钮：图标画在 16x16 的方格内，四周留同样的内边距 → 约 34px 见方
ICON_SIZE = dpi.px(16)
ICON_STROKE = dpi.fx(1.7)   # 图标线宽（像素）；太细在高分屏上会糊，太粗显笨
ICON_TAIL_RATIO = 0.62  # 「文字 + 下拉箭头」组合里箭头相对图标方格的占比
ICON_TEXT_GAP = dpi.px(5)   # 组合按钮中文字与小箭头之间的间隙

# 文字按钮的垂直定位：大写字母墨迹半高 / ascent。
# 微软雅黑 12pt（em 16px）的 ascent 是 17px，B/I/S 的墨迹（cap 高）都是 12px，
# 12 / 2 / 17 ≈ 0.353。Tk 取不到 cap height（font metrics 只有 ascent/descent/
# linespace/fixed），只能按这个比例从 ascent 推；其它字号里该比例基本恒定。
TEXT_CAP_HALF_RATIO = 0.353


def _draw_icon(cv, name, cx, cy, s, color, stroke=ICON_STROKE):
    """在画布 (cx, cy) 处画一个 s x s 的线条图标，返回创建的图元 id 列表。

    全部用 Canvas 矢量图元而非字体符号：Tk 的 Tcl/Tk 8.6 在部分系统上不支持
    U+FFFF 以上的字符，用 emoji 当图标会直接画成方框；矢量图形则与字体无关，
    线宽也能随 DPI 调整，和高分屏下的自绘按钮语言一致。
    """
    ids = []
    x0, y0 = cx - s / 2.0, cy - s / 2.0     # 图标外框左上角
    kw = {"fill": color, "width": stroke, "capstyle": tk.ROUND,
          "joinstyle": tk.ROUND}
    line = lambda *pts: ids.append(cv.create_line(*pts, **kw))

    if name == "bullet":
        # 项目符号：左侧三个圆点 + 右侧三条横线
        for i in range(3):
            yy = y0 + s * (0.22 + 0.28 * i)
            ids.append(cv.create_oval(x0 + 0.5, yy - 1.5, x0 + 3.5, yy + 1.5,
                                      fill=color, outline=""))
            line(x0 + s * 0.42, yy, x0 + s, yy)
    elif name == "numbered":
        # 有序列表：左侧**两个序号 1 / 2** + 右侧两条横线（A 方案）。
        #
        # 为什么只画两个：16px 的格子里，三条横线的行距只有 0.28s ≈ 4.5px。
        # 要在每行塞一个可辨认的数字，最多只能分到 4~5px 高 —— 三个数字挤在
        # 一起时（上一版）每个只有 4 行、还首尾相接，1x 下就是一根竖虫。
        # 砍掉 "3" 之后每个数字能长到 5 行（16px 档 5px 高、4px 宽），中间还
        # 留得下 2px 的缝，1x 下也能一眼读出「1、2」。
        #
        # 数字是**手绘点阵字形**，不是 create_text：7pt 雅黑的行框有 14px，
        # 比行距还大，缩到 5/4/3pt 墨迹仍有 4px，ClearType 还会把 1.2px 的
        # 竖杆摊成浅黄/浅蓝的虚杆。整像素方块在 Tk 里是纯色实心，深浅与旁边
        # 1.7px 的横线一致。
        #
        # 高度跟邻居对齐：数字列总高取 round(0.75*s)，16px 档正好 12px ——
        # 和「项目符号」三条横线的墨迹同高，四个列表图标看上去一样大。
        #
        # 高 DPI 档按 s/16 放大点阵像素 p：16/20px 档 p=1、24px 起 p=2、
        # 48px 起 p=3、64px 起 p=4；数字之间的空行数由目标高度反推，整列
        # 永不超出格子。
        #
        # ⚠️ 行距必须用**整数算术**（top0 + i*pitch），不能逐行 round()：
        #     Python 的 round() 是银行家舍入（round(11.5)=12、round(16.5)=16），
        #     高 DPI 下会把两个数字算到同一行、粘成一块。
        _OL_GLYPHS = {
            "1": ("..#.", ".##.", "..#.", "..#.", ".###"),
            "2": ("###.", "..#.", ".#..", "#...", "###."),
        }
        ROWS = 5
        # 不用 round()：它是银行家舍入（round(2.5)=2），40px 档会停在 p=2、
        # 数字偏小。+0.5 再取整才是「四舍五入」。
        p = max(1, int(s / 16.0 + 0.5))                  # 点阵像素边长（整像素）
        target = max(ROWS * 2, int(round(0.75 * s)))     # 数字列总高（像素）
        gap = max(0, (target - p * ROWS * 2) // p)       # 数字之间的空行数
        if p > 1:
            gap = max(1, gap)                            # 高 DPI 下两个数字不能贴脸
        pitch = (ROWS + gap) * p                         # 相邻数字单元的间距
        total = p * (ROWS * 2 + gap)                     # 整列高度
        top0 = int(cy) - total // 2                      # 整列在图标里垂直居中
        dx0 = int(round(x0))                             # 数字列左沿（整像素）
        for i, ch in enumerate(("1", "2")):
            top = top0 + i * pitch
            for r, row in enumerate(_OL_GLYPHS[ch]):
                for c, cell in enumerate(row):
                    if cell == "#":
                        bx = dx0 + c * p
                        by = top + r * p
                        ids.append(cv.create_rectangle(bx, by, bx + p - 1,
                                                      by + p - 1,
                                                      fill=color, outline=""))
            # 横线与该行数字的行中心对齐；起点仍是 0.42s，和 ● / ➤ 保持一致
            yy = top + 2 * p
            line(x0 + s * 0.42, yy, x0 + s, yy)
    elif name == "todo":
        # 待办：空心方框 + 内嵌对勾。
        # 旧版的构图是「6.4px 小方框 + 勾 + 右侧两条横线」，16px 里塞三组元素，方框内部
        # 只剩 3px 还要走 1.7px 的对勾，实机看就是一团黑块（用户反馈"看不清"）。
        # 现在只保留一个足够大的方框，勾按框宽等比放大 —— 8x8 的画法在 16px 下必然糊。
        bw = s * 0.75
        bx = x0 + (s - bw) / 2.0
        ids.append(cv.create_rectangle(bx, cy - bw / 2.0, bx + bw, cy + bw / 2.0,
                                       outline=color, width=stroke))
        line(bx + bw * 0.20, cy + bw * 0.02,
             bx + bw * 0.42, cy + bw * 0.26,
             bx + bw * 0.82, cy - bw * 0.26)
    elif name == "triangle":
        # 三角列表标记：左侧实心朝右三角（与正文里的 ➤ 同形）+ 右侧三条横线。
        # 光一个实心三角在工具栏里会被读成「播放」按钮（实测放大 8 倍对比），加三条线
        # 与同样是「标记 + 三行」的项目符号 / 有序列表同构，才有「这是一种列表」的语义。
        # 三角宽度 0.34*s（≈5.8px）是 1.7px 描边之外还能看出「实心块」的下限，再窄就成一条斜杠。
        pts = [x0 + s * 0.02, y0 + s * 0.24, x0 + s * 0.36, cy,
               x0 + s * 0.02, y0 + s * 0.76]
        ids.append(cv.create_polygon(pts, fill=color, outline=""))
        for i in range(3):
            yy = int(y0 + s * (0.22 + 0.28 * i) + 0.5)
            line(x0 + s * 0.54, yy, x0 + s, yy)
    elif name == "more":
        # 更多：水平三个点
        for i in range(3):
            xx = cx + s * (i - 1) * 0.34
            ids.append(cv.create_oval(xx - 1.6, cy - 1.6, xx + 1.6, cy + 1.6,
                                      fill=color, outline=""))
    elif name == "chevron":
        # 下拉箭头：朝下的 V
        line(x0 + s * 0.18, cy - s * 0.12, cx, cy + s * 0.20,
             x0 + s * 0.82, cy - s * 0.12)
    elif name == "panel":
        # 折叠工具栏：上下两条横线 + 中间箭头（表示「收起/展开这一条」）
        line(x0, y0 + s * 0.10, x0 + s, y0 + s * 0.10)
        line(x0, y0 + s * 0.90, x0 + s, y0 + s * 0.90)
        line(x0 + s * 0.22, y0 + s * 0.58, cx, y0 + s * 0.32,
             x0 + s * 0.78, y0 + s * 0.58)
    elif name == "undo":
        # 还原 / 撤销：左侧箭头 + 向右兜回来的半圆。半圆用 create_arc（真圆弧），
        # 不用 create_line(smooth=True)——后者在 16px 上会抖成三段折线。
        line(x0 + s * 0.32, y0 + s * 0.21, x0 + s * 0.11, y0 + s * 0.43,
             x0 + s * 0.32, y0 + s * 0.65)
        line(x0 + s * 0.11, y0 + s * 0.43, x0 + s * 0.58, y0 + s * 0.43)
        ids.append(cv.create_arc(x0 + s * 0.35, y0 + s * 0.43,
                                 x0 + s * 0.81, y0 + s * 0.89,
                                 start=90, extent=-180, style=tk.ARC,
                                 outline=color, width=stroke))
    elif name == "sliders":
        # 设置 / 偏好：三条横线 + 各一个滑块。齿轮要在 16px 里画 8 个齿加内孔，
        # 实机只剩一团墨点；滑杆三行结构均匀，缩到 16px 仍分得清行。
        for i, tx in enumerate((0.34, 0.68, 0.44)):
            yy = y0 + s * (0.24 + 0.26 * i)
            line(x0 + s * 0.12, yy, x0 + s * 0.88, yy)
            kx = x0 + s * tx
            ids.append(cv.create_rectangle(kx - s * 0.09, yy - s * 0.09,
                                           kx + s * 0.09, yy + s * 0.09,
                                           fill=color, outline=""))
    elif name == "pin":
        # 置顶：顶线 + 向上箭头。比画图钉好认——图钉的头和针在 16px 里会黏成一根。
        line(x0 + s * 0.20, y0 + s * 0.16, x0 + s * 0.80, y0 + s * 0.16)
        line(cx, y0 + s * 0.34, cx, y0 + s * 0.86)
        line(x0 + s * 0.28, y0 + s * 0.50, cx, y0 + s * 0.32,
             x0 + s * 0.72, y0 + s * 0.50)
    elif name == "star":
        # 星标：与列表行上的星标同一套 _star_points，只有大小不同
        ids.append(cv.create_polygon(_star_points(cx, cy, s * 0.46, s * 0.20),
                                     fill=color, outline=""))
    elif name == "trash":
        # 垃圾桶：提手 + 盖 + 梯形桶身。全部细长图元，互不重叠，16px 下不糊。
        line(x0 + s * 0.20, y0 + s * 0.27, x0 + s * 0.80, y0 + s * 0.27)
        line(x0 + s * 0.40, y0 + s * 0.17, x0 + s * 0.60, y0 + s * 0.17)
        line(x0 + s * 0.28, y0 + s * 0.27, x0 + s * 0.34, y0 + s * 0.85,
             x0 + s * 0.66, y0 + s * 0.85, x0 + s * 0.72, y0 + s * 0.27)
    elif name == "pencil":
        # 重命名：一支斜放的实心笔。整块填充、不描边——16px 里描边加内空会糊成墨块。
        ids.append(cv.create_polygon(
            x0 + s * 0.66, y0 + s * 0.14, x0 + s * 0.86, y0 + s * 0.34,
            x0 + s * 0.42, y0 + s * 0.78, x0 + s * 0.18, y0 + s * 0.84,
            fill=color, outline=""))
    elif name == "folder":
        # 文件夹：顶左的梯形标签 + 主箱体。一笔连成，避免两条线在 16px 里并成一条粗线。
        line(x0 + s * 0.14, y0 + s * 0.80, x0 + s * 0.14, y0 + s * 0.24,
             x0 + s * 0.40, y0 + s * 0.24, x0 + s * 0.50, y0 + s * 0.34,
             x0 + s * 0.86, y0 + s * 0.34, x0 + s * 0.86, y0 + s * 0.80,
             x0 + s * 0.14, y0 + s * 0.80)
    elif name == "move":
        # 移动到文件夹：一根右箭头 + 右端开口向左的方盒（→ ]）。
        # 菜单里「跳到所属文件夹」已经用了 folder 图标，同一张菜单不能两项同图标；
        # 这里也不硬塞「箭头 + 完整文件夹」——16px 里两个形状会黏成一团墨块，
        # 用「去别处 + 落进某个容器」这个更简的符号反而一眼能读。
        line(x0 + s * 0.06, cy, x0 + s * 0.56, cy)
        line(x0 + s * 0.36, y0 + s * 0.28, x0 + s * 0.58, cy,
             x0 + s * 0.36, y0 + s * 0.72)
        line(x0 + s * 0.66, y0 + s * 0.22, x0 + s * 0.92, y0 + s * 0.22,
             x0 + s * 0.92, y0 + s * 0.78,
             x0 + s * 0.66, y0 + s * 0.78)
    elif name == "quote":
        # 引用块：左侧一条竖线 + 右侧三条横线。竖线必须够高（满格），
        # 否则在 16px 里会被读成「字母 I」。
        line(x0 + s * 0.16, y0 + s * 0.10, x0 + s * 0.16, y0 + s * 0.90)
        for i in range(3):
            yy = y0 + s * (0.22 + 0.28 * i)
            line(x0 + s * 0.40, yy, x0 + s * 0.94, yy)
    elif name == "icode":
        # 行内代码：一对尖括号。反引号在这里会退化成两条斜线，认不出来。
        line(x0 + s * 0.40, y0 + s * 0.16, x0 + s * 0.14, cy,
             x0 + s * 0.40, y0 + s * 0.84)
        line(x0 + s * 0.60, y0 + s * 0.16, x0 + s * 0.86, cy,
             x0 + s * 0.60, y0 + s * 0.84)
    elif name == "code":
        # 代码块：一对花括号。`{}` 本身就是「代码块」的通行写法，胜过的方括号的
        # 三点理由：
        #   1. 墨迹 13x14，方括号只有 10x14 —— 方括号是整条工具栏里最瘦的图标
        #      （todo 14、numbered/bullet 16、triangle 17），两侧多空 3~7px，
        #      一排看过去显得不合群；
        #   2. 与 todo（实心方框 + 勾）不再有「都是方框」的联想；
        #   3. 尖括号已经被 icode（行内代码）占了，不能再撞一次。
        # 几何：横臂 0.11s 内缩、竖线走到腰前 0.16s 停、腰尖外凸 0.16s。
        # 这两个 0.16 是可辨性的关键 —— 腰尖只凸 0.12s 时，1.7px 的圆头描边会
        # 把它吃掉大半，8 倍放大看就是「方括号带一排毛刺」，认不出是花括号；
        # 凸到 0.16s 外缘刚好 14.8px，与 todo 方框（14px）同宽，一排里不显胖。
        # 再往上（0.20s）腰尖会顶到 16px 格子边缘，和相邻图标贴脸。
        # 腰前竖线之所以也要 0.16s 而不是更长：竖线越长，腰那两段斜线越短，
        # 尖角就越像竖线上的一个疙瘩。0.16 时竖线 3.7px、斜线 3.7px，两段等长。
        for sgn in (1, -1):
            mx = cx + sgn * s * 0.25            # 括号竖线
            tip = mx - sgn * s * 0.13           # 横臂端
            line(tip, y0 + s * 0.11, mx, y0 + s * 0.11,
                 mx, cy - s * 0.16,
                 mx + sgn * s * 0.16, cy,       # 腰部尖（向外）
                 mx, cy + s * 0.16,
                 mx, y0 + s * 0.89, tip, y0 + s * 0.89)
    elif name == "hr":
        # 分割线：中间一条通栏横线，上下各一条半宽短线，读作「文字 / 分割 / 文字」
        line(x0, cy, x0 + s, cy)
        line(x0 + s * 0.28, y0 + s * 0.16, x0 + s * 0.72, y0 + s * 0.16)
        line(x0 + s * 0.28, y0 + s * 0.84, x0 + s * 0.72, y0 + s * 0.84)
    elif name == "alarm":
        # 任务提醒：圆表盘 + 两根指针 + 顶上两颗铃。
        #
        # 表盘半径 0.39s（直径 12.5px，含 1.7px 描边 14.2px）—— 与 todo 方框
        # （14px）、star（16px）同档。**别按 bbox 判断大小**：老版虽然 bbox 也有
        # 16px，但墨迹几乎全在那颗 9.6px 的小圆上，圆外只剩两条细铃，一排看过去
        # 比邻居小一圈（用户就是这么报的）。填充率才是「看着一样大」的判据。
        #
        # 指针只走到 0.52r / 0.40r：圆环内壁在 0.61r 处（r 减半个描边），针再长
        # 就顶到环上 —— 1.7px 的圆头描边一叠，圆心到顶端连成一根柱，放大看就是
        # 「中间塞满的圆圈」。现在针尖离内壁 2.1px，与 todo 方框里的对勾同理。
        #
        # 铃画成外撇的斜线而不是小圆：闭合小圆在 1.7px 圆头描边下就是一个点。
        # 表盘中心下移 0.05s，把上方那 2.5px 让给铃，同时保证圆底不顶格。
        r = s * 0.39
        oy = cy + s * 0.05
        ids.append(cv.create_oval(cx - r, oy - r, cx + r, oy + r,
                                  outline=color, width=stroke))
        line(cx, oy, cx, oy - r * 0.52)                        # 分针朝上
        line(cx, oy, cx + r * 0.40, oy + r * 0.20)             # 时针朝右下
        for sgn in (-1, 1):
            line(cx + sgn * r * 0.58, oy - r * 0.90,
                 cx + sgn * r * 1.02, oy - r * 1.14)
    elif name == "export":
        # 导出：托盘（开口向上的 U）+ 一根扎进去的箭头。
        # 「托盘 + 向下箭头」= 把内容落到文件里，比「箱子 + 向外箭头」在 16px 上
        # 更容易分辨 —— 后者那个外偏的箭头会和箱体粘在一起。
        line(x0 + s * 0.13, y0 + s * 0.64, x0 + s * 0.13, y0 + s * 0.93,
             x0 + s * 0.87, y0 + s * 0.93, x0 + s * 0.87, y0 + s * 0.64)
        line(cx, y0 + s * 0.05, cx, y0 + s * 0.56)
        line(x0 + s * 0.29, y0 + s * 0.35, cx, y0 + s * 0.58,
             x0 + s * 0.71, y0 + s * 0.35)
    elif name == "link":
        # 超链接：两个斜向相扣的环。只画环、不画中间的连杆 —— 16px 下连杆会把
        # 两个环糊成一团，「两环相交」这个形状本身已经是公认的链环符号。
        r = s * 0.23
        for dx, dy in ((-0.15, 0.15), (0.15, -0.15)):
            ox, oy = cx + s * dx, cy + s * dy
            ids.append(cv.create_oval(ox - r, oy - r, ox + r, oy + r,
                                      outline=color, width=stroke))
    elif name == "lock":
        # 挂锁：上半圆的锁梁 + 锁体。锁梁两端要扎进锁体，否则看着像两个独立图元
        ids.append(cv.create_arc(x0 + s * 0.28, y0 + s * 0.08,
                                 x0 + s * 0.72, y0 + s * 0.56,
                                 start=0, extent=180, style=tk.ARC,
                                 outline=color, width=stroke))
        ids.append(cv.create_rectangle(x0 + s * 0.20, y0 + s * 0.44,
                                       x0 + s * 0.80, y0 + s * 0.90,
                                       outline=color, width=stroke))
    elif name == "unlock":
        # 开锁：锁梁整体右移，与锁体错开 —— 形状差得够明显，不用靠颜色区分
        ids.append(cv.create_arc(x0 + s * 0.44, y0 + s * 0.08,
                                 x0 + s * 0.88, y0 + s * 0.56,
                                 start=0, extent=180, style=tk.ARC,
                                 outline=color, width=stroke))
        ids.append(cv.create_rectangle(x0 + s * 0.16, y0 + s * 0.44,
                                       x0 + s * 0.76, y0 + s * 0.90,
                                       outline=color, width=stroke))
    elif name == "gutter":
        # 行号：左边竖排两个数字（1、2）+ 右边一条通高栏线。
        #
        # 按用户给的两张参考图取的折中：参考图一是「文档 + 折角 + 123 + 两条横线」，
        # 参考图二是「纯 123 竖排」。前者 16px 里画不下 —— 实测「外框 + 两数字 +
        # 两条横线」四个元素挤在一格里，框内净空只有 11px，数字和横线糊成一团黑点
        # （A 案）；去掉横线只剩「框 + 数字」（B 案），框里两个 4x5 的点阵数字也认
        # 不出来。所以取后者的数字排布，用一条**栏线**代替参考图一的「右边内容区」：
        # 数字列 + 边界线，就是编辑器左边那条行号栏。
        #
        # ⚠️ 栏线只能用**竖线**。用横线就变成 numbered（有序列表 = 两个数字 + 两条
        # 横线）的翻版，两个图标并排会直接混淆。
        # ⚠️ 数字只有两个：16px 里三个 5 行点阵 = 17px，超出格子，挤成「竖虫」
        # （numbered 早踩过这个坑）。两个数字 + 1 行缝 = 11px，正好。
        # ⚠️ 两个字模必须**同宽（3 列）且左右边缘对齐**。早先写成 4 列的
        # `..#. / .##. / ..#. / ..#. / .###`（1）和 `###. / ..#. / .#.. / #... / ###.`（2）：
        # 数字 1 整体偏右一列、数字 2 偏左，竖排下来就成了「1 靠右、2 靠左」，
        # 用户一眼看出没对齐。现在两枚都占第 0~2 列，上下左右都齐。
        _G = {"1": (".#.", "##.", ".#.", ".#.", "###"),
              "2": ("###", "..#", ".#.", "#..", "###")}
        p = max(1, int((s * 0.78) / 11.0))          # 点阵像素边长（整像素）
        top = cy - (11 * p) / 2.0                   # 两个数字整体在图标里居中
        dx = x0 + s * 0.16
        for i, ch in enumerate(("1", "2")):
            for r, row in enumerate(_G[ch]):
                for col, cell in enumerate(row):
                    if cell == "#":
                        bx = dx + col * p
                        by = top + (i * 6 + r) * p
                        ids.append(cv.create_rectangle(bx, by, bx + p - 1,
                                                      by + p - 1,
                                                      fill=color, outline=""))
        line(x0 + s * 0.66, y0 + s * 0.10, x0 + s * 0.66, y0 + s * 0.90)
    elif name == "check":
        # 菜单里的「已选中」对勾。**不用文字 ✓**：雅黑那个 ✓ 字形偏窄、起笔带一段
        # 细拖尾，11pt 下歪在标题后面，和菜单右缘怎么摆都对不齐；勾的字面对齐基准
        # 也和旁边的中文不一样，整行看着像贴了个错位的贴纸。矢量勾三段等长、
        # 线宽与菜单里其它图标同档（1.7px），还能随 DPI 一起放大。
        #
        # 几何：落点压到 cy+0.30s（勾底），起笔 0.14s、收笔 0.86s。长臂短臂比约
        # 1:1.4 是「一眼认出是勾」的下限；拐点再往下（0.38s 以下）短臂就缩成一点，
        # 12px 档会读成一条斜线。
        line(x0 + s * 0.14, cy + s * 0.04,
             x0 + s * 0.40, cy + s * 0.30,
             x0 + s * 0.86, cy - s * 0.26)
    return ids


class Tooltip(object):
    """极简悬浮提示：鼠标停留一小会儿后在控件下方浮出一行说明。

    自绘无边框 Toplevel 而不是用 tk 的原生 tooltip（Tk 没有），配色跟当前皮肤走。
    提示随控件一起消失：控件被销毁后再显示会拿到无效窗口 id，所以 _show 前先
    检查 winfo_exists，且监听控件 <Destroy> 主动收起。
    """

    DELAY = 420          # 悬停多久才浮出（毫秒）；太短会跟着鼠标划过到处闪

    def __init__(self, widget, text, pal, delay=None):
        self.widget = widget
        self.text = text
        self.enabled = True      # False = 这个控件此刻没有可点的东西，不弹提示
        self.pal = dict(pal)
        self.delay = self.DELAY if delay is None else delay
        self._job = None
        self._win = None
        widget.bind("<Enter>", self._on_enter, add="+")
        widget.bind("<Leave>", self._on_leave, add="+")
        widget.bind("<ButtonPress>", self._on_leave, add="+")
        widget.bind("<Destroy>", self._on_leave, add="+")

    def _on_enter(self, _e=None):
        self._cancel()
        if not getattr(self, "enabled", True):
            # 控件此刻「没有可点的东西」（例如叶子文件夹前面那格留白）：
            # 别浮出一条点不动的说明 —— 用户会照着提示去点，然后发现没反应
            return
        try:
            self._job = self.widget.after(self.delay, self._show)
        except Exception:
            self._job = None

    def _on_leave(self, _e=None):
        self._cancel()
        self.hide()

    def _cancel(self):
        if self._job is not None:
            try:
                self.widget.after_cancel(self._job)
            except Exception:
                pass
            self._job = None

    def _show(self):
        self._job = None
        if self._win is not None or not self.text:
            return
        try:
            if not self.widget.winfo_exists() or not self.widget.winfo_viewable():
                return
            x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + dpi.px(6)
        except Exception:
            return
        p = self.pal
        try:
            win = tk.Toplevel(self.widget)
            win.wm_overrideredirect(True)      # 无标题栏
            # 同 LinkEditor：气泡是浮层，底色要压过稿纸，别用稿纸色
            win.configure(bg=_popover_border(p))
            lbl = tk.Label(win, text=self.text, bg=_popover_bg(p),
                           fg=p.get("text", "#222222"), font=(FONT[0], 10),
                           bd=0, padx=dpi.px(8), pady=dpi.px(4), justify="left")
            lbl.pack(padx=dpi.px(1), pady=dpi.px(1))
            win.update_idletasks()
            w = win.winfo_reqwidth()
            h = win.winfo_reqheight()
            # 居中在控件下方，但别越出**控件所在那块屏**的边（多屏时 Tk 的
            # winfo_screenwidth 只报主屏，副屏上的气泡会被拽到 x=0）
            area = win_frame.work_area_at(x, y, fallback=win)
            x, y = win_frame.clamp_pos(area, x - w // 2, y, w, h,
                                       pad=dpi.px(4))
            win.wm_geometry("+%d+%d" % (x, y))
            # 主窗口若开了置顶（自绘标题栏拖动时会临时置顶），气泡是它的子 Toplevel，
            # 不跟着置顶就会被自家窗口盖住 —— 只在这一种情况下跟着置顶，平时不抢焦点。
            try:
                if self.widget.winfo_toplevel().attributes("-topmost"):
                    win.attributes("-topmost", True)
            except Exception:
                pass
            self._win = win
        except Exception:
            self.hide()

    def hide(self):
        if self._win is not None:
            try:
                self._win.destroy()
            except Exception:
                pass
            self._win = None

    def set_text(self, text):
        self.text = text


class LinkEditor(object):
    """悬停在超链接上时浮出的小窗：显示文字和地址都能就地改。

    为什么不是只读的 Tooltip：链接的「显示文字」和「地址」在正文里是分开存的
    （正文只留文字，地址存在笔记的 links 里），地址打错字是常态；不能改的话
    用户只能取消链接再重做一遍。显示文字同理——正文里那几个字改起来要选中、
    重输，还容易把链接范围删坏，不如在小窗里直接改。

    刻意不抢焦点：悬停就把焦点切到输入框的话，用户继续打字会打进弹窗里。
    要改就点一下输入框——那时才真正接管键盘。
    """

    HIDE_MS = 260
    # 焦点在两个输入框之间跳转时，前一个的 <FocusOut> 会先到、后一个的
    # <FocusIn> 后到。立刻判定「离开了小窗」的话，Tab 一下就把窗口收了，
    # 所以推迟一小会儿再看焦点到底落在哪儿。
    FOCUS_CHECK_MS = 150

    def __init__(self, master, pal, on_commit, on_enter=None):
        self.master = master
        self.pal = dict(pal)
        self.on_commit = on_commit
        # 指针进到小窗里时通知宿主撤掉它自己那一侧的收起计时：
        # 正文的 <Motion> 在指针离开链接文字时就排了隐藏任务，小窗自己的
        # _cancel_hide 管不到它 —— 不撤销的话小窗会在用户点进输入框前消失。
        self.on_enter = on_enter
        self.win = None
        self.entry = None          # 链接地址
        self.text_entry = None     # 显示文字
        self._tag = None
        self._origin = ""
        self._origin_text = ""
        self._hide_job = None
        self._focused = False

    # ---------- 显示 / 收起 ----------
    def _make(self):
        p = self.pal
        ed = p.get("editor", "#ffffff")
        # 灰壳 + 白输入区：浮层底色必须和稿纸拉开（见 _popover_bg），
        # 输入框却是稿纸本白 —— 两层对比既认得出这是浮层，也标出了能打字的地方
        shell = _popover_bg(p)
        border = _popover_border(p)
        soft = _mix_hex(p.get("soft", "#76767e"), p.get("text", "#222222"), 0.45)
        accent = p.get("accent", "#2f7cf6")
        win = tk.Toplevel(self.master)
        win.overrideredirect(True)
        try:
            win.attributes("-topmost", True)
        except Exception:
            pass
        win.configure(bg=border)
        body = tk.Frame(win, bg=shell)
        body.pack(fill=tk.BOTH, expand=True, padx=dpi.px(1), pady=dpi.px(1))

        def field(label, first=False):
            tk.Label(body, text=label, bg=shell, fg=soft,
                     font=(FONT[0], 9)).pack(anchor="w", padx=dpi.px(9),
                                             pady=(dpi.px(6), 0) if first else (dpi.px(4), 0))
            e = tk.Entry(body, bg=ed, fg=p.get("text", "#222222"),
                         insertbackground=accent, relief=tk.FLAT,
                         font=(FONT[0], 10), width=42, highlightthickness=dpi.px(1),
                         highlightbackground=_mix_hex(ed, border, 0.7),
                         highlightcolor=accent)
            e.pack(fill=tk.X, padx=dpi.px(9), pady=(dpi.px(3), 0), ipady=dpi.px(3))
            e.bind("<Return>", self._commit)
            e.bind("<Escape>", lambda _e: self.hide())
            e.bind("<FocusIn>", self._on_focus_in)
            e.bind("<FocusOut>", self._on_focus_out)
            return e

        self.text_entry = field("显示文字", first=True)
        self.entry = field("链接地址")
        tk.Label(body, text="回车保存 · 清空地址取消链接 · Esc 关闭",
                 bg=shell, fg=soft, font=(FONT[0], 9)).pack(
                     anchor="w", padx=dpi.px(9), pady=(dpi.px(4), dpi.px(6)))
        for w in (win, body):
            w.bind("<Enter>", self._cancel_hide, add="+")
            w.bind("<Leave>", self._on_leave, add="+")
            if self.on_enter is not None:
                w.bind("<Enter>", lambda _e: self.on_enter(), add="+")
        win.bind("<Destroy>", lambda _e: self._reset_refs(), add="+")
        self.win = win
        return win

    def show(self, x, y, url, tag=None, text=None):
        self._tag = tag
        self._origin = url or ""
        self._origin_text = text if text is not None else self._origin
        self._cancel_hide()
        win = self.win
        if win is None or not win.winfo_exists():
            win = self._make()
            self._focused = False
        for ent, val in ((self.text_entry, self._origin_text), (self.entry, self._origin)):
            ent.delete(0, tk.END)
            ent.insert(0, val)
        win.update_idletasks()
        w = max(win.winfo_reqwidth(), dpi.px(300))
        h = win.winfo_reqheight()
        # 边界取**指针所在那块屏**的工作区：多屏时 winfo_screenwidth 只报主屏，
        # 副屏上（坐标为负）弹这一层会被夹到 x=0，也就是跑到主屏最左边去
        area = win_frame.work_area_at(x, y, fallback=win)
        py = int(y)
        if py + h > area[1] + area[3] - dpi.px(8):
            py = area[1] + area[3] - h - dpi.px(8)   # 贴底了翻到指针上方
        px, py = win_frame.clamp_pos(area, x, py, w, h, pad=dpi.px(8))
        win.geometry("%dx%d+%d+%d" % (w, h, px, py))
        win.deiconify()
        try:
            tk.Misc.lift(win)
        except Exception:
            pass

    def hide(self):
        self._cancel_hide()
        self._reset_refs()
        win = self.win
        self.win = None
        self.entry = None
        self.text_entry = None
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass

    def _reset_refs(self):
        self._tag = None
        self._focused = False

    # ---------- 交互 ----------
    def _on_focus_in(self, _e=None):
        self._focused = True
        self._cancel_hide()

    def _on_focus_out(self, _e=None):
        self._focused = False
        self._cancel_hide()
        try:
            self._hide_job = self.master.after(self.FOCUS_CHECK_MS,
                                               self._focus_out_check)
        except Exception:
            self._hide_job = None

    def _focus_out_check(self):
        """焦点真的离开小窗了才提交/收起（在两个输入框之间跳转不算）。"""
        self._hide_job = None
        if self._focused or self.win is None:
            return
        if self._changed():
            self._commit()
        else:
            self.hide()

    def _changed(self):
        try:
            if self.entry is None or self.text_entry is None:
                return False
            if self.entry.get().strip() != self._origin.strip():
                return True
            return self.text_entry.get().strip() != self._origin_text.strip()
        except Exception:
            return False

    def _commit(self, _e=None):
        tag = self._tag
        try:
            val = self.entry.get() if self.entry is not None else ""
        except Exception:
            val = ""
        try:
            txt = self.text_entry.get() if self.text_entry is not None else ""
        except Exception:
            txt = ""
        self.hide()
        if self.on_commit:
            self.on_commit(tag, txt, val)
        return "break"

    def _cancel_hide(self, _e=None):
        if self._hide_job is not None:
            try:
                self.master.after_cancel(self._hide_job)
            except Exception:
                pass
            self._hide_job = None

    def _on_leave(self, _e=None):
        if self._focused:
            return                      # 正在改地址，别把窗口从手里拿走
        self._cancel_hide()
        try:
            self._hide_job = self.master.after(self.HIDE_MS, self.hide)
        except Exception:
            self._hide_job = None

    def apply_theme(self, pal):
        self.pal = dict(pal)
        if self.win is not None:
            self.hide()                 # 最简单的换肤：下一帧按新配色重建


# ---------- 项目链接（关于弹窗用） ----------
# 图标不再内嵌 base64：`icon/github.png` 与 `icon/issue.png` 都是 200px 的线条图，
# 运行时按 alpha 染色成主题文字色（`_tinted_png_photo`），跟皮肤走、也不用再维护
# 一份 3KB 的字符串常量。这两个地址是「关于」里两行链接的唯一来源。
GITHUB_URL = "https://github.com/simonking-ws/local-notes"
ISSUES_URL = GITHUB_URL + "/issues"


class RoundButton(tk.Canvas):
    """自绘圆角按钮（补齐 Tk Button 只有直角、且按下会闪系统灰的问题）。

    Tk 的 Frame/Label/Button 天生是矩形，无法圆角；本类用 Canvas 画圆角背景 +
    create_text 画文字，自己接管 normal / hover / pressed 三态配色，
    因此按下时不会被系统默认灰覆盖。
    """

    def __init__(self, master, pal, text, command=None, font=None,
                 padx=dpi.px(11), pady=dpi.px(6), radius=BTN_RADIUS, primary=False,
                 base=None, fg=None, cursor="hand2", stretch=False, outline=None,
                 icon=None, icon_size=ICON_SIZE, tail=None, icon_img=None):
        # Canvas 自身的底色必须等于按钮所在区域的底色：圆角矩形的四个尖角之外
        # 是画布本底，一旦两者不同（画布默认是系统灰），每个按钮外面就会多出一圈
        # 刺眼的亮方块——工具栏底色越深，这圈方块越显眼。
        _base = base if base is not None else pal["editor"]
        tk.Canvas.__init__(self, master, bd=0, highlightthickness=0, cursor=cursor,
                           bg=_base)
        self._pal = dict(pal)
        self._text = text
        self._command = command
        self._font = font or (FONT[0], 12)
        self._padx = padx
        self._pady = pady
        self._radius = radius
        self._primary = primary
        self._base = _base
        self._fg = fg
        self._outline = outline
        self._state = "normal"
        self._enabled = True
        self._stretch = stretch
        self._icon = icon
        self._icon_size = icon_size
        # icon_img：主图标的**位图**版本（`icon/px/<size>/<name>`）。给了就贴图，
        # 没给（或那一档成品缺失）就退回 `_draw_icon` 的矢量画法。
        # 为什么要有这条路：Tk 8.6 的 canvas 线条没有抗锯齿，带弧线的图形在 16px
        # 上只能一个像素一个像素地爬台阶（花括号就是这么被报「不圆滑」的）。
        self._icon_img = icon_img
        # tail：主图标右侧再挂一枚小图标（如代码块的「[ ] + ▾」下拉箭头）。
        # 与「文字 + chevron」同构，但主元素是矢量图标 —— 与其它纯图标按钮
        # 共用同一套线条语言和 16px 方格，垂直对齐由同一中心保证。
        self._tail = tail
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

    def set_text(self, text):
        """改按钮文字并重算尺寸（字号下拉这类「按钮本身就是当前值」的场景用）。"""
        text = str(text)
        if text == self._text:
            return
        self._text = text
        self.redraw()

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
        # 行高按「常规字重」算：bold 的 descent 比常规多 1px（雅黑 12pt：5 vs 4），
        # 直接用本字体的 linespace 会让「B」按钮比同排的 I/U/S 高 1px。
        lh = _f.Font(family=fam, size=size).metrics("linespace")
        if self._icon and not self._text:
            # 纯图标按钮：宽度按图标方格算；高度取「图标」与「一行文字」的较大者，
            # 保证与同排的文字按钮等高、底边齐平
            w = int(self._icon_size + self._padx * 2)
            if self._tail:
                w += int(ICON_TEXT_GAP + self._icon_size * ICON_TAIL_RATIO)
            h = int(max(self._icon_size, lh) + self._pady * 2)
        elif self._icon:
            # 文字 + 图标（如「H ▾」「{} ▾」）：文字在左、小箭头在右，当作一个下拉按钮
            iw = self._icon_size * ICON_TAIL_RATIO
            w = int(f.measure(self._text) + ICON_TEXT_GAP + iw + self._padx * 2)
            h = int(max(self._icon_size, lh) + self._pady * 2)
        else:
            w = int(f.measure(self._text)) + self._padx * 2
            h = int(lh) + self._pady * 2
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
                            fill=self._cur_fill(), outline=self._cur_outline(),
                            width=dpi.fx(1))
        if self._icon and not self._text:
            if self._tail:
                # 主图标在左、小箭头在右：两者同中心线（h/2），与纯图标按钮一致
                iw = self._icon_size * ICON_TAIL_RATIO
                self._paint_icon(self._padx + self._icon_size / 2.0,
                                 h / 2.0, self._icon_size)
                _draw_icon(self, self._tail, w - self._padx - iw / 2.0,
                           h / 2.0, iw, self._cur_fg())
            else:
                self._paint_icon(w / 2.0, h / 2.0, self._icon_size)
        elif self._icon:
            iw = self._icon_size * ICON_TAIL_RATIO
            tw = f.measure(self._text)
            cx_text = self._padx + tw / 2.0
            cx_icon = w - self._padx - iw / 2.0
            self.create_text(cx_text, self._text_y(h, f), text=self._text, font=f,
                             fill=self._cur_fg())
            self._paint_icon(cx_icon, h / 2.0, iw)
        else:
            self.create_text(w / 2.0, self._text_y(h, f), text=self._text, font=f,
                             fill=self._cur_fg())

    def _paint_icon(self, cx, cy, size):
        """画主图标：有预渲染位图就贴图（带抗锯齿），否则退回矢量线条。

        贴图坐标取整：Tk 放图时按整像素落位，留个 .5 会让图标相对同排矢量
        图标偏半像素 —— 一排看过去就是「这枚好像歪了一点」。
        """
        if self._icon_img:
            img = _icon_photo(self, self._icon_img, size, self._cur_fg())
            if img is not None:
                # 引用必须留着：PhotoImage 被回收后画布上的图会变白
                self._photo = img
                # 自己算左上角再按 nw 放：按钮高度常是奇数，图标中心落在 .5 上。
                # 用「四舍五入」（不是 Python 的银行家舍入：round(6.5)=6，会比
                # 同排复选框高 1px），路线和 Tk 画矢量时一致。
                self.create_image(int(cx - size / 2.0 + 0.5),
                                  int(cy - size / 2.0 + 0.5),
                                  image=img, anchor="nw")
                return
        _draw_icon(self, self._icon, cx, cy, size, self._cur_fg())

    @staticmethod
    def _text_y(h, f):
        """文字绘制点的 y：让大写字母的**墨迹**垂直居中，而不是字体行框居中。

        `create_text(anchor="center")` 居中的是行框（ascent + descent），行框下沿
        比字母底部多出一截 descent。雅黑 12pt 的 descent 在 bold 上是 5px、常规是
        4px，行框因此差 1px —— 同一排的「B」比「I / U / S」高 1px（用户报的
        「加粗的符号偏高」），也跟严格按方格中心画的 16px 矢量图标错开 1px。

        这里反过来算：先定基线 = 几何中心 + cap 半高（大写字母墨迹刚好居中），再
        按 (ascent-descent)/2 折回「行框中心」该在的坐标 —— 各字重同基线，与图标齐。
        """
        asc = f.metrics("ascent")
        desc = f.metrics("descent")
        return h / 2.0 + asc * TEXT_CAP_HALF_RATIO - (asc - desc) / 2.0

    # ---------- 三态配色 ----------
    def _cur_fill(self):
        """常态/悬停/按下三档底色。

        常态 = 按钮所在区域的底色（工具栏按钮因此与底条融成一体，不再是浮在上面
        的一块块白片）；悬停/按下 = **把这块底色自己压暗一档**（`_shade` 只动明度）。

        为什么不用「混中性墨」：往纯黑混会把饱和度一起稀释，绿皮肤的工具带一悬停
        就变成一块蒙灰的暗斑 —— 颜色「不对」而不是「暗了」。从底色推则保住色相，
        工具栏、侧栏、顶栏、对话框各自压暗各自的那块底，换主题也不用重调。
        """
        p = self._pal
        if not self._enabled:
            # 禁用：不跟着鼠标变色（否则一只死按钮还在回应悬停，比不回应更怪）。
            # 主按钮把实心绿压淡，普通按钮直接融回底色。
            return _mix_hex(p["accent"], p["selfg"], 0.45) if self._primary else self._base
        if self._state == "normal":
            return p["accent"] if self._primary else self._base
        if self._primary:
            # 主按钮：向深绿递进
            t = 0.20 if self._state == "hover" else BTN_PRESS_MIX + 0.14
            return _mix_hex(p["accent"], p["selfg"], t)
        if self._state == "hover":
            return _hover_bg(self._base)
        return _press_bg(self._base)

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
            # 主按钮文字：accent 底色上按对比度取白 / 墨（金色 accent 上白字会糊）
            return on_accent(p["accent"]) if self._enabled else _mix_hex(p["accent"], p["selfg"], 0.45)
        if not self._enabled:
            # 禁用态：同一个墨色只混一点点，图标淡到「看得出来但明显不可用」。
            # 不能再淡——工具带本身很浅，再淡就彻底看不见，反而像没画出来。
            base = self._base
            return _mix_hex(base, _ink_target(base), 0.30)
        if self._state == "normal":
            # 显式指定的字色只用于常态；工具栏按钮一律不传，走下面那套推导，
            # 换肤时才能和其它按钮一起按新板重算。
            # 没指定时取「次级文字向主文字靠一大半」的墨色：比纯 soft 更实，
            # 工具条上的 B/I/U 这类小字才压得住底色（掘金的工具图标也接近主文字色）。
            if self._fg:
                return self._fg
            return _mix_hex(p.get("soft", "#76767e"), p.get("text", "#1f1f24"), 0.62)
        # 悬停/按下时底色被压暗，字色往上提一档才不糊。
        return p.get("text") or p["selfg"]

    def redraw(self):
        self.build()

    def apply_theme(self, pal, base=None, old_pal=None):
        """换肤。传 base 时同步更新「按钮所在区域的底色」——否则换肤后按钮的正常态
        仍停在旧底色上，会显出一块洗不掉的色斑（工具栏按钮尤其明显）。

        `old_pal` 给「建窗时显式传了 fg / outline」的按钮用：那两个值是写死在
        实例上的旧板色，不是 tk option，通用重刷够不着；不翻译就会在换肤后留下
        一圈旧描边 / 旧字色。主窗工具栏按钮没传这两项，走默认路径即可。
        """
        self._pal = dict(pal)
        if base is not None:
            self._base = base
        if old_pal is not None:
            self._fg = remap_palette_color(self._fg, old_pal, pal)
            self._outline = remap_palette_color(self._outline, old_pal, pal)
        try:
            # 圆角外的画布本底也要跟上，否则换肤后每个按钮留一圈旧色的方框
            self.configure(bg=self._base)
        except Exception:
            pass
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
        if not self._enabled and st != "normal":
            return                  # 禁用态不亮，也不许按下去
        self._state = st
        self.redraw()

    def _on_release(self, e):
        if not self._enabled:
            return
        self._state = "hover"
        self.redraw()
        if self._command:
            self._command()

    def set_enabled(self, on):
        """启/禁用。只读笔记打开时，工具栏上会改内容的按钮统一禁用。

        淡色比「点下去没反应」更能说明问题：一排灰掉的按钮，第一反应是
        「这篇不能编辑」，而不是「程序坏了」。
        """
        on = bool(on)
        if on == self._enabled:
            return
        self._enabled = on
        self._state = "normal"
        try:
            self.configure(cursor="hand2" if on else "arrow")
        except Exception:
            pass
        self.redraw()

    def set_command(self, command):
        """事后换掉点击回调。

        构造参数里的 command 在 __init__ 时就绑死了，但有些回调需要**把这个
        按钮自己传进去**（检查更新时要临时置灰、把文字换成「检查中…」），
        而构造那一刻变量还没赋值 —— 只能先建、再挂。
        Canvas 没有 ``-command`` 选项，所以不能走 configure。
        """
        self._command = command


class FontSizeSlider(tk.Canvas):
    """字号滑杆：强调色填充轨道 + 圆形滑钮，拖动/点击/滚轮/方向键调整。

    ttk.Scale 是系统方眼样式，和自绘圆角语言不搭，这里整条自己画：
    常态滑钮白底强调色描边，悬停略放大，拖动时实心并浮出数值气泡。
    取值 1pt 连续步进，不再有预设字号。
    """

    HEIGHT = dpi.px(70)
    PAD = dpi.px(12)              # 轨道两端留白，正好容纳滑钮半径
    TRACK_Y = dpi.px(38)

    def __init__(self, master, pal, variable, minimum=8, maximum=25,
                 command=None, command_end=None, width=dpi.px(340)):
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

        self.create_polygon(_round_rect_points(x0, cy - dpi.px(3), x1, cy + dpi.px(3), dpi.px(3), 6),
                            fill=p["line"], outline="")
        if tx > x0 + 1:
            self.create_polygon(_round_rect_points(x0, cy - dpi.px(3), tx, cy + dpi.px(3), dpi.px(3), 6),
                                fill=p["accent"], outline="")

        self.create_text(x1, dpi.px(10), anchor="ne", text="%d pt" % v,
                         font=self._font_val, fill=p["accent"])
        self.create_text(x0, self.HEIGHT - dpi.px(10), anchor="w", text=str(self._min),
                         font=self._font_small, fill=p["soft"])
        self.create_text(x1, self.HEIGHT - dpi.px(10), anchor="e", text=str(self._max),
                         font=self._font_small, fill=p["soft"])

        if self._bubble:
            text = "%d pt" % v
            bw = self._font_bub.measure(text) + dpi.px(16)
            bh = dpi.px(20)
            bx = min(max(tx, self.PAD + bw / 2.0), w - self.PAD - bw / 2.0)
            by = cy - dpi.px(12)
            self.create_polygon(
                _round_rect_points(bx - bw / 2.0, by - bh, bx + bw / 2.0, by,
                                   bh / 2.0, 6),
                fill=p["accent"], outline="")
            self.create_text(bx, by - bh / 2.0, text=text,
                             font=self._font_bub, fill=on_accent(p["accent"]))

        r = dpi.px(9) if self._state == "normal" else dpi.px(10)
        if self._state == "drag":
            fill, outline = p["accent"], _mix_hex(p["accent"], p["selfg"], 0.2)
        else:
            fill, outline = p["editor"], p["accent"]
        if self.focus_get() is self and self._state != "drag":
            self.create_oval(tx - r - dpi.px(3), cy - r - dpi.px(3),
                             tx + r + dpi.px(3), cy + r + dpi.px(3),
                             outline=_mix_hex(p["editor"], p["accent"], 0.25),
                             width=dpi.px(1))
        self.create_oval(tx - r, cy - r, tx + r, cy + r,
                         fill=fill, outline=outline, width=dpi.px(2))
        if self._state == "drag":
            self.create_oval(tx - dpi.px(3), cy - dpi.px(3), tx + dpi.px(3), cy + dpi.px(3),
                             fill=on_accent(fill), outline="")

    def apply_theme(self, pal):
        """换肤：轨道 / 滑钮 / pt 数值 / 气泡全是 Canvas 图元，改完 _pal 要整条重画。

        画布本底也在构造时写死了，得一起换，否则圆角之外留一块旧色方块。
        """
        self._pal = dict(pal)
        try:
            self.configure(bg=pal["editor"])
        except tk.TclError:
            pass
        self.build()


def _scroll_chip_geom(size):
    """按小片尺寸推导双箭尖参数，大小片（工具栏 24 / 折叠条 16）视觉一致。"""
    k = size / 24.0
    return 3.0 * k, 4.6 * k, 3.2 * k, (2.0 if size >= 20 else 1.8)


def _draw_scroll_chip(cv, pal, base, cx, cy, direction, state, size=dpi.px(24)):
    """圆角小片 + 双箭尖：工具栏滑动箭头、列表折叠按钮共用同一套画法与三态配色。

    常态是比底色略深的浅灰片，悬停/按下同样从**这块底**推（`_hover_bg` / `_press_bg`），
    不再直接吃皮肤的 hover 色 —— 后者是为列表行准备的，铺在工具带上会偏色。
    direction 约定与自绘控件一致：-1 向左、1 向右。
    """
    if state == "press":
        fill = _press_bg(base)
        outline = _mix_hex(pal["line"], pal["accent"], 0.55)
        fg = pal["accent"]
    elif state == "hover":
        fill = _hover_bg(base)
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
        fill=fill, outline=outline, width=dpi.fx(1))
    a, b, gap, lw = _scroll_chip_geom(size)
    for off in (-gap, gap):
        ax = cx + off
        cv.create_line(ax - a * direction, cy - b, ax + a * direction, cy,
                       ax - a * direction, cy + b, fill=fg, width=dpi.fx(lw),
                       capstyle=tk.ROUND, joinstyle=tk.ROUND)


class ArrowButton(tk.Canvas):
    """工具栏两端的滑动箭头：圆角小片 + 双箭尖，垂直居中，自带三态。

    和 RoundButton 一样用 Canvas 自绘（Tk 的 Button 是直角，且按下会闪系统灰）。
    画布被 pack(fill=Y) 拉到整条工具栏高，所以按实际尺寸居中绘制，
    窗口变小、箭头出现时也能稳稳落在垂直中线上。
    """

    def __init__(self, master, pal, direction, command=None, size=dpi.px(26),
                 base=None, chip=dpi.px(24)):
        tk.Canvas.__init__(self, master, width=size, height=size, bd=0,
                           highlightthickness=0, cursor="hand2")
        self._pal = dict(pal)
        self._dir = -1 if direction == "left" else 1
        self._command = command
        self._size = size
        self._chip = chip        # 箭尖小片的边长；矮条（标签条 20）用不了 24
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
                          self._dir, self._state, size=self._chip)

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


class TabStrip(tk.Frame):
    """笔记标签条：横向可滚的画布 + 两端箭头 + 右侧「新建」按钮。

    与工具栏共用一套滚动办法（画布套一个 inner Frame，两端 ArrowButton 按需浮出），
    因为标签数不可控：开十几篇之后必须能横向挪，不能把标签压成一指宽。

    为什么不是 ttk.Notebook：正文只有一个 Text（换标签 = 存住当前这篇、再装下一篇），
    Notebook 却要求每页一个子窗口 —— 真套进来就得把编辑器复制 N 份，撤销栈、
    内嵌代码块窗口、滚动位置全都要自己搬一遍。这里只要一条「谁被打开着」的条子。
    标签本身是 Frame + Label（不是画布自绘）：底色要跟着皮肤走、要能整片重配色，
    用 Frame 比每次重画一堆图元省事，观感也和列表行是一套。
    """

    H = dpi.px(25)          # 条高：只有一行短标题，压矮一点把正文的地还回来
    TITLE_DPT = -2      # 标题字号相对正文（FONT[1]=13）的偏移 → 11pt
    CLOSE_DPT = -2      # × 与「+」跟标题同号：条只有 25 高，大一号就顶出去了
    TAB_MIN = dpi.px(64)
    TAB_MAX = dpi.px(176)
    TAB_PAD = dpi.px(7)         # 标题左右留白
    CLOSE_W = dpi.px(16)        # × 占的宽度（含右侧留白）
    ICON_W = dpi.px(14)         # 加密笔记的小挂锁
    SEP_W = dpi.px(1)           # 相邻标签之间的竖分隔线
    ARROW = dpi.px(22)
    NOTE_ICON_H = dpi.px(12)    # 每枚标签左侧那枚「笔记」标记（icon/note.png）的高度
    NOTE_PAD_L = dpi.px(6)      # 标记离标签左边缘的留白
    NOTE_GAP = dpi.px(3)        # 标记与标题之间的间距
    DRAG_TOL = dpi.px(4)        # 拖拽判定阈值（px）：位移小于它就是「点击」
    DRAG_LINE_W = dpi.px(2)     # 拖拽时插入位那条竖线的宽度
    DRAG_EDGE = dpi.px(26)      # 指针离画布左右边缘多近开始自动横向滚

    def __init__(self, master, pal, on_select=None, on_close=None, on_new=None,
                 on_menu=None, on_reorder=None, base=None, height=None):
        tk.Frame.__init__(self, master, height=height or self.H, bd=0,
                          highlightthickness=0)
        self.pack_propagate(False)
        self.pal = dict(pal)
        self.base = base if base is not None else pal.get("editor", "#ffffff")
        self.on_select = on_select
        self.on_close = on_close
        self.on_new = on_new
        self.on_menu = on_menu
        self.on_reorder = on_reorder
        self.h = height or self.H
        self.items = []
        self.active = None
        self.hover = None
        self.tabs = {}          # id -> {"frame","label","close","icon"}
        self._x = 0             # 画布横向偏移（像素）
        # 拖拽重排的状态：_press 记下按下的那一枚（含控件对象，用来认出它被重建过），
        # _drag 只在真正拖起来之后才有值。
        self._press = None
        self._drag = None
        try:
            self._font = tkfont.Font(family=FONT[0],
                                     size=FONT[1] + self.TITLE_DPT)
        except Exception:
            self._font = None

        # 「新建」先 pack（side=RIGHT）才有固定宽度：反过来让 expand 的画布先吃满，
        # 它就被挤成 0 宽了。两端箭头后插在画布之前，于是落在画布左右。
        # 注意 bd/pady：Tk 的 Label 默认 bd=1、pady=1，一圈白边就吃掉 4px 高。
        # 条只有 25 高，不削这一圈，字连同留白就会顶出去。
        self.new_btn = tk.Label(self, text="+", width=2, cursor="hand2",
                                bg=self.base, fg=self.pal.get("soft", "#76767e"),
                                bd=0, pady=0,
                                font=(FONT[0], FONT[1] + self.CLOSE_DPT))
        self.new_btn.pack(side=tk.RIGHT, fill=tk.Y)
        self.new_btn.bind("<Button-1>", lambda e: self.on_new and self.on_new())
        self.new_btn.bind("<Enter>", lambda e: self.new_btn.configure(
            bg=_hover_bg(self.base), fg=self.pal.get("text", "#1f1f24")))
        self.new_btn.bind("<Leave>", lambda e: self.new_btn.configure(
            bg=self.base, fg=self.pal.get("soft", "#76767e")))

        # 每枚标签左侧都要有一枚「笔记」标记（icon/note.png 的斜铅笔），
        # 用来一眼认出这是笔记而不是别的什么。所有标签共用同一张位图 —— 尺寸颜色
        # 都一样，没必要每枚造一张（PhotoImage 还必须留引用，否则被 GC 后图会白掉）。
        self._note_photo = None
        self._build_note_photo()

        self.canvas = tk.Canvas(self, height=self.h, bd=0, highlightthickness=0,
                                bg=self.base)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.inner = tk.Frame(self.canvas, bg=self.base)
        self.win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.arrow_l = ArrowButton(self, pal, "left", command=lambda: self._scroll(-1),
                                   size=self.ARROW, base=self.base,
                                   chip=self.ARROW)
        self.arrow_r = ArrowButton(self, pal, "right", command=lambda: self._scroll(1),
                                   size=self.ARROW, base=self.base,
                                   chip=self.ARROW)
        self.arrow_l.bind_events()
        self.arrow_r.bind_events()
        self.inner.bind("<Configure>", lambda e: self._sync_scroll())
        self.canvas.bind("<Configure>", lambda e: self._sync_scroll())
        for w in (self.canvas, self.inner):
            w.bind("<MouseWheel>", self._wheel)
        # 右键：标签**本体**上的菜单在 _make_tab 里绑；条底那一大片空白区（最后
        # 一枚标签右边）、两端箭头、「+」以前右键毫无反应 —— 而「关闭所有标签」
        # 这种不针对某一枚的动作，恰恰是点空白时想找的。统一走 on_menu，tid=None
        # 表示「没有锚点」。
        # ⚠️ 这里**不能**把 inner 也绑上：inner 是标签的父容器，标签的 bindtags 里
        # 带着 inner（见 _bubble），绑了会在同一击里先弹标签菜单、再弹空白菜单。
        for w in (self, self.canvas, self.new_btn, self.arrow_l, self.arrow_r):
            w.bind("<Button-3>", self._blank_menu)

    # ---------- 外部接口 ----------

    def set_tabs(self, items, active=None):
        """items = [{"id","title","locked"}]；active 为当前标签 id（可为 None）。"""
        items = list(items or [])
        if self._can_diff(items):
            # 绝大多数调用（切标签）都是「还是这几枚，只是活动态换了 / 标题改了」。
            # 这时绝不能整条重建：重建会把「刚按下的那一枚」在主键还按着的时候就
            # destroy 掉，Tk 的隐式按捕获随之失效 —— 手指再滑一下，事件就落到了
            # 指针下面的正文上，正文的 <B1-Motion> 会按拖选锚点拉出一片选区
            #（看着像整篇被选中）。差量刷还顺带省掉每次切标签重建整条的开销和那一闪。
            self.items = items
            for it in items:
                self.set_title(it.get("id"), it.get("title") or "新建笔记")
            if active != self.active:
                self._apply_active(active)
            self.ensure_visible(self.active)
            return
        if self._can_reorder(items):
            self._reorder(items, active)
            return
        self.items = items
        self.active = active
        self._rebuild()

    def _can_diff(self, items):
        """新旧 items 能不能「就地刷」：id 顺序与锁状态一致才算。

        锁状态一变（切走一篇加密笔记会立刻重新封上）标签左侧的图标要从铅笔换成
        挂锁，控件类型都不同 —— 那种必须重建。标题不同则没关系，set_title 会重排。
        """
        if len(items) != len(self.items):
            return False
        for a, b in zip(items, self.items):
            if a.get("id") != b.get("id"):
                return False
            if bool(a.get("locked")) != bool(b.get("locked")):
                return False
        return True

    def _can_reorder(self, items):
        """同一批标签、锁状态也没变，只是顺序不同 → 只重排，别重建。

        重排必须避开 destroy：拖拽时按下的正是其中一枚，把它销毁等于丢掉 Tk 的
        隐式按捕获（后续 motion 会落到指针下面的正文上，见 set_tabs 注释）。
        pack 顺序本来就是屏幕顺序，按新次序重新 pack 一遍即可，控件对象一个不动。
        """
        if len(items) != len(self.items):
            return False
        old = dict((it.get("id"), bool(it.get("locked"))) for it in self.items)
        if len(old) != len(items):
            return False
        for it in items:
            tid = it.get("id")
            if tid not in old or bool(it.get("locked")) != old[tid]:
                return False
        return True

    def _reorder(self, items, active=None):
        """按新顺序重排已有标签（不销毁、不重建）。"""
        order = [it.get("id") for it in items]
        if any(tid not in self.tabs for tid in order):
            # 理论到不了：_can_reorder 已保证同一批。兜底走完整重建。
            self.items = items
            self.active = active
            self._rebuild()
            return
        self.items = items
        for it in items:
            self.set_title(it.get("id"), it.get("title") or "新建笔记")
        frames = [self.tabs[tid]["frame"] for tid in order]
        # 先全摘再按新次序装回去：pack 列表的顺序就是屏幕顺序。
        # 中间这一瞬不会闪 —— Tk 到 idle 才真正重画。
        for f in frames:
            f.pack_forget()
        for f in frames:
            f.pack(side=tk.LEFT, fill=tk.Y)
        last = len(order) - 1
        for i, tid in enumerate(order):
            rec = self.tabs[tid]
            self._set_sep(rec, i != last)       # 「最后一枚不画线」跟着换人
            self._fit_tab(rec, tid)             # 宽度预算里的 sep 那 1px 也跟着换
        if active != self.active:
            self._apply_active(active)
        else:
            self._repaint()
        try:
            self.inner.update_idletasks()
        except Exception:
            pass
        self._sync_scroll()
        self.ensure_visible(self.active)

    def _apply_active(self, tid):
        """只换活动态：摘/挂顶部强调线 + 重配色，**不销毁任何控件**。

        重建的代价不只是性能：标签切换绑在 <Button-1> 上，整条重建会在按下处理里
        销毁按下者，Tk 的按捕获就此丢掉 —— 后续 motion 会落到正文（见 set_tabs 注释）。
        """
        old = self.active
        self.active = tid
        self._drop_accent(self.tabs.get(old))
        self._show_accent(self.tabs.get(tid))
        self._repaint()

    def _show_accent(self, rec):
        """给某一枚挂上顶部那条 2px 强调线（pack 顺序上要排最前，才落在顶边）。"""
        if rec is None or rec.get("top") is not None:
            return
        f = rec["frame"]
        top = tk.Frame(f, height=dpi.px(2), bg=self.pal.get("accent", "#2f7cf6"))
        try:
            top.pack(side=tk.TOP, fill=tk.X, before=f.winfo_children()[0])
        except Exception:
            try:
                top.pack(side=tk.TOP, fill=tk.X)
            except Exception:
                return
        rec["top"] = top

    def _drop_accent(self, rec):
        top = rec.get("top") if rec else None
        if top is None:
            return
        try:
            top.destroy()
        except Exception:
            pass
        rec["top"] = None

    def ensure_visible(self, tid):
        """把某个标签滚进视野（键盘切标签时它可能在画布外）。"""
        w = self.tabs.get(tid)
        if not w:
            return
        self.inner.update_idletasks()
        try:
            x = w["frame"].winfo_x()
            tw = w["frame"].winfo_width()
            view = max(1, self.canvas.winfo_width())
            total = max(1, self.inner.winfo_reqwidth())
        except Exception:
            return
        cur = self._x
        if x < cur:
            cur = max(0, x - 8)
        elif x + tw > cur + view:
            cur = min(max(0, total - view), x + tw - view + 8)
        if cur != self._x:
            self._x = cur
            self.canvas.xview_moveto(cur / float(total))
            self._sync_scroll()

    def apply_theme(self, pal, base=None):
        self.pal = dict(pal)
        if base is not None:
            self.base = base
        self.configure(bg=self.base)
        self.canvas.configure(bg=self.base)
        self.inner.configure(bg=self.base)
        self.new_btn.configure(bg=self.base, fg=self.pal.get("soft", "#76767e"))
        self.arrow_l.apply_theme(pal, base=self.base)
        self.arrow_r.apply_theme(pal, base=self.base)
        # 标记是染过色的位图，换肤得重新染一遍（换色 → 重建整条标签）
        self._build_note_photo()
        # 底色变了 → 每片标签的三档底色都要重算，直接重建最省事
        self._rebuild()

    def _build_note_photo(self):
        """生成所有标签共用的那枚「笔记」标记位图。

        颜色是烤进位图的，换肤必须重新生成；生成失败（文件不在）时返回 None，
        标签就只画标题，不画标记。
        """
        img = _note_icon_photo(self, self.NOTE_ICON_H,
                               self.pal.get("soft", "#76767e"))
        if img is not None:
            self._note_photo = img        # 必须留着：PhotoImage 被回收后图会白掉
        return img

    # ---------- 构建 / 重绘 ----------

    def _rebuild(self):
        # 拖拽途中被重建（换肤、切到加密笔记…）→ 先收尾：那条竖线挂在 inner 上，
        # 会被一起销毁；不收尾的话 _drag 里留着的是已死的引用。
        self._press = None
        self._drag_end()
        for w in list(self.inner.winfo_children()):
            try:
                w.destroy()
            except Exception:
                pass
        self.tabs = {}
        items = self.items
        for i, it in enumerate(items):
            self._make_tab(it, last=(i == len(items) - 1))
        try:
            self.inner.update_idletasks()
        except Exception:
            pass
        self._sync_scroll()
        self.ensure_visible(self.active)

    def _make_tab(self, it, last=False):
        tid = it.get("id")
        locked = bool(it.get("locked"))
        bg, fg = self._tab_colors(tid)
        f = tk.Frame(self.inner, bg=bg, height=self.h, cursor="hand2")
        f.pack(side=tk.LEFT, fill=tk.Y)
        f.pack_propagate(False)
        rec = {"frame": f, "label": None, "close": None, "icon": None,
               "top": None, "sep": None, "sep_on": False, "icon_w": 0,
               "full": it.get("title") or "新建笔记", "locked": locked}
        top = ic = None
        if tid == self.active:
            # 活动标签顶部一条 2px 强调线：和「下方就是白稿纸」一起，把它点出来。
            # 记进 rec：切活动态时是「摘掉旧的 / 挂到新的」差量刷，不重建整条。
            top = tk.Frame(f, height=dpi.px(2), bg=self.pal.get("accent", "#2f7cf6"))
            top.pack(side=tk.TOP, fill=tk.X)
            rec["top"] = top
        if locked:
            # 加密且还没解锁：挂锁 —— 这一栏本来就是「锁着的」，用挂锁才说得清
            ic = tk.Canvas(f, width=self.ICON_W, height=self.h - dpi.px(2), bd=0,
                           highlightthickness=0, bg=bg)
            ic.pack(side=tk.LEFT)
            _draw_icon(ic, "lock", self.ICON_W / 2.0,
                       (self.h - dpi.px(2)) / 2.0, dpi.px(10),
                       self.pal.get("soft", "#76767e"))
            rec["icon"] = ic
            rec["icon_w"] = self.ICON_W
        elif self._note_photo is not None:
            # 普通笔记 / 已解锁的加密笔记：左侧那枚铅笔标记（icon/note.png），
            # 用来一眼认出「这是一篇笔记」。解锁后 locked 变 False，重建标签时
            # 自然就换成铅笔了。
            ic = tk.Label(f, image=self._note_photo, bg=bg, bd=0, pady=0)
            ic.pack(side=tk.LEFT, padx=(self.NOTE_PAD_L, self.NOTE_GAP))
            rec["icon"] = ic
            rec["icon_w"] = (self.NOTE_PAD_L + self._note_photo.width() +
                             self.NOTE_GAP)
        lbl = tk.Label(f, text=rec["full"], bg=bg, fg=fg, anchor="w",
                       bd=0, pady=0,
                       font=(FONT[0], FONT[1] + self.TITLE_DPT))
        lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
        # 相邻标签之间那条竖线：标签底色只比条底深一点点，光靠底色几乎分不出边界，
        # 标题又都是浅灰 —— 没有这条线，五六个标签糊成一片。
        # ⚠️ pack 顺序：side=RIGHT 是**先 pack 的最靠右**，所以分隔线要先占住右边缘，
        # × 再贴在它左边。反过来分隔线会插到标题和 × 中间，把关闭按钮顶到边上。
        # 最后一枚不画 —— 它右边是空白区，画了反而像多了一枚标签。
        # 但「谁是最后一枚」会随重排变，所以线始终建着、只切 pack（见 _set_sep）：
        # 重排时不必 create/destroy，标签的控件对象才能保持不变。
        sep = tk.Frame(f, width=self.SEP_W, bg=self._sep_color())
        rec["sep"] = sep
        rec["sep_on"] = not last
        if not last:
            sep.pack(side=tk.RIGHT, fill=tk.Y)
        cl = tk.Label(f, text="×", bg=bg, fg=self.pal.get("soft", "#76767e"),
                      bd=0, pady=0,
                      font=(FONT[0], FONT[1] + self.CLOSE_DPT), cursor="hand2")
        cl.pack(side=tk.RIGHT, padx=(0, dpi.px(5)))
        rec["label"] = lbl
        rec["close"] = cl
        # 事件：点标签切换、**按住拖拽换位置**、中键关闭、右键菜单。这几样绑在 Frame 上，
        # 而 Tk 的事件不往父控件走 —— 铺满标签的标题/挂锁/强调线靠下面的 _bubble 递上来。
        f.bind("<Button-1>", lambda e, t=tid: self._on_tab_press(e, t))
        f.bind("<B1-Motion>", lambda e, t=tid: self._on_tab_drag(e, t))
        f.bind("<ButtonRelease-1>", lambda e, t=tid: self._on_tab_release(e, t))
        f.bind("<Button-2>", lambda e, t=tid: self.on_close and self.on_close(t))
        # 右键：标签本体弹「这一枚」的菜单。必须 break —— 标签的 bindtags 里还排着
        # inner（`_bubble`），不拦住会在同一击里再弹一次空白区菜单。
        f.bind("<Button-3>", lambda e, t=tid: (self.on_menu and
                                               self.on_menu(e, t), "break")[1])
        f.bind("<Enter>", lambda e, t=tid: self._set_hover(t))
        # 离开：在标题与 × 之间挪动也会各来一次 Leave，照单全收会把高亮闪掉，
        # 所以先看清指针到底还在不在这一枚上（见 _leave_tab）。
        f.bind("<Leave>", lambda e, t=tid: self._leave_tab(t))
        # 标题 / 挂锁 / 顶部强调线三样几乎铺满整枚标签，而 Tk 的事件不往父控件走 ——
        # 不给它们接上这条链路的话，「点标签」只有点到 × 或几像素的边距才有反应。
        for w in (lbl, ic, top, sep):
            if w is not None:
                self._bubble(w, f)
        cl.bind("<Button-1>", lambda e, t=tid: self.on_close and self.on_close(t))
        # × 故意不进 _bubble（否则点关闭会顺带触发「切换」），但它也得能右键：
        # 指针停在标签最右端时右键，落在的就是这枚 16px 的 ×。
        cl.bind("<Button-3>", lambda e, t=tid: (self.on_menu and
                                                self.on_menu(e, t), "break")[1])
        cl.bind("<Enter>", lambda e, c=cl: c.configure(
            fg=self.pal.get("text", "#1f1f24")))
        cl.bind("<Leave>", lambda e, c=cl: c.configure(
            fg=self.pal.get("soft", "#76767e")))
        self.tabs[tid] = rec
        self._fit_tab(rec, tid)

    def _tab_colors(self, tid):
        """（底色, 文字色）：活动 = 稿纸色，悬停 = hover 档，常态 = 底色压深一档。

        ⚠️ 拖拽样式要排在活动前面：拖的那枚几乎总是刚被点过的活动标签，让活动
        样式压住它的话，拖拽全程标签本身毫无反馈（只剩那条竖线在动）。
        """
        p = self.pal
        if self._drag is not None and tid == self._drag.get("tid"):
            # 正在被拖的那一枚：再深一档，和「悬停」区分开 —— 拖着它划过别人时，
            # 悬停高亮会一直在换人，两个都用同一档就分不清哪个是在手上的。
            return (_mix_hex(self.base, _ink_target(self.base), 0.14),
                    p.get("text", "#1f1f24"))
        if tid == self.active:
            return p.get("editor", "#ffffff"), p.get("text", "#1f1f24")
        if tid == self.hover:
            return _hover_bg(self.base), p.get("text", "#1f1f24")
        # 常态底色只比条底深一点（TAB_IDLE_MIX）：深了会把「悬停再暗一档」的层次吃掉，
        # 边界交给下面的 1px 分隔线去讲。⚠️ 口径与 theme._tab_idle_bg 同源 ——
        # 只读提示条要拿它当「不许撞色」的参照，两处写死 0.06 迟早分叉。
        return _tab_idle_bg(self.base), p.get("soft", "#76767e")

    def _sep_color(self):
        """标签之间的竖分隔线颜色。

        不能直接用 `pal["line"]`：皮肤里 line 只比底色深 5%，1px 宽几乎看不出来
        （顶栏那条 1px 分割线就栽在这上面）。这里自己从底色推一档，
        比常态标签底色（压深 0.05）明显，又不至于抢戏。
        """
        return _mix_hex(self.base, _ink_target(self.base), 0.28)

    def _set_sep(self, rec, show):
        """切换某枚标签右边缘那条竖线（重排后「最后一枚」换人了）。

        ⚠️ pack 顺序：side=RIGHT 是**先 pack 的最靠右**，所以补线时要用 before=×，
        线才落在标签最右边缘；直接 pack 会插到标题和 × 中间，把关闭钮顶到边上。
        """
        sep = rec.get("sep")
        if sep is None or bool(rec.get("sep_on")) == bool(show):
            return
        rec["sep_on"] = bool(show)
        cl = rec.get("close")
        try:
            if not show:
                sep.pack_forget()
            elif cl is not None and cl.winfo_exists():
                sep.pack(side=tk.RIGHT, fill=tk.Y, before=cl)
            else:
                sep.pack(side=tk.RIGHT, fill=tk.Y)
        except Exception:
            pass

    def _index_of(self, tid):
        for i, it in enumerate(self.items):
            if it.get("id") == tid:
                return i
        return -1

    def set_title(self, tid, title):
        """只改某一枚标签的文字：标题是正文第一行，打字时它一直在变，
        整条重建会跟着每一个去抖周期闪一下，所以只重排这一片。"""
        rec = self.tabs.get(tid)
        if rec is None or rec["full"] == title:
            return False
        for it in self.items:
            if it.get("id") == tid:
                it["title"] = title
                break
        rec["full"] = title
        self._fit_tab(rec, tid)
        return True

    def _fit_tab(self, rec, tid):
        """按文字长度重算宽度并截断（标题变长变短都要跟着走）。"""
        locked = rec["locked"]
        # 左侧图标占多宽（铅笔标记 / 挂锁），标题可用宽度得把它那份扣掉
        icon_w = rec.get("icon_w", 0)
        # 最后一枚没有分隔线（右边是空白区），宽度预算里别给它多算这 1px
        idx = self._index_of(tid)
        sep_w = 0 if idx == len(self.items) - 1 else self.SEP_W
        avail = self.TAB_MAX - self.CLOSE_W - sep_w - icon_w
        text = self._clip(rec["full"], max(12, avail))
        try:
            rec["label"].configure(text=text)
        except Exception:
            pass
        w = icon_w + self._measure(text) + self.CLOSE_W + sep_w
        try:
            rec["frame"].configure(
                width=max(self.TAB_MIN, min(self.TAB_MAX, int(w) + dpi.px(8))))
        except Exception:
            pass
        self._sync_scroll()

    def _repaint(self):
        """只换颜色（悬停变了）：宽度没变，不必推倒重来。"""
        for tid, rec in self.tabs.items():
            bg, fg = self._tab_colors(tid)
            try:
                rec["frame"].configure(bg=bg)
                rec["label"].configure(bg=bg, fg=fg)
                rec["close"].configure(bg=bg)
                if rec["icon"] is not None:
                    rec["icon"].configure(bg=bg)
            except Exception:
                pass

    def _blank_menu(self, event):
        """条底空白处的右键：交给 on_menu，且 tid=None（没有锚定某一枚标签）。

        ⚠️ 必须 `return "break"`：TabStrip 自己、canvas、inner 是嵌套的，事件虽然
        不冒泡，但 `inner` 排在标签子控件的 bindtags 里（`_bubble`）—— 只在
        「标签没接住」时才会走到这里，正常点标签不会误触。
        """
        if self.on_menu:
            self.on_menu(event, None)
        return "break"

    def _bubble(self, w, frame):
        """把 frame（连同 inner，为的是滚轮）排进子控件的 bindtags。

        Tk 的事件**不向父控件冒泡**：默认 bindtags 只有 [自己, 类, 顶层, all]，
        点在标题 Label 上就等于点了个没有任何绑定的控件 —— 父 Frame 上的
        「点一下切过去」收不到，表现出来就是「标签点不动」。
        把 frame 的名字塞进子控件的 bindtags，事件就会顺着 tag 表走到它身上。

        ⚠️ × 不能这么处理：它自己绑着「关闭」，再冒泡上去会顺带触发「切换」，
        于是关掉一篇的同时又把它（或下一枚）装回来。
        """
        try:
            w.bindtags((str(w), str(self.inner), str(frame), w.winfo_class(),
                        str(w.winfo_toplevel()), "all"))
        except Exception:
            pass

    def _pointer_in(self, rec):
        """此刻指针是否落在这枚标签上（含它的任意子控件）。"""
        try:
            w = self.winfo_containing(self.winfo_pointerx(), self.winfo_pointery())
        except Exception:
            return False
        target = str(rec["frame"])
        while w is not None:
            if str(w) == target:
                return True
            try:
                p = w.winfo_parent()
                w = self.nametowidget(p) if p else None
            except Exception:
                return False
        return False

    def _leave_tab(self, tid):
        """指针真的离开这一枚才清悬停。

        Enter / Leave 是**逐个控件**发的：从标题挪到 ×、或从这枚挪到隔壁那枚，
        中间都会先来一次 Leave。只有「指针已在别处」才该换高亮。
        """
        rec = self.tabs.get(tid)
        if rec is not None and self._pointer_in(rec):
            return
        for oid, orec in self.tabs.items():
            if oid != tid and self._pointer_in(orec):
                self._set_hover(oid)
                return
        self._set_hover(None)

    def _set_hover(self, tid):
        # 拖拽期间别跟着指针换高亮：被拖的那枚已经有了固定的深色，划过别人时
        # 一个个亮起来反而看不清落点（落点由那条竖线讲）。
        if self._drag is not None:
            return
        if tid == self.hover:
            return
        self.hover = tid
        self._repaint()

    # ---------- 拖拽重排 ----------
    #
    # 按下即切换（和以前一样），拖过阈值才进入拖拽：此时不挪任何控件，只在插入位
    # 画一条竖线；**松手才真正重排**。为什么不实时换位：pack 一重排，被拖的那枚就从
    # 指针下面滑走了，而 Tk 没有「拖影」一说（Frame 在布局里，位置不能自由偏），
    # 结果是指针和标签互相追着跑。松手再排还能保证整个过程里一个控件都不销毁 ——
    # Tk 的隐式按捕获靠的就是「按下者还活着」。

    def _on_tab_press(self, event, tid):
        rec = self.tabs.get(tid)
        # 记下控件对象本身：按下会立刻切过去，而切到/切走加密笔记时 locked 变、
        # 整条会被重建（_can_reorder 也救不了），那时这个 frame 已经不存在了。
        # 拿对象做一致性判据，才能认出「起点没了」而不是抓一串死引用。
        self._press = {"tid": tid, "frame": rec["frame"] if rec else None,
                       "x": event.x_root, "y": event.y_root}
        if self.on_select:
            self.on_select(tid)

    def _on_tab_drag(self, event, tid):
        p = self._press
        if p is None or p["tid"] != tid:
            return
        rec = self.tabs.get(tid)
        if (rec is None or p["frame"] is None or rec["frame"] is not p["frame"]
                or not p["frame"].winfo_exists()):
            self._press = None
            self._drag_end()
            return
        if self._drag is None:
            if (abs(event.x_root - p["x"]) < self.DRAG_TOL and
                    abs(event.y_root - p["y"]) < self.DRAG_TOL):
                return
            self._drag_start(tid)
        self._drag["x"] = event.x_root
        self._update_drop(event.x_root)
        dirn = self._auto_dir(event.x_root)
        if dirn != self._drag.get("dirn"):
            self._drag["dirn"] = dirn
            if dirn and not self._drag.get("job"):
                self._drag["job"] = self.after(60, self._drag_tick)

    def _on_tab_release(self, event, tid):
        d = self._drag
        self._press = None
        if d is None:
            return
        slot, src = d.get("idx"), self._index_of(d["tid"])
        self._drag_end()
        if slot is None or src < 0 or not self.on_reorder:
            return
        # 「插到第 slot 枚之前」换算成「最终停在第几位」：往后挪要把自己那一格扣掉
        final = slot if slot <= src else slot - 1
        if 0 <= final < len(self.items) and final != src:
            self.on_reorder(d["tid"], final)

    def _drag_start(self, tid):
        self._drag = {"tid": tid, "idx": None, "x": 0, "dirn": 0, "job": None,
                      "line": None}
        rec = self.tabs.get(tid)
        if rec is not None:
            try:
                # 子控件（标题/铅笔）自带 hand2，会盖住 Frame 的光标 —— 一并改掉
                rec["frame"].configure(cursor="sb_h_double_arrow")
                for w in (rec["label"], rec["icon"]):
                    if w is not None:
                        w.configure(cursor="sb_h_double_arrow")
            except Exception:
                pass
        # 插入位的竖线：做成 inner 的子控件用 place 摆。
        # 不能用 Canvas 图元 —— create_window 的窗口图元永远画在所有图元之上，
        # 画上去也会被 inner 这块实心 Frame 盖住。
        try:
            self._drag["line"] = tk.Frame(self.inner, width=self.DRAG_LINE_W,
                                          bg=self.pal.get("accent", "#2f7cf6"))
        except Exception:
            self._drag["line"] = None
        self._repaint()

    def _drag_end(self):
        d = self._drag
        self._drag = None
        if d is None:
            return
        job = d.get("job")
        if job:
            try:
                self.after_cancel(job)
            except Exception:
                pass
        line = d.get("line")
        if line is not None:
            try:
                line.destroy()
            except Exception:
                pass
        rec = self.tabs.get(d.get("tid"))
        if rec is not None:
            try:
                rec["frame"].configure(cursor="hand2")
                for w in (rec["label"], rec["icon"]):
                    if w is not None:
                        w.configure(cursor="hand2")
            except Exception:
                pass
        self.hover = None
        self._repaint()

    def _drop_index(self, x_root):
        """指针落在哪个插入位（0..n，n 表示「排到最后」）。

        ⚠️ 必须走 canvasx()：横向滚动是 xview_moveto 做的，inner 本身不动，
        frame.winfo_x() 永远是「没滚动时」的位置，直接用 x_root 差值在滚动后算错。
        """
        try:
            x = self.canvas.canvasx(int(x_root) - self.canvas.winfo_rootx())
        except Exception:
            return None
        pos = 0
        order = [it.get("id") for it in self.items]
        for i, tid in enumerate(order):
            rec = self.tabs.get(tid)
            if rec is None:
                continue
            w = rec["frame"].winfo_width()
            if x < pos + w / 2.0:
                return i
            pos += w
        return len(order)

    def _update_drop(self, x_root):
        d = self._drag
        if d is None:
            return
        idx = self._drop_index(x_root)
        if idx is None:
            return
        d["idx"] = idx
        line = d.get("line")
        if line is None:
            return
        pos = 0
        for tid in [it.get("id") for it in self.items][:max(0, min(idx, len(self.items)))]:
            rec = self.tabs.get(tid)
            if rec is not None:
                pos += rec["frame"].winfo_width()
        try:
            line.place(x=max(0, pos - self.DRAG_LINE_W // 2), y=0,
                       width=self.DRAG_LINE_W, height=self.h)
            line.lift()
        except Exception:
            pass

    def _auto_dir(self, x_root):
        """指针贴到画布左右边缘时返回滚动方向（-1 / 0 / 1）。"""
        try:
            x = int(x_root) - self.canvas.winfo_rootx()
            w = self.canvas.winfo_width()
        except Exception:
            return 0
        if w <= 1:
            return 0
        if x < self.DRAG_EDGE:
            return -1
        if x > w - self.DRAG_EDGE:
            return 1
        return 0

    def _drag_tick(self):
        """贴着边缘停住时持续横向滚，否则视野外的位置永远拖不到。"""
        d = self._drag
        if d is None:
            return
        d["job"] = None
        if not d.get("dirn"):
            return
        self._scroll(d["dirn"], ratio=0.12)
        self._update_drop(d.get("x") or 0)
        d["job"] = self.after(60, self._drag_tick)

    # ---------- 滚动 ----------

    def _metrics(self):
        total = max(1, self.inner.winfo_reqwidth())
        view = max(1, self.canvas.winfo_width())
        return total, view, self._x

    def _scroll(self, direction, ratio=0.35):
        total, view, cur = self._metrics()
        step = max(60, int(view * ratio))
        self._x = min(max(0, total - view), max(0, cur + direction * step))
        self.canvas.xview_moveto(self._x / float(total))
        self._sync_scroll()

    def _wheel(self, e):
        self._scroll(-1 if e.delta > 0 else 1, ratio=0.20)
        return "break"

    def _sync_scroll(self):
        try:
            total, view, cur = self._metrics()
        except Exception:
            return
        overflow = total > view + 2
        if not overflow:
            self._x = 0
            self.canvas.xview_moveto(0)
            cur = 0
        else:
            self._x = min(self._x, total - view)
            cur = self._x
        self.canvas.configure(scrollregion=(0, 0, total, self.h))
        self._show_arrow(self.arrow_l, overflow and cur > 1, tk.LEFT)
        self._show_arrow(self.arrow_r, overflow and cur < total - view - 1, tk.RIGHT)

    def _show_arrow(self, arrow, show, side):
        try:
            mapped = bool(arrow.winfo_ismapped())
        except Exception:
            mapped = False
        if show and not mapped:
            # before=canvas：先占住自己的宽度，否则 expand 的画布把它挤成几像素
            arrow.pack(side=side, fill=tk.Y, before=self.canvas)
        elif not show and mapped:
            arrow.pack_forget()

    # ---------- 文字量算 ----------

    def _measure(self, s):
        try:
            return self._font.measure(s)
        except Exception:
            return len(s) * (FONT[1] + 1)

    def _clip(self, s, avail):
        """按像素截断，超了补省略号（Tk 的 Label 不会自己省略）。"""
        if self._font is None or avail <= 8:
            return s
        if self._measure(s) <= avail:
            return s
        lo = 1
        for i in range(1, len(s) + 1):
            if self._measure(s[:i] + "…") > avail:
                break
            lo = i
        return s[:max(1, lo)] + "…"
# 图形取自 icon/sz.svg（1024 viewBox，5 个子路径：上下两条通栏横杠、右侧两条短横杠、
# 左边一个实心三角）。这里按**内容包围盒**重新归一化到 0..1：原图四周各有 13% 的
# 空白，照 viewBox 直接缩会让图形只剩七成、18px 下横杠退化到 1.5px。
# 归一化后实测：四根横杠等厚（0.158 格）、缝 0.123 格，18px 下就是 3px 杠 / 2px 缝。
SZ_BARS = ((0.0000, 1.0000, 0.0000, 0.1579),    # 上通栏
           (0.4286, 1.0000, 0.2807, 0.4386),    # 右上短杠
           (0.4286, 1.0000, 0.5614, 0.7193),    # 右下短杠
           (0.0000, 1.0000, 0.8421, 1.0000))    # 下通栏
# 朝左的实心三角：尖在中线左缘，右腰贴到 0.286 格
SZ_ARROW = ((0.0, 0.5000), (0.2857, 0.2546), (0.2857, 0.7454))


def _draw_sidebar_icon(cv, cx, cy, size, color, outdent=False):
    """画「侧栏开关」：四根横杠 + 一个实心三角。outdent=False 即 sz.svg 原样（三角朝左）。

    全用实心图元：Tk 的 Canvas 不做抗锯齿，描边型图形在 18px 上会散成毛边，
    块面反而边界干脆 —— 也和图标的块面语言一致。
    坐标先按像素 round 再画：四根杠的厚度是同一档，不取整会出现 3px / 2px 混排。
    镜像在**像素空间**做（x → size - x），round 过的整数镜像后仍是整数。
    """
    s = float(size)
    left, top = cx - s / 2.0, cy - s / 2.0
    for bx0, bx1, by0, by1 in SZ_BARS:
        a, b = bx0 * s, bx1 * s
        if outdent:
            a, b = s - b, s - a
        cv.create_rectangle(round(left + a), round(top + by0 * s),
                            round(left + b), round(top + by1 * s),
                            fill=color, outline="")
    pts = []
    for ax, ay in SZ_ARROW:
        x = (s - ax * s) if outdent else ax * s
        pts.extend((round(left + x), round(top + ay * s)))
    cv.create_polygon(pts, fill=color, outline="")


def _draw_chevron_pair(cv, cx, cy, size, color, inward=False, stroke=ICON_STROKE):
    """竖向双箭尖（一上一下）：箭尖朝外 = 摊开，朝内 = 收拢。

    几何按参考稿实测折算：单枚折线半宽 a≈0.30×size、箭尖到臂末端的纵向距离
    arm≈0.34×size。朝外那支的箭尖钉在 cy ± size/2，miter 还会再向外伸出
    ext = t·√(a²+arm²)/a；朝内那支的**臂端**就顶在这条外沿上 —— 两支的
    外沿一致，并排看才一样高。

    ⚠️ 画成**填充多边形**而不是 create_line(width=…)：Tk 的 Canvas 不做抗锯齿，
    粗线的圆角（join）与圆帽（cap）伸出端点的距离并不一样（实测差 1 行）。
    而这两枚图标，一支把「箭尖」放在外沿、另一支把「臂端」放在外沿 —— 同一条
    线画出来，两支就必然一高一矮（光调锚点补不平：0.17→0.16、GLYPH 13→12
    都试过，仍差 1 行）。改成多边形后：轮廓全由顶点决定，不经过 cap / join
    那套光栅近似。这与 `_draw_sidebar_icon` / `_draw_gear_icon` 用块面而非
    描边是同一个理由。

    ⚠️ 朝内**不是**朝外整枚上下翻转：这组双箭尖本身上下对称，整枚翻转等于
    什么都没做（两支会一模一样）。要的是**每支就地翻向**：上枚朝下、下枚朝上。

    ⚠️ 别画成「两枚同向」：同向是翻页 / 滚动（工具栏两端 ArrowButton 就是这么画的），
    一外一内才读得出「整组摊开 / 整组收拢」——这正是文件夹的展开全部 / 收起全部。
    """
    a = size * 0.30               # 每支箭头的半宽
    arm = size * 0.34             # 箭尖到两条臂末端的纵向距离
    t = stroke / 2.0              # 笔画半宽（多边形的半厚度）
    ext = t * ((a * a + arm * arm) ** 0.5) / a      # 箭尖 miter 向外伸出的距离
    if inward:
        # 臂端顶到与朝外那支箭尖相同的外沿（臂端是平头，不自己向外伸）
        outer = size / 2.0 + ext
        bands = ((cy - (outer - arm), -1), (cy + (outer - arm), 1))
    else:
        outer = size / 2.0
        bands = ((cy - outer, 1), (cy + outer, -1))

    def band(apex_y, sgn):
        """一支箭头的外轮廓：apex_y 是中心线上的箭尖，sgn 决定臂朝上还是朝下。"""
        p0 = (cx - a, apex_y + sgn * arm)
        p1 = (cx, apex_y)
        p2 = (cx + a, apex_y + sgn * arm)
        # 两条边的单位法向量，相加即角平分线；沿它走 t/cos(半角) 就是 miter 顶点
        d1 = (p1[0] - p0[0], p1[1] - p0[1])
        d2 = (p2[0] - p1[0], p2[1] - p1[1])
        l1 = (d1[0] ** 2 + d1[1] ** 2) ** 0.5
        l2 = (d2[0] ** 2 + d2[1] ** 2) ** 0.5
        n1 = (-d1[1] / l1, d1[0] / l1)
        n2 = (-d2[1] / l2, d2[0] / l2)
        mx, my = n1[0] + n2[0], n1[1] + n2[1]
        ml = (mx ** 2 + my ** 2) ** 0.5
        mx, my = mx / ml, my / ml
        k = t / (mx * n1[0] + my * n1[1])
        return [(p0[0] + n1[0] * t, p0[1] + n1[1] * t),
                (p1[0] + mx * k, p1[1] + my * k),
                (p2[0] + n2[0] * t, p2[1] + n2[1] * t),
                (p2[0] - n2[0] * t, p2[1] - n2[1] * t),
                (p1[0] - mx * k, p1[1] - my * k),
                (p0[0] - n1[0] * t, p0[1] - n1[1] * t)]

    for apex_y, sgn in bands:
        cv.create_polygon([c for p in band(apex_y, sgn) for c in p],
                          fill=color, outline="")


def _draw_chevron(cv, cx, cy, size, color, down=False):
    """单个**填充实心三角**：`down=False` 是 ▶，`True` 是 ▼（转 90°）。

    名字沿用折线年代（现在画的不是 chevron），只为不改调用点。size 是墨迹的
    外接边长，两态共用同一个正方形外接框：收起态底边在左、顶点在右；展开态
    底边在上、顶点在下。

    ⚠️ 「半宽 = 半高」仍是硬约束：两态只差一次 90° 旋转，只有外接方框是正方形，
    它们才在像素上互为转置、看起来一样大。早先按 `>` 在常见 UI 里的比例取
    0.28 / 0.42（宽高比 0.66），实测收起 7×11、展开 11×7 —— 展开时箭头明显
    矮一截，收/放之间像「箭头缩了」。别回退。

    ⚠️ 画**填充多边形**而不是 `create_line(width=…)`：Tk Canvas 不抗锯齿，
    1.7px 的描线在 8~9px 格子里，圆帽 / 圆角会把端点撑成方角、两臂不等粗
    （2026-11-06 八倍放大图：收起态右端多出一块、V 两臂粗细不一），两个形态
    还容易读成「噪点」和「对勾」。块面同时和「文件夹」标题右侧的
    `_draw_chevron_pair`（全部展开 / 收起）是同一套图标语言；⚠️ 小图标别用
    oval / arc，顶点一律落在整数上。

    ⚠️ cx / cy 请给**像素中心**（SIZE_W/2 − 0.5、SIZE_H/2 − 0.5），GLYPH 取
    **奇数**：画布宽高是偶数时中心落在 .5，size 为奇数则 size/2 也是 .5，
    两者相减才是整数顶点；偏半像素会让两态各自朝一侧多出 1px，转置关系就破了。
    ⚠️ 高 DPI 下画布边长可能是**奇数**（125% / 175% / 225% 档的 px(28) =
    35 / 49 / 63），此时中心会落到整数上，函数内部统一规整回 .5 尾数；取整一律
    `int(v + 0.5)`，**不能用 round()**（银行家舍入把 .5 往偶数拽，平头会偏 1px）。

    ⚠️ 和 `_draw_chevron_pair` 不是一回事：那是两枚并排（翻页/滚动/整组收放），
    这是单枚（树节点收放）。单枚才能和「点这一行前面的箭头」对上。
    """
    # 中心必须是 .5 尾数：奇数边长的画布会让调用方传进整数中心，与 h(.5)
    # 相减就成半像素，round 之后三角高比宽少/多 1px —— 两态转置关系随之破掉。
    if cx % 1 == 0:
        cx += 0.5
    if cy % 1 == 0:
        cy += 0.5
    # ⚠️ int(v + 0.5) 而不是 round()：Python 3 的 round(14.5) = 14（银行家
    # 舍入），会把 .5 往偶数拽；平头是按 mx / my 定的，一拽就偏 1px。
    def _i(v):
        return int(v + 0.5)

    h = size / 2.0
    x0, x1 = _i(cx - h), _i(cx + h)
    y0, y1 = _i(cy - h), _i(cy + h)
    mx, my = _i(cx), _i(cy)
    # 尖端截成 **2px 平头**：等腰三角收窄到 1px 时，Tk 的光栅会把最后两行
    # 画成一根 1px 的细「尾巴」（八倍图里像水滴 / 定位针）。平头跨在中心线
    # 两侧（x: mx−1..mx，y: my−1..my），方向感不变，两态仍互为 90° 转置。
    # ⚠️ 平头取偶数宽：中心落在 .5 上，奇数宽会让平头偏半像素、转置关系破掉。
    if down:
        pts = ((x0, y0), (x1, y0), (mx, y1), (mx - 1, y1))
    else:
        pts = ((x0, y0), (x1, my - 1), (x1, my), (x0, y1))
    cv.create_polygon([v for xy in pts for v in xy], fill=color, outline="")


# ---------- 文件夹行首的「>」展开箭头 ----------
class TwistyButton(tk.Canvas):
    """树里每行前面那枚「>`/`v」：点它收 / 放这一支。

    和顶栏 `IconButton` 同一套交互（常态不画底片，指针停上来才浮一层圆角底），
    但独立成类而不是继承，原因有两个：
      * 它的底色是**行**的底色（选中行是浅绿、普通行是侧栏色、悬停时还会变），
        每次行 hover 都要按新底色重画底片 —— 需要单独的 `refresh_bg()` 入口；
      * 它要能在「叶子」形态下整块隐身（不画箭头、不出 hover 底片、光标不变手形），
        这是 `IconButton` 没有的状态。

    **不挂悬浮提示**：用户明确要求鼠标停上来只浮底片、不弹气泡。顶栏那几个
    图标带提示是因为图形本身看不出干什么，而这枚箭头就在文件夹名前面，语义
    一眼就懂；一行一行地弹提示反而跟着鼠标乱闪。

    尺寸 24×28 是刻意取的：原来是 13×16 的实心小三角，用户反馈「太小、不知道能点」，
    先放到 18×18 后仍嫌不好点 —— 现在高度直接顶到左栏一行的可点高度（行高 38~39
    由行里的字撑起，这一个 28 高正好填满），宽度 24。⚠️ 点击区只放大过、不许缩回去。

    箭头是 9px **填充实心三角**（2026-11-06 方案 C）：此前是 8px 细描线折线、
    常态 `soft` 色、只有 hover 才浮底片，实景里小得像噪点，收起态 `>` 还常被
    读成勾号。现在常态就用 `text` 色，静态也看得出「这里能点」；hover 底片从
    20×24 收到 16×16 贴着箭头，不再冒出一大片色块、中间只有一丁点墨迹。
    """

    SIZE_W = FOLDER_TWISTY_W     # 点击区宽（不许缩）
    SIZE_H = FOLDER_TWISTY_H     # 点击区高（= 左栏一行的可点高度）
    # 奇数 → 顶点落在整像素，两态才严格互为 90° 转置（见 _draw_chevron）
    GLYPH = dpi.px(9) | 1
    PLATE = dpi.px(16)           # hover / press 圆角底片边长（居中贴着箭头）

    def __init__(self, master, pal, collapsed=False, available=True, command=None,
                 base=None):
        self._pal = dict(pal)
        self._collapsed = bool(collapsed)
        self._available = bool(available)
        self._command = command
        self._state = "normal"
        # base 是**这一行**的底色（选中行是浅绿、普通行是侧栏色）：底片由它推出来，
        # 传错就会在选中行上浮出一块别色的方片
        self._base = base if base is not None else pal.get("side", "#e8f0e9")
        tk.Canvas.__init__(self, master, width=self.SIZE_W, height=self.SIZE_H,
                           bd=0, highlightthickness=0, bg=self._base,
                           cursor=("hand2" if available else ""))
        self._bind_events()
        self.build()

    # ---------- 画 ----------
    def build(self):
        """重画：底片（仅 hover/press）+ 箭头。底色永远读 `self["bg"]`。"""
        self.delete("all")
        if not self._available:
            return
        p = self._pal
        bg = self["bg"]
        if self._state in ("hover", "press"):
            fill = _press_bg(bg) if self._state == "press" else _hover_bg(bg)
            fg = p.get("selfg", p.get("text", "#1f1f24"))
            # 底片 16×16 居中：旧版按点击区四周内缩 2px（20×24）配 8px 箭头，
            # 鼠标一停就冒出一大片色块、中间只有一丁点墨迹。现在底片贴着箭头，
            # 「哪儿能点」仍由整格 24×28 兜着（只是不画出来），点击区一个像素没动。
            s = self.PLATE
            x0 = (self.SIZE_W - s) / 2.0
            y0 = (self.SIZE_H - s) / 2.0
            self.create_polygon(
                _round_rect_points(x0, y0, x0 + s, y0 + s, dpi.px(5), 5),
                fill=fill, outline="")
        else:
            # 常态用正文色：文件夹图标 / 名字都是这个色系，箭头不该比它们还淡；
            # 静态下就要看得出这里有个能点的东西（不再只靠 hover 底片）。
            fg = p.get("text", "#1f1f24")
        # cx / cy 取像素中心（SIZE_W/2 − 0.5、SIZE_H/2 − 0.5）：24 列是 0..23、
        # 28 行是 0..27，真中心是 11.5 / 13.5 而不是 12 / 14。差这半像素，墨迹
        # 会整体向右下沉一行，四边留白从对称变成一侧多 1px。
        _draw_chevron(self, self.SIZE_W / 2.0 - 0.5, self.SIZE_H / 2.0 - 0.5,
                      self.GLYPH, fg, down=not self._collapsed)

    def refresh_bg(self):
        """底色被外面改过（行 hover / 选中 / 换肤）之后重画一遍底片。

        ⚠️ 少了这一步，行一变暗，底片还是按旧底色算的那一层 —— 悬停时看起来
        像贴了一块别色的方片。
        """
        try:
            self.build()
        except Exception:
            pass

    # ---------- 状态 ----------
    def set_collapsed(self, collapsed):
        collapsed = bool(collapsed)
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        self.build()

    def set_available(self, ok):
        """叶子（没有子文件夹）时整块隐身：不画、不出 hover 底、光标退回箭头。"""
        ok = bool(ok)
        if ok == self._available:
            return
        self._available = ok
        self._state = "normal"
        try:
            self.configure(cursor=("hand2" if ok else ""))
        except Exception:
            pass
        self.build()

    def set_command(self, command):
        self._command = command

    def apply_theme(self, pal, base=None):
        # 早退：剪枝后每次切文件夹都会走 _restyle_folders，二十来行各复制一遍
        # 调色板是白费；真换肤时 pal 的内容一定变了。
        if base is None and self._pal == pal:
            return
        self._pal = dict(pal)
        if base is not None:
            self._base = base
            self.configure(bg=base)
        self.build()

    # ---------- 事件 ----------
    def _bind_events(self):
        # 这一格**不挂 Tooltip**（见类注释）：`<Enter>` / `<Leave>` 只用来换 hover 态，
        # 没有第二个绑定来抢，所以不传 add="+"。
        self.bind("<Enter>", lambda e: self._set_state("hover"))
        self.bind("<Leave>", lambda e: self._set_state("normal"))
        self.bind("<ButtonPress-1>", lambda e: self._set_state("press"))
        self.bind("<ButtonRelease-1>", self._on_release)

    def _set_state(self, st):
        if not self._available or self._state == st:
            return
        self._state = st
        self.build()

    def _on_release(self, e):
        inside = (0 <= e.x < self.SIZE_W and 0 <= e.y < self.SIZE_H)
        self._set_state("hover" if inside else "normal")
        if inside and self._available and self._command:
            self._command()


# ---------- 顶栏的图标按钮 ----------
class IconButton(tk.Canvas):
    """顶栏上只画图形的小按钮（侧栏开关、全局设置共用一套交互）。

    与工具栏那几枚滑动箭头（圆角片 + 双箭尖）不同：这一排常态**不画底片** ——
    顶栏上摆一块块灰片太吵，只有鼠标真的停上来（或用键盘点下去）时才浮出一层
    18×18 的圆角底。

    子类只做两件事：给出 `TIP`（悬浮提示）和实现 `_paint(fg, bg)`。
    `bg` 是**此刻图形所在的那层底色**：齿轮靠「用底色挖空」画出孔洞，
    拿不到当前底色就会在 hover 时留一块方底色。
    """

    SIZE = dpi.px(18)            # 画布边长（顶栏 30 高，上下各留 6px）
    GLYPH = dpi.px(13)           # 图形边长
    TIP = ""

    def __init__(self, master, pal, command=None, base=None, tip=None):
        self._pal = dict(pal)
        self._command = command
        self._base = base if base is not None else pal.get("top", "#ececec")
        self._state = "normal"
        tk.Canvas.__init__(self, master, width=self.SIZE, height=self.SIZE,
                           bd=0, highlightthickness=0, cursor="hand2",
                           bg=self._base)
        self._tip = Tooltip(self, tip or self.TIP, self._pal)
        self.bind_events()
        self.build()

    def build(self):
        self.delete("all")
        p = self._pal
        if self._state in ("hover", "press"):
            fill = _press_bg(self._base) if self._state == "press" else _hover_bg(self._base)
            fg = p.get("selfg", p.get("text", "#1f1f24"))
            self.create_polygon(
                _round_rect_points(1, 1, self.SIZE - 1, self.SIZE - 1, 5, 5),
                fill=fill, outline="")
        else:
            fill = self._base
            fg = p.get("soft", "#76767e")
        self._paint(fg, fill)

    def _paint(self, fg, bg):
        """子类在这里画图形。cx / cy 由基类给定这套 26px 画布的中心。"""
        raise NotImplementedError

    def apply_theme(self, pal, base=None):
        self._pal = dict(pal)
        if base is not None:
            self._base = base
        self._tip.pal = dict(pal)
        self.configure(bg=self._base)
        self.build()

    def bind_events(self):
        # ⚠️ <Enter> / <Leave> 必须 add="+"：Tooltip 在 __init__ 里已经先给同一对
        # 事件绑过自己的显示/收起，这里再来一次不带 add 的 bind 是**替换**不是追加，
        # 会把提示顶掉 —— 顶栏三枚图标就只剩 hover 底色、永远不出气泡。
        self.bind("<Enter>", lambda e: self._set_state("hover"), add="+")
        self.bind("<Leave>", lambda e: self._set_state("normal"), add="+")
        self.bind("<ButtonPress-1>", lambda e: self._set_state("press"))
        self.bind("<ButtonRelease-1>", self._on_release)

    def _set_state(self, st):
        if self._state == st:
            return
        self._state = st
        self.build()

    def _on_release(self, e):
        inside = (0 <= e.x < self.SIZE and 0 <= e.y < self.SIZE)
        self._set_state("hover" if inside else "normal")
        if inside and self._command:
            self._command()


class SidebarToggle(IconButton):
    """顶栏上那枚「收起 / 展开左侧两栏」的小图标（素笺标题右侧）。

    图标照 icon/sz.svg 折算：原样＝点击收起（三角朝左），水平镜像＝点击展开。
    """

    GLYPH = dpi.px(13)           # 图形边长（四根 2px 杠 + 2px 缝刚好整除）
    TIP = "收起左侧两栏"

    def __init__(self, master, pal, command=None, base=None):
        self._collapsed = False
        IconButton.__init__(self, master, pal, command=command, base=base)

    def _paint(self, fg, bg):
        _draw_sidebar_icon(self, self.SIZE / 2.0, self.SIZE / 2.0, self.GLYPH, fg,
                           outdent=self._collapsed)

    def set_dir(self, collapsed):
        """collapsed=True 时换成镜像（三角朝右），并同步悬浮提示的文案。"""
        collapsed = bool(collapsed)
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        self._tip.set_text("展开左侧两栏" if collapsed else "收起左侧两栏")
        self.build()


# ---------- 文件夹分组标题右侧的「全部展开 / 全部收起」 ----------
class TreeFoldButton(IconButton):
    """左栏「文件夹」标题右侧那两枚：全部展开 / 全部收起。

    与顶栏那排图标同一套交互（常态不画底片、悬停才浮出 18×18 圆角底），只有图形不同：
    竖向双箭尖，朝外=摊开（展开）、朝内=收拢（收起）。跟行首那枚三角刻意区分开 ——
    三角管**这一支**，这两枚管**整列**。
    """

    # 12 而不是继承来的 13：多边形画法下箭尖会再向外延伸约半个线宽
    # （t/sin 半角 ≈ 1.28），12 正好让墨迹占满 y2..y15，上下各留 2px
    GLYPH = dpi.px(12)
    INWARD = False
    TIP = "全部展开"

    def _paint(self, fg, bg):
        # cy 取 SIZE/2 − 0.5：18px 画布的行是 0..17，真正的中心是 8.5 而不是 9。
        # 差这半像素，墨迹会整体下沉一行（上留白 3、下留白 2），和标题中线对不齐
        _draw_chevron_pair(self, self.SIZE / 2.0, self.SIZE / 2.0 - 0.5,
                           self.GLYPH, fg, inward=self.INWARD)


class ExpandAllButton(TreeFoldButton):
    TIP = "全部展开"


class CollapseAllButton(TreeFoldButton):
    INWARD = True
    TIP = "全部收起"


# ---------- 顶栏的「全局设置」齿轮 ----------
# 图形取自 icon/setting.svg（1024 viewBox，四条闭合子路径：齿轮外轮廓 / 齿轮内轮廓 /
# 中心圆环的外圆与内圆）。原图铺满 70..954，四周只留 7% 白边，这里按**联合包围盒**
# 归一化到 0..1，再交给 RDP 抽稀：105 + 116 个点压到 25 + 30 个点，肉眼无差。
#
# 为什么不用描边：这个图标本质是「2px 描边的齿轮 + 中间一枚圆环」，而 Tk 的 Canvas
# 不做抗锯齿，2px 的斜描边会散成两排灰边、齿形全糊。改成「外轮廓填色 → 内轮廓填底色
# 挖空 → 中心圆填色 → 更小的圆填底色」，边界全是按像素算出来的块面，18~22px 都立得住。
GEAR_OUTER = (
    (0.7750, 0.8438), (0.6625, 0.9062), (0.6250, 1.0000), (0.3750, 1.0000),
    (0.3375, 0.9062), (0.2250, 0.8438), (0.1250, 0.8625), (0.0000, 0.6437),
    (0.0625, 0.5687), (0.0687, 0.4375), (0.0062, 0.3625), (0.1313, 0.1437),
    (0.2312, 0.1625), (0.3437, 0.1000), (0.3750, 0.0000), (0.6250, 0.0000),
    (0.6625, 0.0938), (0.7750, 0.1562), (0.8750, 0.1375), (1.0000, 0.3563),
    (0.9375, 0.4313), (0.9437, 0.4937), (0.9375, 0.5563), (1.0000, 0.6313),
    (0.8750, 0.8500),
)
GEAR_INNER = (
    (0.7187, 0.7250), (0.7937, 0.7438), (0.8563, 0.6375), (0.8063, 0.5813),
    (0.8125, 0.5000), (0.8063, 0.4250), (0.8563, 0.3687), (0.7937, 0.2625),
    (0.7187, 0.2813), (0.6467, 0.2303), (0.5875, 0.2063), (0.5625, 0.1250),
    (0.4375, 0.1250), (0.4125, 0.2000), (0.3422, 0.2281), (0.2813, 0.2750),
    (0.2063, 0.2562), (0.1437, 0.3625), (0.1937, 0.4187), (0.1875, 0.5000),
    (0.1937, 0.5750), (0.1437, 0.6313), (0.2063, 0.7375), (0.2813, 0.7187),
    (0.3533, 0.7697), (0.4125, 0.7937), (0.4375, 0.8750), (0.5625, 0.8750),
    (0.5875, 0.8000), (0.6578, 0.7719),
)
# 中心圆环的内外半径（同一套归一化单位）；圆心正好在 (0.5, 0.5)
GEAR_HUB = (0.1875, 0.0625)


def _draw_gear_icon(cv, cx, cy, size, color, bg):
    """画齿轮：外轮廓填图标色 → 内轮廓填底色挖空 → 中心圆环同理。"""
    s = float(size)
    left, top = cx - s / 2.0, cy - s / 2.0

    def poly(points):
        flat = []
        for nx, ny in points:
            flat.extend((left + nx * s, top + ny * s))
        return flat

    cv.create_polygon(poly(GEAR_OUTER), fill=color, outline="")
    cv.create_polygon(poly(GEAR_INNER), fill=bg, outline="")
    ro, ri = GEAR_HUB[0] * s, GEAR_HUB[1] * s
    cv.create_oval(cx - ro, cy - ro, cx + ro, cy + ro, fill=color, outline="")
    # 中心孔（1~2px）：SVG 原样保留，挖出来就是齿轮轴孔那一点亮
    cv.create_oval(cx - ri, cy - ri, cx + ri, cy + ri, fill=bg, outline="")


# ---------- 全局设置弹窗里的小节图标 ----------
# 全部按 **16×16 网格**设计，再按目标尺寸等比换算成像素；一律用**块面**
# （矩形 / 正交多边形）拼，不用 oval / arc / 1px 描线 —— Tk Canvas 不做抗锯齿，
# 小尺寸下弧线的边缘会毛刺、细线会被光栅掰成深浅不一的两行（同 _draw_chevron）。
# 共同语言：外框 + 内部实心块。缩到 14px 也还读得出来。
def _draw_section_icon(cv, kind, cx, cy, size, color, bg):
    """设置窗小节图标。

    kind: settings(齿轮) / font(字形 A) / theme(四格色板) / toolbar(工具条) / tab(标签页)。
    """
    if kind == "settings":
        _draw_gear_icon(cv, cx, cy, size, color, bg)
        return

    s = float(size)
    left, top = cx - s / 2.0, cy - s / 2.0
    u = s / 16.0                      # 一个网格单位等于多少像素
    mid_x, mid_y = left + s / 2.0, top + s / 2.0

    def px(n):
        """网格 x -> 像素。n > 8 的一侧按中线镜像算，保证左右严格对称。

        直接 `left + n*u` 取整会让两侧各自舍入：125% 缩放下 px(2)=3 / px(14)=18，
        左边距 3px、右边距 2px，四格色板就变成「左边三块宽、右边两块窄」。
        镜像后 px(14) = 2*中心 - px(2)，两边永远一样。
        """
        if n > 8:
            return int(2 * mid_x - px(16 - n) + 0.5)
        return int(left + n * u + 0.5)

    def py(n):
        if n > 8:
            return int(2 * mid_y - py(16 - n) + 0.5)
        return int(top + n * u + 0.5)

    w = max(1, int(2 * u + 0.5))      # 框线宽（网格 2 格）

    def rect(x0, y0, x1, y1, col=None):
        cv.create_rectangle(px(x0), py(y0), px(x1), py(y1),
                            fill=col or color, outline="")

    def ring(x0, y0, x1, y1):
        """矩形描边 = 四条实心矩形。

        不用 create_line(width=…)：Tk 的线居中在坐标上，宽 2 时左右各吃半像素，
        光栅后一条边 2px、另一条 1px（八倍图里两边不等宽）。四条矩形则各边都
        正好 w 像素，且左右 / 上下对称。
        """
        X0, Y0, X1, Y1 = px(x0), py(y0), px(x1), py(y1)
        cv.create_rectangle(X0, Y0, X1, Y0 + w, fill=color, outline="")
        cv.create_rectangle(X0, Y1 - w, X1, Y1, fill=color, outline="")
        cv.create_rectangle(X0, Y0, X0 + w, Y1, fill=color, outline="")
        cv.create_rectangle(X1 - w, Y0, X1, Y1, fill=color, outline="")

    def poly(pts, col=None):
        cv.create_polygon([v for p in pts for v in (px(p[0]), py(p[1]))],
                          fill=col or color, outline="")

    if kind == "font":
        # 字形 A：「字体 / 文字样式」最省事也最认得出的符号。
        # 两条 2 格宽的斜腿共用一个平顶（顶点不削尖 —— 16px 下尖端只会糊成一个点），
        # 再加一道横杠。横杠两端各伸进腿里 1 格，光栅后不会在接缝处露底色。
        poly(((7, 1), (9, 1), (3, 15), (1, 15)))       # 左腿
        poly(((7, 1), (9, 1), (15, 15), (13, 15)))     # 右腿
        rect(4, 9, 12, 11)                             # 横杠
    elif kind == "theme":
        # 配色盘：2×2 四个色块（每块 5×5、缝 2）—— 「这套应用用哪套配色」
        for x0 in (2, 9):
            for y0 in (2, 9):
                rect(x0, y0, x0 + 5, y0 + 5)
    elif kind == "toolbar":
        # 工具条 + 条上三枚按钮：画实心条再挖三条底色缝，比「细框 + 小点」更结实。
        # 条高 9 格（4..13）：横条本来就扁，再矮就和旁边 12 格高的图标明显不齐。
        rect(1, 4, 15, 13)
        for x0 in (3, 7, 11):
            rect(x0, 7, x0 + 2, 10, bg)
    elif kind == "tab":
        # 两张标签页叠放：后面那张描边、前面那张实心带**明显凸起**。
        # 凸起是「标签页」唯一的辨识点，矮了（2 格）就退化成「两个错位方块」，
        # 所以留 3 格高、并且比后页窄一截，轮廓才读得出 tab 形。
        ring(6, 2, 14, 11)
        poly(((1, 15), (1, 6), (3, 6), (3, 3), (8, 3), (8, 6), (11, 6), (11, 15)))


class SettingsButton(IconButton):
    """顶栏上的「全局设置」入口（齿轮），排在侧栏开关右侧。

    原来顶栏右侧那枚写着「设置」的圆角按钮挪到了这里，功能不变：
    点开同一张全局设置弹窗（正文字体族 / 正文字号）。
    数据导出导入清空已单独搬到 DataButton（顶栏再往右一枚）。
    """

    GLYPH = dpi.px(16)           # 齿轮细节多，和旁边几枚位图图标同档才看得出齿
    TIP = "全局设置（Ctrl+,）"

    def _paint(self, fg, bg):
        _draw_gear_icon(self, self.SIZE / 2.0, self.SIZE / 2.0, self.GLYPH, fg, bg)


# ---------- 顶栏的「数据管理」图标 ----------
# 图形取自 icon/db.png（后缀是 png，文件内容其实是 SVG 源：1024 viewBox、3 个 <path>、
# 共 10 段闭合子路径 —— 圆柱口的外/内椭圆、两条横纹、柱体、右侧边线，加右下角一枚
# 齿轮的四层）。和 setting.svg 一样按**联合包围盒**归一化（原图 x 7..1018 / y 3..1024，
# 几乎铺满 viewBox），再用 RDP 抽稀：1505 个点压到 203 个。
#
# ⚠️ 这个图标**不能整片填图标色**：齿轮的内轮廓（depth=1）是一整块实心区域，全填会把
# 中心孔封死、齿轮糊成一个黑块（20/22/24/26px 全试过，都糊）。必须按嵌套深度奇偶
# 交替 —— 偶数层填图标色、奇数层填底色挖空，跟 setting.svg 同一套画法。
# 每段的 fg/bg 已经标好，元组顺序就是绘制顺序：外层先画 → 洞后挖 → 更内层再填。
DB_PARTS = (
    ("fg", (
        (0.6231, 0.9641), (0.5626, 0.9429), (0.5000, 0.8964), (0.4962, 0.8698),
        (0.5252, 0.8219), (0.5117, 0.7874), (0.4840, 0.7744), (0.4398, 0.7819),
        (0.4189, 0.7672), (0.4044, 0.6869), (0.4137, 0.6112), (0.4309, 0.5905),
        (0.4784, 0.5903), (0.5047, 0.5747), (0.5149, 0.5413), (0.4912, 0.5008),
        (0.4904, 0.4800), (0.5054, 0.4609), (0.5542, 0.4280), (0.6319, 0.4030),
        (0.6499, 0.4137), (0.6713, 0.4475), (0.7013, 0.4562), (0.7271, 0.4452),
        (0.7552, 0.4061), (0.7866, 0.4060), (0.8481, 0.4324), (0.9009, 0.4726),
        (0.9046, 0.4977), (0.8796, 0.5436), (0.8931, 0.5779), (0.9164, 0.5903),
        (0.9608, 0.5894), (0.9779, 0.6037), (0.9904, 0.6820), (0.9800, 0.7569),
        (0.9633, 0.7754), (0.9185, 0.7753), (0.8898, 0.7918), (0.8801, 0.8234),
        (0.9028, 0.8651), (0.9035, 0.8844), (0.8945, 0.8995), (0.8487, 0.9332),
        (0.7972, 0.9572), (0.7656, 0.9627), (0.7479, 0.9514), (0.7276, 0.9107),
        (0.6950, 0.8998), (0.6645, 0.9163), (0.6452, 0.9566),
    )),
    ("fg", (
        (0.4482, 0.2507), (0.2069, 0.2337), (0.1060, 0.2119), (0.0300, 0.1769),
        (0.0079, 0.1534), (0.0000, 0.1254), (0.0079, 0.0974), (0.0300, 0.0739),
        (0.1060, 0.0388), (0.2069, 0.0171), (0.3117, 0.0055), (0.4482, 0.0000),
        (0.5847, 0.0053), (0.6895, 0.0168), (0.7904, 0.0385), (0.8664, 0.0736),
        (0.8885, 0.0972), (0.8964, 0.1254), (0.8885, 0.1535), (0.8664, 0.1771),
        (0.7905, 0.2122), (0.6365, 0.2407),
    )),
    ("fg", (
        (0.3592, 0.4984), (0.1777, 0.4779), (0.0689, 0.4470), (0.0190, 0.4154),
        (0.0050, 0.3956), (0.0024, 0.3610), (0.0166, 0.3453), (0.0374, 0.3423),
        (0.0554, 0.3533), (0.0664, 0.3766), (0.1168, 0.3983), (0.2299, 0.4226),
        (0.3695, 0.4374), (0.3860, 0.4506), (0.3902, 0.4712), (0.3808, 0.4895),
    )),
    ("fg", (
        (0.3115, 0.7441), (0.1655, 0.7254), (0.0732, 0.6992), (0.0131, 0.6593),
        (0.0034, 0.6425), (0.0024, 0.6117), (0.0166, 0.5961), (0.0375, 0.5930),
        (0.0556, 0.6038), (0.0658, 0.6268), (0.1078, 0.6458), (0.2028, 0.6681),
        (0.3254, 0.6838), (0.3395, 0.6953), (0.3442, 0.7189), (0.3308, 0.7388),
    )),
    ("fg", (
        (0.3793, 0.9998), (0.2422, 0.9903), (0.1176, 0.9656), (0.0494, 0.9377),
        (0.0088, 0.8987), (0.0031, 0.8745), (0.0031, 0.1254), (0.0084, 0.1080),
        (0.0225, 0.0964), (0.0493, 0.0977), (0.0657, 0.1223), (0.0658, 0.8745),
        (0.1074, 0.8957), (0.1805, 0.9146), (0.3925, 0.9391), (0.4103, 0.9550),
        (0.4129, 0.9761), (0.4005, 0.9943),
    )),
    ("fg", (
        (0.8651, 0.3379), (0.8429, 0.3287), (0.8337, 0.3065), (0.8339, 0.1223),
        (0.8429, 0.1032), (0.8651, 0.0940), (0.8872, 0.1032), (0.8964, 0.1254),
        (0.8963, 0.3096), (0.8872, 0.3287),
    )),
    ("bg", (
        (0.5704, 0.8745), (0.6037, 0.8920), (0.6429, 0.8508), (0.6977, 0.8356),
        (0.7525, 0.8508), (0.7917, 0.8920), (0.8300, 0.8701), (0.8175, 0.8304),
        (0.8258, 0.7759), (0.8601, 0.7327), (0.8844, 0.7191), (0.9252, 0.7115),
        (0.9256, 0.6641), (0.9252, 0.6557), (0.8652, 0.6375), (0.8353, 0.6078),
        (0.8174, 0.5585), (0.8293, 0.4984), (0.8214, 0.4929), (0.7842, 0.4739),
        (0.7461, 0.5081), (0.6964, 0.5205), (0.6468, 0.5081), (0.6087, 0.4739),
        (0.5629, 0.4990), (0.5765, 0.5442), (0.5686, 0.5927), (0.5283, 0.6413),
        (0.4676, 0.6588), (0.4670, 0.7042), (0.4676, 0.7152), (0.5010, 0.7175),
        (0.5322, 0.7298), (0.5713, 0.7690), (0.5861, 0.8224),
    )),
    ("bg", (
        (0.0646, 0.1254), (0.1253, 0.1522), (0.2167, 0.1717), (0.4482, 0.1881),
        (0.6794, 0.1717), (0.7706, 0.1522), (0.8312, 0.1254), (0.7705, 0.0983),
        (0.6792, 0.0788), (0.4482, 0.0627), (0.2170, 0.0788), (0.1254, 0.0983),
    )),
    ("fg", (
        (0.6964, 0.7811), (0.6418, 0.7645), (0.6204, 0.7451), (0.5985, 0.6923),
        (0.5999, 0.6634), (0.6268, 0.6131), (0.6500, 0.5959), (0.7061, 0.5847),
        (0.7341, 0.5917), (0.7783, 0.6280), (0.7906, 0.6541), (0.7944, 0.6923),
        (0.7874, 0.7203), (0.7660, 0.7522), (0.7341, 0.7736),
    )),
    ("bg", (
        (0.6964, 0.6469), (0.6710, 0.6573), (0.6601, 0.6827), (0.6659, 0.7030),
        (0.6924, 0.7194), (0.7133, 0.7155), (0.7320, 0.6906), (0.7246, 0.6603),
    )),
)


def _draw_db_icon(cv, cx, cy, size, color, bg):
    """画「数据库圆柱 + 齿轮」：按 DB_PARTS 的顺序逐段填充，bg 段用底色挖空。

    这是 `icon/db.png` 缺失时的回退画法（裁掉 icon/ 的最小包里还能看）。
    """
    s = float(size)
    left, top = cx - s / 2.0, cy - s / 2.0
    for kind, points in DB_PARTS:
        flat = []
        for nx, ny in points:
            flat.extend((left + nx * s, top + ny * s))
        cv.create_polygon(flat, fill=(color if kind == "fg" else bg), outline="")


# ---------- 图标位图：只从「预渲染好的精确尺寸」取，运行时零缩放 ----------
# target/gen_icons.py 会把每张源图分别渲染成 icon/px/<size>/<name>：线条图先裁掉
# 四周透明边，缩放后按 alpha 补一次墨（不然细线会被平均成灰雾），需要染色的四张
# 输出成白色掩膜。运行时这里只做两件事 —— 按名字取对应尺寸、按皮肤上色。
#
# 为什么不在运行时缩：Tk 只有**整数倍最近邻**采样，200px 的图缩到 16px 会丢掉
# 绝大部分像素，一条 16px 宽的线缩完只剩 1.2px，再被最近邻采样一次就断成灰点。
# PIL 虽然有 LANCZOS，但源码运行不保证装得上 Pillow（文档承诺零依赖）。预渲染把
# 「质量」固定在生成那一步，运行环境有没有 PIL 都是同一张图。
_PHOTO_CACHE = {}


def _icon_asset(name, size):
    """返回 (路径, 是否精确尺寸)。精确尺寸的图可以直接贴，不需要任何采样。"""
    exact = os.path.join(_RES, "px", str(int(size)), name)
    if os.path.exists(exact):
        return exact, True
    return os.path.join(_RES, name), False


def _tint_photo(master, path, size, color, exact, by_height=False):
    """把图按 alpha 刷成 color。有 PIL 用 PIL（半透明边保留），没有就逐点 put。

    `by_height` 只影响**回退路径**（没有预渲染成品时）：note.png 是一张宽图，
    按「最长边」缩会把铅笔压扁，得按高度对齐。精确尺寸的成品不做任何缩放。
    """
    try:
        from PIL import Image, ImageTk
        im = Image.open(path).convert("RGBA")
        if not exact:
            bb = im.split()[3].getbbox()
            if bb:
                im = im.crop(bb)
            w0, h0 = im.size
            # 高度对齐 size、宽度等比：宽图（note）不会被拉成正方形
            im = im.resize((max(1, int(round(size * w0 / float(h0)))),
                            max(1, int(size))),
                           getattr(Image, "LANCZOS", Image.BICUBIC))
        r, g, b = _hex_rgb(color)
        out = Image.new("RGBA", im.size, (r, g, b, 255))
        out.putalpha(im.split()[3])       # alpha 原样保留 → 抗锯齿边还在
        return ImageTk.PhotoImage(out, master=master)
    except Exception:
        pass
    try:
        img = tk.PhotoImage(file=path, master=master)
    except Exception:
        return None
    if not exact:
        ref = img.height() if by_height else max(img.width(), img.height())
        k = max(1, int(round(ref / float(size))))
        if k > 1:
            img = img.subsample(k)
    # 没有 PIL 时的染色：逐点 put（半透明边会被压实，只差一圈像素）
    try:
        for y in range(img.height()):
            for x in range(img.width()):
                if img.transparency_get(x, y):
                    continue
                img.put(color, to=(x, y, x + 1, y + 1))
    except Exception:
        pass
    return img


def _icon_photo(master, name, size, color=None, by_height=False):
    """取 `icon/<name>` 在 size 这一档的位图（带缓存，全程序共用同一张）。

    · `icon/px/<size>/<name>` 在 → 原样加载，零缩放（没装 Pillow 也清晰）；
    · 不在（裁过的最小包 / 没跑生成脚本）→ 退回原图缩放，行为同改版前；
    · color 不为 None → 按 alpha 染色（create / github / issue / note 走这条）；
    · by_height=True → 回退路径按高度对齐 size（note.png 是宽图）。
    """
    size = int(size)
    key = (name, size, color, bool(by_height))
    hit = _PHOTO_CACHE.get(key)
    if hit is not None:
        return hit
    path, exact = _icon_asset(name, size)
    if not os.path.exists(path):
        return None
    if color:
        img = _tint_photo(master, path, size, color, exact, by_height)
    else:
        try:
            img = tk.PhotoImage(file=path, master=master)
        except Exception:
            return None
        if not exact:
            ref = img.height() if by_height else max(img.width(), img.height())
            k = max(1, int(round(ref / float(size))))
            img = img.subsample(k) if k > 1 else img
    if img is not None:
        _PHOTO_CACHE[key] = img       # 必须持引用：PhotoImage 被回收后图会白掉
    return img


def _png_icon_photo(master, name, size=dpi.px(25)):
    """`icon/<name>` 的原色版本（db / about / notice 这类颜色已经烤在图里的）。"""
    return _icon_photo(master, name, size)


def _note_icon_photo(master, height=dpi.px(14), color=None):
    """标签页左侧那支斜铅笔：按**高度**取档（icon/px/12/note.png 是 12 高的横图）。"""
    return _icon_photo(master, "note.png", height, color, by_height=True)


class DataButton(IconButton):
    """顶栏上的「数据管理」入口（数据库 + 齿轮），排在全局设置右侧。

    原来埋在全局设置弹窗下半部分的三件事搬到了这里：导出备份 / 导入数据 /
    清空所有数据。设置弹窗因此只剩「正文长什么样」这类外观项。

    图形用 `icon/px/16/db.png`（预渲染的精确尺寸），不走自绘；`DB_PARTS` 只在
    图片缺失时兜底。
    """

    # 画布 18 与其它四枚图标一致（顶栏 30 高，上下各留 6px）—— 这一排的高度
    # 必须齐，否则三枚按钮的视觉外框会一个比一个大。
    SIZE = dpi.px(18)
    GLYPH = dpi.px(16)           # 偶数：18 画布里 (18-16)/2 = 1，图像正好落在整像素上
    TIP = "数据管理"

    def _paint(self, fg, bg):
        img = _icon_photo(self, "db.png", self.GLYPH, fg)
        if img is None:
            _draw_db_icon(self, self.SIZE / 2.0, self.SIZE / 2.0,
                          self.GLYPH - 1, fg, bg)
            return
        self.create_image(self.SIZE // 2, self.SIZE // 2, image=img)


def _draw_info_icon(cv, cx, cy, size, color):
    """画「圆圈 + i」：`icon/about.png` 缺失时的回退画法。

    圆环用 2px 描边（1px 在 16px 上会散成灰虚线圈）、i 的点与干各一块实心矩形，
    全部落在整像素上。比不上位图圆润，但形态认得出来。
    """
    r = max(5, int(size) // 2)
    cv.create_oval(cx - r, cy - r, cx + r - 1, cy + r - 1,
                   outline=color, width=2)
    cv.create_rectangle(cx, cy - int(r * 0.62), cx + 1, cy - int(r * 0.40),
                        fill=color, outline="")
    cv.create_rectangle(cx, cy - int(r * 0.20), cx + 1, cy + int(r * 0.56),
                        fill=color, outline="")


class AboutButton(IconButton):
    """顶栏上的「关于」入口（圆圈 + i），排在数据管理右侧。

    原来躺在全局设置弹窗最底下的「关于」搬到了这里：设置里只剩「正文长什么样」，
    「这是谁做的、代码在哪」和导出/清空一样都属于「关于这个应用」的事，
    各给一枚图标，点开就是一张只读的小窗。

    图形用 `icon/px/16/about.png`（预渲染的精确尺寸），`_draw_info_icon` 只在
    图片缺失时兜底。
    """

    SIZE = dpi.px(18)            # 与其它四枚同一档画布，这一排的外框才对得齐
    # 生成脚本已经按 alpha 包围盒裁掉了四周 12px 透明边，所以这里直接要 16 ——
    # 不再是改版前那种「图 18px、圆环其实只有 15.8px」的凑法。
    GLYPH = dpi.px(16)
    TIP = "关于"

    def _paint(self, fg, bg):
        img = _icon_photo(self, "about.png", self.GLYPH, fg)
        if img is None:
            _draw_info_icon(self, self.SIZE // 2, self.SIZE // 2,
                            self.GLYPH, fg)
            return
        self.create_image(self.SIZE // 2, self.SIZE // 2, image=img)


def _tinted_png_photo(master, name, size, color):
    """`icon/<name>` 的染色版本 —— 直接取预渲染好的 `icon/px/<size>/<name>`。

    这几张（create / github / issue / note）在 icon/px 下存的是**白色掩膜**：
    形状和抗锯齿边都在 alpha 里，运行时只换 RGB，不再重采样。
    """
    return _icon_photo(master, name, size, color)



def _draw_create_icon(cv, cx, cy, size, color):
    """画一个「+」：`icon/create.png` 缺失时的回退（新建 = 记一笔）。"""
    r = max(4, int(size) // 2)
    t = max(1, int(round(size / 8.0)))   # 笔画粗细（15 → 2px；//8 会退成 1px）
    cv.create_rectangle(cx - t, cy - r, cx + t, cy + r, fill=color, outline="")
    cv.create_rectangle(cx - r, cy - t, cx + r, cy + t, fill=color, outline="")


class CreateButton(IconButton):
    """顶栏上的「新建笔记」入口（记事本 + 铅笔），排在关于右侧。

    原来它是顶栏最右端一枚写着「新建笔记」的实心胶囊 —— 用文字按钮当主操作，
    在一条 30px 高的图标带里显得最重、也最占地方。换成图标后右侧整块让给
    窗口键，主操作和设置/数据/关于这排「看一眼笔记」的入口并排，靠**颜色**分层：
    别的图标是皮肤的 soft 灰，这枚是 accent，一眼就能认出「这是要动手的那一枚」。

    图形取自 `icon/create.png`（黑色线条图），运行时染成 accent —— 位图颜色改不了，
    所以在 `_tinted_png_photo` 里按 alpha 重新上色。染色结果按「图 + 颜色」缓存在
    `_icon_photo` 里，换肤后颜色一变，下一次重绘自然会拿到新的一张。
    """

    SIZE = dpi.px(18)
    GLYPH = dpi.px(16)           # 与齿轮 / 数据库同档；icon/px/16/create.png 是预渲染好的
    TIP = "新建笔记（Ctrl+N）"

    def _paint(self, fg, bg):
        color = self._pal.get("accent", "#2f7cf6")
        img = _tinted_png_photo(self, "create.png", self.GLYPH, color)
        if img is None:
            _draw_create_icon(self, self.SIZE // 2, self.SIZE // 2,
                              self.GLYPH, color)
            return
        self.create_image(self.SIZE // 2, self.SIZE // 2, image=img)


# 自绘标题栏（= 顶栏）的高度。窗口键、应用图标、标题基线都按它对齐，
# 改这一处就够了（win_frame 的客户区重算不关心高度，它只负责去掉系统那条）。
#
# 30 = Windows 10/11 在 100% 缩放下的标准标题栏高度，窗口键、应用图标、标题基线
# 都按它对齐。这一排图标缩到画布 18 / 图形 16（字号 9pt bold）后，在 30 里上下
# 各留 6px，整条比原来松一档 —— 高度不跟着缩，缩的只是里面的东西。
TOP_BAR_H = 30

# 编辑器左侧行号栏固定预留几位数字。4 位 = 9999 行，够任何一条笔记用；
# 关键是**不跟当前内容走**，见 gutter_width()。
GUTTER_DIGITS = 4


def gutter_width(unit, total=1, digits=GUTTER_DIGITS):
    """行号栏宽度（像素）：只由字号和**固定位数预算**决定，不跟当前内容走。

    为什么不用「当前总行数的位数」：9→10、99→100、999→1000 每跨一档，整列就变宽
    一截，正文跟着往右挪一次 —— 读到一半被推一下，比常年多留几像素烦得多。
    栏宽一稳，行号也就成了「不动的尺子」。

    `unit` 是数字「0」的像素宽（由行号字体量出来），`total` 是当前总行数。
    只有真超过预算位数才让位：再不让就得把数字裁掉，显示一个错的行号比挪一下更糟。
    """
    d = max(int(digits), len(str(max(1, int(total)))))
    return unit * d + dpi.px(13)


def gutter_slots(v0, unit, height):
    """等距行号「该画哪几格」：返回 (k0, k1) 闭区间，数字 = k + 1。

    行号是一把**刻度尺**：一格的高度固定是正文行高 `unit`，`v0` 是第 1 格顶端
    在视口里的 y（往下滚就是负数）。只要格子和视口有交集就算 —— 上沿滚出去
    半格的那一格、下沿补到画面之外的那几格都算，因此：

    * 空行、正文末尾以下的空白区都有号（一直填满画面，不会数到一半就断）；
    * 数字永远等距，敲字 / 插代码块 / 换字号都不会让某一段挤紧或拉开。

    ⚠️ 别改回「按每行的 dlineinfo 逐行贴齐」：标题、内嵌代码块的行高与正文
    行高差很多，贴齐的结果是 63 / 29 / 67 / 18×9 这种毫无规律的间距。
    """
    unit = max(1, int(unit))
    v0 = int(v0)
    k0 = max(0, int(-v0 // unit))
    k1 = max(k0, int((int(height) - v0 - 1) // unit))
    return k0, k1


def gutter_hit_at(hits, y):
    """行号栏 y 处是哪个号：(目标行, 图元 id)；落在所有区间之外给 (None, None)。

    ⚠️ 必须按**区间**查，不能按「显示号 → 图元」的字典查：号是标尺数出来的，
    一个号未必等于一个逻辑行（代码块那一格占两百多像素，底下几行的号就差了
    八九个），按号查会把点击目标算到别的行去。
    """
    for top, bottom, line, item in hits or ():
        if top <= y < bottom:
            return line, item
    return None, None


class WindowButton(tk.Canvas):
    """窗口键：最小化 / 最大化 / 关闭（挂在自绘标题栏的最右端）。

    系统标题栏被顶栏顶掉之后（见 win_frame.py），这三枚得自己画。刻意照 Windows
    的规矩来：36×30 的方片贴在窗口右上角，常态不画底，悬停浮出浅底、按下再深一档；
    关闭键悬停是红底白叉 —— 这条约定用户早成了肌肉记忆，别跟着皮肤换色。

    图形只用 1px 细线且落在整像素上：Canvas 不做抗锯齿，1.5px 的线会糊成两道灰边。
    最大化之后中间那枚要换成「还原」的双叠框，由 set_maximized() 切换。
    """

    W, H = dpi.px(36), TOP_BAR_H
    CLOSE_BG = "#e81123"          # 关闭键悬停：Windows 的红，不参与换肤
    CLOSE_BG_PRESS = "#c50f1f"

    def __init__(self, master, pal, kind, command=None, base=None):
        self._pal = dict(pal)
        self._kind = kind                      # "min" / "max" / "close"
        self._command = command
        self._base = base if base is not None else pal.get("top", "#ececec")
        self._state = "normal"
        self._maximized = False
        tk.Canvas.__init__(self, master, width=self.W, height=self.H, bd=0,
                           highlightthickness=0, cursor="arrow", bg=self._base)
        self.bind_events()
        self.redraw()

    # ---------------------------------------------------------------- 绘制
    def redraw(self):
        self.delete("all")
        cx, cy = self.W // 2, self.H // 2
        p = self._pal
        if self._state != "normal":
            if self._kind == "close":
                fill = self.CLOSE_BG_PRESS if self._state == "press" else self.CLOSE_BG
                fg = "#ffffff"
            else:
                fill = _press_bg(self._base) if self._state == "press" else _hover_bg(self._base)
                fg = p.get("text", "#1f1f24")
            self.create_rectangle(0, 0, self.W, self.H, fill=fill, outline="")
        else:
            fg = p.get("text", "#1f1f24")

        if self._kind == "min":
            # 最小化：一条居中的横线（Windows 上它比正中略低 1px）
            self.create_line(cx - dpi.px(5), cy, cx + dpi.px(5), cy, fill=fg,
                             width=dpi.px(1))
        elif self._kind == "close":
            self.create_line(cx - dpi.px(4), cy - dpi.px(4),
                             cx + dpi.px(5), cy + dpi.px(5), fill=fg,
                             width=dpi.px(1))
            self.create_line(cx - dpi.px(4), cy + dpi.px(5),
                             cx + dpi.px(5), cy - dpi.px(4), fill=fg,
                             width=dpi.px(1))
        elif self._maximized:
            # 还原：两个错开的方框（前框右下少一条边，像 Windows 的还原键）
            self.create_rectangle(cx - dpi.px(5), cy - dpi.px(2),
                                  cx + dpi.px(3), cy + dpi.px(6),
                                  outline=fg, width=dpi.px(1))
            self.create_line(cx - dpi.px(2), cy - dpi.px(6),
                             cx + dpi.px(6), cy - dpi.px(6), fill=fg,
                             width=dpi.px(1))
            self.create_line(cx + dpi.px(6), cy - dpi.px(6),
                             cx + dpi.px(6), cy + dpi.px(1), fill=fg,
                             width=dpi.px(1))
        else:
            self.create_rectangle(cx - dpi.px(5), cy - dpi.px(5),
                                  cx + dpi.px(5), cy + dpi.px(5),
                                  outline=fg, width=dpi.px(1))

    # ---------------------------------------------------------------- 状态
    def set_maximized(self, flag):
        flag = bool(flag)
        if flag == self._maximized:
            return
        self._maximized = flag
        self.redraw()

    def _set_state(self, st):
        if self._state == st:
            return
        self._state = st
        self.redraw()

    def _on_release(self, e):
        inside = (0 <= e.x < self.W and 0 <= e.y < self.H)
        self._set_state("hover" if inside else "normal")
        if inside and self._command:
            self._command()

    def bind_events(self):
        self.bind("<Enter>", lambda e: self._set_state("hover"))
        self.bind("<Leave>", lambda e: self._set_state("normal"))
        self.bind("<ButtonPress-1>", lambda e: self._set_state("press"))
        self.bind("<ButtonRelease-1>", self._on_release)

    def apply_theme(self, pal, base=None):
        self._pal = dict(pal)
        if base is not None:
            self._base = base
        self.configure(bg=self._base)
        self.redraw()


class ScrollJumpNav(tk.Canvas):
    """编辑区右侧居中的浮动小导航：上下两枚双箭尖，点一下回顶部 / 回底部。

    只在这篇正文确实溢出（出现滚动条）时出现，已经到顶/到底的那一头会变淡、点了不动。
    视觉与工具栏滑动箭头同族：圆角片 + 双箭尖 + 悬停/按下三态，
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
            _round_rect_points(0.5, 0.5, JUMP_W - 0.5, JUMP_H - 0.5,
                               JUMP_W / 2.0, dpi.px(8)),
            fill=_mix_hex(self._base, self._pal["line"], 0.30),
            outline=_mix_hex(self._base, self._pal["line"], 0.55),
            width=dpi.px(1))
        # 两枚图标之间的细分隔线
        self.create_line(dpi.px(7), JUMP_H / 2.0, JUMP_W - dpi.px(7), JUMP_H / 2.0,
                         fill=_mix_hex(self._base, self._pal["line"], 0.5),
                         width=dpi.px(1))
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
                _round_rect_points(cx - dpi.px(11), cy - dpi.px(11),
                                   cx + dpi.px(11), cy + dpi.px(11),
                                   dpi.px(8), 6),
                fill=fill, outline=outline, width=dpi.px(1))
        up = (part == "top")
        # 上下箭尖自成一档：比左右滑动箭头更宽、更平，小尺寸下也看得清朝向
        a, b = dpi.fx(4.6), dpi.fx(3.1)
        gap, lw = dpi.fx(3.2), dpi.fx(2.0)
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

    def __init__(self, master, bg, fg, font, gap=dpi.px(48)):
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


def badge_text(count, cap=99):
    """数量徽章上显示的文字：超过 cap 一律写「99+」，否则原样。

    抽成模块级纯函数是为了能测：三类边界（cap-1 / cap / cap+1）和四位数，
    建 GUI 才能验的话这几条永远没人跑。
    """
    return "%d+" % cap if int(count) > cap else str(int(count))


def badge_size(text_w, linespace, pad_x=6, grow=2):
    """徽章画布尺寸：`(宽, 高)`。

    高度 = 字体行高 + grow（数字四周留一点白，不顶到圆边）；
    宽度**至少等于高度**，所以一两位数字是正圆，三位「99+」才长成胶囊。
    形状规则只写在这一个地方 —— 首建与改数字时都走它，否则两处各算一遍，
    迟早出现「新建时是圆、删到 9 之后变成扁的」。
    """
    h = int(linespace + grow)
    return max(h, int(text_w + pad_x)), h


class CountBadge(tk.Canvas):
    """文件夹行右侧的条数徽章：数字装在一枚小圆里，位数多了自动长成胶囊。

    为什么不用 tk.Label：Tk 的控件全是方的，画不出圆底；而这枚数字要的就是
    「一个小圆点」。Canvas 自绘才有圆。

    形状规则（宽高全由字号推，不写死像素，换字号/换 DPI 都跟着走）：
      * 一位、两位数字：宽度不够撑满就取圆的直径 → 正圆；
      * 三位（「99+」）：左右各接一段直边 → 胶囊。
    超过 99 一律显示「99+」：三位以上的精确个位没有信息价值，还会把文件夹名
    往左挤掉一截。

    底色**从行底色推**（`_mix_hex(bg, 墨色, 0.14)`）而不是固定色：这一行会被
    悬停 / 选中整行换底，徽章若用死色，选中行上就浮出一块别色的圆 —— 与图标
    挖孔、箭头底片踩的是同一个坑。`refresh_bg()` 由 `_row_hover` 调用。
    """

    FONT_SIZE = 10           # 比文件夹名（12）小两档：数字是辅助信息，不跟名字争
    PAD_X = dpi.px(6)        # 文字左右合计留白
    CAP = 99                 # 超过它就显示「99+」

    def __init__(self, master, count=0, bg="#e8f0e9", fg="#76767e",
                 ink="#1f1f24", font=None):
        self._fg = fg
        self._ink = ink
        self._count = int(count)
        if font is None:
            font = tkfont.Font(root=master, family=FONT[0], size=self.FONT_SIZE)
        elif not isinstance(font, tkfont.Font):
            font = tkfont.Font(root=master, family=font[0], size=font[1])
        self._font = font
        # 高度 = 字体行高 + 2：圆略大于字身，数字四周都有留白，不会顶到圆边。
        # 宽度至少要等于高度（= 圆），文字更宽时才往外长。
        self._badge_w, self._badge_h = badge_size(
            self._font.measure(self.text()), self._font.metrics("linespace"),
            self.PAD_X, dpi.px(2))
        tk.Canvas.__init__(self, master, width=self._badge_w, height=self._badge_h,
                           bd=0, highlightthickness=0, bg=bg)
        self._paint()

    # ---------- 数据 ----------
    def text(self):
        return badge_text(self._count, self.CAP)

    def set_count(self, n):
        """改数字（新建/删除/移动笔记时由 _refresh_folder_counts 调用）。"""
        n = int(n)
        if n == self._count:
            return
        self._count = n
        self._resize()
        self._paint()

    def set_style(self, fg=None):
        if fg is not None and fg != self._fg:
            self._fg = fg
            self._paint()

    def refresh_bg(self):
        """行底色变了（悬停 / 选中）→ 只重画，尺寸不变。"""
        self._paint()

    # ---------- 画 ----------
    def _resize(self):
        w, h = badge_size(self._font.measure(self.text()),
                          self._font.metrics("linespace"), self.PAD_X, dpi.px(2))
        if (w, h) != (self._badge_w, self._badge_h):
            self._badge_w, self._badge_h = w, h
            self.configure(width=w, height=h)

    def _paint(self):
        """圆底 + 数字。全整数坐标：Tk 的填充是半开区间，`0..h` 正好 h 像素。

        ⚠️ 不用 `width=` 描边画圆环（会得到粗细不匀的一圈台阶），这里是**实心填充**，
        边缘只有一圈像素台阶，没有半调、也没有断口 —— 之前「毛刺」是细描边 + 非整数
        坐标 + 直径只有 9px 三者叠加，这里一个都不占。
        """
        self.delete("all")
        w, h = self._badge_w, self._badge_h
        pill = _mix_hex(self["bg"], self._ink, 0.14)
        if w > h:
            # 胶囊 = 左右两个半圆 + 中间一块矩形（三者重叠，不会露缝）
            self.create_oval(0, 0, h, h, fill=pill, outline="")
            self.create_oval(w - h, 0, w, h, fill=pill, outline="")
            self.create_rectangle(h / 2.0, 0, w - h / 2.0, h, fill=pill, outline="")
        else:
            self.create_oval(0, 0, w, h, fill=pill, outline="")
        # +0.5：anchor="center" 居中用的是「行盒」（ascent+descent），而数字没有
        # 降部，行盒底部那一条空白会把字形整体推到偏上 1px。半像素补齐后
        # 上下留白相等（实测 100% 与 166% 下都是上 1 / 下 1）。
        self.create_text(w / 2.0, h / 2.0 + 0.5, text=self.text(), fill=self._fg,
                         font=self._font, anchor="center")


# 自绘右键菜单的尺寸与配色
MENU_ROW_H = dpi.px(30)         # 单项高度
MENU_SEP_H = dpi.px(9)          # 分隔线占高
MENU_PAD_X = dpi.px(14)         # 卡片内左右留白
MENU_GUTTER = dpi.px(22)        # 有图标时的文字左缩进 = 16px 图标 + 6px 间隙（图标恰好占满这 22px，
                                # 菜单宽度一个像素都不用加）
MENU_GUTTER_MIN = dpi.px(6)     # 纯文字菜单的缩进。没有图标就别留那一列空位——1px 到 22px 的
                                # 空缩进在实机里就是每行左边一条死白。
# 勾选列：菜单里有开关项时，在最右端留出这一条给对勾。勾 14px（比左边 16px 的
# 图标小一档、比 11pt 文字的 x 高大一点），右端点离菜单右缘 12px —— 与左侧 14px
# 的留白基本对称，勾不会像贴在边上的一个点。
MENU_CHECK_SIZE = dpi.px(14)
MENU_CHECK_W = dpi.px(18)
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
    # indent 是**空格个数**，不是像素 —— 不要过 dpi.px()，否则高 DPI 下缩进莫名变深
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
    if lang in ("json", "sql"):
        # 中文输入法留下的全角逗号 / 中文引号会让 JSON 直接解析失败、让 SQL 断错行，
        # 先归一化；字符串与注释内部不动（见 punct 的文件头）
        code = punct.normalize(code, lang)
    if lang == "json":
        return _format_json(code)
    if lang in ("html", "xml"):
        return _format_markup(code)
    if lang == "python":
        return _fmt_python(code)
    if lang == "sql":
        # SQL 不能只调缩进：日志里抄出来的语句常常一行塞三个 AND，
        # 得按子句关键词重新断行（缩进这一步由它自己决定，见 sql_fmt 的文件头）
        return sql_fmt.format_sql(code)
    if lang in ("java", "bash"):
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
        # {"label","command","danger","enabled","icon","checked"}；label 为 None 表示分隔线
        self._items = []
        self._hit = []       # 可点击行 (y0, y1, index)
        self._seps = []      # 分隔线纵坐标
        self._hover = -1
        self._win = None
        self._canvas = None
        self._width = 0
        self._height = 0
        self._gutter = MENU_GUTTER_MIN
        try:
            self._font = tkfont.Font(family=FONT[0], size=FONT[1] - 1)
        except Exception:
            self._font = None

    # ---------- 组装 ----------
    def add(self, label, command=None, danger=False, enabled=True, icon=None,
            checked=False):
        """icon 取 _draw_icon 的名字；给了图标的菜单会自动把左侧缩进撑到 22px。

        缩进按「整张菜单有没有图标」统一决定，不是逐行：混排时没图标的那几行也
        留同样的空位，否则文字起点参差不齐。所以图标要么配齐、要么一个都别给。

        checked 是「这是一个开关 / 当前生效的是这一项」——**别把 ✓ 拼进 label**：
        字体勾在中文行里对不齐（见 _draw_icon 的 check），而且拼进去的一瞬间
        菜单会跟着变宽、整个卡片左右跳一下。勾走右侧固定列，宽度一次算好。
        """
        self._items.append({"label": label, "command": command,
                            "danger": danger, "enabled": enabled, "icon": icon,
                            "checked": bool(checked)})
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
        has_icon = any(it.get("icon") for it in self._items)
        self._gutter = MENU_GUTTER if has_icon else MENU_GUTTER_MIN
        self._width = MENU_PAD_X * 2 + self._gutter + text_w
        # 勾列按「整张菜单」加，不逐行加：某一行带勾、别的行不带时宽度若跟着变，
        # 菜单右缘就成了锯齿。多数菜单没有勾，所以只在该用的时候才多这 20px。
        if any(it.get("checked") for it in self._items if it["label"] is not None):
            self._width += MENU_CHECK_W
        y = dpi.px(6)
        self._hit = []
        self._seps = []
        for idx, it in enumerate(self._items):
            if it["label"] is None:
                self._seps.append(y + MENU_SEP_H // 2)
                y += MENU_SEP_H
            else:
                self._hit.append((y, y + MENU_ROW_H, idx))
                y += MENU_ROW_H
        self._height = y + dpi.px(6)

    # ---------- 绘制 ----------
    def _render(self):
        c = self._canvas
        p = self.pal
        w, h = self._width, self._height
        line = p.get("line", "#cfcfd6")
        c.delete("all")
        # 卡片底：直角矩形 + 1px 描边，与无边框窗口严丝合缝（圆角会露出窗口直角底色）
        c.create_rectangle(dpi.px(1), dpi.px(1), w - dpi.px(1), h - dpi.px(1),
                           fill=p.get("editor", "#ffffff"), outline=line, width=dpi.px(1))
        for y0, y1, idx in self._hit:
            it = self._items[idx]
            if idx == self._hover and it["enabled"]:
                c.create_rectangle(dpi.px(4), y0 + dpi.px(1), w - dpi.px(4),
                                   y1 - dpi.px(1),
                                   fill=(MENU_DANGER if it["danger"] else p.get("sel", "#cfe0fb")),
                                   outline="")
                fg = "#ffffff" if it["danger"] else p.get("selfg", p.get("text", "#1f1f24"))
            elif not it["enabled"]:
                fg = p.get("soft", "#76767e")
            elif it["danger"]:
                fg = MENU_DANGER
            else:
                fg = p.get("text", "#1f1f24")
            c.create_text(MENU_PAD_X + self._gutter, (y0 + y1) // 2,
                          text=it["label"], anchor="w",
                          font=(FONT[0], FONT[1] - 1), fill=fg)
            # 图标颜色直接吃这一行的 fg：悬停是实底高亮（字色换成 selfg）、禁用走 soft、
            # 危险项标红且悬停时反白。图标若固定成 pal["text"]，悬停那一行就会糊在底色里。
            if it.get("icon"):
                _draw_icon(c, it["icon"], MENU_PAD_X + ICON_SIZE / 2.0,
                           (y0 + y1) // 2.0, ICON_SIZE, fg)
            if it.get("checked"):
                # 勾也用强调色：未悬停时它是整行唯一一处彩色，扫一眼就知道「现在
                # 是这一项」；悬停时底色是 sel（淡蓝），强调色压在上面会糊，改跟 fg。
                # 线宽比别的图标粗 0.2px：勾只有一笔，1.7px 在 14px 的格子里偏单薄，
                # 1x 下会显得若即若离，和右边一整块实底高亮不成比例。
                chk_fg = fg if (idx == self._hover or not it["enabled"]) else \
                    p.get("accent", fg)
                _draw_icon(c, "check",
                           w - MENU_PAD_X - MENU_CHECK_SIZE / 2.0 + dpi.px(2),
                           (y0 + y1) // 2.0, MENU_CHECK_SIZE, chk_fg,
                           stroke=dpi.fx(1.9))
        # 分隔线：左右缩进，比通栏更克制
        for y in self._seps:
            c.create_line(MENU_PAD_X, y, w - MENU_PAD_X, y, fill=line)

    # ---------- 弹出 / 关闭 ----------
    def popup(self, x, y):
        """在屏幕坐标 (x, y) 弹出菜单；贴近边缘时自动内收，保证整框可见。

        内收的边界取**鼠标所在那块显示器的工作区**，不是 Tk 的屏幕尺寸：
        `winfo_screenwidth()/screenheight()` 只报主屏，副屏坐标又常是负数，
        多屏机器上在副屏右键，菜单会被夹到 x=0，直接跨到主屏最左边去。
        """
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
        area = win_frame.work_area_at(x, y, fallback=win)
        if area is None:                      # 非 Windows 又拿不到窗口时按主屏算
            area = (0, 0, win.winfo_screenwidth(), win.winfo_screenheight())
        px, py = win_frame.clamp_pos(area, x, y, self._width, self._height)
        # 必须带 "+" 前缀：负坐标（副屏在主屏左侧/上方时）写成 "+-1000"，
        # 少了这个加号，Tk 会把 "-1000" 当成「距右边缘 1000」。
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


class SlimScrollbar(tk.Canvas):
    """列表右侧的细滚动条（自绘）：只在内容超出时出现，可拖、可点轨道、可滚轮。

    为什么不用 tk.Scrollbar：系统滚动条是直角色块 + 两端箭头，和本项目自绘的圆角
    控件不搭，还会占掉 17px 把两列挤窄。这里只用 8px，正好落在卡片左右留白
    （CARD_PADX）里，拇指是圆角小条，颜色随皮肤由面板底色向次级文字色混合而来。
    """

    W = dpi.px(8)          # 占位宽度（画 6px 拇指，两侧各留 1px，不压到卡片）
    INSET = dpi.px(4)      # 上下留白，拇指不贴面板边缘
    THUMB_MIN = dpi.px(26)

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


class ScrollBody(tk.Frame):
    """对话框里的可滚动内容区：装得下就不滚，装不下才出现右侧那根细滚动条。

    为什么不直接用 `ScrollPanel`：那个是给侧栏/列表写的，两处差得不小。

    1. **它用 `bind_all` 兜底滚轮**。`bind_all` 在 Tk 里是**替换**不是追加，
       弹窗里再造一个就会把侧栏的兜底抢走（关掉弹窗后侧栏滚不动）。这里改由
       宿主 Toplevel 转发（见 `bind_wheel`）—— 弹窗带 grab，滚轮本来就只落在它身上。
    2. **高度由内容决定**。侧栏是铺满的，弹窗不是：内容矮时画布高度得收到内容高，
       下面不能留一片空白；内容超高时才由 `fit()` 钉到可用高，多出来的滚动看。

    为什么需要它：全局设置窗内容原本 735px，加 31px 系统标题栏 = 766px，而 768 高的屏
    扣掉任务栏只剩 738（副屏）—— 底缘连「确定 / 取消」一起被推出屏幕。
    压间距只能省下 30px 左右，换台 125% 缩放的笔记本就彻底救不回来，所以改成能滚。
    现在设置窗更进了一步：整窗固定到「数据管理」窗的高度（见 app_ui._panel_height），
    内容一律滚动查看，矮屏再由 fit() 按屏幕可用高压一道。
    """

    def __init__(self, parent, bg, palette):
        tk.Frame.__init__(self, parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0,
                                yscrollincrement=dpi.px(20))
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._iid = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        # 细滚动条自己 place 在右侧、内容没溢出时自动隐藏（见 SlimScrollbar.sync）
        self.sb = SlimScrollbar(self, bg, palette, self.canvas)
        self.canvas.configure(yscrollcommand=self._on_yscroll)
        self.inner.bind("<Configure>", lambda e: self._sync())
        self.canvas.bind("<Configure>", self._on_cfg)

    # ---------- 对外 ----------
    def fit(self, avail_h):
        """把可视区高度定成 min(内容高, avail_h)，返回最终占用的高度。

        必须在 `wm geometry` **之前**调：弹窗高度是按 reqheight 算的，这里改了
        画布高度，reqheight 才会跟着变（`_center_dialog` 的 fit_body 就是这么用的）。
        """
        self.update_idletasks()
        h = self.content_height()
        h = max(dpi.px(120), min(h, int(avail_h)))
        w = max(self.canvas.winfo_width(), self.content_width(), dpi.px(320))
        self.canvas.configure(width=w, height=h)
        self.update_idletasks()
        self._sync()
        return h

    def bind_wheel(self, top):
        """让整个弹窗（含所有子控件）的滚轮都滚这块内容。

        绑在 Toplevel 上而不是逐个控件：Tk 的事件**不向父控件冒泡**，但顶层窗口
        始终在 bindtags 里（[自己, 类, 顶层, all]），子控件上的滚轮一定会经过它。
        返回 "break" 是为了拦住 "all" 那一级 —— 侧栏的 bind_all 兜底就挂在那儿，
        不拦的话弹窗里滚一下，背后的笔记列表也跟着滚。
        """
        def _wheel(e):
            d = int(getattr(e, "delta", 0)) // 40
            if d:
                self.canvas.yview_scroll(-d, "units")
            return "break"

        top.bind("<MouseWheel>", _wheel, add="+")
        top.bind("<Button-4>",
                 lambda e: self.canvas.yview_scroll(-3, "units"), add="+")
        top.bind("<Button-5>",
                 lambda e: self.canvas.yview_scroll(3, "units"), add="+")

    def apply_theme(self, pal, bg=None):
        """换肤：外框 / 内容画布 / 内框 / 右侧细滚动条四层底色一起换。

        不重建画布，所以 scrollregion、yview、已经滚到的位置都原样保留 —— 弹窗
        换主题时用户不会被甩回顶部，也不用拿快照把位置搬来搬去。
        """
        bg = bg or pal["editor"]
        self.configure(bg=bg)
        self.canvas.configure(bg=bg)
        self.inner.configure(bg=bg)
        self.sb.apply_theme(pal, bg)

    # ---------- 内部 ----------
    def content_height(self, settle=True):
        if settle:
            try:
                self.inner.update_idletasks()
            except Exception:
                pass
        h = self.inner.winfo_reqheight()
        if h <= 1:                      # 还没布局：退回图元包围盒
            b = self.canvas.bbox("all")
            h = (b[3] - b[1]) if b else 0
        return h

    def content_width(self):
        w = self.inner.winfo_reqwidth()
        if w <= 1:
            b = self.canvas.bbox("all")
            w = (b[2] - b[0]) if b else 0
        return w

    def _on_yscroll(self, first, last):
        self.sb.sync(first, last)

    def _on_cfg(self, e):
        self.canvas.itemconfigure(self._iid, width=e.width)
        self._sync()

    def _sync(self):
        """刷新可滚区域；内容没溢出时区域锁成可视高（滚轮就完全不动）。"""
        w = self.canvas.winfo_width() or self.content_width() or 200
        h = max(self.content_height(settle=False), self.canvas.winfo_height())
        self.canvas.configure(scrollregion=(0, 0, w, h))
        self.sb.sync(*self.canvas.yview())


def _has_cjk(s):
    """是否含中日韩字符 / 全角符号（这些字走中文字体，行高比等宽字体高）。"""
    for ch in s:
        if ord(ch) > 0x2E7F:
            return True
    return False


def _sel_fg(pal):
    """文字被选中时的前景色。

    Windows 上的 Tk 默认把「选中文字」画成白色（SystemHighlightText，标签色也会被
    盖掉），而选区底色是浅色（#b5d3bc 之类）→ 选中一段代码就白成一片，根本看不清。
    这里按选区底色算一个对比够大的前景色，显式设给 -selectforeground。
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


class HRule(tk.Frame):
    """正文里的一条分割线（对应 markdown 的 `---`）。

    为什么不用「─────」一串字符铺满一行：
      - 宽字符的宽度随字体变化，行尾永远对不齐；
      - 复制、搜索、导出都会把这串符号当成正文带出去，纯噪声；
      - 想删它的时候通常只会删掉半截，剩下几个残字。

    改用 Text 的 window_create 嵌一个画布（和 CodeBlock 同一套机制），
    宽度跟随编辑区，删除是整块删除，不会在数据里留下任何字符。
    """

    PAD_Y = dpi.px(9)            # 线上下留白（线本身 1px）

    def __init__(self, parent, app):
        tk.Frame.__init__(self, parent, bd=0, highlightthickness=0, cursor="arrow")
        self.app = app
        self._width = 0
        self._color = "#c9c9d1"
        self._cv = tk.Canvas(self, height=self.PAD_Y * 2 + dpi.px(1), bd=0,
                             highlightthickness=0)
        self._cv.pack(fill=tk.X)
        self.apply_theme(app.pal)
        self.bind("<Button-1>", lambda e: self._focus_text())
        self.bind("<Button-3>", self._open_menu)

    def apply_theme(self, pal):
        """换肤：底色跟着稿纸走，线的颜色比普通分隔线略深——它是内容，不是装饰。"""
        self._color = _mix_hex(pal["line"], pal["text"], 0.18)
        try:
            self.configure(bg=pal["editor"])
            self._cv.configure(bg=pal["editor"])
        except Exception:
            pass
        self._redraw()

    def set_width(self, w):
        """宽度由编辑区决定（Text 按 window 的请求宽度摆放）。"""
        self._width = max(1, int(w))
        try:
            self._cv.configure(width=self._width, height=self.PAD_Y * 2 + dpi.px(1))
        except Exception:
            pass
        self._redraw()

    def _redraw(self):
        cv = self._cv
        cv.delete("all")
        w = self._width or cv.winfo_width()
        if w <= 1:
            return
        y = self.PAD_Y
        cv.create_line(dpi.px(4), y, w - dpi.px(4), y, fill=self._color,
                       width=dpi.px(1))

    def _focus_text(self):
        """点一下线，焦点要回到正文——否则接着打字全都打不进去。"""
        try:
            idx = self.app.text.index(self)
            self.app.text.focus_set()
            self.app.text.mark_set("insert", idx)
        except Exception:
            pass

    def _open_menu(self, event=None):
        m = PopupMenu(self, self.app.pal)
        m.add("删除分割线", icon="trash", danger=True, command=self.remove)
        x = event.x_root if event is not None else self.winfo_pointerx()
        y = event.y_root if event is not None else self.winfo_pointery()
        m.popup(x, y)

    def remove(self):
        if self.app._refuse_readonly():
            return
        try:
            self.app.text.delete(self.app.text.index(self))
            self.app.mark_modified()
        except Exception:
            pass


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

    HEADER_H = dpi.px(26)
    PADX = dpi.px(10)
    PADY = dpi.px(7)
    MAX_H = dpi.px(560)             # 代码区最高像素（约 33 行）；超过就内部滚动 + 右侧细滚动条

    def __init__(self, parent, app, lang="", code=""):
        tk.Frame.__init__(self, parent, bd=0, highlightthickness=dpi.px(1))
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
        self._chip = tk.Label(self._head, bd=0, padx=dpi.px(7), pady=dpi.px(1), cursor="hand2")
        self._chip.pack(side=tk.LEFT, padx=(dpi.px(8), 0), pady=dpi.px(3))
        self._chip.bind("<Button-1>", self._open_menu)
        self._copy = tk.Label(self._head, bd=0, padx=dpi.px(6), pady=dpi.px(1), text="复制", cursor="hand2")
        self._copy.pack(side=tk.RIGHT, padx=(0, dpi.px(8)), pady=dpi.px(3))
        self._copy.bind("<Button-1>", lambda e: self.copy_code())
        self._copy.bind("<Enter>", lambda e: self._copy.configure(fg=self.app.pal["accent"]))
        self._copy.bind("<Leave>", lambda e: self._copy.configure(fg=self.app.pal["soft"]))
        # 格式化：先 pack 复制、再 pack 格式化，RIGHT 侧从右往左叠 → 格式化落在复制左边
        self._fmt = tk.Label(self._head, bd=0, padx=dpi.px(6), pady=dpi.px(1), text="格式化", cursor="hand2")
        self._fmt.pack(side=tk.RIGHT, padx=(0, dpi.px(2)), pady=dpi.px(3))
        self._fmt.bind("<Button-1>", lambda e: self.format_code())
        self._fmt.bind("<Enter>", lambda e: self._fmt.configure(fg=self.app.pal["accent"]))
        self._fmt.bind("<Leave>", lambda e: self._fmt.configure(fg=self.app.pal["soft"]))
        self._sep = tk.Frame(self, height=dpi.px(1), bd=0, highlightthickness=0)
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
        # 只读笔记里的代码块同样不能改：inner 是真正的 Text，主编辑区的守卫管不到
        # 它，必须自己再装一道。<Key> 是通配绑定，Tab/BackSpace 有单独绑定会盖掉它，
        # 所以那两个处理函数里也各挡一次。
        self.inner.bind("<Key>", self.app._readonly_key_guard, add="+")
        for _seq in ("<<Paste>>", "<<Cut>>", "<<Clear>>", "<<Undo>>", "<<Redo>>",
                     "<<PasteSelection>>"):
            self.inner.bind(_seq, self.app._readonly_clip_guard, add="+")
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
        self.configure(height=self.HEADER_H + dpi.px(1) + self._line_h + 2 * self.PADY)

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
        if notify and self.app.is_readonly():
            return                      # 只读：语言标签也不能换
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
        """按当前语言格式化代码块。

        JSON / HTML / XML 结构化重排，SQL 按子句关键词断行，其余重排行首缩进。
        """
        if self.app.is_readonly():
            return                      # 只读：格式化会重写整块代码
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
        ro = self.app.is_readonly()     # 只读笔记里代码块也不能改语言 / 删块 / 格式化
        m = PopupMenu(self.app.root, p)
        for label, key in CODE_LANGS:
            m.add(("# " if key == self.lang else "") + label, enabled=not ro,
                  command=lambda k=key: self.set_lang(k))
        m.add_separator()
        m.add("复制代码", command=self.copy_code)
        m.add("格式化代码", enabled=not ro, command=self.format_code)
        m.add("删除代码块", danger=True, enabled=not ro, command=self.delete_block)
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
        if self.app.is_readonly():      # 只读：Tab 会改缩进，直接吃掉
            return "break"
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
        if self.app.is_readonly():
            return "break"              # 只读：既不能删字，也不能删块
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
                # 带 "update"：让 Tk 先把整篇排完版再数像素。不带的话视口外的行还没
                # 参与排版，返回值偏小 —— 上面那条注释说的「不能单独信它」就是指这个。
                n = self.inner.count("1.0", "end-1c", "update", "ypixels")
                n = n[0] if isinstance(n, (tuple, list)) else n
                if n:
                    # -ypixels 数的是「到该处为止已经过去的像素」，不含这一行自身，
                    # 不补上就会少算一行 —— 表现是块底把最后一行切掉半截
                    est = max(est, int(n) + self._line_h_cjk)
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
        want = self.HEADER_H + dpi.px(1) + self._body_h
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
        # 行号栏是按「代码块内部每一行」编号的（见 app_ui._gutter_block_numbers），
        # 块内一滚，数字就得跟着换 —— 这条回调是块内滚动的唯一出口，不能漏。
        try:
            self.app._gutter_schedule()
        except Exception:
            pass
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
