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

# 颜色运算与表面色统一在 theme 里（纯函数，测试不建 GUI 也能断言）。
# 其中下划线那批是转出口：app_ui 从 widgets 这里引它们（历史沿革），别顺手删。
from theme import (FONT, _RES, _mix_hex, _hex_rgb, _contrast_ratio, _ink_target,
                   _luminance, _hover_bg, _press_bg,
                   _sel_bg, _quote_bg, _icode_bg,
                   _search_hit_bg, _popover_bg, _popover_border)

# 列表标记 / 缩进常量在 md_conv 里（纯字符串逻辑，不依赖 tkinter，能直接测）——
# 谁要用就 `from md_conv import`，这里不再代转。
import sql_fmt
import punct


# 智能分类（smart=True）用区别图标，普通文件夹用文件夹图标
SMART_ICON_ALL = "f_all"     # 全部笔记 → 清单三线
SMART_ICON_STAR = "f_star"   # 星标笔记 → 五角星（金色）
SMART_ICON_LOCK = "f_lock"   # 加密笔记 → 挂锁

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


# 颜色运算与状态色（_mix_hex / _shade / _hover_bg / _quote_bg / _popover_bg ...）
# 全部在 theme 里实现：纯函数、不依赖 tkinter，测试可以直接断言色差。
# 这里只是 import 进来 re-export，调用方（widgets / app_ui）的写法不用变。


# 应用中文名 APP_NAME 已随数据层迁到 store.py（窗口标题 / 示例笔记 / 导出文件名共用）

# 编辑去抖：连续输入时把「序列化落库」和「列表行刷新」合并成一次，避免每敲一键都
# 同步做全篇 serialize + SQLite 事务提交（长笔记下可达数百毫秒），把 Tk 的重绘挤到
# 后面造成打字滞后。内存里的 self.data 仍然每次立即更新，只是落盘延后。
SAVE_DEBOUNCE_MS = 600          # 停顿这么久没有任何输入就把数据写进数据库
ROW_REFRESH_DEBOUNCE_MS = 160   # 列表行标题/预览的刷新间隔（仍快到来得及跟手）
READONLY_BAR_H = 26             # 「内置示例笔记 · 不可编辑」提示条的高度

# 主按钮（实心强调色）往深色压多少。普通按钮的悬停/按下没有固定权重了：
# 由 theme._hover_bg / _press_bg 从「按钮所在区域的底色」推（HLS 里只动明度），
# 绿工具带悬停就还是绿、灰侧栏悬停就还是灰，换皮肤也不用逐处调。
BTN_PRESS_MIX = 0.18
BTN_RADIUS = 6              # 圆角收小一档：更接近编辑器工具条的「方中带圆」
TOOLBAR_H = 36          # 工具栏高度；下方还有 1px 分隔线
# 工具栏内分隔线：底色向次级文字色混合的比例。工具带本身压得很轻，分区感主要交给
# 这条线和底部的收口线来交代，所以线要够实，不能跟着底色一起变淡。
TOOL_LINE_MIX = 0.42
# 工具栏底色 = 侧栏色朝稿纸色提亮这个比例（0 = 完全是侧栏色，1 = 完全是稿纸色）。
# 方向是「提亮」而不是「压暗」：工具带排版的是正文，语义上属于编辑区，底色该站在
# 「侧栏 → 工具带 → 稿纸」这条渐亮序列的中间。压暗这条路试过两版——0.10 在浅绿
# 皮肤下被压成一条深色横杠，0.05 用户仍反馈偏暗——因为浅色皮肤里任何压暗都显脏。
# 0.55 时与稿纸差 1.07、与侧栏差 1.09，两边都刚好分得开，观感是「近乎白的浅色条」。
TOOL_BG_MIX = 0.55

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

# 多级文件夹：每深一层整张卡片往右挪这么多像素（层级靠缩进读出来）。
# 14 是「比文件夹图标窄一点」的量：缩进一眼可见，又不至于把深层的名字挤到没处站。
FOLDER_TREE_INDENT = 14
# 展开三角的宽度。叶子这一列留空占位，名字起点才对齐。
FOLDER_TWISTY_W = 13

