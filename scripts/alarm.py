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
           "fmt_full", "is_done", "tips", "due", "month_grid", "shift_month",
           "WEEKDAYS", "MAX_TEXT", "GRACE",
           "STATE_PENDING", "STATE_EXPIRED", "STATE_ACKED", "STATE_MARK",
           "STATES", "STATE_LABELS", "STATE_SYMBOLS", "state_of",
           "REPEAT_ONCE", "REPEAT_DAILY", "REPEAT_WEEKLY", "REPEATS",
           "REPEAT_LABELS", "repeat_kind", "hm_of", "normalize_rule",
           "current_slot", "next_occ", "due_all", "fmt_repeat", "fmt_when"]

# 四态（2026-11-18 用户拍板）：正文卡片与汇总表**共用**同一套判定，不许各算各的。
#   pending 待提醒：还没到点（at > now）
#   expired 已过期：时间已过（at <= now），纯时钟推算，不落库
#   acked   已确认：用户点过到点弹窗的「知道了」/ 卡片上的「完成」，写 alarm_acked
#   mark    仅标记：at == 0，从不提醒，也不是完成
# ⚠️ 已确认优先于已过期：点过确认还显示「已过期」会让人以为没生效。
STATE_PENDING = "pending"
STATE_EXPIRED = "expired"
STATE_ACKED = "acked"
STATE_MARK = "mark"
STATES = (STATE_PENDING, STATE_EXPIRED, STATE_ACKED, STATE_MARK)
STATE_LABELS = {STATE_PENDING: "待提醒", STATE_EXPIRED: "已过期",
                STATE_ACKED: "已确认", STATE_MARK: "仅标记"}
# 汇总表用 BMP 符号（本机 Tcl/Tk 不吃 U+FFFF 以上的 emoji）
STATE_SYMBOLS = {STATE_PENDING: "●", STATE_EXPIRED: "○",
                 STATE_ACKED: "✓", STATE_MARK: "◇"}

# 日历表头。周一排第一列（中国习惯），month_grid 的 lead 计算跟这个顺序绑定，
# 改这里必须同步改 month_grid。也是重复提醒 days 的序号：0=一 … 6=日，
# 与 datetime.date.weekday() 完全一致，不用再换算。
WEEKDAYS = ("一", "二", "三", "四", "五", "六", "日")

# 提醒类型（2026-11-18 用户定）：
#   once   按时间提醒 —— 现有行为，at 就是那一个触发时刻，响一次即止；
#   daily  按天       —— at 是「起始日」（那天之前不响），hm 是每天的时刻；
#   weekly 按周       —— at 是起始日，hm 是时刻，days 是选中的星期（0=一…6=日）。
# ⚠️ 老数据没有 repeat 字段，一律当 once —— 不用迁移、不用写库。
REPEAT_ONCE, REPEAT_DAILY, REPEAT_WEEKLY = "once", "daily", "weekly"
REPEATS = (REPEAT_ONCE, REPEAT_DAILY, REPEAT_WEEKLY)
REPEAT_LABELS = {REPEAT_ONCE: "按时间提醒", REPEAT_DAILY: "按天提醒",
                 REPEAT_WEEKLY: "按周提醒"}

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


# ---------- 重复提醒：规则 + 周期槽位 ----------
# 一条提醒的完整形态（once 只有前三字段，老数据天然兼容）：
#   {"id","text","at","repeat":"once|daily|weekly","hm":"09:00","days":[0,1,2]}
# `at` 对 once 是触发时刻；对 daily/weekly 是「起始日」（含 hm 那一刻）。

def repeat_kind(data):
    r = (data or {}).get("repeat") or REPEAT_ONCE
    return r if r in REPEATS else REPEAT_ONCE


def hm_of(data):
    """时刻归一成 HH:MM；坏值退 09:00，绝不把非法值带进日期计算。"""
    raw = str((data or {}).get("hm") or "09:00")
    try:
        parts = raw.split(":")
        hh, mm = int(parts[0]), int(parts[1])
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return "%02d:%02d" % (hh, mm)
    except (ValueError, IndexError, TypeError):
        pass
    return "09:00"


def normalize_rule(kind, hm, days):
    """弹框结果归一成写库字段；once 返回最小集，不污染老数据。"""
    kind = kind if kind in REPEATS else REPEAT_ONCE
    if kind == REPEAT_ONCE:
        return {"repeat": REPEAT_ONCE}
    out = {"repeat": kind, "hm": hm_of({"hm": hm})}
    if kind == REPEAT_WEEKLY:
        picked = []
        for x in (days or []):
            try:
                v = int(x)
                if 0 <= v <= 6 and v not in picked:
                    picked.append(v)
            except (TypeError, ValueError):
                continue
        out["days"] = sorted(picked)
    return out


