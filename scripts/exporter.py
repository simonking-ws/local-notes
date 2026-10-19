# -*- coding: utf-8 -*-
"""素笺 —— 导出层：把笔记正文还原成 纯文本 / Markdown，并给 PDF 排版提供行模型。

存储里的正文是逐字符的 run 列表（`["t", 字, [标签]]`），中间夹着两种块项：
`["code", 语言, 代码]`（嵌入式代码块）和 `["hr"]`（分割线）。顺序即版式，
所以这里做的第一件事是把这一串东西**拍平成一个有序块列表**：

    {"kind": "line", "runs": [[文字, {标签}], ...]}     一行正文
    {"kind": "code", "lang": "python", "code": "..."}   一整块代码
    {"kind": "hr"}                                      一条分割线

块列表之后各格式各取所需 —— 纯文本、Markdown 都只认字和标签，
PDF 那份（pdf_export.py）还要按标签挑字体，但行模型是同一种。

⚠️ 本模块**不碰 Tk、不碰 store**，纯函数，可直接单测。
"""
import re

# 列表标记：编辑器里是**字面字符**（两格缩进 + 符号 + NBSP 间隙），
# 导出时一律换成各格式自己的标记。常量从 md_conv 借，那边是唯一出处。
from md_conv import LI_INDENT, split_list_prefix, TODO_ON, TODO_LEGACY

_TODO_ON_MARKS = (TODO_ON,) + tuple(k for k, v in TODO_LEGACY.items() if v == TODO_ON)

# 文件名里不能出现的字符（Windows 硬规则），外加首尾的点与空格
_BAD_NAME = re.compile(r'[\\/:*?"<>|\r\n\t]+')
_DUP_DASH = re.compile(r"-{2,}")

# Markdown 行内需要转义的字符。只转那几个真会改语义的：反斜杠自己、
# 强调符、代码符、方括号（别让正文里的 [x] 被读成链接/任务项）。
# `#` `-` `>` 只在行首有意义，而它们在编辑器里早就被转成结构了，这里不处理。
_MD_ESCAPE = re.compile(r"([\\*_`\[\]])")

# 行级标签 → 该行的块类型
_LINE_TAGS = (("title", "title"), ("h2", "h2"), ("quote", "quote"))


def blocks_from_content(content):
    """把存储层的 run 列表拍平成块列表（见模块说明）。

    代码块 / 分割线在 Tk 里是**行内嵌入窗口**，插入时前后各有一个换行，
    所以块后面紧跟的那个换行是「这一行到此结束」，必须吞掉 —— 不吞就会在
    每个代码块后面多出一个空行。
    """
    blocks = []
    runs = []                 # 当前行攒的文字
    swallow_nl = False        # 刚吐出块：下一个换行是它的行尾

    def flush():
        if runs:
            blocks.append({"kind": "line", "runs": list(runs)})
            del runs[:]

    for it in content or ():
        if not isinstance(it, (list, tuple)) or not it:
            continue
        kind = it[0]
        if kind == "t":
            text = it[1] if len(it) > 1 else ""
            tags = frozenset(it[2] or ()) if len(it) > 2 else frozenset()
            for ch in text:
                if ch == "\n":
                    if swallow_nl:
                        swallow_nl = False      # 块占的那一行的换行
                        continue
                    if runs:
                        flush()                 # 这一行到此结束
                    else:
                        blocks.append({"kind": "line", "runs": []})   # 本来就是空行
                    continue
                if runs and runs[-1][1] == tags:
                    runs[-1][0] += ch
                else:
                    runs.append([ch, tags])
        elif kind == "code":
            flush()
            blocks.append({"kind": "code",
                           "lang": (it[1] if len(it) > 1 else "") or "",
                           "code": (it[2] if len(it) > 2 else "") or ""})
            swallow_nl = True
        elif kind == "hr":
            flush()
            blocks.append({"kind": "hr"})
            swallow_nl = True
        elif kind == "w":
            # 历史数据里的待办控件：早已在渲染时变成 ⬜ / ✅ 字符，content 里
            # 可能还留着条目，忽略即可（正文里本来就没有它）。
            continue
    flush()
    # 尾部多余的空行（Tk 的 Text 末尾恒有一个换行）不算内容
    while blocks and blocks[-1]["kind"] == "line" and not blocks[-1]["runs"]:
        blocks.pop()
    return blocks


