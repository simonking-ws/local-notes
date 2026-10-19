# -*- coding: utf-8 -*-
"""`$` 公式自动计算：`$5+6=` 敲完空格 → 变成 `5+6= 11`（纯字符串，不依赖 tkinter）。

设计取舍（都是刻意的，别随手放宽）：

1. **必须由 `$` 起头**。正文里 `5+6=` 这种写法太常见（比如抄一段算式说明），
   没有起始符就去算它等于在替用户改正文。`$` 是个明确的「我要算」信号。
   **`$` 只是触发键，算完就删掉**（见 expand 的返回值）：留在正文里既丑，
   又会让复制出去的内容带上一个没有意义的符号。删掉后这行也不再是公式，
   再敲空格不会二次求值。
2. **`$` 后跟汉字或字母一律不算**。汉字直接让倒扫停下（根本找不到 `$`）；
   字母则要求以 `sin` / `cos` / `tan` 开头 —— 这条是为 markdown 里的
   `$…$` 数学写法留的后路，`$x^2=`、`$abc=` 都不会被动。
3. **只与「光标紧邻」**：确认的空格必须紧跟 `=`，`=` 后面又紧贴光标。
   离开这个位置就不管，用户改历史内容不会突然冒出数字。
4. **算不出来就什么都不做**：语法错、除零、`tan90`、结果量级过大，
   一律静默放弃 —— 不弹框、不留半截结果（与格式化层「拿不准就少做」同一立场）。
5. **结果是纯文本**，不挂 tag、不存元数据：导出、搜索、加密、序列化全部零改动。

支持的运算：`+ - * /`、括号、`^` 幂、`%` 取模、`sin/cos/tan`（参数按**度**，
可以写成 `tan30` 或 `tan(30)`）。结果最多两位小数（四舍五入，去尾零）。
"""

import ast
import math
import re
from decimal import Decimal, ROUND_HALF_UP, localcontext

__all__ = ["expand", "evaluate", "format_number", "MARK"]

MARK = "$"                  # 公式起始符
_EQUALS = "="               # 公式结束符（后面那个空格是触发键）
MAX_LEN = 80                # 表达式长度上限：再长就不像「顺手算一下」了
MAX_ADJ = 20                # 结果量级上限（|x| < 1e20），防 2^999 把界面拖死
FUNCS = ("sin", "cos", "tan")   # 三角函数，参数按度

# 倒扫时认的字符。字母只可能出现在函数名里（`tan30`），别的字母进来也会被
# AST 判非法；全角符号在这里就认，是为了 `$5＋6=` 也能倒扫回 `$`（归一化在
# _prepare 里做，扫不到 `$` 就什么都谈不上）。
_SCAN_OK = set(
    "0123456789.+-*/()^% "          # 半角
    "abcdefghijklmnopqrstuvwxyz"    # 函数名
    "\u3000×÷－＋（）＾％"            # 全角：全角空格 / × / ÷ / － / ＋ / （ ） / ＾ / ％
)

# 全角 → 半角（只这一批，够用且不会误伤）
_TRANS = {
    "\u3000": " ", "×": "*", "÷": "/", "－": "-",
    "＋": "+", "（": "(", "）": ")", "＾": "^", "％": "%",
}

# `tan30` / `tan 30` → `tan(30)`；`tan(30)` 原样（后面不是数字就不匹配）
_IMPLICIT_CALL = re.compile(r"\b(sin|cos|tan)\s*([0-9]+(?:\.[0-9]+)?)", re.I)

# 表达式开头的字母串：用来判「`$` 后面跟的是不是函数名」
_LEAD_NAME = re.compile(r"^\s*([A-Za-z]+)")

# 至少要有一个运算符或函数，否则 `$2026=` 也会被「算」一遍，没意义
_HAS_OP = re.compile(r"[+\-*/^%()]|\b(?:sin|cos|tan)", re.I)


class _Bad(Exception):
    """这个表达式不打算算（语法不认识 / 越界 / 无定义）。"""


# 字面数字的节点类型：3.7 给 `Num`，3.8+ 才并成 `Constant`。
# 本机两个解释器（Anaconda 3.7 / 3.10）都得跑，所以两种都留着。
_NUM_LEGACY = getattr(ast, "Num", None)
_NUM_TYPES = tuple(t for t in (_NUM_LEGACY, ast.Constant) if t is not None)


def _norm(s):
    """全角 → 半角（只这一批，够用且不会误伤）。"""
    for k, v in _TRANS.items():
        s = s.replace(k, v)
    return s


def _prepare(expr):
    """归一化 → 补全函数括号 → `^` 换成 Python 的 `**`。"""
    s = _norm(expr)
    s = _IMPLICIT_CALL.sub(lambda m: "%s(%s)" % (m.group(1).lower(), m.group(2)), s)
    return s.replace("^", "**")