def _ts_on(day, hm):
    hh, mm = [int(x) for x in hm.split(":")]
    return datetime.datetime(day.year, day.month, day.day, hh, mm).timestamp()


def slot_date(data, now=None):
    """当前周期的那一天：最近一个 <= now 的应触发日；没到起始日返回 None。

    once → at 那天（只要 at<=now）；daily → 今天或昨天（看时刻过没过）；
    weekly → 往回最多 7 天找选中的星期。坏规则（weekly 却没选天）视为不响。
    """
    now = datetime.datetime.now().timestamp() if now is None else now
    d = data or {}
    at = d.get("at") or 0
    if not at:
        return None
    kind = repeat_kind(d)
    if kind == REPEAT_ONCE:
        return datetime.datetime.fromtimestamp(at).date() if at <= now else None
    days = d.get("days") or []
    if kind == REPEAT_WEEKLY and not days:
        return None
    anchor = datetime.date.fromtimestamp(at)
    hm = hm_of(d)
    today = datetime.datetime.fromtimestamp(now).date()
    for back in range(8):
        day = today - datetime.timedelta(days=back)
        if day < anchor:
            return None
        if kind == REPEAT_DAILY or day.weekday() in days:
            if _ts_on(day, hm) <= now:
                return day
    return None


def next_occ(data, now=None):
    """严格晚于 now 的下一次触发时间戳；不会再响返回 0。"""
    now = datetime.datetime.now().timestamp() if now is None else now
    d = data or {}
    at = d.get("at") or 0
    if not at:
        return 0
    kind = repeat_kind(d)
    if kind == REPEAT_ONCE:
        return at if at > now else 0
    days = d.get("days") or []
    if kind == REPEAT_WEEKLY and not days:
        return 0
    anchor = datetime.date.fromtimestamp(at)
    hm = hm_of(d)
    today = datetime.datetime.fromtimestamp(now).date()
    for fwd in range(370):                 # 一年足够覆盖任何按周规则
        day = today + datetime.timedelta(days=fwd)
        if day < anchor:
            continue
        if kind == REPEAT_DAILY or day.weekday() in days:
            ts = _ts_on(day, hm)
            if ts > now:
                return ts
    return 0


def next_slot_date(data, now=None):
    """**下一个**应触发日（不管时刻过没过）。

    专门服务「还没到第一个周期就提前确认」：此时 `slot_date()` 返回 None，
    若退成 `@start` 这种静态键，等真正到点时算出来的是 `@2026-11-18`，
    两边永远比不上 —— 用户点了「完成」照样弹窗（2026-11-18 实测踩到）。
    """
    now = datetime.datetime.now().timestamp() if now is None else now
    d = data or {}
    at = d.get("at") or 0
    if not at:
        return None
    kind = repeat_kind(d)
    if kind == REPEAT_ONCE:
        return datetime.datetime.fromtimestamp(at).date()
    days = d.get("days") or []
    if kind == REPEAT_WEEKLY and not days:
        return None
    anchor = datetime.date.fromtimestamp(at)
    today = datetime.datetime.fromtimestamp(now).date()
    for fwd in range(8):
        day = today + datetime.timedelta(days=fwd)
        if day < anchor:
            continue
        if kind == REPEAT_DAILY or day.weekday() in days:
            return day
    return None


def state_key(base_key, data, now=None):
    """fired / acked 的键：once 用旧键，重复提醒按当前周期分槽位。

    当前周期还没开始（今天这一格还没到点、且更早的槽位都不存在）时，
    取**下一个**槽位 —— 提前点「完成」才能挡住即将到来的那一次。
    """
    if repeat_kind(data) == REPEAT_ONCE:
        return base_key
    day = slot_date(data, now) or next_slot_date(data, now)
    return base_key + "@" + (day.isoformat() if day else "start")


def due_all(items, fired, base_key_of, now=None, grace=GRACE, acked=None):
    """扫一轮该处理的提醒（含重复）。items=[(nid, data)]。

    返回 `(fire, stale)`，每项 `(nid, data, key)`：key 已带周期槽位，
    调用方直接写 fired 即可。与旧的 `due()` 并存 —— 后者只服务一次性提醒的
    契约测试，主流程统一走这里。

    `acked` 是 `settings["alarm_acked"]`：**已经在正文卡片上点过「完成」的
    提醒直接跳过**，到点不再弹窗（用户 2026-11-18 要求）。这一步必须放在
    fire/stale 之前 —— 已确认的既不该弹，也不该被补记成 fired。
    """
    now = datetime.datetime.now().timestamp() if now is None else now
    acked = acked or {}
    fire, stale = [], []
    for nid, d in items:
        at = (d or {}).get("at") or 0
        if not at:
            continue
        if repeat_kind(d) == REPEAT_ONCE:
            if at > now:
                continue
            ts = at
        else:
            day = slot_date(d, now)
            if day is None:
                continue
            ts = _ts_on(day, hm_of(d))
        key = state_key(base_key_of(nid, d), d, now)
        if fired.get(key) or acked.get(key):
            continue
        if repeat_kind(d) != REPEAT_ONCE:
            # 重复提醒：当天/本周这一格只要没响过就该弹。GRACE 只对一次性
            # 提醒有意义（合盖回来不补弹）—— 否则 9 点开机、10 点开程序就
            # 永远错过这一天的药，重复功能等于失效。
            fire.append((nid, d, key))
        elif now - ts <= grace:
            fire.append((nid, d, key))
        else:
            stale.append((nid, d, key))
    return fire, stale


