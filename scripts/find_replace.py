# -*- coding: utf-8 -*-
"""查找替换的纯逻辑：命中区间、执行顺序、参考执行器。

⚠️ 这里**不 import tkinter**：替换最容易出错的地方是「一次改多处的顺序」，
把它抽成可单测的纯函数，比在 GUI 里靠肉眼验证靠谱得多。

分工（别搞混）：

* 命中的**位置**由 Tk 自己算（`text.search`，返回 "行.列" 索引）。正文里可能
  有代码块、闹钟这类嵌入窗口/图片，按 Python 字符串偏移去换算会错位。
* 命中的**执行顺序**走这里的 `replacement_plan()`：从后往前删/插，前面的索引
  才不会因为后面先改了而漂移。UI 真实调用它。
* `find_spans()` / `apply_plan()` 是同一套规则的纯字符串版本，供单测当参考
  实现 —— UI 不能整段 delete/insert 重写正文，那样富文本样式全没了。
"""


def find_spans(text, query, nocase=True, limit=5000):
    """返回不重叠的命中区间 [(start, end)]（左闭右开的字符偏移）。

    与 Ctrl+F 的 `text.search(..., nocase=True)` 同口径：不区分大小写、命中之间
    不重叠（`aaaa` 找 `aa` 得到两处而不是三处）、空关键词不算命中。
    `limit` 是和查找浮条一样的兜底上限，防止病态输入卡死。
    """
    if not query:
        return []
    hay = text.lower() if nocase else text
    needle = query.lower() if nocase else query
    n = len(needle)
    spans = []
    i = 0
    while len(spans) < limit:
        j = hay.find(needle, i)
        if j < 0:
            break
        spans.append((j, j + n))
        i = j + n                  # 下一轮从命中末尾开始：不重叠
    return spans


def replacement_plan(spans, replacement, limit=5000):
    """把命中区间排成执行顺序：倒序、去重叠，返回 [(start, end, replacement)]。

    倒序是硬要求：Text 的索引是「第几个字符」，从前往后替换的话，第一处
    `delete + insert` 一改变长度，后面所有索引就全指到别的字符上了。
    调用方按返回顺序对 Text 逐个 `delete(pos, end)` / `insert(pos, text)` 即可
    （`spans` 里可以是 Tk 的 "行.列" 字符串，这里只排序、不做算术）。
    """
    out = []
    prev_start = None
    for start, end in reversed(list(spans)[:limit]):
        if end <= start:
            continue
        # 与已经保留的（更靠后）那一处重叠时只保留靠后的一处
        if prev_start is not None and end > prev_start:
            continue
        out.append((start, end, replacement))
        prev_start = start
    return out


def apply_plan(text, plan):
    """按 plan 在纯字符串上执行一遍，返回新文本（单测的参考执行器）。

    UI 不用它：编辑器里还有加粗、链接、代码块这些非文本内容，只能针对每个
    命中就地删/插，保留两侧既有的样式标签。
    """
    for start, end, replacement in plan:
        text = text[:start] + replacement + text[end:]
    return text


def replace_all(text, query, replacement, nocase=True, limit=5000):
    """纯字符串的「全部替换」：返回 (新文本, 处数)。"""
    plan = replacement_plan(find_spans(text, query, nocase, limit), replacement)
    return apply_plan(text, plan), len(plan)
