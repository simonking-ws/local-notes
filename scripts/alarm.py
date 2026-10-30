# -*- coding: utf-8 -*-
"""任务提醒（正文里的小闹钟）：时间解析、到点判断、悬浮文案。纯函数，不依赖 tkinter。

数据结构 —— 一条提醒的样子（既写进 content，也放内存表）：

    {"id": "a1b2c3d4", "text": "交周报", "at": 1793700000.0}

`at` 是 Unix 时间戳；`at` 为 0 表示「没设时间」（只当正文里的一个标记，不提醒）。

**「已提醒」不放在这条数据里**，而是记在 `settings["alarm_fired"]` 的
`{"<note_id>|<alarm_id>": 时间戳}`。理由：已提醒是「运行时状态」，不是正文内容；
塞进 content 就要在提醒那一刻改笔记并触发一次保存（没打开的笔记还得改 data，
加密笔记还得重新加密）。分开存之后，正文里那条数据永远是干净的，导出、
搜索、加密全都当它不存在。

几条边界，都是刻意的：

1. **时间可空**。没填时间的闹钟只占正文一个位置，永远不提醒 —— 相当于一个
   视觉标记。填了才有 `at`。
2. **过期太久就不弹**。合盖三小时回来不该一次弹八个窗口（见 GRACE）。
   但「过期」不等于「没发生」：照样记成已提醒，悬浮时显示「已提醒」，免得
   用户以为它还没到点。
3. **日期写成过去的**（比如手滑填了昨天）不报错，直接算过期 —— 用户可能
   就是在补记一件已经做过的事。
"""

import calendar
import datetime
import re
import uuid

__all__ = ["new_id", "normalize_text", "default_when", "parse_when", "fmt_at",
           "tips", "due", "month_grid", "shift_month", "WEEKDAYS",
           "MAX_TEXT", "GRACE"]

# 日历表头。周一排第一列（中国习惯），month_grid 的 lead 计算跟这个顺序绑定，
# 改这里必须同步改 month_grid。
WEEKDAYS = ("一", "二", "三", "四", "五", "六", "日")

MAX_TEXT = 120              # 提醒内容长度上限（够写一件事，又不至于把浮窗撑爆）
GRACE = 10 * 60             # 超过 10 分钟还没弹，就不弹了（只记状态）

_DATE_RE = re.compile(r"^\s*(\d{4})?[-/.年]?\s*(\d{1,2})[-/.月]\s*(\d{1,2})\s*日?\s*$")
_TIME_RE = re.compile(r"^\s*(\d{1,2})\s*[:：]\s*(\d{1,2})\s*$")


def new_id():
    """8 位短 id：只用来把正文里的图与数据对起来，不求全局唯一。"""
    return uuid.uuid4().hex[:8]


def normalize_text(s):
    """提醒内容归一：把换行/连续空格压成一个空格，并截断到上限。

    压空格是因为它要在一行 400px 的浮窗里显示，带换行会把浮窗撑得很高。
    """
    return " ".join(str(s or "").split())[:MAX_TEXT]


def _mk_ts(y, m, d, hh, mm):
    try:
        return datetime.datetime(y, m, d, hh, mm).timestamp()
    except (ValueError, OverflowError):
        return None


def default_when(now=None):
    """新建提醒时的默认时刻：`(日期文本, 时, 分)`。

    取「一小时后整点」而不是当前时刻 —— 默认填当前时刻最容易一按确定就弹窗
    （而且现实中人写提醒基本都不是「就现在」）。跨零点时必须带上日期：
    23 点半新建会落到次日 00:00，日期留空按今天算就变成「今天 00:00」，
    一确定即逾期。
    """
    now = now or datetime.datetime.now()
    nxt = (now + datetime.timedelta(hours=1)).replace(minute=0, second=0,
                                                      microsecond=0)
    date_s = nxt.strftime("%Y-%m-%d") if nxt.date() != now.date() else ""
    return date_s, nxt.hour, nxt.minute