# 「移动笔记到…」的选择树：一行 28px（比左栏卡片矮一档，弹窗里更紧凑），
# 每层缩进 18px —— 要同时容下「竖引导线 + 三角 + 图标」，比左栏的 14 宽一点才不挤。
PICK_ROW_H = 28
PICK_INDENT = 18

# 左侧两栏宽度：文件夹列 + 分隔线 + 笔记列表列
SIDEBAR_W = 216         # 文件夹列
LIST_W = 300            # 笔记列表列
# 搜索框：住在**文件夹列表顶部**（不再是顶栏），宽度跟着文件夹列走。
# 左右留白 12 与列表内容对齐 —— 上下同宽，侧栏看起来才是一条直上直下的竖边。
SEARCH_PADX = 12
# 两栏折叠时整列（含搜索框）一起收起，顶栏不留搜索入口，所以没有「折叠态宽度」。
SEARCH_H = 32

# 两栏可拖拽调宽：分隔线不再只是一根 1px 的装饰，而是一条 5px 的「抓取带」。
# 视觉上仍只画中间那 1px（粗细语言不变），两侧各 2px 是留给鼠标的余量 ——
# 纯 1px 的线在 125% 缩放下基本点不中，5px 是「够粗好抓、又看不出来」的折中。
SEP_HIT_W = 5
# 可拖范围。上限还得再受「编辑区至少留 EDITOR_MIN」约束（见 _split_limits），
# 否则两栏都拉满时正文会被挤成一条缝。下限取「名字还能读出来」的宽度。
SIDE_MIN, SIDE_MAX = 150, 360
LIST_MIN, LIST_MAX = 180, 520
EDITOR_MIN = 320

# 主题化确认对话框正文的最大行宽（像素），超出则截断名称，避免多折一行
DLG_TEXT_W = 360

