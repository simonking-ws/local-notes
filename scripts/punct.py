# -*- coding: utf-8 -*-
"""格式化前的中文标点归一化：中文逗号 / 引号 / 冒号 → 英文（纯函数，不依赖 tkinter）。

起因：中文输入法下敲 SQL 或 JSON，标点极容易跟着中文走 ——
`IN (81862，82929)`、`{“name”: “张三”}`、`{"a"：1}` 这种拿去解析必然报错，
而在屏上一个个改最费神。格式化前先扫一遍，用户按一次「格式化」就顺手把
这类脏字符清掉。

边界（别为了「更干净」往外扩）：

1. **只动引号与注释外面**。已被引号括起来的内容是数据不是语法，
   `'他说，你好'` 里的逗号必须原样留着 —— 格式化层悄悄改用户数据是本项目
   最不能接受的 bug（代码格式化一直的立场是「拿不准就宁可少做」）。
   中文引号同理：转换前它还不是定界符，但既然成对出现，就先按「一对引号」
   圈起来、只换两个定界符，里面的逗号照原样 —— 否则 `“你好，世界”` 会被
   改成 `"你好,世界"`，改的是用户写的文案。
2. **只认逗号、引号与（JSON 的）冒号**。全角分号、括号不在其列，不越界替
   用户决定。冒号只对 JSON 生效：SQL 里 `:` 是命名参数 / `:=` 的语法字符，
   换错了会把值悄悄改成别的东西，而用户只要求 JSON。
3. 引号一律落到 ASCII：`“”` → `"`，`‘’` → `'`。
"""

__all__ = ["normalize"]

# 各语言里「不能被改写」的区段：行注释、块注释、字符串 / 标识符引号。
# 只列会用到的两种语言 —— 其它语言不走这个函数（见 widgets._format_code）。
_SYNTAX = {
    # SQL：'' 是字符串，"" 与 `` 是标识符，-- 与 /* */ 是注释
    "sql": {"line": ["--"], "block": [("/*", "*/")], "quotes": ["'", '"', "`"]},
    # JSON：只有 " 字符串
    "json": {"line": [], "block": [], "quotes": ['"']},
}

# 中文标点 → 英文（两种语言共用，见文件头第 2 条）
_MAP = {
    "\uff0c": ",",      # ，
    "\u201c": '"',      # “
    "\u201d": '"',      # ”
    "\u2018": "'",      # ‘
    "\u2019": "'",      # ’
}

# 各语言额外认的标点。冒号只给 JSON：`{"a"：1}` 是纯笔误，而 SQL 的 `:` 是语法
_MAP_BY_LANG = {
    "json": {"\uff1a": ":"},    # ：
    "sql": {},
}

# 中文引号的配对（开 → 闭）：成对出现时整段当一个字符串保护起来，只换两个定界符
_PAIRS = {"\u201c": "\u201d", "\u2018": "\u2019"}


def _scan_quoted(code, i, quote):
    """返回从 i 处的引号开始、整个字符串结束后的下标（未闭合则到文本末尾）。"""
    j, n = i + 1, len(code)
    while j < n:
        c = code[j]
        if c == "\\" and j + 1 < n:       # 反斜杠转义：连同下一个字符一起跳过
            j += 2
            continue
        if c == quote:
            if j + 1 < n and code[j + 1] == quote:   # SQL 里 '' 表示一个引号
                j += 2
                continue
            return j + 1
        j += 1
    return n


def _opaque_end(code, i, syn):
    """若 i 处开始的是注释或字符串，返回它结束后的下标；否则返回 None。"""
    for lm in syn["line"]:                       # 行注释：吃到行尾
        if code.startswith(lm, i):
            j = code.find("\n", i)
            return len(code) if j < 0 else j
    for bs, be in syn["block"]:                  # 块注释
        if code.startswith(bs, i):
            j = code.find(be, i + len(bs))
            return len(code) if j < 0 else j + len(be)
    if code[i] in syn["quotes"]:                 # 字符串 / 标识符
        return _scan_quoted(code, i, code[i])
    return None


def normalize(code, lang):
    """把字符串 / 注释以外的中文逗号、引号（JSON 另含冒号）换成英文，其余原样保留。"""
    syn = _SYNTAX.get(lang or "")
    if not code or syn is None:
        return code
    extra = _MAP_BY_LANG.get(lang, {})
    out = []
    i, n = 0, len(code)
    while i < n:
        j = _opaque_end(code, i, syn)
        if j is not None:                        # 注释 / 字符串：整段照抄
            out.append(code[i:j])
            i = j
            continue
        c = code[i]
        close = _PAIRS.get(c)
        if close is not None:                    # 中文引号：成对就只换定界符
            k = code.find(close, i + 1)
            if k >= 0:
                out.append(_MAP[c] + code[i + 1:k] + _MAP[close])
                i = k + 1
                continue
        out.append(extra.get(c) or _MAP.get(c, c))   # 普通字符：命中映射才换
        i += 1
    return "".join(out)
