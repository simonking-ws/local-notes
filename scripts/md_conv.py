"""markdown 前缀识别（纯函数，不依赖 tkinter）。

为什么单独成模块：输入快捷（敲 `## ` 变标题）和将来的导入导出要用同一套判断，
而这里是整个项目唯一「不建 GUI 就能断言」的文本逻辑——`tests/` 里可以直接 import。
放别的地方就只能靠人工点界面验证。

支持的 markdown 是**有限子集**，只认块级前缀：
    标题 # / ##    引用 >    列表 - * +    有序 1.    代码围栏 ```    分割线 ---
    待办 -[]       三角列表 ->   （都是「前缀 + 空格」触发，与 `- ` 同一套节奏）
    富链接 [文字](url) —— 敲完空格/回车时把 url 收起来，只留蓝色带下划线的文字
不做：表格、图片、H3 以上、脚注、内嵌 HTML —— 它们的排版成本远超收益。
行内标记（**粗** / *斜* / `代码` / ~~删~~）不走这里，由编辑器自己的按键逻辑处理。
"""

import re

# 有序列表：只认「数字 + 一个空格」，后面再跟空格说明已经标准化过了，
# 不认是为了防止「转成 1.  (两空格) 后再敲空格又转一次」的死循环。
_NUM_RE = re.compile(r"^(\d{1,9})\. (?! )")

# 标题：最多两个 #，且第三个字符起不能还是 #（否则 ### 会被当成 ## 误转）
_HASH_RE = re.compile(r"^(#{1,2})(?!#)\s")

# 代码围栏：整行只有 ``` 和可选的语言名
_FENCE_RE = re.compile(r"^```\s*([A-Za-z0-9+#._-]*)\s*$")

# 分割线：三个及以上的 - / * / _（中间可以有空格），整行只有它
_HR_RE = re.compile(r"^\s{0,3}(?:[-*_]\s*){3,}$")

# 裸网址：不把中文和全角标点算进 URL（中文标点紧跟网址时是最常见的误伤）
URL_RE = re.compile(r"https?://[^\s<>\"'`\u3000-\u9fff\uff00-\uffef]+")

# 网址末尾常见的英文标点，属于句子而不属于链接
_URL_TAIL = ".,;:!?)]}'\""


# ---------- 列表标记的形态 ----------
# 列表缩进：列表项相对正文右移两个汉字宽。用两个「全角空格」（U+3000）而不是
# 半角：正文字体是中文，全角空格宽度恰好等于一个汉字，两个就是视觉上的
# 「缩进两格」；半角要四个才够，且在不同字体下宽度不稳。
# 缩进是**正文的一部分**，不是 tag 的 lmargin —— 列表标记本身就是字面字符，
# 缩进跟着标记一起走：用户编辑、回车续接、整行删除都不会出现「tag 残留
# 导致的缩进漂移」（粘贴多行列表时尤其明显）。
LI_INDENT = "\u3000\u3000"

# 标记本体（不含缩进）。旧版插的就是这几个，存量笔记里只有它们。
_MARK_NBSP = "\u00a0\u00a0"

# 标记本体（不含缩进）。尾部必须是**不换行空格 U+00A0**，不能是普通半角空格：
# Tk 的 word wrap 把半角空格当成换行断点，而它后面那串没有空格的汉字是一个
# 不可断的「词」。两者一碰就是实打实的 bug —— 列表文字一长，Tk 在标记尾部的
# 空格处断开，把整段正文丢到下一行，屏幕上成了「符号独占第一行、话从第二行
# 才开始」。NBSP 的 Unicode 换行属性是 NB（禁止在其后断行），标记和它领的那
# 句话由此焊成同一个词，越界时 Tk 按字符断，第一行自然排到满。
# 用 NBSP 而不是全角空格是为了**等长替换**：老笔记里的标记字符数完全相同，
# 换算之后下标、标签范围、库里逐字符存的 content 一概不动。
# 项目符号的字形。12pt 正文下 ● 的墨迹是 8x7，• 只有 5x4（比汉字小一大截，
# 看着像句号）；换字形是等长替换，不影响下标与库里逐字符存的 content。
BULLET_GLYPH = "\u25cf"                # ● 实心圆点
BULLET_GLYPH_OLD = "\u2022"            # • 旧版小圆点（存量笔记里还有）

