# -*- coding: utf-8 -*-
"""`@` 关联笔记：认触发词、给候选排序。纯字符串 / 纯数据，不依赖 tkinter。

为什么单独一个模块：编辑器里的触发判定（`@` 后面跟空格才算、邮箱里的 `@` 不算）
和候选排序都是能脱离界面验证的逻辑，放进这里就能直接写单测，不必起 Tk。

存储形态见 app_actions：链接登记表里存 `note:<笔记 id>`，显示文字是 `@标题`。
用 id 而不是标题 —— 目标笔记改名、换文件夹，已有链接都不会断。
"""

AT = "@"
NOTE_URL_PREFIX = "note:"

# `@` 左边允许的字符：行首、空白、以及常见中英文标点。
# 收这么紧是为了别把 `zhang@163.com` 里的 @ 当成触发（那种 @ 左边是字母/数字）。
_LEFT_OK = set(" \t\r\n（(【“「『《＜<「，。；：、！？…,.;:!?'\"）)】」』》）")

# 查询词里不允许出现这些：它们说明用户已经不是在找笔记了
# （`@ 张 三` 多半是在写一句话，而不是在搜「张 三」）
_STOP = set(" \t@")


def find_mention(line, col):
    """在 line[:col] 里认出待补全的 `@查询`，返回 (起始列, 查询词)；认不出给 None。

    规则：光标前最近的那个 `@`，它后面必须紧跟一个空格（用户就是按「@ + 空格」
    触发的），`@` 左边必须是行首 / 空白 / 标点，查询词里不能再有空格或 `@`。
    """
    if not line or col <= 0:
        return None
    head = line[:col]
    i = head.rfind(AT)
    if i < 0:
        return None
    if i > 0 and head[i - 1] not in _LEFT_OK:
        return None
    rest = head[i + 1:]
    if not rest.startswith(" "):
        # 光秃秃一个 `@`（还在打字、或者是邮箱）不触发
        return None
    q = rest.strip()
    for c in q:
        if c in _STOP:
            return None
    return i, q


def note_url(nid):
    """内链在链接登记表里存的地址。"""
    return "%s%s" % (NOTE_URL_PREFIX, nid or "")


def note_id_of_url(url):
    """内链地址 → 笔记 id；不是内链则返回 None。"""
    u = (url or "").strip()
    if not u.startswith(NOTE_URL_PREFIX):
        return None
    nid = u[len(NOTE_URL_PREFIX):].strip()
    # id 是 uid（字母数字），带斜杠/空白的一律不算内链 —— 挡住 `note://host` 这种
    # 恰好同前缀的怪地址，免得它被当成「目标笔记不存在」
    if not nid:
        return None
    for c in nid:
        if c in " \t/\\":
            return None
    return nid


def mention_text(title):
    """插入正文的显示文字：标题前带 `@`，一眼能看出这是条关联而不是普通链接。"""
    return AT + (title or "").strip()


def rank(cands, query, limit=8):
    """按查询词给候选笔记排序，返回最多 limit 条。

    cands 是 [{"id","title","sub","updated"}] —— 由界面层备好（已排除自己、
    已取好标题），这里只管排。没有查询词时按最近修改排序（想关联的通常是刚写的那篇）；
    有查询词时按「标题开头 > 标题包含 > 副标题（所在文件夹）包含」三档，
    同档里按最近修改降序。
    """
    q = (query or "").strip().lower()
    if not q:
        return sorted(cands, key=lambda c: -_up(c))[:limit]

    def score(c):
        t = (c.get("title") or "").lower()
        s = (c.get("sub") or "").lower()
        if t.startswith(q):
            return 0
        if q in t:
            return 1
        if q in s:
            return 2
        return 3

    out = [c for c in cands if score(c) < 3]
    out.sort(key=lambda c: (score(c), -_up(c), (c.get("title") or "")))
    return out[:limit]


def _up(c):
    try:
        return float(c.get("updated") or 0)
    except Exception:
        return 0.0