def _trig(name, deg):
    """按度求三角函数；Tk 笔记本里写 `tan90` 的场景基本是笔误，直接不算。"""
    d = float(deg)
    if name == "tan" and abs((d - 90.0) % 180.0) < 1e-9:
        raise _Bad()                        # tan90 无定义（不是无穷大）
    f = {"sin": math.sin, "cos": math.cos, "tan": math.tan}[name]
    return Decimal(repr(f(math.radians(d))))


def _const_value(node):
    """取字面数字的值：Python 3.7 给 `Num`，3.8+ 才并成 `Constant`，两个都要认。"""
    if _NUM_LEGACY and isinstance(node, _NUM_LEGACY):  # 3.7 及更早（3.10 上也命中）
        return node.n
    if isinstance(node, ast.Constant):                  # 3.8+
        return node.value
    raise _Bad()


def _ev(node):
    """白名单遍历 AST —— 绝不 eval。只认字面数字、四则、幂、取模、一元正负、三角函数。"""
    if isinstance(node, _NUM_TYPES):
        v = _const_value(node)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise _Bad()
        return Decimal(str(v))
    if isinstance(node, ast.UnaryOp):
        x = _ev(node.operand)
        if isinstance(node.op, ast.UAdd):
            return x
        if isinstance(node.op, ast.USub):
            return -x
        raise _Bad()
    if isinstance(node, ast.BinOp):
        a, b = _ev(node.left), _ev(node.right)
        op = node.op
        if isinstance(op, ast.Add):
            return a + b
        if isinstance(op, ast.Sub):
            return a - b
        if isinstance(op, ast.Mult):
            return a * b
        if isinstance(op, ast.Div):
            return a / b                     # 除以 0 由 Decimal 抛 DivisionByZero
        if isinstance(op, ast.Mod):
            return a % b
        if isinstance(op, ast.Pow):
            if abs(b) > 1000:                # 2^999999：算得出来也放不下，别算
                raise _Bad()
            return a ** b
        raise _Bad()
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise _Bad()
        name = node.func.id.lower()
        if name not in FUNCS or len(node.args) != 1 or node.keywords:
            raise _Bad()
        return _trig(name, _ev(node.args[0]))
    raise _Bad()


def evaluate(expr):
    """算一个表达式，返回 Decimal；算不了返回 None（调用方据此什么都不做）。"""
    try:
        tree = ast.parse(_prepare(expr), mode="eval")
        with localcontext() as ctx:
            ctx.prec = 34                    # 三角函数与幂都要留够中间精度
            val = _ev(tree.body)
        if val != val:                       # NaN
            return None
        if val != 0 and val.adjusted() > MAX_ADJ:
            return None
        if val != 0 and val.adjusted() < -MAX_ADJ:
            return None
    except Exception:                        # noqa: BLE001 语法错 / 除零 / 溢出 / 无定义
        return None
    return val


def format_number(val):
    """最多两位小数，四舍五入并去掉尾零：3 / 2.5 / 0.58。"""
    q = val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    s = format(q, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("", "-", "-0") else s


def _looks_like_formula(expr):
    """`$` 后面到底是不是一条公式。先归一化 —— 全角 `＋` `×` 也是运算符。"""
    s = _norm(expr)
    m = _LEAD_NAME.match(s)
    if m and m.group(1).lower() not in FUNCS:
        return False                         # $abc= / $x^2= —— 当普通文字，不算
    return bool(_HAS_OP.search(s))


def expand(line, pos):
    """在 `pos`（刚敲下的那个确认空格的列）处尝试展开 `$公式=`。

    返回 `(结果文本, 插入位置, $ 的列号)` 或 None。行内容与光标都不在预期
    位置时返回 None —— 这里只回答「该不该算、算出来是什么、`$` 要删哪一格」，
    改正文的事由调用方做（它负责把 `$` 删掉、把结果插进去）。
    """
    if not line or pos is None:
        return None
    if pos - 1 < 0 or pos >= len(line):
        return None
    if line[pos - 1] != _EQUALS or line[pos] != " ":
        return None                          # 光标前不是「= + 空格」，不管
    i = pos - 2
    while i >= 0 and line[i] in _SCAN_OK:
        i -= 1
    if i < 0 or line[i] != MARK:
        return None                          # 没有 $ 起头（或中间夹了汉字等）
    expr = line[i + 1:pos - 1].strip()
    if not expr or len(expr) > MAX_LEN:
        return None
    if not _looks_like_formula(expr):
        return None
    val = evaluate(expr)
    if val is None:
        return None
    # 返回的 `i` 是 `$` 的位置：调用方要把这一格删掉，删完后面的列号都要 -1
    return format_number(val), pos + 1, i
