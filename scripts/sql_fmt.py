"""SQL 关键词格式化：按子句关键词断行，只重排空白，一个字符都不改。

为什么不复用 widgets._fmt_reindent：那个只重算「行首缩进」，一行里塞了三个 AND
它一点办法都没有 —— 而真实场景里 SQL 多半是从日志、控制台、IDE 里复制出来的，
换行位置完全不由人，缩进根本不是问题所在。

这里的做法是「先拍平再断行」：

1. 词法切分，记住每个词元前面**有没有空白**（不记几个、也不记是空格还是换行）；
2. 把所有词元按原间距重新拼起来 —— 于是原文里那些莫名其妙的换行全被抹平，
   而 `o.\\`status\\`=1`（无空格）与 `b.org_id = o.id`（有空格）这种手写间距原样保留；
3. 只在**括号深度 0** 处断开：
   - `SELECT`（含 DISTINCT / ALL）之后另起一行；
   - 字段列表**一个字段一行**（深度 0 的逗号收行、逗号留在行尾），每行缩进四格；
   - `FROM` 自己另起一行，字段列表到此收尾；
   - `WHERE` / `AND` / `OR` / `HAVING` / `GROUP BY` / `ORDER BY` / `LIMIT` /
     `UNION` / `JOIN` 系列 / `SET` / `VALUES` … 之前另起一行。

括号内一律不断行 —— 子查询、IN 的值表、函数参数因此都能保持原本的紧凑样子
（`count(a, b)`、`GROUP BY o.id, o.name` 里的逗号都不会被拆开）。
缩进只发生在「SELECT 与 FROM 之间」这一段字段列表上：再往下的 AND 行保持顶格，
与之前的输出一致（用户只要求字段那一段退格）。
"""

import re

__all__ = ["format_sql"]

# 字段列表的缩进。四格足够把「字段」和「子句」分开，又不会把长字段挤到折行；
# 想换风格改这一个常量即可，重排逻辑不关心它有多宽。
FIELD_INDENT = "    "

# 这些词出现在行首位置（深度 0）时，先结束上一行
_BREAK_BEFORE = {
    "FROM",
    "WHERE", "AND", "OR", "HAVING", "LIMIT",
    "GROUP BY", "ORDER BY",
    "UNION", "UNION ALL", "EXCEPT", "INTERSECT",
    "JOIN", "LEFT JOIN", "RIGHT JOIN", "INNER JOIN", "OUTER JOIN",
    "CROSS JOIN", "FULL JOIN", "NATURAL JOIN",
    "SET", "VALUES",
    "INSERT", "INSERT INTO", "UPDATE", "DELETE", "DELETE FROM", "REPLACE",
}

# 两个词组成一个语义词：`LEFT JOIN` 要断在 LEFT 前，而不是 LEFT 与 JOIN 之间
_TWO_WORD = {
    "GROUP BY", "ORDER BY", "UNION ALL", "INSERT INTO", "DELETE FROM",
    "LEFT JOIN", "RIGHT JOIN", "INNER JOIN", "OUTER JOIN",
    "CROSS JOIN", "FULL JOIN", "NATURAL JOIN",
    # 行锁子句：拆开会让 UPDATE 掉到下一行，看着像开了条新语句
    "FOR UPDATE", "FOR SHARE",
}

_JOIN_HEAD = {"LEFT", "RIGHT", "INNER", "OUTER", "CROSS", "FULL", "NATURAL"}

# 多字符运算符要先于单字符判断，否则 `< =` 会被拆成两个
_OPERATORS = ("<=", ">=", "<>", "!=", "||", "::", ":=", "->>", "->")

_WORD_RE = re.compile(r"[A-Za-z_\u4e00-\u9fff][\w$\u4e00-\u9fff]*")
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def _tokenize(text):
    """切成 (kind, text, space_before)。kind ∈ word / str / ident / num / punct / comment。

    space_before 只回答「前面有没有空白」，这是关键：SQL 里 `a.b`、`a = b`、`IN (`
    的空格是写的人有意为之，重排时不能替他决定，只能记住然后原样还原。
    """
    toks = []
    i, n = 0, len(text)
    sp = False
    while i < n:
        c = text[i]
        if c in " \t\r\n\f\v":
            sp = True
            i += 1
            continue
        if text.startswith("--", i):                       # 行注释吃到行尾
            j = text.find("\n", i)
            j = n if j < 0 else j
            toks.append(("comment", text[i:j].rstrip(), sp))
            sp, i = True, j
            continue
        if text.startswith("/*", i):                       # 块注释
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            toks.append(("comment", text[i:j], sp))
            sp, i = True, j
            continue
        if c == "'":                                       # 字符串：'' 表示一个引号
            j = i + 1
            while j < n:
                if text[j] == "'":
                    if j + 1 < n and text[j + 1] == "'":
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            toks.append(("str", text[i:j], sp))
            sp, i = False, j
            continue
        if c in "`\"":                                     # 反引号 / 双引号标识符
            j = text.find(c, i + 1)
            j = n if j < 0 else j + 1
            toks.append(("ident", text[i:j], sp))
            sp, i = False, j
            continue
        if c.isdigit():
            m = _NUM_RE.match(text, i)
            toks.append(("num", m.group(0), sp))
            sp, i = False, m.end()
            continue
        m = _WORD_RE.match(text, i)
        if m:
            toks.append(("word", m.group(0), sp))
            sp, i = False, m.end()
            continue
        for op in _OPERATORS:
            if text.startswith(op, i):
                toks.append(("punct", op, sp))
                sp, i = False, i + len(op)
                break
        else:
            toks.append(("punct", c, sp))
            sp, i = False, i + 1
    return toks