# 工具栏图标按钮：图标画在 16x16 的方格内，四周留同样的内边距 → 约 34px 见方
ICON_SIZE = 16
ICON_STROKE = 1.7       # 图标线宽（像素）；太细在高分屏上会糊，太粗显笨
ICON_TAIL_RATIO = 0.62  # 「文字 + 下拉箭头」组合里箭头相对图标方格的占比
ICON_TEXT_GAP = 5       # 组合按钮中文字与小箭头之间的间隙

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
        # 有序列表：左侧 1/2/3 小字 + 右侧三条横线
        for i, ch in enumerate(("1", "2", "3")):
            yy = y0 + s * (0.22 + 0.28 * i)
            ids.append(cv.create_text(x0 + 1.4, yy, text=ch, fill=color,
                                      font=(FONT[0], 7, "bold"), anchor="w"))
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
            yy = y0 + s * (0.22 + 0.28 * i)
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
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
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
                           bd=0, padx=8, pady=4, justify="left")
            lbl.pack(padx=1, pady=1)
            win.update_idletasks()
            w = win.winfo_reqwidth()
            # 居中在控件下方，但别越出屏幕右缘
            sw = win.winfo_screenwidth()
            x = min(max(0, x - w // 2), max(0, sw - w - 4))
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
        body.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        def field(label, first=False):
            tk.Label(body, text=label, bg=shell, fg=soft,
                     font=(FONT[0], 9)).pack(anchor="w", padx=9,
                                             pady=(6, 0) if first else (4, 0))
            e = tk.Entry(body, bg=ed, fg=p.get("text", "#222222"),
                         insertbackground=accent, relief=tk.FLAT,
                         font=(FONT[0], 10), width=42, highlightthickness=1,
                         highlightbackground=_mix_hex(ed, border, 0.7),
                         highlightcolor=accent)
            e.pack(fill=tk.X, padx=9, pady=(3, 0), ipady=3)
            e.bind("<Return>", self._commit)
            e.bind("<Escape>", lambda _e: self.hide())
            e.bind("<FocusIn>", self._on_focus_in)
            e.bind("<FocusOut>", self._on_focus_out)
            return e

        self.text_entry = field("显示文字", first=True)
        self.entry = field("链接地址")
        tk.Label(body, text="回车保存 · 清空地址取消链接 · Esc 关闭",
                 bg=shell, fg=soft, font=(FONT[0], 9)).pack(
                     anchor="w", padx=9, pady=(4, 6))
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
        w = max(win.winfo_reqwidth(), 300)
        h = win.winfo_reqheight()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        px = min(max(0, int(x)), max(0, sw - w - 8))
        # 指针下方优先；贴底了翻到指针上方，别让弹窗跑到屏幕外
        py = int(y)
        if py + h > sh - 8:
            py = max(0, sh - h - 8)
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
                 padx=11, pady=6, radius=BTN_RADIUS, primary=False,
                 base=None, fg=None, cursor="hand2", stretch=False, outline=None,
                 icon=None, icon_size=ICON_SIZE, tail=None):
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
                            fill=self._cur_fill(), outline=self._cur_outline(), width=1)
        if self._icon and not self._text:
            if self._tail:
                # 主图标在左、小箭头在右：两者同中心线（h/2），与纯图标按钮一致
                iw = self._icon_size * ICON_TAIL_RATIO
                _draw_icon(self, self._icon, self._padx + self._icon_size / 2.0,
                           h / 2.0, self._icon_size, self._cur_fg())
                _draw_icon(self, self._tail, w - self._padx - iw / 2.0,
                           h / 2.0, iw, self._cur_fg())
            else:
                _draw_icon(self, self._icon, w / 2.0, h / 2.0, self._icon_size,
                           self._cur_fg())
        elif self._icon:
            iw = self._icon_size * ICON_TAIL_RATIO
            tw = f.measure(self._text)
            cx_text = self._padx + tw / 2.0
            cx_icon = w - self._padx - iw / 2.0
            self.create_text(cx_text, self._text_y(h, f), text=self._text, font=f,
                             fill=self._cur_fg())
            _draw_icon(self, self._icon, cx_icon, h / 2.0, iw, self._cur_fg())
        else:
            self.create_text(w / 2.0, self._text_y(h, f), text=self._text, font=f,
                             fill=self._cur_fg())

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
            return "#ffffff" if self._enabled else _mix_hex(p["accent"], p["selfg"], 0.45)
        if not self._enabled:
            # 禁用态：同一个墨色只混一点点，图标淡到「看得出来但明显不可用」。
            # 不能再淡——工具带本身很浅，再淡就彻底看不见，反而像没画出来。
            base = self._base
            return _mix_hex(base, _ink_target(base), 0.30)
        if self._state == "normal":
            # 显式指定的字色只用于常态（比如「A 颜色」按钮永远是强调色）。
            # 没指定时取「次级文字向主文字靠一大半」的墨色：比纯 soft 更实，
            # 工具条上的 B/I/U 这类小字才压得住底色（掘金的工具图标也接近主文字色）。
            if self._fg:
                return self._fg
            return _mix_hex(p.get("soft", "#76767e"), p.get("text", "#1f1f24"), 0.62)
        # 悬停/按下时底色被压暗，字色往上提一档才不糊。
        return p.get("text") or p["selfg"]

    def redraw(self):
        self.build()

    def apply_theme(self, pal, base=None):
        """换肤。传 base 时同步更新「按钮所在区域的底色」——否则换肤后按钮的正常态
        仍停在旧底色上，会显出一块洗不掉的色斑（工具栏按钮尤其明显）。"""
        self._pal = dict(pal)
        if base is not None:
            self._base = base
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

    def __init__(self, master, pal, direction, command=None, size=26, base=None,
                 chip=24):
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

    H = 25              # 条高：只有一行短标题，压矮一点把正文的地还回来
    TITLE_DPT = -2      # 标题字号相对正文（FONT[1]=13）的偏移 → 11pt
    CLOSE_DPT = -2      # × 与「+」跟标题同号：条只有 25 高，大一号就顶出去了
    TAB_MIN = 64
    TAB_MAX = 176
    TAB_PAD = 7         # 标题左右留白
    CLOSE_W = 16        # × 占的宽度（含右侧留白）
    ICON_W = 14         # 加密笔记的小挂锁
    SEP_W = 1           # 相邻标签之间的竖分隔线
    ARROW = 22
    NOTE_ICON_H = 12    # 每枚标签左侧那枚「笔记」标记（icon/note.png）的高度
    NOTE_PAD_L = 6      # 标记离标签左边缘的留白
    NOTE_GAP = 3        # 标记与标题之间的间距
    DRAG_TOL = 4        # 拖拽判定阈值（px）：位移小于它就是「点击」
    DRAG_LINE_W = 2     # 拖拽时插入位那条竖线的宽度
    DRAG_EDGE = 26      # 指针离画布左右边缘多近开始自动横向滚

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
        top = tk.Frame(f, height=2, bg=self.pal.get("accent", "#2f7cf6"))
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
            top = tk.Frame(f, height=2, bg=self.pal.get("accent", "#2f7cf6"))
            top.pack(side=tk.TOP, fill=tk.X)
            rec["top"] = top
        if locked:
            # 加密且还没解锁：挂锁 —— 这一栏本来就是「锁着的」，用挂锁才说得清
            ic = tk.Canvas(f, width=self.ICON_W, height=self.h - 2, bd=0,
                           highlightthickness=0, bg=bg)
            ic.pack(side=tk.LEFT)
            _draw_icon(ic, "lock", self.ICON_W / 2.0, (self.h - 2) / 2.0, 10,
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
        cl.pack(side=tk.RIGHT, padx=(0, 5))
        rec["label"] = lbl
        rec["close"] = cl
        # 事件：点标签切换、**按住拖拽换位置**、中键关闭、右键菜单。这几样绑在 Frame 上，
        # 而 Tk 的事件不往父控件走 —— 铺满标签的标题/挂锁/强调线靠下面的 _bubble 递上来。
        f.bind("<Button-1>", lambda e, t=tid: self._on_tab_press(e, t))
        f.bind("<B1-Motion>", lambda e, t=tid: self._on_tab_drag(e, t))
        f.bind("<ButtonRelease-1>", lambda e, t=tid: self._on_tab_release(e, t))
        f.bind("<Button-2>", lambda e, t=tid: self.on_close and self.on_close(t))
        f.bind("<Button-3>", lambda e, t=tid: self.on_menu and self.on_menu(e, t))
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
        # 常态底色只比条底深一点（0.06）：深了会把「悬停再暗一档」的层次吃掉，
        # 边界交给下面的 1px 分隔线去讲。
        return _mix_hex(self.base, _ink_target(self.base), 0.06), p.get("soft", "#76767e")

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
                width=max(self.TAB_MIN, min(self.TAB_MAX, int(w) + 8)))
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

    SIZE = 18            # 画布边长（顶栏 30 高，上下各留 6px）
    GLYPH = 13           # 图形边长
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

    GLYPH = 13           # 图形边长（四根 2px 杠 + 2px 缝刚好整除）
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