_M_BULLET = BULLET_GLYPH + _MARK_NBSP  # "●␣␣"
_M_TRI = "\u27a4" + _MARK_NBSP         # "➤␣␣"（Office 项目符号风格）

BULLET_MARK = LI_INDENT + _M_BULLET
NUM_MARK = LI_INDENT + "1." + _MARK_NBSP
TRI_MARK = LI_INDENT + _M_TRI

# 符号 -> 列表种类。「•」和「●」是两代项目符号（都认），「▸」「➤」同理
_MARK_KIND = {BULLET_GLYPH: "bullet", BULLET_GLYPH_OLD: "bullet",
              "\u27a4": "triangle", "\u25b8": "triangle"}
# 旧字形 -> 现行字形的归一表（一换一、等长）。只放**已被取代**的：
# • (U+2022) 在 12pt 正文里墨迹只有 5x4，比汉字小一大截，看着像句号；
# ● (U+25CF) 是 8x7，才是正常的项目符号大小。存量笔记载入时由
# app_actions._upgrade_list_marks 按「prefix 与原文不一致」自动换掉。
# 现行字形绝不能进这张表 —— 自环会让下一轮换字形互相覆盖（TODO_LEGACY 同理）。
_MARK_ALIAS = {BULLET_GLYPH_OLD: BULLET_GLYPH}
# 标记尾部允许出现的空白（半角空格是老笔记的形态）
_MARK_SPACE = " \u3000\u00a0"


def _unstick(s):
    """把一段尾巴里的**半角空格**换成等宽的 NBSP，其余字符照抄。

    只换半角：U+3000 的换行属性是 ID，本身就不会断行，换成 NBSP 反而会让它
    变窄一个字。半角空格换成 NBSP 宽度一模一样，用户看不出改动。
    """
    return s.replace(" ", "\u00a0")


# ---------- 待办勾选框 ----------
# 勾选框是**字面字符**，不是自绘控件。以前用嵌入窗口（widgets.TodoBox），
# 但 Tk 把嵌入式窗口当成一个**必然的换行点**：待办文字一长，Tk 就在框后面断
# 一次，整段文字掉到第二行 —— 屏幕上只剩一个孤零零的框。实测过在框后接
# NBSP / 全角空格 / WORD JOINER / ZWJ，Tk 8.6 一律照断，没得商量。
# 字符是文字流的一部分，和后面的汉字同属一个不可断的词，长行照常排满。
# 顺带白得三件事：能选中复制、能进撤销栈、能随导出一起走。
#
# 未勾用 U+2B1C「⬜ 白色大方块」、已勾用 U+2705「✅ 打钩按钮」。
#
# **两个必须是同一族、同一字号、同一字宽的一对**。上一版未勾用 ▢（U+25A2，
# Segoe UI Symbol 15pt）、已勾用 ✅（Segoe UI Emoji 9pt），各自按墨迹高度配的
# 字号 —— 结果打钩瞬间图标往下掉 3px、后面正文左移 1px：两个不同字体的基线算法
# 和字宽曲线根本不会自动对齐。换成 ⬜ 后两者同属 Emoji 族，实测：
#   advance 17→18 全等、墨迹 16×15 对 16×15、中心同为 −5.0，
#   打钩前后方框位置 / 正文起点 / 下一行位置三项位移都是 0。
# 顺带 ✅ 的圆角绿块仍是字形自己画的，不必拿 tag 的 background 去糊（那玩意儿
# 按字符框铺，勾选框会变成一根竖条）。
# 代价（已知并接受）：⬜ 是 emoji 粗边方框，比 ▢ 那种细描边粗一档；而 ✅ 是实心块，
# 两者的着墨量仍差一倍 —— 那是"勾上了"的语义本身（同系统复选框），调字号消不掉。
TODO_OFF = "\u2b1c"            # ⬜ 未勾（粗边圆角方框）
TODO_ON = "\u2705"             # ✅ 已勾（圆角实心块 + 白勾）
# 存量笔记里的旧字形（一换一，等长）。☐ / ☑ 是第一代直角方框，▢ 是上一版的细方框，
# ⚪ 是更早的空心圆；⚠️ ⬜ 和 ✅ 自己是现行形态，**不能**再出现在这张表里
# （自环会让下一轮换字形时互相覆盖）。
TODO_LEGACY = {"\u2610": TODO_OFF, "\u26aa": TODO_OFF, "\u25a2": TODO_OFF,
               "\u2611": TODO_ON}