def _upper(tok):
    return tok[1].upper() if tok[0] == "word" else ""


def _pair_up(toks):
    """把 `LEFT` + `JOIN`、`GROUP` + `BY` 这类固定搭配并成一个词元。"""
    out = []
    i, n = 0, len(toks)
    while i < n:
        a, b = toks[i], toks[i + 1] if i + 1 < n else None
        if b is not None and a[0] == b[0] == "word":
            combo = (_upper(a) + " " + _upper(b))
            if combo in _TWO_WORD or (_upper(a) in _JOIN_HEAD and _upper(b) == "JOIN"):
                out.append(("word", a[1] + " " + b[1], a[2]))
                i += 2
                continue
        out.append(a)
        i += 1
    return out


def _render(toks):
    """按原间距把一行的词元拼成字符串（行首不留空白）。"""
    buf = []
    for k, (kind, text, sp) in enumerate(toks):
        if k and sp:
            buf.append(" ")
        buf.append(text)
    return "".join(buf)


def format_sql(code):
    """返回按关键词断开后的 SQL；已经格式化的输入再跑一遍结果不变（幂等）。"""
    text = (code or "").replace("\r\n", "\n").replace("\r", "\n")
    toks = _pair_up(_tokenize(text))
    lines = []
    cur = []                       # 当前行的词元
    depth = 0
    between = 0                    # BETWEEN … AND 里的 AND 不是连接词，不能断
    fields = False                 # 眼下是不是在 SELECT 与 FROM 之间的字段列表里
    i, n = 0, len(toks)

    def flush():
        if cur:
            body = _render(cur)
            lines.append(FIELD_INDENT + body if fields else body)
            del cur[:]

    while i < n:
        tok = toks[i]
        kind, body, _sp = tok
        up = _upper(tok)

        if kind == "comment":
            # 行注释必须独占一行，否则它会把后面的语句一起"注释"掉；单行的块注释
            # 没这个风险，就让它跟在词后面（`o.id, /* 主键 */ o.name` 不至于被切碎）。
            # 字段段里逗号刚把行断掉，cur 是空的 —— 这类注释仍然贴到后一个字段上，
            # 否则「字段 / 注释 / 字段」会散成三行。
            if body.startswith("/*") and "\n" not in body and (cur or fields):
                cur.append(tok)
                i += 1
                continue
            flush()
            cur.append(tok)
            flush()
            i += 1
            continue

        if kind == "punct" and body == ")":
            depth = max(0, depth - 1)

        if depth == 0:
            if up == "BETWEEN":
                between += 1
            elif up == "AND" and between:
                between -= 1
            elif up in _BREAK_BEFORE:
                # 上一行（多半是 SELECT 的字段列表）先按当时的状态收掉，再翻篇：
                # 从这里开始是新子句，不再缩进
                if cur:
                    flush()
                fields = False

        # SELECT 前后都要断：前面是 `union all` / `insert into …` 时先把那行收掉，
        # 自己独占一行，字段列表（连同 `DISTINCT`）从下一行起 —— 样板如此。
        # 必须在通用 push 之前处理，否则 SELECT 会被塞进正在拼的那一行。
        if depth == 0 and up == "SELECT":
            flush()
            cur.append(tok)
            if i + 1 < n and _upper(toks[i + 1]) in ("DISTINCT", "ALL"):
                i += 1
                cur.append(toks[i])
            flush()                # SELECT 这行自己顶格（fields 此刻还是 False）
            fields = True          # 之后再成行的是字段列表，要缩进
            i += 1
            continue

        cur.append(tok)

        if kind == "punct":
            if body == "(":
                depth += 1
            elif body == "," and fields and depth == 0:
                # 字段列表一个字段一行：逗号留在行尾，下一个字段另起一行（继续缩进）。
                # 括号里的逗号不动 —— `count(a, b)`、`IN (1, 2, 3)`、函数参数都靠它保住
                flush()
            elif body == ";" and depth == 0:          # 多语句：分号收尾并空一行
                flush()
                fields = False
                if i + 1 < n:
                    lines.append("")
        i += 1

    flush()
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)