def line_text(block):
    return "".join(r[0] for r in block["runs"])


def line_tags(block):
    """整行吃到的行级标签（title / h2 / quote），没有则空集。"""
    got = set()
    for _t, tags in block["runs"]:
        got |= (tags or set())
    return {name for tag, name in _LINE_TAGS if tag in got}


def _split_marker(block):
    """拆出列表/待办前缀，返回 (kind, 正文起始下标, 缩进层数)。

    用的是编辑器那一份 `split_list_prefix`：它认得 ● / ➤ / 1. / ⬜✅ 以及各种
    老字形，还知道这几个标记后面跟的是 NBSP 而不是空格。
    """
    if block["kind"] != "line" or not block["runs"]:
        return (None, 0, 0)
    kind, prefix = split_list_prefix(line_text(block))
    if not kind:
        return (None, 0, 0)
    lead = 0
    for ch in prefix:
        if ch in ("\u3000", " "):
            lead += 1
        else:
            break
    # 编辑器里**每**一层列表都带两个全角空格的缩进，顶层也有 —— 所以减掉一格
    # 才是「嵌套层数」，顶层（只带一格缩进）落在 0。
    level = max(0, lead // max(1, len(LI_INDENT)) - 1)
    return (kind, len(prefix), level)


def _slice_runs(runs, start):
    """把一行 run 从第 start 个字符之后切出来（用于剔掉列表标记）。"""
    out, pos = [], 0
    for text, tags in runs:
        nxt = pos + len(text)
        if nxt <= start:
            pos = nxt
            continue
        cut = text[max(0, start - pos):]
        if cut:
            out.append([cut, tags])
        pos = nxt
    return out


def _check_state(runs):
    """待办行是勾着的吗。

    只扫前几个字符：勾选框永远在行首（缩进 + 方框），而正文里也可能出现 ✅，
    整行找会把「正文提到 ✅ 的普通行」误判成已勾。
    """
    head = "".join(r[0] for r in runs)[:8]
    return any(mark in head for mark in _TODO_ON_MARKS)


def safe_filename(title, ext):
    """按笔记标题拼一个安全文件名（Windows 不允许的那几个字符一律抹掉）。"""
    name = _BAD_NAME.sub(" ", (title or "").strip()).strip(" .")
    name = _DUP_DASH.sub("-", name)
    if not name:
        name = "未命名笔记"
    if len(name) > 80:                     # 加上目录后别超 MAX_PATH
        name = name[:80].rstrip(" .")
    return "%s.%s" % (name, ext.lstrip("."))


# ---------- 纯文本 ----------

def plain_text(blocks):
    """纯文本：只留字，列表标记换成最普通的写法，不带任何行内语法。

    刻意**不**保留 ● / ➤ / ⬜ 这些字形：它们是为编辑器里的等宽对齐挑的，
    到了 txt 里既占宽又挑字体；换成 `- `、`[x] ` 到处都读得下去。
    """
    out = []
    for b in blocks:
        if b["kind"] == "code":
            out.extend(b["code"].split("\n"))
            continue
        if b["kind"] == "hr":
            out.append("-" * 24)
            continue
        mi = marker_info(b)
        kind, cut, level = mi["kind"], mi["cut"], mi["level"]
        body_runs = _slice_runs(b["runs"], cut) if cut else b["runs"]
        body = "".join(r[0] for r in body_runs)
        tags = line_tags(b)
        if kind == "check":
            mark = "[x] " if mi["checked"] else "[ ] "
        elif kind == "numbered":
            mark = mi["text"] + " "
        elif kind in ("bullet", "triangle"):
            mark = "- "
        else:
            mark = ""
        text = ("  " * level) + mark + body
        if "quote" in tags and text.strip():
            text = ("  " * level) + "  " + text.lstrip()
        out.append(text)
    return "\n".join(out) + "\n"


# ---------- Markdown ----------

def _escape_md(s):
    return _MD_ESCAPE.sub(r"\\\1", s)


def _inline(runs, links, in_quote=False):
    """把一行的 run 拼成带 Markdown 行内标记的字符串。"""
    out = []
    for text, tags in runs:
        tags = tags or frozenset()
        if not text:
            continue
        if "icode" in tags:
            # 行内代码：整段包反引号，里面的符号**不转义**（转义符在代码里是字面量）
            body = "`%s`" % text
        else:
            body = _escape_md(text)
            link_tag = next((t for t in tags if str(t).startswith("lk_")), None)
            if link_tag and links.get(link_tag):
                body = "[%s](%s)" % (body, links[link_tag])
            if "underline" in tags:
                body = "<u>%s</u>" % body
            if "strike" in tags:
                body = "~~%s~~" % body
        if "italic" in tags and "bold" in tags:
            body = "***%s***" % body
        elif "bold" in tags:
            body = "**%s**" % body
        elif "italic" in tags:
            body = "*%s*" % body
        out.append(body)
    return "".join(out)


def markdown_text(blocks, links=None):
    """Markdown：标题 / 列表 / 待办 / 引用 / 代码块 / 分割线 + 行内强调。

    行与行之间打**硬换行**（行尾两个空格）：编辑器是「一行就是一行」，
    不打的话渲染器会把相邻几行并成一段，笔记里的分行就白分了。
    """
    links = links or {}
    out = []
    prev_list = False          # 上一行是不是列表项（含待办）
    for b in blocks:
        if b["kind"] == "code":
            fence = "```"
            out.append("%s%s" % (fence, b["lang"]))
            out.extend(b["code"].split("\n") if b["code"] else [""])
            out.append(fence)
            prev_list = False
            continue
        if b["kind"] == "hr":
            out.append("---")
            prev_list = False
            continue
        mi = marker_info(b)
        kind, cut, level = mi["kind"], mi["cut"], mi["level"]
        body_runs = _slice_runs(b["runs"], cut) if cut else b["runs"]
        body = _inline(body_runs, links)
        tags = line_tags(b)
        indent = "  " * level
        is_list = kind in ("check", "numbered", "bullet", "triangle")
        # 列表与其它块之间必须空一行：Markdown 的「懒惰续行」会把紧跟列表的
        # 普通段落接成列表项的一部分，说明文字就跑到项目符号底下去了。
        if is_list != prev_list and out and out[-1] != "":
            out.append("")
        prev_list = is_list
        if kind == "check":
            line = indent + ("- [x] " if mi["checked"] else "- [ ] ") + body
        elif kind == "numbered":
            line = indent + mi["text"] + " " + body
        elif kind in ("bullet", "triangle"):
            line = indent + "- " + body
        elif "title" in tags:
            line = "# " + body
        elif "h2" in tags:
            line = "## " + body
        elif "quote" in tags:
            line = ("> " if body.strip() else ">") + body
        else:
            line = body
        # 硬换行：普通段落行与引用行行尾补两个空格。标题与列表项各自成块，
        # 补了反而会在渲染器里多出空行。
        if line.strip() and kind is None and body.strip() \
                and "title" not in tags and "h2" not in tags:
            line = line + "  "
        out.append(line)
    return "\n".join(out) + "\n"


def marker_info(block):
    """列表/待办行的标记信息（三种导出格式共用一份判定）。

    返回 {"kind", "cut", "level", "text", "checked"}：
      kind     bullet / triangle / numbered / check / None
      cut      标记在行内占掉的字符数（正文从这里开始）
      level    嵌套层数（顶层 0）
      text     numbered 的序号原文（"1."），其它为空
      checked  待办是否已勾
    """
    kind, cut, level = _split_marker(block)
    text = ""
    if kind == "numbered":
        prefix = line_text(block)[:cut].lstrip("\u3000 ")
        stop = 0
        while stop < len(prefix) and prefix[stop] not in " \u00a0":
            stop += 1
        text = prefix[:stop].strip() or "1."
    return {"kind": kind, "cut": cut, "level": level, "text": text,
            "checked": _check_state(block["runs"]) if kind == "check" else False}
