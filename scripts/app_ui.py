# -*- coding: utf-8 -*-
"""素笺 —— 界面层：NotesApp 的布局、渲染、刷新与鼠标交互。

以 mixin 形态提供，最终由 notes_app.py 里的 class NotesApp(UIMixin, ActionsMixin) 组合。
"""
import math
import os
import queue
import re
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

# 像素常量要跟 DPI 走：SCALE 已在 notes_app.py 顶部定好（导入本模块之前）
import dpi
import secret

from store import (APP_NAME, save_data,
                   SMART_FOLDERS, SYSTEM_FOLDER, LOCKED_BODY_TEXT, folder_children,
                   subtree_ids, ensure_note_color, resolve_adjacent_colors,
                   pick_note_color, NOTE_COLORS,
                   ALARM_INDEX_ID, ALARM_ROW_TAG,
                   alarm_index_content)
from theme import (FONT, _RES, build_palette, DEFAULT_THEME, THEMES, list_themes,
                   theme_label, on_accent)
from widgets import (RoundButton, FontSizeSlider, ArrowButton,
                     SidebarToggle, SettingsButton, DataButton, AboutButton,
                     CreateButton, WindowButton,
                     ExpandAllButton, CollapseAllButton, TwistyButton,
                     TOP_BAR_H,
                     ScrollJumpNav, MarqueeLabel, CountBadge, PopupMenu,
                     ScrollPanel, ScrollBody, HRule, Tooltip, TabStrip,
                     gutter_width, gutter_slots, gutter_hit_at,
                     SMART_ICON_ALL, SMART_ICON_STAR, SMART_ICON_LOCK,
                     SMART_ICON_ALARM,
                     FOLDER_ICON_W, FOLDER_ICON_H, CARD_RADIUS, CARD_PADX, CARD_GAP,
                     SIDEBAR_W, LIST_W, SEARCH_PADX,
                     SEARCH_H, DLG_TEXT_W,
                     SEP_HIT_W, SIDE_MIN, SIDE_MAX, LIST_MIN, LIST_MAX, EDITOR_MIN,
                     FOLDER_TREE_INDENT, FOLDER_TWISTY_H,
                     PICK_ROW_H, PICK_INDENT,
                     TOOLBAR_H, TOOL_LINE_MIX, JUMP_W, JUMP_GAP,
                     VSCROLL_W,
                     TEXT_PADX, TEXT_PADY, line_spacing,
                     READONLY_BAR_H,
                     BTN_PRESS_MIX, CODE_FONTS, CODE_LANGS, MENU_DANGER, _sel_fg,
                     _quote_bg, _sel_bg, _icode_bg, _hover_bg, _search_hit_bg,
                     GITHUB_URL, ISSUES_URL,
                     _tinted_png_photo, _icon_photo,
                     _mix_hex, _ink_target, _round_rect_points, _star_points,
                     _toolbar_bg, _tab_idle_bg,
                     _readonly_bar_bg, _status_bar_bg, _status_bar_rule,
                     _status_bar_fg, _draw_section_icon,
                     repaint_widget_colors,
                     AAImage, aa_disc, aa_ring, aa_left_half, aa_round_rect,
                     aa_outline, aa_segment)
from md_conv import (TODO_OFF, TODO_ON, TODO_LEGACY, TODO_MARKS, TODO_GAP, TODO_GAP_TAG,
                     LI_INDENT, split_list_prefix)
import secret
import updater
from find_replace import replacement_plan
from version import __version__
from win_frame import (WindowFrame, work_area, work_area_at,
                       fit_dialog, dialog_center_y, panel_canvas_height)


# 编辑区底部状态条的高度（创建时间 / 修改时间 / 字数）。
# ⚠️ 不再等于 TEXT_PADY：2026-11-05 起 TEXT_PADY 让出 8px 给首行标题的 spacing1，
# 但 9pt 一行字实测要 17px（100%），12px 的状态条会把字底裁掉，状态条仍保持 20。
# 多出来的 (FOOTER_H - TEXT_PADY) = 8px 由 Text 的 pack 底部外边距补上，于是
# 「正文最后一行 → 窗口底」仍是 20，状态条浮出来既不裁字、也不遮正文。
FOOTER_H = dpi.px(20)
# Text 的 pack 底部外边距：把被 TEXT_PADY 让出去的那 8px 还给底部留白。
FOOTER_GAP = FOOTER_H - TEXT_PADY
# 触发状态条的「底部热区」高度。比状态条本身高一截：只留 20px 的话，
# 指针得像素级瞄准最下面那一条才碰得到，实际是「怎么移都不出来」。
FOOTER_HOT_H = FOOTER_H + dpi.px(26)
# 不换行时正文底部那条横向细滚动条的厚度。只在 wrap=NONE 且真有超宽长行时浮出，
# 与右侧竖向滚动条同宽感；窗口拉宽到放得下、或切回自动换行就自动收起。
HSCROLL_H = dpi.px(9)

# 待办勾选框（字面字符 ⬜ / ✅，见 md_conv.TODO_OFF 里为什么不使用自绘控件）。
# 未勾态的字符颜色 = chk_off 向主文字色混合 —— 和原来自绘空心框的描边同一套算法，
# 混到 0.5 时对比约 3.4，够看清又不抢正文。
TODO_OFF_MIX = 0.50
# 勾选框字号 **按正文比例算**，不用固定加几号：用户在设置里把正文从 8pt 调到
# 25pt，固定加号会让图标越来越大/越来越小，比例一走就"图标比字大一号"的观感没了。
# 系数由**墨迹高度**测出来（目标：勾选框墨迹 = 汉字墨迹）：
#   Emoji 族的 ⬜ / ✅ 墨迹 ≈ 1.5 × 字号；汉字墨迹 ≈ 1.17 × 字号。
#   12pt 正文 → 10pt 图标，两者都是 15~16px 高。
# **两个状态共用同一个系数**，这是硬要求：上一版给 ▢ / ✅ 各算一套（1.25 / 0.75），
# 打钩瞬间图标往下掉 3px、后面正文左移 1px —— 不同字体不同字号不可能自动对齐。
TODO_ICON_SCALE = 0.83
# 勾选框的字体族，按顺序取系统里第一款有的。⬜ 和 ✅ 必须落在**同一族**里，字宽
# 才会相等（同族同字号时 advance 完全一致，打钩时正文不会左右跳）。
# Segoe UI Symbol 兜底：它画得出 ⬜，但 U+2705 是「带斜线的勾」不是「绿底白勾」，
# 所以只能垫底。注意族名要写系统里的**实际名字**：这台中文 Windows 上 families()
# 返回的是「微软雅黑」，「Microsoft YaHei」永远匹配不上（老配置就栽在这）。
TODO_ICON_FONTS = ("Segoe UI Emoji", "Segoe UI Symbol", "微软雅黑")

# ---------- Ctrl+F 查找 / Ctrl+H 查找替换的浮条 ----------
# 两个标签分开：FIND_HIT_TAG 是全部命中，FIND_CUR_TAG 是「当前跳到的那一条」。
# 不与顶栏搜索的 "find" 合并，否则两边会互相抹掉对方的高亮（见 _config_tags）。
FIND_HIT_TAG = "findhit"
FIND_CUR_TAG = "findcur"

# ---------- 笔记列表的行内间距 ----------
# 单条笔记里「标题」与「正文预览」之间的留白（px，上下各一份）。
# ⚠️ 别指望靠 `pl.pack(pady=...)` 调：预览顶边本来就是 0，那十几 px 空白
# 不是 margin，而是标题在上面那一行里**垂直居中**留下的余量 —— 星标 Button 在
# Windows 主题下有 15px 固定内边距（字高 30 → 实测 49），单它把顶行撑到 49，
# 而标题 Canvas 只有 24，居中后下面自然空出 13px。所以这里给顶行定高
# `标题字高 + 2×本值`，星标多出来的空白由父容器裁掉（裁到的全是内边距，
# 星标字墨仍在正中，不会缺角）。
NOTE_TITLE_GAP = dpi.px(6)

# ---------- 竖向（矩形）选择 ----------
# 键位：Alt + 左键拖动。Ctrl+Alt 也认 —— 那是 VS Code 的习惯，两种都收下。
# ⚠️ **0x8 绝不能拿来判 Alt**（本机实测，见下）：这台机器上 Tk 报的 state 里 0x8
# 是**常驻位** —— 不按任何键时 report 出来的就是 0x8（Shift=0x9、Ctrl=0xC 也都带着
# 它），真按下 Alt 才多出 0x20000（0x20008）。早先写成 `0x20000 | 0x8`，结果是
# **每一次鼠标按下都命中 Alt**：普通拖选永远挤不出 sel，双击选词、Shift+点击、
# 拖出窗口自动滚、工具栏那 9 处按 tag_ranges("sel") 干活的功能全线哑掉 ——
# 用户看到的就是「Alt+拖动和普通拖动效果一样，都是列选择」。
# 所以 Windows 只认 0x20000；X11 上 Mod1(0x8) 才是 Alt，那里才用它。
ALT_MASK = 0x20000 if sys.platform.startswith("win") else 0x8
# Ctrl 的 state 位各平台一致（0x4 = Control），可以放心硬编码；Alt 则必须走上面那个
# 平台分支。两处判据别混着用 —— 判 Alt 时带上 0x4 或判 Ctrl 时带上 0x8，都会把
#「组合键放行」变成「全部放行」（见 _col_key 里那条注释）。
CTRL_MASK = 0x4


def alt_key_down():
    """直接问系统：此刻 Alt 键有没有真的按着（Windows 专用兜底）。

    为什么还要这一层：上面的 0x20000 是当前 Tk 版本在本机上报的位，可 modifier
    映射随 Tk 版本、输入法、远程桌面而变；一旦哪天它改口，光靠 state 又会变成
    「普通拖选变列选择」或者「Alt 完全没反应」。真假以系统为准。
    """
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes
        return bool(ctypes.windll.user32.GetKeyState(0x12) & 0x8000)  # VK_MENU
    except Exception:
        return False
# 矩形高亮用的标签：**不复用 sel**。Tk 的 <<Copy>> 只认 sel.first→sel.last 这一整段
# 连续文本（实测：挂三段 sel 复制出来会把矩形外的字一起带上），所以矩形自己挂一个
# 标签、复制也自己实现；顺带让工具栏那 9 处 tag_ranges("sel") 完全不受影响
#（它们只取第一段，混用会变成「加粗只作用于第一行那截」）。
COLSEL_TAG = "colsel"
# 列模式下「每行一根光标」的宽度（像素）。Tk 的 Text 只有一根系统光标，多光标只能
# 自己画：每行一个这么宽的小窗 place 进正文，见 _col_caret_show。高度不写死 ——
# 由 dlineinfo 的行盒逐行量（标题行 43、正文 28），光标才和所在行一样高。
COL_CARET_W = dpi.px(2)
# 拖出正文后的自动滚动节奏（ms）与每次滚动的行数
COLSEL_SCROLL_MS = 45
COLSEL_SCROLL_STEP = 1


# ---------- 信息型弹窗的统一宽度 ----------
# 全局设置 / 数据管理 / 关于这三张窗并排看是「一套」，宽度必须一样：
# 三张都按内容自适应的话，关于最宽（547）、设置居中（516）、数据管理最窄（416），
# 点来点去窗口左右边缘一直在跳，像三个不同程序弹出来的。
# 统一到「关于」的宽度 —— 那是唯一**撑不满也得那么宽**的一张：里面有两串完整
# 项目地址不能折行，宽度由最长的那串地址决定。所以这里用同一套字体去量地址，
# 再补上那一行固定的框距（图标 16 + 两侧留白 + 标签 + 右缘 20）。
_PANEL_W = None


def _panel_width(root=None):
    """三张信息弹窗（全局设置 / 数据管理 / 关于）的公共宽度（像素）。

    跟着字体和地址走而不是写死：换了系统字体、以后改了仓库地址，三张窗一起变宽
    变窄，不会只剩「关于」一张忽然比邻居胖一圈。11pt 微软雅黑下 = 380 + 180 = 560。
    """
    global _PANEL_W
    if _PANEL_W is None:
        try:
            f = tkfont.Font(root=root, family=FONT[0], size=11)
            url_w = max(f.measure(GITHUB_URL), f.measure(ISSUES_URL))
        except Exception:
            url_w = 380          # 量不出来时退回实测值，宁可少几像素也不能炸
        # 180 = 地址行除地址本身以外的固定开销（实测 167）+ 余量。余量有两个用处：
        # ① 保证「关于」的 reqwidth 打不过它，三张窗才会**正好**一样宽；
        # ② 把宽度让给数据管理里最长的那句说明（512px），三段说明都能一行说完，
        #    不会出现「第一段折两行、后两段一行」的参差。
        _PANEL_W = url_w + dpi.px(180)
    return _PANEL_W


# ---------- 信息型弹窗的统一高度 ----------
_PANEL_H_FALLBACK = 504      # 100% 缩放下「数据管理」窗的实测客户区高度


def _panel_height(app):
    """「全局设置」该有多高 —— 直接量「数据管理」窗的自然高度。

    不写死像素：数据管理窗的说明文字会随系统字体折行，换字体 / 换语言 / 高 DPI
    都会让它的自然高度变。让设置窗去问它本人，两张窗才是同一把尺，而不是「在
    我这台机器上刚好一样」。量法见 `open_data_manager(_measure=True)`：建一张
    withdraw 的窗、摆完同样的内容、读 reqheight 后立刻销毁，不显示也不抢 grab。
    每次打开设置窗量一次（几毫秒）且不缓存 —— 缓存会在数据管理窗改了布局后
    悄悄留着旧值，那就又对不齐了。
    """
    try:
        h = int(app.open_data_manager(_measure=True))
        if h > 100:
            return h
    except Exception:
        pass
    return dpi.px(_PANEL_H_FALLBACK)


def _draw_accent_bar(cv, w, h, color):
    """在笔记卡片左缘画一条圆头色条（宽 4、上下各留 6、两端半圆）。

    为什么不用现成的圆头图元（`create_oval` 或 `create_line(capstyle="round")`）：
    Tk 的 Canvas 走 GDI 光栅化、**完全没有抗锯齿**，颜色非此即彼。4px 宽的条，
    端头半径只有 2px，`create_oval` 会把偶数尺寸的包围盒向右多占一列 —— 顶端
    2 像素、往下第三行却宽出 5 像素，右边多出一个 1px 的凸起（实测像素：
    `..##..` / `####` / `#####` / `#####` / `####`），这就是「两头有毛刺」的来源。

    所以这里自己按行栅格化：把端头当成半圆，逐行算出左右边界（浮点），
    小数部分落到哪个像素就按覆盖率把**色条色与卡片底色混合**后画那一个像素。
    中间直身段仍然是一个矩形，只有上下各 2 行要逐像素补，开销可以忽略。

    坐标约定：x 取 4 而不是 0 —— 卡片有 CARD_RADIUS 圆角，贴边的竖条会在上下
    拐角处戳到圆角外面；缩进 4 + 上下各留 6 之后整条都在卡片内部。
    """
    bw = dpi.px(4)
    x0 = dpi.px(4)
    y0, y1 = dpi.px(6), h - dpi.px(6)
    if y1 - y0 < bw * 2:
        return                  # 行被压得极矮时别画，否则端头糊成一团
    bg = cv._color              # 当前卡片底色：混合色要跟着悬停/选中一起变
    r = bw / 2.0
    cv.create_rectangle(x0, y0 + r, x0 + bw, y1 - r, fill=color, outline="")
    for y in range(int(y0), int(y1)):
        cy = y + 0.5
        if cy < y0 + r:
            d = y0 + r - cy
        elif cy > y1 - r:
            d = cy - (y1 - r)
        else:
            continue            # 直身段，已经被上面那个矩形盖住了
        dx = r - math.sqrt(max(0.0, r * r - d * d))
        xs, xe = x0 + dx, x0 + bw - dx
        li, xi = int(math.ceil(xs - 1e-9)), int(math.floor(xe + 1e-9))
        if xs - math.floor(xs) > 1e-6:                  # 左端半覆盖像素
            p = int(math.floor(xs))
            cv.create_rectangle(p, y, p + 1, y + 1,
                                fill=_mix_hex(bg, color, li - xs), outline="")
        if math.floor(xe + 1e-9) - xe < -1e-6:          # 右端半覆盖像素
            p = int(math.floor(xe))
            cv.create_rectangle(p, y, p + 1, y + 1,
                                fill=_mix_hex(bg, color, xe - p), outline="")
        if xi > li:
            cv.create_rectangle(li, y, xi, y + 1, fill=color, outline="")


class UIMixin(object):
    def _build_ui(self):
        self.root.title(APP_NAME)
        self.root.geometry("%dx%d" % (dpi.px(1080), dpi.px(700)))
        self.root.minsize(dpi.px(820), dpi.px(540))
        # 窗口/任务栏/弹窗图标：笔记本+铅笔（替换 Tk 默认的羽毛）
        self._set_app_icon()
        # 自绘标题栏：下面那条 30px 顶栏就是窗口标题栏本身，系统那条会被顶掉
        # （做法见 win_frame.py：不动窗口样式，只把客户区改报成「整个窗口」）。
        # 先把窗口藏起来 —— 客户区重算必须在窗口拿到真实尺寸之后，而重算之前
        # 露出来的那一帧还是带系统标题栏的毛坯，闪一下很难看。
        self.root.withdraw()
        self.frame_ctl = WindowFrame(self.root)

        # 顶部栏（= 标题栏）
        self.top = tk.Frame(self.root, height=TOP_BAR_H, bg=self.pal["top"])
        self.top.pack(fill=tk.X)
        # 顶栏与下方区域的硬分割线：顶栏底色和左栏（side）**完全同色**（皮肤给
        # 的都是同一个 top），不带这一道两者会连成一片，标题栏到哪儿结束看不出来。
        # 不能用 pal["line"]（只比 top 深 5%，1px 肉眼看不出），改按顶栏色压深
        # 22%，实测在 1x 下是一条清楚的界线而不刺眼。
        self.top_rule = tk.Frame(self.root, height=dpi.px(1), bg=self._top_rule_color())
        self.top_rule.pack(fill=tk.X)
        # 窗口键：贴窗口右上角，先 pack 的先占最右，所以顺序是 关闭 → 最大化 → 最小化
        self.win_btns = {}
        for kind, cmd in (("close", self.close_app),
                          ("max", self.frame_ctl.toggle_max),
                          ("min", self.frame_ctl.minimize)):
            b = WindowButton(self.top, self.pal, kind, command=cmd,
                             base=self.pal["top"])
            b.pack(side=tk.RIGHT)
            self.win_btns[kind] = b
        # 应用名：图标 + 「素笺」。系统标题栏收起来之后，这枚图标就是界面上
        # 唯一还带应用标识的地方。左侧留白 12px —— 原来是按左缘那条 22px 折叠条
        # 让位的，折叠条撤掉后改成与左栏搜索框同一个内边距（SEARCH_PADX），
        # 上下两条左缘才在同一条竖线上。
        self.brand_icon_img = self._brand_photo(dpi.px(16))
        if self.brand_icon_img is not None:
            self.brand_icon = tk.Label(self.top, image=self.brand_icon_img,
                                       bg=self.pal["top"], bd=0)
            self.brand_icon.pack(side=tk.LEFT, padx=(SEARCH_PADX, 0))
            first_padx = dpi.px(6)
        else:
            self.brand_icon = None
            first_padx = SEARCH_PADX
        # 字号 9pt bold：10 → 9 → 8 试过一轮，8pt 时「素笺」两个字的横画会并成
        # 一团（SimHei / 雅黑在这个号下都没有内嵌点阵，Tk 只能靠灰阶凑），
        # 退回来定在 9pt；比它更小的动作交给图标那边去缩。
        self.brand = tk.Label(self.top, text=APP_NAME, bg=self.pal["top"],
                              fg=self.pal["text"], font=(FONT[0], 9, "bold"),
                              anchor="w")
        self.brand.pack(side=tk.LEFT, padx=(first_padx, 0))
        # 竖分割线：把「我是谁」（图标 + 应用名）和「能干什么」分成两段。
        # 用的是顶栏那条横分割线的同一份颜色（同样的压深 22%），一条横两条竖，
        # 粗细都是 1px、长度只占顶栏的一半 —— 是分隔符，不是边框。
        self.top_divider = tk.Frame(self.top, width=dpi.px(1), height=dpi.px(16),
                                    bg=self._top_rule_color())
        self.top_divider.pack(side=tk.LEFT, padx=(dpi.px(10), dpi.px(9)))
        # 侧栏开关：标题右侧一枚小图标（图形照 icon/sz.svg 折算）。
        # 原样＝点击收起左侧两栏，折叠后换成水平镜像＝点击展开。
        # 左缘那条折叠条撤掉后，这里就是**唯一**的收起/展开入口。
        self.side_toggle = SidebarToggle(self.top, self.pal,
                                         command=self.toggle_lists,
                                         base=self.pal["top"])
        self.side_toggle.pack(side=tk.LEFT)
        # 全局设置：紧跟侧栏开关的齿轮（图形照 icon/setting.svg 折算）。
        # 原来顶栏右侧那枚写着「设置」的胶囊按钮就挪到了这儿 —— 标题左边这排
        # 都是「看一眼整份笔记」的入口，右侧留给内容操作（新建笔记）和窗口键；
        # 一枚 20px 的小图标比一块 60px 的文字底片安静得多。功能完全不变。
        self.settings_btn = SettingsButton(self.top, self.pal,
                                           command=self.open_settings,
                                           base=self.pal["top"])
        self.settings_btn.pack(side=tk.LEFT, padx=(dpi.px(10), 0))
        # 数据管理：紧跟齿轮（图形照 icon/db.png 折算，数据库圆柱 + 右下一枚齿轮）。
        # 导出备份 / 导入数据 / 清空原本挤在设置弹窗下半部分，和「正文字体、字号」
        # 这类外观项混在一起，找起来得先想到「设置」；单独立一枚图标后，三件事
        # 一步直达。
        self.data_btn = DataButton(self.top, self.pal,
                                   command=self.open_data_manager,
                                   base=self.pal["top"])
        self.data_btn.pack(side=tk.LEFT, padx=(dpi.px(10), 0))
        # 关于：紧跟数据管理（图形照 icon/about.png 折算，圆圈 + i）。
        # 原本压在全局设置弹窗最底下，得先打开设置、再往下看才找得到；
        # 「代码在哪、这是谁做的」和导出/清空同属「关于这个应用」，各给一枚图标。
        self.about_btn = AboutButton(self.top, self.pal,
                                     command=self.open_about,
                                     base=self.pal["top"])
        self.about_btn.pack(side=tk.LEFT, padx=(dpi.px(10), 0))
        # 第二条竖分割线：把「看」的一排（折叠/设置/数据/关于）和「动手」的那一枚
        # 隔开。与第一条同宽同高同色，两条竖线因此是一套标点，而不是两个边框。
        self.create_divider = tk.Frame(self.top, width=dpi.px(1), height=dpi.px(16),
                                       bg=self._top_rule_color())
        self.create_divider.pack(side=tk.LEFT, padx=(dpi.px(10), dpi.px(9)))
        # 新建笔记：紧跟关于（图形照 icon/create.png 折算，记事本 + 铅笔），
        # 运行时染成强调色 —— 这排图标里只有它是「动手」的，其余都是「看」的。
        # 原来它是最右端那枚写着「新建笔记」的实心胶囊：文字按钮压在一条 30px
        # 高的图标带里最重、最占地方，右侧整块让给窗口键之后，主操作反而更靠前了。
        self.create_btn = CreateButton(self.top, self.pal,
                                       command=self.new_note,
                                       base=self.pal["top"])
        self.create_btn.pack(side=tk.LEFT)

        # 三栏
        body = tk.Frame(self.root, bg=self.pal["top"])
        body.pack(fill=tk.BOTH, expand=True)
        self._body = body

        self._lists_collapsed = False
        # 两栏宽度（像素）。_side_w / _list_w 是「用户想要的宽度」，持久化在
        # settings["side_w"] / ["list_w"]；_side_aw / _list_aw 是「这一刻真正生效的」——
        # 窗口被拖窄时会临时夹紧，但**不写回设置**：缩窗口这个动作不该顺手改了
        # 用户定的列宽，窗口再拉宽时列宽得原样回来。
        self._side_w = self._read_side_w()
        self._list_w = self._read_list_w()
        # 控件就是按上面这两个宽度建的，所以「已生效」一开始就等于它们；
        # 真正的夹紧交给第一次 <Configure>（那时才知道窗口多宽）。
        self._side_aw = self._side_w
        self._list_aw = self._list_w
        # 拖动分隔条期间为 True：名字 / 标题的按像素截断先跳过（见 _fit_folder_name）
        self._resizing = False
        self._sep_drag = None
        self._sep_lines = {}

        # 左：文件夹
        self.side = tk.Frame(body, width=self._side_w, bg=self.pal["side"])
        self.side.pack(side=tk.LEFT, fill=tk.Y)
        self.side.pack_propagate(False)
        # 搜索框：贴在文件夹列表的正上方，宽度跟着这一列走，左右留白 12px 与列表
        # 内容对齐 —— 上下同宽，侧栏才是一条直上直下的竖边。
        #
        # 画布的**父窗口是 root 而不是这里的 holder**：两栏折叠会把整列 pack_forget，
        # 搜索框跟着收起（place_forget），再展开时重新 place 回 holder 顶部。
        # holder 只负责占出这块位置和高度。
        self.search_holder = tk.Frame(self.side, bg=self.pal["side"], height=SEARCH_H)
        self.search_holder.pack(fill=tk.X, padx=SEARCH_PADX, pady=(dpi.px(8), dpi.px(4)))
        self.search_holder.pack_propagate(False)
        self.search_canvas = tk.Canvas(self.root,
                                       width=self._side_w - 2 * SEARCH_PADX,
                                       height=SEARCH_H, bd=0,
                                       highlightthickness=0, bg=self.pal["side"])
        self.search_canvas.place(in_=self.search_holder, x=0, y=0)
        self.search_var = tk.StringVar()
        # 一处入口管三件事：过滤列表、刷新占位文字、给正文里的命中词打高亮。
        # 以前这里是两三条 trace 各干各的，加「高亮」时很容易只挂到其中一条上，
        # 于是出现「列表滤了、正文没标」这种半生效状态。
        self.search_var.trace_add("write", lambda *a: self._on_search_change())
        self.search_ent = tk.Entry(self.search_canvas, textvariable=self.search_var, bd=0,
                                   bg=self.pal["editor"], fg=self.pal["text"],
                                   selectbackground=self.pal.get("sel", "#cfe0fb"),
                                   selectforeground=_sel_fg(self.pal),
                                   font=(FONT[0], 12), insertbackground=self.pal["accent"])
        # 占位文字：真实 Label（为什么不用画布文字图元，见 _style_search_box 注释）
        self.search_ph_lbl = tk.Label(self.search_canvas, text="搜索",
                                      bg=self.pal["editor"], fg=self.pal["soft"],
                                      font=(FONT[0], 12), anchor="w")
        self.search_ph_lbl.bind("<Button-1>",
                                lambda e: self.search_ent.focus_set())
        self._style_search_box()
        # 点到图标/圆角区域也能把焦点给输入框
        self.search_canvas.bind("<Button-1>", lambda e: (self.search_ent.focus_set(),
                                                         self._update_search_ph()))
        self.search_ent.bind("<FocusIn>", lambda e: self._update_search_ph())
        self.search_ent.bind("<FocusOut>", lambda e: self._update_search_ph())
        # 占位文字的显隐跟着 `_on_search_change` 一起刷新（不必再挂一条 trace）
        self.folder_panel = ScrollPanel(self.side, self.pal["side"], self.pal)
        self.folder_panel.pack(fill=tk.BOTH, expand=True, padx=dpi.px(8), pady=(dpi.px(2), dpi.px(10)))
        # 文件夹列底部不再摆「新建 / 删除文件夹」按钮：列表的空白处右键就是入口
        # （行上的右键仍是文件夹自己的菜单）。画布负责内容下方那大片留白，
        # inner 负责行与行之间的缝和分组标题以外的空隙。
        for _blank in (self.folder_panel.canvas, self.folder_panel.inner):
            _blank.bind("<Button-3>", self._folder_blank_popup)

        # 中：笔记列表（列间硬竖线分隔，直角硬朗）。两条竖线现在都是可拖的：
        # 5px 抓取带 + 居中的 1px 细线，指针压上去变色、按住左右拖改列宽、双击复位。
        self._vsep1 = self._build_splitter(body, "side")
        self._vsep1.pack(side=tk.LEFT, fill=tk.Y)
        self.list_frame = tk.Frame(body, width=self._list_w)
        self.list_frame.pack(side=tk.LEFT, fill=tk.Y)
        self.list_frame.pack_propagate(False)
        # 文件夹列宽度变化时让搜索框跟着对齐（它住在这一列顶部，宽度 = 列宽 - 留白）
        self.side.bind("<Configure>", lambda e: self._sync_search_width(), add="+")
        self.mid_head = tk.Label(self.list_frame, anchor="w", font=(FONT[0], 12, "bold"))
        self.mid_head.pack(fill=tk.X, padx=dpi.px(16), pady=(dpi.px(7), dpi.px(4)))
        self.note_panel = ScrollPanel(self.list_frame, self.pal["list"], self.pal)
        self.note_panel.pack(fill=tk.BOTH, expand=True)

        # 右：编辑区（列间硬竖线分隔）
        self._vsep2 = self._build_splitter(body, "list")
        self._vsep2.pack(side=tk.LEFT, fill=tk.Y)
        self.editor_frame = tk.Frame(body)
        self.editor_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        # 涂鸦标注层：临时模态，关闭即丢弃。蒙层盖的是**整个主窗口客户区**
        # （2026-11-06 用户要求覆盖整个界面：顶栏 / 侧栏 / 列表 / 稿纸 / 状态条
        # 一起进网点），窗口移动或缩放时按新几何重摆一次。折叠工具栏不改窗口
        # 大小，蒙层也用不着跟着动，所以只挂 root。
        self._doodle = None
        self.root.bind("<Configure>", lambda e: self._doodle_relayout(), add="+")
        # 窗口一改宽，先按当前窗口把两栏夹回可用范围（只夹紧、不写回设置）。
        # 放在这里是因为 side / list_frame 都已存在，夹紧时能立刻见到效果。
        body.bind("<Configure>", lambda e: self._on_body_resize(), add="+")
        # 分隔条拖动的「后半程」挂在顶层窗口上（原因见 _build_splitter）：指针拖到
        # 哪一栏上都能收到，处理函数里按 _sep_drag 判空放行，平时零成本。
        self.root.bind("<B1-Motion>", self._sep_motion, add="+")
        self.root.bind("<ButtonRelease-1>", self._sep_release, add="+")
        # 笔记行拖拽的后半程同理（原因见 _note_press）：拖拽一开始会新建一个浮签
        # 窗口，Windows 在焦点切换时会把 Tk 的本地 grab（SetCapture）收走，之后
        # 指针一跨到别的行上，行自己的 <B1-Motion> 就再也收不到了。挂在顶层上
        # 则走冒泡，跨行 / 跨栏 / 跨到编辑器都不会丢；`_drag` 为空时立即返回。
        self.root.bind("<B1-Motion>", self._drag_on_motion, add="+")
        self.root.bind("<ButtonRelease-1>", self._drag_on_release, add="+")
        # 工具栏：底色跟随皮肤侧栏色，与白色编辑区明确区分（bar 存成 self 以便换肤时重配色）
        bar = tk.Frame(self.editor_frame, height=TOOLBAR_H, bg=self.pal["side"])
        bar.pack(fill=tk.X)
        self.toolbar = bar
        self._build_toolbar(bar)
        # 工具栏与编辑区之间的分隔线。展开时只有 1px，颜色比工具栏底色更深一档，
        # 给这条工具带收个口；工具栏被折叠后它变成 6px 的窄条 —— 这是「重新展开」
        # 的唯一入口，因为折叠后工具栏上所有按钮（含 ⋯）都一起消失了。
        self._tool_bottom = tk.Frame(self.editor_frame, height=dpi.px(1), bg=self._tool_line())
        self._tool_bottom.pack(fill=tk.X)
        self._tool_bottom.bind("<Button-1>", lambda e: self._expand_toolbar())
        self._tool_bottom.bind("<Enter>", self._on_toolbar_strip_enter, add="+")
        self._tool_bottom.bind("<Leave>", self._on_toolbar_strip_leave, add="+")

        # 标签条：夹在工具带与稿纸之间。放在**工具带下面**而不是最顶上，是因为
        # 顶栏那 30px 已经是窗口自己的标题区，标签再顶一层就成了两条并列的横条；
        # 而活动标签底色 = 稿纸色，紧贴着下面的白纸，看上去就是「这张纸被抽出来」。
        self._tabs = []             # 打开着的笔记 id，按打开顺序
        self._tab_active = None
        # 多标签关掉时 self.tabbar 为 None（标签条不存在这件事，全类都按「可能没有」处理）
        self.tabbar = None
        if self._multi_tab_enabled():
            self.tabbar = self._make_tabbar()
            self.tabbar.pack(fill=tk.X)
        self._tabs_restore()

        # 只读提示条：打开内置示例笔记或未解锁的加密笔记时出现，夹在工具带与稿纸之间。
        # 没有它的话，用户在这篇笔记里打不出字只会以为是程序卡了。
        self._readonly = False
        self._ro_kind = "sample"
        self._ro_flash_job = None
        self._ro_restoring = False
        # 载入笔记期间为 True：这段时间编辑器由程序改动，不算用户编辑（见 load_editor）
        self._loading = False
        # ⚠️ 底色别用 pal["hover"]（列表行悬停色）：它和未选中标签只差 6/255，
        # 两条灰带会连成一片。改用主题色的淡纸片，见 _ro_bar_bg。
        ro = self._ro_bar_bg()
        self._ro_bar = tk.Frame(self.editor_frame, height=READONLY_BAR_H,
                                bg=ro)
        self._ro_label = tk.Label(self._ro_bar,
                                  text="内置示例笔记 · 内容不可编辑（文字可以选中复制）",
                                  bg=ro,
                                  fg=self.pal.get("soft", "#76767e"),
                                  font=(FONT[0], 10), anchor="w")
        self._ro_label.pack(side=tk.LEFT, padx=TEXT_PADX + 2)
        # 加密笔记未解锁时多一个入口：直接在提示条上点「输入口令」，
        # 不必再去右键菜单里找一遍
        self._ro_action = tk.Label(self._ro_bar, text="输入口令 →",
                                   bg=ro,
                                   fg=self._ro_accent_fg(), font=(FONT[0], 10, "bold"),
                                   anchor="w", cursor="hand2")
        self._ro_action.bind("<Button-1>", lambda e: self._unlock_current())
        # 带恢复槽的加密笔记再给一条常驻入口：不必先打开口令框、也不必去右键
        # 菜单里翻。has_recovery 为假时隐藏 —— 没有槽却给个必定失败的按钮更糟。
        self._ro_recovery = tk.Label(self._ro_bar, text="忘记口令？用恢复码 →",
                                     bg=ro,
                                     fg=self._ro_accent_fg(),
                                     font=(FONT[0], 10, "bold"),
                                     anchor="w", cursor="hand2")
        self._ro_recovery.bind("<Button-1>", lambda e: self._recover_current())

        # 自动换行是全局视图开关（settings["editor_wrap"]）：默认不换行，点工具栏
        # 那枚 ↵ 才切到 wrap=CHAR；再点回 NONE。⚠️ 必须是 CHAR 而不是 WORD：
        # Tk 的 word wrap 只认半角空格，一整段没空格的汉字算「一个词」，放不下时
        # 整块被推到下一显示行、上一行留一大截空白（同一段话复制两遍最明显：
        # 第二遍从行首重新起排）。CHAR 按字符断，一行占满才折。
        # ⚠️ 软换行只是显示效果，正文里一个换行符都不会多 ——
        # 内容 / 序列化 / 备份全不受影响。
        self.text = tk.Text(self.editor_frame, bd=0, padx=TEXT_PADX, pady=TEXT_PADY,
                            wrap=(tk.CHAR if self._editor_wrap_on() else tk.NONE),
                            undo=True,
                            font=(self.ui_font or FONT[0], self.body_size),
                            spacing1=dpi.px(4), spacing2=dpi.px(8), spacing3=dpi.px(4),
                            highlightthickness=0, insertbackground=self.pal["accent"],
                            selectbackground=self.pal.get("sel", "#cfe0fb"),
                            inactiveselectbackground=self.pal.get("sel2", "#e0e9f7"),
                            selectforeground=_sel_fg(self.pal), exportselection=False)
        # 底部外边距 FOOTER_GAP(8) 补回被 TEXT_PADY 让出去的那 8px：状态条改挂
        # editor_frame 后整条浮在「8px 外边距 + 12px 下留白」里，不遮最后一行字。
        self.text.pack(fill=tk.BOTH, expand=True, pady=(0, FOOTER_GAP))
        # ---------- 左侧行号栏 ----------
        # 独立一块 Canvas 贴在正文左边（不是 Text 里那种行号控件，Tk 没有）。
        # 一列**等距**的数字：一格的高度 = 正文当前字号的行高（`_gutter_metrics`
        # 量出来的 unit），每 unit 像素一个号往下数，空行和末尾的空白区都有号。
        # ⚠️ 不要改回「按每行的 dlineinfo 贴齐」：标题、内嵌代码块的行高与 unit
        # 差很多，贴齐的结果是 63 / 29 / 67 / 18×9 这种毫无规律的间距（用户否掉）。
        # 创建在 text 之后、pack 时必须 before=text，否则会跑到正文右边去。
        self._gutter_font = None
        self._gutter_font_size = 0
        self._gutter_job = None
        self._gutter_phase = 0      # 刻度相位（见 _measure_gutter_phase）
        self._gutter_hover = None   # 当前高亮的画布图元 id（不是行号，见 _gutter_hit）
        self._gutter_lines = {}     # 显示号 → 画布图元 id（探针/调试用）
        # 每个数字占据的纵向区间：[(top, bottom, 逻辑行, 图元 id)]。
        # ⚠️ 不能用「行号 → 图元」的字典查命中：号是标尺数出来的，和逻辑行号
        # 不再一一对应（代码块那一格占 260px，底下几行的号就差了 9），按号查会错位。
        self._gutter_hit = []
        # 查找 / 替换浮条：懒创建（不按 Ctrl+F、Ctrl+H 就永远不占内存），这里只占个位
        self._find_bar = None
        self._find_hits = []
        self._find_idx = 0
        self._find_job = None
        self._replace_mode = False     # 替换行是否展开（Ctrl+H）
        self.gutter = tk.Canvas(self.editor_frame, bd=0, highlightthickness=0,
                                bg=self.pal["editor"], width=dpi.px(30))
        self.gutter.bind("<Button-1>", self._on_gutter_click)
        self.gutter.bind("<Button-3>", self._gutter_menu)
        self.gutter.bind("<Motion>", self._on_gutter_motion, add="+")
        self.gutter.bind("<Leave>", lambda e: self._gutter_hover_set(None), add="+")
        self.gutter.bind("<Configure>", lambda e: self._gutter_schedule(), add="+")
        self.gutter.bind("<MouseWheel>", self._on_gutter_wheel)
        self.gutter.pack(side=tk.LEFT, fill=tk.Y, before=self.text)
        self._apply_line_numbers(redraw=False)
        self._config_tags()
        self.text.bind("<<Modified>>", self.on_edit)
        self.text.config(state=tk.DISABLED)
        self.text.bind("<Return>", self._editor_return)
        # 常用文字样式快捷键。工具栏提示里写了 Ctrl+B/I/U，这里必须真的绑上——
        # 而且要让工具栏敢精简，高频操作得能离手完成。实例绑定优先于 Text 的
        # 类绑定（Ctrl+B 原本是 emacs 风格的「光标左移」），返回 "break" 阻止继续传播。
        for _key, _tag in (("b", "bold"), ("i", "italic"), ("u", "underline")):
            self.text.bind("<Control-%s>" % _key,
                           lambda e, t=_tag: (self.toggle_sel_tag(t), "break")[1])
        self.text.bind("<Control-Shift-S>",
                       lambda e: (self.toggle_sel_tag("strike"), "break")[1])
        # 行级样式：Ctrl+1 / Ctrl+2 一级、二级标题，Ctrl+0 回到正文。
        # 作用于光标所在行，不用先选中；编号与「H」下拉里的顺序一致，好记。
        for _key, _tag in (("1", "title"), ("2", "h2"), ("0", "body")):
            self.text.bind("<Control-Key-%s>" % _key,
                           lambda e, t=_tag: (self.apply_line_tag(t), "break")[1])
        # 插入类：Ctrl+Shift+K 代码块（K 取自 code 的末字母，避开与复制同名的 Ctrl+Shift+C）、
        # Ctrl+Shift+T 待办（T = todo）
        self.text.bind("<Control-Shift-K>",
                       lambda e: (self.insert_code(), "break")[1])
        self.text.bind("<Control-Shift-T>",
                       lambda e: (self.apply_list_kind("check"), "break")[1])
        # 整行操作：Ctrl+D 复制当前行、Ctrl+Y 删当前行（有选区则作用于整块）。
        # Ctrl+Y 原本挂在重做上：删整行是天天要用几十次的动作，重做是偶尔一次，
        # 让高频的占走近的那组键；重做回到 Tk 默认的 Ctrl+Shift+Z（下面另有绑定，
        # 别的程序里 Ctrl+Y 的重做习惯要委屈一下）。
        self.text.bind("<Control-d>", lambda e: (self.duplicate_line(), "break")[1])
        self.text.bind("<Control-y>", lambda e: (self.delete_line(), "break")[1])
        # 重做：Ctrl+Y 让给「删除整行」之后，Ctrl+Shift+Z 是唯一的重做入口。
        # Tk 默认已把这条绑到 <<Redo>>，这里再绑一次并 break 只是把意图写清楚。
        self.text.bind("<Control-Shift-Z>",
                       lambda e: (self._redo_shortcut(), "break")[1])
        # 只读笔记的输入拦截。注意 <Key> 是通配绑定，会被上面这些更具体的单键绑定
        # 覆盖（Tk 每个 bindtag 只执行最具体的那条），所以格式化入口必须在各自方法里
        # 再挡一次；这里挡住的是普通输入、删除、粘贴这一大片。
        self.text.bind("<Key>", self._readonly_key_guard, add="+")
        for _seq in ("<<Paste>>", "<<Cut>>", "<<Clear>>", "<<Undo>>", "<<Redo>>",
                     "<<PasteSelection>>"):
            self.text.bind(_seq, self._readonly_clip_guard, add="+")
        # Ctrl+F：本篇内查找（浮出查找框，不动左栏列表）。
        # 必须绑在**实例**上并 return "break" —— Text 的类绑定把 Ctrl+F 接成了
        # emacs 的「光标右移」，类绑定在 toplevel/all 之前跑，不在这里拦下来，
        # 全局那条 bind_all 永远轮不到。
        self.text.bind("<Control-f>", self._find_open)
        # Ctrl+H：同一张浮条展开替换行。Text 的类绑定把 Ctrl+H 接成 emacs 的
        # 「删除光标前一个字符」，不在这里绑实例 + break，按下去会先删一个字。
        self.text.bind("<Control-h>", self._replace_open)
        # ---------- markdown 输入快捷 ----------
        # 敲完 "# " / "- " 这类块级前缀，敲空格那一刻就地转成已有的样式。
        # 这里**不能** return "break"：要让 Tk 先把空格插进去，idle 时才去看行首，
        # 否则判断时永远差一个字符。真正的转换在 _md_after_space 里的 idle 回调。
        self.text.bind("<space>", self._md_after_space, add="+")
        # `@ 关联笔记`：同一个 <space> 上再挂一路 —— 光标前是 `@` 就浮出笔记候选。
        # 两路互不干扰：上面那条只看行首的 markdown 前缀，这条只看光标前的 `@`。
        self.text.bind("<space>", self._mention_after_space, add="+")
        # 候选面板开着时 ↑ ↓ Esc 归它（不开时这些绑定直接放行，不影响光标移动）；
        # 点正文别处、滚轮、光标移出触发词时收起 —— 见 _mention_refilter
        for _seq in ("<Up>", "<Down>", "<Escape>"):
            self.text.bind(_seq, self._mention_key, add="+")
        # 查找框开着时，焦点在正文里按 Esc 也要能关掉（候选面板没开时它才生效，
        # 上面那条 _mention_key 先跑、它会 break 掉候选面板那一支）
        self.text.bind("<Escape>", self._find_esc, add="+")
        for _seq in ("<Left>", "<Right>", "<Home>", "<End>",
                     "<Prior>", "<Next>", "<MouseWheel>"):
            self.text.bind(_seq, self._mention_recheck, add="+")
        self.text.bind("<Button-1>", lambda e: self._mention_close(), add="+")
        # Ctrl+E 行内代码。Tk 的类绑定里 Ctrl+E 是 emacs 的「移到行尾」，
        # 不 break 会先跳光标再执行，必须盖掉（同 Ctrl+F 那次的坑）。
        self.text.bind("<Control-e>", lambda e: (self.toggle_icode(), "break")[1])
        # Ctrl+Shift+- 分割线。部分 Tk 版本把 Shift+minus 上报成 underscore，
        # 两条序列都绑；一次按键只会命中更具体的那条，不会插出两条线。
        for _seq in ("<Control-Shift-minus>", "<Control-underscore>"):
            self.text.bind(_seq, lambda e: (self.insert_hr(), "break")[1])
        # Ctrl+K 链接（Tk 类绑定里是 emacs 的「删除到行尾」，同样必须 break）
        self.text.bind("<Control-k>", lambda e: (self.toggle_link(), "break")[1])
        # 链接：点击打开、悬停变手型（tag_bind 只影响带 link 标签的文字）
        self.text.tag_bind("link", "<Button-1>", self._on_link_click)
        self.text.tag_bind("link", "<Enter>",
                           lambda e: self.text.configure(cursor="hand2"))
        self.text.tag_bind("link", "<Leave>",
                           lambda e: self.text.configure(cursor="xterm"))
        # 悬停在链接上浮出「地址可编辑」小窗（富链接才有；裸网址显示的就是地址本身）
        self._link_tip_tag = None
        self._link_tip_span = None      # 悬停裸网址时记下它的范围（富链接用 tag）
        self._link_tip_job = None
        self._link_tip_show_job = None
        self._link_editor = None
        self._bind_link_tip()
        # 编辑区宽度变化 → 代码块重新对齐宽度并重算高度（否则换行行数会算错），
        # 横向细条也按新宽度决定显隐（窗口拉宽到放得下长行就自动收起）。
        self.text.bind("<Configure>", lambda e: self._sync_code_width(), add="+")
        self.text.bind("<Configure>", lambda e: self._sync_hscroll(), add="+")
        # Shift+滚轮横向浏览长行：不换行时鼠标滚轮仍归正文上下滚，别抢。
        self.text.bind("<Shift-MouseWheel>", self._on_shift_wheel)
        sy = tk.Scrollbar(self.editor_frame, command=self.text.yview)
        self._place_vscroll(sy)
        self._vscroll = sy
        # 横向细条：与竖向那条同款系统细条，只在 wrap=NONE + 内容超宽时 place 出来。
        self._xview_span = (0.0, 1.0)
        self._hscroll = tk.Scrollbar(self.editor_frame, orient=tk.HORIZONTAL,
                                     command=self.text.xview, width=HSCROLL_H)
        # 正文滚动时同步滚动条 + 浮动导航（回顶部/回底部）的显隐与可用端
        self.text.config(yscrollcommand=self._on_text_scroll,
                         xscrollcommand=self._on_text_xscroll)
        # 右侧居中的浮动导航：浮在正文之上，只在这篇能滚时才出现
        self.jump_nav = ScrollJumpNav(self.editor_frame, self.pal,
                                      command=self._jump_scroll, base=self.pal["editor"])
        self._jump_placed = False
        self._jump_job = None
        self._sync_jump_nav()
        # 底部状态条：创建时间 / 修改时间 / 字数。
        # 只在指针挪到**正文底部**那一带才浮出来 —— 常驻的话，阅读时永远有
        # 一条灰条压着最后一行；一进正文就浮出来也一样，等于没隐藏。
        self._footer = self._build_footer()
        self._footer_job = None
        self._footer_visible = False
        # <Enter> 只在「指针从外面跨进正文」时来一次：指针本来就在正文里、
        # 一路挪到底部，是一句 Enter 都收不到的。所以两条都挂同一个按坐标判断的
        # 处理函数 —— 进来时正好在底部、和进来后再挪到底部，走的是一条路径。
        self.text.bind("<Enter>", self._on_footer_motion, add="+")
        self.text.bind("<Leave>", self._schedule_footer_hide, add="+")
        self.text.bind("<Motion>", self._on_footer_motion, add="+")
        # 指针扫过待办行（方框**和它领的那句话**）时换成小手，其余地方保持文本
        # 光标。挂在全局 Motion 上而不是 tag_bind：tag 只管得到方框那几个像素，
        # 而「这一整条都是待办」才是用户的心理模型；反过来，待办行行尾之后的
        # 空白不属于任何字，那里仍然要是普通光标。
        self._hover_cursor = "xterm"
        self.text.bind("<Enter>", self._on_todo_hover, add="+")
        self.text.bind("<Motion>", self._on_todo_hover, add="+")
        # ---------- 拖选守卫 ----------
        # Text 的类绑定把 <B1-Motion> 直接接在 tk::TextSelectTo 上，而它**不检查**
        # 「这次拖拽有没有在正文里按下过」：只要内部那份拖选锚点 mark 存在、当前点
        # 与锚点不同，就 tag remove sel + tag add sel 锚点→当前点 刷一遍。
        # 切笔记时 delete("1.0","end") 会让锚点（gravity=left）塌到 1.0，于是
        # 「在标签条/工具栏上按下、手指顺势滑进正文」那一下，选区就从文章开头算起 ——
        # 指针在正文中下部，看起来就是整篇被选中；更糟的是这时一打字，Tk 会用新
        # 字符替换整个选区（0.6s 后落库），是真的会丢内容。
        # 实例绑定先于类绑定，标志位为假就 break 掉，TextSelectTo 根本不会执行。
        self._text_pressed = False
        self.text.bind("<Button-1>", self._on_text_press, add="+")
        # 兜底：点到「有方框字符、却没有 todo 标签」的位置也要能打钩（粘贴 / 撤销 /
        # 早期数据留下的裸方框）。排在 _on_text_press 之后：先记按下标志，再判方框。
        self.text.bind("<Button-1>", self._on_click_untagged_todo, add="+")
        self.text.bind("<ButtonRelease-1>", self._on_text_release, add="+")
        self.text.bind("<B1-Motion>", self._on_text_drag)
        # 同一条链上还挂着 <B1-Leave> → tk::TextAutoScan：按住鼠标拖出正文时它每 50ms
        # 自己再调一次 TextSelectTo（用的还是 Priv(x)/Priv(y) 那组坐标），于是「从标签条
        # 拖进来、一路拖出正文底部」这条路径绕过了上面的 motion 守卫，选区又从 1.0 算起。
        # 这里补一道同样的判定；没在正文里按下的，连自动滚动的 repeat 也一并取消。
        self.text.bind("<B1-Leave>", self._on_text_drag_leave)
        # ---------- 竖向（矩形）选择 = notepad++ 的列模式 ----------
        # Alt + 左键拖动（Ctrl+Alt 也认）。模型照抄 notepad++：矩形 = **行区间 ×
        # 列区间**（`_col`），而不是「一串屏幕行像素段」—— 高亮、复制、打字、删除
        # 全部由这一份数据派生，看到的和拿到的才是同一块。
        #   · 拖动：_colsel（临时态）→ 算出 _col → _col_paint() 刷高亮
        #   · 松手：_colsel 清掉，**_col 留着**（这是「选完之后不消失」的关键）
        #   · 打字：列插入（每行同一列插同一个字）/ Backspace·Delete：删列
        #   · 取消：单击正文、Esc、导航键、新选区、切笔记
        self._colsel = None
        self._col = None
        # Tk 的 <<Copy>> 只取 sel.first→sel.last 那一段连续文本，矩形必须自己算。
        self.text.bind("<<Copy>>", self._on_copy, add="+")
        # 全选会让 sel 覆盖整篇，再叠一块矩形就说不清了 → 清。
        self.text.bind("<<SelectAll>>", self._colsel_drop, add="+")
        # 粘贴 / 剪切：列模式下改成「列粘贴 / 列剪切」（notepad++ 同款）。
        self.text.bind("<<Paste>>", self._col_paste, add="+")
        # 粘贴进来的是**裸字符**：剪贴板只有文字、没有标签，粘出来的 ⬜ / ✅ 既小
        # 又点不动（点击切换的 tag_bind 挂在 todo 上）。粘完补一遍标签。
        self.text.bind("<<Paste>>", self._on_paste_fix_todo, add="+")
        self.text.bind("<<PasteSelection>>", self._on_paste_fix_todo, add="+")
        self.text.bind("<<Cut>>", self._col_cut, add="+")
        # 键盘：可打印字符 = 列插入，Backspace / Delete = 列删除，其余键退出列模式。
        # ⚠️ 通用 <Key> 收不到「有更具体绑定」的那些键（见 _on_escape 的注释），
        # 所以 <space>（markdown 转换 / @ 候选已占）、<Return>、方向键 这些必须
        # 各自再挂一条，否则列模式一按空格就漏。
        self.text.bind("<Key>", self._col_key, add="+")
        self.text.bind("<space>", self._col_space, add="+")
        self.text.bind("<Return>", self._col_drop_pass, add="+")
        for _seq in ("<Up>", "<Down>", "<Left>", "<Right>",
                     "<Home>", "<End>", "<Prior>", "<Next>"):
            self.text.bind(_seq, self._col_drop_pass, add="+")
        # 取消的快捷键。必须单独绑 <Escape>：同一 bindtag 上只要有一条更具体的
        # 按键绑定（上面 @ 候选面板挂的那条）匹配上，Tk 就不再走通用的 <Key> 了。
        # 注册顺序在候选面板之后：面板开着时 Esc 先关面板，再按一次才清列模式。
        self.text.bind("<Escape>", self._on_escape, add="+")
        # 普通选区一出现就让列模式作废（两者语义互斥，复制只能有一个来源）。
        # <<Selection>> 在 Tk 8.6 上对 Text 有效，鼠标拖选、双击、Shift+点击、
        # Ctrl+A、甚至别处代码 tag_add("sel") 都能兜住。
        self.text.bind("<<Selection>>", self._on_sel_change, add="+")
        # ⚠️ 这里**没有** <FocusOut> 那条：列模式是「编辑状态」，不是「临时高亮」。
        # 上一版挂过，结果点工具栏、Alt 键引起的焦点抖动都会把刚选好的列弄没 ——
        # 用户报的「选中之后会自动消失」就是这个。要取消就点一下正文或按 Esc。
        # ---------- 列模式的「每行一根光标」 ----------
        # 光标条是 place 出来的真窗口，坐标只在摆放那一刻算过一次。窗口一改尺寸
        # 软换行就重排，每行的屏幕位置跟着变，条必须重摆（滚动同理，见 _on_text_scroll）。
        self.text.bind("<Configure>", self._col_caret_relayout, add="+")
        self._install_col_insert_hook()
        # 恢复上次退出时的工具栏折叠状态。remember=False：这是「读配置」，不该反过来写库。
        self._tool_collapsed = False
        if self._tool_cfg().get("collapsed"):
            self._set_toolbar_collapsed(True, remember=False)
        self._bind_global_shortcuts()

        # 标题栏接线：顶栏空白、应用图标、应用名都能拖动窗口 / 双击最大化。
        # 顶栏上的按钮与侧栏开关不动 —— 它们各自要响应点击（Tk 的事件不会冒泡，
        # 绑定打在父 Frame 上影响不到子控件，天然互不打架）。
        drag_targets = [self.top, self.brand]
        if self.brand_icon is not None:
            drag_targets.append(self.brand_icon)
        self.frame_ctl.bind_drag(*drag_targets)
        self._start_window_frame()

    def _start_window_frame(self):
        """等布局与尺寸落定之后再接管标题栏，然后照旧最大化启动。"""
        self.root.update_idletasks()
        if self.frame_ctl.install():
            # 还原位置自己记：客户区被改写之后，Tk 的 normal geometry 会和实际
            # 尺寸对不上（直接还原会摊成一整屏），见 win_frame.set_restore_box
            ax, ay, aw, ah = work_area(self.root)
            w, h = min(1080, aw), min(700, ah)
            self.frame_ctl.set_restore_box(ax + (aw - w) // 2, ay + (ah - h) // 2, w, h)
            # 最大化 / 还原的状态交给窗口消息驱动，按钮永远跟真实状态走
            self.frame_ctl.set_on_zoom(self.win_btns["max"].set_maximized)
            self.frame_ctl.maximize()
            self.root.deiconify()
            # 上屏之后客户区才真正结算：先量一次「窗口 - 客户区」的实测偏差，
            # 再按它摆位（窗口还没 map 时 SetWindowPos 只改外框，客户区不跟）
            self.root.update_idletasks()
            self.frame_ctl.measure()
            self.frame_ctl.maximize()
        else:
            # 拿不到窗口过程（非 Windows，或被系统拒绝）：老老实实退回系统标题栏，
            # 那三枚自绘窗口键就不该摆出来凑数 —— 否则会出现两套关闭按钮
            for b in self.win_btns.values():
                b.pack_forget()
            self.root.deiconify()

    def close_app(self):
        """自绘关闭键：先把还在去抖里的编辑刷进库，再拆窗口过程、销毁窗口。

        系统关闭按钮原来直接 destroy —— 打完字 0.6 秒内点叉，最后那几句就没了。
        """
        try:
            self._flush_save()
        except Exception:
            pass
        self.frame_ctl.close()

    def _brand_photo(self, size):
        """顶栏的应用图标：取 `icon/px/<size>/app_icon.png`（预渲染的精确尺寸）。

        这张不再走「512 原图现场缩」：16px 那一档是生成脚本单独调过饱和度与对比度
        的（拟物图直接缩到 16px 只剩一团米色，铅笔认不出来），20/24/32/44 各有
        各的档，取到就是对的尺寸，一个像素都不用插值。
        """
        return _icon_photo(self.root, "app_icon.png", size)

    def _bind_global_shortcuts(self):
        """焦点不在正文里（比如正停在列表或侧栏）也要能用的快捷键。

        正文那几条绑在 Text 实例上、更早触发并 return "break"，所以这里用 bind_all
        兜底不会与之重复执行 —— 事件链是 widget > class > toplevel > all，
        前面的环节一旦 break 就轮不到 all。
        """
        # 基础快捷键
        pairs = (
            ("<Control-n>", self.new_note),        # 新建笔记
            ("<Control-s>", self._flush_save),     # 立刻落库（平时是输入停顿后自动存）
            ("<Control-comma>", self.open_settings),  # 设置（同多数软件的 Ctrl+,）
            ("<Control-f>", self._find_open),      # 本篇内查找
            ("<Control-h>", self._replace_open),   # 本篇内查找替换
        )
        # 多标签快捷键：**始终绑**，由各回调自己判 _multi_tab_enabled() 决定要不要动。
        # 不能在启动时按开关决定绑不绑 —— 开关能在设置里当场拨动，那样拨到「开」
        # 之后快捷键要重启才活（而且解绑 bind_all 会连带清掉别的绑定）。
        # 关着的时候按下去等于空操作，不会误伤别的功能。
        pairs += (
            ("<Control-Tab>", self._tab_next),         # 下一个标签
            ("<Control-Shift-Tab>", self._tab_prev),   # 上一个标签
            # 部分键盘布局把 Shift+Tab 报成 ISO_Left_Tab（Windows 的 Tk 不认这个
            # keysym，bind 会直接抛错——所以整包 try/except，各平台能挂几条挂几条）
            ("<Control-ISO_Left_Tab>", self._tab_prev),
            # Ctrl+PageUp/PageDown：同一件事的备用键。Windows 的 Tk 用 event_generate
            # 造不出 Ctrl+Tab（事件直接消失），真键盘没问题，但保不齐哪套键盘布局
            # 也吞它 —— 多挂一对肯定能到的键，浏览器也这么干
            ("<Control-Next>", self._tab_next),
            ("<Control-Prior>", self._tab_prev),
            ("<Control-w>", self._tab_close_active),   # 关掉当前标签
        )
        for seq, fn in pairs:
            try:
                # 涂鸦是临时模态：Ctrl+N / 查找 / 切标签等一律挡掉，否则会趁
                # 蒙层盖着的时候把正文换掉，退出时的快照就对不上了。
                # ⚠️ bind_all 不受 grab 影响，光靠 grab_set 拦不住这些键。
                self.root.bind_all(
                    seq,
                    lambda e, f=fn: ("break" if self.doodle_active()
                                    else (f(), "break")[1]))
            except Exception:
                pass
        # 涂鸦入口是唯一在模态里仍要响应的全局键：再按一次 = 退出。
        # 两个写法都试一遍（不同 Tk 版本对 Shift+D 的 keysym 说法不一致）。
        for seq in ("<Control-Shift-D>", "<Control-Shift-Key-D>"):
            try:
                self.root.bind_all(seq, lambda e: (self.toggle_doodle(), "break")[1])
                break
            except Exception:
                pass

    def _on_search_change(self):
        """搜索框内容一变就走这里：过滤列表 + 刷新占位文字 + 重打正文高亮。

        清空搜索（退格删光）同样走这条路径 —— 关键词没了，高亮自然
        整片撤掉，不需要额外的「取消搜索」入口。
        """
        self._update_search_ph()
        self.refresh_notes()
        self._apply_search_highlight()

    def _apply_search_highlight(self):
        """把搜索词在**当前打开的这篇**正文里标黄。

        只标编辑器里那篇：列表里几十条笔记的正文并不在内存里（加密的连明文都没有），
        要标全得把每条都解出来重排一遍，代价远大于收益。

        直接操作 tag、不进撤销栈：Tk 的 undo 只记 insert/delete，tag 增删不入栈
        （已实测），所以这里不必包 `edit_separator`，Ctrl+Z 撤的仍是用户的输入。
        """
        try:
            self.text.tag_remove("find", "1.0", tk.END)
        except Exception:
            return
        q = (getattr(self, "search_var", None).get() if hasattr(self, "search_var")
             else "").strip()
        if not q or not self.current_note():
            return
        start = "1.0"
        for _ in range(2000):          # 兜底：不给「一轮轮标下去」留死循环的可能
            try:
                pos = self.text.search(q, start, stopindex=tk.END, nocase=True)
            except Exception:
                return
            if not pos:
                return
            end = "%s+%dc" % (pos, len(q))
            try:
                self.text.tag_add("find", pos, end)
            except Exception:
                return
            start = end

    def _on_text_scroll(self, first, last):
        """Text 的 yscrollcommand 钩子：滚动条照旧，顺带更新浮动导航。"""
        try:
            self._vscroll.set(first, last)
        except Exception:
            pass
        # 行号跟着正文滚：它们不在同一个控件里，谁也不会替谁动
        self._gutter_schedule(0)
        self._sync_jump_nav(float(first), float(last))
        if self._col_active():
            # 光标条是按屏幕坐标 place 的，内容一滚它们就全不在原处了；滚出视口
            # 的行也不该再画（_col_caret_show 里按 y 过滤）。
            self._col_caret_show()

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
            # 竖向居中，停在**滚动条左侧**（滚动条占右缘 17px，胶囊不覆盖它 —— 它
            # 任何一列都得能拖）。正文右留白 25，滚动条吃掉 17 之后只剩 8px 空隙，
            # 26 宽的胶囊摆进去必然压住行尾那一两个字；这是「不挡滚动条」的必然代价，
            # 别为了躲字再往右挪 —— 那就又盖回滚动条上了。
            # rely=0.5 是对整个编辑列取中，正文上面还有工具栏 + 标签条 —— 补回
            # 「顶部那截的一半」，导航才落在**正文**的正中（top 为 0 时正好不偏）。
            self.jump_nav.place(relx=1.0, rely=0.5, anchor="e",
                                y=max(0, self.text.winfo_y() // 2),
                                x=-(VSCROLL_W + JUMP_GAP))
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

    # ---------- 左侧行号 ----------
    # 只做「看得见、点得到、能关掉」，不做断点/折叠之类的编辑器特权：
    # 这是笔记软件不是 IDE，行号的用处是「刚才那句在第几行」和「快速定位」。

    def _line_numbers_on(self):
        """行号开关落在 settings.line_numbers（默认开）。"""
        cfg = self.data.get("settings")
        if not isinstance(cfg, dict):
            return True
        return bool(cfg.get("line_numbers", True))

    def _apply_line_numbers(self, redraw=True):
        """按开关挂上 / 摘下行号栏。切换后正文宽度会变，必须让 Text 重排一次。"""
        g = getattr(self, "gutter", None)
        if g is None:
            return
        on = self._line_numbers_on()
        try:
            managed = bool(g.winfo_manager())
            if on and not managed:
                # 必须 before=text：正文早就 pack 好了，裸 pack 会排到它右边去
                g.pack(side=tk.LEFT, fill=tk.Y, before=self.text)
                g.lift()
            elif not on and managed:
                g.pack_forget()
        except Exception:
            pass
        if redraw:
            # 刚 pack 的控件要等几何管理落定才 ismapped，否则 _gutter_redraw 会
            # 因为「还没显示」直接返回，切开关后行号要等下次滚动才出现
            try:
                g.update_idletasks()
            except Exception:
                pass
            self._gutter_schedule(0)

    def set_line_numbers(self, on):
        """改行号开关并落库（去抖，和正文用同一条保存链路）。"""
        on = bool(on)
        if on == self._line_numbers_on():
            return
        self.data.setdefault("settings", {})["line_numbers"] = on
        self._apply_line_numbers()
        self._schedule_save()

    def _gutter_schedule(self, delay=8):
        """把重绘推迟到本轮事件之后：一次滚动会连来好几条 yscroll，合并成一次画。"""
        if not self._line_numbers_on():
            return
        if self._gutter_job is not None:
            self._cancel_after(self._gutter_job)
        self._gutter_job = self._after(delay, self._gutter_redraw)

    def _gutter_redraw(self):
        """重画行号：一列**等距**的数字，一格的高度 = 正文当前字号的行高。

        unit 由 `_gutter_metrics()` 量出来（正文普通行的行高，含段间距），数字
        每 unit 像素一个、从文档第一行往下连续数 —— 空行、正文末尾以下的空白
        区域照样有号。因此：

        * 行号列永远等距：敲字、插代码块、换字号都不会让某一段突然挤紧或拉开；
        * 滚动时整列跟着正文一起上下走，数字与正文的相对位置恒定；
        * 「一行」的口径是正文行高，不是某个控件自己的行距。

        代价（用户拍板接受）：标题（字号更大）和内嵌代码块（行距更紧、整块
        两百多像素高）那几行会偏十几像素 —— 它们的行距本来就不是 unit，
        「等距」和「逐行贴齐」只能选一个。
        """
        self._gutter_job = None
        g = getattr(self, "gutter", None)
        if g is None:
            return
        try:
            if not g.winfo_exists():
                return
        except Exception:
            return
        g.delete("all")
        self._gutter_lines = {}
        self._gutter_hit = []
        self._gutter_hover = None
        if not self._line_numbers_on() or not g.winfo_ismapped():
            return
        p = self.pal
        h = g.winfo_height()
        if h <= 1:
            return
        fam = getattr(self, "ui_font", None) or FONT[0]
        # 比正文小两档：行号是参考信息，和正文同号会把眼睛往左拽
        size = max(7, min(int(self.body_size) - 2, 13))
        if self._gutter_font is None or self._gutter_font_size != size:
            try:
                self._gutter_font = tkfont.Font(family=fam, size=size)
            except Exception:
                self._gutter_font = None
            self._gutter_font_size = size
        unit, base = self._gutter_metrics()
        unit = max(dpi.px(6), int(unit))
        # Tk 的 Canvas 文字没有 baseline 锚点，"se" 指的是**包围盒底边**（含
        # descender 那段空白）。直接把底边摆在基线上，数字会浮起 3~4px ——
        # 所以补一个 descent，让数字的字脚真正坐到基线上。
        try:
            desc = int(self._gutter_font.metrics("descent")) if self._gutter_font else 3
        except Exception:
            desc = 3
        try:
            total = int(str(self.text.index("end-1c")).split(".")[0])
        except Exception:
            total = 1
        # 整列的刻度以「文档第一行的顶端」为原点：先问出视口顶端离正文开头多少
        # 像素，反推出第一行的视口坐标 v0（往下滚就是负的）。滚动时数字才不乱跳。
        try:
            info0 = self.text.dlineinfo("@0,0")
            d = self.text.count("1.0", "@0,0", "ypixels")
        except Exception:
            return
        if not info0:
            return
        v0 = int(info0[1]) - (int(d[0]) if d else 0)
        # 让刻度「穿过多行」而不是钉死在第一行：标题那种大字号行会把正文往下推
        # 几十像素（实测 72px 一行），而 72 不是 unit 的整数倍 —— 整列数字就会
        # 统一偏高 12~13px。
        # ⚠️ 相位只在**文档顶端可见时**量一次并缓存：每次都按「当前第一条可见
        # 普通行」重算的话，滚动时各行残余像素略有不同，数字会整体跳一格（实测
        # 滚 6 行时同一行旁边的号从 17 变成 16）。缓存之后整列纹丝不动。
        try:
            info1 = self.text.dlineinfo("1.0")
        except Exception:
            info1 = None
        if info1:
            self._gutter_phase = self._measure_gutter_phase(v0, unit)
        v0 += int(getattr(self, "_gutter_phase", 0) or 0)
        k0, k1 = gutter_slots(v0, unit, h)
        # 栏宽**不跟内容走**：固定预留 GUTTER_DIGITS 位（见 widgets.gutter_width）
        unit_w = self._gutter_font.measure("0") if self._gutter_font else dpi.px(8)
        w = gutter_width(unit_w, max(total, k1 + 1))
        try:
            if int(float(g.cget("width"))) != int(w):   # cget 可能回来是 "30.0"
                g.configure(width=w)
        except Exception:
            pass
        # 右侧一道极淡的竖线把「行号」和「正文」分开。不能取 pal["line"]：
        # 它只比底色深 5%，在白色稿纸上等于不存在。
        g.create_line(w - dpi.px(1), 0, w - dpi.px(1), h,
                      fill=_mix_hex(p.get("editor", "#ffffff"),
                                    p.get("text", "#222222"), 0.12))
        for k in range(k0, k1 + 1):
            y = v0 + k * unit
            # 点这个号跳到「这个高度上的那一行」；空白段跳到最后一行
            try:
                ln = int(str(self.text.index("@0,%d" % int(max(0, y + unit // 2)))).split(".")[0])
            except Exception:
                ln = total
            self._gutter_number(g, w, y + base + desc, k + 1, y, y + unit, ln, p)

    def _measure_gutter_phase(self, v0, unit):
        """刻度相对「文档第一行顶端」的相位修正（像素，±unit/2）。

        第一行常常是标题：字号更大、段间距更宽（实测一行 72px），72 不是 unit
        的整数倍，于是整列数字会统一偏高十几像素。找第一行**普通行**（行高正好
        等于 unit），把刻度对齐到它的顶端 —— 代价只是整列挪不到半格。

        只在文档顶端可见时调用（见 `_gutter_redraw`），结果由调用方缓存。
        """
        try:
            y = 0
            prev = -(1 << 30)
            for _ in range(60):
                info = self.text.dlineinfo("@0,%d" % int(y))
                if not info:
                    break
                _x, yy, _w, hh, _b = info
                if yy <= prev:
                    break
                prev = yy
                if int(hh) == unit:
                    return int(yy - int(round((yy - v0) / float(unit))) * unit - v0)
                y = yy + max(1, hh)
        except Exception:
            pass
        return 0

    def _gutter_metrics(self):
        """量一格的 (行高, 基线偏移)。行高取正文普通行的行高，不取控件自己的。

        * unit：可见行里出现次数最多的行高。空行、普通文字都长这样，标题和内嵌
          代码块是少数；直接读字体 metrics 会漏掉 Tk 加的段间距（spacing1/3）
          和标签自带的 spacing，实测差 4px 以上。
        * base：这一行里基线相对行顶的偏移，数字就坐在这个基线上。

        结果按（正文字号, 字体）缓存 —— 每次滚动都去量八十行纯属白烧 Tcl 调用。
        """
        key = (self.body_size, str(self.text.cget("font")))
        if getattr(self, "_gutter_unit_key", None) == key:
            cached = getattr(self, "_gutter_unit_val", None)
            if cached:
                return cached
        try:
            total = int(str(self.text.index("end-1c")).split(".")[0])
        except Exception:
            total = 1
        cap = dpi.px(80)          # 比这还高的行不是「正常行」（代码块 / 大图）
        counts = {}
        bases = {}
        for ln in range(1, min(total, 80) + 1):
            try:
                info = self.text.dlineinfo("%d.0" % ln)
            except Exception:
                continue
            if not info:
                continue
            hh = int(info[3])
            if 4 <= hh <= cap:
                counts[hh] = counts.get(hh, 0) + 1
                bases.setdefault(hh, int(info[4]))
        if counts:
            unit = max(counts.items(), key=lambda kv: (kv[1], -kv[0]))[0]
            base = bases.get(unit, int(round(unit * 0.72)))
            self._gutter_unit_key = key
            self._gutter_unit_val = (unit, base)
            return unit, base
        # 一行都量不到（空笔记 / 还没映射）：按字体 + 段间距估，且**不缓存**，
        # 等真有字了再量准的
        try:
            f = tkfont.Font(family=getattr(self, "ui_font", None) or FONT[0],
                            size=int(self.body_size))
            unit = int(f.metrics("linespace"))
        except Exception:
            unit = dpi.px(19)
        for opt in ("spacing1", "spacing3"):
            try:
                unit += int(float(self.text.cget(opt) or 0))
            except Exception:
                pass
        unit = max(unit, dpi.px(12))
        return unit, int(round(unit * 0.72))

    def _gutter_number(self, g, w, ty, num, top, bottom, line, p):
        """画一个行号，并登记它占的纵向区间（悬停 / 点击都按区间查，不按号查）。"""
        item = g.create_text(w - dpi.px(7), ty, text=str(num), anchor="se",
                             font=self._gutter_font,
                             fill=p.get("soft", "#76767e"),
                             tags=("gnum", "gnum_%d" % num))
        self._gutter_lines[num] = item
        if bottom <= top:
            bottom = top + 1
        self._gutter_hit.append((int(top), int(bottom), line, item))
        return item

    def _gutter_line_at(self, y):
        """行号栏 y 处对应的 (目标行号, 图元 id)；落在数字区间外就给 (None, None)。

        区间是画的时候一并登记的（见 `_gutter_number`），不按数字反查 —— 号是
        标尺数出来的，一个号未必等于一个逻辑行。
        """
        return gutter_hit_at(self._gutter_hit, y)

    def _on_gutter_motion(self, e):
        _ln, item = self._gutter_line_at(e.y)
        if item is not None:
            self._gutter_hover_set(item)

    def _gutter_hover_set(self, item):
        """悬停的那个行号变主题色 —— 点之前就知道「会跳到哪一行」。

        参数是**画布图元 id**，不是行号：号是标尺数出来的，一个号未必等于一个
        逻辑行，按号找会亮错一条。
        """
        if item == self._gutter_hover:
            return
        old, self._gutter_hover = self._gutter_hover, item
        soft = self.pal.get("soft", "#76767e")
        acc = self.pal["accent"]
        if old is not None:
            try:
                self.gutter.itemconfigure(old, fill=soft)
            except Exception:
                pass
        if item is not None:
            try:
                self.gutter.itemconfigure(item, fill=acc)
            except Exception:
                pass

    def _on_gutter_click(self, e):
        ln, _item = self._gutter_line_at(e.y)
        if ln is None:
            # 语言条左侧、块与块之间的留白没有数字，也没有区间 —— 按正文行兜底，
            # 别让「点到行号栏空白」变成没反应。
            try:
                ln = int(str(self.text.index("@0,%d" % int(e.y))).split(".")[0])
            except Exception:
                return "break"
        try:
            self.text.mark_set("insert", "%d.0" % ln)
            self.text.see("%d.0" % ln)
            self.text.focus_set()
        except Exception:
            pass
        return "break"

    def _on_gutter_wheel(self, e):
        """滚轮落在行号栏上也要滚正文 —— 那里不是「别的控件」，是同一条正文的边。"""
        try:
            self.text.yview_scroll(int(-1 * (e.delta / 120)), "units")
        except Exception:
            pass
        return "break"

    def _gutter_menu(self, e):
        m = PopupMenu(self.root, self.pal)
        m.add("隐藏行号", command=lambda: self.set_line_numbers(False))
        try:
            m.popup(e.x_root, e.y_root)
        except Exception:
            pass
        return "break"

    # ---------- Ctrl+F 查找 / Ctrl+H 替换（只看当前打开的这篇） ----------
    # 与顶栏搜索是两件事：顶栏那个会**过滤左栏列表**、跨全部笔记搜；这个只在
    # 当前正文里找，不动列表、不落库。所以高亮标签也分开（见 FIND_HIT_TAG）。
    # 查找与替换是同一张浮条、同一份 `_find_hits`：Ctrl+F 收起替换行，Ctrl+H 展开。

    def _build_find_bar(self):
        """造查找 / 替换浮条（只造不挂）。

        ⚠️ 父容器必须是 `self.text` 而不是 `editor_frame`：editor_frame 顶上还压着
        工具栏、标签条、只读提示条，按它的坐标算 y=8 会正好糊在工具栏和「+」上。
        挂进 Text 里 place，坐标就是「正文可视区」的，而且和底部状态条一个路子
        （不随正文滚动，永远贴在可视区右上角）。

        两行共用**一张**浮条：Ctrl+F 只挂第一行，Ctrl+H 再 pack 出第二行。拆成
        两张浮窗就得维护两套命中高亮，还会出现「查找框关了、替换框还挂着」。
        """
        p = self.pal
        bar = tk.Frame(self.text, bg=p["editor"], bd=0,
                       highlightthickness=dpi.px(1),
                       highlightbackground=p.get("line", "#cfcfd6"))
        row1 = tk.Frame(bar, bg=p["editor"], bd=0)
        row1.pack(fill=tk.X)
        row2 = tk.Frame(bar, bg=p["editor"], bd=0)   # 先不 pack：Ctrl+H 才展开

        def mk_btn(parent, text, fn, tip, size=11, padx=4, pady=0,
                   side=tk.LEFT, right=2):
            """`side=tk.RIGHT` 时贴浮条右边缘。

            ⚠️ 两行的最右一枚（第一行 ✕ / 第二行「全部替换」）必须用**同一个**
            `right` 外边距，否则右边缘差几像素、看着就是没对齐。pack 用 side=RIGHT
            时**先 pack 的在最右**，所以调用顺序要倒过来（先 ✕ 再 ↑↓）。
            """
            b = tk.Label(parent, text=text, bg=p["editor"],
                         fg=p.get("soft", "#76767e"), cursor="hand2",
                         font=(FONT[0], size), bd=0,
                         padx=dpi.px(padx), pady=dpi.px(pady))
            if side == tk.RIGHT:
                b.pack(side=tk.RIGHT, padx=(0, dpi.px(right)))
            else:
                b.pack(side=tk.LEFT)
            b.bind("<Button-1>", lambda e, f=fn: (f(), "break")[1])
            b.bind("<Enter>", lambda e, w=b: w.configure(
                bg=_hover_bg(p["editor"]), fg=p["text"]))
            b.bind("<Leave>", lambda e, w=b: w.configure(
                bg=p["editor"], fg=p.get("soft", "#76767e")))
            self._add_tip(b, tip)
            return b

        # 输入框要「看得出是输入框」：原来的 entry 无边框、底色和浮条同色，
        # 空的时候整个框消失在背景里，只剩右边几枚按钮（用户反馈「不知道在哪输入」）。
        # 现在压一档底色 + 1px 描边 + 框内占位提示，焦点进来描边转主题色。
        field_bg = _mix_hex(p["editor"], p["text"], 0.05)
        field_line = _mix_hex(p["editor"], p["text"], 0.22)
        ph_fg = _mix_hex(field_bg, p.get("soft", "#76767e"), 0.9)

        def mk_entry(parent, var, hint):
            e = tk.Entry(parent, textvariable=var, width=18, bd=0,
                         relief="flat",
                         highlightthickness=dpi.px(1),
                         highlightbackground=field_line,
                         highlightcolor=p["accent"],
                         bg=field_bg, fg=p["text"],
                         insertbackground=p["accent"],
                         font=(FONT[0], 11))
            e.pack(side=tk.LEFT, padx=(dpi.px(9), dpi.px(4)), pady=dpi.px(6))
            # 占位文字做成 Entry 的子 Label：Tk 的 Entry 没有 placeholder，
            # 往 textvariable 里塞假文字会污染 `_find_var.get()`（查找词、计数、
            # 替换判断全读这个变量）。子窗口会被父窗口裁掉超出部分，窄框不会溢出。
            ph = tk.Label(e, text=hint, bg=field_bg, fg=ph_fg, bd=0,
                          font=(FONT[0], 10), cursor="xterm")
            ph._sujian_ph = True     # _restyle_find_bar 靠它认出来，别刷成浮条底色

            def sync(*_a):
                # ⚠️ 只按「有没有内容」判断，**不看焦点**：Ctrl+F / Ctrl+H 打开时
                # 焦点本来就在框里，若聚焦就藏提示，空框又会变回一滴墨水都没有，
                # 正好复现用户反馈的「不知道在哪输入」。和浏览器一致：一直显示到
                # 真正敲进第一个字符为止。
                try:
                    if var.get():
                        ph.place_forget()
                    else:
                        ph.place(x=dpi.px(5), rely=0.5, anchor="w")
                except Exception:
                    pass

            var.trace_add("write", sync)
            e.bind("<FocusIn>", lambda ev: (sync(), "break")[1], add="+")
            e.bind("<FocusOut>", lambda ev: (sync(), "break")[1], add="+")
            ph.bind("<Button-1>", lambda ev: (e.focus_set(), "break")[1])
            e._ph = ph
            sync()
            return e

        self._find_var = tk.StringVar()
        ent = mk_entry(row1, self._find_var, "查找内容")
        self._find_entry = ent
        self._find_count = tk.Label(row1, text="", bg=p["editor"],
                                    fg=p.get("soft", "#76767e"),
                                    font=(FONT[0], 9), bd=0)
        self._find_count.pack(side=tk.LEFT, padx=(0, dpi.px(6)))
        for txt, fn, tip in (("✕", self._find_close, "关闭（Esc）"),
                             ("↓", lambda: self._find_step(1), "下一个（Enter）"),
                             ("↑", lambda: self._find_step(-1), "上一个（Shift+Enter）")):
            # 倒序 + RIGHT：先 pack 的在最右，✕ 才会贴住浮条右边缘，
            # 和第二行「全部替换」在同一条竖线上。
            mk_btn(row1, txt, fn, tip, side=tk.RIGHT)

        # 第二行：替换输入框 + 两枚动作。留「替换为」三个字，否则空输入框
        # 和上面的查找框长得一模一样，用户分不清该往哪儿打字。
        tk.Label(row2, text="替换为", bg=p["editor"],
                 fg=p.get("soft", "#76767e"),
                 font=(FONT[0], 10), bd=0).pack(side=tk.LEFT, padx=(dpi.px(9), 0))
        self._replace_var = tk.StringVar()
        ent2 = mk_entry(row2, self._replace_var, "可留空")
        ent2.pack_configure(padx=(dpi.px(4), dpi.px(4)))
        self._replace_entry = ent2
        # 倒序 + RIGHT：「全部替换」最右（与第一行 ✕ 同一条竖线），「替换」在它左侧。
        # 「替换」的 right=0，靠两枚 Label 各自的内部 padx 撑开间距，和原来一样。
        mk_btn(row2, "全部替换", self._find_replace_all,
               "替换全部命中（Ctrl+Enter）", size=10, padx=7, pady=2,
               side=tk.RIGHT)
        mk_btn(row2, "替换", self._find_replace_one,
               "替换当前这一处（Enter）", size=10, padx=7, pady=2,
               side=tk.RIGHT, right=0)

        # Enter 下一条 / Shift+Enter 上一条，和浏览器一致；Esc 关掉
        for seq, delta in (("<Return>", 1), ("<KP_Enter>", 1)):
            ent.bind(seq, lambda e, d=delta: (self._find_step(d), "break")[1])
        ent.bind("<Shift-Return>", lambda e: (self._find_step(-1), "break")[1])
        # 替换框：Enter 替换当前并跳下一条，Shift+Enter 跳上一条，Ctrl+Enter 全替
        for seq, step in (("<Return>", 1), ("<KP_Enter>", 1)):
            ent2.bind(seq, lambda e, s=step: (self._find_replace_one(s), "break")[1])
        ent2.bind("<Shift-Return>",
                  lambda e: (self._find_replace_one(-1), "break")[1])
        ent2.bind("<Control-Return>",
                  lambda e: (self._find_replace_all(), "break")[1])
        # 两个输入框里都要能 Esc / 切模式。⚠️ Entry 的类绑定把 Ctrl+H 接成
        # 「删前一个字符」、Ctrl+F 接成光标右移，不绑实例 + break 就会误删字。
        for e in (ent, ent2):
            e.bind("<Escape>", lambda ev: (self._find_close(), "break")[1])
            e.bind("<Control-f>", lambda ev: (self._find_open(), "break")[1])
            e.bind("<Control-h>", lambda ev: (self._replace_open(), "break")[1])
        # 输入即搜（不需要回车）。关键词一变就把「当前命中」拨回第一条。
        self._find_var.trace_add("write", lambda *a: self._find_refresh(reset=True))
        self._find_bar = bar
        self._find_row2 = row2
        self._find_hits = []
        self._find_idx = 0
        self._find_job = None

    def _find_open(self, event=None, replace=False):
        """Ctrl+F：本篇内查找；`replace=True` 时（Ctrl+H）展开第二行做查找替换。"""
        if getattr(self, "_find_bar", None) is None:
            self._build_find_bar()
        bar = self._find_bar
        if not bar.winfo_manager():
            # 贴在正文右上角。往左让开 18px 是给右侧浮动导航和滚动条留的位
            bar.place(relx=1.0, x=-dpi.px(18), y=dpi.px(10), anchor="ne")
        bar.lift()
        # 两行共用一张浮条，切模式只 pack / pack_forget 第二行：命中状态、关键词、
        # 高亮全部留着，不会「从查找切到替换就跳回第一条」。
        if replace:
            if not self._find_row2.winfo_manager():
                self._find_row2.pack(fill=tk.X)
        else:
            self._find_row2.pack_forget()
        self._replace_mode = bool(replace)
        # 窗口窄的时候把输入框收短：查找条是「贴在正文右上角」的，宽过正文就会被
        # place 裁掉左半边（关键词和计数一起看不见）。18 字符是宽松时的默认值；
        # 130 / 190 是两行「除输入框以外」的固定开销（第一行计数 + 三枚按钮，
        # 第二行再加「替换为」+ 两枚文字按钮），少算就会在最小窗口上压出去。
        try:
            avail = self.text.winfo_width() - dpi.px(18)
            overhead = dpi.px(190 if replace else 130)
            chars = max(6, min(18, int((avail - overhead) / float(dpi.px(9)))))
            self._find_entry.configure(width=chars)
            self._replace_entry.configure(width=chars)
        except Exception:
            pass
        # 选中了一个词再按 Ctrl+F / Ctrl+H → 直接把它填进去，少一次输入
        sel = ""
        try:
            sel = self.text.get("sel.first", "sel.last")
        except Exception:
            sel = ""
        if sel and "\n" not in sel and len(sel) <= 80:
            self._find_var.set(sel)
        try:
            bar.update_idletasks()
        except Exception:
            pass
        # 替换模式下关键词已经填好了就直接落在替换框，连 Ctrl+H 带选词一步到位；
        # 否则先填查找词，Tab 也能按顺序走到替换框。
        if replace and self._find_var.get():
            self._replace_entry.focus_set()
            self._replace_entry.select_range(0, tk.END)
        else:
            self._find_entry.focus_set()
            self._find_entry.select_range(0, tk.END)
        self._find_refresh()
        return "break"

    def _replace_open(self, event=None):
        """Ctrl+H：查找替换。走同一张浮条，只是多展开一行。"""
        return self._find_open(event, replace=True)

    def _find_close(self):
        bar = getattr(self, "_find_bar", None)
        if bar is None:
            return "break"
        try:
            bar.place_forget()
            self._find_row2.pack_forget()
        except Exception:
            pass
        self._replace_mode = False
        if getattr(self, "_find_job", None) is not None:
            self._cancel_after(self._find_job)
            self._find_job = None
        try:
            self.text.tag_remove(FIND_HIT_TAG, "1.0", tk.END)
            self.text.tag_remove(FIND_CUR_TAG, "1.0", tk.END)
        except Exception:
            pass
        self._find_hits = []
        self._find_idx = 0
        try:
            self.text.focus_set()
        except Exception:
            pass
        return "break"

    def _find_refresh(self, reset=False):
        """重扫全文、重贴高亮。

        直接操作 tag、不进撤销栈（Tk 的 undo 只记 insert/delete），所以不必包
        edit_separator —— Ctrl+Z 撤的仍是用户的输入，不会变成「撤掉一次高亮」。
        """
        bar = getattr(self, "_find_bar", None)
        if bar is None or not bar.winfo_manager():
            return
        if reset:
            self._find_idx = 0
        self._find_hits = []
        try:
            self.text.tag_remove(FIND_HIT_TAG, "1.0", tk.END)
            self.text.tag_remove(FIND_CUR_TAG, "1.0", tk.END)
        except Exception:
            return
        q = self._find_var.get() if getattr(self, "_find_var", None) else ""
        if not q:
            self._find_count.configure(text="")
            return
        start = "1.0"
        for _ in range(5000):        # 兜底：不给「一轮轮标下去」留死循环的可能
            try:
                pos = self.text.search(q, start, stopindex=tk.END, nocase=True)
            except Exception:
                break
            if not pos:
                break
            end = "%s+%dc" % (pos, len(q))
            try:
                self.text.tag_add(FIND_HIT_TAG, pos, end)
            except Exception:
                break
            self._find_hits.append((pos, end))
            start = end
        if not self._find_hits:
            self._find_count.configure(text="无结果")
            return
        self._find_idx = max(0, min(self._find_idx, len(self._find_hits) - 1))
        self._find_highlight_current()

    def _find_highlight_current(self):
        """把「当前命中」换成醒目底色并滚进视口，同时更新 3/12 计数。"""
        if not self._find_hits:
            return
        pos, end = self._find_hits[self._find_idx]
        try:
            self.text.tag_remove(FIND_CUR_TAG, "1.0", tk.END)
            self.text.tag_add(FIND_CUR_TAG, pos, end)
            self.text.see(pos)
        except Exception:
            return
        self._find_count.configure(
            text="%d/%d" % (self._find_idx + 1, len(self._find_hits)))

    def _find_step(self, delta):
        """Enter / ↑ / ↓：在命中之间循环。没有命中就先重扫一次。"""
        if not self._find_hits:
            self._find_refresh(reset=True)
            return "break"
        self._find_idx = (self._find_idx + int(delta)) % len(self._find_hits)
        self._find_highlight_current()
        return "break"

    # ---------- 查找替换（Ctrl+H） ----------
    def _find_has_object(self, pos, end):
        """命中范围里有没有嵌入窗口 / 图片（代码块、闹钟图标）。

        `text.search` 找的是字符，正常不会命中这些元素，但真撞上了也不能
        delete —— 那会把 serialize() 认领的非文本元素连根拔掉，重开笔记就少一块。
        """
        try:
            for kind, _val, _idx in self.text.dump(
                    pos, end, window=True, image=True,
                    text=False, tag=False, mark=False):
                if kind in ("window", "image"):
                    return True
        except Exception:
            pass
        return False

    def _find_replace_ready(self):
        """替换前的统一守卫；返回关键词，不能替换就返回 None。

        只读（内置示例 / 未解锁的加密笔记）走 `_refuse_readonly()` 闪提示条；
        没选中笔记时正文是 DISABLED，delete/insert 会直接抛，也在这里挡掉。
        """
        if self._refuse_readonly():
            return None
        if not self.current_note():
            self._find_count.configure(text="没有打开的笔记")
            return None
        q = self._find_var.get() if getattr(self, "_find_var", None) else ""
        if not q:
            return None
        self._find_refresh()          # 正文可能刚被编辑过，旧索引已经指偏了
        if not self._find_hits:
            self._find_count.configure(text="无结果")
            return None
        try:
            if str(self.text.cget("state")) == "disabled":
                return None
        except Exception:
            pass
        return q

    def _find_pick_near(self, anchor, step):
        """重扫后把「当前命中」拨到 anchor 之后（step>0）/ 之前（step<0）的第一条。"""
        if not self._find_hits:
            return 0
        try:
            if step > 0:
                for i, (a, _b) in enumerate(self._find_hits):
                    if self.text.compare(a, ">=", anchor):
                        return i
                return 0                      # 到尾了就绕回第一条
            for i in range(len(self._find_hits) - 1, -1, -1):
                if self.text.compare(self._find_hits[i][0], "<", anchor):
                    return i
            return len(self._find_hits) - 1
        except Exception:
            return 0

    def _find_swap(self, pos, end, repl):
        """就地换一段字符：删掉命中、在原位插回替换串（两侧样式标签不动）。"""
        self.text.delete(pos, end)
        if repl:
            self.text.insert(pos, repl)

    def _find_edit_group(self, fn):
        """把一组 delete/insert 收进**一个** undo 单元，返回 fn 的结果。

        ⚠️ Tk 默认 autoseparators=True，会在 delete 与 insert 之间自动插一条撤销
        边界（两者命令类型不同）。实测这时 `edit_separator()` 也压不住：一次
        Ctrl+Z 只撤掉插入，被删的词留在地上（'cat' → ''，半个词没了）。所以
        这一组操作期间临时关掉自动分组、手工在首尾各打一条边界。
        """
        old = self.text.cget("autoseparators")
        self.text.configure(autoseparators=False)
        try:
            self.text.edit_separator()
            return fn()
        finally:
            self.text.edit_separator()
            self.text.configure(autoseparators=old)

    def _find_replace_one(self, step=1):
        """替换当前这一处，然后跳到下一条（Shift+Enter 跳上一条）。"""
        q = self._find_replace_ready()
        if q is None:
            return "break"
        repl = self._replace_var.get() if getattr(self, "_replace_var", None) else ""
        idx = max(0, min(self._find_idx, len(self._find_hits) - 1))
        pos, end = self._find_hits[idx]
        if self._find_has_object(pos, end):
            return "break"
        try:
            if len(self.text.get(pos, end)) != len(q):
                return "break"
            self._find_edit_group(lambda: self._find_swap(pos, end, repl))
        except Exception:
            return "break"
        self.on_edit()                # <<Modified>> 可能在 idle 才回调，这里主动收口
        anchor = self.text.index("%s +%dc" % (pos, len(repl)))
        self._find_idx = idx
        self._find_refresh()
        # 替换串里含查找词时（a → aa），重扫后不能还指回刚替换的那一处，
        # 否则连按 Enter 会一直在原地打转。
        self._find_idx = self._find_pick_near(anchor, step)
        self._find_highlight_current()
        return "break"

    def _find_replace_all(self):
        """替换全部命中：按 `replacement_plan` 从后往前改，前面的索引才不漂移。"""
        q = self._find_replace_ready()
        if q is None:
            return "break"
        repl = self._replace_var.get() if getattr(self, "_replace_var", None) else ""
        hits = list(self._find_hits)
        try:
            # 传的是**序号**区间而不是 "行.列" 字符串：纯函数里的重叠判断用
            # 数值比较，字符串比较会把 "1.9" 判成比 "1.10" 还靠后。
            plan = replacement_plan([(i, i + 1) for i in range(len(hits))], repl)

            def do_all():
                done = 0
                for i, _end, text in plan:
                    pos, end = hits[i]
                    if self._find_has_object(pos, end):
                        continue
                    if len(self.text.get(pos, end)) != len(q):
                        continue
                    self._find_swap(pos, end, text)
                    done += 1
                return done

            done = self._find_edit_group(do_all)
        except Exception:
            return "break"
        self.on_edit()
        self._find_idx = 0
        self._find_refresh(reset=True)
        self._find_count.configure(text=("已替换 %d 处" % done) if done else "无结果")
        # 让「已替换 N 处」停一会儿再换回真实命中数；期间改关键词会提前刷新
        if getattr(self, "_find_job", None) is not None:
            self._cancel_after(self._find_job)
        self._find_job = self._after(1200, self._find_refresh)
        return "break"

    def _restyle_find_bar(self):
        """换肤时重配查找 / 替换浮条的控件色（关键词与命中范围保持不动）。

        ⚠️ 加了两行之后 bar 的直接子级是 row1 / row2 两个 Frame，只遍历一层
        会一个控件都刷不到 —— 必须递归。
        """
        bar = getattr(self, "_find_bar", None)
        if bar is None:
            return
        p = self.pal
        # 输入框底色 / 描边 / 占位字色都是从当前皮肤推出来的，换肤必须一起重算，
        # 否则浅色皮肤下会留一块旧的深色方块。
        field_bg = _mix_hex(p["editor"], p["text"], 0.05)
        field_line = _mix_hex(p["editor"], p["text"], 0.22)
        ph_fg = _mix_hex(field_bg, p.get("soft", "#76767e"), 0.9)

        def walk(w):
            for c in w.winfo_children():
                if isinstance(c, tk.Entry):
                    c.configure(bg=field_bg, fg=p["text"],
                                insertbackground=p["accent"],
                                highlightbackground=field_line,
                                highlightcolor=p["accent"])
                elif getattr(c, "_sujian_ph", False):
                    # 占位 Label 是 Entry 的子级，顺序上排在 Entry 之后处理，
                    # 必须盖住它、用输入框自己的底色。
                    c.configure(bg=field_bg, fg=ph_fg)
                elif isinstance(c, tk.Frame):
                    c.configure(bg=p["editor"])
                elif isinstance(c, tk.Label):
                    c.configure(bg=p["editor"], fg=p.get("soft", "#76767e"))
                walk(c)

        try:
            bar.configure(bg=p["editor"],
                          highlightbackground=p.get("line", "#cfcfd6"))
            walk(bar)
        except Exception:
            pass

    def _find_esc(self, event=None):
        """焦点在正文里按 Esc：查找框开着就关掉，没开就放行（候选面板还占着它）。"""
        bar = getattr(self, "_find_bar", None)
        if bar is not None and bar.winfo_manager():
            return self._find_close()
        return None

    def _find_on_edit(self):
        """正文改动后重扫：命中位置会移动，不重扫就会指到旧字符上。

        只重算不重置下标 —— 用户一边改一边看「还有几个」，突然跳回第一条更烦。
        打字时每次按键都会走到这里，所以去抖 60ms：一次输入连着改十几个字符，
        没必要每改一个就全文重扫一遍。
        """
        bar = getattr(self, "_find_bar", None)
        if bar is None or not bar.winfo_manager():
            return
        if getattr(self, "_find_job", None) is not None:
            self._cancel_after(self._find_job)
        self._find_job = self._after(60, self._find_refresh)

    # ---------- 底部状态条：创建时间 / 修改时间 / 字数 ----------
    # 为什么浮在正文上、还要「鼠标移到底部才出现」：
    #   - 常驻一条灰条会压在最后一行正文下面，阅读时很碍眼；
    #   - 单独占一行布局又要吃掉 26px 高度，而且窗口矮的时候更要命；
    #   - 「指针一进正文就浮出来」也不行 —— 那等于没隐藏，视线里始终有一条。
    # 所以做成贴在编辑区底部的浮层：指针挪进底部热区才浮出来，离开即收起。

    def _footer_bg(self):
        """状态条底色：**主题色的沉一档**（稿纸压深 + accent 染色）。

        推导在 theme._status_bar_bg：换主题时色相跟着 accent 走，同时自动与
        顶部「淡纸片」提示条、未选中标签拉开色差。状态条是浮层，得先有一条
        看得见的边界，再谈文字 —— 文字色也是按这块底的反差动态提深的。
        """
        return _status_bar_bg(self.pal)

    def _top_rule_color(self):
        """顶栏下沿那条分割线：顶栏色压深 22%。

        皮肤里 top 和 side 是同一个值，pal["line"] 又只比 top 深 5%（1px 上几乎
        看不见）。22% 是实测的落点：1x 下明确是一条界线，又不至于像描了黑边。
        """
        top = self.pal.get("top") or "#ececec"
        return _mix_hex(top, _ink_target(top), 0.22)

    def _build_footer(self):
        bg = self._footer_bg()
        fg = self._foot_fg()
        # ⚠️ 父级是 editor_frame，不是 Text：place 的子窗口超出 Text 底边会被父窗口
        # 裁掉（实测），而状态条 20px 要跨「Text 下留白 12 + pack 外边距 8」两段。
        # 挂在外层才完整显示；水平范围仍按 Text 内容区算（见 _show_footer）。
        f = tk.Frame(self.editor_frame, bg=bg, height=FOOTER_H)
        # 上沿细线从状态条自己的底色再压一档：pal["line"] 只深 5%，1px 上等于没画
        self._foot_rule = tk.Frame(f, height=dpi.px(1), bg=self._foot_rule_bg())
        self._foot_rule.pack(fill=tk.X)
        row = tk.Frame(f, bg=bg)
        row.pack(fill=tk.BOTH, expand=True)
        self._foot_row = row
        # pady/borderwidth 归零：状态条只有 20px，1px 分隔线占掉 1 后行区 19px，
        # 而 Label 默认的 2px pady + 2px border 会把 9pt 字撑到 23px，字底被裁。
        # 实测归零后 reqheight = 17，留 2px 余量；换肤改色不会覆盖这两个选项。
        self._foot_left = tk.Label(row, text="", bg=bg, fg=fg,
                                   font=(FONT[0], 9), anchor="w",
                                   pady=0, borderwidth=0)
        self._foot_left.pack(side=tk.LEFT, padx=(TEXT_PADX - 6, 0))
        self._foot_right = tk.Label(row, text="", bg=bg, fg=fg,
                                    font=(FONT[0], 9), anchor="e",
                                    pady=0, borderwidth=0)
        self._foot_right.pack(side=tk.RIGHT, padx=(0, dpi.px(16)))
        # 指针从正文移进这条状态条时不该把它收起来（否则永远读不到、也点不到）
        for w in (f, row, self._foot_left, self._foot_right):
            w.bind("<Enter>", self._cancel_footer_hide, add="+")
            w.bind("<Leave>", self._schedule_footer_hide, add="+")
        return f

    def _pointer_at_bottom(self, e):
        """指针是否落在编辑区底部的热区里（按相对正文左上角的 y 算）。"""
        y = getattr(e, "y", None)
        if y is None:
            return True                     # 拿不到坐标的事件按「在底部」处理
        try:
            h = self.text.winfo_height()
        except Exception:
            return False
        return h > 0 and (h - y) <= FOOTER_HOT_H

    def _on_footer_motion(self, e=None):
        """指针进正文 / 在正文里移动：只有挪到**底部**才浮出状态条。

        已经浮着时只把「收起」计时撤掉 —— 字数只在内容变化时才变，而内容变化
        必然走 _flush_active_row 里那条 220ms 去抖刷新，不必跟着指针移动重算
        （长笔记每次移动都全量数一遍字符是白花的开销）。
        """
        if not self._pointer_at_bottom(e):
            # 指针在正文上半部分：状态条该收起来（本来就藏着时这句是空转）
            if self._footer_visible:
                self._schedule_footer_hide()
            return
        if self._footer_visible:
            self._cancel_footer_hide()
            return
        self._show_footer()

    def _show_footer(self, _e=None):
        self._cancel_footer_hide()
        f = getattr(self, "_footer", None)
        if f is None:
            return
        self._refresh_footer()
        try:
            # ⚠️ 状态条现在是 editor_frame 的子窗口（见 _build_footer），place 相对
            # 外层：anchor="sw" + rely=1.0 让**底边**贴在编辑区底部，整条从底往上
            # 占 FOOTER_H(20) —— 正好是「8px pack 外边距 + 12px 下留白」，不碰正文。
            # x/width 仍按 Text 内容区算，避开左侧行号栏、也不压右侧滚动条。
            # ⚠️ 别改回挂成 Text 的子窗口：那样超出 Text 底边的 8px 会被父窗口
            # clip，状态条下半截凭空消失（实测）。
            tx = self.text.winfo_x() + TEXT_PADX
            tw = max(dpi.px(40), self.text.winfo_width() - 2 * TEXT_PADX)
            f.place(x=tx, rely=1.0, anchor="sw", width=tw, height=FOOTER_H)
            tk.Misc.lift(f)
            self._footer_visible = True
            # 状态条占了底部 20px：不换行时的横向细条上抬，别被盖住
            self._place_hscroll()
        except Exception:
            pass

    def _hide_footer(self):
        self._footer_job = None
        self._footer_visible = False
        f = getattr(self, "_footer", None)
        if f is not None:
            try:
                f.place_forget()
            except Exception:
                pass
        self._place_hscroll()

    def _cancel_footer_hide(self, _e=None):
        if self._footer_job is not None:
            try:
                self._cancel_after(self._footer_job)
            except Exception:
                pass
            self._footer_job = None

    def _schedule_footer_hide(self, _e=None):
        # 列模式期间这条状态条是唯一的「你还在列模式」提示，别自动收起来。
        if self._col_active():
            return
        self._cancel_footer_hide()
        self._footer_job = self._after(220, self._hide_footer)

    def _stamp(self, ts):
        try:
            return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts) / 1000.0))
        except Exception:
            return "—"

    def _char_count(self):
        """字数：正文 + 代码块里除空白以外的字符数。

        中文语境下「字数」就是字符数，不是英文那种按空格切词的 word count；
        空白（含换行、缩进）不计，否则代码块里的大片缩进会把字数吹得很离谱。
        """
        try:
            s = self.text.get("1.0", "end-1c")
        except Exception:
            s = ""
        for blk in getattr(self, "_codes", []):
            try:
                s += blk.snapshot()
            except Exception:
                pass
        return len(re.sub(r"\s+", "", s))

    def _refresh_footer(self):
        left = getattr(self, "_foot_left", None)
        if left is None:
            return
        n = self.current_note()
        if not n:
            left.configure(text="")
            self._foot_right.configure(text="")
            return
        col = getattr(self, "_col", None)
        if col is not None:
            # 列模式常驻提示：打字后矩形会变成「零宽列光标」，屏幕上一点高亮都没有，
            # 不写清楚的话用户不知道自己还在列编辑里，下一串字又会被逐行插进去。
            left.configure(text="列模式　%d 行 × %d 列　·　打字逐行插入　·　Esc 退出"
                           % (len(col["rows"]), col["c1"] - col["c0"]))
            return
        left.configure(text="创建 %s　·　修改 %s" % (
            self._stamp(n.get("created")), self._stamp(n.get("updated"))))
        sealed = bool(n.get("sec")) and not n.get("content")
        self._foot_right.configure(
            text=("已加密 · 未解锁" if sealed else "{:,} 字".format(self._char_count())))

    def _place_vscroll(self, sy):
        """编辑区竖向滚动条从**正文顶部**开始（原先是整列高度，会压住工具栏右端的滑动箭头）。

        顶部偏移不写死 37：上面摆了什么（工具栏、标签条、折叠后的 6px 窄条）都以
        正文自己的 y 为准 —— 加标签条时这行没跟着改，滚动条就会压住标签条右端的「+」。
        """
        def place(_e=None):
            if self._layout_txn:   # 事务期间跳过，事务结束后统一调一次
                return
            try:
                y = self.text.winfo_y()
                h = self.editor_frame.winfo_height() - y
            except Exception:
                return
            if h > 40:
                sy.place(relx=1.0, y=y, width=VSCROLL_W, height=h, anchor="ne")
        self._vscroll_place_fn = place
        self.editor_frame.bind("<Configure>", place, add="+")
        place()

    # ---------- 正文自动换行（全局视图开关） ----------
    def _editor_wrap_on(self):
        """读 settings["editor_wrap"]：缺省 / 脏数据一律「不换行」（首次默认）。

        这是全局视图状态，不是每篇笔记的属性：切笔记 / 切标签都不变，重启后由
        这一项还原上次选择。只认真布尔 True，老库没有这一项就是默认不换行。
        """
        s = self.data.get("settings")
        return isinstance(s, dict) and s.get("editor_wrap") is True

    def _wrap_tip_text(self, on):
        return ("自动换行：已开启，点一下关闭" if on else
                "自动换行：已关闭，点一下开启（长行可横向滚动）")

    def toggle_editor_wrap(self):
        self.set_editor_wrap(not self._editor_wrap_on())

    def set_editor_wrap(self, on):
        """切换正文软换行。

        wrap=CHAR 由 Tk 按 Text 当前宽度实时重排：窗口拉宽 / 字号放大，折行位置
        自己跟着补上，不需要往正文里插换行符，也不需要在这里手动重排文本。
        ⚠️ 别改回 WORD：Tk 只在空格处断词，一长串汉字是一个「词」，会整块掉到
        下一行；同一段文字复制两次时表现为「第二遍从行首重新起排」。
        切回 NONE 则是一行到底，长行交给横向细条 + Shift+滚轮浏览。
        """
        on = bool(on)
        s = self.data.get("settings")
        if not isinstance(s, dict):
            s = {}
            self.data["settings"] = s
        s["editor_wrap"] = on
        save_data(self.data)
        try:
            self.text.configure(wrap=tk.CHAR if on else tk.NONE)
            self.text.xview_moveto(0.0)
        except Exception:
            return
        b = getattr(self, "_wrap_btn", None)
        if b is not None:
            try:
                b.set_selected(on)
            except Exception:
                pass
        tip = getattr(self, "_wrap_tip", None)
        if tip is not None:
            try:
                tip.set_text(self._wrap_tip_text(on))
            except Exception:
                pass
        self._sync_hscroll()
        # 显示行数变了：行号刻度 / 浮动导航 / 代码块宽度 / 列模式光标条按新布局重算
        try:
            self._gutter_schedule(0)
            self._sync_jump_nav()
            self._sync_code_width()
            if self._col_active():
                self._col_caret_show()
        except Exception:
            pass

    # ---------- 不换行时的横向滚动 ----------
    def _on_text_xscroll(self, first, last):
        self._xview_span = (float(first), float(last))
        self._place_hscroll()

    def _sync_hscroll(self):
        """窗口尺寸 / 切换换行后主动问一次 xview，别只等 Tk 的 xscrollcommand。"""
        try:
            first, last = self.text.xview()
        except Exception:
            return
        self._xview_span = (float(first), float(last))
        self._place_hscroll()

    def _place_hscroll(self, _e=None):
        """横向细条只在「不换行 + 真有超宽长行」时浮在正文底沿。

        状态条浮出时会占掉底部 FOOTER_H，那条一出现就把横条上抬，别互相盖住。
        """
        sb = getattr(self, "_hscroll", None)
        if sb is None or getattr(self, "_layout_txn", False):
            return
        first, last = getattr(self, "_xview_span", (0.0, 1.0))
        if self._editor_wrap_on() or (last - first) >= 0.999:
            if sb.winfo_ismapped():
                sb.place_forget()
            return
        try:
            x = self.text.winfo_x()
            w = max(dpi.px(40), self.text.winfo_width() - VSCROLL_W)
            y = (self.text.winfo_y() + self.text.winfo_height() - HSCROLL_H
                 - (FOOTER_H if getattr(self, "_footer_visible", False) else 0))
        except Exception:
            return
        sb.place(x=x, y=y, width=w, height=HSCROLL_H)
        tk.Misc.lift(sb)
        sb.set(first, last)

    def _on_shift_wheel(self, e):
        if self._editor_wrap_on():
            return None
        try:
            # Text 没有 xscrollincrement 选项，按可视区比例走一档最稳（不依赖
            # 「units 到底多少像素」这个 Tk 版本差异）。
            first, _last = self.text.xview()
            step = 0.08
            self.text.xview_moveto(max(0.0, min(1.0,
                first + (-step if e.delta > 0 else step))))
        except Exception:
            pass
        return "break"

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
        # 圆角底：fill 与 Entry 同色 → 输入框与底无缝；描边用分隔线色。
        # 底用 editor 白（而不是列表底色）：搜索框现在躺在侧栏灰底上，
        # 只有纯白 + 描边才看得出「这里可以打字」，与下方文件夹卡片同理。
        self._search_bg_item = c.create_polygon(
            _round_rect_points(1, 1, W - 1, H - 1, dpi.px(9), 6),
            fill=p["editor"], outline=p["line"], width=dpi.px(1))
        # 放大镜：圆环 + 右下手柄（Tk 不能用 emoji 字符，直接画）
        icx, icy, r = dpi.px(19), dpi.px(14), dpi.fx(5.5)
        c.create_oval(icx - r, icy - r, icx + r, icy + r,
                      outline=p["soft"], width=dpi.px(2))
        c.create_line(icx + r * 0.72, icy + r * 0.72,
                      icx + r + dpi.fx(4.0), icy + r + dpi.fx(4.0),
                      fill=p["soft"], width=dpi.px(2), capstyle=tk.ROUND)
        # 图标与输入区之间的分隔竖线：把要输入的关键字和放大镜隔开
        c.create_line(dpi.px(34), dpi.px(8), dpi.px(34), H - dpi.px(8),
                      fill=p["line"], width=dpi.px(1))
        # 输入框摆在分隔线右侧（place 按像素定位、垂直居中，右侧留白给圆角）
        self.search_ent.place(in_=c, x=dpi.px(42), rely=0.5, anchor="w",
                              width=W - dpi.px(56))
        # 占位文字跟随换色；可见性（place/place_forget）交给 _update_search_ph
        self.search_ph_lbl.configure(bg=p["editor"], fg=p["soft"])
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
            c.coords(item, *_round_rect_points(1, 1, W - 1, H - 1, dpi.px(9), 6))
        self.search_ent.place_configure(x=dpi.px(42), rely=0.5, anchor="w",
                                        width=W - dpi.px(56))
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
                self.search_ph_lbl.place(in_=self.search_canvas, x=dpi.px(43),
                                         rely=0.5, anchor="w")
                self.search_ph_lbl.lift()   # 压在输入框上层，真实控件叠放稳定
            else:
                self.search_ph_lbl.place_forget()
        except Exception:
            pass

    def _place_search_box(self):
        """安放搜索框：它只属于文件夹列表顶部。

        两栏折叠会把整列 pack_forget —— 搜索框跟着一起收起（place_forget），
        顶栏不留任何残件：折叠的语义就是「把左侧两栏整片收干净，编辑区独占窗口」。
        再展开时按原位置摆回 holder 顶部。
        """
        try:
            if self._lists_collapsed:
                # 焦点还在已经收起的输入框里的话，键击仍会写进 search_var 去过滤列表 ——
                # 画面上却什么都看不见。收起前先把焦点交还给主窗口。
                try:
                    if self.root.focus_get() is self.search_ent:
                        self.root.focus_set()
                except Exception:
                    pass
                self.search_canvas.place_forget()
            else:
                self.search_canvas.place(in_=self.search_holder, x=0, y=0)
                self._update_search_ph()      # 占位文字随之恢复（已聚焦/有词则仍隐藏）
        except Exception:
            pass

    def _sync_search_width(self):
        """搜索框宽度跟着**文件夹列**走 —— 它就住在这一列的顶部。

        折叠时整列被 pack_forget、搜索框也已 place_forget，没有宽度可跟，直接跳过。
        """
        if self._layout_txn or self._lists_collapsed:
            return        # 事务期间不联动，结束后由 _end_layout_txn 统一刷
        try:
            w = self._widget_width(self.side) - 2 * SEARCH_PADX
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

    # ---------- 列宽拖拽（左侧两栏可手动调） ----------
    def _build_splitter(self, parent, which):
        """列间分隔条：5px 的抓取带 + 居中的 1px 细线。

        为什么不用 1px 的 Frame 直接当抓手：1px 在 125% 缩放下几乎点不中，
        鼠标得像素级瞄准。可点区域放到 5px，多出来的 4px 用父容器底色盖着
        （父容器与左栏同色，所以看不出来），细线本身仍是 1px —— 粗细语言不变。

        which = "side"（左栏/文件夹）或 "list"（笔记列表）。
        """
        # 抓取带的底色取**左边那一栏**的颜色：这样多出来的 4px 里有一半直接融进
        # 左栏（看不出来），剩下一半压在右栏上，被中间那条 1px 线一挡，读起来像
        # 「分隔线带一点左栏的色调」，而不是一条 5px 的异色柱子。
        base = self.pal["side"] if which == "side" else self.pal["list"]
        wrap = tk.Frame(parent, width=SEP_HIT_W, bg=base,
                        cursor="sb_h_double_arrow")
        line = tk.Frame(wrap, width=dpi.px(1), bg=self.pal["line"],
                        cursor="sb_h_double_arrow")
        line.place(x=SEP_HIT_W // 2, y=0, relheight=1.0, width=dpi.px(1))
        self._sep_lines[which] = line
        # 1px 的细线压在抓取带正中间，鼠标最容易落到的就是它 —— 两个都绑，
        # 否则「正好点在线上」反而拖不动。
        #
        # ⚠️ 只绑「按下」。**B1-Motion / Release 必须绑在顶层窗口上**：鼠标一离开
        # 这 5px 就落在别的控件（列表、正文）上，事件只发给指针底下的那个控件；
        # 想靠 grab_set 把事件拉回来是不行的 —— Tk 的 grab 只放行抓取窗口子树里的
        # 事件，指针一旦移出这 5px，运动事件直接被丢弃（实测：一下 motion 都收不到，
        # 松开时列宽纹丝不动）。顶层窗口在**每个控件的 bindtags 里**，所以绑它才收得全。
        for w in (wrap, line):
            w.bind("<Button-1>", lambda e, k=which: self._sep_press(e, k))
            w.bind("<Double-Button-1>", lambda e, k=which: self._sep_reset(e, k))
            w.bind("<Enter>", lambda e, k=which: self._sep_hover(k, True))
            w.bind("<Leave>", lambda e, k=which: self._sep_hover(k, False))
        return wrap

    def _sep_hover(self, which, on):
        """悬停时把细线染成强调色 —— 这是「这里能拖」的唯一提示。"""
        ln = self._sep_lines.get(which)
        if ln is None:
            return
        try:
            ln.configure(bg=_mix_hex(self.pal["line"], self.pal["accent"], 0.45)
                         if on else self.pal["line"])
        except Exception:
            pass

    def _split_limits(self, which):
        """当前这一栏能拖到多宽（min, max）。

        上限受两头夹：常量上限，以及「编辑区至少留 EDITOR_MIN」——
        窗口就 1080 时右栏最多能到 1080-216-10-320 出头，再宽正文就没地方站了。
        """
        lo, hi = (SIDE_MIN, SIDE_MAX) if which == "side" else (LIST_MIN, LIST_MAX)
        try:
            total = self._body.winfo_width()
        except Exception:
            total = 0
        if total <= 1:
            return lo, hi
        other = self._list_aw if which == "side" else self._side_aw
        room = total - other - 2 * SEP_HIT_W - EDITOR_MIN
        return lo, max(lo, min(hi, int(room)))

    def _apply_split_widths(self, side=None, list_w=None):
        """把（想要 / 给定）的列宽夹进可用范围并落到控件上，返回是否真的改了。

        只改控件宽度，**不写设置**：拖动时每帧都会走到这里，写库交给松手那一次。
        显式给进来的值视为「用户这一次的意图」，**先夹紧再记下**——否则一口气
        拖出屏幕外会记成 side_w = -504 这种值（虽然读回来还会再夹一次，但存进
        库里的数字得是能看懂的那个）。
        """
        changed = False
        lo, hi = self._split_limits("side")
        ws = max(lo, min(hi, int(self._side_w if side is None else side)))
        if side is not None:
            self._side_w = ws
        if ws != self._side_aw:
            self._side_aw = ws
            try:
                self.side.configure(width=ws)
            except Exception:
                pass
            changed = True
        lo, hi = self._split_limits("list")
        wl = max(lo, min(hi, int(self._list_w if list_w is None else list_w)))
        if list_w is not None:
            self._list_w = wl
        if wl != self._list_aw:
            self._list_aw = wl
            try:
                self.list_frame.configure(width=wl)
            except Exception:
                pass
            changed = True
        return changed

    def _doodle_relayout(self):
        """涂鸦蒙层跟着**主窗口**走（移动 / 缩放 / 最大化）。

        蒙层盖整窗，所以只关心 root 的客户区几何；`relayout()` 自己按
        (x, y, w, h) 去重，Configure 连发时不会反复重画。笔迹是视口像素坐标，
        窗口缩放后不跟随文字漂移 —— 这是「锁定滚动」的既定代价。
        """
        d = getattr(self, "_doodle", None)
        if d is not None and d.active:
            try:
                d.relayout()
            except Exception:
                pass

    def _on_body_resize(self):
        """窗口宽度变了：按当前可用宽重新夹紧两栏（不改设置）。"""
        if self._layout_txn:
            return
        self._apply_split_widths()

    def _sep_press(self, e, which):
        self._sep_drag = (which, e.x_root,
                          self._side_aw if which == "side" else self._list_aw)
        self._resizing = True
        return "break"

    def _sep_motion(self, e):
        """顶层窗口上的 B1-Motion：只有按住分隔条时才干活，平时一律放行。"""
        if not self._sep_drag:
            return None
        which, x0, w0 = self._sep_drag
        want = w0 + (e.x_root - x0)
        if which == "side":
            self._apply_split_widths(side=want)
        else:
            self._apply_split_widths(list_w=want)
        return "break"

    def _sep_release(self, e):
        """顶层窗口上的松手：收尾（补截断 + 落库），然后照旧吞掉这一下。"""
        if not self._sep_drag:
            return None
        self._end_sep_drag()
        self._refit_names_after_split()
        self._save_split_widths()
        return "break"

    def _sep_reset(self, e, which):
        """双击：这一栏恢复默认宽度（文件夹 216 / 列表 300）。"""
        if self._sep_drag:
            self._end_sep_drag()
        if which == "side":
            self._apply_split_widths(side=SIDEBAR_W)
        else:
            self._apply_split_widths(list_w=LIST_W)
        self._refit_names_after_split()
        self._save_split_widths()
        return "break"

    def _end_sep_drag(self):
        self._sep_drag = None
        self._resizing = False

    def _refit_names_after_split(self):
        """拖动期间跳过的名字截断，松手后一次补齐。

        文件夹名 / 笔记标题的「按像素截断」要二分测量再重设 MarqueeLabel，
        十来行累计近 200ms（见 _fit_folder_name 的注释）—— 拖动时逐帧跑就是卡顿，
        所以拖动期间直接跳过（文字会被行盒子自然裁掉，看着就是列变窄了），
        松手补一次。行盒子变宽时 Configure 也会各自触发一遍，这里只兜底变窄的情况。
        """
        try:
            for fid in list(self._folder_rows):
                self._fit_folder_name(fid)
        except Exception:
            pass
        try:
            for nid in list(self._rows):
                self._fit_note_title(nid)
        except Exception:
            pass

    def _save_split_widths(self):
        """松手时才落库：拖动过程中每帧 save 一次会把数据库写爆。

        存的是**设计稿宽度**（÷SCALE），不是当前物理像素：同一份数据在 125% 和
        200% 的机器上都该还原成同样的视觉比例，否则换台机器一打开，两栏要么挤成
        一条缝、要么把正文推到屏幕外。读回来时由 `_read_col_w` 再乘当前 SCALE。
        """
        try:
            st = self.data.setdefault("settings", {})
            st["side_w"] = int(round(self._side_w / dpi.SCALE))
            st["list_w"] = int(round(self._list_w / dpi.SCALE))
            save_data(self.data)
        except Exception:
            pass

    def toggle_lists(self):
        """收起/展开「文件夹栏 + 笔记列表」两栏，编辑区随之向左撑开或还原。

        一次折叠会牵动整行：编辑区变宽 → 正文重新换行、工具栏重算箭头、搜索框跟着
        收起 / 摆回。这些联动若各自在中间的半成品宽高上跑一遍，就会看到「停一下再跳」。
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
            # 箭头：原样（朝左）＝收起，镜像（朝右）＝展开
            self.side_toggle.set_dir(self._lists_collapsed)
            # 搜索框跟着整列一起收起 / 展开（place_forget ↔ place 回 holder 顶部）
            self._place_search_box()
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

    def _set_app_icon(self):
        """把窗口/任务栏/弹窗图标换成「素笺」图标（替换 Tk 默认羽毛）。

        优先 `wm iconbitmap -default`：.ico 里就带着 16/24/32/48/64/128/256 七档，
        Windows 按当前 DPI 和场景自己挑最合适的那一张（任务栏 32、高 DPI 48/64），
        比只塞一张 64px 位图再由系统缩放清楚。ico 缺失或调用失败时退回 iconphoto。
        """
        ico = os.path.join(_RES, "app_icon.ico")
        if os.path.exists(ico):
            try:
                self.root.iconbitmap(default=ico)   # default = 以后新建的弹窗都继承
                return
            except Exception:
                pass
        img = _icon_photo(self.root, "app_icon.png", dpi.px(64))
        if img is None:
            return
        self._app_icon = img              # 持引用：PhotoImage 被 GC 图标会消失
        try:
            self.root.iconphoto(True, img)
        except Exception:
            pass

    def _tool_bg(self):
        """工具栏底色：侧栏色朝稿纸色提亮一档。

        工具带排版的是正文，语义上属于编辑区而不是侧栏，所以底色落在
        「侧栏 → 工具带 → 白稿纸」这条渐亮序列的中间，读起来是「近乎白的浅色条」。
        早先走的是相反方向（向主文字色压暗），在浅色皮肤里无论压多重都显脏：
        0.10 是一条深色横杠，0.05 用户仍反馈偏暗——浅底上不存在"压得刚好"的档位。

        若某套皮肤的侧栏与稿纸本来就同色，提亮拿不到层次，退一档到「稿纸压暗一点」，
        保证工具带在两色之间仍有可见的分区。

        推导本身在 theme._toolbar_bg（纯函数、回归测试直接断言）；这里只是转发，
        免得标签条底色和「未选中标签」的参照色各算一份。
        """
        return _toolbar_bg(self.pal)

    def _ro_bar_bg(self):
        """只读提示条底色：主题色的淡纸片，且保证不与「未选中标签」撞色。

        ⚠️ 别改回 pal["hover"]：那是列表行的悬停色（由 list 底推出来），铺在
        标签条正下方会跟灰色未选中标签连成一条，提示读成「多了一枚标签」。
        """
        return _readonly_bar_bg(self.pal, _tab_idle_bg(self._tool_bg()))

    def _ro_accent_fg(self):
        """提示条上「可点 / 闪一下」的强调字色：accent 往主文字色压深。

        纸片本身已经带主题色，纯 accent 落在上面只剩 3:1 左右，口令入口和
        拦截闪烁会发虚；压深一档到主文字色附近，既仍是主题色系又读得清。
        """
        p = self.pal
        return _mix_hex(p["accent"], p.get("text", "#1f1f24"), 0.35)

    def _foot_rule_bg(self):
        return _status_bar_rule(self.pal, self._footer_bg())

    def _foot_fg(self):
        # 传当前底色：文字要按这块底的反差逐档提深，不能拿 soft 硬铺
        return _status_bar_fg(self.pal, self._footer_bg())

    def _tool_line(self):
        """工具栏内分隔线色：由工具栏底色向次级文字色混合，保证在底色上仍看得见。"""
        return _mix_hex(self._tool_bg(), self.pal.get("soft", "#76767e"), TOOL_LINE_MIX)

    def _strip_bg(self):
        """工具栏折叠后的窄条底色。

        折叠条本身就是「工具带被压扁」的意思，所以取工具栏底色向强调色压深一档；
        用 hover 那类亮色反而不对——6px 的亮缝看着像一条高光，不像可以点开的入口。
        """
        return _mix_hex(self._tool_bg(), self.pal.get("accent", "#2f7cf6"), 0.32)

    # ---------- 工具栏 ----------

    # 工具栏的可配置项：(id, 悬浮提示, 分组)。分组只用于自动插入分隔线——
    # 同组项连续排列、组与组之间一条竖线；某组全被隐藏时它的竖线也不出现。
    TOOL_ITEMS = [
        ("style",    "段落样式：H1 一级标题 / H2 二级标题", 0),
        ("bold",     "加粗  Ctrl+B",                       1),
        ("italic",   "斜体  Ctrl+I",                       1),
        ("underline", "下划线  Ctrl+U",                    1),
        ("strike",   "删除线",                             1),
        ("color",    "文字颜色 / 下划线颜色",               1),
        ("link",     "超链接  Ctrl+K",                     1),
        ("bullet",   "项目符号列表（右键：转换 / 清除）",   2),
        ("triangle", "三角列表（右键：转换 / 清除）",       2),
        ("numbered", "有序列表（右键：转换 / 重新编号）",   2),
        ("todo",     "待办事项（右键：转换 / 清除）",       2),
        ("code",     "代码块（选择语言）",                  2),
        ("alarm",    "任务提醒：在正文里插一个小闹钟",        2),
        # 第 15 枚（2026-11-09 用户指定）：自动换行是视图开关，不改正文，所以
        # 只读笔记里也照常可点；与 ⋯ 同组，中间不再插分隔线。
        ("wrap",     "自动换行：一行占满后自动折行",          3),
        ("more",     "更多：导出笔记 / 插入分割线 / 全局设置", 3),
    ]

    # 颜色面板共用的预设色板（6 列 x 3 行）：深浅灰阶 + 常用强调色
    COLOR_SWATCHES = [
        "#000000", "#434343", "#76767e", "#a0a0a8", "#c0392b", "#e67e22",
        "#f1c40f", "#27ae60", "#16a34a", "#2980b9", "#2f7cf6", "#8e44ad",
        "#e84393", "#ffffff", "#7f8c8d", "#d35400", "#16a085", "#2c3e50",
    ]

    # 全局设置「工具栏按钮定制」里给每项起的短名（TOOL_ITEMS 的提示语太长，
    # 不适合当复选框文字）。列表类一律用「无序 / 有序」的正式叫法：工具栏图标是
    # 项目符号 / 数字序号，写成「列表 / 序号」会让人以为一个是总开关、一个是编号工具。
    TOOL_SHORT_NAMES = {
        "style": "段落样式", "bold": "加粗", "italic": "斜体",
        "underline": "下划线", "strike": "删除线", "color": "颜色",
        "link": "超链接",
        "bullet": "无序列表", "numbered": "有序列表",
        "triangle": "三角列表", "todo": "待办",
        "code": "代码块", "alarm": "任务提醒", "wrap": "自动换行",
    }

    def _tool_cfg(self):
        """工具栏配置（折叠状态 + 隐藏项）持久化在 data["settings"]["toolbar"]。

        老数据没有这一项时给默认值：全部显示、不折叠——保持升级后的观感不变。
        """
        cfg = self.data.setdefault("settings", {}).setdefault("toolbar", {})
        if not isinstance(cfg, dict):
            cfg = {}
            self.data["settings"]["toolbar"] = cfg
        cfg.setdefault("collapsed", False)
        hidden = cfg.setdefault("hidden", [])
        if not isinstance(hidden, list):
            hidden = []
            cfg["hidden"] = hidden
        return cfg

    def _tool_hidden(self):
        valid = {i[0] for i in self.TOOL_ITEMS}
        return {h for h in self._tool_cfg()["hidden"] if h in valid}

    def _save_tool_cfg(self):
        save_data(self.data)

    def _build_toolbar(self, bar):
        """工具栏外壳：可横向滚动的画布 + 两端箭头 + 内容容器。

        按钮本身由 _populate_toolbar 放进 tool_inner。这样「自定义显示」只需重建
        内容，画布、箭头、滚轮绑定都不用动。
        """
        p = self.pal
        tbg = self._tool_bg()
        self.tool_canvas = tk.Canvas(bar, height=TOOLBAR_H, bd=0, highlightthickness=0,
                                     bg=tbg)
        # 先 pack 画布并撑满，箭头按需插到它左右两侧
        self.tool_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tool_inner = tk.Frame(self.tool_canvas, bg=tbg)
        # 窗口 item 的 id 没有用到（不需要搬动/改尺寸），只创建不保存
        self.tool_canvas.create_window((0, 0), window=self.tool_inner, anchor="nw")
        # 箭头常态色 = 工具栏底色，否则常态会露出一个白圆
        self.tool_left = ArrowButton(bar, p, "left", command=lambda: self._scroll_toolbar(-1),
                                     base=tbg)
        self.tool_right = ArrowButton(bar, p, "right", command=lambda: self._scroll_toolbar(1),
                                      base=tbg)
        self.tool_left.bind_events()
        self.tool_right.bind_events()

        self._populate_toolbar()

        # 内容宽 / 可视宽变化时重算溢出与箭头显隐
        self.tool_inner.bind("<Configure>", lambda e: self._sync_toolbar())
        self.tool_canvas.bind("<Configure>", lambda e: self._sync_toolbar())
        # 滚轮横向滑动（工具栏上滚轮 = 左右移动）
        for w in (self.tool_canvas, self.tool_inner):
            w.bind("<MouseWheel>", self._toolbar_wheel)

    def _populate_toolbar(self):
        """按配置往 tool_inner 里放按钮。改完自定义设置后整块重建。"""
        for w in list(self.tool_inner.winfo_children()):
            try:
                w.destroy()
            except Exception:
                pass
        self.fmt_buttons = []
        self._tool_seps = []
        self._tool_tips = []
        self._code_btn = None
        self._more_btn = None
        self._wrap_btn = None      # 整块重建后旧按钮已销毁，别留着指向它
        self._wrap_tip = None
        self._tool_btns = {}

        hidden = self._tool_hidden()
        last_group = None
        for iid, tip, group in self.TOOL_ITEMS:
            if iid in hidden:
                continue
            # 只在「相邻两个可见项属于不同组」时插分隔线，中间组被整组隐藏也不会连出两条
            if last_group is not None and group != last_group:
                self._sep(self.tool_inner)
            last_group = group
            self._make_tool_item(iid, tip)
        try:
            self.tool_inner.update_idletasks()
        except Exception:
            pass
        # 重建出来的按钮默认是「可用」，只读笔记里要立刻按当前状态重新压灰，
        # 否则在示例笔记里改一次工具栏按钮，工具栏就又能点了。
        self._sync_tool_enabled()

    def _make_tool_item(self, iid, tip):
        """建单个工具栏项。iid 见 TOOL_ITEMS。"""
        parent = self.tool_inner
        b = None
        if iid == "style":
            b = self._mk_dropdown(parent, "H", tip, self._pick_style_menu)
        elif iid == "bold":
            b = self._mk_fmt(parent, "B", lambda: self.toggle_sel_tag("bold"), bold=True, tip=tip)
        elif iid == "italic":
            b = self._mk_fmt(parent, "I", lambda: self.toggle_sel_tag("italic"), italic=True,
                             tip=tip)
        elif iid == "underline":
            b = self._mk_fmt(parent, "U", lambda: self.toggle_sel_tag("underline"),
                             underline=True, tip=tip)
        elif iid == "strike":
            b = self._mk_fmt(parent, "S", lambda: self.toggle_sel_tag("strike"), strike=True,
                             tip=tip)
        elif iid == "color":
            # ⚠️ 别给这枚按钮单独传 fg=pal["accent"]：那是把「建窗那一刻」的强调色
            # 写死在 RoundButton 实例上，换主题时旁边 B/I/U 都按新板重算字色，
            # 唯独这枚还留着旧色（深色主题下像被选中/高亮，格外扎眼）。颜色面板
            # 里的色板已经足够说明它是干什么的，按钮本身和其它工具栏项同一套样式。
            b = self._mk_dropdown(parent, "A", tip, None, command=self._open_color_panel)
        elif iid == "link":
            b = self._mk_icon(parent, "link", tip, self.toggle_link)
        elif iid in ("bullet", "numbered", "triangle", "todo"):
            # 对有序/无序/三角列表使用自定义位图图标（list.png / unorder_list.png / arrow.png），其它保持矢量绘制
            if iid == "numbered":
                icon_img = "list.png"
            elif iid == "bullet":
                icon_img = "unorder_list.png"
            elif iid == "triangle":
                icon_img = "arrow.png"
            else:
                icon_img = None
            # 命令显式传给 apply_list_kind。曾经用 getattr(self, "insert_" + iid)
            # 拼方法名：名字藏在字符串里，静态扫描看不见引用，谁也不敢删那几个
            # 一行包装；而且改 iid 就直接 AttributeError。
            list_kind = "check" if iid == "todo" else iid
            b = self._mk_icon(parent, iid, tip,
                              lambda k=list_kind: self.apply_list_kind(k),
                              icon_img=icon_img)
            # 四个列表按钮共用一套右键菜单：互转、重新编号、清除标记（P1）。
            # 左键仍是「切换 / 批量应用」（P0），右键补上左键表达不了的动作。
            b.bind("<Button-3>", self._open_list_menu)
        elif iid == "code":
            b = self._mk_code_ui(parent)
        elif iid == "alarm":
            b = self._mk_icon(parent, "alarm", tip, self.insert_alarm)
        elif iid == "wrap":
            on = self._editor_wrap_on()
            b = self._mk_icon(parent, "wrap", self._wrap_tip_text(on),
                              self.toggle_editor_wrap)
            b.set_selected(on)          # 亮 = 自动换行开着；暗 = 一行到底
            self._wrap_btn = b
            self._wrap_tip = self._tool_tips[-1]   # _mk_icon 里刚挂上的那枚提示
        elif iid == "more":
            b = self._mk_icon(parent, "more", tip, self._open_more_menu)
            self._more_btn = b
        if b is not None:
            # 记下 iid → 按钮，只读笔记里要按 id 精确禁用（⋯ 菜单不能被禁，
            # 里面还有「全局设置」这种与内容无关的项）
            self._tool_btns[iid] = b

    def _mk_icon(self, parent, icon, tip, cmd, icon_img=None):
        """纯图标按钮（列表 / 序号 / 待办 / ⋯ 等）。

        内边距 8 / 外边距 2：13 项排开要压进 1080 窗口那 536px 的可视宽，
        按 9/3 算是 581px（溢出 45px，右箭头会把 ⋯ 整颗盖住）。收这 1px 是
        为了默认窗口下整条不用横向滚动 —— 再往上加按钮就得先减掉别的东西。
        """
        b = RoundButton(parent, self.pal, "", command=cmd, icon=icon, icon_img=icon_img,
                        padx=dpi.px(8), pady=dpi.px(4), base=self._tool_bg())
        b.pack(side=tk.LEFT, padx=dpi.px(2), pady=dpi.px(3))
        b.bind_events()
        self.fmt_buttons.append(b)
        self._add_tip(b, tip)
        return b

    def _mk_dropdown(self, parent, text, tip, menu_builder, command=None, fg=None):
        """「文字 + 小箭头」的下拉按钮：H（段落样式）、A（颜色）。

        command 为空时点击弹菜单（menu_builder 接收按钮本身，用来定位弹窗）；
        给了 command 则直接执行（颜色面板不需要先弹一层菜单）。
        """
        holder = {}

        def act():
            if command is not None:
                command()
            else:
                menu_builder(holder.get("btn"))

        b = RoundButton(parent, self.pal, text, command=act, icon="chevron",
                        padx=dpi.px(8), pady=dpi.px(4), fg=fg, base=self._tool_bg())
        holder["btn"] = b
        b.pack(side=tk.LEFT, padx=dpi.px(2), pady=dpi.px(3))
        b.bind_events()
        self.fmt_buttons.append(b)
        self._add_tip(b, tip)
        if text == "H":
            self._style_btn = b
        return b

    def _add_tip(self, widget, text):
        """给工具栏控件挂悬浮提示（换肤后会统一刷新配色）。"""
        if not hasattr(self, "_tool_tips"):
            self._tool_tips = []
        t = Tooltip(widget, text, self.pal)
        self._tool_tips.append(t)
        return t

    def _popup_under(self, widget, menu):
        """在某个控件正下方弹出菜单；控件已销毁时退回鼠标位置。"""
        try:
            if widget is not None and widget.winfo_exists():
                x = widget.winfo_rootx()
                y = widget.winfo_rooty() + widget.winfo_height() + 2
            else:
                raise RuntimeError
        except Exception:
            x, y = self.root.winfo_pointerx(), self.root.winfo_pointery()
        menu.popup(x, y)

    def _pick_style_menu(self, btn=None):
        """段落样式：H1 一级标题 / H2 二级标题 / 正文。

        「正文」这一项必须留着 —— 工具栏上已经不再单独摆「正文」按钮，
        但没有它就没法把一行标题降回普通段落。
        """
        if not self.current_note():
            self._show_info("提示", "请先选择一条笔记。")
            return
        m = PopupMenu(self.root, self.pal)
        m.add("H1 一级标题", command=lambda: self.apply_line_tag("title"))
        m.add("H2 二级标题", command=lambda: self.apply_line_tag("h2"))
        m.add_separator()
        m.add("正文", command=lambda: self.apply_line_tag("body"))
        self._popup_under(btn or self._style_btn, m)

    def _open_more_menu(self, btn=None):
        """⋯ 溢出菜单：放低频项，把工具栏主区留给每天都在用的那几个按钮。

        导出也收在这里：一篇笔记通常只在收尾时导一次，为它常驻一枚图标不划算，
        而三种格式又必须并列摆出来（差别在「要不要保留排版、能不能再编辑」，
        用户得先看一眼才好选），正好够占菜单里的一组。
        """
        ro = self.is_readonly()     # 只读笔记：会改内容的项置灰，界面类项照常可用
        m = PopupMenu(self.root, self.pal)
        # 涂鸦不改正文（笔迹在独立窗口里，关闭即丢），所以只读笔记也照常可用；
        # 工具栏 14 项已经顶到 559/562px，第 15 枚一律收进 ⋯ 菜单。
        m.add("涂鸦标注…（Esc 退出）" if not self.doodle_active() else "退出涂鸦标注",
              icon="pencil", command=self.toggle_doodle)
        m.add_separator()
        m.add("插入分割线", icon="hr", enabled=not ro, command=self.insert_hr)
        m.add("还原默认格式", icon="undo", enabled=not ro, command=self.reset_format)
        m.add_separator()
        # 导出不属于「改内容」，只读笔记照样能导
        m.add("导出为纯文本（.txt）", icon="export",
              command=lambda: self.export_note("txt"))
        m.add("导出为 Markdown（.md）", icon="export",
              command=lambda: self.export_note("md"))
        m.add("导出为 PDF（.pdf）", icon="export",
              command=lambda: self.export_note("pdf"))
        m.add_separator()
        # 行号开关：右键行号能关，但关掉之后那块区域就没了 —— 恢复入口必须另有
        # 一处，否则用户只能去改配置文件。打勾表示当前是开着的（勾由 PopupMenu
        # 画在右端，不拼进文案，见 widgets.PopupMenu.add）。
        m.add("显示行号", icon="gutter", checked=self._line_numbers_on(),
              command=lambda: self.set_line_numbers(not self._line_numbers_on()))
        m.add("折叠工具栏", icon="panel", command=lambda: self._set_toolbar_collapsed(True))
        self._popup_under(btn or getattr(self, "_more_btn", None), m)

    def _open_list_menu(self, event):
        """列表按钮右键菜单（P1）：四种列表互转 / 重新编号 / 清除标记。

        菜单作用于**编辑器当前的光标行或选区**，不是按钮本身的状态 —— 和左键
        同一个作用域。当前选区若都是同一种列表，就在对应项后面打勾，用户不用
        猜「这一行现在算什么」。
        """
        if not self.current_note() or self.is_readonly():
            return "break"
        cur = self._common_list_kind()
        m = PopupMenu(self.root, self.pal)
        for kind, label, icon in (("bullet", "项目符号", "bullet"),
                                  ("numbered", "有序列表", "numbered"),
                                  ("triangle", "三角列表", "triangle"),
                                  ("check", "待办事项", "todo")):
            # 勾走右侧固定列（checked），不拼进 label —— 四个选项里只有一个带勾，
            # 拼字符会让那一行突然比其他项宽 8px，整张菜单跟着跳一下
            m.add(label, icon=icon, checked=(cur == kind),
                  command=lambda k=kind: self.apply_list_kind(k))
        m.add_separator()
        has_num = any(self._list_kind_at(ln) == "numbered"
                      for ln in self._touched_lines()
                      if str(ln) != self.TITLE_LINE)
        m.add("重新编号", icon="numbered", enabled=has_num,
              command=self.renumber_selected_lists)
        m.add("清除列表标记", icon="trash",
              command=lambda: self.apply_list_kind(None))
        try:
            m.popup(event.x_root, event.y_root)
        except Exception:
            pass
        return "break"

    # ---------- 工具栏折叠 / 自定义 ----------

    def _set_toolbar_collapsed(self, collapsed, remember=True):
        """折叠或展开整条工具栏。

        折叠只是把这条 36px 的条藏起来；下方那条分隔线同时变厚成 6px 作为唯一的
        「展开」入口——没有它的话工具栏一收起就再也点不回来了（⋯ 按钮也跟着一起
        消失了）。来回一趟正文净多出 31px（37 → 6），少的 5px 就是那条窄条。
        """
        collapsed = bool(collapsed)
        self._tool_collapsed = collapsed
        if remember:
            self._tool_cfg()["collapsed"] = collapsed
            self._save_tool_cfg()
        try:
            if collapsed:
                self.toolbar.pack_forget()
                self._tool_bottom.configure(height=dpi.px(6), bg=self._strip_bg(), cursor="hand2")
            else:
                self.toolbar.pack(fill=tk.X, before=self._tool_bottom)
                self._tool_bottom.configure(height=dpi.px(1), bg=self._tool_line(), cursor="")
        except Exception:
            pass
        if collapsed and getattr(self, "_strip_tip", None) is None:
            self._strip_tip = Tooltip(self._tool_bottom, "点击展开工具栏", self.pal)
        self.root.update_idletasks()
        try:
            self._sync_toolbar()
        except Exception:
            pass
        if self._vscroll_place_fn:
            self._vscroll_place_fn()
        self._sync_jump_nav()

    def _on_toolbar_strip_enter(self, _e=None):
        """折叠状态下的窄条：指针移上来就加深一档，暗示这里可以点。"""
        if not getattr(self, "_tool_collapsed", False):
            return
        try:
            self._tool_bottom.configure(
                bg=_mix_hex(self._tool_bg(), self.pal["accent"], 0.55))
        except Exception:
            pass

    def _on_toolbar_strip_leave(self, _e=None):
        if not getattr(self, "_tool_collapsed", False):
            return
        try:
            self._tool_bottom.configure(bg=self._strip_bg())
        except Exception:
            pass

    def _expand_toolbar(self):
        if getattr(self, "_tool_collapsed", False):
            self._set_toolbar_collapsed(False)

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

    def apply_palette(self, old_pal=None):
        """整窗重刷配色。`old_pal` 只在运行时换主题时给（启动时控件刚建好，无需翻译）。"""
        p = self.pal
        # 纯色背景（已移除渐变）：根/顶栏/侧栏用浅色实底，列表与编辑区保持纯色确保文字可读
        self.root.configure(bg=p["top"])
        self.top.configure(bg=p["top"])
        self.side.configure(bg=p["side"])
        self.list_frame.configure(bg=p["list"])
        self.editor_frame.configure(bg=p["editor"])
        # 三栏之间的硬竖线分隔（抓取带底色 = 左栏色，细线才是分隔线本身）
        self._vsep1.configure(bg=p["side"])
        self._vsep2.configure(bg=p["list"])
        for ln in self._sep_lines.values():
            ln.configure(bg=p["line"])
        # 顶栏与下方区域的横分割线
        self.top_rule.configure(bg=self._top_rule_color())
        # 侧栏/列表面板：纯色实底（无渐变）
        self.folder_panel.configure(bg=p["side"])
        self.folder_panel.canvas.configure(bg=p["side"])
        self.folder_panel.inner.configure(bg=p["side"])
        self.folder_panel.sb.apply_theme(p, p["side"])
        self.note_panel.canvas.configure(bg=p["list"])
        self.note_panel.inner.configure(bg=p["list"])
        self.note_panel.sb.apply_theme(p, p["list"])
        # 搜索框（文件夹列表顶部）：Canvas 自绘圆角 + 放大镜，整体重画一遍
        self.search_holder.configure(bg=p["side"])
        self.search_canvas.configure(bg=p["side"])
        self.search_ent.configure(bg=p["editor"], fg=p["text"],
                                  insertbackground=p["accent"])
        self._style_search_box()
        # 顶栏应用名与图标（含两条竖分割线：应用名右侧、「关于」与「新建」之间；
        # 都是同一个 _top_rule_color()，与顶栏那条横线同色）
        self.brand.configure(bg=p["top"], fg=p["text"])
        for d in (self.top_divider, self.create_divider):
            d.configure(bg=self._top_rule_color())
        if self.brand_icon is not None:
            self.brand_icon.configure(bg=p["top"])
        # 三枚窗口键：底色是顶栏色，图标色跟着皮肤的主文字色走
        for b in getattr(self, "win_btns", {}).values():
            b.apply_theme(p, base=p["top"])
        # 编辑区右侧浮动导航（回顶部 / 回底部）
        self.jump_nav.apply_theme(p, base=p["editor"])
        self.create_btn.apply_theme(p, base=p["top"])
        self.settings_btn.apply_theme(p, base=p["top"])
        self.data_btn.apply_theme(p, base=p["top"])
        self.about_btn.apply_theme(p, base=p["top"])
        self.side_toggle.apply_theme(p, base=p["top"])
        # 工具栏：外层条 / 滚动画布 / 内层 / 两端箭头 / 内部分隔线 / 底部分割线 统一跟随皮肤
        tbg = self._tool_bg()
        self.toolbar.configure(bg=tbg)
        self._tool_bottom.configure(bg=self._tool_line())
        # 标签条：底色与工具栏同档（活动标签是稿纸色），换肤时整条重配色
        try:
            self.tabbar.apply_theme(p, base=tbg)
        except Exception:
            pass
        # 只读提示条：主题色淡纸片（⚠️ 不是 pal["hover"]，会和未选中标签撞色）
        try:
            ro = self._ro_bar_bg()
            acc_fg = self._ro_accent_fg()
            self._ro_bar.configure(bg=ro)
            self._ro_label.configure(bg=ro, fg=p.get("soft", "#76767e"))
            self._ro_action.configure(bg=ro, fg=acc_fg)
            self._ro_recovery.configure(bg=ro, fg=acc_fg)
        except Exception:
            pass
        # 底部状态条（创建/修改/字数）：底色带主题色相，换肤时逐块重配色
        # （底 / 上沿细线 / 文字三处都从 _status_bar_* 现算，不缓存旧值）
        try:
            fbg = self._footer_bg()
            ffg = self._foot_fg()
            self._footer.configure(bg=fbg)
            self._foot_row.configure(bg=fbg)
            self._foot_rule.configure(bg=self._foot_rule_bg())
            self._foot_left.configure(bg=fbg, fg=ffg)
            self._foot_right.configure(bg=fbg, fg=ffg)
        except Exception:
            pass
        # 链接地址小窗：直接丢掉重来（下次悬停按新配色重建）
        try:
            if self._link_editor is not None:
                self._link_editor.pal = dict(p)
                self._link_editor.hide()
        except Exception:
            pass
        # `@` 候选面板同理：配色是构造时吃进去的，换肤了就先收起
        self._mention_close()
        self.tool_canvas.configure(bg=tbg)
        self.tool_inner.configure(bg=tbg)
        self.tool_left.apply_theme(p, base=tbg)
        self.tool_right.apply_theme(p, base=tbg)
        for s in self._tool_seps:
            try:
                s.configure(bg=self._tool_line())
            except Exception:
                pass
        # 悬浮提示的气泡配色跟着皮肤走（换肤后下次悬停就是新配色）
        for tip in getattr(self, "_tool_tips", []):
            tip.pal = dict(p)
        if getattr(self, "_strip_tip", None) is not None:
            self._strip_tip.pal = dict(p)
        # 折叠条：折叠时是「可点的窄条」，展开时退回 1px 分隔线
        try:
            if getattr(self, "_tool_collapsed", False):
                self._tool_bottom.configure(height=dpi.px(6), bg=self._strip_bg(), cursor="hand2")
            else:
                self._tool_bottom.configure(height=dpi.px(1), bg=self._tool_line(), cursor="")
        except Exception:
            pass
        self.text.configure(bg=p["editor"], fg=p["text"], insertbackground=p["accent"],
                             selectbackground=_sel_bg(p),
                             inactiveselectbackground=_sel_bg(p, active=False),
                             selectforeground=_sel_fg(p))
        self._config_tags()
        # 行号栏：底色是稿纸色；数字和图元是画上去的，改完底色必须重画一遍
        try:
            self.gutter.configure(bg=p["editor"])
        except Exception:
            pass
        self._gutter_schedule(0)
        # 查找浮条：控件颜色是构造时吃进去的，换肤要逐块改（关键词保留）
        self._restyle_find_bar()
        self._col_caret_invalidate()
        for blk in self._codes:
            try:
                blk.apply_theme(p)
            except Exception:
                pass
        for hr in self._hrs:
            try:
                hr.apply_theme(p)
            except Exception:
                pass
        for b in self.fmt_buttons:
            # 常态底色 = 工具栏底色；显式写死在实例上的 fg / outline（不在 tk option
            # 里，通用重刷够不着）也要按「旧板 → 新板」翻译，否则换肤后那几枚按钮
            # 会留着建窗时的旧字色 / 旧描边 —— 工具栏尤其明显。
            b.apply_theme(p, base=tbg, old_pal=old_pal)
        self.refresh_folders()
        self.refresh_notes()
        # 换肤后重刷搜索高亮：_config_tags 虽然已经把 "find" 的底色换成新主题的，
        # 但 refresh_notes 未必重建正文，命中范围可能还是老皮肤下算的那批
        # （光标位置一变就错位）。这里按当前搜索词整篇重标一遍，最省事也最准。
        try:
            self._apply_search_highlight()
        except Exception:
            pass
        try:
            self._sync_toolbar()
        except Exception:
            pass
        # 涂鸦蒙层：配色构造时吃进去，换肤要按新皮肤重算薄雾 / 工具条 / 提示片。
        # ⚠️ 只重画装饰层，笔迹（"stroke"）保持原样，别把用户刚画的抹掉。
        try:
            d = getattr(self, "_doodle", None)
            if d is not None and d.active:
                d.apply_theme(p)
        except Exception:
            pass

    # ---------- 主题切换 ----------
    def _read_theme(self):
        """从 settings 读当前主题 id；不认识（含已下线的 skin）或缺失都用默认（森林绿）。"""
        tid = (self.data.get("settings") or {}).get("theme")
        if tid in THEMES:
            return tid
        return DEFAULT_THEME

    def _current_theme(self):
        return self._read_theme()

    def set_theme(self, tid):
        """运行时切换主题：换调色板 → 重刷整窗 → 持久化到 settings。

        路径与现有换肤逻辑同构（apply_palette 已覆盖所有控件 / 标签 / 横带 / 图标），
        所以这套切换天然复用全部派生色推导，不需要另写配色逻辑。
        """
        if tid not in THEMES:
            return
        old_pal = dict(self.pal)          # 供「把旧板色写死在身上的控件」翻译
        self.pal = build_palette(tid)
        self.apply_palette(old_pal=old_pal)
        self.data.setdefault("settings", {})["theme"] = tid
        save_data(self.data)
        # 正文里已嵌入的闹钟图标按 star 染色：清缓存并就地重染，避免换肤后残留旧色
        self._refresh_alarm_images()

    # 标题相对正文的放大比例。用比例而不是固定偏移（旧版是 +4 / +2），是因为
    # 固定偏移在小字号下层级明显、到大字号就几乎看不出差别（正文 24 时 H1 只有 28）。
    # 1.35 / 1.15 这组值在正文 12 时正好给出 16 / 14，和原来的观感一致，
    # 而正文 20 时变成 27 / 23，层级依然立得住。
    H1_SCALE = 1.35
    H2_SCALE = 1.15

    @classmethod
    def _heading_size(cls, body, scale):
        """按比例算标题字号，并保证至少比正文大 2 号（小字号下不至于挤在一起）。"""
        return max(int(body) + 2, int(round(int(body) * scale)))

    def _config_tags(self):
        p = self.pal
        bs = self.body_size          # 全局正文字号（设置里改，重启保持）
        fam = getattr(self, "ui_font", None) or FONT[0]   # 全局正文字体族
        # 加粗 / 斜体 / 粗斜体：三个互斥标签。
        # Tk 里两个都设 font 的标签会「优先级高的整体覆盖低的」，所以粗+斜必须合成
        # 一个 bold_italic 标签，否则二者只能二选一（后建的 italic 会把 bold 顶掉）。
        self.text.tag_configure("bold", font=(fam, bs, "bold"))
        self.text.tag_configure("italic", font=(fam, bs, "italic"))
        self.text.tag_configure("bold_italic", font=(fam, bs, "bold italic"))
        self.text.tag_configure("underline", underline=True)
        self.text.tag_configure("strike", overstrike=True)
        # 行级样式：H1 / H2 按比例跟着正文字号缩放（见 H1_SCALE / H2_SCALE 的说明）。
        h1 = self._heading_size(bs, self.H1_SCALE)
        h2 = self._heading_size(bs, self.H2_SCALE)
        # 段间距只有一份定义：spacing1 在段首上、spacing3 在段尾下，两者之和就是
        # 「回车换行」的段间距离（基线差 = 行高 + 上 + 下）。
        # ⚠️ Tk 的自动折行（同一逻辑行的后续 display line）吃的是 **spacing2**，
        # 默认 0 → 折行比回车紧掉 (上+下) 那么多（实测正文基线差 20 vs 24）。
        # `line_spacing()` 让 spacing2 取 spacing1 + spacing3，两种换行的行距才一致；
        # 标题类传 wrap=一半 —— 两行标题之间塞进整个段间距会像两个段落。
        self.text.tag_configure("title", font=(fam, h1, "bold"), **line_spacing(8, 8, wrap=8))
        self.text.tag_configure("h2", font=(fam, h2, "bold"), **line_spacing(6, 6, wrap=6))
        self.text.tag_configure("body", font=(fam, bs), **line_spacing(2, 2))
        # ⚠️ 首行**没有**专属标签：title 的 8px 段前距在首行同样生效，选中标题时
        # 色块上下各留 8px 才对称。离顶 20 的口径由 TEXT_PADY(12) + 8 凑出来，
        # 不是靠压掉 spacing1（旧版 firstline 的做法，2026-11-05 已移除）。
        # ⚠️ 更别把 title 的 spacing1 改成 0 —— 那会把全文所有 H1 的段前距一起砍掉。
        self.text.tag_configure("done", overstrike=True, foreground=p["soft"])
        # 待办勾选框：**字面字符**，字号按正文比例算，两个状态**共用同一套字体与
        # 字号**（见 TODO_ICON_SCALE）。共用是硬要求：上一版给 ▢ / ✅ 各配一套
        # （1.25 / 0.75），打钩瞬间图标往下掉 3px、后面正文左移 1px。
        _icon_fam = self.todo_icon_font()
        _icon_sz = min(28, max(7, int(round(bs * TODO_ICON_SCALE))))
        self.text.tag_configure("todo", font=(_icon_fam, _icon_sz),
                                foreground=_mix_hex(p.get("chk_off", "#c9c9d1"),
                                                    p.get("text", "#222222"),
                                                    TODO_OFF_MIX))
        # 已勾：绿色。**不设 background** —— 底色和那块圆角都由 ✅ 字形自己画，
        # 勾在里面是挖空的（= 编辑器底色）。拿 tag 的 background 去糊的话，底色
        # 按「字符框」铺，宽 0.6em、高是整行行高，勾选框会变成一根竖条。
        self.text.tag_configure("todo_done", font=(_icon_fam, _icon_sz),
                                foreground=p.get("green", "#16a34a"))
        # 间隙（方框后那两个 NBSP）吃**正文字体**，不跟方框的图标字体走：
        # 它决定「标记 → 正文」的距离，四种列表都该是两个正文宽的空格
        # （● / ➤ / 1. 尾部用的就是同一个 _MARK_NBSP）。以前跟着图标字体，
        # 一个 NBSP 就 17px，比别人的 12px 宽一截，四种列表并排时对不齐。
        # 字号不随勾选态变（⬜ / ✅ 同号），正文起点才不会在打钩瞬间横跳。
        self.text.tag_configure(TODO_GAP_TAG, font=(fam, bs))
        # 行内代码：等宽 + **中性灰**底。底色刻意不吃主题色 —— 引用块吃的是
        # 主题色（有色纸片），两者同处正文，必须一眼分开；具体推导见
        # theme._icode_bg（它还会保证与引用底至少拉开 ICODE_MIN_DIST）。
        # 等宽字体从 code_font() 取（和代码块同一款），
        # 取不到就退回正文字体——宁可宽度不对，也不能让 tag_configure 抛异常
        # （这个函数在启动路径上，一抛就是白屏）。
        try:
            icode_fam = self.code_font()[0]
        except Exception:
            icode_fam = fam
        self.text.tag_configure("icode", font=(icode_fam, max(9, bs - 1)),
                                background=_icode_bg(p))
        # 引用块：淡底 + 左右内收，做成一块「嵌在正文里的小纸片」。
        # 缩进按字数算（两个汉字宽），和列表的「两个全角空格」视觉对齐——
        # 两处都用同一套「两格」标尺，列表和引用挨着时才不会一深一浅。
        # 不做左侧竖线：Tk 的 tag 只支持四边一起加边框，要真竖线得浮一层画布
        # 跟着 yview 重算位置，代价和浮动导航同级；淡底 + 内收在阅读时效果等价。
        # 汉字宽按 (字体族, 字号) 缓存：拖字号滑杆时 _config_tags 会连续调用，
        # 每次新建一个 tkfont.Font 去 measure 是白花时间（一次约 0.1ms，但没必要）。
        key = (fam, bs)
        cache = getattr(self, "_char_w_cache", None)
        if not cache or cache[0] != key:
            try:
                cache = (key, tkfont.Font(family=fam, size=bs).measure("\u4e2d"))
            except Exception:
                cache = (key, max(8, int(bs * 1.33)))
            self._char_w_cache = cache
        _char_w = cache[1]
        # 「任务提醒列表」汇总表：五列靠 **tag 级制表位** 按像素对齐。
        # 只作用于带 ALARM_ROW_TAG 的行 —— 普通笔记里的制表符仍是默认制表位，
        # 也不用切笔记时还原 widget 级配置；字号 / 字体一变本函数会重算一遍。
        # ⚠️ 别退回「按字数补空格」：正文字体中文 15px / 半角空格 8px（1.875 倍，
        # 不是 2），每行会按汉字数累积误差，任务名一长后面三列肉眼就歪。
        try:
            _mf = tkfont.Font(family=fam, size=bs)
            _s1 = _mf.measure("2026-11-09 09:00") + _char_w
            _s2 = _s1 + _char_w * 18        # 任务列 18 汉字宽，容得下 15 汉字 / 30 半角
            _gap = _char_w              # 两个状态列之间留一个汉字宽，别挤成一团
            _s3 = _s2 + _mf.measure("已完成：是") + _gap
            _s4 = _s3 + _mf.measure("已确认：否") + _gap
            self.text.tag_configure(ALARM_ROW_TAG, tabs=(_s1, _s2, _s3, _s4))
        except Exception:
            pass                            # 量不到字体就留默认制表位，别让启动白屏
        _qind = _char_w * 2
        self.text.tag_configure("quote", lmargin1=_qind, lmargin2=_qind,
                                rmargin=_char_w,
                                background=_quote_bg(p), **line_spacing(3, 3))
        # 链接：固定蓝 + 下划线（不跟 accent，绿皮肤下 accent 是绿的）。点击行为在
        # _on_link_click 里（tag_bind）。裸网址和富链接共用这一个样式标签，观感统一。
        self.text.tag_configure("link", foreground=p.get("link") or p["accent"],
                                underline=True)
        # 搜索命中：只上底色、不动字色（字色跟着主题走，浅稿纸上深字、深稿纸上浅字，
        # 琥珀底两头都读得清；设了字色会把加粗/标题的既有观感一起盖掉）。
        # 底色推导见 theme._search_hit_bg：琥珀色，刻意避开主题色 —— 绿皮肤下
        # 主题色就是绿的，和引用块/选中行同族，高亮会被读成「这段被选中了」。
        self.text.tag_configure("find", background=_search_hit_bg(p))
        # Ctrl+F 查找框的命中：和顶栏搜索共用琥珀底色（同一个心智模型），但**另开两个
        # 标签** —— 顶栏搜索会整片 `tag_remove("find")`，共用的话关掉查找框就把顶栏
        # 搜索的高亮一起抹了；反过来顶栏搜索每次刷新也会吃掉查找框的「当前命中」。
        # 当前命中年那条用主题色压深一档：一片琥珀里得能一眼找到「现在跳到哪一条」。
        self.text.tag_configure(FIND_HIT_TAG, background=_search_hit_bg(p))
        self.text.tag_configure(FIND_CUR_TAG,
                                background=_mix_hex(p["editor"], p["accent"], 0.42),
                                foreground=_sel_fg(p))
        # 加密笔记未解锁时正文区的占位说明：用最淡的字色，一眼就知道它不是正文
        self.text.tag_configure("locked_ph", foreground=p["soft"])
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
        # 字体标签的优先级（Tk 里后 raise 的胜出）：body < bold < italic < bold_italic
        # < h2 < title。行级样式压在强调标签之上，否则在小标题里按加粗会把字号顶回正文；
        # 而 title 又在 h2 之上，保证大标题不会被 h2 抢走。
        # f_* 动态字体标签在本函数里更晚配置，天然压在最上面（用户显式选字体优先）。
        # icode 排在 bold_italic 之后、h2/title 之前：行内代码的等宽字体要压过
        # 加粗/斜体（同一段文字两套 font 会整体互斥，代码优先），但行级样式必须
        # 压最上，否则标题行里插行内代码会把字号顶回正文。
        # todo_gap 排在 todo 之前：它只借字号，任何真样式都该压过它。
        for t in ("body", "bold", "italic", "bold_italic", "icode", "h2", "title",
                  TODO_GAP_TAG, "todo", "todo_done"):
            try:
                self.text.tag_raise(t)
            except Exception:
                pass
        # 每条富链接自己的标签 lk_N：样式与 "link" 一致，优先级压在行级样式之上
        # —— 否则标题行里的链接会被 title 的字体盖成黑字。换肤时也要重刷一遍。
        self._restyle_links()
        # 搜索命中压在引用 / 行内代码这些**静态底色**之上：它们都带 background，
        # 排在 find 下面时命中词的黄底会被整块盖掉，看起来像「搜索没高亮」。
        # 但必须排在 "sel" 下面 —— 选区是操作反馈，永远最上层。
        self.text.tag_raise("find")
        # 查找框的两个标签：命中铺底色、当前命中再压一层。同样必须在静态底色
        # （引用 / 行内代码）之上，否则「一排黄底里就那几行没变色」。
        self.text.tag_raise(FIND_HIT_TAG)
        self.text.tag_raise(FIND_CUR_TAG)
        # 竖向（矩形）选择的高亮。颜色跟普通选区同一套 —— 用户认知里它就该是
        # 「选中了」；标签单独一个只是为了复制能自己算（见 COLSEL_TAG 的注释）。
        # 它压在 find 之上、sel 之下：矩形是操作结果，搜索命中不该盖住它。
        self.text.tag_configure(COLSEL_TAG,
                                background=self.pal.get("sel", "#cfe0fb"),
                                foreground=_sel_fg(self.pal))
        self.text.tag_raise(COLSEL_TAG)
        # 选区必须压在所有标签之上。
        #
        # Tk 的 tag 优先级是「后 raise 的赢」，而 "sel" 是 Text 自带的标签，天生排在
        # 最底（实测 tag_names() 里它永远在第 0 位）。上面这串 raise 一跑，quote /
        # icode / title 全都跑到它上面去了 —— 它们都带 background，于是一选中引用
        # 行，画的仍是引用的有色纸片，看起来**根本没选中**。这就是「引用的背景色
        # 和选中的背景色重合」的根因：不是颜色选得像，是选区被盖住了。
        # 选区是操作反馈，永远该在最上面；放在这里是因为每次 _restyle_links 都会
        # 把 lk_N 顶上去，必须紧跟其后重新压一遍。
        self.text.tag_raise("sel")
        # 勾选框是正文里的字符，点击只能靠 tag_bind 接住（原来挂的是控件自己的
        # Button-1）。tag_bind 在换肤重建 tag 之前一直有效，重复绑同一个处理函数
        # 只是替换旧绑定，不会叠加。
        for tg in ("todo", "todo_done"):
            try:
                self.text.tag_bind(tg, "<Button-1>", self._on_todo_click)
            except Exception:
                pass
        # 字号/字体可能刚变过：列表悬挂缩进是按像素算的，得按新宽度重挂一遍
        self._sync_list_indent()

    def _apply_body_size(self, size):
        """套用全局正文字号：编辑器默认字体 + 正文/加粗/斜体/标题标签一起缩放。

        标签字号改了，已存在于文本里的这些标签会自动跟着变（Tk tag_configure 即时生效）。
        """
        self.body_size = min(25, max(8, int(size)))
        self.text.configure(font=(getattr(self, "ui_font", None) or FONT[0], self.body_size))
        self._config_tags()
        # 字号一换，一个空格多少像素就变了 —— 光标条给虚拟列估位置用的正是它
        self._col_caret_invalidate()
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

    def open_settings(self, _state=None):
        """全局设置：正文字号 / 字体 / 外观主题 / 工具栏按钮定制 / 标签页。

        `_state` 是可选的初始状态（字号 / 多标签开关 / 工具栏勾选 / 滚动位置），
        用户调用的入口都不用给，按已落库的设置初始化。

        窗内换主题走**就地重刷**（见 repaint_settings）：窗口、焦点、未确认的勾选
        和滚动位置全留在原地，不 destroy 再 create —— 那一下就是用户看到的「闪」。
        """
        state = _state or {}
        # 本窗当前配色。刻意**拷贝**而不是引用 self.pal：set_theme 会把 app 的 pal
        # 整个换掉，而窗内控件 / 闭包都按 p 取值；换肤时把 p 原地更新，重画图标等
        # 闭包就自动拿到最新的一套，不需要重建窗口。
        p = dict(self.pal)
        dlg = tk.Toplevel(self.root)
        dlg.title("全局设置")
        dlg.transient(self.root)
        dlg.resizable(False, False)
        dlg.configure(bg=p["editor"])

        # 内容全装进可滚动区，底部按钮行留在外面钉住；整窗高度跟「数据管理」窗
        # 对齐（见 _panel_height），多出来的内容滚动查看。
        # 这窗自然高 735（内容 662 + 按钮行 73），比数据管理窗（504）高一大截；
        # 以前只在矮屏装不下时才滚，主屏上仍是长窗，两张窗来回切像换了程序。
        # 现在固定同一把尺；矮屏再退到「屏幕能放多高就多高」，确定 / 取消始终可见。
        scroller = ScrollBody(dlg, p["editor"], p)
        scroller.pack(fill=tk.BOTH, expand=True)
        body = scroller.inner

        section_icons = []       # 四枚小节的图标 Canvas：换肤时按种类重画

        def sec_icon(parent, kind, size=16):
            """小节标题前的小图标：按当前主题色画在稿纸底色的 Canvas 上。

            尺寸走 dpi.px，画布即图标本身（不留内边距）；图形由 `_draw_section_icon`
            按 16×16 网格换算。「字体设置 / 外观主题 / 工具栏按钮定制 / 标签页」四节
            共用同一套画法与同一个尺寸（16px）—— 四节是平级的，图标忽大忽小会让人
            以为哪一节是子项。
            """
            n = dpi.px(size)
            cv = tk.Canvas(parent, width=n, height=n, bg=p["editor"],
                           bd=0, highlightthickness=0, takefocus=0)
            _draw_section_icon(cv, kind, n / 2.0, n / 2.0, n, p["accent"], p["editor"])
            # 笔画是「画上去」的，不在 tk 的颜色 option 里：换肤要按这枚图标的
            # 种类和尺寸重画一次，通用重刷只负责画布底色。
            cv._sec_icon = (kind, n)
            section_icons.append(cv)
            return cv

        head = tk.Frame(body, bg=p["editor"])
        head.pack(anchor="w", padx=dpi.px(20), pady=(dpi.px(16), 0))
        # 这颗原本是「全局设置」的齿轮标题 —— 它下面整段都是正文的字体与字号，
        # 叫「全局设置」等于把窗口名写了两遍（标题栏已经有一个），也让人以为
        # 下面那些外观 / 工具栏项都归它管。改成「字体设置」+ 字形 A，
        # 和后面三节（外观主题 / 工具栏按钮定制 / 标签页）构成平级的四节。
        sec_icon(head, "font").pack(side=tk.LEFT, padx=(0, dpi.px(6)),
                                    pady=(dpi.px(1), 0))
        tk.Label(head, text="字体设置", bg=p["editor"], fg=p["text"],
                 font=(FONT[0], 12, "bold")).pack(side=tk.LEFT)

        cur = tk.IntVar(value=int(state.get("size") or self.body_size))

        # ---- 正文字体族：原来占着工具栏 93px，收进设置后成为正经的全局项 ----
        fam_row = tk.Frame(body, bg=p["editor"])
        fam_row.pack(anchor="w", padx=dpi.px(20), pady=(dpi.px(8), 0))
        tk.Label(fam_row, text="正文字体", bg=p["editor"], fg=p["text"],
                 font=(FONT[0], 12)).pack(side=tk.LEFT, padx=(0, dpi.px(10)))
        fam_cb = ttk.Combobox(fam_row, width=16, state="readonly", height=8)
        fam_cb["values"] = [f[0] for f in self.FONT_FAMILIES]
        fam_cb.set(next((lb for lb, fam in self.FONT_FAMILIES
                         if (fam or "") == (self.ui_font or "")), "默认"))
        fam_cb.pack(side=tk.LEFT)

        def on_family(_e=None):
            fam = dict(self.FONT_FAMILIES).get(fam_cb.get(), None)
            self.ui_font = fam or ""
            self.data.setdefault("settings", {})["font_family"] = self.ui_font
            # 标签与编辑器默认字体一起换 → 全文立刻跟着变
            self._config_tags()
            self.text.configure(font=(self.ui_font or FONT[0], self.body_size))
            preview()
            save_data(self.data)

        fam_cb.bind("<<ComboboxSelected>>", on_family)

        tk.Label(body, text="正文大小（只改正文；H1 / H2 按比例跟随，正文 12 号时是 16 / 14）",
                 bg=p["editor"], fg=p["soft"], font=(FONT[0], 11)).pack(anchor="w", padx=dpi.px(20),
                                                                    pady=(dpi.px(10), 0))

        # 预览盒：一行 H1 + 一行正文，滑动时同时跟着变，直观看出「标题按比例走」。
        # 宽高按最大字号 25pt 一次性算好并锁死 —— 文字单行、盒高固定，拖动滑杆时
        # 对话框不会跟着变宽变高（不抖动）。
        import tkinter.font as _tf
        _h1_max = _tf.Font(family=FONT[0], size=self._heading_size(25, self.H1_SCALE),
                           weight="bold")
        _body_max = _tf.Font(family=FONT[0], size=25)
        _h1_text = "一级标题 H1"
        _sample_text = "正文预览：笔记内容 Abc"
        box = tk.Frame(body, bg=p["hover"], bd=dpi.px(1), relief=tk.SOLID,
                       height=(_h1_max.metrics("linespace")
                               + _body_max.metrics("linespace") + dpi.px(20)))
        box.pack(fill=tk.X, padx=dpi.px(20), pady=(dpi.px(8), dpi.px(4)))
        box.pack_propagate(False)
        # 两行字放内层容器里垂直居中：盒高按 25pt 锁死，小字号时才不会顶在上沿、
        # 下面留一大片空
        inner = tk.Frame(box, bg=p["hover"])
        inner.place(x=dpi.px(10), rely=0.5, anchor="w")
        sample_h1 = tk.Label(inner, text=_h1_text, bg=p["hover"], fg=p["text"],
                             anchor="w", justify="left",
                             font=(FONT[0], self._heading_size(self.body_size, self.H1_SCALE),
                                   "bold"))
        sample_h1.pack(anchor="w")
        sample = tk.Label(inner, text=_sample_text, bg=p["hover"],
                          fg=p["text"], anchor="w", justify="left",
                          font=(FONT[0], self.body_size))
        sample.pack(anchor="w")

        def preview(_v=None):
            v = int(cur.get())
            fam = self.ui_font or FONT[0]
            sample.configure(font=(fam, v))
            sample_h1.configure(font=(fam, self._heading_size(v, self.H1_SCALE), "bold"))

        # 上面两行是照「已落库的字号」造的；带初始状态进来时那可能是个还没确认的
        # 新值（滑杆拖过但没点确定），这里补一次，预览才不会跳回旧号。
        preview()

        # 字号滑杆：8–25pt 连续步进，拖动时气泡显示当前值
        # 画布本身固定 340 宽，靠 anchor="center" 整体水平居中（轨道、滑钮、
        # 右上角 pt 数值、两端 8/25 标签都跟着居中，不再缩在左侧）
        slider = FontSizeSlider(body, p, cur, minimum=8, maximum=25,
                                command=preview, width=dpi.px(340))
        slider.pack(anchor="center", pady=(dpi.px(12), dpi.px(2)))

        # ---- 外观主题：六张色卡折行平铺，点一下就换 ----
        # 原来是 ttk.Combobox：得先展开才知道有几套，每套长什么样更是一点看不见。
        # 改成「圆圈 + 名称」的网格：圈内按**这套主题自己**的配色上色（左半顶栏 /
        # 侧栏色、右半稿纸色、圆心一点强调色），六套能横向比。色卡内部颜色是固定
        # 的、不随当前主题变；随主题变的只有选中环与名称字色。
        tk.Frame(body, height=dpi.px(1), bg=p["line"]).pack(
            fill=tk.X, padx=dpi.px(20), pady=(dpi.px(6), 0))
        theme_row = tk.Frame(body, bg=p["editor"])
        theme_row.pack(anchor="w", padx=dpi.px(20), pady=(dpi.px(4), 0))
        sec_icon(theme_row, "theme").pack(side=tk.LEFT, padx=(0, dpi.px(6)),
                                          pady=(dpi.px(1), 0))
        tk.Label(theme_row, text="外观主题", bg=p["editor"], fg=p["text"],
                 font=(FONT[0], 12, "bold")).pack(side=tk.LEFT)

        theme_items = list_themes()                      # [(id, 显示名), ...]
        # 列数按窗口宽度算：默认的 560 宽正好摆得下 6 个（一行全见，不用滚），
        # 窗口变窄就自动折成两行。固定写死 3 列会把后 3 套挤到视口外。
        # 这张窗的滚动区高度跟数据管理窗对齐、是固定的：主题节本来就在视口下沿，
        # 卡片摆太高会把名称顶到按钮行后面，所以尺寸压成「圆 + 一行名称」。
        CELL_W, CELL_H = dpi.px(76), dpi.px(60)
        SWATCH = dpi.px(34)
        RING_W = dpi.px(3)                       # 选中粗环；回归测试按这个数断言
        HALO_GAP = max(2, dpi.px(2))             # 粗环外的光晕留白
        BADGE_R = max(4, dpi.px(7))              # 右上角「已选中」对勾徽标半径
        THEME_COLS = max(3, min(len(theme_items),
                                (_panel_width(dlg) - dpi.px(48)) // CELL_W))
        # 每套主题自己的调色板在建窗时算一次并缓存：色卡要拿来做横向比较，
        # 换主题时重算反而会让六张卡一起变色，就失去「预览」的意义了。
        theme_pals = {tid: dict(build_palette(tid)) for tid, _lb in theme_items}
        theme_cards = []           # [(tid, Canvas)]，换肤时逐张重画
        theme_hover = [None]
        # 「当前选中哪一套」是这张窗自己的状态，刻意不每次去读 _current_theme()：
        # 点击要先给出选中反馈（第一帧），整窗换肤渲染是第二步；状态提交前读 app
        # 只能拿到旧主题，色卡就会慢半拍才亮，像没点中。
        theme_selected = [self._current_theme()]
        theme_grid = tk.Frame(body, bg=p["editor"])
        theme_grid.pack(anchor="center", pady=(0, 0))

        # 状态 → 已栅格化行数据的缓存。纯 Python 字符串，不绑 Tk 解释器，
        # 也不像 PhotoImage 那样有跨 root 的坑；上限 64 组，避免越攒越多。
        bitmap_cache = {}

        def draw_theme_cards():
            """重画色卡：整张卡先栅格化成一张抗锯齿位图，再贴到 Canvas。

            ⚠️ 原来用 `create_oval` / `create_arc` 画圆和环：Tk Canvas 走 GDI、
            完全不抗锯齿，34px 的圆是硬边台阶，椭圆描边还被掰成一圈粗细不均的
            方块，五个图元各自光栅化又差半像素 —— 这就是用户说的「毛刺」。现在
            所有曲线都用 `widgets.AAImage` 按覆盖率混色一次合成（圆 / 半圆 /
            环 / 光晕 / 圆角底板 / 徽标 / 对勾），圆心与半径全是浮点 SDF，不再
            交给 Tk 的整数包围盒（半开区间 [x0,x1) 和 `r=SWATCH//2` 在高 DPI
            下各会吃掉 1px）。名称仍是 create_text —— 系统字体自带抗锯齿。

            选中态叠五层：浅色底板 → 圆外光晕 → 粗环 → 右上对勾 → 名称加粗换色；
            悬停只给一层更淡的中性底板。位图按状态缓存：`<Enter>/<Leave>` 只
            重算受影响的两张卡，不会整排重绘导致顿挫。测试读 `cv._card_state`
            这份语义状态，不再按图元 fill / outline / 宽度猜渲染实现。
            """
            cur_id = theme_selected[0]
            acc = p["accent"]
            # 1px 细环不能直接用 pal["line"]（只比底色深 5%，几乎看不见）
            idle_ring = _mix_hex(p["editor"], p["text"], 0.24)
            card_bg = _mix_hex(p["editor"], acc, 0.10)       # 选中底板：淡淡一层强调色
            card_line = _mix_hex(p["editor"], acc, 0.34)      # 底板描边：比底色看得清
            hover_bg = _mix_hex(p["editor"], p["text"], 0.05)  # 悬停底板：中性、更弱
            halo = _mix_hex(p["editor"], acc, 0.55)            # 粗环外的第二圈
            check_fg = on_accent(acc)                          # 实心 accent 上的对勾字色
            for tid, cv in theme_cards:
                q = theme_pals[tid]
                selected = (tid == cur_id)
                hovered = (tid == theme_hover[0]) and not selected
                ring_color = acc if (selected or hovered) else idle_ring
                ring_w = float(RING_W if selected else max(1, dpi.px(1)))

                # 状态没变就别重算位图：悬停是高频事件，整排重绘会顿一下。
                # ⚠️ key 里必须带 tid —— 六张卡共用一个缓存，圆内三色各不相同，
                # 少了 tid 会把同一份像素贴到所有主题上。
                key = (tid, cur_id, selected, hovered, dpi.SCALE,
                       p["editor"], p["text"], acc)
                if getattr(cv, "_card_key", None) == key:
                    continue
                cv._card_key = key

                # 几何与状态无关，缓存命中时名称位置 / 语义状态也要用同一组值
                cx = CELL_W / 2.0
                r = SWATCH / 2.0
                cy = float(dpi.px(2)) + r
                rows = bitmap_cache.get(key)
                if rows is None:
                    pic = AAImage(CELL_W, CELL_H, p["editor"])

                    # ① 整张卡的圆角底板（选中 / 悬停）
                    if selected or hovered:
                        rr, bb = aa_round_rect(dpi.px(2), dpi.px(1),
                                              CELL_W - dpi.px(2), CELL_H - dpi.px(3),
                                              float(dpi.px(8)))
                        pic.fill(card_bg if selected else hover_bg, rr, bb)
                        pic.fill(card_line if selected else hover_bg,
                                 aa_outline(rr, max(1, dpi.px(1))), bb)
                    # ② 圆外光晕：与粗环之间留一道底色缝
                    if selected:
                        hs, hb = aa_ring(cx, cy, r + HALO_GAP, max(1, dpi.px(2)))
                        pic.fill(halo, hs, hb)
                    # ③ 圆内三色预览：各主题自己的固定色，换主题不重算
                    ds, db = aa_disc(cx, cy, r)
                    pic.fill(q["editor"], ds, db)
                    ls, lb = aa_left_half(cx, cy, r)
                    pic.fill(q["top"], ls, lb)
                    dot_s, dot_b = aa_disc(cx, cy, max(2.0, r / 3.0))
                    pic.fill(q["accent"], dot_s, dot_b)
                    # ④ 右上对勾徽标：圆 + 1px 底色描边 + 两段折线
                    if selected:
                        bx = cx + r - BADGE_R + dpi.px(1)
                        by = cy - r + BADGE_R + dpi.px(1)
                        bs, bbox = aa_disc(bx, by, float(BADGE_R))
                        pic.fill(acc, bs, bbox)
                        es, eb = aa_ring(bx, by, float(BADGE_R), max(1, dpi.px(1)))
                        pic.fill(p["editor"], es, eb)
                        c1 = (bx - BADGE_R * 0.10, by + BADGE_R * 0.38)
                        s1, b1 = aa_segment(bx - BADGE_R * 0.45, by, c1[0], c1[1],
                                            max(2, dpi.px(2)))
                        s2, b2 = aa_segment(c1[0], c1[1],
                                            bx + BADGE_R * 0.52, by - BADGE_R * 0.42,
                                            max(2, dpi.px(2)))
                        pic.fill(check_fg, s1, b1)
                        pic.fill(check_fg, s2, b2)
                    # ⑤ 环最后盖上去：外半径就是圆半径，向内占 ring_w
                    rs, rb = aa_ring(cx, cy, r, ring_w)
                    pic.fill(ring_color, rs, rb)

                    rows = pic.rows
                    if len(bitmap_cache) > 64:
                        bitmap_cache.clear()
                    bitmap_cache[key] = rows

                # 位图贴在 Canvas 上。PhotoImage 必须挂在实例上保活，否则被 GC
                # 后这一格会变空白（同 _PHOTO_CACHE / self._app_icon 的红线）。
                img = AAImage.from_rows(CELL_W, CELL_H, rows).to_photo(
                    cv, getattr(cv, "_theme_photo", None))
                cv._theme_photo = img
                cv.delete("all")
                cv.create_image(0, 0, anchor="nw", image=img, tags=("bitmap",))
                name_y = cy + r + dpi.px(3)
                cv.create_text(cx, name_y, text=theme_label(tid), anchor="n",
                               tags=("name",),
                               font=(FONT[0], 10, "bold" if selected else "normal"),
                               fill=acc if selected else p["text"])
                cv._card_state = {
                    "tid": tid, "cur_id": cur_id,
                    "selected": selected, "hovered": hovered,
                    "swatch": (q["editor"], q["top"], q["accent"]),
                    "swatch_bottom": cy + r,
                    "ring": (ring_color, ring_w),
                    "cardbg": (card_bg if selected else hover_bg)
                              if (selected or hovered) else None,
                    "halo": halo if selected else None,
                    "check": bool(selected),
                    "name": (theme_label(tid), acc if selected else p["text"],
                             bool(selected)),
                }

        def on_theme(tid):
            if tid not in THEMES:
                return
            # 已经是当前这一套（窗内选中态和落库状态一致）：不必白刷一遍整窗，
            # 连点时尤其明显。
            if tid == theme_selected[0] and tid == self._current_theme():
                return

            # ① 先选中：只改窗内选中状态并重画色卡（按 _card_key 差量，实际只
            #    重算旧 / 新两张），立刻把这一帧推上屏。此时不碰 self.pal、不
            #    apply_palette、不落库 —— 点下去第一眼就该看到选中标记，而不是
            #    等整窗换肤渲染完才补上。
            theme_hover[0] = None
            theme_selected[0] = tid
            draw_theme_cards()
            dlg.update_idletasks()

            # ② 再渲染：主窗换肤 + 窗内逐控件重刷 + 落库。仍在同一个回调里，
            #    ⚠️ 不 destroy、不 after(0)、不重建：窗口全程可见，也就没有
            #    「旧窗消失 → 新窗出现」那一帧闪；grab / 焦点 / 没点确定的字号
            #    与勾选 / 滚动位置全都留在原地，不必再靠快照搬来搬去。
            #    上面没动过 self.pal / p，所以两处 old_pal 仍是真正的旧板，
            #    逐控件旧色 → 新色的翻译不会被短路。
            self.set_theme(tid)
            repaint_settings()

        def on_theme_hover(tid):
            theme_hover[0] = tid
            draw_theme_cards()

        for i, (tid, _lb) in enumerate(theme_items):
            cv = tk.Canvas(theme_grid, width=CELL_W, height=CELL_H,
                           bg=p["editor"], bd=0, highlightthickness=0,
                           takefocus=0, cursor="hand2")
            cv.grid(row=i // THEME_COLS, column=i % THEME_COLS,
                    padx=dpi.px(3), pady=dpi.px(2))
            cv._theme_tid = tid          # 回归测试按这个标记找色卡，别按尺寸猜
            cv.bind("<Button-1>", lambda _e, t=tid: on_theme(t))
            cv.bind("<Enter>", lambda _e, t=tid: on_theme_hover(t))
            cv.bind("<Leave>", lambda _e: on_theme_hover(None))
            theme_cards.append((tid, cv))
        draw_theme_cards()

        # ---- 工具栏按钮定制：编辑区上方那条工具条上留哪几枚 ----
        # 这组勾选框原来住在工具栏 ⋯ →「工具栏设置」的独立小窗里。改工具栏本质是
        # 「这个应用长什么样」，和正文字体、字号同一类；分成两处只会让人记两个地方。
        # 合并进来之后 ⋯ 菜单只留与正文内容有关的操作。
        tk.Frame(body, height=dpi.px(1), bg=p["line"]).pack(fill=tk.X, padx=dpi.px(20), pady=(dpi.px(14), 0))
        tb_head = tk.Frame(body, bg=p["editor"])
        tb_head.pack(fill=tk.X, padx=dpi.px(20), pady=(dpi.px(10), 0))
        sec_icon(tb_head, "toolbar").pack(side=tk.LEFT, padx=(0, dpi.px(6)),
                                         pady=(dpi.px(1), 0))
        tk.Label(tb_head, text="工具栏按钮定制", bg=p["editor"], fg=p["text"],
                 font=(FONT[0], 12, "bold")).pack(side=tk.LEFT)
        tk.Label(body, text="取消勾选即从工具栏移除，随时可以再勾回来。",
                 bg=p["editor"], fg=p["soft"], font=(FONT[0], 11)).pack(
                     anchor="w", padx=dpi.px(20), pady=(dpi.px(2), dpi.px(6)))

        tb_vars = {}
        box = tk.Frame(body, bg=p["editor"])
        box.pack(fill=tk.X, padx=dpi.px(16))
        # 三列等宽：uniform 让三列格子一样大，列与列之间那条空隙就处处相同。
        # 交给 grid 自己按内容排的话，第一列会被某一项撑宽、后两列挤在后面，
        # 三段的水平间距一个宽一个窄（用户一眼就看出来了）。
        for _c in range(3):
            box.columnconfigure(_c, weight=1, uniform="tb")

        def set_all(val):
            for v in tb_vars.values():
                v.set(val)

        # 全选 / 全不选贴在小节标题右端：十二个框逐个点太累，尤其「先全清、再挑几枚」
        # 这种用法 —— 它比「一个个取消」少点十下。字号比正文小一档、描边胶囊，
        # 不抢标题的注意力。
        b_none = RoundButton(tb_head, p, "全不选", command=lambda: set_all(False),
                             base=p["editor"], fg=p["soft"], outline=p["line"],
                             font=(FONT[0], 10), padx=dpi.px(9), pady=dpi.px(2))
        b_none.pack(side=tk.RIGHT)
        b_none.bind_events()
        b_all = RoundButton(tb_head, p, "全选", command=lambda: set_all(True),
                            base=p["editor"], fg=p["soft"], outline=p["line"],
                            font=(FONT[0], 10), padx=dpi.px(11), pady=dpi.px(2))
        b_all.pack(side=tk.RIGHT, padx=(0, dpi.px(6)))
        b_all.bind_events()

        # 有初始状态快照就用快照里的勾选：用户可能已经改了一半还没点确定
        hidden_now = state["hidden"] if "hidden" in state else self._tool_hidden()
        j = 0
        for iid, label, _g in self.TOOL_ITEMS:
            if iid == "more":
                # ⋯ 不可隐藏：菜单里还有折叠工具栏、插入分割线这些不进勾选表的东西，
                # 一旦藏掉它们就再没入口（原来那个独立小窗也同理跳过它）。
                continue
            name = self.TOOL_SHORT_NAMES.get(iid) or label.split("：")[0]
            v = tk.BooleanVar(value=iid not in hidden_now)
            tb_vars[iid] = v
            # 三列四行、按行读：段落样式|加粗|斜体 / 下划线|删除线|颜色 / …
            # （两列会多占两行高度，弹窗本来就偏长了）
            # 右侧不留 padx：列宽由 columnconfigure 均分，留了反而让三列各自偏右几像素。
            tk.Checkbutton(box, text=name, variable=v, bg=p["editor"], fg=p["text"],
                           font=(FONT[0], 11), anchor="w", bd=0,
                           activebackground=p["editor"], activeforeground=p["text"],
                           selectcolor=p["hover"], cursor="hand2",
                           highlightthickness=0).grid(
                               row=j // 3, column=j % 3, sticky="w",
                               padx=(dpi.px(4), 0), pady=dpi.px(1))
            j += 1

        # ---- 标签页：工具带下面那条标签条要不要出现 ----
        # 与「工具栏按钮定制」同属界面开关，所以并排放在同一张窗里；用分割线 + 小节标题
        # 隔开，否则紧跟在那十二个勾选框后面，会被当成工具栏项之一。
        tk.Frame(body, height=dpi.px(1), bg=p["line"]).pack(
            fill=tk.X, padx=dpi.px(20), pady=(dpi.px(12), 0))
        tab_head = tk.Frame(body, bg=p["editor"])
        tab_head.pack(anchor="w", padx=dpi.px(20), pady=(dpi.px(8), 0))
        sec_icon(tab_head, "tab").pack(side=tk.LEFT, padx=(0, dpi.px(6)),
                                       pady=(dpi.px(1), 0))
        tk.Label(tab_head, text="标签页", bg=p["editor"], fg=p["text"],
                 font=(FONT[0], 12, "bold")).pack(side=tk.LEFT)
        mt_var = tk.BooleanVar(
            value=bool(state["multi_tab"]) if "multi_tab" in state
            else bool(self._multi_tab_enabled()))
        # padx 必须与其它 Label / 勾选框同为 20：勾选框的「左缘」是那个方框，
        # 用 16 的话整行会比上下所有文字左移 4px（方框又是一块实心色，特别显眼）。
        tk.Checkbutton(body, text="启用多标签页", variable=mt_var, bg=p["editor"],
                       fg=p["text"], font=(FONT[0], 11), anchor="w", bd=0,
                       activebackground=p["editor"], activeforeground=p["text"],
                       selectcolor=p["hover"], cursor="hand2",
                       highlightthickness=0).pack(anchor="w", padx=dpi.px(20),
                                                  pady=(dpi.px(2), 0))
        # 说明压成一行（这窗已经偏长，多一行在 768 高的屏上会把确定按钮挤出画面）
        tk.Label(body, text="同时打开多篇笔记；关闭后标签条消失，Ctrl+Tab / Ctrl+W 一并停用。",
                 bg=p["editor"], fg=p["soft"], font=(FONT[0], 11), justify="left",
                 wraplength=_panel_width(dlg) - 44).pack(
                     anchor="w", padx=dpi.px(20), pady=(dpi.px(1), 0))

        def settings_snapshot():
            """打包「还没点确定」的临时状态，供「确定」一次性落库。

            换主题现在不再重建窗口，字号 / 勾选本来就留在控件上；滚动位置同理，
            scroll 只用于 `_state` 传进来的初始位置。
            """
            return {
                "size": int(cur.get()),
                "multi_tab": bool(mt_var.get()),
                "hidden": [i for i, v in tb_vars.items() if not v.get()],
                "scroll": float(scroller.canvas.yview()[0]),
            }

        # 这张窗实际用到的调色板角色。限定键是为了避开 top / side 这类同色键：
        # 旧板里两个键撞成同一个色时，不能让它随便挑一个角色翻译成新色。
        SETTINGS_PAL_KEYS = ("editor", "text", "soft", "hover", "line",
                             "accent", "sel", "selfg")

        def repaint_settings():
            """就地换肤：把窗内控件与自绘图元一起刷成当前主题，不重建任何控件。

            所有 configure 都在同一个事件回调里做完，Tk 到下一次空闲才统一重绘，
            屏幕上不会出现窗口消失的空档。Canvas 上的笔画不在 tk 颜色 option 里，
            所以通用重刷之后还要逐个调 apply_theme / 重画，缺一个就会留下旧色斑。
            """
            old = dict(p)
            new = dict(self.pal)
            # p 是本窗的配色引用（闭包按名字取值）：原地更新后，下面重画图标等
            # 用到的都是新主题；下一次再切主题时 old 也自动站在这次的新板上。
            p.clear()
            p.update(new)

            # 1) 普通 tk 控件：逐颜色 option 翻译旧板色 → 新板色
            #    （ttk 下拉框、系统默认色、派生色认不出来，自动跳过）
            repaint_widget_colors(dlg, old, new, SETTINGS_PAL_KEYS)
            # 2) 可滚区：外框 / 画布 / 内框 / 右侧细滚动条四层，yview 原样保留
            scroller.apply_theme(new, bg=new["editor"])
            # 3) Canvas 自绘：四枚小节图标、字号滑杆、四枚圆角按钮、六张主题色卡
            for cv in section_icons:
                kind, n = cv._sec_icon
                cv.delete("all")
                _draw_section_icon(cv, kind, n / 2.0, n / 2.0, n,
                                   new["accent"], new["editor"])
            slider.apply_theme(new)
            # 色卡圆内是六套主题各自的固定色（不跟着换），但选中环 / 名称字色
            # 用的是当前板：不重画就会留一圈旧主题的强调色。
            # 顺手对齐窗内选中态：万一有从色卡点击以外的地方直接 set_theme 再
            # 调到这里重刷，选中标记也不会停在旧主题上。
            theme_selected[0] = self._current_theme()
            draw_theme_cards()
            for btn in (b_none, b_all, b_ok, b_cancel):
                # old_pal 只影响显式传过 fg / outline 的那几枚：那两个值是写死在
                # 实例上的旧板色，不在 tk option 里，得单独翻译。
                btn.apply_theme(new, base=new["editor"], old_pal=old)

        def ok(_=None):
            snap = settings_snapshot()
            # 开关先落库，再让 _save_body_size 一次写盘（两处都 save 会多写一遍）
            mt_new = snap["multi_tab"]
            self.data.setdefault("settings", {})["multi_tab"] = mt_new
            self._save_body_size(snap["size"])
            # 点确定就生效：开着就现造标签条、关掉就拆掉，不必重启
            self._apply_multi_tab(mt_new)
            # 与字号同一套语义（字体族是即时预览、字号靠确定收尾）。
            cfg = self._tool_cfg()
            new_hidden = snap["hidden"]
            if new_hidden != list(cfg.get("hidden") or []):
                cfg["hidden"] = new_hidden
                self._save_tool_cfg()
                self._populate_toolbar()      # 整块重建，分隔线也按新的相邻关系重排
                try:
                    self._sync_toolbar()      # 项少了可能不再溢出，箭头要收掉
                except Exception:
                    pass
            dlg.destroy()

        def cancel(_=None):
            dlg.destroy()


        # 底部按钮行：两个圆角胶囊整体居中（原生 tk.Button 只能右对齐摆着）。
        # 按钮宽度按文字测量生成，不随字号/对话框宽度变化，永远不会被挤压。
        # 上留白从 14 提到 22：原来的「关于」段（分割线 + 标题 + 链接）已经搬到
        # 顶栏那枚 i 图标里，这里得自己补出滑杆与按钮之间的呼吸。
        btn_row = tk.Frame(dlg, bg=p["editor"])
        btn_row.pack(fill=tk.X, padx=dpi.px(16), pady=(dpi.px(22), dpi.px(16)))
        btn_mid = tk.Frame(btn_row, bg=p["editor"])
        btn_mid.pack(anchor="center")   # 无 side/fill 时 anchor=center 即水平居中
        b_ok = RoundButton(btn_mid, p, "确定", command=ok, primary=True,
                           font=(FONT[0], 12, "bold"), padx=dpi.px(22), pady=dpi.px(7))
        b_ok.pack(side=tk.LEFT)
        b_ok.bind_events()
        b_cancel = RoundButton(btn_mid, p, "取消", command=cancel,
                               base=p["editor"], fg=p["soft"], outline=p["line"],
                               padx=dpi.px(22), pady=dpi.px(7))
        b_cancel.pack(side=tk.LEFT, padx=(dpi.px(10), 0))
        b_cancel.bind_events()

        dlg.bind("<Escape>", cancel)
        dlg.protocol("WM_DELETE_WINDOW", cancel)
        scroller.bind_wheel(dlg)

        def fit_body(avail_h):
            """把整窗钉到数据管理窗的高度，多出来的内容交给滚动区。

            ⚠️ 每次都要调（_center_dialog 保证）：Canvas 默认 265 高，不喂它就会
            缩成一个小窗里滚。这里不再是「屏幕给多少用多少」，而是先跟邻居等高，
            再被屏幕可用高夹一道 —— 两张窗的位置、大小完全重合，切换时不跳。
            """
            dlg.update_idletasks()
            fixed = dlg.winfo_reqheight() - scroller.canvas.winfo_reqheight()
            scroller.fit(panel_canvas_height(_panel_height(self), avail_h, fixed,
                                             dpi.px(120)))

        # 宽度跟数据管理、关于同一把尺（见 _panel_width），三张窗左右边缘对齐
        self._center_dialog(dlg, min_w=_panel_width(dlg), fit_body=fit_body,
                            center_y=True)
        # 带初始状态进来时滚回那一屏（几何定下来之后设才有效，所以放在居中之后）。
        # 窗内换主题是就地重刷，滚动位置本来就留在原地，用不着这一步。
        if "scroll" in state:
            dlg.update_idletasks()
            scroller.canvas.yview_moveto(state["scroll"])
        try:
            dlg.grab_set()
        except Exception:
            pass

    def open_data_manager(self, _measure=False):
        """数据管理：导出备份 / 导入数据 / 清空所有数据。

        这三件事原来挤在全局设置弹窗的下半部分 —— 外观项和数据操作混在一张窗里，
        想导出得先想到「设置」。现在顶栏单独立一枚图标（数据库 + 齿轮），一步直达。

        每一项做成「标题 + 一句说明 + 按钮」三行：导出和导入是安全操作，
        清空是不可撤销的，说明里必须把后果写清楚，别指望用户看按钮颜色就懂。

        `_measure=True` 是给 `_panel_height` 用的标尺模式：摆完同样的内容后只读
        reqheight 就销毁，窗口始终 withdraw、不设 transient、不抢 grab。全局设置
        窗拿这个数当自己的高度，两张窗才是同一把尺（改了这里的排版，那边自动跟）。
        """
        p = self.pal
        dlg = tk.Toplevel(self.root)
        dlg.title("数据管理")
        if _measure:
            dlg.withdraw()          # 一次都不让它有机会映射出来
        else:
            dlg.transient(self.root)
        dlg.resizable(False, False)
        dlg.configure(bg=p["editor"])
        # 与关于、全局设置同宽；说明文字按这个宽度折行（左右各 20 的内边距 + 少量框距）
        panel_w = _panel_width(dlg)
        wrap_w = panel_w - 44
        tk.Label(dlg, text="数据管理", bg=p["editor"], fg=p["text"],
                 font=(FONT[0], 13, "bold")).pack(anchor="w", padx=dpi.px(20), pady=(dpi.px(16), dpi.px(2)))
        tk.Label(dlg, text="所有笔记保存在 notes_data.db（程序同目录）。",
                 bg=p["editor"], fg=p["soft"], font=(FONT[0], 11)).pack(
                     anchor="w", padx=dpi.px(20))

        rows = (
            ("导出备份",
             "导出文件夹、笔记与外观设置，用于存档或换机；说明与「系统」不进备份。",
             "导出…", self.export_data, p["accent"]),
            ("导入数据",
             "从备份恢复：覆盖会连外观一起换回去，合并只追加本机没有的笔记。",
             "选择文件…", self.import_data, p["text"]),
            ("清空所有数据",
             "删除全部笔记与自建文件夹，回到初次打开的状态。此项无法撤销。",
             "清空…", self.clear_all_data, MENU_DANGER),
        )
        for title, desc, btn_text, cmd, fg in rows:
            tk.Frame(dlg, height=dpi.px(1), bg=p["line"]).pack(fill=tk.X, padx=dpi.px(20), pady=(dpi.px(14), 0))
            tk.Label(dlg, text=title, bg=p["editor"], fg=p["text"],
                     font=(FONT[0], 12, "bold")).pack(anchor="w", padx=dpi.px(20), pady=(dpi.px(10), 0))
            tk.Label(dlg, text=desc, bg=p["editor"], fg=p["soft"],
                     font=(FONT[0], 11), justify="left",
                     wraplength=wrap_w).pack(anchor="w", padx=dpi.px(20), pady=(dpi.px(3), 0))
            # 按钮按文字量宽度，不撑满整行：三个按钮大小不一才看得出轻重
            b = RoundButton(dlg, p, btn_text,
                            command=lambda c=cmd: (dlg.destroy(), c()),
                            base=p["editor"], fg=fg, outline=p["line"],
                            padx=dpi.px(14), pady=dpi.px(5))
            b.pack(anchor="w", padx=dpi.px(20), pady=(dpi.px(8), 0))
            b.bind_events()

        btn_row = tk.Frame(dlg, bg=p["editor"])
        btn_row.pack(fill=tk.X, padx=dpi.px(16), pady=(dpi.px(16), dpi.px(16)))
        b_close = RoundButton(btn_row, p, "关闭", command=dlg.destroy,
                              base=p["editor"], fg=p["soft"], outline=p["line"],
                              padx=dpi.px(22), pady=dpi.px(7))
        b_close.pack(anchor="center")
        b_close.bind_events()

        if _measure:
            dlg.update_idletasks()
            h = dlg.winfo_reqheight()
            dlg.destroy()
            return h

        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)
        self._center_dialog(dlg, min_w=panel_w, center_y=True)
        try:
            dlg.grab_set()
        except Exception:
            pass

    def open_about(self):
        """关于：应用身份 + 项目地址（原来压在全局设置弹窗最底下）。

        设置弹窗里现在只剩「正文长什么样」这类可调项；「这是谁、代码在哪」
        是只读信息，和导出/清空一样属于「关于这个应用」，单开一张小窗。
        """
        p = self.pal
        dlg = tk.Toplevel(self.root)
        dlg.title("关于")
        dlg.transient(self.root)
        dlg.resizable(False, False)
        dlg.configure(bg=p["editor"])

        # 头：应用图标 + 名字 / 一句话定位（图标 44px，缩略图不会糊）
        head = tk.Frame(dlg, bg=p["editor"])
        head.pack(anchor="w", padx=dpi.px(20), pady=(dpi.px(18), 0))
        icon_img = self._brand_photo(dpi.px(44))
        if icon_img is not None:
            lab = tk.Label(head, image=icon_img, bg=p["editor"], bd=0)
            lab.image = icon_img       # PhotoImage 必须持住引用，否则显示成空白
            lab.pack(side=tk.LEFT, padx=(0, dpi.px(12)))
        title_box = tk.Frame(head, bg=p["editor"])
        title_box.pack(side=tk.LEFT)
        tk.Label(title_box, text=APP_NAME, bg=p["editor"], fg=p["text"],
                 font=(FONT[0], 16, "bold")).pack(anchor="w")
        tk.Label(title_box, text="零依赖的本地笔记应用 · Python + Tkinter",
                 bg=p["editor"], fg=p["soft"], font=(FONT[0], 11)).pack(anchor="w",
                                                                      pady=(dpi.px(2), 0))
        # 版本号挂在这里，而不是工具栏：工具栏 14 项已经把 1080 窗口的横向预算
        # 吃到只剩 3px（559/562），再加一枚按钮就会出现横向滚动箭头。而「我装的是
        # 哪个版本」属于「关于这个应用」，和项目地址、反馈通道是一类东西。
        tk.Label(title_box, text="v" + __version__, bg=p["editor"], fg=p["soft"],
                 font=(FONT[0], 10)).pack(anchor="w", pady=(dpi.px(3), 0))

        # 拆成三行短句而不是一段长文：一段话折在「——」后面会断得很难看。
        # ⚠️ 第三行是「检查更新」上线后补的：原来第一行写的是「不联网」，而现在
        # 有个会真的发请求的按钮 —— 承诺要么兑现、要么把边界说清楚，不能自相矛盾。
        # wraplength 取 470 = 窗宽 560 减左右各 20、再留 60 余量。最长那句（带
        # GitHub 的）在本机微软雅黑 13px 下量出来 401px —— 原来的 368 装不下它，
        # 那一行会折成两行、比另外两行高一截，三行就不再像「一组」。
        # 470 留了 69px 余量，换字体或系统缩放 125% 都还折不到。
        for i, line in enumerate((
                "不注册、不上传，笔记只存在这台机器上。",
                "数据在程序同目录的 notes_data.db，拷走就能换机。",
                "只有检查或下载更新时才会访问 GitHub，其余时间不联网。")):
            tk.Label(dlg, text=line, bg=p["editor"], fg=p["soft"],
                     font=(FONT[0], 11), justify="left",
                     wraplength=dpi.px(470)).pack(anchor="w", padx=dpi.px(20),
                                          pady=(dpi.px(14) if i == 0 else dpi.px(2), 0))

        # 项目地址 / 问题反馈：两行，图标分别是 icon/github.png 与 icon/issue.png。
        # 图标是线条图（原色烤死成 #191313），运行时按 alpha 染成主题文字色，
        # 换肤不会跟正文打架；16px 与 11pt 的行高（约 21px）居中齐平。
        tk.Frame(dlg, height=dpi.px(1), bg=p["line"]).pack(fill=tk.X, padx=dpi.px(20), pady=(dpi.px(14), 0))
        tk.Label(dlg, text="项目与反馈", bg=p["editor"], fg=p["text"],
                 font=(FONT[0], 12, "bold")).pack(anchor="w", padx=dpi.px(20), pady=(dpi.px(10), 0))
        # PhotoImage 必须由窗口持住引用，否则被回收后行里只剩文字
        self._about_photos = []
        link_box = tk.Frame(dlg, bg=p["editor"])
        link_box.pack(anchor="w", padx=dpi.px(20), pady=(dpi.px(4), 0))
        for name, label, url in (("github.png", "项目地址", GITHUB_URL),
                                 ("issue.png", "问题反馈", ISSUES_URL)):
            self._about_link_row(link_box, p, name, label, url)

        # 检查结果就地显示在这一行，**不另开弹窗**：关于窗本身是模态的（下面
        # 有 grab_set），再叠一层 _show_info 会 wait_window 卡住；更麻烦的是
        # Tk 的 grab 是「唯一一个」而不是「一层套一层」—— 那层窗一关，上层的
        # grab 就没了，关于窗会突然变成非模态。
        # 初始不 pack：没有结果时不占高度，有结果再塞进来并重新居中。
        note = tk.Label(dlg, text="", bg=p["editor"], fg=p["soft"],
                        font=(FONT[0], 10), anchor="w", justify="left",
                        wraplength=_panel_width(dlg) - 40, cursor="arrow")

        btn_row = tk.Frame(dlg, bg=p["editor"])
        btn_row.pack(fill=tk.X, padx=dpi.px(16), pady=(dpi.px(14), dpi.px(16)))
        inner = tk.Frame(btn_row, bg=p["editor"])
        inner.pack(anchor="center")
        b_check = RoundButton(inner, p, "检查更新", base=p["editor"],
                              fg=p["text"], outline=p["line"], padx=dpi.px(18), pady=dpi.px(7))
        b_check.pack(side=tk.LEFT, padx=(0, dpi.px(8)))
        b_check.bind_events()
        # 回调里要用到按钮自己（查询期间置灰、把字换成「检查中…」），构造那一刻
        # b_check 还没赋值，所以先建再挂。Canvas 没有 -command 选项，只能走 set_command。
        b_check.set_command(lambda: self._check_update_start(
            dlg, note, btn_row, b_check))
        b_close = RoundButton(inner, p, "关闭", command=dlg.destroy,
                              base=p["editor"], fg=p["soft"], outline=p["line"],
                              padx=dpi.px(22), pady=dpi.px(7))
        b_close.pack(side=tk.LEFT)
        b_close.bind_events()

        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)
        # 这把尺子的长度就是「关于」量出来的：另外两张窗跟着它走
        self._center_dialog(dlg, min_w=_panel_width(dlg), center_y=True)
        try:
            dlg.grab_set()
        except Exception:
            pass

    # ---------------- 检查更新（P0：只查、只提示，不下载不替换） ----------------
    # 这一组紧挨着 open_about，因为它目前唯一的入口就在那张窗上。

    def _run_bg(self, fn, done, interval=80):
        """在后台线程跑 fn()，回到 Tk 主线程再调 done(ok, 结果 / 异常)。

        Tk 不是线程安全的：子线程里碰任何控件都可能随机崩，或者更糟 ——
        界面看着正常、内部状态却悄悄错乱。所以子线程只负责往队列里丢结果，
        主线程用 after 轮询取出来，`done` 保证在主线程执行。

        `daemon=True`：万一某个请求卡在网络里，用户关窗退出时不能让它把进程
        吊在那儿不结束。
        """
        q = queue.Queue()

        def work():
            try:
                q.put((True, fn()))
            except Exception as exc:        # noqa: BLE001
                q.put((False, exc))

        threading.Thread(target=work, daemon=True).start()

        def poll():
            try:
                ok, payload = q.get_nowait()
            except queue.Empty:
                try:
                    self.root.after(interval, poll)
                except tk.TclError:         # 主窗口已经销毁
                    return
                return
            done(ok, payload)

        self.root.after(interval, poll)

    def _check_update_start(self, dlg, note, btn_row, btn):
        """「检查更新」：按钮先变「检查中…」并置灰，查完再还原。

        置灰不只是防连点 —— 网络卡住时 8 秒才超时，不给反馈的话用户会以为
        按钮坏了，然后一直点。
        """
        if getattr(self, "_upd_busy", False):
            return
        self._upd_busy = True
        btn.set_enabled(False)
        btn.set_text("检查中…")

        def done(ok, payload):
            self._upd_busy = False
            try:
                if not dlg.winfo_exists():
                    return                  # 查询期间用户把关于窗关了
                btn.set_text("检查更新")
                btn.set_enabled(True)
                if ok:
                    res = payload
                else:
                    res = {"state": "error",
                           "error": "检查更新时出错：%s" % payload}
                self._render_update_result(dlg, note, btn_row, btn, res)
            except tk.TclError:
                pass                        # 窗口刚好在这一刻被销毁

        self._run_bg(updater.check, done)

    def _render_update_result(self, dlg, note, btn_row, btn, res):
        """把检查结果写进关于窗底部那一行，并按新的内容高度重新居中。

        按钮承担「下一步」：有新版本时它从「检查更新」变成「下载更新」，下载
        完成再变成「立即更新」。所有动作都落在这一个按钮上，是为了不新增控件
        —— 这张窗的宽度是「关于」量出来的标尺（_panel_width），其他地方都照它走。
        """
        p = self.pal
        state = res.get("state")
        link = ""
        # 先把按钮复位成「检查更新」：上一次可能停在「下载更新」或「立即更新」
        # 上，不复位就会出现「点重查却进了另一个动作」的错乱
        btn.set_text("检查更新")
        btn.set_command(lambda: self._check_update_start(dlg, note, btn_row, btn))
        if state == "new":
            asset = updater.pick_asset(res)
            size = updater.human_size((asset or {}).get("size"))
            note.configure(fg=p.get("link") or "#1668d9", cursor="hand2",
                           text="发现新版本 %s%s，点此查看更新说明 ›"
                                % (res.get("version", ""),
                                   ("（%s）" % size) if size else ""))
            # 兜底到 releases 列表页而不是某个具体 tag：万一 html_url 缺失，
            # 列表页至少能让人自己找到最新那个
            link = res.get("html_url") or (GITHUB_URL + "/releases")
            if asset:
                btn.set_text("下载更新")
                btn.set_command(lambda: self._update_start_download(
                    dlg, note, btn_row, btn, res))
            else:
                # 没有可下载的包（只剩源码）：只能开网页，别给一个点了就报错的按钮
                btn.set_text("打开发布页")
                btn.set_command(lambda: self._open_url(link))
        elif state == "latest":
            note.configure(fg=p["soft"], cursor="arrow",
                           text="已是最新版本（v%s）。" % __version__)
        else:
            note.configure(fg=MENU_DANGER, cursor="arrow",
                           text=res.get("error") or "检查失败。")

        # 重查时 note 可能已经挂过链接：先解绑，否则「上一次是新版、这一次是
        # 最新」还会挂着上一轮的点击目标
        note.unbind("<Button-1>")
        if link:
            note.bind("<Button-1>", lambda _e: self._open_url(link))
        # 用 winfo_manager() 而不是 winfo_ismapped()：后者在窗口尚未映射时也是 0，
        # 会把已经 pack 过的 label 再 pack 一次（Tk 会重复排队，行距翻倍）
        if not note.winfo_manager():
            note.pack(anchor="w", padx=dpi.px(20), pady=(dpi.px(10), 0), before=btn_row)
        # 内容高度变了：_center_dialog 支持重复调用（它会先松开 minsize 再重新量）
        self._relayout_dialog(dlg)

    # ---------------- 自动更新：下载 → 校验 → 退出后替换 ----------------
    # 这一段全部建立在「Windows 不允许覆盖正在运行的 exe」这条硬约束上：
    # 应用内只能做到"把新版本准备好并验证无误"，真正的替换必须在进程退出
    # 之后由批处理执行（见 updater.BAT 里的逐条注释）。

    def _relayout_dialog(self, dlg):
        """内容行变高/变矮后重新居中。_center_dialog 设计上可重复调用。"""
        try:
            self._center_dialog(dlg, min_w=_panel_width(dlg))
        except tk.TclError:
            pass

    def _update_blocked_reason(self):
        """能不能自己替换自己。返回空串表示可以，否则是给用户看的原因。"""
        if not updater.is_frozen():
            return "源码运行模式不会自动替换文件，请到发布页手动下载。"
        if os.path.basename(sys.executable).lower() != updater.APP_EXE.lower():
            # 用户把 exe 改过名：包里那个 素笺.exe 也能跑，但替换完启动哪个、
            # 旧名字那个还留着 —— 与其猜，不如让人自己去下
            return ("程序文件名不是 %s，为免替换出错，请到发布页手动下载。"
                    % updater.APP_EXE)
        return ""

    def _update_start_download(self, dlg, note, btn_row, btn, res):
        """点「下载更新」：后台下载 + 校验 + 解包，进度就地显示在结果行上。"""
        if getattr(self, "_upd_busy", False):
            return
        p = self.pal
        if not note.winfo_manager():
            note.pack(anchor="w", padx=dpi.px(20), pady=(dpi.px(10), 0), before=btn_row)
        why = self._update_blocked_reason()
        if why:
            # 光标保持 hand2：上一轮挂的「打开更新说明」绑定还在，这行文字仍然可点
            # —— 自动替换走不通时，那条路就是唯一的出路，别让它看着像不可点
            note.configure(fg=MENU_DANGER, cursor="hand2", text=why)
            btn.set_text("检查更新")
            btn.set_command(lambda: self._check_update_start(dlg, note, btn_row, btn))
            self._relayout_dialog(dlg)
            return

        self._upd_busy = True
        # 代次：用户取消后再点一次、或窗口关掉再开，旧那轮的轮询要能自己停下来
        self._upd_gen = getattr(self, "_upd_gen", 0) + 1
        gen = self._upd_gen
        prog = {"done": 0, "total": 0, "cancel": False, "phase": "download"}

        btn.set_text("取消下载")
        # 取消只置一个标记，真正的停止由下载循环在下一块数据前看到 —— 不从
        # 界面线程去动子线程里的状态，也不去 kill 它（那会留下写了一半的文件）
        btn.set_command(lambda: prog.__setitem__("cancel", True))
        self._update_poll_progress(dlg, note, prog, gen)

        def done(ok, payload):
            if gen != getattr(self, "_upd_gen", 0):
                return                      # 已经开始了新一轮
            self._upd_busy = False
            # 告诉进度轮询"收摊"：它每 150ms 写一次文本，如果不等它停，最后
            # 一帧会把下面刚写好的结论盖回「正在取消…」（实测踩到过）
            prog["closed"] = True
            try:
                if not dlg.winfo_exists():
                    return
            except tk.TclError:
                return
            btn.set_enabled(True)
            btn.set_text("下载更新")
            btn.set_command(lambda: self._update_start_download(
                dlg, note, btn_row, btn, res))
            if ok:
                self._update_ready(dlg, note, btn_row, btn, payload)
                return
            if isinstance(payload, updater.Cancelled):
                note.configure(fg=p["soft"], cursor="arrow",
                               text="已取消下载（下次从断点继续）。")
            elif isinstance(payload, updater.UpdateError):
                note.configure(fg=MENU_DANGER, cursor="arrow", text=str(payload))
            else:
                note.configure(fg=MENU_DANGER, cursor="arrow",
                               text="下载时出错：%s" % payload)
            self._relayout_dialog(dlg)

        self._run_bg(lambda: updater.prepare(res, prog), done)

    def _update_poll_progress(self, dlg, note, prog, gen):
        """每 150ms 把 prog 里的两个数字读出来写成一句人话。

        子线程只往 dict 里写整数（GIL 保证读到的不会是被撕裂的值），绝不碰
        控件；这里每帧只做一次 configure，文本长度也控制在不会折行的范围内 ——
        进度行一变高变矮，整张窗就会跟着抖。
        """
        if gen != getattr(self, "_upd_gen", 0) or prog.get("closed"):
            return
        try:
            if not dlg.winfo_exists():
                return
            done = int(prog.get("done") or 0)
            total = int(prog.get("total") or 0)
            phase = prog.get("phase") or "download"
            if prog.get("cancel"):
                text = "正在取消…"
            elif phase == "verify":
                text = "正在校验安装包…"
            elif phase == "extract":
                text = "正在解压安装包…"
            elif total:
                # 封顶 99：还在写文件时出现 100% 会让人以为卡住了
                text = "正在下载… %d%%（%s / %s）" % (
                    min(99, done * 100 // max(1, total)),
                    updater.human_size(done) or "0 B", updater.human_size(total))
            else:
                text = "正在下载… %s" % (updater.human_size(done) or "0 B")
            note.configure(text=text, fg=self.pal["soft"], cursor="arrow")
        except tk.TclError:
            return
        self._after(150, lambda: self._update_poll_progress(dlg, note, prog, gen))

    def _update_ready(self, dlg, note, btn_row, btn, info):
        """下载校验都过了：只差「退出去让批处理替换」。"""
        p = self.pal
        how = "校验通过" if info.get("verified") else "发布未提供校验值"
        note.configure(fg=p["soft"], cursor="arrow",
                       text="更新包已就绪（%s，%s）。点「立即更新」会重启并完成安装。"
                            % (info.get("version") or "", how))
        btn.set_enabled(True)
        btn.set_text("立即更新")
        btn.set_command(lambda: self._update_apply(dlg, note, btn, info))
        self._relayout_dialog(dlg)

    def _update_apply(self, dlg, note, btn, info):
        """最后一步：存一份快照 → 拉起批处理 → 退出本进程。"""
        btn.set_enabled(False)
        btn.set_text("正在重启…")
        # 快照固定落在 data/updates/，用户从「数据管理 → 导入数据」里直接能选到；
        # 这个窗口下一秒就销毁，弹窗提示反而看不清，所以只落文件不提示
        self._snapshot_before_update(info.get("version"))
        try:
            updater.launch_apply(info["src"], updater.app_dir(),
                                 os.path.abspath(sys.executable),
                                 workdir=info.get("dir"))
        except Exception as e:
            btn.set_enabled(True)
            btn.set_text("立即更新")
            note.configure(fg=MENU_DANGER, cursor="arrow",
                           text="没能启动更新程序：%s" % e)
            return
        try:
            dlg.destroy()
        except tk.TclError:
            pass
        self.root.destroy()

    def _snapshot_before_update(self, ver):
        """替换前把整个库导一份到 data/updates/，返回路径（失败返回空串）。

        这一份是给「新版把数据迁移写坏了」兜底的：那种情况下用户重开新版本看到
        的是空笔记本，唯一能救回来的就是这份快照（数据管理 → 导入数据）。
        只留最近 3 份，再多纯属占地方。
        """
        try:
            import backup
            import store as _store
            folder = os.path.join(_store.BASE, "updates")
            os.makedirs(folder, exist_ok=True)
            # 版本号可能自带 v（release 的 tag 常写 v1.1.0）—— 去掉再拼，
            # 免得出现 pre-vv1.1.0 这种谁也不认识的文件名
            label = str(ver or __version__).strip().lstrip("vV") or "unknown"
            path = os.path.join(folder, "pre-v%s-%s.json.gz" % (
                label, time.strftime("%Y%m%d-%H%M%S")))
            backup.dump(path, backup.encode(self.data))
            olds = sorted(f for f in os.listdir(folder) if f.startswith("pre-v"))
            for name in olds[:-3]:
                try:
                    os.remove(os.path.join(folder, name))
                except OSError:
                    pass
            return path
        except Exception:
            return ""

    def _check_update_aftermath(self):
        """启动时收尾上一次自动更新：失败就提示，旧安装包顺手清掉。

        替换发生在进程退出之后，那一刻没有任何代码能弹窗，所以"失败"只能写成
        文件、下次启动再说。没有这一步，用户看到的就是「点了更新、重启了、
        还是老版本，也没人告诉我为什么」。
        """
        try:
            got = updater.read_apply_error()
        except Exception:
            got = None
        if got:
            path, text = got
            try:
                os.remove(path)
            except OSError:
                pass
            self._show_error(
                "自动更新没成功",
                "新版本没能替换进去，当前仍是 v%s。\n\n%s\n\n"
                "可以到发布页手动下载最新版。" % (__version__, text.strip()))
        try:
            # drop_version：刚装上的那个版本的工作目录已经没用了（26MB），
            # 哪怕它带着 package.zip 也要删 —— 否则会一直留在临时目录里
            updater.clean_update_dirs(drop_version=__version__)
        except Exception:
            pass

    def _open_url(self, url):
        """用系统浏览器打开链接。失败了就把地址抄进剪贴板再提示，别让人干瞪眼。"""
        import webbrowser
        try:
            if webbrowser.open(url):
                return
        except Exception:
            pass
        holder = None
        try:
            # 记下当前模态窗：_show_error 自己 grab_set，关掉时会把 grab 一起
            # 带走（Tk 的 grab 只有「一个」），不还回去的话关于窗就成了非模态，
            # 会被主窗口直接盖住
            holder = self.root.grab_current()
        except Exception:
            pass
        self._show_error("打开链接失败", "没能调起浏览器，请手动访问：\n" + url)
        if holder is not None:
            try:
                if holder.winfo_exists():
                    holder.grab_set()
            except Exception:
                pass

    def _about_link_row(self, parent, p, icon_name, label, url):
        """关于弹窗里的一行外链：图标 + 中文标签 + 完整地址，整行可点。

        地址是**完整 URL 且不折行**（用户要看的就是这串地址），所以弹窗宽度由
        「最长的那串地址」决定、不是拍一个固定值 —— 换地址、换字体都不会挤成两行
        （本机微软雅黑 11pt 下最长 380px，这一行占 547）。正因为这张窗是**唯一撑满**
        的一张，全局设置和数据管理的宽度也照它定（见 _panel_width），三张一样宽 560。
        """
        row = tk.Frame(parent, bg=p["editor"], cursor="hand2")
        row.pack(anchor="w", pady=(dpi.px(3), 0))
        # 图标槽固定 16×16 再居中放图：没有 PIL 时回退路径的位图会大几像素
        # （整数倍降采样只能取 17/23 这种档），直接 pack 会把两行文字推得不对齐。
        box = tk.Frame(row, width=dpi.px(16), height=dpi.px(16), bg=p["editor"], cursor="hand2")
        # 左边让 3px：标题「项目与反馈」的 Label 框虽然也在 x=20，但汉字字形自带
        # 约 3px 左边距，墨迹实际从 x=23 起 —— 位图没有这层留白，不补就看得出
        # 图标比标题往左冒出去一截。
        box.pack(side=tk.LEFT, padx=(dpi.px(3), 0))
        box.pack_propagate(False)
        img = _tinted_png_photo(box, icon_name, dpi.px(16), p["text"])
        ic = None
        if img is not None:
            self._about_photos.append(img)      # 不持住引用，图标会被 GC 掉
            ic = tk.Label(box, image=img, bg=p["editor"], bd=0, cursor="hand2")
            ic.place(relx=0.5, rely=0.5, anchor="center")
        # 「项目地址」「问题反馈」都是四个字，宽度天然相同 → 两行的地址左缘自己就齐了
        lb = tk.Label(row, text=label, bg=p["editor"], fg=p["text"],
                      font=(FONT[0], 11), cursor="hand2")
        lb.pack(side=tk.LEFT, padx=(dpi.px(8), dpi.px(8)))
        ub = tk.Label(row, text=url, bg=p["editor"],
                      fg=p.get("link") or "#1668d9",
                      font=(FONT[0], 11), cursor="hand2")
        # 右侧留 20：弹窗宽度由这一行撑出来，不留白的话地址会贴着右缘
        ub.pack(side=tk.LEFT, padx=(0, dpi.px(20)))

        def open_url(_e=None):
            # 统一走 _open_url：它会把「浏览器没调起来」这种情况处理掉，
            # 并且在事后把模态窗的 grab 还回来（三处重复的内联 webbrowser 已合并）
            self._open_url(url)

        targets = [row, box, lb, ub] if ic is None else [row, box, ic, lb, ub]
        for w in targets:
            w.bind("<Button-1>", open_url)
        return row

    def _ask_import_mode(self, content_line, exported_at=""):
        """导入方式选择：覆盖 / 合并 / 取消。

        `content_line` 是「N 个文件夹、M 条笔记」这种已经排好的句子（由
        backup.summary 给）—— 备份是压缩的还是明文的、来自哪个 schema 版本，
        到这一步都已经解开，这里只报个数。
        """
        p = self.pal
        win = self._dlg_body("导入数据")
        res = {"v": None}
        tk.Label(win, text="备份文件内容：%s" % content_line,
                 bg=p["top"], fg=p["text"], font=(FONT[0], 12)).pack(
                     anchor="w", padx=dpi.px(18), pady=(dpi.px(16), dpi.px(4)))
        if exported_at:
            tk.Label(win, text="导出时间：%s" % exported_at, bg=p["top"],
                     fg=p["soft"], font=(FONT[0], 11)).pack(anchor="w", padx=dpi.px(18))
        tk.Label(win, text="当前数据：%d 个文件夹、%d 条笔记。请选择导入方式：" %
                 (len(self.data["folders"]), len(self.data["notes"])),
                 bg=p["top"], fg=p["soft"], font=(FONT[0], 11)).pack(
                     anchor="w", padx=dpi.px(18), pady=(dpi.px(6), dpi.px(2)))
        # 两种方式的差别必须写清：覆盖会连外观一起换回备份的样子，合并只动内容。
        # 两行各自成句（wraplength 430 大于最长那行的 408px），断行位置由文案决定，
        # 不靠窗口宽度去折 —— 否则换个字体就折成三行、跟上面的信息行错位。
        tk.Label(win, text="覆盖 = 用备份完全替换现有数据，外观设置也按备份恢复；\n"
                           "合并 = 保留现有内容，只追加本机没有的笔记，内容相同的自动跳过。",
                 bg=p["top"], fg=p["soft"], font=(FONT[0], 10), justify="left",
                 wraplength=dpi.px(430)).pack(anchor="w", padx=dpi.px(18))

        def choose(v):
            res["v"] = v
            win.destroy()

        row = tk.Frame(win, bg=p["top"])
        row.pack(fill=tk.X, padx=dpi.px(14), pady=(dpi.px(12), dpi.px(4)))
        ow = RoundButton(row, p, "覆盖现有数据", command=lambda: choose("overwrite"),
                         base=p["editor"], fg=p["accent"], outline=p["line"])
        ow.pack(side=tk.LEFT, padx=dpi.px(4))
        ow.bind_events()
        mg = RoundButton(row, p, "合并追加", command=lambda: choose("merge"),
                         base=p["editor"], outline=p["line"])
        mg.pack(side=tk.LEFT, padx=dpi.px(4))
        mg.bind_events()
        cc = RoundButton(row, p, "取消", command=lambda: choose(None),
                         base=p["editor"], outline=p["line"])
        cc.pack(side=tk.RIGHT, padx=dpi.px(4))
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

    def _apply_new_data(self, new):
        """导入/清空后统一收尾：重置选中态、搜索词、重建界面。"""
        self.data = new
        # 整份数据换掉了：上一篇的明文与密钥一律作废（引用它们只会指到不存在的笔记）
        self._editor_note = None
        self._keys = {}
        self._cur_pw = None     # 旧数据的口令对新数据毫无意义，一并忘掉
        self._folder_sel = set()
        self._folder_anchor = None
        self._note_sel = set()
        self._note_anchor = None
        try:
            self.search_var.set("")
        except Exception:
            pass
        self.body_size = self._read_body_size()
        # 覆盖导入会采纳备份里的外观（字号 / 字体 / 工具栏显隐）：当场重铺一遍，
        # 否则要重启才生效 —— 而「换机后外观回不来」正是这次要修的问题之一。
        self.ui_font = self._read_ui_font()
        try:
            self._apply_body_size(self.body_size)
        except Exception:
            pass
        self.refresh_all()
        self.load_editor()
        try:
            self._populate_toolbar()
        except Exception:
            pass

    def _open_color_panel(self):
        if not self.current_note():
            self._show_info("提示", "请先选择一条笔记。")
            return
        if not self.text.tag_ranges("sel"):
            self._show_info("提示", "请先选中要设置颜色的文字。")
            return
        self._show_color_panel()

    def _show_color_panel(self):
        """文字颜色 / 下划线颜色合并在同一个面板里，左右两栏各一套色板。

        合并之前是两个独立按钮（U 颜色 / A 颜色），点开的是两个几乎一样的窗口；
        收成一个 A ▾ 之后工具栏少一个按钮，选色时也不用开两次窗。
        """
        p = self.pal
        dlg = tk.Toplevel(self.root)
        dlg.title("颜色")
        dlg.transient(self.root)
        dlg.resizable(False, False)
        dlg.configure(bg=p["editor"])

        tk.Label(dlg, text="颜色", bg=p["editor"], fg=p["text"],
                 font=(FONT[0], 13, "bold")).pack(pady=(dpi.px(12), 0))
        tk.Label(dlg, text="作用于当前选中的文字", bg=p["editor"], fg=p["soft"],
                 font=(FONT[0], 11)).pack(pady=(0, dpi.px(10)))

        wrap = tk.Frame(dlg, bg=p["editor"])
        wrap.pack(padx=dpi.px(10), pady=(0, dpi.px(4)))

        def pick_native(ttl, cb):
            from tkinter import colorchooser
            _rgb, hexv = colorchooser.askcolor(title=ttl, parent=dlg)
            if hexv:
                cb(hexv)
                dlg.destroy()

        for title, cb, native_title in (
                ("文字颜色", self.apply_color, "选择文字颜色"),
                ("下划线颜色", self.apply_underline_color, "选择下划线颜色")):
            col = tk.Frame(wrap, bg=p["editor"])
            col.pack(side=tk.LEFT, padx=dpi.px(10))
            tk.Label(col, text=title, bg=p["editor"], fg=p["text"],
                     font=(FONT[0], 12, "bold")).pack(anchor="w", pady=(0, dpi.px(6)))
            grid = tk.Frame(col, bg=p["editor"])
            grid.pack()
            for i, hexv in enumerate(self.COLOR_SWATCHES):
                # activebackground 必须与自身同色：不设的话按下会被系统默认灰覆盖
                b = tk.Button(grid, width=3, height=1, bg=hexv, relief=tk.RAISED, bd=dpi.px(1),
                              activebackground=hexv, activeforeground=hexv,
                              cursor="hand2",
                              command=lambda hv=hexv, f=cb: (f(hv), dlg.destroy()))
                b.grid(row=i // 6, column=i % 6, padx=dpi.px(4), pady=dpi.px(4))
            tk.Button(col, text="自定义颜色…", bg=p["editor"], fg=p["accent"],
                      bd=0, relief=tk.FLAT, font=(FONT[0], 11),
                      command=lambda t=native_title, f=cb: pick_native(t, f),
                      activebackground=p["hover"], activeforeground=p["selfg"],
                      cursor="hand2").pack(anchor="w", pady=(dpi.px(6), 0))

        tk.Button(dlg, text="取消", bg=p["editor"], fg=p["soft"], bd=0, relief=tk.FLAT,
                  font=(FONT[0], 12), command=dlg.destroy,
                  activebackground=p["hover"], activeforeground=p["text"],
                  cursor="hand2").pack(pady=(dpi.px(6), dpi.px(14)))

        dlg.update_idletasks()
        w = dlg.winfo_reqwidth()
        h = dlg.winfo_reqheight()
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - w) // 2)
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - h) // 3)
        dlg.geometry("+%d+%d" % (x, y))
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        try:
            dlg.grab_set()
        except Exception:
            pass

    def _mk_fmt(self, parent, label, cmd, **kw):
        st = []
        if kw.get("bold"): st.append("bold")
        if kw.get("italic"): st.append("italic")
        if kw.get("underline"): st.append("underline")
        if kw.get("strike"): st.append("overstrike")
        f = (FONT[0], 12) + tuple(st)
        # 圆角按钮（Canvas 自绘）：按下不会出现系统默认灰，圆角/间距也可控
        # pady=4 / 外边距 3：按钮高 = 行高 21 + 8 = 29，占 35，正好塞进 36 的条
        # padx=10 / 外边距 2：与 _mk_icon 同一套缩紧后的横向预算（见那边的说明）
        b = RoundButton(parent, self.pal, label, command=cmd, font=f, padx=dpi.px(10), pady=dpi.px(4),
                        base=self._tool_bg())
        b.pack(side=tk.LEFT, padx=dpi.px(2), pady=dpi.px(3))
        b.bind_events()
        self.fmt_buttons.append(b)
        if kw.get("tip"):
            self._add_tip(b, kw["tip"])
        return b

    def _sep(self, parent):
        # 17 高 + 上下各 8：在 35 的内容高里正好落在 9..25，中线 17.5 与按钮同高
        # 左右 5：与按钮的 2px 外边距凑成「按钮 2 | 空 5 | 线 1 | 空 5 | 按钮 2」
        s = tk.Frame(parent, width=dpi.px(1), bg=self._tool_line(), height=dpi.px(17))
        s.pack(side=tk.LEFT, padx=dpi.px(5), pady=dpi.px(8))
        self._tool_seps.append(s)

    def refresh_folders(self, keep_scroll=False):
        """重建左栏整列。

        keep_scroll=True 时保留滚动位置（收/放子文件夹专用）：整列重建要先销毁
        所有行，那一刻滚动区高度被压到 0，Tk 立刻把偏移钳回 0 —— 行建回来之后
        整列已经弹回顶部，看上去就是「展开一个文件夹，别的全跳了一下」。
        所以先把视口顶端此刻对应的画布 y（= 当前滚动偏移的像素值）记下来，
        建完再按新的滚动区高度放回去：点中的那一行原地不动，新增的子行在它下面展开。
        """
        p = self.pal
        keep_off = None
        if keep_scroll:
            try:
                keep_off = self.folder_panel.canvas.canvasy(0)
            except Exception:
                keep_off = None
        for w in list(self.folder_panel.inner.children.values()):
            w.destroy()
        self._stop_all_marquee()     # 行即将销毁，先停掉跑马灯计时器
        self._folder_rows = {}
        self._folder_count_labels = {}
        self._folder_name_labels = {}
        self._folder_icons = {}
        self._folder_twisty = {}
        self._fold_header = None     # 「文件夹」标题 Frame：整列重建后换新（见 _reorder_...）
        # 分两组：智能分类（按条件筛的视图）在上，真实文件夹在下。
        # 分组只靠 smart 标记分，不依赖 data["folders"] 里的先后 —— 导入来的数据
        # 顺序可能很乱，但 smart 标记是_ensure_smart_folders 保证过的。
        smart = [f for f in self.data["folders"] if f.get("smart")]
        real = [f for f in self.data["folders"] if not f.get("smart")]
        self._make_group_label("智能分类", top=0)
        for f in smart:
            self._make_folder_row(f)
        self._make_group_label("文件夹", top=dpi.px(12), fold_tools=True)
        # 真实文件夹按 **树** 渲染（缩进 + 展开三角）。data["folders"] 仍是平铺的
        # 一维表，父子关系只由 f["parent"] 决定，顺序 = 数组里的先后。
        self._render_folder_tree(real)
        # 显式刷新滚动区，确保文件夹过多时滚动条启用、可拖（多重延迟兜底窗口时序）
        self.folder_panel.inner.update_idletasks()
        self.folder_panel._update()
        # 延后的两次 _update 会按最终行高再算一次滚动区：跟着再放一次偏移，
        # 否则「先按错的高度算 → 再算对」这两步之间滚动位置会漂一次
        def _upd():
            self.folder_panel._update()
            if keep_off is not None:
                self._restore_folder_scroll(keep_off)

        if keep_off is not None:
            self._restore_folder_scroll(keep_off)
        self.folder_panel.canvas.after(30, _upd)
        self.folder_panel.canvas.after(120, _upd)
        # 行被重建后需重新绑定滚轮（否则指针停在行/图标上滚不动）
        self.folder_panel.bind_wheel()
        # 行重建 = 新控件：指针若正停在某个名字上，收不到 Enter，这里补一次
        self._after(120, self._resume_marquee_under_pointer)

    def _restore_folder_scroll(self, off):
        """把左栏滚到「画布 y = off 贴在视口顶端」的位置（off 为像素）。

        Tk 的 yview_moveto 吃的是**比例**（偏移 / 滚动区总高），而总高在重建过程中
        会变（先按旧行高算、再按新行高算），所以每次都得现取 scrollregion 重算；
        直接把旧比例塞回去会漂。
        """
        try:
            cv = self.folder_panel.canvas
            self.folder_panel._update()
            sr = (cv.cget("scrollregion") or "").split()
            h = float(sr[3]) if len(sr) == 4 else 0.0
            if h <= 0:
                return
            cv.yview_moveto(min(1.0, max(0.0, off / h)))
            if getattr(self.folder_panel, "sb", None) is not None:
                self.folder_panel.sb.sync(*cv.yview())
        except Exception:
            pass

    def _scroll_folder_into_view(self, fid):
        """把左栏滚到刚好露出这个文件夹行（已在视口里就不动）。

        ⚠️ 展开祖先走的是 `_insert_folder_rows` → `_after_folder_toggle`，后者会在
        30ms / 120ms 两个时间点把「旧偏移」再放回去一次。所以这里立刻滚一次之后
        还要在 150ms 补一次，否则刚滚到位就被延后回填拽回原处。两次都是幂等的。
        """
        def go():
            row = self._folder_rows.get(fid)
            wrap = getattr(row, "_wrap", None) if row is not None else None
            if wrap is None:
                return
            try:
                if not wrap.winfo_exists():
                    return
                self._restore_folder_scroll(
                    self._scroll_to_show(wrap, self.folder_panel.canvas.canvasy(0)))
            except Exception:
                pass

        go()
        self._after(150, go)

    def _refresh_folder_counts(self):
        """就地刷新左栏每个文件夹的条数。

        新建/删除/移动笔记时调用：文件夹行不重建（避免闪烁），只改计数徽章。
        """
        for fid, badge in list(self._folder_count_labels.items()):
            if badge is None:
                continue
            try:
                if not badge.winfo_exists():
                    continue
                badge.set_count(len(self.folder_notes(fid)))
            except Exception:
                pass

    def _card_layer(self, parent, color, outside, accent=None):
        """在行容器内铺一层圆角背景（Canvas + place），返回该 Canvas。

        Tk 的 Frame 是矩形、无法圆角，所以背景交给 Canvas 画：画布底色是「卡片外」
        的面板色，只有圆角多边形内部才是卡片色 —— 两者不能同色，否则圆角会被填满、
        看上去还是方块。

        accent 给定时再在左缘画一条圆头色条（笔记的辨识色）。**必须画在这里**：
        悬停 / 选中换底色走的是同一个 `draw()`，色条跟着一起重画，四周永远是当前
        底色；另贴一块 Canvas 的话，换底色时色条周围会露出一圈旧色的小方块。
        """
        cv = tk.Canvas(parent, bg=outside, bd=0, highlightthickness=0)
        cv.place(relx=0, rely=0, relwidth=1, relheight=1)
        # 压到最底层，内容控件画在它上面。
        # 注意：Canvas.lower() 是「画布内图元降层」，widget 降层必须走 Tk 原生命令
        tk.Misc.lower(cv)
        cv._color = color
        cv._accent = accent

        def draw(ev=None):
            w, h = cv.winfo_width(), cv.winfo_height()
            if w < 3 or h < 3:
                return
            cv.delete("all")
            cv.create_polygon(_round_rect_points(0.5, 0.5, w - 0.5, h - 0.5,
                                                 CARD_RADIUS, 6),
                              fill=cv._color, outline="")
            if cv._accent:
                _draw_accent_bar(cv, w, h, cv._accent)

        cv._draw = draw
        cv.bind("<Configure>", draw)
        return cv

    def _paint_star(self, nid, rec, bg):
        """把一行上的星标按钮刷成它应有的样子（图标 + 前景 + 悬停前景一起）。

        星标状态**每次从笔记数据读**，不读 `rec["starred"]` 那份快照：快照是建行
        那一刻的副本，`toggle_star` 之后没跟着改，而切换笔记会走 `_restyle_notes` /
        `_set_note_active` / `_restore_note_row` 重刷整行样式 —— 这几处照着旧快照
        把颜色刷回去，用户看到的就是「点亮的星标切走一趟又变灰了」。
        """
        n = next((x for x in self.data["notes"] if x["id"] == nid), None)
        starred = bool(n and n.get("starred"))
        fg = self.pal["star"] if starred else self.pal["soft"]
        try:
            rec["sb"].configure(text="★" if starred else "☆", bg=bg,
                                activebackground=bg, fg=fg, activeforeground=fg)
        except Exception:
            pass
        # 快照一并刷新：即使有别处仍照它读，也不会再拿旧值覆盖刚才点燃的星
        rec["starred"] = starred

    def _paint_card(self, row, color):
        """把行的圆角背景换成指定底色（选中/悬停/普通切换时调用）。"""
        cv = getattr(row, "_card", None)
        if cv is None or not cv.winfo_exists():
            return
        cv._color = color
        cv._draw()

    def _make_group_label(self, text, top=dpi.px(10), fold_tools=False):
        """左栏的分组标题（智能分类 / 文件夹）。

        纯装饰：不进 `_folder_rows`、不参与选中与拖放。放进那张表的话，
        Shift 区间、拖拽落点、「当前文件夹」遍历都会把它当成一行文件夹。

        fold_tools=True 时在这一行右缘挂两枚「竖向双箭尖」（全部展开 / 全部收起）。
        之所以挂在标题行而不是每行文件夹上：这是**整列**级动作，每行都摆一对同样的
        按钮既挤又没意义（整列动作放行级，点哪行都一样）。
        """
        p = self.pal
        house = tk.Frame(self.folder_panel.inner, bg=p["side"])
        house.pack(fill=tk.X, padx=dpi.px(16), pady=(top, dpi.px(3)))
        if fold_tools:
            # 「文件夹」那一行是真实文件夹行的起点：就地同步后要重排时，
            # 第一行得钉在它后面（没有这个引用就只能靠「追加到末尾」猜）
            self._fold_header = house
        lbl = tk.Label(house, text=text, bg=p["side"], fg=p["soft"], anchor="w",
                       font=(FONT[0], 9, "bold"))
        lbl.pack(side=tk.LEFT)
        if fold_tools:
            # 顺序照参考稿：左边朝外（展开）、右边朝内（收起）。pack(side=RIGHT) 是
            # 「后 pack 的贴得更左」，所以先放收起那枚，视觉上才是「展开 | 收起」。
            # base 必须给侧栏色：IconButton 默认吃顶栏色，悬停底会浮出一块别色的方片。
            CollapseAllButton(house, p, command=self.collapse_all_folders,
                              base=p["side"]).pack(side=tk.RIGHT)
            ExpandAllButton(house, p, command=self.expand_all_folders,
                            base=p["side"]).pack(side=tk.RIGHT, padx=(0, dpi.px(2)))
        # 分组标题也是一整条空白带：右键在这里同样能新建文件夹（不留「点了没反应」的死角）。
        # 标题是被 Frame 包着的独立控件，事件不会冒泡，两处都得绑。
        for w in (house, lbl):
            w.bind("<Button-3>", lambda e: self._folder_blank_popup(e))
        return lbl

    def _render_folder_tree(self, folders):
        """把文件夹列表按 parent 关系渲染成树（深度优先、同层按数组先后）。

        折叠的那一支**整支不建行**（而不是建了再隐藏）：省掉一堆用不上的控件，
        更重要的是行字典里只有看得见的行 —— 后面 Shift 区间多选、换肤重刷、
        hover 复原都是照这张表遍历的，藏着看不见的行会让多选范围莫名其妙。

        「谁该露出来、第几层」全在 `_visible_folder_items` 里算，这里只负责建行：
        整列收放要能拿到同一份列表做对比（谁该留、谁该走），两处必须同源。
        """
        for f, depth in self._visible_folder_items(folders):
            self._make_folder_row(f, depth)

    def _collect_visible_tree(self, fid, depth, out, seen=None):
        """按渲染顺序收集这一支里「此刻该露出来」的后代（碰到收起的就停在那儿）。

        与 `_render_folder_tree` 同一套深度优先顺序，只是只走一支。
        seen 防环：导入来的脏数据理论上已被 normalize_folder_parents 清过，
        这里再兜一层，免得成环时把界面卡死。
        """
        seen = seen if seen is not None else set()
        if fid in seen:
            return
        seen.add(fid)
        for k in folder_children(fid, self.data["folders"]):
            out.append((k, depth))
            if not k.get("collapsed"):
                self._collect_visible_tree(k["id"], depth + 1, out, seen)

    def _packed_after(self, wrap):
        """内层层里紧跟在 wrap 后面的那个兄弟（没有就返回 None = 追加到末尾）。"""
        try:
            slaves = self.folder_panel.inner.pack_slaves()
            i = slaves.index(wrap)
            return slaves[i + 1] if i + 1 < len(slaves) else None
        except Exception:
            return None

    def _scroll_shift_for(self, widgets, off):
        """这批控件里有多少像素落在「视口顶端之上」。

        增删行时滚动偏移要按它校正：视口顶端的那一行必须还是同一行，否则
        上面凭空少/多一段，下面的内容就整体往上/下窜 —— 那才是用户看到的「跳动」。
        """
        try:
            cv = self.folder_panel.canvas
            top = cv.winfo_rooty()
            s = 0
            for w in widgets:
                if w is None or not w.winfo_exists():
                    continue
                cy = w.winfo_rooty() - top + off      # 该行在画布里的 y
                h = w.winfo_height()
                s += max(0, min(cy + h, off) - cy)    # 与「视口顶端之上」的重叠部分
            return s
        except Exception:
            return 0

    def _insert_folder_rows(self, fid):
        """展开：只把这一支新露出来的行插进列表，不碰其它任何一行。

        整列重建（refresh_folders）会把所有行销毁再建一遍 —— 控件全换新的，
        视觉上就是「别的文件夹跟着跳一下」。这里靠 pack(before=) 就地插入，
        点中的那一行和它上面的一律不动。
        """
        row = self._folder_rows.get(fid)
        if row is None:
            return False
        wrap = getattr(row, "_wrap", None)
        if wrap is None or not wrap.winfo_exists():
            return False
        cv = self.folder_panel.canvas
        off = cv.canvasy(0)
        before = self._packed_after(wrap)
        depth = getattr(row, "_depth", 0)
        items = []
        self._collect_visible_tree(fid, depth + 1, items)
        made = []
        for f, d in items:
            r = self._make_folder_row(f, d, before=before)
            made.append(getattr(r, "_wrap", r))
        self.folder_panel.inner.update_idletasks()
        # 新行整段落在视口顶端之上时，偏移要跟着往下补，否则下面被凭空顶下去
        self._after_folder_toggle(off + self._scroll_shift_for(made, off))
        return True

    def _drop_folder_row(self, fid):
        """销毁一行，并把它在五张附表里的登记一起清掉。行不存在返回 False。

        `_remove_folder_rows`（收一支）和 `_sync_folder_rows`（整列收放）共用，
        免得哪天加了新附表只在一处补。
        """
        row = self._folder_rows.pop(fid, None)
        if row is None:
            return False
        self._folder_name_labels.pop(fid, None)
        self._folder_count_labels.pop(fid, None)
        self._folder_icons.pop(fid, None)
        self._folder_twisty.pop(fid, None)
        self._stop_marquee(fid)          # 名字跑马灯的计时器随行一起走
        w = getattr(row, "_wrap", None) or row
        try:
            w.destroy()
        except Exception:
            pass
        return True

    def _remove_folder_rows(self, fid):
        """收起：只销毁这一支里已经渲染出来的后代行，其它行原样留着。"""
        cv = self.folder_panel.canvas
        off = cv.canvasy(0)
        ids = set(subtree_ids(fid, self.data["folders"])) - {fid}
        doomed = [getattr(self._folder_rows.get(k), "_wrap", None)
                  for k in ids if self._folder_rows.get(k) is not None]
        # 先算「被删掉的行里有多少在视口顶端之上」→ 偏移相应往回退，屏幕上的行才不动
        off -= self._scroll_shift_for([d for d in doomed if d is not None], off)
        for kid in ids:
            self._drop_folder_row(kid)
        self._after_folder_toggle(off)
        return True

    def _sync_folder_rows(self):
        """按当前 collapsed 状态**就地**同步左栏行：只增 / 删 / 挪，不重建整列。

        为什么需要它：「全部展开 / 全部收起」原来走 refresh_folders —— 把所有行
        destroy 掉再建一遍。销毁与重建之间隔着一次 idle，那一瞬列表是空的，
        用户看到的就是「点一下白闪一下」。整列收放改的只是「哪几行该露出来」，
        控件完全不用换。

        滚动用「钉住视口顶端那一行」的办法，而不是累加像素：收放会同时删/建
        几十行，逐行估误差会越积越多；钉住一行再反算偏移，误差只有这一行的高度。

        返回 False = 状态不可用（一行都没有 / 拿不到顺序），调用方退回整列重建。
        """
        try:
            items = self._visible_folder_items()
        except Exception:
            return False
        if not items:
            return False
        want = [f["id"] for f, _ in items]
        want_set = set(want)
        real = self._real_folder_ids()      # 只认真实文件夹：智能分类那三行永远是常驻的
        cv = self.folder_panel.canvas
        off = cv.canvasy(0)
        # 先把基准行钉住：收放之后它的相对位置必须不变。
        # 传 want_set：基准行若正是即将被收起的那支里的一行，换成它的前一个可见行。
        anchor = self._folder_anchor_wrap(off, want_set, real)

        cur = [w._fid for w in self.folder_panel.inner.pack_slaves()
               if getattr(w, "_fid", None) in real and w._fid in self._folder_rows]
        for fid in [k for k in cur if k not in want_set]:
            self._drop_folder_row(fid)

        for f, depth in items:
            fid = f["id"]
            row = self._folder_rows.get(fid)
            wrap = getattr(row, "_wrap", None) if row is not None else None
            if wrap is not None and wrap.winfo_exists():
                continue
            # 插在「目标顺序里它后面第一个已经存在的行」前面：连续插的新行会按
            # 建行顺序落在同一个锚点上（pack -before 后插的排在先插的之后），正好是目标顺序
            self._make_folder_row(f, depth, before=self._row_before_for(fid))

        self.folder_panel.inner.update_idletasks()
        # 顺序万一还被搅乱（脏数据 / 上一轮半途而废），整体按目标顺序排一次。
        # 只比真实文件夹行：智能分类那三行固定在前面，不参与
        order = [w._fid for w in self.folder_panel.inner.pack_slaves()
                 if getattr(w, "_fid", None) in real and w._fid in self._folder_rows]
        if order != want:
            self._reorder_folder_rows(want)
            self.folder_panel.inner.update_idletasks()

        new_off = off
        if anchor is not None:
            w, dy = anchor
            try:
                if w.winfo_exists():
                    y = w.winfo_rooty() - cv.winfo_rooty() + cv.canvasy(0)
                    new_off = max(0.0, y - dy)
            except Exception:
                pass
        self._after_folder_toggle(new_off)
        return True

    def _real_folder_ids(self):
        """真实文件夹（非智能分类）的 id 集合。

        智能分类那三行（全部 / 星标 / 加密）也是 `_folder_rows` 里的一行、也带 `_fid`，
        但它们**永远常驻**、跟 collapsed 没有关系 —— 整列收放要拿「真实文件夹」
        这个集合把行筛一遍，否则它们会被当成「不该露出来的行」删掉。
        """
        return set(f["id"] for f in self.data["folders"] if not f.get("smart"))

    def _folder_anchor_wrap(self, off, keep=None, real=None):
        """找出视口顶端那一行，返回 (wrap, 它相对视口的 y)。拿不到就返回 None。

        相对 y 是 ≤ 0 的数（行顶可能落在视口上方）。收放后按它反算偏移，
        这一行就会停在屏幕上原来的位置 —— 用户不会看到整列上下窜。
        若那一行正好要被收起（`keep` 是收完之后还在的 id 集合），改用它的前一个
        可见行、并且钉住那一行的**下沿**：父行不会被收掉，所以它一定还在。
        `real` 给了就只看真实文件夹行（智能分类行挡在视口顶上时继续往下找）。
        """
        real = real if real is not None else self._real_folder_ids()
        try:
            cv = self.folder_panel.canvas
            top = cv.winfo_rooty()
            slaves = [w for w in self.folder_panel.inner.pack_slaves()
                      if w.winfo_exists()]
            for i, w in enumerate(slaves):
                fid = getattr(w, "_fid", None)
                if fid not in real or fid not in self._folder_rows:
                    continue                 # 标题行 / 智能分类行：不算基准
                y = w.winfo_rooty() - top + off
                if y + w.winfo_height() <= off:
                    continue                 # 整行都在视口上方
                if keep is None or fid in keep:
                    return (w, y - off)
                prev = self._prev_folder_wrap(slaves, i, real)
                if prev is not None:
                    py = prev.winfo_rooty() - top + off
                    return (prev, py - off + prev.winfo_height())
                return None
        except Exception:
            pass
        return None

    def _prev_folder_wrap(self, slaves, idx, real=None):
        """slaves[idx] 之前最近的一个真实文件夹行的 wrap（跨过分组标题）。"""
        real = real if real is not None else self._real_folder_ids()
        for w in reversed(slaves[:idx]):
            fid = getattr(w, "_fid", None)
            if fid in real and fid in self._folder_rows:
                return w
        return None

    def _reorder_folder_rows(self, want):
        """按目标顺序把已有的行重排（pack 重排，零销毁）。

        第一行要落在「文件夹」分组标题之后，所以得先找得到那个标题 Frame
        （`_make_group_label` 里存过引用，标题行重建时会跟着换）。
        """
        hdr = getattr(self, "_fold_header", None)
        if hdr is not None:
            try:
                if not hdr.winfo_exists():
                    hdr = None
            except Exception:
                hdr = None
        prev = None
        for fid in want:
            row = self._folder_rows.get(fid)
            wrap = getattr(row, "_wrap", None) if row is not None else None
            if wrap is None or not wrap.winfo_exists():
                continue
            try:
                if prev is not None:
                    wrap.pack_configure(after=prev)
                elif hdr is not None:
                    wrap.pack_configure(after=hdr)
            except Exception:
                pass
            prev = wrap

    def _after_folder_toggle(self, off):
        """就地增删行之后的收尾：滚动区、滚轮绑定、图标与选中态。

        ⚠️ `ScrollPanel._update()` 里的 `_nudge_reqheight` 会把内层 Frame 瞬间压成
        1×1 再放开（为了逼 Tk 重算请求高度），这一步会把滚动偏移钳回 0；所以必须
        先记住偏移、更新完再放回去 —— 否则「就地插入」省下的那点位移全被它吃掉。
        """
        self.folder_panel.inner.update_idletasks()
        self.folder_panel._update()
        self._restore_folder_scroll(off)

        def _upd():
            self.folder_panel._update()
            self._restore_folder_scroll(off)

        self.folder_panel.canvas.after(30, _upd)
        self.folder_panel.canvas.after(120, _upd)
        # 新行是新控件：滚轮要重新绑（指针停在新行上才滚得动）
        self.folder_panel.bind_wheel()
        # 就地插入是 append 进字典的 → 键序要和屏幕顺序对齐（Shift 多选靠它取区间）
        self._resync_folder_row_order()
        # 图标 / 三角 / 选中态重画（只改样式，不重建行）
        self._restyle_folders()
        self._after(120, self._resume_marquee_under_pointer)

    def _resync_folder_row_order(self):
        """按屏幕上的实际顺序重排 `_folder_rows`（就地增删行之后必做）。

        这张表的**键序**就是 Shift 区间多选的取序。就地插入是 append 进字典的，
        键序会与屏幕顺序脱节 —— 展开一支之后再 Shift 多选，区间会串到别的文件夹上。
        """
        try:
            order = [w._fid for w in self.folder_panel.inner.pack_slaves()
                     if getattr(w, "_fid", None) in self._folder_rows]
            missing = [k for k in self._folder_rows if k not in order]
            self._folder_rows = {k: self._folder_rows[k] for k in order + missing}
        except Exception:
            pass

    def _visible_folder_items(self, folders=None):
        """按渲染顺序生成 [(folder, depth)]（真实文件夹；收起的支整支跳过）。

        这就是左栏**此刻该有哪几行**的唯一答案：`_render_folder_tree` 照着它建行，
        整列收放照着它对比「谁该留、谁该走、谁还没建」。两处共用一份，才不会有
        「重建时少一行 / 同步时多一行」这种对不上的情况。

        顺序：深度优先、同层按数组先后；收起的那一支整支跳过。
        兜底：parent 悬空的行（导入的脏数据）挂到根层补出来，总比界面上凭空消失好；
        但**藏在收起那支里的不算** —— 那是用户自己收的，补回来等于又摊开了。
        """
        fs = [f for f in (self.data["folders"] if folders is None else folders)
              if not f.get("smart")]
        out = []
        rendered = set()
        stack = [(f, 0) for f in reversed(folder_children(None, fs))]
        while stack:
            f, depth = stack.pop()
            fid = f.get("id")
            if not fid or fid in rendered:
                continue
            rendered.add(fid)
            out.append((f, depth))
            if not f.get("collapsed"):
                stack.extend((k, depth + 1)
                             for k in reversed(folder_children(fid, fs)))
        for f in fs:
            fid = f.get("id")
            if not fid or fid in rendered or self._hidden_by_collapse(fid):
                continue
            rendered.add(fid)
            out.append((f, 0))
        return out

    def _row_before_for(self, fid):
        """fid 这一行该插在谁前面（返回那个兄弟的 wrap；None = 追加到末尾）。"""
        items = self._visible_folder_items()
        idx = next((i for i, (f, _) in enumerate(items) if f.get("id") == fid), None)
        if idx is None:
            return None
        for f, _ in items[idx + 1:]:
            row = self._folder_rows.get(f.get("id"))
            w = getattr(row, "_wrap", None) if row is not None else None
            if w is not None and w.winfo_exists():
                return w
        return None

    def _scroll_to_show(self, w, off):
        """行 w 不在可视区时把偏移挪到刚好看得见它（已在区里就原样返回）。"""
        try:
            cv = self.folder_panel.canvas
            y = w.winfo_rooty() - cv.winfo_rooty() + off
            h = w.winfo_height()
            vh = cv.winfo_height()
            if y < off:
                return max(0.0, y)
            if y + h > off + vh:
                return max(0.0, y + h - vh)
        except Exception:
            pass
        return off

    def _insert_new_folder_row(self, f):
        """新建文件夹后就地把那一行插进列表，**不重建整列**。

        重建整列（refresh_folders）要销毁所有行再建一遍，控件全换成新的 —— 这就是
        「新建完一个文件夹，别的全跳一下」。新文件夹是叶子，插进去不影响任何一行的
        缩进，所以完全可以只加一行。
        """
        cv = self.folder_panel.canvas
        off = cv.canvasy(0)
        pid = f.get("parent")
        if pid:
            pf = next((x for x in self.data["folders"] if x["id"] == pid), None)
            # 父收着：新文件夹会藏在里面看不见（用户会以为没建成）→ 直接展开整支
            if pf is not None and pf.get("collapsed"):
                return self._insert_folder_rows(pid)
        items = self._visible_folder_items()
        depth = next((d for ff, d in items if ff.get("id") == f["id"]), 0)
        row = self._make_folder_row(f, depth, before=self._row_before_for(f["id"]))
        wrap = getattr(row, "_wrap", row)
        self.folder_panel.inner.update_idletasks()
        # 新行若整段落在视口顶端之上，偏移要跟着补，否则下面的行被凭空顶下去
        off += self._scroll_shift_for([wrap], off)
        self._after_folder_toggle(self._scroll_to_show(wrap, off))
        return True

    def _hidden_by_collapse(self, fid):
        """这一行的某一级祖先是不是收起的（自己收起不算：它本人还得显示）。"""
        by = {f.get("id"): f for f in self.data["folders"]}
        cur = by.get(fid)
        hops = 0
        while cur is not None and hops < 32:
            pid = cur.get("parent")
            if not pid or pid == cur.get("id"):
                return False
            cur = by.get(pid)
            if cur is None:
                return False
            if cur.get("collapsed"):
                return True
            hops += 1
        return False

    def _make_folder_row(self, f, depth=0, before=None):
        """创建并追加单个文件夹行。

        depth 是这一行在第几层（0 = 根层）：整张卡片往右挪 depth × FOLDER_TREE_INDENT，
        前面再加一列展开三角（有子文件夹才画，叶子同伴留空占位让名字对齐）。

        before 给的是「插到它前面」的那个兄弟（同样是挂在内层 Frame 上的 wrap）：
        收/放子文件夹时靠它把新行**就地插进列表**，而不是重建整列。
        """
        p = self.pal
        cnt = len(self.folder_notes(f["id"]))
        kids = folder_children(f["id"], self.data["folders"])
        has_child = bool(kids) and not f.get("smart")
        collapsed = bool(f.get("collapsed")) and not f.get("smart")
        active = (f["id"] == self.data["current_folder"])
        base = p["sel"] if active else p["side"]
        # 同样用实底色，避免透明 Frame 不重绘导致的高亮残留
        indent = depth * FOLDER_TREE_INDENT
        wrap = tk.Frame(self.folder_panel.inner, bg=p["side"])
        if before is not None:
            try:
                if not before.winfo_exists():
                    before = None
            except Exception:
                before = None
        if before is not None:
            # Tk 的 pack 支持 before= 插到指定兄弟之前 → 新行就地落在父行后面
            wrap.pack(fill=tk.X, padx=(CARD_PADX + indent, CARD_PADX),
                      pady=(CARD_GAP, 0), before=before)
        else:
            wrap.pack(fill=tk.X, padx=(CARD_PADX + indent, CARD_PADX), pady=(CARD_GAP, 0))
        row = tk.Frame(wrap, bg=base)
        row.pack(fill=tk.BOTH, expand=True)
        row._depth = depth
        row._wrap = wrap          # 收/放时要按 wrap 定位（插在谁前面 / 销毁谁）
        wrap._fid = f["id"]       # 反查用：按 pack 顺序重排行字典（见 _resync_...）
        row._card_outside = p["side"]
        row._card = self._card_layer(row, base, p["side"])
        # 行首的「▶ / ▼」展开箭头：点它收/放这一支。
        # 24×28 的可点区（原来 18×18，再早是 13×16）：高 28 正好顶满一行
        # （行高由文字撑到 38~39，pack 会在竖直方向居中）。⚠️ 点击区不许缩回去，
        # 用户两次反馈「太小不好点」。箭头是 9px 填充实心三角、常态就用正文色，
        # 静态也看得出可点；指针停上来才浮一层 16×16 圆角底。叶子同样占这一格
        # （不画箭头、不变手形），名字才对得齐。
        tw = TwistyButton(row, self.pal, collapsed=collapsed, available=has_child,
                          base=base,
                          command=(lambda fid=f["id"]: self._twisty_click(fid)))
        tw.pack(side=tk.LEFT, padx=(dpi.px(2), 0))
        self._folder_twisty[f["id"]] = tw
        if has_child:
            # 右键：弹不出文件夹菜单，也别让它选中（编组贴左边这是对 kid 的尊重）
            tw.bind("<Button-3>", lambda e: "break")
            # 不用悬浮提示（用户要求静默）：指针停上来只浮底片，不弹气泡
            tw._tw_bound = True   # 叶子后来长出子文件夹时，_restyle_folders 要补绑
        # 然后是文件夹图标（自绘；智能文件夹用区别图标，普通文件夹用圆角文件夹）
        icon = tk.Canvas(row, width=FOLDER_ICON_W, height=FOLDER_ICON_H,
                         bg=base, bd=0, highlightthickness=0)
        icon.pack(side=tk.LEFT, padx=(dpi.px(4), dpi.px(5)), pady=dpi.px(6))
        self._folder_icons[f["id"]] = icon
        # 悬停 / 选中换底色时，图标要跟着重画：画布底色本身是图标的"留白"，
        # 只 configure(bg=) 不重画，留在上面的图元还是按旧底色取的色（比如曾经的挖孔），
        # 会看着像一块脏点。统一走 refresh_bg，别在别处单独 configure。（_row_hover 会调它）
        icon.refresh_bg = lambda cv=icon, fid=f["id"]: self._draw_folder_icon(cv, fid)
        self._draw_folder_icon(icon, f["id"])
        # 右侧数量先 pack(side=RIGHT)：先把宽度占死，名字再长也压不到它。
        # 自绘的圆底徽章而不是 Label：数字要装在一个小圆里（Tk 的控件都是方的）。
        # 字号 10 比名字（12）小两档，行高由下面的名字盒决定（徽章只有 21 高，
        # 竖直方向自动居中）—— 所以它不会再把整行撑高。
        cl = CountBadge(row, cnt, bg=base,
                        fg=(p["selfg"] if active else p["soft"]),
                        ink=p["text"], font=self._measure_font(FONT[0], 10))
        cl.pack(side=tk.RIGHT, padx=(dpi.px(6), dpi.px(12)))
        # 名字装进「固定宽度 + 不随内容扩张」的盒子：超长部分被盒子裁掉，
        # 不会把右侧数字挤走（Tk 的 pack 会按请求宽度挤压邻居，必须靠盒子兜住）
        # 量的是 **bold** 而不是 normal：选中行标题是 bold，同字号下 linespace
        # 会多 1px（21→22），按 normal 量出来的盒子会把选中行的字底裁掉一条。
        # 这里不改字号、只把盒子做够，行高由它决定 → 正文本就撑得下两种字重。
        name_h = self._measure_font(FONT[0], 12, "bold").metrics("linespace")
        name_box = tk.Frame(row, bg=base, height=name_h)
        name_box.pack(side=tk.LEFT, fill=tk.X, expand=True, pady=dpi.px(4))
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
        row.bind("<Enter>", lambda e, r=row: self._row_hover(r, self._row_hover_bg(r), fb=p["side"]))
        row.bind("<Leave>", lambda e, r=row: self._row_hover(r, r._base_bg, fb=p["side"]))
        row.bind("<Button-1>", lambda e, fid=f["id"]: self._folder_click(e, fid))
        # 双击行 = 收起 / 展开这一支（有子文件夹才生效，见 _folder_double_click）
        row.bind("<Double-Button-1>", lambda e, fid=f["id"]: self._folder_double_click(fid))
        row.bind("<Button-3>", lambda e, fid=f["id"]: self._folder_popup(e, fid))
        for ch in list(row.winfo_children()) + [name_label]:
            # 背景层 Canvas 也要绑定：Canvas 不把鼠标事件冒泡给父 Frame。
            # ⚠️ Tk 的 bind 不带 add="+" 是「替换」不是「追加」——三角画布上先注册
            # 的收/放绑定若在这里照抄一遍就被顶掉了，表现成「点三角只选中、
            # 永远收不起来」。所以三角跳过重绑：它自己的两键绑定已经齐了
            # （左键收/放 + break，右键 break）。
            if ch is tw:
                continue
            ch.bind("<Button-1>", lambda e, fid=f["id"]: self._folder_click(e, fid))
            # 双击行 = 收起 / 展开这一支（三角自己管收/放，连点两下=收了又放，不叠加）
            ch.bind("<Double-Button-1>",
                    lambda e, fid=f["id"]: self._folder_double_click(fid))
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
        if self._resizing:
            # 拖分隔条期间跳过：截断要二分测量 + 重设 MarqueeLabel，十来行累计
            # 近 200ms/帧，逐帧跑就拖不动了。松手由 _refit_names_after_split 补。
            return
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

    def _refresh_note_row(self, nid):
        """就地把某一行的标题 / 预览刷新一遍，不重建整列（避免其它笔记闪一下）。

        加密笔记在两种时刻需要它：刚被重新封上（标题要退回「首字 + 星号」），
        以及刚用会话里的口令自动解开（标题要从遮蔽形态恢复成真标题）。
        """
        n = next((x for x in self.data["notes"] if x["id"] == nid), None)
        rec = self._rows.get(nid)
        if not n or not rec:
            return
        try:
            rec["row"]._full_name = self.title_of(n)
            self._fit_note_title(nid)
            rec["pl"].configure(
                text=self.trunc(self.preview_of(n), 22) or "暂无更多文字")
        except Exception:
            pass

    def _fit_note_title(self, nid):
        """把笔记标题按可用像素截断成「…」；空间尚未量出时随后重试。

        与文件夹名同一套逻辑：标题装在固定宽盒子里，超长被裁掉，右侧的
        星标 / 日期永远不会被挤走。
        """
        if self._resizing:
            return          # 同 _fit_folder_name：拖动期间不测，松手补一次
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

        - 智能分类：全部笔记 = 清单三线；星标笔记 = 五角星（金色）；加密笔记 = 挂锁。
        - 普通文件夹：随树状态切换 —— 收起 = 圆角空心（白底 + 主题色描边 + 左上小舌），
          展开 = 实心主题色前盖 + 探出的稿纸（见 _draw_folder_icon_open）。
          主体用 editor 白而非 sel 浅绿，因为选中行的底色就是 sel，浅绿填充会和行底糊在一起。
        """
        c.delete("all")
        self._draw_folder_icon_raw(c, fid)
        # 上面的坐标是按 18×16 逻辑格写死的，而画布尺寸已经乘过 DPI ——
        # 整体放大一次，图标才会铺满画布（100% 时 scale_canvas 是空操作）。
        dpi.scale_canvas(c)

    def _draw_folder_icon_raw(self, c, fid=None):
        p = self.pal

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

        if fid == SMART_ICON_LOCK:
            # 挂锁：上方半圆锁梁 + 下方圆角锁体。锁梁用圆弧（start=0 逆时针 180°
            # 正好是上半圈），两端被锁体压住，看上去是一体的。
            # 颜色吃正文墨色而不是主题色：锁是「状态」不是「强调」，且要比金星
            # 明显沉一档 —— 三颗图标并排时，金 = 星标、墨 = 上锁，一眼能分开。
            col = p["text"]
            c.create_arc(4.6, 1.6, 13.4, 10.4, start=0, extent=180, style=tk.ARC,
                         outline=col, width=1.8)
            body = _round_rect_points(3.2, 6.8, 14.8, 14.2, 2.2)
            c.create_polygon(body, fill=col, outline=col, width=0)
            return

        if fid == SMART_ICON_ALARM:
            # 任务提醒：一只方闹钟。⚠️ 18px 里一律整数矩形拼（Canvas 不抗锯齿，
            # oval / arc 会掉成一圈毛刺）：两枚铃铛 + 四条边框 + L 形指针 + 两只脚。
            # 颜色跟正文里那枚小闹钟一致（star 琥珀），一眼能对上「这是同一件事」。
            col = p["star"]
            for a, b, cc, d in (
                    (3, 1, 6, 3), (11, 1, 14, 3),        # 左右铃铛
                    (4, 3, 13, 5),                       # 顶边 + 连铃的横梁
                    (3, 5, 5, 13), (12, 5, 14, 13),      # 左右边框
                    (4, 12, 13, 14),                     # 底边
                    (8, 6, 9, 10), (9, 9, 11, 10),       # 时针 / 分针
                    (3, 14, 5, 15), (12, 14, 14, 15)):   # 两只脚
                c.create_rectangle(a, b, cc, d, fill=col, outline="", width=0)
            return

        if fid == SYSTEM_FOLDER:
            # 系统区：一台显示器（屏 + 支架 + 底座）。
            # ⚠️ 全用矩形拼，**一点曲线都不能有**：Tk 的 Canvas 不抗锯齿，
            # create_oval / create_arc 是折线硬栅格化 —— 18px 的圆环直径才 9 像素，
            # 弧线每走 1px 掉一格，再被 DPI 放大 1.66 倍，看上去就是一圈毛刺。
            # 所以连外框的圆角都不用：只留直角，放大后每条边仍是一条笔直的台阶。
            # ⚠️ 两条硬规矩（第一版就是栽在这里，看着像「虚线框 + 断开的底座」）：
            #   1. Tk 矩形填充是**半开区间** [x0,x1) × [y0,y1) —— (1,0,16,1) 只画出
            #      1 行，不是 2 行。想有 2px 厚就得写 x1-x0 = 2。
            #   2. 相邻矩形的共边必须写**同一个整数**（屏下边到 y=11、支架从 y=11 起），
            #      scale_canvas 乘同一个系数后仍严丝合缝；差 1 就会裂出一条底色缝。
            col = p["text"]
            c.create_rectangle(1, 0, 17, 2, fill=col, outline="", width=0)      # 上框
            c.create_rectangle(1, 9, 17, 11, fill=col, outline="", width=0)     # 下框
            c.create_rectangle(1, 2, 3, 9, fill=col, outline="", width=0)       # 左框
            c.create_rectangle(15, 2, 17, 9, fill=col, outline="", width=0)     # 右框
            c.create_rectangle(8, 11, 10, 14, fill=col, outline="", width=0)    # 支架
            c.create_rectangle(4, 14, 14, 16, fill=col, outline="", width=0)    # 底座
            return

        # --- 普通文件夹：收起 = 空心描边；展开 = 实心前盖 + 探出的一张纸 ---
        if self._folder_is_open(fid):
            self._draw_folder_icon_open(c)
        else:
            self._draw_folder_icon_closed(c)

    def _folder_is_open(self, fid):
        """这枚文件夹图标该画「展开」还是「收起」：有子文件夹、且没被收起来。

        树的展开/收起状态只由 f["collapsed"] 决定 —— 图标跟着这一个字段走，
        换肤重刷（_restyle_folders 重画图标）时自然取到最新状态。
        """
        f = next((x for x in self.data["folders"] if x.get("id") == fid), None)
        if not f or f.get("smart"):
            return False
        return bool(folder_children(fid, self.data["folders"])) and not f.get("collapsed")

    def _draw_folder_icon_closed(self, c):
        """收起的文件夹：左上小舌 + 空心圆角箱体（白底 + 主题色描边）。"""
        p = self.pal
        col = p["accent"]
        # 左上小舌（先画，主体压在其下缘，视觉上是一体伸出的）
        # 注意：关键边用整数坐标，否则 1px 描边落在半像素上会被 Tk 挪到相邻行，底边被挤出画布
        c.create_polygon(_round_rect_points(2.0, 2.5, 8.0, 6.2, 1.4),
                         fill=col, outline=col, width=0)
        # 主体：圆角矩形，白底 + 主题色 1px 描边
        c.create_polygon(_round_rect_points(2.0, 6.0, 16.0, 14.0, 2.0),
                         fill=p["editor"], outline=col, width=1)

    def _draw_folder_icon_open(self, c):
        """展开的文件夹：实心主题色前盖朝下摊开，后面探出一角稿纸。

        只把描边换成实心还不够「打开」—— 再加「露出的纸」这个公认符号，
        18px 里也能一眼读出「这支是摊开的」；实心前盖同时把两种状态在
        视觉重量上拉开（收起 = 轻/空心，展开 = 重/实心）。
        """
        p = self.pal
        col = p["accent"]
        # 纸先画：下半截被前盖压住，看上去是从文件夹里探出来的
        c.create_polygon(_round_rect_points(5.4, 1.0, 13.6, 8.6, 1.2),
                         fill=p["editor"], outline=col, width=1)
        # 前盖：上窄下宽的梯形（朝观众翻开的那一片）
        c.create_polygon((2.4, 7.0, 15.6, 7.0, 16.8, 14.0, 1.2, 14.0),
                         fill=col, outline=col, width=0)

    def _row_hover_bg(self, row):
        """这一行的「悬停」底色：**从该行自己的底色推**（选中行悬停不变色）。

        为什么不能统一用 pal["hover"]：
          - 文件夹行铺在 #e8f0e9 上、笔记行铺在 #f6f8f5 上，同一个悬停色在两块底
            上深浅不同；
          - 更要命的是皮肤给的 hover(#dde8df) 与 sel(#dbe8dc) 只差 5/765 ——
            「鼠标放上去」和「选中这一条」长得一模一样。
        从底推（`_hover_bg` 只压明度）之后，悬停永远只是「这块底暗一档」，
        与带主题色的选中行由构造方式分开。
        """
        base = getattr(row, "_base_bg", None) or self.pal["list"]
        if base in (self.pal["sel"], self.pal["sel2"]):
            return base
        return _hover_bg(base)

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
            # 自绘的小按钮（行首箭头）底片是**按底色现推**的：底色刚被改，
            # 不重画就还是旧底色算出来的那一层，悬停时像贴了块别色方片
            refresh = getattr(ch, "refresh_bg", None)
            if callable(refresh):
                refresh()
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
        # 左栏多选收敛成「只有它」：视图的当前项只有一个，多选是批量操作的残余，
        # 不能跟着新视图留着（下面 load_editor 顺回标签那一趟已经明确不反写这里）
        self._folder_sel = {fid}
        self._folder_anchor = fid
        self._restyle_folders()
        self.refresh_notes()
        self.load_editor()

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
                    cl.set_style(fg=(p["selfg"] if hl else p["soft"]))
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
            base = p["sel"] if active else (p["sel"] if seld else p["side"])
            row._base_bg = base
            self._row_hover(row, base, fb=p["side"])
            self._style_folder_labels(f["id"], active or seld)
            # 字重变了 → 可用宽度里的字数也变，重新按像素截断
            self._fit_folder_name(f["id"])
            # 图标随调色板重绘（换主题/切选中态时颜色同步）
            cv = self._folder_icons.get(f["id"])
            if cv is not None and cv.winfo_exists():
                self._draw_folder_icon(cv, f["id"])
            twv = self._folder_twisty.get(f["id"])
            if twv is not None and twv.winfo_exists():
                ok = bool(folder_children(f["id"], self.data["folders"]))
                # 叶子刚长出子文件夹（新建是就地插入、行没重建）：这一格此前是
                # 隐身占位、没绑过右键 —— 在这里补上，否则箭头出来了却点不动
                if ok and not getattr(twv, "_tw_bound", False):
                    twv.set_command(lambda fid=f["id"]: self._twisty_click(fid))
                    twv.bind("<Button-3>", lambda e: "break")
                    twv._tw_bound = True
                twv.apply_theme(self.pal)
                twv.set_available(ok)                  # 叶子：整块隐身、光标退回箭头
                twv.set_collapsed(bool(f.get("collapsed")))

    def _folder_click(self, e, fid):
        """左键：普通=导航并单选；Shift=从锚点区间多选；Ctrl=切换单个。"""
        shift = (e.state & 0x0001) != 0
        ctrl = (e.state & 0x0004) != 0
        # 按**看到的顺序**（含智能分类、按树抽屉展开后从上到下的顺序）取多选区间，
        # 而不是 data["folders"] 的平铺顺序 —— 那里面子文件夹未必挨着父。
        ids = list(self._folder_rows.keys())
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

        - 智能分类（全部 / 星标 / 加密）视图：只清掉文件夹多选，保留当前视图，
          点哪条笔记都不会跳转到它所属的文件夹。
        - 普通文件夹视图：同步高亮该文件夹为唯一选中（视图本来就是它，无需切换）。
        - 仅由普通点击触发；Shift/Ctrl 多选笔记时调用会破坏区间多选，切勿挂上去。
        """
        n = next((x for x in self.data["notes"] if x["id"] == nid), None)
        fid = (n.get("folder") if n else None)
        cur = self.data["current_folder"]
        if cur in SMART_FOLDERS:
            self._folder_sel = set()
            self._folder_anchor = None
        elif fid and fid not in SMART_FOLDERS:
            self._folder_sel = {fid}
            self._folder_anchor = fid
        else:
            self._folder_sel = set()
            self._folder_anchor = None
        # 必须重绘：否则文件夹行的多选高亮会残留在界面上，看起来“没生效”
        self._restyle_folders()

    def _delete_folders(self, fids):
        """批量删除选中的普通文件夹（连同它们的子文件夹与笔记）。"""
        # 智能分类是筛选视图、系统区是程序自带的 —— 都不在可删之列。多选里混进了
        # 系统那一格时直接摘掉，不能因为它整批拒绝（用户选的是别的文件夹）。
        real = [i for i in fids if i not in SMART_FOLDERS and i != SYSTEM_FOLDER]
        if not real:
            self._show_info("提示", "智能分类与系统文件夹都不可删除。")
            return
        folders = self.data["folders"]
        # 每棵树连同子孙一起删；选中互相嵌套时（比如同时勾了父和子）只算一次
        realset = set()
        for fid in real:
            realset |= set(subtree_ids(fid, folders))
        cnt = sum(1 for n in self.data["notes"] if n.get("folder") in realset)
        if cnt:
            tail = ""
            if len(realset) > len(real):
                tail = "（连同 %d 个子文件夹）" % (len(realset) - len(real))
            msg = ("选中的 %d 个文件夹%s下共有 %d 条笔记。\n"
                   "删除后这些笔记会一起删除，是否继续？" % (len(real), tail, cnt))
        else:
            msg = "确定删除选中的 %d 个文件夹？" % len(realset)
        if not self._ask_confirm("删除文件夹", msg, ok_text="删除", danger=True):
            return
        if cnt:
            dropped = {n["id"] for n in self.data["notes"] if n.get("folder") in realset}
            self.data["notes"] = [x for x in self.data["notes"] if x["id"] not in dropped]
            self._note_sel -= dropped
            if self.data.get("current_note") in dropped:
                self.data["current_note"] = None
        self.data["folders"] = [x for x in folders if x["id"] not in realset]
        self._folder_sel = set()
        self._folder_anchor = None
        if self.data["current_folder"] in realset:
            self.data["current_folder"] = "f_all"
        save_data(self.data)
        self.refresh_all()

    def _resolve_note_colors(self, notes):
        """给这次要画的整列笔记定颜色：保证相邻两条不同，返回 {笔记 id: 颜色}。

        颜色平时跟着笔记走（`ensure_note_color`），这里只处理「撞在一起」的那几条。
        落库走 `_schedule_save`（和正文同一个去抖），关窗时 `close_app` 会兜底刷库。
        """
        sv = getattr(self, "search_var", None)
        searching = bool((sv.get() if sv is not None else "").strip())
        accents, changed = resolve_adjacent_colors(notes, persist=not searching)
        if changed and not searching:
            self._schedule_save()
        return accents

    def _set_row_accent(self, nid, color):
        """就地换某一行色条的颜色（拖拽排序后不重建整列，也就不能靠重建来重画）。"""
        rec = self._rows.get(nid)
        card = rec.get("row")._card if rec else None
        if card is None or getattr(card, "_accent", None) == color:
            return
        card._accent = color
        card._draw()

    def _refresh_alarm_index(self):
        """按全库提醒重建「任务提醒列表」的正文 / 内链（纯内存，不动 updated）。

        它是视图不是内容：增删闹钟、时间翻过「已完成」、点过弹窗的「知道了」，
        都得让它跟上。重写 content 而不落库 —— 每次启动 / 导入都会重算，库里
        存的是哪一版都不影响显示；也不改 updated，否则每次重算都把它顶到
        「全部笔记」最前面。
        """
        n = next((x for x in self.data["notes"]
                  if x.get("id") == ALARM_INDEX_ID), None)
        if n is None:
            return
        content, links = alarm_index_content(self.data)
        n["content"] = content
        n["links"] = links

    def refresh_notes(self):
        p = self.pal
        self._refresh_alarm_index()   # 列表标题/预览读的是正文，先跟上最新提醒
        self._star_buttons = {}
        self._rows = {}
        cur = next((f for f in self.data["folders"] if f["id"] == self.data["current_folder"]), None)
        self.mid_head.configure(text=cur["name"] if cur else "笔记", fg=p["soft"], bg=p["list"])
        self._stop_all_marquee()     # 行即将销毁，先停掉跑马灯计时器
        for w in list(self.note_panel.inner.children.values()):
            w.destroy()
        notes = self.visible_notes()
        # 色条：同一屏里相邻两条不能同色（8 色必然有重复，重复色挨在一起就看不出是
        # 两篇了）。搜索结果是临时行序，只算这一次显示用什么颜色，不写回笔记。
        accents = self._resolve_note_colors(notes)
        self._active_title = self._active_preview = self._active_date = None
        for i, n in enumerate(notes):
            self._make_note_row(n, i, n["id"] == self.data.get("current_note"),
                                accent=accents.get(n["id"]))
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

    def _scroll_note_into_view(self, nid):
        """把笔记列表滚到刚好露出这一条（已在视口里就不动）。

        与左栏 `_scroll_to_show` 同一套「最小滚动」口味：只在超出时才挪，能少挪
        就少挪 —— 整屏跳一下比没滚到更让人找不着北。
        """
        rec = self._rows.get(nid)
        wrap = rec.get("wrap") if rec else None
        if wrap is None:
            return
        try:
            if not wrap.winfo_exists():
                return
            self.note_panel.inner.update_idletasks()
            cv = self.note_panel.canvas
            sr = (cv.cget("scrollregion") or "").split()
            total = float(sr[3]) if len(sr) == 4 else 0.0
            if total <= 0:
                return
            top = float(wrap.winfo_y())      # 相对内层 Frame，等于画布内容坐标
            h = float(wrap.winfo_height())
            view_top = cv.canvasy(0)
            view_h = cv.winfo_height()
            off = view_top
            if top < view_top:
                off = top
            elif top + h > view_top + view_h:
                off = top + h - view_h
            else:
                return
            cv.yview_moveto(min(1.0, max(0.0, off / total)))
            if getattr(self.note_panel, "sb", None) is not None:
                self.note_panel.sb.sync(*cv.yview())
        except Exception:
            pass

    def _make_note_row(self, n, index, active, before=None, accent=None):
        """创建并追加单条笔记行，返回它的外层 wrap。

        与文件夹行（_make_folder_row）同一套思路：新建笔记时只调用这个，把新行
        插到正确位置，避免整列重建——否则已存在的行被销毁再重建会闪一下。
        before 给定时插到该控件之前（用于把新笔记放到列表中间）。
        accent 是这次这一行该用的色条颜色（调用方已按行序消过相邻同色）；
        不给就退回「跟着笔记走」的那个颜色。
        """
        p = self.pal
        starred = n.get("starred", False)
        # 行 Frame 用实底色（不能用透明 ""：Windows 下透明 Frame 不会触发父级重绘，
        # 取消选中时会残留上一次的高亮像素，表现为“背景色没消失”）
        row_frame_bg = p["sel"] if active else p["list"]
        child_bg = p["sel"] if active else p["list"]
        # 圆角悬浮卡片：外层 wrap 负责左右留白与行间距，内层 row 自带圆角背景层
        wrap = tk.Frame(self.note_panel.inner, bg=p["list"])
        opts = dict(fill=tk.X, padx=CARD_PADX,
                    pady=(CARD_GAP if index else dpi.px(2), 0))
        if before is not None:
            opts["before"] = before
        wrap.pack(**opts)
        row = tk.Frame(wrap, bg=row_frame_bg)
        row.pack(fill=tk.BOTH, expand=True)
        row._card_outside = p["list"]
        # 色条：颜色跟着笔记走（见 store.ensure_note_color），这条笔记第一次被
        # 渲染时就定下来，之后切文件夹、换排序都不变；只有和相邻行撞色时，
        # 由调用方（_resolve_note_colors）临时指定一个
        if accent is None:
            accent = ensure_note_color(n, self.data["notes"])
        row._card = self._card_layer(row, row_frame_bg, p["list"], accent=accent)
        top = tk.Frame(row, bg=row_frame_bg)
        # 顶行定高：高度 = 标题字高 + 上下各一份 NOTE_TITLE_GAP（理由见那边的注释）。
        # 星标 Button 比这更高（49 > 36），多出的空白由 Tk 按父容器边界裁掉。
        t_h = self._measure_font(FONT[0], 13, "bold").metrics("linespace")
        top.configure(height=t_h + 2 * NOTE_TITLE_GAP)
        top.pack_propagate(False)
        top.pack(fill=tk.X, padx=dpi.px(14), pady=(dpi.px(2), 0))
        # 右侧（星标 + 日期）先 pack：先把宽度占死，标题再长也压不到它
        right = tk.Frame(top, bg=row_frame_bg)
        right.pack(side=tk.RIGHT)
        sb = tk.Button(right, text="★" if starred else "☆",
                       fg=(p["star"] if starred else p["soft"]),
                       bg=child_bg, bd=0, relief=tk.FLAT, cursor="hand2",
                       font=(FONT[0], 17),
                       activebackground=child_bg, activeforeground=(p["star"] if starred else p["soft"]),
                       command=lambda nid=n["id"]: self.toggle_star(nid))
        sb.pack(side=tk.RIGHT, padx=(0, dpi.px(6)))
        self._star_buttons[n["id"]] = sb
        dl = tk.Label(right, text=self._date_text(n), bg=child_bg, fg=p["soft"],
                       anchor="e", font=(FONT[0], 11))
        dl.pack(side=tk.RIGHT)
        # 标题装进「固定高度 + 不随内容扩张」的盒子：超长部分被盒子裁掉，
        # 不会把右侧的星标/日期挤走（与文件夹名同一套做法）
        tl_box = tk.Frame(top, bg=child_bg, height=t_h)
        tl_box.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, dpi.px(8)))
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
        # 摘要 11pt（与日期同档）：比标题小两号才分得出主次；12pt 时只小一号，
        # 一眼看过去标题和摘要几乎同重，整列像两行正文。
        pl = tk.Label(row, text=prev, bg=child_bg, fg=p["soft"], anchor="w",
                       font=(FONT[0], 11))
        pl.pack(fill=tk.X, padx=dpi.px(14), pady=(0, dpi.px(2)))
        if active:
            self._active_title, self._active_preview, self._active_date = tl, pl, dl
        self._rows[n["id"]] = {"row": row, "top": top, "right": right,
                                "starred": starred, "active": active,
                                "tl": tl, "pl": pl, "dl": dl, "sb": sb,
                                "wrap": wrap}
        row._base_bg = row_frame_bg
        row.bind("<Enter>", lambda e, r=row: self._row_hover(r, self._row_hover_bg(r)))
        row.bind("<Leave>", lambda e, r=row: self._row_hover(r, r._base_bg))
        # 拖拽：只在「按下」这一处记起点。motion / release **不绑在行上**，
        # 而是统一挂在顶层窗口（见 __init__ 里的 _drag_on_motion / _drag_on_release）——
        # 指针一开始移动就会跨出本行，行级绑定只能收到起点那一格。
        # 未超过阈值视为普通点击 → 选中该笔记。
        row.bind("<ButtonPress-1>", lambda e, nid=n["id"]: self._note_press(e, nid))
        row.bind("<Button-3>", lambda e, nid=n["id"]: self._popup(e, nid))
        # 子控件也要绑定按下/右键：父级不在子控件的 bindtags 中，事件不会自动冒泡
        # （背景层 Canvas 同样要绑定，它覆盖了行四周的内边距区域）
        for w in (top, right, tl_box, tl, dl, pl, row._card):
            w.bind("<ButtonPress-1>", lambda e, nid=n["id"]: self._note_press(e, nid))
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
        # 新笔记是「插进」两行之间：上下两位本来就不同色，只要新这条避开它俩就行。
        # 必须让**新来的**让路 —— 让邻居改色的话，用户会觉得自己点错了笔记。
        avoid = [notes[i].get("color") for i in (idx - 1, idx + 1)
                 if 0 <= i < len(notes)]
        avoid = [c for c in avoid if c in NOTE_COLORS]
        if n.get("color") not in NOTE_COLORS or n.get("color") in avoid:
            n["color"] = pick_note_color(self.data["notes"], n.get("folder"),
                                         avoid)
            self._schedule_save()
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
                first["wrap"].pack_configure(pady=(dpi.px(2), 0))
        self.note_panel.inner.update_idletasks()
        self.note_panel._update()
        self.note_panel.bind_wheel()

    def select_note(self, nid, sync_folder=True):
        """选中并装载一条笔记。

        sync_folder=False 是给**不是用户点笔记**的路径用的：切换文件夹时
        `select_folder` 先把 current_note 清成 None，`_load_editor_body` 发现编辑区
        空了就把标签条上那一篇顺回来 —— 那一刻并不是「用户点了笔记」，不能反过来
        把左栏高亮改到它所属的文件夹。否则刚点中的文件夹会被这一篇的文件夹顶掉，
        而两者用的是同一个选中色，看上去就是「那个文件夹始终选着」。
        """
        if self.doodle_active():
            return          # 涂鸦期间不许换笔记（蒙层 / 快照都绑在当前正文上）
        prev = self.data.get("current_note")
        if prev == nid:
            # 点中「已是当前项」的笔记：同样要取消文件夹多选（否则这里直接 return 会漏掉同步）
            self._note_sel = {nid}
            self._note_anchor = nid
            if sync_folder:
                self._sync_folder_sel_to_note(nid)
            self._restyle_notes()
            self.load_editor()
            return
        self.data["current_note"] = nid
        self._note_sel = {nid}
        self._note_anchor = nid
        # 点单条笔记：取消文件夹 Shift 多选，单独选中其对应文件夹
        if sync_folder:
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

    # ---------- 标签页 ----------
    # 正文只有一个 Text：切标签 = 存住当前这篇（走正常的去抖/落库）+ 装下一篇。
    # 因此「哪几篇开着」是一份 id 列表，不是每篇一个编辑器；撤销栈、代码块窗口、
    # 滚动位置随装载重建，换来的是不用把编辑器拆成 N 份。

    MAX_TABS = 20

    def _make_tabbar(self):
        """造一条标签条（只造不管挂）。

        启动与「设置里重新打开多标签」两处都要造，回调表必须一模一样 ——
        抄两份迟早有一份漏参数，所以只在 `_build` 那一处建。
        """
        return TabStrip(self.editor_frame, self.pal,
                        on_select=self._tab_go,
                        on_close=self._tab_close,
                        on_new=self.new_note,
                        on_menu=self._tab_menu,
                        on_reorder=self._tab_move,
                        base=self._tool_bg())

    def _multi_tab_enabled(self):
        """全局配置：是否启用多标签页（默认 True）。"""
        cfg = self.data.setdefault("settings", {})
        return cfg.get("multi_tab", True)

    def _apply_multi_tab(self, enabled):
        """把「启用多标签页」落到界面上 —— 点确定即生效，不要求重启。

        打开：现造标签条挂回工具带下面，再把上次开着的那几枚标签恢复出来；
        关闭：拆掉标签条、清空列表。正文只有一份 Text，当前这篇照旧在编辑，
        关掉标签不涉及任何内容改动，所以这里不需要存盘。
        """
        enabled = bool(enabled)
        if enabled and self.tabbar is None:
            self.tabbar = self._make_tabbar()
            # 运行中的编辑区里稿纸（以及可能的只读提示条）都已经 pack 就位，
            # 裸 pack() 会排到正文下面去 —— 必须显式插到它们前面。
            # 顺序与启动时一致：标签条 → 只读条 → 稿纸。
            before = self._ro_bar if self._ro_bar.winfo_manager() else self.text
            self.tabbar.pack(fill=tk.X, before=before)
            self._tabs_restore()
        elif not enabled and self.tabbar is not None:
            try:
                self.tabbar.destroy()
            except Exception:
                pass
            self.tabbar = None
            self._tabs = []
            self._tab_active = None

    def _tabs_cfg(self):
        """开着的标签持久化在 settings.tabs：关掉应用再回来，上次那几篇还开着。"""
        cfg = self.data.setdefault("settings", {})
        ids = cfg.get("tabs")
        if not isinstance(ids, list):
            ids = []
            cfg["tabs"] = ids
        return ids

    def _tabs_restore(self):
        if not self._multi_tab_enabled():
            # 多标签功能关闭时，保持只有当前笔记的单一标签，不渲染标签条
            self._tabs = []
            self._tab_active = None
            return
        saved = [i for i in self._tabs_cfg() if isinstance(i, str)]
        have = set(n["id"] for n in self.data["notes"])
        self._tabs = [i for i in saved if i in have][:self.MAX_TABS]
        cur = self.data.get("current_note")
        if cur and cur not in self._tabs:
            self._tabs.append(cur)
        self._tab_active = cur if cur in self._tabs else (
            self._tabs[-1] if self._tabs else None)
        self._render_tabs()

    def _render_tabs(self):
        if self.tabbar is None:
            # 多标签关着：没有标签条可画。判 tabbar 而不是判配置 —— 两者只可能
            # 由 _apply_multi_tab 同时改，这样「配置说开着、控件却不在」时也不会
            # 在 set_tabs 上抛 AttributeError。
            return
        # 按 self._tabs 重画整条；顺手剪掉已被删除的笔记（删笔记/导入换库都可能）。
        notes = dict((n["id"], n) for n in self.data["notes"])
        keep = [i for i in self._tabs if i in notes]
        if keep != self._tabs:
            self._tabs = keep
            if self._tab_active not in keep:
                self._tab_active = keep[-1] if keep else None
        items = [{"id": i, "title": self.title_of(notes[i]),
                  "locked": bool(self._is_sealed(notes[i]))} for i in self._tabs]
        self.tabbar.set_tabs(items, self._tab_active)
        cfg = self._tabs_cfg()
        cfg[:] = list(self._tabs)

    def _tab_ensure(self, nid):
        """编辑器里装的就是这篇 → 标签条上必须有它，而且是活动的那一枚。"""
        if not nid:
            self._tab_active = None
            self._render_tabs()
            return
        if nid not in self._tabs:
            if len(self._tabs) >= self.MAX_TABS:
                # 上限保护：最旧的一枚让位（渲染里同时会把已删除的笔记剪掉）
                self._tabs.pop(0)
            self._tabs.append(nid)
        self._tab_active = nid
        self._render_tabs()

    def _tab_go(self, nid):
        """点标签：切过去。列表选中与左栏高亮由 select_note 一并跟上。"""
        if not nid or nid == self._tab_active:
            return
        self.select_note(nid)
        self._scroll_note_into_view(nid)

    def _tab_drop(self, nid):
        """摘掉一枚标签，返回「接下来该激活谁」（右邻 → 左邻 → None）。"""
        if nid in self._tabs:
            i = self._tabs.index(nid)
            self._tabs.pop(i)
            if i < len(self._tabs):
                return self._tabs[i]
            if i > 0:
                return self._tabs[i - 1]
        return None

    def _tab_move(self, nid, index):
        """把某枚标签挪到第 index 位（拖拽松手时唯一的调用点）。

        `_tabs` 的顺序就是显示顺序，改完 `_render_tabs` 交下去即可：标签条发现
        「还是这几枚、只是顺序变了」会走 `_reorder` 重排（不重建控件），
        所以拖拽松手那一刻按下的标签不会被销毁。
        """
        if nid not in self._tabs:
            return
        i = self._tabs.index(nid)
        index = max(0, min(len(self._tabs) - 1, int(index)))
        if index == i:
            return
        self._tabs.pop(i)
        self._tabs.insert(index, nid)
        self._render_tabs()
        save_data(self.data)

    def _tab_close(self, nid):
        if self._tab_active == nid:
            self._tab_activate(self._tab_drop(nid))
        else:
            self._tabs = [i for i in self._tabs if i != nid]
            self._render_tabs()
            save_data(self.data)

    def _tab_close_others(self, nid):
        self._tabs = [nid]
        self._tab_active = nid
        self._render_tabs()
        if self.data.get("current_note") != nid:
            self.select_note(nid)
        save_data(self.data)

    def _tab_close_all(self):
        """关掉全部标签：编辑区清空，但不删笔记、不动任何内容。

        `_tab_activate(None)` 是唯一能把编辑区真正清空的入口（它把 current_note
        置空、重装编辑器、重画标签条）。标签列表本身必须显式落库 —— 否则下次启动
        `_tabs_restore` 会把上一条 settings.tabs 又原样开回来，看起来像没关掉。
        """
        if not self._tabs:
            return
        self._tabs = []
        self._tab_active = None
        self._tab_activate(None)
        save_data(self.data)

    def _tab_activate(self, nid, sync_folder=True):
        """切到某枚标签；None = 全关了，编辑区空着。

        sync_folder 透传给 `select_note`：由 `_load_editor_body` 自动顺位回标签时
        传 False（不是用户点的笔记，左栏高亮不该被改写）。
        """
        if self.doodle_active():
            return
        self._tab_active = nid
        if nid:
            self.select_note(nid, sync_folder=sync_folder)
            self._scroll_note_into_view(nid)
            return
        self.data["current_note"] = None
        self._note_sel = set()
        self._note_anchor = None
        self._restyle_notes()
        self.load_editor()
        self._render_tabs()

    def _tab_switch(self, delta):
        """Ctrl+Tab / Ctrl+Shift+Tab：在开着的标签里循环。"""
        if len(self._tabs) < 2:
            return
        i = self._tabs.index(self._tab_active) if self._tab_active in self._tabs else 0
        nid = self._tabs[(i + delta) % len(self._tabs)]
        self.select_note(nid)
        self._scroll_note_into_view(nid)

    def _tab_next(self):
        if not self._multi_tab_enabled():
            return
        self._tab_switch(1)

    def _tab_prev(self):
        if not self._multi_tab_enabled():
            return
        self._tab_switch(-1)

    def _tab_close_active(self):
        if not self._multi_tab_enabled():
            return
        if self._tab_active:
            self._tab_close(self._tab_active)
        elif self.current_note():
            self._tab_close(self.current_note()["id"])

    def _tab_menu(self, event, nid=None):
        """标签条右键菜单。nid=None 表示点在条底空白处 —— 没有锚定的那一枚。

        空白处只给「不针对某一枚」的动作：新建一篇、关掉全部。历史上这里右键毫无
        反应，而开着一排标签想一次收干净时，第一反应往往是去空白处右键。
        """
        if not nid:
            m = PopupMenu(self.root, self.pal)
            m.add("新建笔记", icon="pencil", command=self.new_note)
            m.add_separator()
            m.add("关闭所有标签", icon="trash", enabled=bool(self._tabs),
                  command=self._tab_close_all)
            try:
                m.popup(event.x_root, event.y_root)
            except Exception:
                pass
            return "break"
        i = self._tabs.index(nid) if nid in self._tabs else -1
        m = PopupMenu(self.root, self.pal)
        m.add("关闭标签", icon="trash", command=lambda: self._tab_close(nid))
        m.add("关闭其它标签", icon="trash", enabled=len(self._tabs) > 1,
              command=lambda: self._tab_close_others(nid))
        # 全关：只关标签不删笔记。放在「其它」下面，是因为它是三者里唯一会把
        # 编辑区清空的动作 —— 误点代价最大，排远一点。
        m.add("关闭所有标签", icon="trash", enabled=bool(self._tabs),
              command=self._tab_close_all)
        # 分区：上面是「关单枚 / 只留这一枚」，下面是「按方向成批关」。
        # 方向项没有可用目标时置灰而不是隐藏 —— 菜单长度别跟着右键位置跳来跳去。
        m.add_separator()
        m.add("关闭左侧标签", icon="trash", enabled=i > 0,
              command=lambda: self._tab_close_side(nid, "left"))
        m.add("关闭右侧标签", icon="trash", enabled=0 <= i < len(self._tabs) - 1,
              command=lambda: self._tab_close_side(nid, "right"))
        # 换位置只有「拖」这一条路（见 TabStrip 的拖拽重排）：右键菜单本身就要用鼠标
        # 右键才打得开，在这里再放一份「左移/右移一位」既不解决无鼠标场景，又让菜单
        # 多出一组。挪位置是一次性动作，拖比点两下更直接。
        try:
            m.popup(event.x_root, event.y_root)
        except Exception:
            pass

    def _tab_close_side(self, nid, side):
        """把某一枚左（右）边的标签成批关掉，nid 自己留下当锚点。

        活动标签若正好在被关的那一堆里，就落到 nid 上 —— 和 `_tab_close` 的
        「右邻 → 左邻」同一个思路：总得有一篇顶上来，否则标签还开着、编辑区却空了。
        """
        if nid not in self._tabs:
            return
        i = self._tabs.index(nid)
        if side == "left":
            drop, keep = self._tabs[:i], self._tabs[i:]
        else:
            drop, keep = self._tabs[i + 1:], self._tabs[:i + 1]
        if not drop:
            return
        self._tabs = keep
        if self._tab_active is not None and self._tab_active in drop:
            self._tab_activate(nid)      # 这一支内部会 select_note → 重装编辑器 + 重画标签
        else:
            self._render_tabs()
        save_data(self.data)

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
        self._paint_star(nid, rec, p["list"])
        self._paint_card(rec["row"], p["list"])

    def _restyle_notes(self):
        """统一刷新笔记行样式：当前(active) > 多选高亮 > 普通；不重建行、不闪烁。"""
        p = self.pal
        cur = self.data.get("current_note")
        valid = {n["id"] for n in self.data["notes"]}
        self._note_sel &= valid
        for rid, rec in self._rows.items():
            active = (rid == cur)
            seld = (rid in self._note_sel)
            rbg = p["sel"] if active else (p["sel"] if seld else p["list"])
            cbg = p["sel"] if active else (p["sel"] if seld else p["list"])
            rec["row"].configure(bg=rbg)
            rec["row"]._base_bg = rbg
            rec["top"].configure(bg=rbg)
            rec["right"].configure(bg=rbg)
            rec["tl"].set_style(bg=cbg, fg=(p["selfg"] if (active or seld) else p["text"]))
            rec["pl"].configure(bg=cbg, fg=p["soft"])
            rec["dl"].configure(bg=cbg)
            self._paint_star(rid, rec, cbg)
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
        """批量删除选中的笔记。系统笔记（内置说明 / 任务提醒列表）自动跳过。"""
        if not nids:
            return
        ids = set(nids)
        protected = {n["id"] for n in self.data["notes"]
                     if n["id"] in ids and self.is_protected_note(n)}
        if protected:
            ids -= protected
            if not ids:
                n = next((x for x in self.data["notes"] if x["id"] in protected), None)
                self._refuse_sample_delete(n)
                return
        msg = "确定删除选中的 %d 条笔记？" % len(ids)
        if protected:
            msg += "\n\n另有 %d 条系统笔记不会删除。" % len(protected)
        if not self._ask_confirm("删除", msg, ok_text="删除", danger=True):
            return
        self.data["notes"] = [x for x in self.data["notes"] if x["id"] not in ids]
        self._note_sel = set()
        self._note_anchor = None
        cur_removed = self.data.get("current_note") in ids
        if cur_removed:
            # 标签一并收掉，编辑器顺到邻居上（右邻 → 左邻）；单条删除同款行为
            self.data["current_note"] = self._tab_drop(self.data["current_note"])
        save_data(self.data)
        # 只移除被删的行 + 就地刷新计数：文件夹行与其余笔记都不重建 → 不闪烁
        self._remove_note_rows(ids)
        if cur_removed:
            self._active_title = self._active_preview = self._active_date = None
        self._refresh_folder_counts()
        self._restyle_notes()
        if cur_removed:
            self.load_editor()

    def _note_press(self, e, nid):
        # 按住 Shift / Ctrl 进行多选（批量删除），不进入拖拽逻辑
        if (e.state & 0x0001) or (e.state & 0x0004):
            self._note_select(e, nid)
            return
        # 普通按下：**立刻**取消其它选中项，只留当前这一条。
        # 放在「按下」而不是「松手」做，是因为松手事件可能丢失（拖出窗口、中途
        # 触发了别的流程），那样多选高亮就会残留；Finder / 资源管理器也是按下即收拢。
        if self._note_sel != {nid}:
            self._note_sel = {nid}
            self._note_anchor = nid
            self._restyle_notes()
        # 上一次拖拽的收尾：指针在窗口外松手时收不到 <ButtonRelease-1>，浮签会留在
        # 屏幕上 —— 下一次按下列表行就是「复位」的时机（_drag_end 可重复调用）。
        if getattr(self, "_drag_win", None) is not None:
            self._drag_end()
            self._drag = None
        # ⚠️ 这里**不 grab**：Tk 的本地 grab 在 Windows 上就是 SetCapture，而拖拽一
        # 开始就要新建那个无边框的「浮起标题」窗口 —— 新窗口抢焦点时系统会自己清掉
        # capture（WM_CAPTURECHANGED），此后指针一跨到别的行上就再也收不到
        # <B1-Motion>，表现为「浮签卡住不动、指示线不出、松手才突然生效」。
        # motion / release 统一挂在顶层窗口上（见 __init__ 与 _drag_on_motion）：
        # 事件从指针下的任意控件都会冒泡到顶层，跨行、跨栏、跨到编辑器都收得到。
        # 系统笔记（内置说明 / 任务提醒列表）不建**可拖**的态：它们钉在各自的视图里，
        # 拖出去也留不住（下次启动会被搬回来），中间那段时间就成了「数据看起来不对」。
        # ⚠️ 但这里**不能**把 `_drag` 置空就 return ——「松手时把笔记装进编辑器」是
        # `_finish_drag` 干的活，`_drag` 为空它第一行就退出了，表现就是「点说明笔记
        # 只高亮、正文不换」，用户看到的是「打不开了」。留一条 started=False 的记录
        # 正好复用那条收口逻辑（松手 → select_note），只是 motion 不许把它变成浮签。
        n = next((x for x in self.data["notes"] if x["id"] == nid), None)
        if n is not None and self.is_protected_note(n):
            self._drag = {"nid": nid, "x": e.x_root, "y": e.y_root,
                          "started": False, "sample": True}   # sample 键 = 不可拖标记
            return
        self._drag = {"nid": nid, "x": e.x_root, "y": e.y_root, "started": False}

    def _finish_drag(self, x_root, y_root, state=0):
        """拖拽收口：按落点决定「移动文件夹 / 调整顺序 / 只是选中」。

        唯一调用点是顶层那份 <ButtonRelease-1>（_drag_on_release）。
        """
        d = getattr(self, "_drag", None)
        if not d:
            return
        nid = d["nid"]
        moved = False
        if d.get("started"):
            # 松手点才是最终意图：不复用上一帧的 d["drop"] —— 指针最后一段常常
            # 没有对应的 <B1-Motion>（松手太快，或系统把中间几帧合并了）。
            tgt = self._note_drop_target(x_root, y_root, nid)
            fid = self._folder_under(x_root, y_root)
            self._drag_end()
            if fid and fid not in SMART_FOLDERS:
                self._move_note_to_folder(nid, fid)
                moved = True
            elif tgt:
                # 在列表里松手＝调整上下顺序（与「拖到文件夹」同一手势，按落点分流）
                self._apply_note_reorder(tgt)
                moved = True
        else:
            # 没到阈值＝一次普通点击：不建浮签，但也要把拖拽态收干净
            self._drag_end()
        # 落定后的选中：移动过的笔记**必须**成为唯一选中项（用户要求「移动后默认
        # 选中移动的那条」）。以前这里对 `moved` 什么都不做，于是两套高亮同时成立 —
        # 上一条仍是 `current_note`（active 色）、被拖的那条留在 `_note_sel`（seld 色），
        # 列表上就亮着两行。
        # 先收干净拖拽态：下面的选中可能切视图 / 重装编辑器，中途跑到的 Motion
        # 不该再被当成「还在拖」。
        self._drag = None
        if moved:
            self._select_after_move(nid)
        # Shift / Ctrl 多选时：保留已选集合，只把当前笔记同步为编辑器加载对象（不重建多选）
        elif state & 0x0001 or state & 0x0004:
            self.data["current_note"] = nid
            # 笔记 Shift/Ctrl 多选：只同步编辑器加载对象，保留多选集合、不动文件夹列表
            self._restyle_notes()
            self.load_editor()
        # 普通点击 / 拖拽中途松手：视为选中该笔记
        else:
            self.select_note(nid)

    def _select_after_move(self, nid):
        """拖拽落定后，把被拖的那条收为**唯一**选中项。

        两处调用来源共用它：拖进别的文件夹（`_move_note_to_folder`）与在列表里
        改顺序（`_apply_note_reorder`）。

        换文件夹后它可能已经不在当前视图里（看的就是源文件夹）—— 这时跟到它
        新的家去。否则编辑器里装着这一篇、列表里一条都不亮，比「双亮」更难解释。
        搜索态不跟：搜索是临时过滤，跳视图会让人莫名其妙，就地收拢即可。
        """
        n = next((x for x in self.data["notes"] if x["id"] == nid), None)
        if n is None:
            return
        searching = bool(getattr(self, "search_var", None)
                         and self.search_var.get().strip())
        visible = any(x["id"] == nid for x in self.visible_notes())
        if not visible and not searching and self._note_home_folder(n):
            self._popup_goto_folder(nid)   # 逐级展开 + 切视图 + 选中 + 滚到眼前 + 装编辑器
            return
        if self.data.get("current_note") == nid:
            # 拖的本来就是当前这篇：只收拢选中，不重装编辑器（会丢光标和滚动位置）
            self._note_sel = {nid}
            self._note_anchor = nid
            self._sync_folder_sel_to_note(nid)
            self._restyle_notes()
        else:
            self.select_note(nid, sync_folder=not searching)
        self._scroll_note_into_view(nid)

    # 拖拽期间的中转站。两者都挂在顶层窗口上（见 __init__），事件从指针下的任意
    # 控件冒泡上来，跨行 / 跨栏都不丢；`_drag` 为空时立即返回，平时零成本。
    def _drag_on_motion(self, e):
        d = getattr(self, "_drag", None)
        if not d:
            return
        if d.get("sample"):
            # 说明笔记只有「松手选中」这一半，永远不升级成拖拽（见 _note_press）
            return
        if not d["started"]:
            # 阈值 8px：人手点击常带几像素抖动，太小会把「点一下」误判成拖拽
            if abs(e.x_root - d["x"]) > 8 or abs(e.y_root - d["y"]) > 8:
                d["started"] = True
                self._drag_start(d["nid"])
        if d["started"]:
            self._drag_move(e.x_root, e.y_root)
            self._drag_highlight(e.x_root, e.y_root)

    def _drag_on_release(self, e):
        self._finish_drag(e.x_root, e.y_root, e.state)

    def _drag_start(self, nid):
        n = next((x for x in self.data["notes"] if x["id"] == nid), None)
        title = self.trunc(self.title_of(n) if n else "笔记", 16)
        dlg = tk.Toplevel(self.root)
        dlg.overrideredirect(True)
        dlg.configure(bg=self.pal["sel"], bd=dpi.px(1), relief=tk.RAISED)
        tk.Label(dlg, text="   " + title, bg=self.pal["sel"], fg=self.pal["text"],
                 font=(FONT[0], 12, "bold")).pack(padx=dpi.px(10), pady=dpi.px(6))
        dlg.geometry("+%d+%d" % (self._drag["x"], self._drag["y"]))
        self._drag_win = dlg

    def _drag_move(self, x, y):
        if self._drag_win:
            self._drag_win.geometry("+%d+%d" % (x + 12, y + 8))

    def _drag_highlight(self, x, y):
        d = getattr(self, "_drag", None)
        tgt = self._note_drop_target(x, y, d["nid"]) if d else None
        if tgt:
            # 落在笔记列表里 → 显示插入位置，清掉文件夹高亮（两者只能亮一个）
            if getattr(self, "_drag_target", None):
                self._folder_restore(self._drag_target)
                self._drag_target = None
            self._show_drop_line(tgt["y"])
            d["drop"] = tgt
            return
        self._hide_drop_line()
        if d:
            d["drop"] = None
        fid = self._folder_under(x, y)
        prev = getattr(self, "_drag_target", None)
        if prev == fid:
            return
        if prev and prev in self._folder_rows:
            self._folder_restore(prev)
        if fid and fid in self._folder_rows:
            row = self._folder_rows[fid]
            self._row_hover(row, self._row_hover_bg(row))
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

    # ---------- 笔记列表拖拽排序 ----------
    # 与「拖到文件夹」共用一套拖拽手势：指针停在中间列表里 = 调整上下顺序，
    # 停在左侧文件夹行上 = 移动所属文件夹。二者靠指针位置区分，不需要两个修饰键。

    @staticmethod
    def _same_note_group(a, b):
        """两条笔记是否处在同一个排序分组（置顶 / 星标都相同）。

        跨组拖拽会让「置顶」「星标」的置顶语义失效：把普通笔记拖到置顶区，
        松手后它又会被排序键弹回下面，看着像没反应。所以只在组内重排。
        """
        return (bool(a.get("pinned")) == bool(b.get("pinned")) and
                bool(a.get("starred", False)) == bool(b.get("starred", False)))

    def _note_drop_target(self, x, y, nid):
        """指针在笔记列表里时，算出「插到哪两条之间」。

        返回 None 表示不构成重排（指针不在列表里、搜索中、或位置没变化）。
        返回的 y 是插入指示线在 `note_panel.inner` 里的纵坐标。
        """
        # 搜索结果是按匹配度/更新时间排的，拖它没有持久意义，直接不接
        if hasattr(self, "search_var") and self.search_var.get().strip():
            return None
        cv = self.note_panel.canvas
        x1, y1 = cv.winfo_rootx(), cv.winfo_rooty()
        x2, y2 = x1 + cv.winfo_width(), y1 + cv.winfo_height()
        if not (x1 <= x <= x2 and y1 <= y <= y2):
            return None
        notes = self.visible_notes()
        drag = next((n for n in notes if n["id"] == nid), None)
        if drag is None:
            return None
        group = [n for n in notes if self._same_note_group(n, drag)]
        if len(group) < 2:
            return None
        pos_of = {n["id"]: i for i, n in enumerate(notes)}
        # 指针落在哪一行的上半/下半 → 列表里的原始插入位置
        yl = y - self.note_panel.inner.winfo_rooty()
        raw = len(notes)
        for i, n in enumerate(notes):
            rec = self._rows.get(n["id"])
            if not rec:
                continue
            wrap = rec["wrap"]
            if yl < wrap.winfo_y() + wrap.winfo_height() / 2.0:
                raw = i
                break
        # 把「全体里的插入位置」折算成「同分组内的插入位置」
        before = sum(1 for n in group if pos_of[n["id"]] < raw)
        old = group.index(drag)
        pos = before - 1 if old < before else before
        pos = max(0, min(len(group) - 1, pos))
        if pos == old:
            return None
        rest = group[:old] + group[old + 1:]
        rest.insert(pos, drag)
        prev = rest[pos - 1] if pos > 0 else None
        nxt = rest[pos + 1] if pos + 1 < len(rest) else None
        # 指示线画在「松手后下一条」的上沿；拖到组尾则画在组内最后一条的下沿
        if nxt is not None:
            boundary = pos_of[nxt["id"]]
        else:
            boundary = pos_of[group[-1]["id"]] + 1
        if boundary < len(notes):
            rec = self._rows.get(notes[boundary]["id"])
            line_y = rec["wrap"].winfo_y() if rec else 0
        else:
            rec = self._rows.get(notes[-1]["id"])
            line_y = (rec["wrap"].winfo_y() + rec["wrap"].winfo_height()
                      if rec else 0)
        return {"nid": nid,
                "prev": prev["id"] if prev else None,
                "next": nxt["id"] if nxt else None,
                "y": line_y}

    def _show_drop_line(self, y):
        """在行与行之间画一条 2px 强调色细线（place 在 inner 上，能盖住 packed 行）。"""
        line = getattr(self, "_drop_line", None)
        try:
            alive = line is not None and line.winfo_exists()
        except Exception:
            alive = False
        if not alive:
            line = tk.Frame(self.note_panel.inner, bg=self.pal["accent"],
                            height=dpi.px(2), bd=0)
            self._drop_line = line
        pad = CARD_PADX + dpi.px(2)
        width = max(1, self.note_panel.inner.winfo_width() - 2 * pad)
        line.configure(bg=self.pal["accent"])
        line.place(x=pad, y=max(0, int(y) - dpi.px(1)),
                   width=width, height=dpi.px(2))
        try:
            line.lift()
        except Exception:
            pass

    def _hide_drop_line(self):
        line = getattr(self, "_drop_line", None)
        self._drop_line = None
        if line is None:
            return
        try:
            line.destroy()
        except Exception:
            pass

    def _apply_note_reorder(self, tgt):
        """把拖拽结果写回 `order`，并原地重排行（不整列重建）。"""
        notes = {n["id"]: n for n in self.data["notes"]}
        drag = notes.get(tgt.get("nid"))
        prev = notes.get(tgt.get("prev"))
        nxt = notes.get(tgt.get("next"))
        if drag is None:
            return
        # 首次拖拽：先把全库现有顺序固化成数值，后面才能做「插到两位之间」
        self.ensure_note_order()
        p = self._note_order(prev) if prev else None
        q = self._note_order(nxt) if nxt else None
        if p is not None and q is not None:
            if q - p > 1e-6:
                val = (p + q) / 2.0
            else:
                # 二分到浮点精度尽头（连续往同一个缝里插很多次）→ 组内重编号
                group = [n for n in self.visible_notes()
                         if self._same_note_group(n, drag)]
                base = min(self._note_order(n) or 0.0 for n in group)
                for i, n in enumerate(group):
                    n["order"] = base + i * 1024.0
                val = drag["order"]
        elif p is not None:
            val = p + 1024.0
        elif q is not None:
            val = q - 1024.0
        else:
            val = 0.0
        drag["order"] = float(val)
        # 行序变了，「相邻」也跟着变：撞色的要重挑。行控不重建，所以只把变了的那
        # 几行色条就地重画，不整列闪一下
        accents, _ = resolve_adjacent_colors(self.visible_notes(), persist=True)
        save_data(self.data)
        self._reorder_note_rows()
        for nid, c in accents.items():
            self._set_row_accent(nid, c)
        self._scroll_note_into_view(drag["id"])

    def _reorder_note_rows(self):
        """按新的可见顺序重新 pack 已有的行（控件不销毁，不闪）。"""
        notes = self.visible_notes()
        prev = None
        for i, n in enumerate(notes):
            rec = self._rows.get(n["id"])
            if not rec:
                continue
            wrap = rec["wrap"]
            if prev is not None:
                try:
                    wrap.pack_configure(after=prev)
                except Exception:
                    pass
            try:
                wrap.pack_configure(
                    pady=(dpi.px(2) if i == 0 else CARD_GAP, 0))
            except Exception:
                pass
            prev = wrap
        self.note_panel.inner.update_idletasks()
        self.note_panel._update()


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
        # 插入指示线是 place 上来的独立 Frame，不属于任何会被销毁的分支，
        # 松手/中途取消都得自己收掉，否则那根 2px 线会一直挂在列表上。
        self._hide_drop_line()

    def _folder_under(self, x, y):
        for fid, row in self._folder_rows.items():
            # 智能分类是筛选视图、系统区只装说明笔记 —— 两者都不是笔记的落点，
            # 指针停上去不高亮、松手也不移动（拖拽分流看这里）
            if fid in SMART_FOLDERS or fid == SYSTEM_FOLDER:
                continue
            ry = row.winfo_rooty()
            rh = row.winfo_height()
            rx = row.winfo_rootx()
            rw = row.winfo_width()
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                return fid
        return None

    def load_editor(self):
        """把当前笔记装进编辑器（切换笔记 / 改字号 / 解锁都用这个方法）。

        外层这一圈只为划清一件事：**我们自己往编辑器里塞内容 ≠ 用户在改笔记**。
        正文构建的第一步是 `text.delete` 清空旧内容，Tk 会因此点亮 <<Modified>>；
        此刻 `current_note` 已经指向新笔记、只读标志却还停在上一篇的状态上，
        于是那次删除会被当成一次用户编辑，把「空编辑区」序列化写进新笔记里 ——
        列表标题当场跳成「新建笔记」（加密笔记正在等口令时最明显），
        去抖保存还有机会把这条空内容落进库。`_loading` 期间一律不认编辑。
        """
        self._loading = True
        try:
            return self._load_editor_body()
        finally:
            self._loading = False
            try:
                self.text.edit_modified(False)
                self.text.edit_reset()
            except Exception:
                pass
            # 换过正文之后，行号要整套重算（行数、折行位置都变了）；
            # 查找框若开着，关键词留着、在新这篇里接着找。
            self._gutter_schedule(0)
            self._find_on_edit()

    def _load_editor_body(self):
        self.text.config(state=tk.NORMAL)
        self._md_pending = None      # 换笔记：上一行遗留的待补样式作废
        self._hide_link_tip()        # 悬停小窗指向的是上一篇的链接，先收起
        self._mention_close()        # 候选面板同样绑着上一篇的插入点
        if getattr(self, "_md_job", None) is not None:
            # 还没执行的 markdown 转换也要取消：它带着**上一篇**的行号，
            # 留到 idle 跑就会拿旧行号去改新笔记的内容。
            try:
                self._cancel_after(self._md_job)
            except Exception:
                pass
            self._md_job = None
        # 待办勾选框是正文字符，随 text.delete 一起没了，不必单独销毁
        for blk in self._codes:
            try:
                blk.destroy()
            except Exception:
                pass
        self._codes = []
        for hr in self._hrs:
            try:
                hr.destroy()
            except Exception:
                pass
        self._hrs = []
        self.text.delete("1.0", tk.END)
        # 显式把选区标签清掉：现在「切完没有选区」是靠上面那句 delete 顺带实现的
        #（没有内容，sel 自然也就没了），但那只是副作用、不是契约。写死一次更稳：
        # 一旦哪天真按段删除、或 delete 之后再 insert 时残留了 sel，就是「整篇高亮」。
        try:
            self.text.tag_remove("sel", "1.0", "end")
        except Exception:
            pass
        # 矩形高亮同理：不清的话新笔记顶上会挂着上一篇那片蓝块，而它指向的
        # 已经不是同一批文字了（复制出来会是新笔记的内容，离谱且难查）。
        self._colsel_drop()
        n = self.current_note()
        self._refresh_alarm_index()   # 汇总表正开着时，正文也要跟上最新状态
        # 先假设「编辑器里的不是这篇笔记的明文」：解锁失败时正文区放占位文字，
        # 那条路必须先把这个标志立起来，否则占位话会被当成真正文写回数据库。
        # （serialize() 里的 _locked_view 守卫是同一件事的第二道保险。）
        self._locked_view = False
        # 切走一篇加密笔记 = 立刻重新封上（内存里不留明文），切回来时要用口令开。
        # 拿到 id 是为了把列表里那一行就地刷一遍：封上之后标题要变回「首字 + 星号」，
        # 不刷的话列表还挂着真标题，看着像「解锁状态没退干净」。
        sealed = self._seal_prev_note(keep=n["id"] if n else None)
        if sealed:
            self._refresh_note_row(sealed)
        if not n:
            self._set_readonly(False)
            self.text.config(state=tk.DISABLED)
            self._editor_note = None
            self._sync_jump_nav()
            self._refresh_footer()
            self._apply_search_highlight()      # 正文空了，高亮自然也没了
            # 标签条：先剪掉已被删掉的 id（删笔记 / 删文件夹 / 换库都可能走到这）。
            # 剪完还有标签而编辑区空着 → 顺到最近的那枚上（与关标签同款行为）；
            # 一枚不剩才摘掉活动态，编辑区空着等新建。
            self._render_tabs()
            if self._tabs and self._tab_active:
                # 顺位回标签 ≠ 用户点了笔记：左栏高亮留给调用方（切文件夹时是刚点中
                # 的那一行），这里只把编辑区填上
                self._tab_activate(self._tab_active, sync_folder=False)
            elif not self._tabs:
                self._tab_active = None
                self._render_tabs()
            return
        # 加密笔记：没解锁就弹口令框；取消则按「已加密 · 未解锁」只读呈现。
        # 切到一个不加密的目标（含「没有笔记」）就没有「当前口令」可比了：
        # 下次再开加密笔记必须重新问，哪怕口令和几个笔记之前那篇一样。
        was_locked = secret.is_blob(n.get("sec")) and not (n.get("content") or [])
        if not secret.is_blob(n.get("sec")):
            self._cur_pw = None
        unlocked = self._ensure_unlocked(n)
        if was_locked and unlocked:
            # 这次是「当场解开」的（含用会话里已记住的口令静默解开）：把列表那一行
            # 就地刷回真标题，否则它还挂着「首字 + 星号」的遮蔽形态。
            self._refresh_note_row(n["id"])
        self._editor_note = n["id"]
        # 只读态要在渲染之前定好：待办框创建时会按它决定鼠标形状
        if unlocked:
            self._set_readonly(
                self.is_protected_note(n),
                "alarm_index" if self.is_alarm_index(n) else "sample")
        else:
            self._set_readonly(True, "locked")
        if unlocked:
            self._render_content(n["content"])
        else:
            # 未解锁：正文区留一句话说明为什么是空的（不放空屏 —— 空屏看起来像
            # 「这篇本来就没内容」）。这句只是界面上的占位，serialize() 会拦住它。
            self._locked_view = True
            try:
                self.text.insert("1.0", LOCKED_BODY_TEXT, ("locked_ph",))
            except Exception:
                pass
        # 载入即把老形态的列表标记（符号后跟半角空格）换成 NBSP 形态：那两个
        # 半角空格是 Tk 的换行断点，会让长列表的第一行只剩符号。详见
        # _upgrade_list_marks —— 要赶在 edit_reset 之前跑，不进撤销栈。
        self._upgrade_list_marks()
        # 升级会**插入字符**（勾选框后面那个 NBSP）并重写方框字形，所以补标签
        # 必须在它之后再来一遍：库里存在「✅ 直接接文字」的行（间隙整段缺失），
        # 渲染时算不出间隙、补不上 todo_gap，等 _upgrade_list_marks 把 NBSP 插进去
        # 就没机会了 —— 那一格会光着，勾选后的绿底/字号都跟别的不一致。
        # 已经正确的行在这里直接 return，不会点亮 modified。
        self._sync_todo_tags()
        # 换一篇就回到开头，浮动导航同步成「一开始只有向下可用」
        self.text.yview_moveto(0.0)
        self._after_idle(self._sync_jump_nav)
        self.text.edit_reset()
        self.text.edit_modified(False)
        self.text.config(state=tk.NORMAL)
        self.text.focus_set()
        self._refresh_footer()
        # 搜索中切到另一篇：命中词要在新正文里重新标一遍（旧标签随 delete 没了）
        self._apply_search_highlight()
        # 编辑器装好了 → 标签条上必须有这一篇且为活动态。放在最后：解锁之后
        # _is_sealed 才是终态，标签上的小挂锁才不会多留一会儿。
        self._tab_ensure(n["id"])

    # 只读笔记里要禁用的工具栏项。⋯ 不在此列：它里面还有「全局设置」「折叠工具栏」
    # 这类与正文无关的入口，整颗禁掉反而连界面都调不动了。
    READONLY_LOCKED = ("style", "bold", "italic", "underline", "strike",
                       "color", "link", "bullet", "numbered", "triangle",
                       "todo", "code")

    def _set_readonly(self, on, kind=None):
        """切换只读态：提示条、光标、工具栏可用性一起切。

        只读笔记不对 Text 设 state=DISABLED —— 那样连选中复制都做不到，用户想
        摘一句说明都摘不出来。这里保持可聚焦，只在按键 / 粘贴 / 工具栏三处拦截。

        kind 区分两种只读：示例笔记（"sample"）与未解锁的加密笔记（"locked"），
        两者的提示文案和后续动作完全不同，提示条上要分开说。
        """
        on = bool(on)
        if kind is not None:
            self._ro_kind = kind
        if on == getattr(self, "_readonly", False):
            self._update_readonly_bar()     # 文案可能刚从"示例"变成"加密"
            self._sync_tool_enabled()       # 工具栏可能刚重建过，同步一次
            return
        self._readonly = on
        try:
            self.text.configure(cursor="arrow" if on else "xterm")
        except Exception:
            pass
        # 光标是 _on_todo_hover 按上一次结果做差量设置的，切态时要把账本一起清掉，
        # 否则从只读切回可编辑后指针停在原处，小手/文本光标不会重新判断。
        self._hover_cursor = None
        self._update_readonly_bar()
        self._sync_tool_enabled()

    def _update_readonly_bar(self):
        bar = getattr(self, "_ro_bar", None)
        if bar is None:
            return
        try:
            if self._readonly:
                kind = getattr(self, "_ro_kind", "sample")
                locked = (kind == "locked")
                can_rc = False
                if locked:
                    # 恢复槽在不在，决定提示条上要不要给第二条常驻入口。
                    # 正文重加密不会动槽（secret.reseal_with_key 会原样带过来），
                    # 所以这里按当前 sec 判定即可，不用缓存。
                    try:
                        n = self.current_note()
                        can_rc = bool(n and secret.has_recovery(n.get("sec")))
                    except Exception:
                        can_rc = False
                    text = ("这篇笔记已加密 · 输入口令或用恢复码查看" if can_rc
                            else "这篇笔记已加密 · 输入口令后才能查看内容")
                elif kind == "alarm_index":
                    text = "任务提醒列表 · 自动汇总，内容不可编辑（点 @笔记名 可跳回原文）"
                else:
                    text = "内置示例笔记 · 内容不可编辑（文字可以选中复制）"
                self._ro_label.configure(text=text)
                rc_label = getattr(self, "_ro_recovery", None)
                if locked:
                    self._ro_action.pack(side=tk.LEFT, padx=(dpi.px(10), 0))
                    if rc_label is not None:
                        if can_rc:
                            rc_label.pack(side=tk.LEFT, padx=(dpi.px(10), 0))
                        else:
                            rc_label.pack_forget()
                else:
                    self._ro_action.pack_forget()
                    if rc_label is not None:
                        rc_label.pack_forget()
                bar.pack(fill=tk.X, before=self.text)
            else:
                bar.pack_forget()
        except Exception:
            pass

    def _unlock_current(self):
        """点提示条上的「输入口令 →」：给当前这篇加密笔记解一次锁。"""
        nid = self.data.get("current_note")
        if not nid:
            return
        self.unlock_note(nid)

    def _recover_current(self):
        """点提示条上的「忘记口令？用恢复码 →」：直接走验码 + 重设口令。"""
        n = self.current_note()
        if not n or not secret.has_recovery(n.get("sec")):
            return
        self.recover_note_with_code(n)

    def _sync_tool_enabled(self):
        ro = getattr(self, "_readonly", False)   # 工具栏先于编辑区构建，此时标志还没建
        for iid, b in getattr(self, "_tool_btns", {}).items():
            try:
                b.set_enabled(not ro or iid not in self.READONLY_LOCKED)
            except Exception:
                pass

    def _flash_readonly_hint(self):
        """在提示条上闪一下：说明这次操作被拦了，但不弹窗打断阅读。

        只在提示条可见时才有意义——不可见说明当前笔记能编辑，那也不需要提示。
        """
        bar = getattr(self, "_ro_bar", None)
        if bar is None or not getattr(self, "_readonly", False):
            return
        try:
            if not bar.winfo_ismapped():
                return
            job = getattr(self, "_ro_flash_job", None)
            if job is not None:
                try:
                    self._cancel_after(job)
                except Exception:
                    pass
            self._ro_label.configure(fg=self._ro_accent_fg())
            self._ro_flash_job = self._after(450, self._unflash_readonly_hint)
        except Exception:
            pass

    def _unflash_readonly_hint(self):
        self._ro_flash_job = None
        try:
            self._ro_label.configure(fg=self.pal.get("soft", "#76767e"))
        except Exception:
            pass

    def _render_content(self, content):
        # 闹钟表跟着正文一起换：不在这里清空的话，切走的笔记留下的 name → 数据
        # 会继续留着，新笔记里同名图元（id 撞车）就会认错人
        self._alarms = {}
        for it in content:
            if it[0] == "t":
                self.text.insert(tk.END, it[1])
                tags = it[2] if len(it) > 2 else []
                for tg in tags:
                    self.text.tag_add(tg, "insert-1c", "insert")
            elif it[0] == "w":
                # 老数据里的待办是**嵌入窗口**项 ["w", checked, tags]。待办现在是
                # 字面字符，这里把窗口渲染成 ⬜ / ✅，并把它带的标签一起戴上 ——
                # 丢掉的话老笔记里加粗的待办重开后样式就没了。
                self._add_checkbutton(checked=bool(it[1]),
                                      tags=(it[2] if len(it) > 2 else ()))
            elif it[0] == "code":
                self._add_code_block(lang=(it[1] if len(it) > 1 else ""),
                                     code=(it[2] if len(it) > 2 else ""))
            elif it[0] == "hr":
                self._add_hr()
            elif it[0] == "alarm":
                self._alarm_add(tk.END, it[1] if len(it) > 1 and isinstance(it[1], dict) else {},
                                move=False)
        # 方框的 todo / todo_done 标签逐行归一：老库里存在「光有字符没标签」的
        # 待办（早先回车续接的产物），不补的话它们既小又点不动。
        # **必须排在下面画删除线之前**：删除线是按 todo_done 标签范围找行的，
        # 补标签晚一步，那些 ✅ 行就只剩绿框、没有划过线的正文。
        self._sync_todo_tags()
        # 已勾的待办整行套 done（删除线 + 变灰）。勾选态现在是行首那个 ✅ 字符，
        # 逐行扫一遍即可 —— 不再需要维护控件实例表。
        # 删除线从**勾选框之后**开始画：划过方框看着像把方框也划掉了。
        for a, b in zip(*[iter(self.text.tag_ranges("todo_done"))] * 2):
            ln = str(a).split(".")[0]
            self.text.tag_add("done", *self._todo_body_span(ln))
        self._normalize_quote_spans()
        # 第 1 行恒为标题（旧数据首行可能是 body/h2/quote，打开即纠正）
        self._ensure_title_line()
        # 富链接的 lk_N 标签是按字符回填的，载入后按当前配色统一刷一遍
        self._restyle_links()
        # 列表悬挂缩进：嵌入式勾选框此刻可能还没布局完，count 量不到真实宽度，
        # 所以 idle 后再补一次（同步那次负责先把续行的位置撑起来）
        self._sync_list_indent()
        self._after_idle(self._sync_list_indent)
        # 闹钟的悬浮/右键绑定 + 到点轮询（首次载入时挂上，之后靠标记防重复）
        self._alarm_bind()

    def _normalize_quote_spans(self):
        """把每段引用行的标签扩到含行尾换行符。

        渲染是按字符逐个 tag_add 的（`insert-1c` → `insert`），换行符不在标签里。
        Tk 只在 tag 覆盖到换行符时才把 background 铺满整行宽度，否则底色停在
        最后一个字 —— 多行引用就成了长短不齐的色条，看着像文本高亮而不是引用块。

        按行扫而不是按 tag 范围反推行号：tag 的结束位置常常刚好落在下一行的
        `x.0`（因为上一行的换行符被覆盖了），拿行号直接算会把下一行也圈进来。

        **空行不再保留色块**：引用文字删光即退出引用（「引用删掉后引用色不必保留」），
        所以这里逐行调 _sync_quote_line 时，空行那一支是「清掉」而不是「补满」。
        载入一篇旧笔记时，早年存下来的空引用色带会就此被摘掉，与编辑时的规则一致。

        半行也要小心：正文行被并进引用行之后，保存下来的是「前几个字有 quote」，
        重新载入时若无脑补满整行，那条被并进来的正文就白改了。逐行调用
        _sync_quote_line 正是为了「满行才铺、半行剪尾、空行清掉」。
        """
        try:
            last = int(str(self.text.index("end-1c")).split(".")[0])
        except Exception:
            return
        for ln in range(1, last + 1):
            try:
                if not self._line_has_tag("quote", str(ln)):
                    continue
                self._sync_quote_line("%d.0" % ln, "%d.end" % ln)
            except Exception:
                pass

    def _add_checkbutton(self, checked=False, index=tk.END, tags=()):
        """插入一个待办标记：字符 ⬜ / ✅ + 不换行空格。

        字符而不是自绘控件的理由见 md_conv.TODO_OFF。空格必须是 NBSP：
        Tk 会在半角空格处断行，待办文字一长就掉到第二行。

        方框和间隙**分开打标签**：方框带 todo / todo_done（字号 + 颜色 + 点击
        绑定），间隙带 todo_gap（借同一个字号、不带颜色）。合在一起的话，勾选态
        的颜色会顺着间隙一路铺到文字跟前。

        `tags` 是老数据（窗口项 ["w", checked, tags]）带过来的行内样式，
        渲染老笔记时要跟着一起戴上，否则加粗的待办重开后样式就丢了。

        **行首插入时先补两格缩进**（LI_INDENT），和项目符号 / 三角 / 序号同一条
        规矩：待办是行级标记，四种列表该在同一条竖线上。以前只有待办贴行首，
        同一份笔记里项目符号和待办差着两格。只认「列 0」—— 句中/行内进来的
        方框（老数据里的窗口项）本来就跟着前文走，别硬塞缩进。
        """
        tag = "todo_done" if checked else "todo"
        ch = TODO_ON if checked else TODO_OFF
        keep = tuple(t for t in (tags or ())
                     if t not in ("sel", "done", "todo", "todo_done", TODO_GAP_TAG))
        pos = self.text.index(index)        # 先换成具体下标：index 可能是 tk.END
        ln, col = str(pos).split(".")
        if int(col) == 0:
            # 显式给空标签集：插入点紧挨着上一行的换行符，不指定的话会继承
            # 上一行的行级标签（引用 / 标题），两格缩进就被染成引用底色。
            self.text.insert(pos, LI_INDENT, ())
            pos = "%s.%d" % (ln, len(LI_INDENT))
        self.text.insert(pos, ch, keep + (tag,))
        self.text.insert(pos + " +1c", TODO_GAP, (TODO_GAP_TAG,))
        return tag

    def _on_todo_hover(self, event):
        """悬浮待办行 → 小手；行尾之后的空白 / 普通正文 → 文本光标。

        判定用的是**字符下标**而不是行的存在：`index("@x,y")` 把指针换算成
        最近的字符，只有列号仍落在这一行文字之内才算「压着字」。待办行行尾
        那一大片空白会被 Tk 归到该行 end，那里不该变手型。

        链接的那几个字符不动：link / lk_N 自己用 tag_bind 管手型，这里改回来
        会跟它们抢（同一次移动里 Motion 先到、Enter 后到，抢完就卡住）。
        """
        if self.is_readonly():
            return                      # 只读态整体是箭头，别掺和
        try:
            idx = self.text.index("@%d,%d" % (event.x, event.y))
            ln, col = str(idx).split(".")
            col = int(col)
            names = self.text.tag_names(idx)
            if "link" in names or any(t.startswith("lk_") for t in names):
                self._hover_cursor = None
                return
            cur = "xterm"
            txt = self.text.get(ln + ".0", ln + ".end")
            if col < len(txt) and split_list_prefix(txt)[0] == "check":
                cur = "hand2"
        except Exception:
            return
        if cur != self._hover_cursor:
            self._hover_cursor = cur
            try:
                self.text.configure(cursor=cur)
            except Exception:
                pass

    # ---------- 竖向（矩形）选择 ----------
    def _alt_down(self, event=None):
        """这次鼠标事件是否「按着 Alt」（Ctrl+Alt 自然也算）。

        两级判据：先看 Tk 报的 state（那位在 Windows 和 X11 上不一样、且本机 0x8
        还是常驻位，详见 ALT_MASK 的注释），再兜一层系统的真实键状态 —— modifier
        映射随 Tk 版本 / 输入法而变，真假最终以系统为准。

        ⚠️ 也因此不能用 `<Alt-Button-1>` 这种序列绑定代替 —— Tk 生成事件时会用
        **真实键盘状态**盖掉 state 的修饰位，`event_generate` 根本造不出来，
        想验就得真按键（见 .workbuddy/shot/probe_realdrag.py）。
        """
        try:
            if (getattr(event, "state", 0) or 0) & ALT_MASK:
                return True
        except Exception:
            pass
        return alt_key_down()

    def _on_text_press(self, event=None):
        """记下「这一下是按在正文里的」，供 _on_text_drag 判定；Alt 按下则起矩形。"""
        self._text_pressed = True
        if event is not None and self._alt_down(event):
            self._colsel_begin(event)
            # 拦住 tk::TextButton1：insert mark 和 sel 都不要动 —— 矩形跟它们无关，
            # 而且保住 insert mark 意味着「矩形后面打字」不会把整块替换掉。
            return "break"
        self._colsel_drop()      # 普通点击：矩形作废（类绑定随后会清 sel）
        return None

    def _on_text_release(self, event=None):
        """松手：清掉拖拽状态，并把文本光标留在这次手势的终点。

        「Alt 点一下没拖」当成啥也没选（notepad++ 里那一下也是放个列光标、等于挪个
        位置）：留一个零行零宽的列模式在屏幕上什么也看不见，可下一次 Ctrl+V 是列粘贴、
        <Key> 走的是逐行插入 —— 用户以为自己在普通编辑，白白挨一记。真拖动过的才留着。
        """
        self._text_pressed = False
        st = self._colsel
        if st is not None:
            self._colsel_stop_scroll(st)
            # 只清拖拽态，**不清高亮**：列得留着，用户松手后要按 Ctrl+C / 直接打字。
            self._colsel = None
            if not st.get("moved"):
                # 纯 Alt 单击：起手时已经把光标挪过去了，这里只是别留下列模式
                self._colsel_drop()
            elif event is not None:
                # 拖完了：把光标留在矩形这一侧（notepad++ 同款），而不是让它停在
                # 起手前那个八竿子打不着的位置 —— 否则「我明明在中间改，退出之后
                # 一打字却写到别处去了」。
                try:
                    self.text.mark_set("insert", "@%d,%d" % (int(event.x), int(event.y)))
                except Exception:
                    pass
                # 拖动中为了「常亮」把闪烁定时器停了，松手这一刻必须显式重启：
                # 静止之后通常**不会再有**任何一次 _col_caret_show（滚动/重绘都
                # 不一定发生），等它顺手带起来就是「选中后光标一直不闪」，
                # 直到按退格触发 _col_delete → _col_paint 才重新起搏。
                self._col_caret_start_blink()
        else:
            # 不是矩形松手 → 这一下是普通点选 / 拖选 / 双击 / Shift+点击。
            # 保险起见再收一次矩形（<<Selection>> 那条通常已经收过了）。
            self._colsel_drop()
        return None

    def _on_text_drag(self, event=None):
        """<B1-Motion> 守卫：矩形跟着画；不是从正文里按下的拖拽一律拦掉。

        拦的是「在标签条 / 工具栏 / 侧栏上按下，手指顺势滑进正文」这一类 ——
        按下处不在正文，却会触发 Text 的 tk::TextSelectTo；而切笔记后拖选锚点
        停在 1.0，于是选区从文章开头算，看着就是整篇被选中，一打字还会被替换。
        返回 "break" 后类绑定不再执行，选区和锚点都不会被动到。
        """
        if self._colsel is not None and event is not None:
            self._colsel_move(event)
            return "break"          # 别让 TextSelectTo 把 sel 也刷一遍
        if not getattr(self, "_text_pressed", False):
            return "break"
        # 走到这儿说明是「在正文里按下的普通拖拽」= 用户又要做普通选择了。
        # 补这一刀是因为按下那一下可能被 tag 绑定吃掉（点在链接 / 待办勾选框上时
        # tag 绑定先于 widget 绑定，且它 return "break"），_on_text_press 没机会跑，
        # 旧矩形就会一直亮着。
        self._colsel_drop()
        return None

    def _on_text_drag_leave(self, event=None):
        """<B1-Leave> 的同款守卫（tk::TextAutoScan 会自己再刷一遍选区）。

        没在正文里按下时把 repeat 也停掉：否则哪怕 break 了这一次，已经排上队的
        50ms 回调仍会拿 Priv 坐标调一次 TextSelectTo。
        """
        if self._colsel is not None and event is not None:
            # 矩形拖出正文：改自己滚。TextAutoScan 必须停 —— 它同样会调 TextSelectTo，
            # 把 sel 按锚点刷出来，和矩形叠成两片高亮。
            self._cancel_autoscan()
            self._colsel_move(event)
            return "break"
        if not getattr(self, "_text_pressed", False):
            self._cancel_autoscan()
            return "break"
        return None

    def _colsel_begin(self, event):
        self._cancel_autoscan()
        try:
            self.text.focus_set()   # 下面 break 掉了 tk::TextButton1，焦点得自己补
        except Exception:
            pass
        # 同理，**清旧选区也是 tk::TextButton1 干的活**：break 掉它之后，上一步留在
        # 屏幕上的 sel 会原样躺着，和矩形叠成两片高亮；更糟的是那时按 Ctrl+C，
        # 按「有 sel 就复制 sel」的规则复制出来的是**之前那处**选区 —— 用户看到
        # 的就是「竖向选过之后别的选中/复制全乱了」，所以这里自己清一次。
        try:
            self.text.tag_remove("sel", "1.0", "end")
        except Exception:
            pass
        try:
            # 锚点存**索引**（点击处那个字符），不存 y：拖到边缘会自动滚动，内容
            # 一滚同一个 y 就指向别的行了，矩形起点会整体跑偏。列号也从这里取 ——
            # notepad++ 的列是从按下那一列起算的。
            i0 = self.text.index("@%d,%d" % (int(event.x), int(event.y)))
        except Exception:
            i0 = "1.0"
        # 顺便把文本光标挪到按下处：起手到拖完的整个过程里，Alt 这一路是接管的
        # （类绑定 tk::TextButton1 被 break 掉了，它本来才是挪 insert 的那个），
        # 光标不跟着走的话，屏幕上就只有一块列高亮、看不见自己在哪儿。
        # 早先这里特意不动 insert，是「矩形后打字会整块被替换」那个年代的顾虑，
        # 列模式有自己的打字路径（_col_key）之后已经不成立了。
        try:
            self.text.mark_set("insert", i0)
        except Exception:
            pass
        self._colsel = {"x0": int(event.x), "i0": i0,
                        "x": int(event.x), "y": int(event.y),
                        "job": None, "dir": 0, "moved": False}
        self._colsel_paint()
        try:
            self.text.tag_raise(COLSEL_TAG)
        except Exception:
            pass

    def _colsel_move(self, event):
        st = self._colsel
        if not st:
            return
        st["x"] = int(event.x)
        st["y"] = int(event.y)
        st["moved"] = True              # 真的拖动过：这次手势才算「选了一列」
        self._colsel_paint()
        self._colsel_scroll_watch()

    def _colsel_paint(self):
        """按锚点与当前指针算矩形 = **行区间 × 列区间**，交给 _col_paint 画。

        为什么不再按「屏幕行 × 像素」记：notepad++ 的列模式是按**字符列**的，
        复制 / 打字 / 删除都得按列对齐。早先那版高亮由像素段拼出来、复制再由
        像素段反推，中英混排时「看到的」和「拿到手的」不是同一块。现在只留下
        一份行列数据，三者都从它派生。

        两端位置用 `@x,y` 求索引（x 小于行首会 clamp 到行首、大于行尾会 clamp
        到行尾且**不会跳到下一屏幕行**，实测），再把行号/列号 interchange 成
        「起点在右下」也能用的形式。
        """
        st = self._colsel
        if not st:
            return
        t = self.text
        H = max(1, t.winfo_height())
        try:
            i0 = t.index(st["i0"])
            i_end = t.index("@%d,%d" % (int(st["x"]), max(1, min(H - 2, st["y"]))))
        except Exception:
            return
        l0, c0 = (int(x) for x in i0.split("."))
        l1 = int(i_end.split(".")[0])
        rule = st.get("rule")
        if rule is None:
            rule = st["rule"] = self._col_measure(i0)
        c1 = self._col_of_x(int(st["x"]), rule, c0)
        if l0 > l1 or (l0 == l1 and c0 > c1):      # 反向拖：整体交换
            l0, c0, l1, c1 = l1, c1, l0, c0
        self._col = {"rows": list(range(l0, l1 + 1)), "c0": c0, "c1": c1}
        self._col_paint()

    def _col_measure(self, idx):
        """量一把「像素 ↔ 字符列」的尺子：(屏幕行起点 x, 起点列, 每字符像素)。

        只量**锚点所在的屏幕行**：那一行字体固定，中英混排下用它当基准，整列
        才会像 notepad++ 那样从左对齐到右。⚠️ 别改成「落点行」—— 拖过一行短行
        时 x 会被 clamp 到行尾，列宽一下缩到 2；自动滚动时落点行一直在换，矩形
        就会跟着一抖一抖（实测段数 22 → 10）。量一次缓存起来，滚出屏幕也不影响。
        """
        t = self.text
        try:
            info = t.dlineinfo(idx)
            if not info:
                return None
            ymid = info[1] + info[3] // 2
            a = t.index("%s display linestart" % idx)
            b = t.index("@100000,%d" % ymid)
            ca, cb = int(a.split(".")[1]), int(b.split(".")[1])
            n = cb - ca
            if n <= 0:
                return None
            ba = t.bbox(a)                      # 起点 x（含悬挂缩进）
            if not ba:
                return None
            # 每字符宽度用**正文字号**量，不拿锚点那一行量：锚点常常落在标题行上，
            # 标题字大，一算下来整列就窄了一大截（实测拖动 90px 只够 5 个字）。
            # 列编辑本来就是在正文上用的，正文的尺子才是对的；bbox 只用来取起点 x
            # —— 它的 width 是「到控件右边界」，不能拿来当字宽。
            try:
                f = tkfont.Font(family=(getattr(self, "ui_font", None) or FONT[0]),
                                size=self.body_size)
                w = float(f.measure("中")) or 12.0
            except Exception:
                w = 12.0
            return (ba[0], ca, w)
        except Exception:
            return None

    def _col_of_x(self, x, rule, fallback):
        """按尺子把像素 x 换成字符列。"""
        if not rule:
            return fallback
        x0, ca, w = rule
        if w <= 0:
            return fallback
        return max(0, ca + int(round((x - x0) / w)))

    # ---------- 列模式：_col（行区间 × 列区间）派生的一切 ----------
    def _col_active(self):
        """列模式是否激活。注意「零宽列」也算：打完字之后列光标还在，
        再敲一个字仍是逐行插入 —— 这是「选中之后不会自动消失」的关键。"""
        return getattr(self, "_col", None) is not None

    def _col_paint(self):
        """按 _col 刷高亮：逐**逻辑行**取 [c0, c1)，再按屏幕行切开。

        必须按屏幕行切：开了自动换行（wrap=CHAR）后 `tag_add(a, b)` 跨了软换行
        会把中间那一整段也涂成蓝的（看着像整段被选）。软换行处「上一屏幕行的
        lineend」正好等于「下一屏幕行的 linestart」，所以沿线尾推进就能逐段切。
        关掉自动换行（wrap=NONE，默认）时每个逻辑行只有一个显示行，切出来仍是一段，
        这段逻辑照常成立。
        """
        t = self.text
        try:
            t.tag_remove(COLSEL_TAG, "1.0", "end")
        except Exception:
            return
        col = self._col
        if not col or col["c1"] <= col["c0"]:
            self._sync_col_footer()
            # 零宽列虽然不画高亮，光标**仍要画**：那正是「列光标」停的位置，
            # 打完字的那一刻它是用户判断「我在哪儿」的唯一依据。
            self._col_caret_show()
            return
        for ln in col["rows"]:
            try:
                linelen = int(t.index("%d.end" % ln).split(".")[1])
            except Exception:
                continue
            a = "%d.%d" % (ln, min(col["c0"], linelen))
            b = "%d.%d" % (ln, min(col["c1"], linelen))
            if t.compare(a, ">=", b):
                continue
            for sa, sb in self._col_split_display(a, b):
                try:
                    t.tag_add(COLSEL_TAG, sa, sb)
                except Exception:
                    pass
        self._sync_col_footer()
        self._col_caret_show()

    # ---------- 列模式：选中的每一行都有一根闪烁的光标 ----------
    # Tk 的 Text 只有一个 insert 标记、只有一根系统光标，没有多光标 API —— 想让
    # 「竖向选中的每一行」都有一根，只能自己画。几条实测决定了现在的做法：
    #   ① Canvas 覆盖层 + WS_EX_LAYERED 色键：透明是透明的，可画上去的线也跟着
    #      一起没了（Tk 的 Canvas 在色键模式下不出内容）→ 放弃。
    #   ② 每行一个 2px 小窗（Frame(text) + place + lift）→ 画得出来，位置精确。
    #      高度取 dlineinfo 的**行盒**（含 spacing1/3），不是 bbox 的字框 ——
    #      后者会让光标比整行矮 8~16px。
    #   ③ ⚠️ 别指望 WS_EX_TRANSPARENT 让鼠标穿透：它管绘制顺序、不管命中测试，
    #      点在条上正文收不到（见 _col_caret_forward），只能转发事件。
    #   ④ 闪烁「灭」相位用 place_forget 真藏，不涂底色：条有整行高，跨到
    #      spacing 区域时涂 sel 色会留一条 2px 残影。
    #   ⑤ 定时器与重绘解耦：重绘只 ensure 不重启，否则连续重绘会让 tick 永远
    #      轮不到 —— 就是「选中后不闪，按退格才闪」。
    # 全程只碰 UI 窗口，一个字符都不动，序列化/落库零风险。
    def _col_caret_origin(self):
        """place 坐标系相对 bbox 坐标系的偏移（实测一次并缓存）。

        `place` 放进正文时原点是**内容区**（在 padx/pady 之内），而 `bbox()` 返回的
        是相对**部件左上角**（含 padding）—— 实测差 (15, 11)。这个数随 borderwidth
        和 padding 变，硬编码迟早在某次改皮肤时错位，所以放一个 1px 探针窗口实测。
        """
        off = getattr(self, "_col_caret_off", None)
        if off is not None:
            return off
        off = (0, 0)
        # ⚠️ 下面那句 update_idletasks 会派发 <Configure>，而 <Configure> 上挂着
        # _col_caret_relayout → 它又调 _col_caret_show → 回到这里：不拦就是无限
        # 递归（实测直接把界面卡死）。所以计算期间把重绘那条路封掉。
        self._col_caret_busy = True
        try:
            probe = tk.Frame(self.text, width=1, height=1,
                             bd=0, highlightthickness=0)
            probe.place(x=0, y=0)
            self.text.update_idletasks()
            off = (probe.winfo_rootx() - self.text.winfo_rootx(),
                   probe.winfo_rooty() - self.text.winfo_rooty())
            probe.destroy()
        except Exception:
            pass
        finally:
            self._col_caret_busy = False
        self._col_caret_off = off
        return off

    def _col_caret_bar(self):
        """取一根光标条（池化复用：滚动和闪烁每半拍都要，别反复创建销毁窗口）。"""
        pool = getattr(self, "_col_caret_pool", None)
        if pool is None:
            pool = self._col_caret_pool = []
        if pool:
            return pool.pop()
        # 新建就带亮色：万一某条路径漏了 _col_caret_paint，也不会留一条
        # SystemButtonFace 灰线（浅色稿纸上等于没画）。池子复用的条由 paint 统一刷。
        bar = tk.Frame(self.text, width=COL_CARET_W, bd=0, highlightthickness=0,
                       bg=self.pal.get("accent", "#333333"))
        try:
            bar.configure(cursor=self.text.cget("cursor"))
        except Exception:
            pass
        # 鼠标事件一律转交正文（见 _col_caret_forward 的说明）
        for _seq in ("<Button-1>", "<ButtonRelease-1>", "<B1-Motion>",
                     "<Double-Button-1>", "<Button-3>", "<Motion>",
                     "<MouseWheel>"):
            bar.bind(_seq, lambda e, s=_seq: self._col_caret_forward(s, e), add="+")
        return bar

    def _col_caret_forward(self, seq, event):
        """把落在光标条上的鼠标事件原样交给正文。

        条是压在正文上的**真窗口**，天生会吃掉落在它那 2px 上的鼠标 —— 点上去正文
        没反应、光标不动。

        ⚠️ 别指望 `WS_EX_TRANSPARENT`：Tk 的 Frame 默认就带着这个位（实测 exstyle
        = 0x24，0x20 早就在），可 `WindowFromPoint` 依然命中条、点击依然到不了正文
        —— 那个位管的是**绘制顺序**，不是命中测试。子类化 WndProc 回 HTTRANSPARENT
        倒是能行，但那要劫持 Tk 自己的窗口过程，太危险。老老实实转发最稳：
        坐标 = 条在正文里的位置（`_caret_xy`，与 bbox 同一坐标系）+ 事件在条内的偏移。

        转发走 event_generate，而它的 state 会被**真实键盘状态**覆盖 —— 正好是想要
        的：Alt 按着点条上，正文那边照样开始新的一列。
        """
        xy = getattr(event.widget, "_caret_xy", None)
        if not xy:
            return None
        try:
            kw = {"x": int(xy[0] + int(event.x)), "y": int(xy[1] + int(event.y))}
            if seq == "<MouseWheel>":
                kw["delta"] = int(getattr(event, "delta", 0) or 0)
            self.text.event_generate(seq, **kw)
        except Exception:
            pass
        return "break"

    def _col_caret_char_w(self):
        """一个空格的像素宽 —— 用来给虚拟列（行不够长）估算位置。

        列模式下补位补的是空格（见 _col_insert），所以这里也按空格量，才和
        「补完之后那一列停在哪」对得上。量一次缓存。
        """
        w = getattr(self, "_col_caret_cw", None)
        if w:
            return w
        w = 8.0
        try:
            f = tkfont.Font(self.text, self.text.cget("font"))
            w = float(f.measure(" ")) or 8.0
        except Exception:
            pass
        self._col_caret_cw = w
        return w

    def _col_caret_show(self):
        """按 _col 在每一行画一根光标条。

        位置：列号 c1（矩形右边界，也就是「选中的每一行后面」）。打字之后列变零宽，
        c0 == c1，落点自然跟到插入内容后面。行不够长时按字宽外推，和 _col_insert
        补空格后的实际位置一致。
        """
        col = self._col
        t = self.text
        # 防重入：_col_caret_hide_native 那句 configure(insertwidth=0) 也会派发
        # <Configure>，而 <Configure> 上挂着重绘 —— 不挡就会在这里绕一圈。
        if getattr(self, "_col_caret_in_show", False):
            return
        self._col_caret_in_show = True
        try:
            self._col_caret_show_inner(col, t)
        finally:
            self._col_caret_in_show = False

    def _col_caret_show_inner(self, col, t):
        self._col_caret_recycle()
        if not col or not col.get("rows"):
            return
        dx, dy = self._col_caret_origin()
        c = col["c1"]
        cw = self._col_caret_char_w()
        try:
            H = t.winfo_height()
        except Exception:
            H = 0
        used = []
        for ln in col["rows"]:
            try:
                linelen = int(t.index("%d.end" % ln).split(".")[1])
            except Exception:
                continue
            idx = "%d.%d" % (ln, c) if c <= linelen else "%d.end" % ln
            try:
                bb = t.bbox(idx)
                # ⚠️ 高度必须取 dlineinfo 的「行盒」，不能用 bbox 的 height：
                # bbox 只量字框（正文 20 / H1 27），spacing1/3 的段前段后距不算
                # 在内，按字框画出来的光标会比整行矮 8~16px，像一根没长齐的短线。
                # dlineinfo 的 y 是行盒顶（含 spacing1），与 bbox 同一坐标系。
                di = t.dlineinfo(idx)
            except Exception:
                bb, di = None, None
            if not bb:
                continue                    # 滚出视口：不画
            x = bb[0] + (c - linelen) * cw if c > linelen else bb[0]
            y, h = (di[1], di[3]) if di else (bb[1], bb[3])
            if y + h <= 0 or y >= H:
                continue
            bar = self._col_caret_bar()
            # 摆放参数交给 paint 统一执行：闪烁「灭」相位要 place_forget 真藏起来
            #（涂成高亮底色会在新增的 spacing 区域留下一条 2px 残影）。
            bar._caret_geom = (int(x) - dx, y - dy, COL_CARET_W, h)
            # 转发鼠标事件时要用：条在正文里的位置（bbox 坐标系，见 _col_caret_forward）
            bar._caret_xy = (int(x), y)
            used.append(bar)
        self._col_caret_used = used
        if used:
            self._col_caret_hide_native()
            if getattr(self, "_colsel", None) is not None:
                # 拖动中：相位锁在「亮」，常亮不闪（和 VS Code 拖动时一样）。
                # ⚠️ **不要在这里 cancel 定时器**：鼠标在窗口外松开时
                # <ButtonRelease-1> 可能收不到，停掉的定时器就再没人重启了。
                # 让它一直跑，由 _col_caret_tick 见到 _colsel 只保持亮不翻转。
                self._col_caret_on = True
            # ⚠️ 静止重绘（滚动 / Configure / 换肤 / 打字）**绝不重启定时器**，
            # 也绝不把相位掰回亮：旧实现每次 show 都 blink(on=True) 取消并
            # 重排 600ms，只要有一条路径以短间隔反复重绘，tick 就永远轮不到
            # —— 光标条一直常亮，正是用户报的「选中后不闪，按退格才闪」
            #（退格走 _col_delete → _col_paint，恰好把那条链断开了）。
            # 这里只按当前相位上色 + 补一条定时器，呼吸不被打断。
            self._col_caret_paint()
            self._col_caret_ensure()
        else:
            # 选中行全滚出视口：条收起来了，相位归亮、系统光标还回来；
            # 定时器仍留着，滚回来时立刻接上呼吸。
            self._col_caret_on = True
            self._col_caret_restore_insert()
            self._col_caret_ensure()

    def _col_caret_paint(self):
        """按当前相位显示 / 隐藏所有条（闪烁每半拍一次，要便宜）。

        ⚠️「灭」用 place_forget **真藏起来**，不是涂成高亮底色：光标条现在有整行
        高，跨到了 spacing1/3 区域，那里没有 COLSEL_TAG 的底色，涂 `pal["sel"]`
        会留一条 2px 的浅色残影 —— 看着就是「光标没闪，只是变了下颜色」。
        系统光标灭的那半拍也是不画，行为一致。
        """
        on = bool(getattr(self, "_col_caret_on", True))
        bg = self.pal.get("accent", "#333333")
        # place / place_forget 理论上不派发 Text 的 <Configure>；万一派发了，
        # busy 位会让 _col_caret_relayout 直接回去，不会绕回 show 无限递归。
        self._col_caret_busy = True
        try:
            for bar in getattr(self, "_col_caret_used", None) or ():
                g = getattr(bar, "_caret_geom", None)
                if not g:
                    continue
                try:
                    if on:
                        if str(bar.cget("bg")) != str(bg):
                            bar.configure(bg=bg)
                        bar.place(x=g[0], y=g[1], width=g[2], height=g[3])
                        tk.Misc.lift(bar)
                    else:
                        bar.place_forget()
                except Exception:
                    pass
        finally:
            self._col_caret_busy = False

    def _col_caret_cancel(self):
        """停掉闪烁定时器（拖动中常亮 / 手势重启 / 退出列模式）。"""
        job = getattr(self, "_col_caret_job", None)
        self._col_caret_job = None
        if job is not None:
            try:
                # 走统一出口：_after 把 id 记在 _after_jobs 里，直接 after_cancel
                # 会让那个集合每半拍攒一个死 id（列模式开一晚上就是上万个）。
                self._cancel_after(job)
            except Exception:
                pass

    def _col_caret_ensure(self):
        """保证列模式开着时始终有一条定时器；已经在跑就原样留着。

        节奏照抄系统光标：亮 insertontime、灭 insertofftime，这样它和别处的
        光标是同一个呼吸频率。所有重绘路径都走这里，**绝不 cancel 再排** ——
        理由见 _col_caret_show_inner。
        """
        if getattr(self, "_col_caret_job", None) is not None:
            return
        if not self._col_active():
            return
        try:
            ms = int(self.text.cget("insertontime" if getattr(
                self, "_col_caret_on", True) else "insertofftime"))
        except Exception:
            ms = 300
        self._col_caret_job = self._after(max(50, ms), self._col_caret_tick)

    def _col_caret_start_blink(self):
        """拖完松手：从「亮」相位重新起一个完整周期（见 _on_text_release）。

        拖动中定时器是停着的，而松手之后往往**没有**任何一次 _col_caret_show
        —— 不能指望「下一次重绘顺手把闪烁带起来」，必须在这里显式重启。
        """
        self._col_caret_cancel()
        self._col_caret_on = True
        self._col_caret_paint()
        self._col_caret_ensure()

    def _col_caret_tick(self):
        self._col_caret_job = None
        if not self._col_active():
            return
        if getattr(self, "_colsel", None) is not None:
            # 拖动中：保持亮、不翻转。定时器照跑，松手（或手势异常中断后
            # _colsel 被清掉）不需要任何额外事件就能自动接上闪烁。
            self._col_caret_on = True
        else:
            self._col_caret_on = not self._col_caret_on
        self._col_caret_paint()
        self._col_caret_ensure()

    def _col_caret_recycle(self):
        """把上一批条收进池子（不销毁：滚动和闪烁每半拍都走一遍创建销毁太吵）。"""
        used = getattr(self, "_col_caret_used", None) or []
        pool = getattr(self, "_col_caret_pool", None)
        if pool is None:
            pool = self._col_caret_pool = []
        for bar in used:
            try:
                bar.place_forget()
            except Exception:
                pass
            pool.append(bar)
        self._col_caret_used = []

    def _col_caret_hide_native(self):
        """列模式期间藏掉 Tk 自带那根光标。

        它只画在 rows[-1]（insert mark 就一个），留着就是「最后一行两根、别的行
        一根」。藏了之后每一行都由我们自己画，粗细和相位才完全一致。
        ⚠️ 退出时必须还回去（_col_caret_restore_insert）—— 忘了就是「光标没了」。
        """
        if getattr(self, "_col_native_iw", None) is None:
            try:
                self._col_native_iw = self.text.cget("insertwidth")
            except Exception:
                self._col_native_iw = 2
        # ⚠️ 必须「真的不一样才改」：Tk 的 configure 哪怕设成同一个值也会派发
        # <Configure>，而 <Configure> 上挂着重绘 → 重绘又回到这里 → 界面直接卡死
        #（实测一次 show 就要 250ms 以上、update 再也退不出来）。
        try:
            if int(self.text.cget("insertwidth")) != 0:
                self.text.configure(insertwidth=0)
        except Exception:
            pass

    def _col_caret_restore_insert(self):
        iw = getattr(self, "_col_native_iw", None)
        if iw is None:
            return
        try:
            if int(self.text.cget("insertwidth")) != int(iw):
                self.text.configure(insertwidth=iw)
        except Exception:
            pass
        self._col_native_iw = None

    def _col_caret_hide(self):
        """退出列模式：收起所有条、停掉定时器、把系统光标还回来。"""
        self._col_caret_cancel()
        self._col_caret_on = True       # 下次进入列模式从「亮」相位起步
        self._col_caret_recycle()
        self._col_caret_restore_insert()

    def _col_caret_relayout(self, event=None):
        """窗口尺寸变了 → 软换行重排，光标条的 x/y 全变，必须重算。"""
        if getattr(self, "_col_caret_in_show", False) or \
                getattr(self, "_col_caret_busy", False):
            return None             # 摆放途中 / 校准途中：别再进来一次
        if not self._col_active():
            return None
        self._col_caret_show()
        return None

    def _col_caret_invalidate(self):
        """换皮肤 / 改正文字号之后调用：字宽和 place 原点都得重新量。

        这两个数是缓着的（每次摆放都实测太吵），而它们恰好都跟字体和 padding 挂钩，
        皮肤一换就可能变。清掉缓存、列模式还开着就立刻重摆。
        """
        self._col_caret_cw = None
        self._col_caret_off = None
        if self._col_active():
            self._col_caret_show()

    def _sync_col_footer(self):
        """列模式期间让底部状态条**常驻**并显示「列模式」（见 _refresh_footer）。

        矩形拖到一半时每次 motion 都会走到这里，所以只在没浮出来的时候 show 一次，
        之后只刷文字 —— _show_footer 里的 place/lift 没必要每帧做。
        """
        if self._col_active():
            if getattr(self, "_footer_visible", False):
                self._refresh_footer()
            else:
                self._show_footer()

    def _col_split_display(self, a, b):
        """把同一逻辑行上的索引区间按**屏幕行**切成若干段。

        屏幕行尾用 `@超大x, ymid` 夹出来（x 大于行尾会 clamp 到行尾、且不会跳到
        下一个屏幕行，实测）。⚠️ 别用 `display lineend`：本机 Tk 8.6.8 上它返回的
        是**逻辑行**末尾（整行），切出来只有一段，长段落会被整块涂蓝。
        """
        t = self.text
        segs = []
        try:
            cur = t.index("%s display linestart" % a)
        except Exception:
            cur = a
        for _ in range(600):            # 兜底上限，一屏通常几十行
            if t.compare(cur, ">=", b):
                break
            info = t.dlineinfo(cur)
            if not info:
                break
            try:
                end = t.index("@100000,%d" % (info[1] + info[3] // 2))
            except Exception:
                break
            sa = cur if t.compare(cur, ">", a) else a
            sb = end if t.compare(end, "<", b) else b
            if t.compare(sa, "<", sb):
                segs.append((sa, sb))
            # 用 `+1 display line` 前进（软换行处上一屏行尾 == 下一屏行首，拿
            # end 当下一轮起点会原地打转 —— 这里踩过一次）。
            try:
                nxt = t.index("%s +1 display line" % cur)
            except Exception:
                break
            if nxt == cur:
                break
            cur = nxt
        return segs

    def _col_lines(self):
        """列覆盖的每一行在当前文档里的文本（复制 / 剪切的内容）。

        短行只到自己行尾，行尾之外**不补空格**（notepad++ 复制列块同款）。
        """
        col = self._col
        if not col:
            return []
        t = self.text
        out = []
        for ln in col["rows"]:
            try:
                linelen = int(t.index("%d.end" % ln).split(".")[1])
            except Exception:
                continue
            a = min(col["c0"], linelen)
            b = min(col["c1"], linelen)
            out.append(t.get("%d.%d" % (ln, a), "%d.%d" % (ln, b)))
        return out

    def _col_edit_rows(self):
        """列编辑真正下手的行：嵌了代码块 / 分割线的行必须跳过。

        那一行上那一格是 `window_create` 出来的真窗口，删掉那个字符就把整块
        代码删了 —— 列编辑不该有这种杀伤力。
        """
        col = self._col
        if not col:
            return []
        t = self.text
        bad = set()
        try:
            for w in (t.window_names() or ()):
                bad.add(int(t.index(w).split(".")[0]))
        except Exception:
            pass
        return [ln for ln in col["rows"] if ln not in bad]

    def _col_sync_insert(self):
        """把文本光标挪到列光标所在的那一格（最后一行 · 列光标的列）。

        用户判断「我在哪儿」全靠那根竖线：列编辑一共就走过 rows[-1] 那几行，
        可如果 insert 一直赖在起手前的老位置，等退出列模式再打字，字就落到文章
        别处去了 —— 现场在中间，输出在开头，怎么看都像失控。
        """
        col = self._col
        if not col or not col["rows"]:
            return
        ln = col["rows"][-1]
        try:
            linelen = int(self.text.index("%d.end" % ln).split(".")[1])
            self.text.mark_set("insert", "%d.%d" % (ln, min(col["c0"], linelen)))
        except Exception:
            pass

    def _col_insert(self, text):
        """列插入：覆盖的每一行同一列插 text，原来那一列先被替换掉。

        notepad++ 的列编辑三件事：① 从最后一行往前改（行号才不会漂）；
        ② 行不够长就先补空格到那一列（虚拟列）；③ 插完列光标停在插入内容之后，
        **仍在列模式** —— 所以连着敲字是「每行都跟着长」，而不是只在光标处插。
        """
        col = self._col
        if not col:
            return False
        if self._refuse_readonly():
            self._colsel_drop()
            return False
        t = self.text
        rows, c0, c1 = self._col_edit_rows(), col["c0"], col["c1"]
        if not rows:
            return False
        t.edit_separator()
        try:
            for ln in sorted(rows, reverse=True):
                eol = "%d.end" % ln
                linelen = int(t.index(eol).split(".")[1])
                a = min(c0, linelen)
                b = min(c1, linelen) if c1 > c0 else a
                if a < c0:                      # 虚拟列：补空格到 c0 再插
                    t.insert(eol, " " * (c0 - a))
                    a = c0
                if b > a:
                    t.delete("%d.%d" % (ln, a), "%d.%d" % (ln, b))
                t.insert("%d.%d" % (ln, a), text)
        finally:
            t.edit_separator()
        col["c0"] = col["c1"] = c0 + len(text)   # 零宽列：光标停在插入内容之后
        self._col_sync_insert()
        self._col_paint()
        self.mark_modified()
        return True

    def _col_delete(self, forward):
        """列删除：删掉覆盖的那一列；零宽列时按 Backspace / Delete 各删一格。"""
        col = self._col
        if not col:
            return False
        if self._refuse_readonly():
            self._colsel_drop()
            return False
        t = self.text
        rows, c0, c1 = self._col_edit_rows(), col["c0"], col["c1"]
        if not rows:
            return False
        t.edit_separator()
        try:
            for ln in sorted(rows, reverse=True):
                linelen = int(t.index("%d.end" % ln).split(".")[1])
                if c1 > c0:
                    a, b = min(c0, linelen), min(c1, linelen)
                elif forward:
                    a, b = min(c0, linelen), min(c0 + 1, linelen)
                else:
                    a, b = max(0, min(c0, linelen) - 1), min(c0, linelen)
                if b > a:
                    t.delete("%d.%d" % (ln, a), "%d.%d" % (ln, b))
        finally:
            t.edit_separator()
        col["c0"] = col["c1"] = (c0 if c1 > c0 or forward
                                 else max(0, c0 - 1))
        self._col_sync_insert()
        self._col_paint()
        self.mark_modified()
        return True

    def _col_paste(self, event=None):
        """<<Paste>>：列模式下按行粘（剪贴板第 N 行 → 列覆盖的第 N 行）。"""
        if not self._col_active():
            return None
        if self._refuse_readonly():
            self._colsel_drop()
            return "break"
        try:
            raw = self.root.clipboard_get()
        except Exception:
            raw = ""
        if not raw:
            return "break"
        lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if len(lines) > 1 and lines[-1] == "":     # 复制整块时末尾常带一个空行
            lines.pop()
        if len(lines) == 1:
            self._col_insert(lines[0])
            return "break"
        self._col_insert_lines(lines)
        return "break"

    def _col_insert_lines(self, lines):
        """列粘贴的多行版：第 i 行插 lines[i]，行数不够就复用最后一行。

        配对按**行号升序**（lines[0] 给最上面那行），下手按降序 —— 从后往前改，
        行号才不会因为前面的行变长而漂。
        """
        col = self._col
        t = self.text
        rows = self._col_edit_rows()
        if not rows or not col:
            return
        c0, c1 = col["c0"], col["c1"]
        width = c1 - c0 if c1 > c0 else 0
        pairs = [(ln, lines[i] if i < len(lines) else lines[-1])
                 for i, ln in enumerate(sorted(rows))]
        t.edit_separator()
        try:
            for ln, txt in reversed(pairs):
                eol = "%d.end" % ln
                linelen = int(t.index(eol).split(".")[1])
                a = min(c0, linelen)
                b = min(c1, linelen) if width else a
                if a < c0:
                    t.insert(eol, " " * (c0 - a))
                    a = c0
                if b > a:
                    t.delete("%d.%d" % (ln, a), "%d.%d" % (ln, b))
                t.insert("%d.%d" % (ln, a), txt)
        finally:
            t.edit_separator()
        col["c0"] = col["c1"] = c0 + len(lines[0])
        self._col_sync_insert()
        self._col_paint()
        self.mark_modified()

    def _col_cut(self, event=None):
        """<<Cut>>：列模式下 = 复制那一列 + 删掉那一列。"""
        if not self._col_active():
            return None
        if self._refuse_readonly():
            self._colsel_drop()
            return "break"
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append("\n".join(self._col_lines()))
            self.root.update_idletasks()
        except Exception:
            pass
        self._col_delete(True)
        return "break"

    def _col_key(self, event=None):
        """列模式下的键盘：可打印字符 → 列插入，Backspace/Delete → 列删除，
        其余（导航键 / 功能键 / 输入法）→ 退出列模式，动作照常。

        ⚠️ 通用 <Key> 收不到「有更具体绑定」的那些键 —— <space>、<Return>、
        方向键、<Escape> 都各自另挂了一条，见 _setup_editor_bindings 里的说明。
        """
        if not self._col_active():
            return None
        st = getattr(event, "state", 0) or 0
        # ⚠️ 这里的 0x8 是**血的教训**，别再加回去：本机 Tk 报的 state 里 0x8 是
        # 常驻位（不按任何键也是 0x8），写成 `st & 0x8` 判 Alt 的结果就是**每一次
        # 按键都被当成组合键放行** —— 列模式形同虚设，Backspace 只删掉光标前那一
        # 个字符、打字只在最后一行追加（用户报的正是这个）。Ctrl 用 0x4（各平台
        # 一致），Alt 统一走 ALT_MASK（平台分支，见文件头注释）。
        if st & CTRL_MASK or st & ALT_MASK:
            return None                 # Ctrl / Alt 组合键：原样放行（Ctrl+C 等）
        ks = getattr(event, "keysym", "") or ""
        ch = getattr(event, "char", "") or ""
        if ks in ("BackSpace", "Delete"):
            self._col_delete(ks == "Delete")
            return "break"
        if len(ch) == 1 and (ch >= " " or ch == "\t"):
            self._col_insert(ch)
            return "break"
        # 输入法（IME）的键：中文 / 日文输入法开着时，字母键被 IME 吃掉，Tk 报上来的
        # keysym 是 "??"、char 为空 —— 那串字符不从 char 走，而是等 IME 提交时直接
        # 进 Tcl 的 tk::TextInsert（见 _install_col_insert_hook / _col_tcl_insert）。
        # 这里**绝不能**退出列模式：一退，提交上来的整串就成了普通插入、落在 insert
        # 那一格 —— 用户报的「竖向选了，打字却只在最后一行追加」就是这么来的。
        # 放给 IME，等它提交时由钩子改成逐行插入。
        if ks in ("", "??", "Multi_key", "ISO_Next_Group", "ISO_Prev_Group"):
            return None
        # 功能键 / 其余杂键：退出列模式，按键本身的动作照常。
        self._colsel_drop()
        return None

    def _install_col_insert_hook(self):
        """接管 Tcl 层的 `tk::TextInsert` —— 中文输入法提交走的正是一条路。

        `bind Text <Key>` 的内容是 `tk::TextInsert %W %A`：不管字符是从按键来的、
        还是 IME 提交上来的，插入文本最终都进这个 proc。列模式如果只在 Python 侧
        守着 `<Key>` 绑定，中文输入（keysym "??"、char 空）会整串绕过列编辑，直接
        落到 insert 那一格 —— 就是「竖向选了，打字却只在最后一行追加」。
        所以在这一层包一道：列模式且是正文 → 改成逐行插入，其余原样放行。

        只 rename 一次（拿 `::tk::TextInsert__sj_orig` 存在与否判断），免得重建
        界面 / 重开标签时把钩子套成两层。
        """
        try:
            self.root.tk.createcommand("_sj_col_insert", self._col_tcl_insert)
        except Exception:
            return
        try:
            self.root.tk.eval(
                "if {[llength [info commands ::tk::TextInsert]]"
                " && ![llength [info commands ::tk::TextInsert__sj_orig]]} {\n"
                "    rename ::tk::TextInsert ::tk::TextInsert__sj_orig\n"
                "    proc ::tk::TextInsert {w s} {\n"
                "        if {[_sj_col_insert $w $s]} { return }\n"
                "        ::tk::TextInsert__sj_orig $w $s\n"
                "    }\n"
                "}")
        except Exception:
            pass

    def _col_tcl_insert(self, w, s):
        """`tk::TextInsert` 的钩子：列模式 + 正文 → 逐行插入；其余一律放行。

        返回 1 = 已处理，Tcl 那边就别再调原 proc 了。⚠️ 这是从 Tcl 里调进来的，
        一抛异常整个应用的输入就废了（所有 Text 都插不进字），所以整段包死，
        出任何差错都退回「放行」。
        """
        try:
            if not s or not self._col_active():
                return 0
            if str(w) != str(self.text):
                return 0            # 代码块里嵌的 Text：不归正文的列模式管
            if self._refuse_readonly():
                self._colsel_drop()
                return 0
            return 1 if self._col_insert(s) else 0
        except Exception:
            return 0

    def _col_space(self, event=None):
        """<space>：列模式下是「每行插一个空格」（<space> 被 markdown 转换和
        @ 候选占着，通用 <Key> 根本收不到，只能单独挂一条）。"""
        if not self._col_active():
            return None
        self._col_insert(" ")
        return "break"

    def _col_drop_pass(self, event=None):
        """导航键 / 回车：退出列模式，按键本身的动作照常（notepad++ 同款）。"""
        if self._col_active():
            self._colsel_drop()
        return None

    def _colsel_scroll_watch(self):
        """指针跑出正文上下边界就开滚（滚不滚得动由 _colsel_tick 判断）。"""
        st = self._colsel
        if not st:
            return
        H = self.text.winfo_height()
        d = -1 if st["y"] < 0 else (1 if st["y"] > H else 0)
        st["dir"] = d
        if d == 0:
            self._colsel_stop_scroll(st)
        elif st["job"] is None:
            st["job"] = self.text.after(COLSEL_SCROLL_MS, self._colsel_tick)

    def _colsel_tick(self):
        st = self._colsel
        if not st:
            return
        st["job"] = None
        d = st["dir"]
        if not d:
            return
        before = self.text.yview()
        self.text.yview_scroll(d * COLSEL_SCROLL_STEP, "units")
        if before == self.text.yview():
            return                  # 到顶/到底了，别再空转
        # 内容滚了，同一内容位置换了 y：把指针 y 顶到新边缘，矩形才会继续长
        st["y"] = 0 if d < 0 else max(0, self.text.winfo_height() - 1)
        self._colsel_paint()
        st["job"] = self.text.after(COLSEL_SCROLL_MS, self._colsel_tick)

    def _colsel_stop_scroll(self, st):
        job = st.get("job")
        if job is not None:
            try:
                self.text.after_cancel(job)
            except Exception:
                pass
            st["job"] = None

    def _on_escape(self, event=None):
        """Esc 退出列模式；没在列模式就放行（候选面板 / 别的 Esc 绑定照常）。

        必须单独绑 <Escape>：同一个 bindtag 上只要有一条更具体的按键绑定匹配上，
        Tk 就不再走通用的 <Key> 了 —— 上面 `@ 候选` 挂的那条 <Escape> 就是这个
        更具体的绑定，于是之前 Esc 从来都到不了 _colsel_drop。
        """
        if not self._col_active():
            return None
        # 拖拽途中按 Esc：连这次手势一起作废 —— 否则矩形没了、手指还在动，
        # 一个普通选区又从按下点长出来，看着像「按 Esc 反而选上了」。
        self._text_pressed = False
        self._cancel_autoscan()
        self._colsel_drop()
        return "break"              # 这一次 Esc 只用来清矩形，别再触发别的动作

    def _on_sel_change(self, event=None):
        """普通选区一出现，列模式立刻退出。

        两者在语义上互斥：复制只能有一个来源。之前允许并存，而 <<Copy>> 里矩形
        优先级最高，于是「先矩形、再正常选一段、Ctrl+C」复制出来的是那块旧矩形
        —— 用户看到的就是「竖向选过之后，别的选中功能都不对了」。
        """
        if not self._col_active():
            return None
        try:
            if not self.text.tag_ranges("sel"):
                return None
        except Exception:
            return None
        self._colsel_drop()
        return None

    def _colsel_drop(self, event=None):
        """退出列模式（单击 / Esc / 导航键 / 新选区 / 全选 / 切笔记）。

        ⚠️ `_col` 一旦存在就要无条件清：**零宽列**（打完字的列光标）屏幕上没有高亮，
        只按 tag_ranges 判断就会把它漏在这儿，于是列模式悄悄还开着，下一串字
        全被逐行插进去 —— 那正是「说不清什么时候又在列编辑」的源头。

        反过来，普通拖选现在能正常出 sel 了，`<<Selection>>` / `<B1-Motion>` 每一次
        motion 都会把人叫到这里。没得可清时必须早点回去：下面这几步头一件是
        `tag_remove(COLSEL_TAG, "1.0","end")` —— 全文档扫一遍，再刷底部状态条、
        排一个 220ms 定时器，每帧做一遍既费 CPU 又会让状态条抽动。
        """
        st = self._colsel
        if self._col is None and st is None:
            # 没列可清就早点回去，但**残留的光标条必须收掉**：条是 place 出来的
            # 真窗口，没人管它就会一直挂在正文上闪。
            if not getattr(self, "_col_caret_used", None):
                try:
                    if not self.text.tag_ranges(COLSEL_TAG):
                        return None
                except Exception:
                    return None
        self._col = None
        self._col_caret_hide()
        if st is not None:
            self._colsel_stop_scroll(st)
            self._colsel = None
        try:
            self.text.tag_remove(COLSEL_TAG, "1.0", "end")
            # 列模式的常驻提示条到此为止：交回普通状态，让它照旧自动收起
            self._refresh_footer()
            self._schedule_footer_hide()
        except Exception:
            pass
        return None

    def _on_copy(self, event=None):
        """<<Copy>>：列模式下按行拼，否则交回 Tk 默认。

        Tk 的 tk_textCopy 只取 `sel.first`→`sel.last` 这一段**连续**文本：实测挂
        `1.3-1.7 / 2.3-2.5 / 3.3-3.7` 三段 sel，复制出来是 " abcdefg\\n第二行 短\\n
        第三行 一二三" —— 矩形外的字全带上了。所以列模式不走 sel，复制自己算。
        """
        if not self._col_active():
            return None
        # 兜底：只要当前存在普通选区，就以普通选区为准 —— 用户按 Ctrl+C 时眼睛看的
        # 是屏幕上最后选中的那块，列是更早的操作。_on_sel_change 本该已经清掉了，
        # 这里再判一次是因为「sel 被代码直接打上、<<Selection>> 还没派发」的窗口
        # 虽小却真实存在（比如查找命中高亮）。
        try:
            has_sel = bool(self.text.tag_ranges("sel"))
        except Exception:
            has_sel = False
        if has_sel:
            self._colsel_drop()
            return None
        parts = self._col_lines()
        if not parts:
            return None
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append("\n".join(parts))
            self.root.update_idletasks()
        except Exception:
            return None
        return "break"

    def _cancel_autoscan(self):
        """停掉 tk::TextAutoScan 排队里的那串 50ms 回调（只 break 拦不住已排队的）。"""
        try:
            self.text.tk.call("tk::CancelRepeat")
        except Exception:
            pass

    def _on_todo_click(self, event):
        """点勾选框切换：换字符、换样式标签，整行 done（删除线 + 变灰）跟着走。"""
        # tag 绑定**先于** widget 绑定执行（实测），所以 Alt+拖动从勾选框上起手时
        # 这一条会抢在 _on_text_press 前面跑 —— 不先让路，一下就把待办切了。
        if self._alt_down(event):
            return None
        if self._refuse_readonly():
            return "break"
        try:
            idx = self.text.index("@%d,%d" % (event.x, event.y))
        except Exception:
            return "break"
        self.toggle_todo_at(idx)
        return "break"

    def _on_paste_fix_todo(self, event=None):
        """粘贴后把这一段里的方框标签补齐（复制过来的待办勾不了，就是缺这一步）。

        Tk 的 <<Copy>> 只带走字符，剪贴板里没有标签 —— 粘进来的 `⬜ / ✅` 是**裸
        字符**：比旁边的方框小一号、颜色也淡，而且点击切换的 tag_bind 挂在 todo
        标签上，点它完全没反应。

        实例绑定先于类绑定（真正执行插入的那条）跑，此刻文字还没进来，所以只能先
        记下行号，等这一轮事件处理完（after_idle）再扫；多扫一行是照顾「以换行
        结尾的粘贴」—— 末尾那行空行也是这次粘出来的。
        """
        try:
            start = int(str(self.text.index("insert")).split(".")[0])
        except Exception:
            return None

        def fix():
            try:
                end = int(str(self.text.index("insert")).split(".")[0])
                last = int(str(self.text.index("end-1c")).split(".")[0])
            except Exception:
                return
            self._sync_todo_lines(start, min(last, end + 1))

        self._after_idle(fix)
        return None

    def _on_click_untagged_todo(self, event=None):
        """兜底：点到「有方框字符、却没有 todo 标签」的地方，就地补上并切换。

        tag_bind 挂在标签上，没标签就点不动。粘贴已经由 `_on_paste_fix_todo` 补过，
        但撤销、早期数据、别处拷来的文本都可能留下裸方框 —— 逐个入口去堵总会漏，
        这里按**字符**兜住：点到的不是方框、或者标签齐全（那属于 tag_bind 的活）
        就立刻放行。

        实例绑定先于类绑定，而 tag_bind 是在类绑定里跑的，两者不会同时触发。
        """
        if event is None or self._alt_down(event):
            return None              # Alt 是矩形选择（列模式）的起手，别抢
        try:
            idx = self.text.index("@%d,%d" % (event.x, event.y))
            tags = self.text.tag_names(idx)
        except Exception:
            return None
        if "todo" in tags or "todo_done" in tags:
            return None              # 正常路径：交给 tag_bind
        try:
            if self.text.get(idx, idx + " +1c") not in TODO_MARKS:
                return None
        except Exception:
            return None
        if self._refuse_readonly():
            return "break"
        self.toggle_todo_at(idx)     # 切换时会把 todo / todo_done 一起补齐
        return "break"

    def _add_hr(self, index=tk.END):
        """在正文里放一条分割线（结构项 ["hr"] 的渲染出口）。"""
        hr = HRule(self.text, self)
        self._hrs.append(hr)
        self.text.window_create(index, window=hr, padx=0, pady=0)
        hr.set_width(self._code_width())
        return hr

    def _todo_body_span(self, ln):
        """待办行里**正文**那一段的起止（跳过方框和它后面的间隙）。

        删除线 / 变灰只该落在文字上：划过方框会让人以为整个框被划掉了。
        """
        try:
            txt = self.text.get(ln + ".0", ln + ".end")
        except Exception:
            txt = ""
        kind, prefix = split_list_prefix(txt)
        start = len(prefix) if (kind == "check" and prefix) else 0
        return ("%s.%d" % (ln, start), ln + ".end")

    def _tag_todo_prefix(self, ln):
        """给待办行行首的方框补上 todo / todo_done，并把它后面的间隙归到 todo_gap。

        为什么非要有这一步：`todo` / `todo_done` 不只是字号和颜色，**点击切换的
        tag_bind 也挂在它们身上**。凡是绕过 _add_checkbutton 进到正文里的方框 ——
        回车续接出来的新行、用户手敲或从别处粘过来的框、早先版本存下来的无标签
        待办 —— 都会渲染成「小一号的灰框」，而且怎么点都不打钩。
        这里按行首字符统一判定，几种来源一次收掉；标签与字符谁说了算，字符说了算
        （未勾配 todo、已勾配 todo_done，写错了也顺手纠正）。

        **方框与间隙分开**：底色只该落在方框那一个字符上（见 _add_checkbutton）。
        老数据里间隙是跟着方框一起打 todo 的，所以这里要把它摘下来换成 todo_gap，
        否则勾选后的绿底会一直铺到文字跟前。

        已经正确时直接返回：Tk 的 tag_add 也会点亮 modified 标记，只读笔记里那就是
        一次白白的「按原文回滚」，编辑时也会多跑一轮。
        """
        try:
            txt = self.text.get(ln + ".0", ln + ".end")
        except Exception:
            return
        kind, prefix = split_list_prefix(txt)
        if kind != "check" or not prefix:
            return
        off = len(txt) - len(txt.lstrip("\u3000 "))     # 跳过行首缩进，对准方框
        if off >= len(txt):
            return
        # 旧字形先归一再判勾选态：载入时 _sync_todo_tags 跑在 _upgrade_list_marks
        # **之前**，此刻正文里还可能是旧字形（☑ / ☐ / ▢），直接和 TODO_ON 比会把已勾的行当成
        # 未勾，补上 todo 标签（方框变灰、还点不出正确状态）。
        cur = TODO_LEGACY.get(txt[off], txt[off])
        want = "todo_done" if cur == TODO_ON else "todo"
        other = "todo" if want == "todo_done" else "todo_done"
        try:
            self._ensure_todo_tag(want, "%s.%d" % (ln, off),
                                  "%s.%d" % (ln, off + 1), other)
            gap_s, gap_e = "%s.%d" % (ln, off + 1), "%s.%d" % (ln, len(prefix))
            if self.text.compare(gap_s, "<", gap_e):
                # 间隙只认 todo_gap 一个标签：方框那边的 todo / todo_done 一律摘掉，
                # 否则勾选后这一段会被染成绿色，图标看着像条尾巴。`done` 也摘：
                # 老数据把删除线画到了间隙上（空白上划线看不见，但存回去就成了脏标签，
                # 选中该行时会露出一截多余的删除线）。
                self._ensure_todo_tag(TODO_GAP_TAG, gap_s, gap_e,
                                      "todo", "todo_done", "done")
        except Exception:
            pass

    def _ensure_todo_tag(self, tag, start, end, *drop):
        """让 [start, end) 恰好带着 tag、不带 drop 里的标签；已经如此就什么都不做。"""
        if self.text.compare(start, ">=", end):
            return
        names = self.text.tag_names(start)
        if tag in names and not set(drop) & set(names) \
                and self._tag_covers_line(tag, start, end):
            return
        for d in drop:
            self.text.tag_remove(d, start, end)
        self.text.tag_add(tag, start, end)

    def _sync_todo_tags(self):
        """全文扫一遍，把每行行首方框的标签补齐（载入笔记时调用）。

        必要性：早先版本回车续接出来的待办是**裸字符**（没有任何标签），并且
        已经随笔记存进了库里 —— 那些方框打开后既小又暗、点不动。载入时补一遍，
        老笔记开箱即恢复可点，用户重新编辑一次就写回库里成为正常形态。
        """
        try:
            last = int(str(self.text.index("end-1c")).split(".")[0])
        except Exception:
            return
        self._sync_todo_lines(1, last)

    def _sync_todo_lines(self, first, last):
        """给 first..last 行（含）的行首方框补标签；越界交给 _tag_todo_prefix 自己忽略。"""
        for ln in range(max(1, int(first)), int(last) + 1):
            self._tag_todo_prefix(str(ln))

    def _todo_gap_end(self, ln, txt, col):
        """方框（第 col 列）后面那段间隙的结束列：紧跟的一串空白都算，可能为空。"""
        i = col + 1
        while i < len(txt) and txt[i] in " \u3000\u00a0\t":
            i += 1
        return i

    def toggle_todo_at(self, idx):
        """切换 idx 处（或者它所在行行首）的勾选框（字符版）。

        先认**点到的那一个**方框：老笔记里存在「方框不在行首」的待办（早年工具栏
        按钮把框插在光标处），只认行首的话点它一声不响。点到的不是方框才退回
        行首那个 —— 方框只有十几像素宽，点在后面的文字上也想切这一条。
        """
        try:
            ln = str(self.text.index(idx)).split(".")[0]
            txt = self.text.get(ln + ".0", ln + ".end")
        except Exception:
            return
        start = len(txt) - len(txt.lstrip("\u3000 "))
        try:
            col = int(str(self.text.index(idx)).split(".")[1])
        except Exception:
            col = start
        if not (0 <= col < len(txt) and txt[col] in TODO_MARKS):
            col = start
        if txt[col:col + 1] not in TODO_MARKS:
            return
        ls, le = "%s.%d" % (ln, col), "%s.%d" % (ln, col + 1)
        # 旧字形要先归一再判状态：直接和 TODO_OFF 比的话，一个还没被
        # _upgrade_list_marks 收拾的 ☐ 会被当成「已勾」，点一下反而把它写成 ⬜。
        on = TODO_LEGACY.get(txt[col], txt[col]) == TODO_OFF
        self.text.edit_separator()
        tags = [t for t in self.text.tag_names(ls) if t not in ("todo", "todo_done", "sel")]
        self.text.delete(ls, le)
        self.text.insert(ls, TODO_ON if on else TODO_OFF, tuple(tags + [
            "todo_done" if on else "todo"]))
        # 间隙只认 todo_gap：方框的状态标签不能留在这一段上（会把间隙染成绿色）
        gap_e = "%s.%d" % (ln, self._todo_gap_end(ln, txt, col))
        self._ensure_todo_tag(TODO_GAP_TAG, ls + " +1c", gap_e,
                              "todo", "todo_done", "done")
        if on:
            # 删除线从方框之后画起：行首的框划到行尾（跳过框后的间隙），
            # 句中的框只划它后面那截 —— 划前面已经写完的文字说不通。
            if col == start:
                self.text.tag_add("done", *self._todo_body_span(ln))
            else:
                self.text.tag_add("done", "%s.%d" % (ln, col + 1), ln + ".end")
        else:
            self.text.tag_remove("done", ln + ".0", ln + ".end")
        self.text.edit_separator()
        self.mark_modified()

    def _pick_icon_font(self, cache_key, candidates):
        """从 candidates 里取系统里第一款装了的字体族，缓存起来。

        为什么勾选框不跟正文族走（见 TODO_ICON_FONTS）：⬜ / ✅ 是**图标**，各字体
        画出来的体量能差一倍（同一个方框字符各族的墨迹能差一半高度），而且 U+2705
        在 Symbol 里是「带斜线的勾」不是「绿底白勾」。跟着正文族走的话，用户把
        字体从雅黑切到宋体，勾选框会当场变形。
        只探测一次并缓存：这个函数在 _config_tags 里调用，拖字号滑杆时会连跑。
        """
        fam = getattr(self, cache_key, None)
        if fam:
            return fam
        fam = getattr(self, "ui_font", None) or FONT[0]
        try:
            avail = set(tkfont.families())        # 注意：这里是**大小写敏感**的原始名
            for c in candidates:
                if c in avail:
                    fam = c
                    break
        except Exception:
            pass
        setattr(self, cache_key, fam)
        return fam

    def todo_icon_font(self):
        """待办图标（⬜ / ✅）的字体族。

        **两个状态必须共用这一个返回值**：同族同字号，advance 才会完全相等，
        打钩时后面的正文才不左右跳。
        """
        return self._pick_icon_font("_todo_icon_family", TODO_ICON_FONTS)

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
            # 浮动导航（JUMP_W）停在滚动条左侧：右占位 = 滚动条 + 间隙 + 胶囊。
            # 用常量算，别读 winfo_reqwidth —— place 是显式给宽，reqwidth 取的是
            # 主题默认值，两者在 166% 下能差 2~3px。
            right = max(right, VSCROLL_W + JUMP_GAP + JUMP_W + 6)
        except Exception:
            pass
        return max(120, w - TEXT_PADX - right - 2)

    def _sync_code_width(self, fit=True):
        """窗口缩放后让每个代码块重新对齐编辑区宽度（并按新宽度重算高度）。

        分割线也要跟着走：它的画布宽度是显式给的，不重设就会停在旧宽度上，
        窗口一拉宽，线就短了一截。
        """
        if not self._codes and not self._hrs:
            return
        w = self._code_width()
        for b in list(self._codes):
            try:
                if b.winfo_exists():
                    b.set_width(w, fit=fit)
            except Exception:
                pass
        for h in list(self._hrs):
            try:
                if h.winfo_exists():
                    h.set_width(w)
            except Exception:
                pass

    def _mk_code_ui(self, parent):
        """工具栏的「代码块」按钮：花括号图标 + 下拉箭头，点开选语言后插入代码块。

        图标走**位图**（`icon/px/<size>/code.png`，运行时按皮肤染色），矢量画法
        只作兜底。原因：Tk 8.6 的 canvas 线条没有抗锯齿，16px 的花括号整枚只有
        2 个灰阶，腰部和斜线全是台阶 —— 用户报的「不够圆滑」就是这个。位图由
        `target/make_brace_icon.py` 按 4 倍超采样画好再缩档，边缘是渐变的灰。

        更早的两版：文字「{}」（字形重心偏低 2.5px，整枚图标沉到按钮下缘）、
        纯矢量花括号（位置对齐了，但仍是硬边）。
        """
        b = RoundButton(parent, self.pal, "", command=self._pick_code_lang,
                        icon="code", icon_img="code.png", tail="chevron",
                        padx=dpi.px(8), pady=dpi.px(4), base=self._tool_bg())
        b.pack(side=tk.LEFT, padx=dpi.px(2), pady=dpi.px(3))
        b.bind_events()
        self.fmt_buttons.append(b)
        self._add_tip(b, "代码块（选择语言）")
        self._code_btn = b
        return b

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
        # 行内代码挂在代码块按钮上：两者都是「代码」，共用一个入口省工具栏的位置
        m.add("行内代码  Ctrl+E", icon="icode", enabled=not self.is_readonly(),
              command=self.toggle_icode)
        m.add_separator()
        for label, key in CODE_LANGS:
            m.add(label, command=lambda k=key: self.insert_code(k))
        m.popup(x, y)

    def _dlg_frame_pad(self, win):
        """弹窗的系统标题栏 / 边框尺寸 (单侧边框宽, 顶部标题栏高)，像素。

        `winfo_reqwidth/reqheight` 只算**客户区**，而 `wm geometry` 的 x/y 指的是
        **窗口矩形**（含标题栏）的左上角 —— 夹取位置时必须按整窗算，否则窗口会从
        屏幕上沿 / 下沿漏出去一条。量法：`winfo_rootx/y` 是客户区左上角，
        `winfo_x/y` 是窗口矩形左上角，差就是装饰。

        窗口还没映射（withdraw 中）时两个数都是默认值、差为 0，退回按实测值估：
        Win10/11 在 100% 缩放下标题栏 31px、左右边框各 8px，跟着 `dpi.px` 一起放大。
        """
        try:
            dx = int(win.winfo_rootx()) - int(win.winfo_x())
            dy = int(win.winfo_rooty()) - int(win.winfo_y())
        except Exception:
            dx = dy = 0
        if dx <= 0:
            dx = dpi.px(8)
        if dy <= 0:
            dy = dpi.px(31)
        return dx, dy

    def _center_dialog(self, win, min_w=360, fit_body=None, center_y=None):
        """对话框居中于主窗口，并保证最小宽度。

        `center_y=True`：整窗（含系统标题栏与下边框）**垂直居中**于主窗口客户区，
        给顶栏那三张信息型弹窗（全局设置 / 数据管理 / 关于）用 —— 它们是一套，
        高度各不相同，1/3 摆法会让窗口明显偏上、彼此切换时上下边缘还在跳。
        默认 False 仍是老规则「纵向 1/3 处」，小型输入对话框放那儿更顺手。
        `None` 表示沿用这张窗上次的选择，内容变高后重复居中（见 `_relayout_dialog`）
        不会悄悄跳回 1/3。

        Windows 的标题栏文字会按窗口宽度裁剪：simpledialog 只按 prompt 计算宽度，
        窗口太窄时「新建文件夹」会被裁成只剩一个「新」字，所以这里自己定宽。

        可重复调用：内容高度变化后（如设置里预览文字换行变高）再调一次，
        窗口会跟着伸缩并保持居中——否则固定高度的窗口会把底部按钮挤出画面。
        先放开 minsize 下限，否则上一次的大尺寸会把窗口卡住、缩不回来。

        `fit_body(avail_h)`：内容区的最终高度由调用方定（可选）。它拿到的是这块屏上
        **客户区最多能有多高**，据此把内容收进可滚区、按需压扁，返回后这里按新高度
        重摆一次。**每次都调**，不只装不下时才调 —— 可滚容器的画布有个默认高度
        （Tk Canvas 默认 265px），不喂给它的话，装得下的内容也会缩在一个小窗口里滚。
        不给这个回调就是老行为：内容多高窗口多高，装不下就溢出屏幕。
        """
        try:
            if center_y is None:
                center_y = bool(getattr(win, "_dlg_center_y", False))
            win._dlg_center_y = bool(center_y)
            # 首次居中时窗口多半还没映射：geometry 请求和 WM 的 map 存在竞态，
            # Windows 偶尔会无视定位请求、把窗口摆在默认位置（0,0 附近）。
            # 先收起来再摆位、最后再放出来，WM 显示时拿到的就是最终位置。
            mapped = win.winfo_ismapped()
            if not mapped:
                win.withdraw()
            win.update_idletasks()
            win.minsize(1, 1)
            ww = max(win.winfo_reqwidth(), min_w)
            wh = win.winfo_reqheight()
            rx = self.root.winfo_rootx() or 0
            ry = self.root.winfo_rooty() or 0
            rw = self.root.winfo_width() or 900
            rh = self.root.winfo_height() or 620
            # 夹到**主窗口所在那块屏**的工作区：多屏时 rx 可能是负数（副屏在主屏
            # 左侧），而 Tk 的 winfo_screenheight 只报主屏 —— 老写法 max(..., 0)
            # 会把副屏上弹出的对话框硬拽到主屏左上角。
            area = work_area_at(rx + rw // 2, ry + rh // 2, fallback=win)
            # 标题栏不属于客户区，但确实占屏幕：按整窗夹，见 _dlg_frame_pad
            dx, dy = self._dlg_frame_pad(win)
            pad = dpi.px(6)
            _x, _y, avail_h = fit_dialog(area, 0, 0, ww, wh, dx, dy, pad)
            if fit_body is not None:
                # 让调用方按这块屏的可用高度定内容高度：装得下就等高于内容
                # （滚动条自动隐藏），装不下（副屏 738 高 vs 设置窗 735 + 31 标题栏）
                # 就收进可滚区，底部的按钮行始终留在屏幕里。
                fit_body(avail_h)
                win.update_idletasks()
                ww = max(win.winfo_reqwidth(), min_w)
                wh = win.winfo_reqheight()
            # 横向居中于主窗口。纵向两档：小型输入窗仍按 1/3 处摆；信息型三张窗
            # 按整窗（含标题栏 / 下边框）精确垂直居中 —— 中心对齐后，三张窗来回
            # 切换只有上下边缘对称伸缩，不再整块往上偏。
            if center_y:
                y_want = ry + dialog_center_y(rh, wh, dy, dx)
            else:
                y_want = ry + (rh - wh) // 3
            x, y, _avail = fit_dialog(
                area, rx + (rw - ww) // 2, y_want,
                ww, wh, dx, dy, pad)
            win.geometry("%dx%d+%d+%d" % (ww, wh, x, y))
            win.minsize(ww, wh)
            if mapped:
                # 已显示的（重复居中）：逼 idle 任务立刻落实新几何，不留半截在旧位
                win.update_idletasks()
            else:
                win.deiconify()
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
        btns.pack(fill=tk.X, padx=dpi.px(16), pady=(dpi.px(12), dpi.px(14)))
        if cancel_cmd is not None:
            tk.Button(btns, text="取消", width=8, bg=p["editor"], fg=p["soft"], bd=dpi.px(1),
                      relief=tk.FLAT, font=(FONT[0], 11), cursor="hand2",
                      activebackground=p["hover"], activeforeground=p["selfg"],
                      command=cancel_cmd).pack(side=tk.RIGHT, padx=(dpi.px(8), 0))
        # 确定：默认强调色实底（危险操作改红色），悬停/按下向深色递进（不闪系统灰）
        ok_bg = MENU_DANGER if danger else p["accent"]
        ok_fg = on_accent(ok_bg)     # 金色 accent 上白字会糊，按对比度取白 / 墨
        ok_active = (_mix_hex(ok_bg, "#000000", 0.12) if danger
                     else _mix_hex(p["accent"], p["selfg"], BTN_PRESS_MIX))
        tk.Button(btns, text=ok_text, width=8, bg=ok_bg, fg=ok_fg, bd=0,
                  relief=tk.FLAT, font=(FONT[0], 11, "bold"), cursor="hand2",
                  activebackground=ok_active, activeforeground=on_accent(ok_active),
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
                     fill=tk.X, padx=dpi.px(16), pady=(dpi.px(16), dpi.px(6)))

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
        body.pack(fill=tk.X, padx=dpi.px(16), pady=(dpi.px(16), dpi.px(6)))
        color = MENU_DANGER if kind == "error" else p["accent"]
        ic = tk.Canvas(body, width=dpi.px(20), height=dpi.px(20), bd=0,
                       highlightthickness=0, bg=p["top"])
        ic.pack(side=tk.LEFT, anchor="n", padx=(0, dpi.px(10)), pady=(dpi.px(1), 0))
        # 画布已经是 dpi.px(20) 的物理尺寸，圆和字都要按同一比例落位
        ic.create_oval(dpi.px(1), dpi.px(1), dpi.px(19), dpi.px(19),
                       fill=color, outline="")
        ic.create_text(dpi.px(10), dpi.px(10),
                       text=("!" if kind == "error" else "i"),
                       fill=on_accent(color), font=(FONT[0], 11, "bold"))
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
                 font=(FONT[0], 12)).pack(fill=tk.X, padx=dpi.px(16), pady=(dpi.px(16), dpi.px(6)))
        var = tk.StringVar(value=initial)
        e = tk.Entry(win, textvariable=var, font=(FONT[0], 12), bg=p["editor"],
                     fg=p["text"], insertbackground=p["accent"], relief=tk.FLAT,
                     highlightthickness=dpi.px(1), highlightbackground=p["line"],
                     highlightcolor=p["accent"], width=30)
        e.pack(fill=tk.X, padx=dpi.px(16), ipady=dpi.px(5))

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

    def _ask_link(self, text="", url="", title="插入超链接", removable=False):
        """超链接编辑框：显示文字 + 链接地址。返回 {"text","url"}，取消返回 None。

        removable=True 时多一个「取消链接」按钮（能走到这儿的多半是在改已有的链接），
        它返回 {"text": 原文, "url": ""}。
        """
        p = self.pal
        win = self._dlg_body(title)
        res = {"v": None}
        tv, uv = tk.StringVar(value=text), tk.StringVar(value=url)

        def field(label, var, focus=False):
            tk.Label(win, text=label, bg=p["top"], fg=p["text"], anchor="w",
                     font=(FONT[0], 11)).pack(fill=tk.X, padx=dpi.px(16), pady=(dpi.px(10), dpi.px(2)))
            e = tk.Entry(win, textvariable=var, font=(FONT[0], 12), bg=p["editor"],
                         fg=p["text"], insertbackground=p["accent"], relief=tk.FLAT,
                         highlightthickness=dpi.px(1), highlightbackground=p["line"],
                         highlightcolor=p["accent"], width=34)
            e.pack(fill=tk.X, padx=dpi.px(16), ipady=dpi.px(5))
            if focus:
                e.focus_set()
            return e

        field("显示文字（正文里看到的那几个字）", tv)
        field("链接地址（http:// 可以省略）", uv, focus=True)
        hint = tk.Label(win, text="", bg=p["top"], fg=MENU_DANGER, anchor="w",
                        font=(FONT[0], 10))
        hint.pack(fill=tk.X, padx=dpi.px(16), pady=(dpi.px(4), 0))

        def ok(_=None):
            u = uv.get().strip()
            if not u:
                hint.configure(text="链接地址不能为空。")
                return
            res["v"] = {"text": tv.get().strip(), "url": u}
            win.destroy()

        def remove(_=None):
            res["v"] = {"text": tv.get().strip(), "url": ""}
            win.destroy()

        def cancel(_=None):
            res["v"] = None
            win.destroy()

        btns = self._dlg_buttons(win, ok, cancel)
        if removable:
            rm = tk.Button(btns, text="取消链接", width=9, bg=p["editor"], fg=MENU_DANGER,
                           bd=dpi.px(1), relief=tk.FLAT, font=(FONT[0], 11), cursor="hand2",
                           activebackground=p["hover"], activeforeground=MENU_DANGER,
                           command=remove)
            rm.pack(side=tk.LEFT, padx=(0, dpi.px(8)))
        win.bind("<Return>", ok)
        win.bind("<Escape>", cancel)
        win.protocol("WM_DELETE_WINDOW", cancel)
        self._center_dialog(win, min_w=420)
        try:
            win.grab_set()
        except Exception:
            pass
        win.wait_window()
        return res["v"]

    def _ask_password(self, title, prompt, allow_empty=False, out=None):
        """口令输入框（掩码显示）。返回原样字符串，取消返回 None。

        不 strip：口令里的空格是口令的一部分，替用户"顺手清理"会让他下次登不进来。
        `allow_empty=True` 时允许直接确定空口令（返回 ""），用于「导出备份」——
        空口令 = 只做编码、不加密，导入时无需口令。取消仍返回 None，两者不混。

        `out` 给一个 dict 时会多显示一条「用恢复码恢复」入口；点了它返回 None，
        并把 `out["recovery"]=True` 写进去。用旁路 dict 而不是改返回值，是为了
        不碰其它十几处调用（导出备份、改口令等）的返回值语义。
        """
        p = self.pal
        win = self._dlg_body(title)
        res = {"v": None}
        try:
            fnt = tkfont.Font(family=FONT[0], size=12)
            wrap = max([fnt.measure(line) for line in prompt.split("\n")] + [1])
        except Exception:
            wrap = DLG_TEXT_W
        tk.Label(win, text=prompt, bg=p["top"], fg=p["text"], anchor="w",
                 justify="left", font=(FONT[0], 12), wraplength=min(wrap, dpi.px(380))).pack(
                     fill=tk.X, padx=dpi.px(16), pady=(dpi.px(16), dpi.px(8)))
        var = tk.StringVar()
        e = tk.Entry(win, textvariable=var, show="\u2022", font=(FONT[0], 12),
                     bg=p["editor"], fg=p["text"], insertbackground=p["accent"],
                     relief=tk.FLAT, highlightthickness=dpi.px(1),
                     highlightbackground=p["line"], highlightcolor=p["accent"], width=30)
        e.pack(fill=tk.X, padx=dpi.px(16), ipady=dpi.px(5))
        if out is not None:
            def use_recovery(_=None):
                out["recovery"] = True
                res["v"] = None
                win.destroy()
            tk.Button(win, text="忘记口令？用恢复码恢复 →", bg=p["top"],
                      fg=p["accent"], bd=0, relief=tk.FLAT, cursor="hand2",
                      font=(FONT[0], 10), activebackground=p["top"],
                      activeforeground=p["accent"],
                      command=use_recovery).pack(anchor="w", padx=dpi.px(16),
                                                 pady=(dpi.px(6), 0))

        def ok(_=None):
            if not var.get() and not allow_empty:
                return
            res["v"] = var.get()
            win.destroy()

        def cancel(_=None):
            res["v"] = None
            win.destroy()

        self._dlg_buttons(win, ok, cancel)
        win.bind("<Return>", ok)
        win.bind("<Escape>", cancel)
        win.protocol("WM_DELETE_WINDOW", cancel)
        self._center_dialog(win, min_w=360)
        e.focus_set()
        try:
            win.grab_set()
        except Exception:
            pass
        win.wait_window()
        return res["v"]

    def _show_recovery_code(self, code, rid, note_title, replacing=False):
        """设 / 改口令后展示**本篇**恢复码。True = 用户抄下并同意启用。

        顺序是红线：先展示 → 勾选「已抄下」→ 才允许启用并写槽。反过来会留下
        一把没人知道的钥匙，而用户还以为没启用。程序绝不保存这份码。
        """
        p = self.pal
        win = self._dlg_body("保存新的恢复码" if replacing else "保存本篇恢复码")
        res = {"v": False}
        name = note_title or "这篇笔记"
        head = ("改口令后已为《%s》生成新恢复码，旧码同时作废。" % name
                if replacing else "已为《%s》生成本篇恢复码。" % name)
        tk.Label(win, text=head, bg=p["top"], fg=p["text"], anchor="w",
                 justify="left", font=(FONT[0], 12),
                 wraplength=DLG_TEXT_W).pack(fill=tk.X, padx=dpi.px(16),
                                             pady=(dpi.px(16), dpi.px(8)))
        box = tk.Frame(win, bg=p["editor"], highlightthickness=dpi.px(1),
                       highlightbackground=p["line"])
        box.pack(fill=tk.X, padx=dpi.px(16))
        tk.Label(box, text=code, bg=p["editor"], fg=p["text"],
                 font=(FONT[0], 16, "bold")).pack(padx=dpi.px(12),
                                                 pady=(dpi.px(12), dpi.px(4)))
        tk.Label(box, text="凭证编号 " + secret.recovery_id_display(rid) +
                 " · 用来对照码与笔记，不是秘密",
                 bg=p["editor"], fg=p["soft"], font=(FONT[0], 10)).pack(
                     padx=dpi.px(12), pady=(0, dpi.px(8)))

        # 复制出去的是整条凭证 [笔记名称]-[凭证编号]-[恢复码]，不再光秃秃一段码。
        # 剪贴板必须写 self.root 而不是这个弹窗 win：Tk 的剪贴板是「延迟提供」，
        # 数据由拥有所有权时存活的窗口在别人来取时才给出；win 一 destroy，回调
        # 失效，剪贴板就回退到上一个所有者（很可能是编辑区里复制过的笔记正文）。
        # root 常驻，所有权稳定，关掉本弹窗也不丢这份凭证。
        copied = tk.StringVar(value="")
        def copy_code():
            self.root.clipboard_clear()
            self.root.clipboard_append(
                secret.format_recovery_record(name, rid, code))
            self.root.update_idletasks()
            copied.set("已复制整条凭证 · 粘贴到别处后请清空剪贴板")
        tk.Button(win, text="复制恢复码", bg=p["top"], fg=p["accent"], bd=0,
                  relief=tk.FLAT, cursor="hand2", font=(FONT[0], 10),
                  activebackground=p["top"], activeforeground=p["accent"],
                  command=copy_code).pack(anchor="w", padx=dpi.px(16),
                                          pady=(dpi.px(6), 0))
        # 复制格式常驻展示：让用户随时看清复制出去长什么样，不被「已复制」提示覆盖。
        tk.Label(win, text="复制格式：[笔记名称]-[凭证编号]-[恢复码]", bg=p["top"],
                 fg=p["soft"], anchor="w", font=(FONT[0], 10),
                 wraplength=DLG_TEXT_W).pack(fill=tk.X, padx=dpi.px(16),
                                             pady=(dpi.px(2), 0))
        tk.Label(win, textvariable=copied, bg=p["top"], fg=p["soft"],
                 anchor="w", font=(FONT[0], 10),
                 wraplength=DLG_TEXT_W).pack(fill=tk.X, padx=dpi.px(16))
        tk.Label(win, text=("程序不会保存这份码，只保存用它封装过的密文。请抄到"
                            "另一台设备或密码管理器，别和 notes_data.db 放同一目录；"
                            "丢了只能重设口令重新生成。"),
                 bg=p["top"], fg=p["text"], anchor="w", justify="left",
                 font=(FONT[0], 11), wraplength=DLG_TEXT_W).pack(
                     fill=tk.X, padx=dpi.px(16), pady=(dpi.px(10), dpi.px(6)))

        def ok(_=None):
            if not checked.get():
                return
            res["v"] = True
            win.destroy()

        def cancel(_=None):
            res["v"] = False
            win.destroy()

        checked = tk.IntVar(value=0)
        def toggle():
            on = bool(checked.get())
            # 禁用态别用 accent 实底：那样看着像能点，而这枚按钮正是
            # 「先抄下、才启用」的闸门，视觉上必须一眼分得出可点与否。
            bg = p["accent"] if on else p["editor"]
            fg = on_accent(bg) if on else p["soft"]
            ok_btn.configure(state=tk.NORMAL if on else tk.DISABLED,
                             bg=bg, fg=fg, activebackground=bg,
                             activeforeground=fg)
        tk.Checkbutton(win, text="我已抄下并保存到别处", variable=checked,
                       command=toggle, bg=p["top"], fg=p["text"],
                       selectcolor=p["editor"], activebackground=p["top"],
                       activeforeground=p["text"], anchor="w", bd=0,
                       font=(FONT[0], 11)).pack(fill=tk.X, padx=dpi.px(16))
        btns = tk.Frame(win, bg=p["top"])
        btns.pack(fill=tk.X, padx=dpi.px(16), pady=(dpi.px(10), dpi.px(14)))
        tk.Button(btns, text="暂不启用", width=10, bg=p["editor"], fg=p["soft"],
                  bd=dpi.px(1), relief=tk.FLAT, font=(FONT[0], 11),
                  cursor="hand2", activebackground=p["hover"],
                  activeforeground=p["selfg"],
                  command=cancel).pack(side=tk.RIGHT, padx=(dpi.px(8), 0))
        ok_btn = tk.Button(btns, text="启用恢复", width=10, bg=p["editor"],
                           fg=p["soft"], bd=0, relief=tk.FLAT,
                           font=(FONT[0], 11, "bold"), cursor="hand2",
                           state=tk.DISABLED, command=ok)
        ok_btn.pack(side=tk.RIGHT)
        win.bind("<Return>", ok)
        win.bind("<Escape>", cancel)
        win.protocol("WM_DELETE_WINDOW", cancel)
        self._center_dialog(win, min_w=420)
        try:
            win.grab_set()
        except Exception:
            pass
        win.wait_window()
        return res["v"]

    def _ask_recovery_code(self, title, note_title, rid=""):
        """输入本篇恢复码，返回规范化后的码；取消返回 None。

        故意不掩码：24 字符分组码掩码极易抄错，而威胁模型是「捡到 db 的人」，
        不是肩窥。旁边给出凭证编号让用户确认没拿错另一篇的码。也接受直接粘贴
        复制按钮给的整条凭证串，会自动取出末尾那段恢复码。
        """
        p = self.pal
        win = self._dlg_body(title)
        res = {"v": None}
        tip = "输入《%s》的恢复码" % (note_title or "这篇笔记")
        if rid:
            tip += "\n凭证编号 " + secret.recovery_id_display(rid) + "（用于对照，不是秘密）"
        tip += "\n可直接粘贴复制的整条凭证，会自动取出恢复码"
        tk.Label(win, text=tip, bg=p["top"], fg=p["text"], anchor="w",
                 justify="left", font=(FONT[0], 12),
                 wraplength=DLG_TEXT_W).pack(fill=tk.X, padx=dpi.px(16),
                                             pady=(dpi.px(16), dpi.px(8)))
        var = tk.StringVar()
        e = tk.Entry(win, textvariable=var, font=(FONT[0], 13), bg=p["editor"],
                     fg=p["text"], insertbackground=p["accent"], relief=tk.FLAT,
                     highlightthickness=dpi.px(1), highlightbackground=p["line"],
                     highlightcolor=p["accent"], width=32)
        e.pack(fill=tk.X, padx=dpi.px(16), ipady=dpi.px(5))
        err = tk.StringVar(value="")
        tk.Label(win, textvariable=err, bg=p["top"], fg=MENU_DANGER,
                 anchor="w", font=(FONT[0], 10)).pack(fill=tk.X,
                                                       padx=dpi.px(16))

        def ok(_=None):
            code = secret.norm_recovery_code(
                secret.parse_recovery_record(var.get()))
            if not code:
                err.set("格式不对：应为 24 位数字与大写字母，可带连字符；"
                        "也可直接粘贴复制的整条凭证。")
                return
            res["v"] = code
            win.destroy()

        def cancel(_=None):
            res["v"] = None
            win.destroy()

        self._dlg_buttons(win, ok, cancel, ok_text="验证")
        win.bind("<Return>", ok)
        win.bind("<Escape>", cancel)
        win.protocol("WM_DELETE_WINDOW", cancel)
        self._center_dialog(win, min_w=420)
        e.focus_set()
        try:
            win.grab_set()
        except Exception:
            pass
        win.wait_window()
        return res["v"]

    def _ask_choose_folder(self, title, prompt, options):
        """从一棵**可收放的文件夹树**里选一个；返回其 id，取消返回 None。

        早先这里是 Listbox 平铺 + 全角空格缩进：「工作」和「个人/工作」两条长得一模
        一样，也读不出谁是谁的子 —— 平铺的文本本来就没有层次。改成树之后，
        竖引导线画出祖孙关系、三角收放无关的分支、右边标各自的笔记条数，层级一眼可见。

        折叠状态**只活在这个弹窗里**（存在局部集合里，不写库）：在弹窗里随手收起来的
        分支，关掉重开还是原样，也不会把侧栏那棵树的展开状态改掉。
        """
        p = self.pal
        win = self._dlg_body(title)
        res = {"v": None}
        tk.Label(win, text=prompt, bg=p["top"], fg=p["text"], anchor="w", justify="left",
                 font=(FONT[0], 12), wraplength=DLG_TEXT_W).pack(
                     fill=tk.X, padx=dpi.px(16), pady=(dpi.px(16), dpi.px(8)))

        ids = {f["id"] for f in options}
        # parent 不在这批候选里的就是「这一层的根」。源文件夹整支已被排除，正常不会
        # 出现孤儿；parent 悬空的脏数据（别人导来的备份）也会落到这里当根处理。
        roots = [f for f in options if f.get("parent") not in ids]
        collapsed = {f["id"] for f in options if f.get("collapsed")}
        sel = {"id": None}
        rows, order = {}, []

        outer = tk.Frame(win, bg=p["line"])     # 外面套 1px 描边框：内容不再是一片白
        outer.pack(fill=tk.X, padx=dpi.px(16), pady=(0, dpi.px(6)))
        panel = ScrollPanel(outer, p["editor"], p)
        panel.canvas.configure(height=min(max(len(options), 4), 9) * PICK_ROW_H)
        panel.pack(fill=tk.BOTH, expand=True, padx=dpi.px(1), pady=dpi.px(1))

        def kids_of(pid):
            return [f for f in options if f.get("parent") == pid]

        def base_of(fid):
            return p["sel"] if sel["id"] == fid else p["editor"]

        def paint(fid):
            """按当前选中态重刷这一行的底色（含所有子控件）。"""
            rec = rows.get(fid)
            if not rec:
                return
            row = rec["row"]
            row._base_bg = base_of(fid)
            self._row_hover(row, row._base_bg, fb=p["editor"])

        def select(fid, scroll=True):
            prev, sel["id"] = sel["id"], fid
            if prev in rows:
                paint(prev)
            paint(fid)
            # 初始选中不滚：弹窗一打开就该停在列表顶部；键盘移动时才把选中行滚进可视区
            if scroll:
                reveal(fid)

        def reveal(fid):
            """键盘移动时把选中的行滚进可视区。"""
            rec = rows.get(fid)
            if not rec:
                return
            try:
                panel.inner.update_idletasks()
                y = rec["row"].winfo_y()
                h = panel.inner.winfo_reqheight() or panel.canvas.winfo_height() or 1
                panel.canvas.yview_moveto(max(0.0, min(1.0, (y - 8.0) / float(h))))
            except Exception:
                pass

        def toggle(fid):
            """收 / 放这一支：只影响弹窗里的临时状态，不碰库。"""
            if not rows.get(fid, {}).get("kids"):
                return
            if fid in collapsed:
                collapsed.discard(fid)
            else:
                collapsed.add(fid)
            render()

        def draw_guide(c, anc_last, depth):
            """画层级的竖引导线：祖先那支还在往下走就画满整行，到顶了只画到中线。

            配上每行中线上那一小截横线，凑成 └ / ├ 的拐角，比单纯缩进更容易
            看出「这条挂在谁下面」。
            """
            mid = PICK_ROW_H / 2.0
            for a, is_last_branch in enumerate(anc_last):
                x = a * PICK_INDENT + PICK_INDENT / 2.0
                c.create_line(x, 0, x, mid if is_last_branch else PICK_ROW_H,
                              fill=p["line"], width=dpi.fx(1))
            if anc_last:      # 根目录不画线，它上面没有东西
                up = (depth - 1) * PICK_INDENT + PICK_INDENT / 2.0
                c.create_line(up, mid, depth * PICK_INDENT + PICK_INDENT / 2.0, mid,
                              fill=p["line"], width=dpi.fx(1))

        def make_row(f, depth, anc_last):
            kids = kids_of(f["id"])
            fid = f["id"]
            pad_y = (PICK_ROW_H - FOLDER_ICON_H) / 2.0     # 图标/三角在行里垂直居中
            row = tk.Frame(panel.inner, bg=p["editor"], height=PICK_ROW_H,
                           cursor="hand2")
            row.pack(fill=tk.X)
            row.pack_propagate(False)
            sp = tk.Frame(row, width=dpi.px(8), bg=p["editor"])
            sp.pack(side=tk.LEFT, fill=tk.Y)
            guide = tk.Canvas(row, width=depth * PICK_INDENT + PICK_INDENT // 2,
                              height=PICK_ROW_H, bd=0, highlightthickness=0,
                              bg=p["editor"])
            guide.pack(side=tk.LEFT)
            draw_guide(guide, anc_last, depth)
            # 箭头：与左栏同一个控件（24×28 可点区、16×16 悬停底片），叶子同宽占位
            # ⚠️ 这里行高只有 PICK_ROW_H=28，可点区会顶满一整行，别再放大底片
            tw = TwistyButton(row, self.pal, collapsed=fid in collapsed,
                              available=bool(kids), base=p["editor"],
                              command=(lambda i=fid: toggle(i)))
            tw.pack(side=tk.LEFT, pady=max(0, (PICK_ROW_H - FOLDER_TWISTY_H) / 2.0))
            ic = tk.Canvas(row, width=FOLDER_ICON_W, height=FOLDER_ICON_H, bd=0,
                           highlightthickness=0, bg=p["editor"])
            ic.pack(side=tk.LEFT, pady=pad_y)
            (self._draw_folder_icon_open if (kids and fid not in collapsed)
             else self._draw_folder_icon_closed)(ic)
            dpi.scale_canvas(ic)
            name = tk.Label(row, text=f["name"], bg=p["editor"], fg=p["text"],
                            font=(FONT[0], 12), anchor="w")
            name.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(dpi.px(6), 0))
            cnt = tk.Label(row, text="%d 条" % len(self.folder_notes(fid)),
                           bg=p["editor"], fg=p["soft"], font=(FONT[0], 11),
                           anchor="e", padx=dpi.px(10))
            cnt.pack(side=tk.RIGHT)
            rows[fid] = {"row": row, "kids": bool(kids)}
            order.append(fid)

            # 除三角外的每个控件都接管：单击选中、双击确定、进出这一行点亮/熄灭。
            # 逐个绑而不是只绑 row，是因为 Tk 不把鼠标事件冒泡给父容器 ——
            # 鼠标停在图标/文字上时，只绑 row 是收不到事件的。
            for w in (row, sp, guide, ic, name, cnt):
                w.bind("<Button-1>", lambda e, i=fid: select(i))
                w.bind("<Double-Button-1>", lambda e: ok())
                # 悬停：只看「指针还在不在这行里」 —— Tk 在父子之间穿梭会连发
                # Leave/Enter，不判区域的话鼠标一进到行里的图标上高亮就灭了
                w.bind("<Enter>", lambda e, i=fid: hover(i, True))
                w.bind("<Leave>", lambda e, i=fid: hover(i, False))

        def hover(fid, on):
            """悬停只是「这块底暗一档」，选中的行不吃这套（见 _row_hover_bg）。"""
            rec = rows.get(fid)
            if not rec:
                return
            row = rec["row"]
            if on:
                if sel["id"] != fid:
                    self._row_hover(row, _hover_bg(row._base_bg or p["editor"]),
                                    fb=p["editor"])
                return
            try:
                inside = panel.canvas.winfo_containing(*panel.canvas.winfo_pointerxy())
            except Exception:
                inside = None
            w = inside
            while w is not None:
                if w is row:
                    return
                w = getattr(w, "master", None)
            paint(fid)

        def walk(pid, depth, anc_last):
            kids = roots if pid is None else kids_of(pid)
            for i, f in enumerate(kids):
                last = (i == len(kids) - 1)
                make_row(f, depth, anc_last)
                if f["id"] not in collapsed:
                    walk(f["id"], depth + 1, anc_last + [last])

        def render():
            for w in list(panel.inner.winfo_children()):
                w.destroy()
            rows.clear()
            del order[:]
            walk(None, 0, [])
            panel.inner.update_idletasks()
            panel._update()
            panel.bind_wheel()
            cur = sel["id"] if sel["id"] in rows else None
            if cur is None:
                # 选中行被收进了刚折起来的那支里：退到最近的可见祖先，
                # 不能一跳跳回第一行 —— 用户的手感是「选中跟着这支走」
                f = next((x for x in options if x["id"] == sel["id"]), None)
                while f is not None:
                    if f.get("parent") in rows:
                        cur = f["parent"]
                        break
                    f = next((x for x in options if x["id"] == f.get("parent")), None)
            sel["id"] = None
            if cur:
                select(cur, scroll=False)

        def move_sel(delta):
            if not order or sel["id"] not in order:
                return
            i = order.index(sel["id"]) + delta
            if 0 <= i < len(order):
                select(order[i])

        def key_branch(key):
            """→ 展开这一支，← 收起。已经是那条边的状态就不动，免得按一下弹一下。"""
            fid = sel["id"]
            if not fid or not rows.get(fid, {}).get("kids"):
                return
            if key == "Right" and fid in collapsed:
                collapsed.discard(fid)
            elif key == "Left" and fid not in collapsed:
                collapsed.add(fid)
            else:
                return
            render()

        KEYS = ("<Up>", "<Down>", "<Left>", "<Right>")

        def unbind_keys():
            """摘掉四个全局方向键：它们是 bind_all 绑上去的，关窗必须还回去。"""
            try:
                for k in KEYS:
                    panel.canvas.unbind_all(k)
            except Exception:
                pass

        def retire_panel():
            """让这只临时面板「退休」—— 它的 bind_all 是全进程范围的兜底。

            不能直接 unbind_all("<MouseWheel>")：那会把主窗口那两块面板的滚轮兜底
            一起解掉（同一个 all tag），整个列表就滚不动了。改成让它不再认领任何
            区域，它在全局回调里就自然变成空操作。
            """
            panel._in_region = lambda e: False
            panel._on_wheel_child = lambda e: None
            panel._on_wheel_child_dir = lambda d: None

        def cleanup():
            retire_panel()
            unbind_keys()

        def ok(_=None):
            if not sel["id"]:
                return
            res["v"] = sel["id"]
            cleanup()
            win.destroy()

        def cancel(_=None):
            res["v"] = None
            cleanup()
            win.destroy()

        render()
        self._dlg_buttons(win, ok, cancel)
        win.bind("<Return>", ok)
        win.bind("<Escape>", cancel)
        win.protocol("WM_DELETE_WINDOW", cancel)
        # 方向键必须 bind_all：Tk 把按键事件交给**焦点控件**、不冒泡给容器，
        # 焦点落在行里的某个 Label 上时，面板自己是收不到的。弹窗是模态的，
        # 这段时间没有第二个可交互的窗口，绑全局是安全的 —— 关窗前摘掉即可。
        for key, fn in (("<Up>", lambda e: move_sel(-1)), ("<Down>", lambda e: move_sel(1)),
                        ("<Left>", lambda e: key_branch("Left")),
                        ("<Right>", lambda e: key_branch("Right"))):
            panel.canvas.bind_all(key, fn)
        win.bind("<Destroy>", lambda e: unbind_keys())
        self._center_dialog(win)
        panel.canvas.focus_set()
        win.grab_set()
        win.wait_window()
        return res["v"]