TODO_MARKS = (TODO_OFF, TODO_ON) + tuple(TODO_LEGACY)

# 方框后面那个间隙：**与项目符号 / 三角 / 序号同宽同字体**（两个正文宽的不换行空格）。
# 以前它是图标字体下的一个 NBSP（17px），比其它的 12px 宽出一截 —— 四种列表
# 并排时「标记 → 正文」的距离各不一样。间隙必须用 NBSP 而不是半角空格：
# 半角空格是 Tk 的换行断点，待办文字一长就掉到第二行（理由同 _MARK_NBSP）。
TODO_GAP = _MARK_NBSP

# 方框后面那个间隙的标签：只借字号撑行高，不带颜色（勾选态的绿底不该铺到间隙上）。
TODO_GAP_TAG = "todo_gap"


def split_list_prefix(txt):
    """拆出整行开头的列表前缀，返回 (kind, prefix)。

    prefix **含该行原有的缩进**，调用方原样拼回去即可保持缩进一致。
    长度恒等于正文里被它占掉的那一段 —— 拿它去定位「正文首字」是安全的，
    这也是标记里的空格必须用 NBSP（等宽替换）而不是全角空格的原因。

    `kind` 为 "bullet" / "triangle" / "numbered" / "check"（待办），
    不是列表行时为 (None, "")。
    老笔记里的标记尾部是半角空格，这里照样认，并且**一律返回现行形态**：
    用户在旧行上回车续接出来的新行自此带着新标记（老笔记唯一的迁移途径）。
    """
    if not txt:
        return (None, "")
    i = 0
    while i < len(txt) and txt[i] in ("\u3000", " "):
        i += 1
    lead, body = txt[:i], txt[i:]
    if body[:1] in TODO_MARKS:
        kind = "check"                    # 待办：框占一个字符，后面跟着标记间隙
        i = 1
    else:
        kind = _MARK_KIND.get(body[:1])
        if kind is None:
            m = re.match(r"^\d+\.", body)
            if m is None:
                return (None, "")
            i = m.end()
            kind = "numbered"
        else:
            i = 1
    j = i
    while j < len(body) and body[j] in _MARK_SPACE:
        j += 1
    # 符号后面没跟空格（"•没有空格"）：它是正文里的一个字符，不是列表标记。
    # 只有标记没有文字同理 —— 那行还不成列表，等用户敲了字再说。
    # 待办例外：空待办行也要算（「只留一个方框」正是回车结束列表的那个状态）。
    if kind == "check":
        # 旧字形归一成现行字形（☐ / ⚪ / ▢ → ⬜，☑ → ✅）：**一换一，长度不变**，
        # 调用方一律靠 len(prefix) 定位「正文首字」，下标、标签范围一概不动。
        pre = _unstick(body[:j])
        pre = TODO_LEGACY.get(pre[:1], pre[:1]) + pre[1:]
        return (kind, lead + pre)
    if j == i or j >= len(body):
        return (None, "")
    pre = _unstick(body[:j])
    # 项目符号的老字形（•）在出口统一成现行字形（●）：调用方拿 prefix 与原文
    # 比较，不一致就会把那一格重写成现行形态（_upgrade_list_marks）。一换一、
    # 等长，正文长度和下标一概不动。
    if kind == "bullet":
        pre = _MARK_ALIAS.get(pre[:1], pre[:1]) + pre[1:]
    return (kind, lead + pre)