def parse_when(date_s, time_s, now=None):
    """把对话框里的「日期」「时间」两段文本解析成一个时间戳。

    - 日期空 → 今天；支持 `2026-10-30` / `10-30` / `10/30` / `10月30日`
    - 时间空 → 返回 0（不设时间，不提醒）
    - 返回 `(ts, err)`：`err` 非空表示输入不合法，此时 `ts` 无意义
    """
    now = now or datetime.datetime.now()
    t = str(time_s or "").strip()
    if not t:
        return 0.0, ""
    m = _TIME_RE.match(t)
    if not m:
        return None, "时间请写成 18:00 这样"
    hh, mm = int(m.group(1)), int(m.group(2))
    if hh > 23 or mm > 59:
        return None, "时间超出范围了（00:00 - 23:59）"

    d = str(date_s or "").strip()
    if not d:
        y, mo, dd = now.year, now.month, now.day
    else:
        m = _DATE_RE.match(d)
        if not m:
            return None, "日期请写成 2026-10-30 或 10-30"
        y = int(m.group(1)) if m.group(1) else now.year
        mo, dd = int(m.group(2)), int(m.group(3))
    ts = _mk_ts(y, mo, dd, hh, mm)
    if ts is None:
        return None, "这个日期不存在"
    return ts, ""


def month_grid(year, month):
    """某个自然月的日历网格：42 格（6 行 × 7 列），每格是该月日号或 0（补白）。

    固定 6 行而不按需 4/5 行，是为了让日历控件翻月时高度不跳 —— 窗口高度一变，
    「‹ ›」按钮就会跟着上下窜，点的位置全靠猜。
    周一排第一列：`calendar.monthrange` 的星期也是周一=0，直接当补白数用。
    """
    first_wd, ndays = calendar.monthrange(year, month)
    cells = [0] * first_wd + list(range(1, ndays + 1))
    return cells + [0] * (42 - len(cells))


def shift_month(year, month, delta):
    """前后翻 `delta` 个月，返回 `(年, 月)`；跨年自己滚。"""
    idx = year * 12 + (month - 1) + delta
    return idx // 12, idx % 12 + 1


def fmt_at(ts):
    """`10-30 18:00`；今天的省略月日，显示 `今天 18:00`。没设时间返回空串。"""
    if not ts:
        return ""
    t = datetime.datetime.fromtimestamp(ts)
    today = datetime.date.today()
    hm = t.strftime("%H:%M")
    if t.date() == today:
        return "今天 " + hm
    if t.date() == today + datetime.timedelta(days=1):
        return "明天 " + hm
    if t.year == today.year:
        return t.strftime("%m-%d ") + hm
    return t.strftime("%Y-%m-%d ") + hm


def tips(data, fired=False, now=None):
    """悬浮提示的两行文字：第一行标题，第二行细节。

    已提醒的优先说明状态 —— 用户悬停过来是想知道「响过没有」，而不是再看一遍
    时间和内容（那些在右键里能改）。
    """
    now = now or datetime.datetime.now().timestamp()
    at = (data or {}).get("at") or 0
    text = (data or {}).get("text") or ""
    who = text or "任务提醒"
    if fired:
        when = fmt_at(at)
        return who, ("已提醒 · " + when) if when else "已提醒"
    if not at:
        return who, "未设时间 · 右键可编辑"
    if at <= now:
        return who, fmt_at(at) + " · 已到点未提醒"
    return who, fmt_at(at) + " 提醒"


def due(items, now, grace=GRACE):
    """挑出这一轮该处理的提醒。

    `items` 是 `[(note_id, data)]`（只含**未**提醒过的），返回
    `(该弹的, 该判过期的)`：两个都是 `[(note_id, data)]`。

    分成两个列表而不是只返回该弹的：过期的也得记下状态，否则每一轮 tick 都会
    重新扫到它，永远停不下来。
    """
    fire, stale = [], []
    for note_id, d in items:
        at = (d or {}).get("at") or 0
        if not at or at > now:
            continue
        if now - at <= grace:
            fire.append((note_id, d))
        else:
            stale.append((note_id, d))
    return fire, stale