class SettingsButton(IconButton):
    """顶栏上的「全局设置」入口（齿轮），排在侧栏开关右侧。

    原来顶栏右侧那枚写着「设置」的圆角按钮挪到了这里，功能不变：
    点开同一张全局设置弹窗（正文字体族 / 正文字号）。
    数据导出导入清空已单独搬到 DataButton（顶栏再往右一枚）。
    """

    GLYPH = 15           # 齿轮细节多，比侧栏开关大 2px 才看得出齿
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


def _png_icon_photo(master, name, size=25):
    """载入 `icon/<name>`（200×200 RGBA）并整数倍降采样到约 size 见方。

    Tk 只能**整数倍**降采样（最近邻），所以先按 200/size 四舍五入出倍数 k，
    再 subsample(k)：k=12 时出来 16px，正好是顶栏要的档。不需要 PIL。

    图形带 alpha，贴到任何底色上都会自己合成，换肤不用重新生成。
    ⚠️ 与自绘版的两点差别：①颜色是图里烤死的（`#323333`），不跟皮肤的
    `soft` / `selfg` 变，hover 时只有圆角底变色；②缩放是最近邻，边缘没有
    抗锯齿。文件不在（裁过的最小包）时返回 None，调用方退回自绘。
    """
    path = os.path.join(_RES, name)
    if not os.path.exists(path):
        return None
    try:
        img = tk.PhotoImage(file=path, master=master)
    except Exception:
        return None
    k = max(1, int(round(max(img.width(), img.height()) / float(size))))
    return img.subsample(k) if k > 1 else img