def detect_prefix(line):
    """识别一行开头的 markdown 块级前缀。

    返回 (kind, consume, extra)：
        kind    : "title" / "h2" / "quote" / "numbered" / "bullet" / "triangle"
                  / "check" / "fence" / "hr" / None
        consume : 需要从行首删掉的字符数（hr / fence 是整行长度）
        extra   : numbered 的起始序号；fence 的语言名；其余为 None

    已经转过的列表标记直接返回 None —— 编辑器里存的是渲染后的符号，
    不认它的话「敲 - 转成 • 之后再敲空格」会重复处理。这些标记现在带着
    两格缩进，所以判断前要先剥掉行首缩进。
    """
    if not line:
        return (None, 0, None)
    # 已经是编辑器形态的列表就不再处理 —— 「敲 - 转成 • 之后再敲空格」会
    # 重复转换。这些标记带着两格缩进，判断交给 split_list_prefix。
    # 序号要留给下面 _NUM_RE 的「只认一个半角空格」去分：markdown 的 "1. "
    # 和已转换的 "1.  " / "1.\u00a0\u00a0" 差别只在尾空格，split_list_prefix
    # 为了给续接用，两种都认，区分不了。
    if split_list_prefix(line)[0] in ("bullet", "triangle", "check"):
        return (None, 0, None)
    m = _FENCE_RE.match(line)
    if m:
        return ("fence", len(line), m.group(1) or "")
    m = _HASH_RE.match(line)
    if m:
        n = len(m.group(1))
        return ("title" if n == 1 else "h2", n + 1, None)
    if line.startswith("> "):
        return ("quote", 2, None)
    if is_hr(line):
        return ("hr", len(line), None)
    m = _NUM_RE.match(line)
    if m:
        return ("numbered", len(m.group(0)), int(m.group(1)))
    # 待办：`-[]` + 空格。**只认紧凑写法** —— `- [ ]` 敲到 `- ` 时已经被
    # 项目符号吃掉了（`- ` 在本函数更前面就转成 ●），那三个字符根本留不到
    # 空格这一刻。空框、框里打勾的各种写法都按未勾处理：勾选态是点出来的。
    # 与 `- ` 同一套规则：前缀后面必须紧跟空格，`-[]项目` 只是正文。
    if line[:4] == "-[] ":
        return ("check", 4, None)
    # 三角列表：`->` + 空格。放在 `- ` 之前判断，免得哪天加宽匹配条件被抢。
    if line[:3] == "-> ":
        return ("triangle", 3, None)
    if line[:2] in ("- ", "* ", "+ "):
        return ("bullet", 2, None)
    return (None, 0, None)


def parse_fence(line):
    """整行是代码围栏（``` 或 ```python）时返回语言名，否则 None。

    返回空字符串表示「围栏没写语言」，所以判断必须用 `is not None`——
    ``` 本身就是一个合法的围栏，真值判断会把它当成非围栏。
    """
    m = _FENCE_RE.match((line or "").strip())
    return None if m is None else (m.group(1) or "")


def is_hr(line):
    """整行是否是一条分割线（--- / *** / ___）。"""
    return bool(_HR_RE.match(line)) and set(line.strip()) <= set("-*_ ")


# ---------- 富链接 / 时间函数 ----------

# markdown 富链接：[文字](https://example.com)。括号里不允许空白与嵌套括号——
# 编辑器只处理「一次敲完的链接」，URL 里带空格的情况本来就不是合法 URL。
MD_LINK_RE = re.compile(r"\[([^\[\]\n]+)\]\(([^()\s]+)\)")

# 时间函数：date() / datetime()。必须顶在行尾或空格之后（前面不能是字母数字），
# 否则 "update()" 这类函数名会被误展开。
_TIME_CALL_RE = re.compile(r"(?<![0-9A-Za-z_])(datetime\(\)|date\(\))$")


def find_time_call(prefix):
    """prefix 的末尾若是 `date()` / `datetime()`，返回 (名字, 起点下标)，否则 None。

    只认**末尾**是关键：这样"日期函数怎么写？date() 是一个例子"里已经写完的
    date() 不会被后续空格反复展开，只有刚敲完那一刻才替换。
    """
    m = _TIME_CALL_RE.search(prefix or "")
    if not m:
        return None
    return (m.group(1)[:-2], m.start(1))     # 去掉尾部的 "()"


def find_urls(text, offset=0):
    """找出文本里的裸网址，返回 [(start, end, url)]。

    末尾的英文标点会被剥掉（"见 https://a.com/x." 里的句号不属于链接）。
    offset 用于把行内下标换算成 Text 组件的绝对下标（字符偏移）。
    """
    out = []
    for m in URL_RE.finditer(text):
        url = m.group(0)
        e = m.end()
        while url and url[-1] in _URL_TAIL:
            url = url[:-1]
            e -= 1
        if len(url) > len("https://"):
            out.append((m.start() + offset, e + offset, url))
    return out
