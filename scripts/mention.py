# -*- coding: utf-8 -*-
"""`@` 关联笔记：认触发词、给候选排序。纯字符串 / 纯数据，不依赖 tkinter。

为什么单独一个模块：编辑器里的触发判定（`@` 后面跟空格才算、邮箱里的 `@` 不算）
和候选排序都是能脱离界面验证的逻辑，放进这里就能直接写单测，不必起 Tk。

存储形态见 app_actions：链接登记表里存 `note:<笔记 id>`，显示文字是 `@标题`。
用 id 而不是标题 —— 目标笔记改名、换文件夹，已有链接都不会断。
"""

AT = "@"
NOTE_URL_PREFIX = "note:"

# 查询词里不允许出现这些：它们说明用户已经不是在找笔记了
# （`@ 张 三` 多半是在写一句话，而不是在搜「张 三」）
_STOP = set(" \t@")


def find_mention(line, col):
    """在 line[:col] 里认出待补全的 `@查询`，返回 (起始列, 查询词)；认不出给 None。

    规则只有两条，都落在 `@` **右边**：

      1. 光标前最近的那个 `@` 后面必须紧跟一个空格（用户按的就是「@ + 空格」）；
      2. 这个空格之后的查询词里不能再有空格或 `@`（又一个空格 = 开始写下一句）。

    ⚠️ 不要再加「`@` 左边必须是行首 / 空白 / 标点」这类白名单。早期版本靠它挡
    邮箱（`zhang@163.com` 左边是字母），可它顺手把**紧跟文字**的 `@` 也挡掉了：
    「今天开会@ 」左邻是「会」，「abc@ 」左邻是「c」，用户反馈弹不出候选框。
    其实邮箱根本不需要这条：`@` 后面紧跟的是 `163.com` 而不是空格，第 1 条已经
    把它们全挡住了（`zhang@163.com`、`a@b c` 都不触发）。真正区分「@ 关联」与
    「普通 @」的信号是**后面那个空格**，不是左边那个字符。
    """
    if not line or col <= 0:
        return None
    head = line[:col]
    i = head.rfind(AT)
    if i < 0:
        return None
    rest = head[i + 1:]
    if not rest.startswith(" "):
        # 光秃秃一个 `@`（还在打字、或者邮箱 / 用户名里的 @）不触发
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


def rank(cands, query, limit=None):
    """按查询词给候选笔记排序；limit 给数字才截断，默认**返回全部**。

    ⚠️ 别把默认值改回 8：候选浮层现在是一屏 8 行 + 滚动条，8 条上限会把
    后面的笔记直接丢掉，滚动也翻不出来（2026-11-06 用户报「没展示全部笔记」）。
    真要限量就由调用方显式传 limit。

    cands 是 [{"id","title","sub","updated"}] —— 由界面层备好（已排除自己、
    已取好标题），这里只管排。没有查询词时按最近修改排序（想关联的通常是刚写的那篇）；
    有查询词时按「标题开头 > 标题包含 > 副标题（所在文件夹）包含」三档，
    同档里按最近修改降序。

    查询词按空格分词做 AND：搜索框里可以写「工作 会议」找「工作 / 会议」目录下的
    某篇笔记（正文里打字这条路本来就禁止第二个空格，只有弹框搜索框用得上）。
    单个词时行为与老版完全一致。
    """
    words = (query or "").strip().lower().split()
    if not words:
        return sorted(cands, key=lambda c: -_up(c))[:limit]
    q = words[0]

    def score(c):
        t = (c.get("title") or "").lower()
        s = (c.get("sub") or "").lower()
        hay = t + " " + s
        if not all(w in hay for w in words):
            return 3
        if all(w in t for w in words):
            return 0 if t.startswith(q) else 1
        if q in t:
            return 1
        if q in s:
            return 2
        return 2

    out = [c for c in cands if score(c) < 3]
    out.sort(key=lambda c: (score(c), -_up(c), (c.get("title") or "")))
    return out[:limit]


def _up(c):
    try:
        return float(c.get("updated") or 0)
    except Exception:
        return 0.0