def _db_icon_photo(master, size=25):
    """`icon/db.png`（数据库圆柱 + 齿轮）的位图版本。"""
    return _png_icon_photo(master, "db.png", size)


def _note_icon_photo(master, height=14, color=None):
    """`icon/note.png`（367×200 的一支斜铅笔）→ 按高度缩放、保持宽高比的位图。

    前头那两套都不适用：`_png_icon_photo` 按「缩成 size 见方」算降采样倍数，
    `_tinted_png_photo` 也是裁 bbox 后重采样成正方形 —— 367×200 的宽图压成 1:1
    会把铅笔拉变形。

    有 PIL：按高度等比缩放（LANCZOS，边缘有抗锯齿），再按 alpha 染色成 color
    （位图颜色是烤死的，不染就跟皮肤脱节）；没有 PIL：退回 PhotoImage 整数倍
    降采样（最近邻、原色直出）。
    """
    path = os.path.join(_RES, "note.png")
    if not os.path.exists(path):
        return None
    try:
        from PIL import Image, ImageTk
        im = Image.open(path).convert("RGBA")
        bb = im.split()[3].getbbox()
        if bb:
            im = im.crop(bb)                 # 裁掉四周透明边
        w, h = im.size
        nw = max(1, int(round(height * w / float(h))))
        resample = getattr(Image, "LANCZOS", getattr(Image, "BICUBIC", None))
        im = im.resize((nw, max(1, height)), resample)
        if color:
            r, g, b = _hex_rgb(color)
            out = Image.new("RGBA", im.size, (r, g, b, 255))
            out.putalpha(im.split()[3])       # alpha 原样保留 → 抗锯齿边还在
            im = out
        return ImageTk.PhotoImage(im, master=master)
    except Exception:
        pass
    try:
        img = tk.PhotoImage(file=path, master=master)
    except Exception:
        return None
    k = max(1, int(round(img.height() / float(height))))
    if k > 1:
        img = img.subsample(k)
    if color:
        # 没有 PIL 时的染色：逐点 put（半透明边缘会被压实，20px 上下只差一圈像素）
        try:
            for y in range(img.height()):
                for x in range(img.width()):
                    if img.transparency_get(x, y):
                        continue
                    img.put(color, to=(x, y, x + 1, y + 1))
        except Exception:
            pass
    return img