def fmt_repeat(data, now=None):
    """规则短文案：汇总表时间列 / 卡片第一行用。once 走原来的 fmt_at。"""
    d = data or {}
    if repeat_kind(d) == REPEAT_ONCE:
        return fmt_at(d.get("at") or 0)
    hm = hm_of(d)
    if repeat_kind(d) == REPEAT_DAILY:
        return "每天 " + hm
    days = sorted(d.get("days") or [])
    if not days:
        return "未选星期"
    return "每周" + "".join(WEEKDAYS[x] for x in days) + " " + hm


def fmt_when(data, now=None):
    """卡片时间行：规则 + 下次时刻（重复提醒才有）。"""
    now = datetime.datetime.now().timestamp() if now is None else now
    out = fmt_repeat(data, now)
    if repeat_kind(data) == REPEAT_ONCE:
        return out
    nxt = next_occ(data, now)
    return out + (" · 下次 " + fmt_at(nxt) if nxt else "")


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


def fmt_full(ts):
    """`2026-11-09 18:00`；没设时间返回空串。

    汇总表里要的是「哪一天几点」，不能用 fmt_at 的「今天 / 明天」相对写法 ——
    表是拿来回看的，隔一天再看，「今天 18:00」就指向了别的日子。
    """
    if not ts:
        return ""
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def is_done(data, now=None):
    """「已完成」的判据（用户 2026-11-09 定义）：**提醒时刻已过**就算完成。

    纯计算、不落库：状态跟着时钟走，过了那个点自动翻面，不需要任何按钮，
    也不会因为换机器、导备份而丢。没设时间（at=0）的闹钟只是正文里的一枚
    标记，永远不算完成。
    """
    now = datetime.datetime.now().timestamp() if now is None else now
    at = (data or {}).get("at") or 0
    if not at:
        return False
    if repeat_kind(data) != REPEAT_ONCE:
        # 重复提醒的「这一次」＝当前周期到点了就完成，下次自动翻回待提醒
        day = slot_date(data, now)
        return day is not None
    return at <= now


def state_of(data, acked=False, now=None):
    """一条提醒的四态判定，返回 STATES 之一。

    `acked` 由调用方按 `state_key()` 从 settings["alarm_acked"] 查好传进来
    （纯函数不碰 data 结构，正文卡片和汇总表各查各的 note_id）。重复提醒查的是
    **当前周期**那一个键 —— 今天确认了明天还是待提醒。

    ⚠️ 顺序不能换：acked 一定压在 expired 前面；at=0 即使误写了 acked 也按「仅标记」
    显示 —— 没设时间的提醒从不弹窗，也就不该有「已确认」。
    """
    at = (data or {}).get("at") or 0
    if not at:
        return STATE_MARK
    if acked:
        return STATE_ACKED
    now = datetime.datetime.now().timestamp() if now is None else now
    if repeat_kind(data) != REPEAT_ONCE:
        # 起始日还没到 = 待提醒；到了当前周期那一刻 = 已过期（等确认）
        return STATE_EXPIRED if slot_date(data, now) is not None else STATE_PENDING
    return STATE_EXPIRED if at <= now else STATE_PENDING


def tips(data, fired=False, now=None):
    """悬浮提示的两行文字：第一行标题，第二行细节。

    已提醒的优先说明状态 —— 用户悬停过来是想知道「响过没有」，而不是再看一遍
    时间和内容（那些在右键里能改）。
    """
    now = now or datetime.datetime.now().timestamp()
    at = (data or {}).get("at") or 0
    text = (data or {}).get("text") or ""
    who = text or "任务提醒"
    when = fmt_repeat(data, now) if repeat_kind(data) != REPEAT_ONCE else fmt_at(at)
    if fired:
        return who, ("已提醒 · " + when) if when else "已提醒"
    if not at:
        return who, "未设时间 · 右键可编辑"
    if repeat_kind(data) != REPEAT_ONCE:
        return who, when + " · 下次 " + (fmt_at(next_occ(data, now)) or "—")
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