class DataButton(IconButton):
    """顶栏上的「数据管理」入口（数据库 + 齿轮），排在全局设置右侧。

    原来埋在全局设置弹窗下半部分的三件事搬到了这里：导出备份 / 导入数据 /
    清空所有数据。设置弹窗因此只剩「正文长什么样」这类外观项。

    图形直接用 `icon/db.png` 的位图（整数倍降采样到 16px），不走自绘；
    `DB_PARTS` 只在图片缺失时兜底。
    """

    # 画布 18 与其它四枚图标一致（顶栏 30 高，上下各留 6px）—— 这一排的高度
    # 必须齐，否则三枚按钮的视觉外框会一个比一个大。
    SIZE = 18
    GLYPH = 15           # 200 / 13 ≈ 15.4 → subsample(13,13) 得 15px
    TIP = "数据管理"

    def _paint(self, fg, bg):
        img = getattr(self, "_photo", None)
        if img is None:
            img = _db_icon_photo(self, self.GLYPH)
            self._photo = img        # 必须留着：PhotoImage 被回收后画布上的图会白掉
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

    图形直接用 `icon/about.png` 的位图（整数倍降采样到 16px），
    `_draw_info_icon` 只在图片缺失时兜底。
    """

    SIZE = 18            # 与其它四枚同一档画布，这一排的外框才对得齐
    # 位图目标 18px（= 整块画布），不是图形 18px：about.png 的内容只有 176/200，
    # 四周留着 12px 透明边 —— 按 15 取值只会拿到 subsample(13) 的 15px 图，
    # 圆环实际只有 13.2px，比齿轮和数据库都小一圈。
    # GLYPH=18 → k=round(200/18)=11 → 18px 图、圆环 15.8px，和旁边两枚齐平。
    GLYPH = 18
    TIP = "关于"

    def _paint(self, fg, bg):
        img = getattr(self, "_photo", None)
        if img is None:
            img = _png_icon_photo(self, "about.png", self.GLYPH)
            self._photo = img        # 必须留着：PhotoImage 被回收后画布上的图会白掉
        if img is None:
            _draw_info_icon(self, self.SIZE // 2, self.SIZE // 2,
                            self.GLYPH, fg)
            return
        self.create_image(self.SIZE // 2, self.SIZE // 2, image=img)


# 没 PIL 时的回退表：图标名 -> 图形占整图的比例（按 alpha 包围盒量出来的）。
# 只对需要精确对齐的图登记；没登记的按 _CONTENT_RATIO 兜底。
_CONTENT_RATIOS = {
    "create.png": 0.79,      # 158 / 200
    "github.png": 1.00,      # 200×196，几乎铺满，没有透明边
    "issue.png": 0.74,       # 148 / 200
}
_CONTENT_RATIO = 0.79        # 未登记图标的默认占比（create.png 那一档）


def _tinted_png_photo(master, name, size, color, ratio=None):
    """载入 `icon/<name>`、按 alpha 包围盒裁边、缩到 size 见方，并把图形染成 color。

    与 `_png_icon_photo` 的分工：那个是「原色直出」（db / about 两张本身就烤好了
    颜色），这个要**换色**，所以优先走 PIL —— 除了染色还能顺手按 bbox 裁掉四周的
    透明边，图形正好铺满 size（这几张 200px 图标普遍留了 10% 边）。

    没有 PIL 时退回 PhotoImage：整数倍降采样 + 逐点 `put` 上色。降采样前先按
    `ratio`（图形占整图的比例）把目标放大，抵消掉不掉的透明边，图形仍是 size 见方；
    `put` 不能带 alpha，半透明边缘会被压成实心 —— 20px 上下只差一圈像素，认了。
    """
    path = os.path.join(_RES, name)
    if not os.path.exists(path):
        return None

    try:
        from PIL import Image, ImageTk
        resample = getattr(Image, "LANCZOS", getattr(Image, "BICUBIC", None))
        im = Image.open(path).convert("RGBA")
        bb = im.split()[3].getbbox()
        if bb:
            im = im.crop(bb)              # 裁掉四周透明边，图形铺满
        im = im.resize((max(1, size), max(1, size)), resample)
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
    # 位图目标按「图形占整图的比例」反推：k=11 时 18px 图、图形 14.2px，
    # 居中画到 18px 画布上被裁掉的正是那圈透明边。
    if ratio is None:
        ratio = _CONTENT_RATIOS.get(name, _CONTENT_RATIO)
    k = max(1, int(round(img.width() * ratio / float(size))))
    if k > 1:
        img = img.subsample(k)
    try:
        for y in range(img.height()):
            for x in range(img.width()):
                if img.transparency_get(x, y):
                    continue
                img.put(color, to=(x, y, x + 1, y + 1))
    except Exception:
        return img
    return img



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
    所以在 `_tinted_png_photo` 里按 alpha 重新上色，并缓存「图 + 当时用的颜色」，
    换肤后颜色一变就重新生成。
    """

    SIZE = 18
    GLYPH = 15           # 与齿轮同档：内容裁边后正好 15px
    TIP = "新建笔记（Ctrl+N）"

    def _paint(self, fg, bg):
        color = self._pal.get("accent", "#2f7cf6")
        img = getattr(self, "_photo", None)
        if img is None or getattr(self, "_photo_color", None) != color:
            img = _tinted_png_photo(self, "create.png", self.GLYPH, color)
            self._photo = img        # 必须留着：PhotoImage 被回收后画布上的图会白掉
            self._photo_color = color
        if img is None:
            _draw_create_icon(self, self.SIZE // 2, self.SIZE // 2,
                              self.GLYPH, color)
            return
        self.create_image(self.SIZE // 2, self.SIZE // 2, image=img)


# 自绘标题栏（= 顶栏）的高度。窗口键、应用图标、标题基线都按它对齐，
# 改这一处就够了（win_frame 的客户区重算不关心高度，它只负责去掉系统那条）。
#
# 30 = Windows 10/11 在 100% 缩放下的标准标题栏高度，窗口键、应用图标、标题基线
# 都按它对齐。这一排图标缩小后（画布 18 / 齿轮 15 / 数据库 15 / 字号 9pt bold）在 30
# 里上下各留 6px，整条比原来松一档 —— 高度不跟着缩，缩的只是里面的东西。
TOP_BAR_H = 30


class WindowButton(tk.Canvas):
    """窗口键：最小化 / 最大化 / 关闭（挂在自绘标题栏的最右端）。

    系统标题栏被顶栏顶掉之后（见 win_frame.py），这三枚得自己画。刻意照 Windows
    的规矩来：36×30 的方片贴在窗口右上角，常态不画底，悬停浮出浅底、按下再深一档；
    关闭键悬停是红底白叉 —— 这条约定用户早成了肌肉记忆，别跟着皮肤换色。

    图形只用 1px 细线且落在整像素上：Canvas 不做抗锯齿，1.5px 的线会糊成两道灰边。
    最大化之后中间那枚要换成「还原」的双叠框，由 set_maximized() 切换。
    """

    W, H = 36, TOP_BAR_H
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
            self.create_line(cx - 5, cy, cx + 5, cy, fill=fg, width=1)
        elif self._kind == "close":
            self.create_line(cx - 4, cy - 4, cx + 5, cy + 5, fill=fg, width=1)
            self.create_line(cx - 4, cy + 5, cx + 5, cy - 4, fill=fg, width=1)
        elif self._maximized:
            # 还原：两个错开的方框（前框右下少一条边，像 Windows 的还原键）
            self.create_rectangle(cx - 5, cy - 2, cx + 3, cy + 6, outline=fg, width=1)
            self.create_line(cx - 2, cy - 6, cx + 6, cy - 6, fill=fg, width=1)
            self.create_line(cx + 6, cy - 6, cx + 6, cy + 1, fill=fg, width=1)
        else:
            self.create_rectangle(cx - 5, cy - 5, cx + 5, cy + 5, outline=fg, width=1)

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
MENU_GUTTER = 22        # 有图标时的文字左缩进 = 16px 图标 + 6px 间隙（图标恰好占满这 22px，
                        # 菜单宽度一个像素都不用加）
MENU_GUTTER_MIN = 6     # 纯文字菜单的缩进。没有图标就别留那一列空位——1px 到 22px 的
                        # 空缩进在实机里就是每行左边一条死白。
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
        # {"label","command","danger","enabled","icon"}；label 为 None 表示分隔线
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
    def add(self, label, command=None, danger=False, enabled=True, icon=None):
        """icon 取 _draw_icon 的名字；给了图标的菜单会自动把左侧缩进撑到 22px。

        缩进按「整张菜单有没有图标」统一决定，不是逐行：混排时没图标的那几行也
        留同样的空位，否则文字起点参差不齐。所以图标要么配齐、要么一个都别给。
        """
        self._items.append({"label": label, "command": command,
                            "danger": danger, "enabled": enabled, "icon": icon})
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
            c.create_text(MENU_PAD_X + self._gutter, (y0 + y1) // 2,
                          text=it["label"], anchor="w",
                          font=(FONT[0], FONT[1] - 1), fill=fg)
            # 图标颜色直接吃这一行的 fg：悬停是实底高亮（字色换成 selfg）、禁用走 soft、
            # 危险项标红且悬停时反白。图标若固定成 pal["text"]，悬停那一行就会糊在底色里。
            if it.get("icon"):
                _draw_icon(c, it["icon"], MENU_PAD_X + ICON_SIZE / 2.0,
                           (y0 + y1) // 2.0, ICON_SIZE, fg)
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

    PAD_Y = 9            # 线上下留白（线本身 1px）

    def __init__(self, parent, app):
        tk.Frame.__init__(self, parent, bd=0, highlightthickness=0, cursor="arrow")
        self.app = app
        self._width = 0
        self._color = "#c9c9d1"
        self._cv = tk.Canvas(self, height=self.PAD_Y * 2 + 1, bd=0,
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
            self._cv.configure(width=self._width, height=self.PAD_Y * 2 + 1)
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
        cv.create_line(4, y, w - 4, y, fill=self._color, width=1)

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
