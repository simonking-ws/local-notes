# -*- coding: utf-8 -*-
"""素笺 —— 数据层（从 notes_app.py 拆出）。

这一层**不 import tkinter**，只依赖标准库（外加同为零依赖的 md_conv：待办方框
用什么字符、间隙用什么标签，必须和编辑器共用同一份定义）。

内含：
  - 路径常量（BASE / DATA_FILE / DB_FILE）与应用名 APP_NAME
  - 内容序列化小工具 uid / _t / _cb / plain_of
  - 初始数据 seed / 旧数据改名 _fix_sample_note
  - SQLite 数据层 Store（含搜索、导入归一化与合并）
"""
import os
import sys
import json
import time
import sqlite3

from md_conv import TODO_OFF, TODO_ON, TODO_MARKS, TODO_GAP, TODO_GAP_TAG, LI_INDENT
import secret


def _app_dir():
    """程序所在目录：打包成 exe 后取 exe 所在目录，源码运行时取脚本目录（scripts/）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _data_dir():
    """数据目录：源码运行时是项目根的 data/，打包后是 exe 旁的 data/（数据仍随程序带得走）。"""
    if getattr(sys, "frozen", False):
        return os.path.join(_app_dir(), "data")
    return os.path.normpath(os.path.join(_app_dir(), os.pardir, "data"))


BASE = _data_dir()
# 旧版 JSON 数据文件（首次运行若发现它会自动迁移进 db，不会丢数据）
DATA_FILE = os.path.join(BASE, "notes_data.json")
# 现在的唯一数据源：SQLite 数据库
DB_FILE = os.path.join(BASE, "notes_data.db")


def _legacy_json():
    """目录改到 data/ 之前的旧位置（项目根）里若还留着 json，也认。"""
    return os.path.join(os.path.dirname(BASE), "notes_data.json")


def _resolve_db():
    """确保 data/ 存在；老版本把库放在程序目录的话就搬进来，别让人以为数据丢了。"""
    try:
        os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    except Exception:
        pass
    try:
        if os.path.exists(DB_FILE):
            return DB_FILE
        old = os.path.join(_app_dir(), "notes_data.db")
        if os.path.exists(old):
            os.replace(old, DB_FILE)
    except Exception:
        pass
    return DB_FILE

# 应用中文名（窗口标题 / 示例笔记 / 导出备份文件名统一取这里）
APP_NAME = "素笺"

# 加密笔记未解锁时，列表标题只露首字、后跟一个星号（如「周*」）——数据在
# secret.HINT_KEY 里随密文块走；UI 层与动作层共用这份定义。
SEAL_TITLE_MASK = "*"
# 未解锁时编辑区显示的占位文字（load_editor 渲染，serialize 有 _locked_view 守卫
# 保证它永远不会被当成正文写回数据库）。
LOCKED_BODY_TEXT = "文字已加密"

# 数据库被占用（多开、杀软扫描、云盘同步锁）时最多等这么久，超时就报错交给界面层提示。
# 不取 sqlite3 默认的 5 秒：那段时间界面线程是冻住的，用户只会以为程序卡死。
BUSY_TIMEOUT_S = 2.0

# 保存失败时写出的兜底快照前缀（与「导出备份」同格式，事后可用「导入数据」捞回）
PENDING_PREFIX = "pending_"


def uid():
    import random, string
    return "x" + "".join(random.choices(string.ascii_letters + string.digits, k=9))


def _t(s, tags=()):
    """把字符串拆成逐字符项，便于带格式序列化/还原。

    换行符自身不继承任何行级标签（title/h2/body）：若 title 的
    spacing1/spacing3 落在换行符上，会在标题与正文之间额外插出
    不可见空行，导致新笔记开头出现多余间距。普通标签（加粗/颜色/
    字体/下划线）仍按原样逐字符继承。
    """
    out = []
    for c in s:
        if c == "\n":
            out.append(["t", c, []])
        else:
            out.append(["t", c, list(tags)])
    return out


def _cb(checked, tags=()):
    """待办项：**字面字符**形态（⬜ / ✅ + 一个不换行空格）。

    以前是嵌入窗口（["w", checked, tags]）—— 见 md_conv.TODO_OFF 里为什么
    改成字符。字符形态下「勾没勾」由字符本身（⬜ / ✅）表达，样式标签
    （todo / todo_done）照旧随内容一起存：编辑器序列化出来的就是这个形态
    （app_actions.serialize 逐字符记标签），两边必须一样，否则存一次、
    重开一次，待办框的颜色和点击就丢了。

    间隙用的是 TODO_GAP（两个正文宽的不换行空格），与项目符号 / 三角 / 序号
    尾部同一个常量：四种列表「标记 → 正文」的距离才一致。间隙只带 todo_gap
    （借字号、不带颜色）：跟着方框一起打 todo 的话，勾选后的绿底会连空隙一起铺开。

    行首带两格缩进（LI_INDENT），与项目符号 / 三角 / 序号同一条竖线 ——
    缩进不带标签，只占位。
    """
    ch = TODO_ON if checked else TODO_OFF
    tag = "todo_done" if checked else "todo"
    return [["t", LI_INDENT, []], ["t", ch, list(tags) + [tag]],
            ["t", TODO_GAP, [TODO_GAP_TAG]]]


# 内置说明笔记里的快捷键表：(键, 说明)。顺序按「先文件、再格式、后结构、最后编辑」排，
# 与 README 的快捷键表保持一致——改一处就要同步另一处，否则文档和程序里的说明会打架。
SAMPLE_KEYS = [
    ("Ctrl+N", "新建笔记"),
    ("Ctrl+S", "立即保存"),
    ("Ctrl+,", "打开全局设置"),
    ("Ctrl+B", "加粗"),
    ("Ctrl+I", "斜体"),
    ("Ctrl+U", "下划线"),
    ("Ctrl+Shift+S", "删除线"),
    ("Ctrl+E", "行内代码"),
    ("Ctrl+1", "一级标题"),
    ("Ctrl+2", "二级标题"),
    ("Ctrl+0", "回到正文"),
    ("Ctrl+Shift+K", "插入代码块"),
    ("Ctrl+Shift+T", "插入待办"),
    ("Ctrl+Shift+-", "插入分割线"),
    ("Ctrl+K", "插入 / 编辑超链接"),
    ("Ctrl+D", "复制整行：光标所在行整行复制一份插到下面"),
    ("Ctrl+Y", "删除整行：光标所在行整行删掉"),
    ("Ctrl+Z", "撤销"),
    ("Ctrl+Shift+Z", "重做"),
    ("Ctrl+Tab", "下一个标签页（Ctrl+PageDown 同）"),
    ("Ctrl+Shift+Tab", "上一个标签页（Ctrl+PageUp 同）"),
    ("Ctrl+W", "关闭当前标签页"),
]

# markdown 输入快捷的说明表：键位说明 + 敲完之后的形态
SAMPLE_MD = [
    ("# + 空格", "一级标题"),
    ("## + 空格", "二级标题"),
    ("- + 空格", "项目符号（●），* 和 + 也可以，右移两格"),
    ("-> + 空格", "三角列表（➤），右移两格"),
    ("-[] + 空格", "待办勾选框，右移两格，接着写事情，点框即勾选"),
    ("1. + 空格", "有序列表，右移两格，回车自动递增"),
    ("> + 空格", "引用块：文字缩进两格，回车继续引用、空行再回车跳出"),
    ("``` + 空格", "代码块；后面跟语言名（```python）敲回车也行"),
    ("---", "回车后变成一条分割线"),
    ("[文字](网址)", "超链接：敲空格或回车后只剩文字，悬停可看地址并修改"),
    ("date()", "敲空格或回车 → 当前日期，如 2026-10-23"),
    ("datetime()", "敲空格或回车 → 当前时刻，如 2026-10-23 18:50:25"),
    ("$5+6=", "敲空格 → 出结果并去掉 $（5+6= 11）：四则、括号、^ 幂、% 取模、tan30；只认 $ 开头的"),
]


def _sample_md_section():
    """「markdown 快捷输入」小节。

    单独成节而不是塞进快捷键表：前者靠「在行首敲符号」，后者靠「按组合键」，
    学习路径不一样，混在一张表里反而不好找。
    """
    out = _t("markdown 快捷输入", ["h2"]) + [["t", "\n", []]]
    out += _t("在段首敲下面这些再敲一个空格，格式当场生效（最后几条不限行首，是敲完那一刻展开）：")
    out += [["t", "\n", []]]
    for k, desc in SAMPLE_MD:
        out += _t(k, ["bold"]) + _t("　" + desc) + [["t", "\n", []]]
    out += [["t", "\n", []]]
    return out


def _sample_keys_section():
    """「快捷键」小节：H2 标题 + 一行一条（键名加粗）+ 结尾说明。

    seed 与老库升级共用这一段，避免「新库有表、老库没有」的分叉。
    键与说明之间用全角空格隔开：中英混排下半角空格忽宽忽窄，全角空格稳定得多，
    也不依赖等宽字体。
    """
    out = _t("快捷键", ["h2"]) + [["t", "\n", []]]
    for k, desc in SAMPLE_KEYS:
        out += _t(k, ["bold"]) + _t("　" + desc) + [["t", "\n", []]]
    out += [["t", "\n", []]]
    out += _t("这篇说明是内置的，不能编辑也不能删除；想试这些快捷键，新建一篇笔记即可。")
    out += [["t", "\n", []]]
    out += _t("想给某篇笔记上锁：在左侧列表里右键 →「设置加密口令…」，"
              "之后每次打开都要输入口令。")
    out += [["t", "\n", []]]
    return out


def sample_content():
    """内置示例笔记的完整正文（seed 与老库升级都从这一份取）。"""
    return (_t("欢迎使用" + APP_NAME, ["title"]) + [["t", "\n", []]]
            + _t("这是一款复刻 macOS 笔记的桌面程序，用你电脑自带的 Python 即可运行。")
            + [["t", "\n", []]]
            + _t("试着用顶部工具栏：加粗、标题、待办清单都支持。")
            + [["t", "\n", []]]
            + _t("所有内容自动保存在本地 notes_data.db。")
            + [["t", "\n", []]]
            + _sample_md_section()
            + _sample_keys_section())


def _drop_legacy_sample_line(content):
    """摘掉旧版那行速记（`快捷键：Ctrl+N 新建、…`），返回新的 content。

    注意 content 是「逐字符一项」存的，一条文字段落由几十个单字项拼成，不能拿
    单项文本去 startswith——那只会在第一个字上就判错。这里先把连续的字项拼成
    段落，再判断整段前缀，命中就整段连它后面的换行一起丢掉。
    """
    out = []
    i, n = 0, len(content)
    while i < n:
        it = content[i]
        if it[0] != "t" or it[1] == "\n":
            out.append(it)
            i += 1
            continue
        j = i
        seg = []
        while j < n and content[j][0] == "t" and content[j][1] != "\n":
            seg.append(content[j])
            j += 1
        if not "".join(x[1] for x in seg).startswith("快捷键：Ctrl+N"):
            out.extend(seg)
        elif j < n and content[j][0] == "t" and content[j][1] == "\n":
            j += 1                      # 连同段落后的换行一起摘掉，免得多出一个空行
        i = j
    return out


# 说明笔记里由程序生成的尾部小节的标题；整段替换时用来找边界
_SAMPLE_TAIL_HEADINGS = ("markdown 快捷输入", "快捷键")


def _content_before_sample_tail(content):
    """返回「程序生成的尾部小节」之前的内容；没有这些小节时返回 None。

    边界要同时认两个标题：只认「快捷键」的话，新版把 markdown 那节当成用户内容
    保留下来，再追加一次就重复了。
    """
    for i, it in enumerate(content):
        if not (it[0] == "t" and len(it) > 2 and "h2" in it[2]):
            continue
        seg, j = [], i
        while j < len(content) and content[j][0] == "t" and content[j][1] != "\n":
            seg.append(content[j][1])
            j += 1
        if "".join(seg).strip() in _SAMPLE_TAIL_HEADINGS:
            return content[:i]
    return None


def _upgrade_sample_note(data):
    """老库里的内置说明笔记同步尾部两节（markdown 说明 + 快捷键表）。

    幂等，但用的是**整段替换**而不是只追加：新增的快捷键（Ctrl+E 之类）要能进到
    老库里已经存在的这一节，只追加会永远停在旧表上。
    替换范围限定在程序生成的两节之内，前面的说明文字原样保留。
    """
    want_tail = _sample_md_section() + _sample_keys_section()
    changed = False
    for n in data.get("notes", []):
        if not n.get("sample"):
            continue
        content = list(n.get("content") or [])
        head = _content_before_sample_tail(content)
        if head is None:
            # 还没有这两节（很老的库）：先摘掉旧版那行速记，再接上新表
            new = _drop_legacy_sample_line(content) + want_tail
        else:
            new = head + want_tail
        if new != content:
            n["content"] = new
            changed = True
    return changed


def plain_of(n):
    """笔记纯文本（搜索用）：待办框记作一个空格，换行归为空格。"""
    s = []
    for it in n.get("content") or []:
        if it[0] == "t":
            s.append(it[1])
        elif it[0] == "w":
            s.append(" ")
        elif it[0] == "code":          # 代码块整段计入搜索文本
            s.append(it[2] if len(it) > 2 else "")
        elif it[0] == "hr":
            continue                   # 分割线是纯装饰，不产生搜索文本
    txt = "".join(s)
    # 待办框现在是**字面字符**：方框连着后面那个间隙空格，正好顶掉一个空格。
    # 不换掉的话搜索文本里会混进方框字符，搜「买菜」命中的是「⬜ 买菜」，
    # 命中范围没问题但结果看着别扭。所有代方框字符都要换（含旧版直角 ☐/☑）。
    # 间隙可能是现行的两个 NBSP（TODO_GAP），也可能是老数据里的一个 NBSP / 半角空格。
    for mark in TODO_MARKS:
        for gap in (TODO_GAP, "\u00a0", " "):
            txt = txt.replace(mark + gap, " ")
        txt = txt.replace(mark, " ")
    return txt.replace("\n", " ").strip()


# ---------- 智能分类 ----------
# 左栏把笔记分成两组：「智能分类」（按条件筛出来的视图，没有真实归属，不能删、
# 不能当新建笔记的落点）和「文件夹」（用户自己建的真实归属）。
# 顺序即左栏的显示顺序；名字是唯一真相，老库里的旧名由 _ensure_smart_folders 统一。
SMART_FOLDERS = ("f_all", "f_star", "f_lock")
SMART_FOLDER_NAMES = {"f_all": "全部笔记", "f_star": "星标笔记", "f_lock": "加密笔记"}

# 文件夹最多几层（根层算第 1 层）。再深就缩进到看不见了，超过即不准再往下建。
# 这个值只在建子文件夹时判定一次，读到更深的历史数据（比如别人导来的备份）时
# 由 normalize_folder_parents 把超出的那几层提到根。
MAX_FOLDER_DEPTH = 5


def smart_notes(fid, notes):
    """按智能分类 / 普通文件夹筛一批笔记（纯函数，不碰 UI）。

    f_lock「加密笔记」只认 `sec` 里有没有密文 —— 加密笔记落库时正文和 plain 都是空的，
    所以这里（和搜索）都不需要解密，也不可能靠关键词命中。
    """
    notes = notes or []
    if fid == "f_all":
        return list(notes)
    if fid == "f_star":
        return [n for n in notes if n.get("starred", False)]
    if fid == "f_lock":
        return [n for n in notes if secret.is_blob(n.get("sec"))]
    return [n for n in notes if n.get("folder") == fid]


def folder_children(fid, folders):
    """fid 的**直接**子文件夹，顺序取 data["folders"] 里的先后（= 用户看到的顺序）。

    fid 传 None 表示「根层」——没有 parent 的文件夹都属于它。
    """
    out = []
    for f in (folders or []):
        if not isinstance(f, dict):
            continue
        if f.get("parent") == fid and f.get("id") != fid:
            out.append(f)
    return out


def folder_depth(fid, folders):
    """到根的距离，根层 = 1。

    父引用成环或指向不存在的文件夹时不再往上追（就地停下），
    所以这个函数永远有返回值 —— 界面用它判「还能不能再往下建一层」。
    """
    by = {f.get("id"): f for f in (folders or []) if isinstance(f, dict)}
    d, seen, cur = 1, set(), fid
    while cur in by and cur not in seen and d <= MAX_FOLDER_DEPTH + 2:
        seen.add(cur)
        pid = by[cur].get("parent")
        if not pid or pid == cur or pid not in by:
            break
        cur = pid
        d += 1
    return d


def folder_ancestors(fid, folders):
    """fid 的全部祖先，**从根到父**排好（不含自己）；已经是根层就返回 []。

    「跳到所属文件夹」要用它把路径上收着的各级逐级展开 —— 顺序必须是自上而下，
    否则展开下一级时它的行还没被渲染出来（父收着时子行整支都不存在）。
    成环 / 父引用悬空时就此停下，所以永远有返回值，不会死循环。
    """
    by = {f.get("id"): f for f in (folders or []) if isinstance(f, dict)}
    out, seen, cur = [], {fid}, fid
    while True:
        f = by.get(cur)
        if f is None:
            break
        pid = f.get("parent")
        if not pid or pid == cur or pid not in by or pid in seen:
            break
        seen.add(pid)
        out.append(by[pid])
        cur = pid
    out.reverse()
    return out


def subtree_ids(fid, folders):
    """自己 + 全部后代的 id（含重复防御：成环时每个 id 只算一次）。

    删除、计数、搜索都要整棵子树一起算 —— 父文件夹只数本级笔记的话，
    用户把笔记挪进子文件夹后父级数字反而变小，看着像数据丢了。
    """
    out, seen, stack = [], set(), [fid]
    while stack:
        cur = stack.pop()
        if cur in seen or cur is None:
            continue
        seen.add(cur)
        out.append(cur)
        for c in folder_children(cur, folders):
            stack.append(c.get("id"))
    return out


def normalize_folder_parents(folders):
    """清掉站不住的 parent 引用，返回是否有改动。

    要挡的有四类，任何一种放着不管都会让左栏渲染出问题：
      - 父不存在（备份里被删了一半、跨库合并）；
      - 指向自己（等于没有根的行，渲染时会递归到爆栈）；
      - 成环（A→B→A，同上）；
      - 父是智能分类 / 层级超过 MAX_FOLDER_DEPTH（导入的外部数据可能很深）。
    清掉后的文件夹提到根层：宁 Flat 也不能让行凭空消失或爆栈。
    """
    folders = folders or []
    by = {f.get("id"): f for f in folders if isinstance(f, dict)}
    changed = False
    for f in folders:
        if not isinstance(f, dict):
            continue
        pid = f.get("parent")
        if not pid:
            continue
        bad = False
        if pid == f.get("id") or pid not in by or by[pid].get("smart"):
            bad = True
        else:
            # 沿着父链一路往上：数出父这一支有几层，自己再加上一层不能超限；
            # 中途撞见走过的点 = 成环（渲染时会递归到爆栈），直接判不合格。
            lv, seen, cur = 1, set(), pid
            while cur in by:
                if cur in seen:
                    bad = True
                    break
                seen.add(cur)
                nxt = by[cur].get("parent")
                if not nxt or nxt not in by or nxt == cur:
                    break
                cur = nxt
                lv += 1
            if lv + 1 > MAX_FOLDER_DEPTH:
                bad = True
        if bad:
            f.pop("parent", None)
            changed = True
    return changed


def _ensure_smart_folders(data):
    """确保三个智能分类都在、名字是现行文案、且排在最前面。返回是否有改动。

    智能分类是**筛选视图**而不是真实文件夹：`smart=True` 只用来标记身份，
    真正的归属永远在普通文件夹里（见 app_actions.new_note 的兜底）。
    老库可能缺 f_lock（这一版才加）、也可能还带着已废弃的「我的 Mac」——
    后者在调用方（NotesApp.__init__）里剔除，这里只负责补齐与排序。
    """
    old = data.get("folders") or []
    folders = [f for f in old if isinstance(f, dict)]
    changed = len(folders) != len(old)
    by_id = {}
    for f in folders:
        fid = f.get("id")
        if fid not in SMART_FOLDERS:
            continue
        if not f.get("smart"):
            f["smart"] = True
            changed = True
        want = SMART_FOLDER_NAMES[fid]
        if f.get("name") != want:
            f["name"] = want
            changed = True
        by_id[fid] = f
    for fid in SMART_FOLDERS:
        if fid in by_id:
            continue
        f = {"id": fid, "name": SMART_FOLDER_NAMES[fid], "smart": True}
        by_id[fid] = f
        folders.append(f)
        changed = True
    head = [by_id[fid] for fid in SMART_FOLDERS]
    rest = [f for f in folders if f.get("id") not in SMART_FOLDERS]
    if folders[:len(head)] != head:
        changed = True
    data["folders"] = head + rest
    return changed


def seed():
    now = time.time() * 1000
    return {
        "folders": [
            {"id": "f_all", "name": "全部笔记", "smart": True},
            {"id": "f_star", "name": "星标笔记", "smart": True},
            {"id": "f_lock", "name": "加密笔记", "smart": True},
            {"id": "f_work", "name": "工作"},
            {"id": "f_life", "name": "个人"},
        ],
        "notes": [
            {"id": uid(), "folder": "f_work", "sample": True,
             "content": sample_content(),
             "pinned": True, "starred": False, "created": now, "updated": now},
            {"id": uid(), "folder": "f_work",
             "content": (_t("周会待办", ["title"]) + [["t", "\n", []]]
                         + _cb(False) + _t("整理上周进度\n")
                         + _cb(False) + _t("同步需求文档\n")),
             "pinned": False, "starred": True, "created": now - 3600e3 * 3, "updated": now - 3600e3 * 3},
            {"id": uid(), "folder": "f_life",
             "content": (_t("读书清单", ["title"]) + [["t", "\n", []]]
                         + _t("《人类简史》\n《思考，快与慢》\n")),
             "pinned": False, "starred": False, "created": now - 3600e3 * 26, "updated": now - 3600e3 * 26},
            {"id": uid(), "folder": "f_life",
             "content": (_t("周末计划", ["title"]) + [["t", "\n", []]]
                         + _t("爬山 + 看一部电影\n")),
             "pinned": False, "starred": False, "created": now - 3600e3 * 50, "updated": now - 3600e3 * 50},
        ],
        "current_folder": "f_all",
        "current_note": None,
    }


def _fix_sample_note(data):
    """旧库里内置的示例笔记随应用改名。

    只认“标题整串恰好等于历史示例标题”的笔记：标题换成「欢迎使用素笺」，
    并把该篇正文里的“备忘录”措辞统一成“笔记”。用户自己改过标题的笔记一概不动。
    返回是否改动过（改动则调用方落库，plain 搜索列会一起重建）。
    """
    want = "欢迎使用" + APP_NAME
    old_titles = ("欢迎使用备忘录", "欢迎使用笔记")
    changed = False
    for n in data.get("notes", []):
        content = n.get("content") or []
        idxs = [i for i, it in enumerate(content)
                if len(it) > 2 and it[0] == "t" and it[2] and "title" in it[2]]
        if not idxs:
            continue
        if "".join(content[i][1] for i in idxs) not in old_titles:
            continue
        tags = list(content[idxs[0]][2])
        mark = set(idxs)
        start, end = idxs[0], idxs[0]
        while end < len(content) and end in mark:     # 标题字符是连续的一段
            end += 1
        head, tail = content[:start], content[end:]

        def _fixed(items):                            # 正文措辞同步（仅这一篇示例）
            # 内容是按字符存的（每个字一项），所以替换要先把同标签的连续文字合并成串，
            # 再按字符拆回去，否则“备忘录”跨项永远匹配不到。
            out = []
            buf, tags_run = [], None

            def flush():
                if buf:
                    for c in "".join(buf).replace("备忘录", "笔记"):
                        out.append(["t", c, list(tags_run)])
                    del buf[:]

            for it in items:
                it = list(it)
                if it[0] == "t":
                    if buf and it[2] != tags_run:
                        flush()
                    tags_run = it[2]
                    buf.append(it[1])
                else:
                    flush()
                    tags_run = None
                    out.append(it)
            flush()
            return out

        n["content"] = _fixed(head) + [["t", c, tags] for c in want] + _fixed(tail)
        changed = True
    return changed


def _mark_sample_note(data):
    """给内置示例笔记补上 `sample` 标记（老库里没有这个字段）。

    判据和 `_fix_sample_note` 一致：标题整串恰好等于「欢迎使用素笺」。标记的作用是
    让这篇笔记在 UI 上不可删除 —— 它是唯一一份使用说明，删掉就再也找不回来了。
    用户自己改过标题的笔记一概不动（改名 = 收下它，保护自动解除）。
    返回是否改动过（改动则调用方落库）。
    """
    want = "欢迎使用" + APP_NAME
    changed = False
    for n in data.get("notes", []):
        if n.get("sample"):
            continue
        content = n.get("content") or []
        idxs = [i for i, it in enumerate(content)
                if len(it) > 2 and it[0] == "t" and it[2] and "title" in it[2]]
        if not idxs:
            continue
        if "".join(content[i][1] for i in idxs) != want:
            continue
        n["sample"] = True
        changed = True
    return changed


# trigram 分词下，查询词至少 3 个字符才凑得齐一个 trigram；再短就退回 LIKE
FTS_MIN_Q = 3


def like_escape(s):
    """转义 LIKE 通配符：用户输入里的 % 和 _ 必须当普通字符搜。

    不转义的话搜 "50%" 会命中所有含 5 的笔记（% 是任意长度通配），
    搜 "a_" 会命中 "ab"（_ 是单字符通配）。转义串本身也要先转义。
    """
    return (str(s).replace("\\", "\\\\")
                  .replace("%", "\\%")
                  .replace("_", "\\_"))


class Store(object):
    """SQLite 数据层：所有数据落在 notes_data.db。

    为什么从 JSON 换成 SQLite：
      - 原来每次保存都整份 json.dump 整个数据集，且搜索要把每条笔记的 content
        逐字符拼成纯文本再逐个比对 —— 笔记一多就越用越卡。
      - 现在笔记正文只写「真正变过的那一条」（用签名比对），搜索交给 SQL：
        plain 列 + LIKE + ORDER BY，数据库自己过滤排序，Python 不再全表扫描。
    内存里仍然保持 self.data 这个 dict，界面代码无需跟着改。
    """

    def __init__(self, path=DB_FILE):
        self.path = path
        # 数据库被占用时（例如误开了两个窗口）最多等 BUSY_TIMEOUT_S 秒就报错。
        # sqlite3 默认要等 5 秒，而那 5 秒是卡在界面线程上的，用户会觉得程序死了。
        self.con = sqlite3.connect(path, timeout=BUSY_TIMEOUT_S)
        self._sig = {}          # id -> 签名：跳过未变动的笔记，避免全量重写
        self._pos = {}          # id -> 列表位置：位置变了（如新建插到首位）也要回写
        self._fts = False       # FTS5 索引是否可用（不可用则退回 LIKE）
        self._init_db()
        self._init_fts()

    # ---------- 建表 ----------
    def _init_db(self):
        c = self.con
        c.executescript("""
            CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
            CREATE TABLE IF NOT EXISTS folders (
                id    TEXT PRIMARY KEY,
                name  TEXT,
                smart INTEGER DEFAULT 0,
                pos   INTEGER);
            CREATE TABLE IF NOT EXISTS notes (
                id      TEXT PRIMARY KEY,
                folder  TEXT,
                content TEXT,
                plain   TEXT,
                pinned  INTEGER DEFAULT 0,
                starred INTEGER DEFAULT 0,
                created REAL,
                updated REAL,
                pos     INTEGER DEFAULT 0,
                sample  INTEGER DEFAULT 0,
                links   TEXT,
                sec     TEXT);
            CREATE INDEX IF NOT EXISTS idx_notes_folder ON notes(folder);
            CREATE INDEX IF NOT EXISTS idx_notes_star   ON notes(starred);
        """)
        # 老库补列（本版新增 pos）：没有它读出来的顺序不稳定，导出/新建顺序会漂
        cols = {r[1] for r in c.execute("PRAGMA table_info(notes)")}
        if "pos" not in cols:
            c.execute("ALTER TABLE notes ADD COLUMN pos INTEGER DEFAULT 0")
        # 老库补列（本版新增 sample）：内置示例笔记的「不可删除」标记。
        # 没有它的话保护只在当前这次运行里有效，重启后就丢了。
        if "sample" not in cols:
            c.execute("ALTER TABLE notes ADD COLUMN sample INTEGER DEFAULT 0")
        # 老库补列（本版新增 links / sec）：
        #   links —— 每个超链接的 URL（正文里只留 lk_N 标签，文字本身当显示文字）
        #   sec   —— 加密笔记的密文。有值的行 content/plain 一律写空：
        #            数据库里除了密文，不该留下任何可读的明文痕迹
        if "links" not in cols:
            c.execute("ALTER TABLE notes ADD COLUMN links TEXT")
        if "sec" not in cols:
            c.execute("ALTER TABLE notes ADD COLUMN sec TEXT")
        # 老库补列（本版新增多级文件夹）：
        #   parent    —— 父文件夹 id（根层为空）
        #   collapsed —— 树里这一支收起来了没有（纯展示状态，但要跟走窗口重开）
        fcols = {r[1] for r in c.execute("PRAGMA table_info(folders)")}
        if "parent" not in fcols:
            c.execute("ALTER TABLE folders ADD COLUMN parent TEXT")
        if "collapsed" not in fcols:
            c.execute("ALTER TABLE folders ADD COLUMN collapsed INTEGER DEFAULT 0")
        c.commit()

    # ---------- 全文索引（FTS5） ----------
    def _init_fts(self):
        """建 FTS5 全文索引；环境不支持就静默降级，仍走 LIKE（见 search_ids）。

        用 trigram 分词器：中文没有空格，unicode61 会把整段中文当成一个词，
        搜「数据库」在「数据库设计」里反而命中不了；trigram 按 3 字滑窗切分，
        任意子串都能匹配。代价是查询词要 >= 3 个字符才有完整 trigram，
        更短的查询由 search_ids 自动退回 LIKE。
        """
        self._fts = False
        c = self.con
        if sqlite3.sqlite_version_info < (3, 34, 0):   # trigram 自 3.34 起提供
            return
        try:
            # 先在 temp 库试建：避免把不支持的虚拟表留在正式库里
            c.execute("CREATE VIRTUAL TABLE temp._fts_probe "
                      "USING fts5(x, tokenize='trigram')")
            c.execute("DROP TABLE temp._fts_probe")
            c.executescript("""
                CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts
                    USING fts5(id UNINDEXED, plain, tokenize='trigram');
                CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
                    -- save() 用的是 INSERT OR REPLACE：它不会触发 DELETE/UPDATE 触发器，
                    -- 所以这里先按 id 清一遍再插，保证索引里同一条笔记始终只有一份
                    DELETE FROM notes_fts WHERE id = new.id;
                    INSERT INTO notes_fts(id, plain) VALUES (new.id, new.plain);
                END;
                CREATE TRIGGER IF NOT EXISTS notes_au AFTER UPDATE ON notes BEGIN
                    UPDATE notes_fts SET plain = new.plain WHERE id = new.id;
                END;
                CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
                    DELETE FROM notes_fts WHERE id = old.id;
                END;
            """)
            # 建表前就存在的笔记（老库升级）要回填一次；按去重后的 id 数比较，
            # 万一被旧版本写进了重复行也能自愈
            n_notes = c.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
            n_idx = c.execute("SELECT COUNT(*) FROM (SELECT DISTINCT id "
                              "FROM notes_fts)").fetchone()[0]
            if n_idx != n_notes:
                c.execute("DELETE FROM notes_fts")
                c.execute("INSERT INTO notes_fts(id, plain) "
                          "SELECT id, plain FROM notes")
            c.commit()
            self._fts = True
        except Exception:
            self._fts = False

    # ---------- 读 ----------
    def is_empty(self):
        return (self.con.execute("SELECT COUNT(*) FROM folders").fetchone()[0] == 0 and
                self.con.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 0)

    def load(self):
        """读库；空库（首次运行）时先从旧 notes_data.json 迁移，否则写入初始数据。"""
        if self.is_empty():
            d = None
            src = DATA_FILE if os.path.exists(DATA_FILE) else _legacy_json()
            if os.path.exists(src):
                try:
                    with open(src, "r", encoding="utf-8") as f:
                        d = json.load(f)
                except Exception:
                    d = None
            if not (isinstance(d, dict) and d.get("folders")):
                d = seed()
            self.save(d, full=True)
        return self.read_all()

    def read_all(self):
        d = {"folders": [], "notes": [], "current_folder": "f_all", "current_note": None}
        for fid, name, smart, parent, collapsed in self.con.execute(
                "SELECT id, name, smart, parent, collapsed FROM folders ORDER BY pos"):
            f = {"id": fid, "name": name}
            if smart:
                f["smart"] = True
            if parent:
                f["parent"] = parent
            if collapsed:
                f["collapsed"] = True
            d["folders"].append(f)
        for (nid, folder, content, plain, pinned, starred, created, updated,
             sample, links, sec) in self.con.execute(
                "SELECT id, folder, content, plain, pinned, starred, created, updated,"
                " sample, links, sec FROM notes ORDER BY pos, id"):
            sealed = False
            sec_obj = None
            if sec:
                try:
                    sec_obj = json.loads(sec)
                except Exception:
                    sec_obj = None
                sealed = isinstance(sec_obj, dict) and bool(sec_obj.get("data"))
            try:
                body = json.loads(content) if (content and not sealed) else []
            except Exception:
                body = []
            if not isinstance(body, list):
                body = []
            note = {"id": nid, "folder": folder, "content": body,
                    "pinned": bool(pinned), "starred": bool(starred),
                    "created": created or 0.0, "updated": updated or 0.0}
            if sample:
                note["sample"] = True
            if sealed:
                # 密文原样带出，明文一个字符都不读（库里本来也没存）
                note["sec"] = sec_obj
            if links:
                try:
                    ln = json.loads(links)
                    if ln:
                        note["links"] = ln
                except Exception:
                    pass
            d["notes"].append(note)
        d["current_folder"] = self._meta("current_folder", "f_all")
        d["current_note"] = self._meta("current_note", None)
        for key in ("settings", "styles"):
            v = self._meta(key, None)
            if v:
                d[key] = v
        return d

    def _meta(self, k, default):
        row = self.con.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        if row is None or row[0] in (None, "", "null"):
            return default
        try:
            return json.loads(row[0])
        except Exception:
            return default

    # ---------- 写 ----------
    def save(self, d, full=False):
        """把内存数据落库。默认只写签名变了的笔记（full=True 时全量重写）。"""
        c = self.con
        with c:
            c.execute("DELETE FROM folders")
            for i, f in enumerate(d.get("folders") or []):
                c.execute("INSERT INTO folders(id,name,smart,parent,collapsed,pos)"
                          " VALUES(?,?,?,?,?,?)",
                          (f["id"], f.get("name", ""),
                           1 if f.get("smart") else 0,
                           f.get("parent") or None,
                           1 if f.get("collapsed") else 0, i))
            seen = set()
            for i, n in enumerate(d.get("notes") or []):
                nid = n["id"]
                seen.add(nid)
                pos = i
                sig = self._sig_of(n)
                if not full and self._sig.get(nid) == sig and self._pos.get(nid) == pos:
                    continue          # 没变过 → 不写，省掉一次 JSON 序列化 + 磁盘写
                # 加密笔记：正文只以密文形式落库，content / plain 一律写空。
                # `plain` 是搜索列，留有明文等于把口令白设了（搜关键词就能把内容搜出来）。
                sec_obj = n.get("sec")
                sealed = isinstance(sec_obj, dict) and bool(sec_obj.get("data"))
                c.execute("INSERT OR REPLACE INTO notes"
                          "(id,folder,content,plain,pinned,starred,created,updated,pos,sample,"
                          " links,sec)"
                          " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                          (nid, n.get("folder"),
                           "" if sealed else json.dumps(n.get("content") or [],
                                                        ensure_ascii=False),
                           "" if sealed else plain_of(n),
                           1 if n.get("pinned") else 0,
                           1 if n.get("starred") else 0,
                           n.get("created") or 0.0, n.get("updated") or 0.0, pos,
                           1 if n.get("sample") else 0,
                           json.dumps(n.get("links") or {}, ensure_ascii=False),
                           json.dumps(sec_obj, ensure_ascii=False) if sealed else ""))
                self._sig[nid] = sig
                self._pos[nid] = pos
            for k in [k for k in self._sig if k not in seen]:
                self._sig.pop(k, None)
                self._pos.pop(k, None)
            # 删掉内存里已不存在的笔记。注意不能写 "id NOT IN (全部 id)"：
            # SQLite 的占位符数量有上限（本项目 Python 自带的版本是 999），
            # 笔记一多就会报 "too many SQL variables"，所以先取出库里的 id
            # 算出差集，再按批（每批 400）删。
            gone = [r[0] for r in c.execute("SELECT id FROM notes")
                    if r[0] not in seen]
            for i in range(0, len(gone), 400):
                chunk = gone[i:i + 400]
                c.execute("DELETE FROM notes WHERE id IN (%s)" %
                          ",".join("?" * len(chunk)), chunk)
            for k in ("current_folder", "current_note", "settings", "styles"):
                if k in d:
                    c.execute("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)",
                              (k, json.dumps(d[k], ensure_ascii=False)))

    def _sig_of(self, n):
        """「这条笔记是否变过」的廉价判据（不序列化全文）。

        编辑必然刷新 updated，星标/置顶/移动也都在签名里，足以覆盖所有改动路径。
        links / sec 也要算进去：改链接地址、重新加密都不一定改 updated
        （比如只改了 URL 就顺手 save），漏了就会出现"改了没存"。
        """
        sec = n.get("sec")
        return (n.get("updated"), n.get("starred"), n.get("pinned"),
                n.get("folder"), len(n.get("content") or []), n.get("sample"),
                len(n.get("links") or {}),
                (sec or {}).get("tag") if isinstance(sec, dict) else None)

    # ---------- 搜索 ----------
    def search_ids(self, folder, q, folder_ids=None):
        """关键词检索：SQL 里过滤 + 排序，直接返回排好序的 id 列表。

        SQLite 的 LIKE 默认对 ASCII 不区分大小写（case_sensitive_like 关闭），
        中文本来也无大小写问题，所以直接用即可。
        关键词里的 % / _ 是 LIKE 通配符，必须先转义（见 like_escape）。

        folder_ids：多级文件夹时传进来的是「整棵子树」的 id（含它自己）。父文件夹
        要连子孙里的笔记一起搜回来，否则切到父级再搜索会「少一堆刚看见的笔记」。
        """
        where, args = [], []
        if self._fts and len(q) >= FTS_MIN_Q:
            # 查询串整体当一个短语（双引号包起来，内部引号翻倍转义）
            where.append("id IN (SELECT id FROM notes_fts WHERE plain MATCH ?)")
            args.append('"%s"' % q.replace('"', '""'))
        else:
            where.append("plain LIKE ? ESCAPE '\\'")
            args.append("%" + like_escape(q) + "%")
        if folder == "f_star":
            where.append("starred=1")
        elif folder == "f_lock":
            # 加密过的笔记：密文整块存在 sec 列，没加密的行这一列是**空串**（不是 NULL，
            # save 一律按 `... if sealed else ""` 写入），所以两种都要排除。
            # 未解锁时 content/plain 都是空，所以它们不会出现在关键词命中里 ——
            # 这是对的：库里本来就没有明文可搜。
            where.append("sec IS NOT NULL AND sec <> ''")
        elif folder and folder != "f_all":
            ids = list(folder_ids or [folder])
            if len(ids) == 1:
                where.append("folder=?")
                args.append(ids[0])
            elif not ids:
                where.append("0")               # 树是空的 → 一条都不该命中
            else:
                where.append("folder IN (%s)" % ",".join("?" * len(ids)))
                args.extend(ids)
        sql = ("SELECT id FROM notes WHERE " + " AND ".join(where) +
               " ORDER BY pinned DESC, starred DESC, updated DESC")
        return [r[0] for r in self.con.execute(sql, args)]

    # ---------- 清空 / 重置 ----------
    def reset(self, d=None):
        """清空整库并写入给定（或初始）数据 —— 清空数据 / 导入时用它。"""
        d = d if d is not None else seed()
        with self.con:
            self.con.execute("DELETE FROM notes")
            self.con.execute("DELETE FROM folders")
            self.con.execute("DELETE FROM meta")
        self._sig = {}
        self._pos = {}
        self.save(d, full=True)

    def close(self):
        """关闭连接，并把模块级单例置空。

        置空这一步不能省：否则下次 store() 会拿到一个已经关闭的连接，
        报 "Cannot operate on a closed database"——懒创建就要配上「关了就清」。
        """
        global _STORE
        try:
            self.con.commit()
            self.con.close()
        except Exception:
            pass
        if _STORE is self:
            _STORE = None


_STORE = None


def store():
    """全局唯一的 Store（懒创建，避免 import 时就动数据库文件）。"""
    global _STORE
    if _STORE is None:
        _STORE = Store(_resolve_db())
    return _STORE


def load_data():
    return store().load()


# ---------- 保存失败：交给界面层提示 + 兜底快照 ----------
# 本层不 import tkinter，所以只留一个回调钩子由界面层注入。
# 没注册钩子时行为与以前完全一致（异常照抛），单元测试和命令行用法不受影响。
_SAVE_ERROR_HOOK = None


def set_save_error_hook(fn):
    """注册保存失败回调 fn(exc, data)；回调返回即视为已处理，save_data 不再抛。"""
    global _SAVE_ERROR_HOOK
    _SAVE_ERROR_HOOK = fn


def save_data(d):
    """落库。

    返回 True = 成功；False = 失败但已由钩子处理；未注册钩子时异常照常抛出。
    save() 内部用事务包着，失败会回滚，不会留下写了一半的库。
    """
    try:
        store().save(d)
        return True
    except Exception as e:
        hook = _SAVE_ERROR_HOOK
        if hook is None:
            raise
        # 钩子自己崩了就让它冒出去：那是编程错误，不该被当成「已妥善处理」
        hook(e, d)
        return False


def pending_files():
    """data/ 下遗留的兜底快照，按时间从旧到新。"""
    try:
        names = sorted(n for n in os.listdir(BASE)
                       if n.startswith(PENDING_PREFIX) and n.endswith(".json"))
    except Exception:
        return []
    return [os.path.join(BASE, n) for n in names]


def dump_pending(d, reason=""):
    """把内存数据写成 data/pending_<时间戳>.json，返回路径（失败返回 None）。

    格式与「数据管理 → 导出备份」一致，所以事后可以直接用「导入数据」把它捞回来。
    """
    path = os.path.join(BASE, "%s%s.json" % (PENDING_PREFIX, time.strftime("%Y%m%d-%H%M%S")))
    snap = {
        "_app": "notes-app",
        "_version": 1,
        "_pending": True,
        "_reason": str(reason or ""),
        "_exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "folders": d.get("folders") or [],
        "notes": d.get("notes") or [],
        "settings": d.get("settings") or {},
        "styles": d.get("styles") or {},
    }
    try:
        os.makedirs(BASE, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, indent=2)
        return path
    except Exception:
        return None


# ---------- 导入：把外部 JSON 整理成「可直接落库」的数据 ----------

def _carry_extra(src, dst):
    """把「正文以外」的新字段带进导入结果（覆盖导入与合并导入共用）。

    少带一个就会出事：
      links —— 链接还在（标签也跟着 content 走），但点开时找不到 URL；
      sec   —— 加密笔记的正文在库里本来就是空的，丢了密文等于丢了整篇内容。
    """
    links = src.get("links")
    if isinstance(links, dict) and links:
        dst["links"] = links
    sec = src.get("sec")
    if isinstance(sec, dict) and sec.get("data"):
        dst["sec"] = sec
        dst["content"] = []          # 以密文为准，别被顺手塞进来的明文覆盖


def normalize_import(d, settings=None, styles=None):
    """覆盖式导入：补齐智能文件夹、字段与归属。"""
    folders = [dict(f) for f in (d.get("folders") or [])
               if isinstance(f, dict) and f.get("id") and f.get("name")]
    ids = {f["id"] for f in folders}
    smart_box = {"folders": folders}
    _ensure_smart_folders(smart_box)     # 三个智能分类一起补齐（含顺序）
    folders = smart_box["folders"]
    normalize_folder_parents(folders)    # 悬空 / 成环 / 过深的 parent 就地清掉
    ids |= set(SMART_FOLDERS)
    if "f_work" not in ids:
        folders.append({"id": "f_work", "name": "工作"})
        ids.add("f_work")
    now = time.time() * 1000
    notes = []
    for n in d.get("notes") or []:
        if not isinstance(n, dict) or not isinstance(n.get("content"), list):
            continue
        notes.append({
            "id": n.get("id") or uid(),
            # 备份里的文件夹若已不存在，落到「工作」，避免笔记变成孤儿
            "folder": n.get("folder") if n.get("folder") in ids else "f_work",
            "content": n["content"],
            "pinned": bool(n.get("pinned")),
            "starred": bool(n.get("starred")),
            "created": n.get("created") or now,
            "updated": n.get("updated") or now,
        })
        if n.get("sample"):
            # 示例笔记的「不可删除」标记随备份走，否则导入后它就又能被删掉了
            notes[-1]["sample"] = True
        _carry_extra(n, notes[-1])
    return {"folders": folders, "notes": notes, "current_folder": "f_all",
            "current_note": None,
            "settings": settings or {},
            "styles": styles or {}}


def merge_import(d, cur):
    """合并追加：保留现有文件夹/笔记，追加备份里没有的（id 冲突则生成新 id）。"""
    folders = [dict(f) for f in (cur.get("folders") or [])]
    by_id = {f["id"]: f for f in folders}
    by_name = {f.get("name"): f for f in folders}
    for f in d.get("folders") or []:
        if not isinstance(f, dict) or not f.get("id"):
            continue
        if f["id"] in by_id:
            continue
        if f.get("name") in by_name:      # 同名文件夹视为同一个，不重复建
            continue
        nf = dict(f)
        nf.pop("smart", None)             # 导入的普通文件夹不保留智能标记
        folders.append(nf)
        by_id[nf["id"]] = nf
        by_name[nf.get("name")] = nf
    ids = {f["id"] for f in folders}
    if "f_work" not in ids:
        folders.append({"id": "f_work", "name": "工作"})
        ids.add("f_work")
    now = time.time() * 1000
    notes = [dict(n) for n in (cur.get("notes") or [])]
    have = {n["id"] for n in notes}
    for n in d.get("notes") or []:
        if not isinstance(n, dict) or not isinstance(n.get("content"), list):
            continue
        nid = n.get("id") or uid()
        if nid in have:                   # id 撞了：换一个新 id，两边都保留
            nid = uid()
        notes.append({
            "id": nid,
            "folder": n.get("folder") if n.get("folder") in ids else "f_work",
            "content": n["content"],
            "pinned": bool(n.get("pinned")),
            "starred": bool(n.get("starred")),
            "created": n.get("created") or now,
            "updated": n.get("updated") or now,
        })
        if n.get("sample"):
            notes[-1]["sample"] = True
        _carry_extra(n, notes[-1])
        have.add(nid)
    box = {"folders": folders}
    _ensure_smart_folders(box)      # 合并后同样保证三个智能分类在最前
    normalize_folder_parents(box["folders"])
    return {"folders": box["folders"], "notes": notes,
            "current_folder": cur.get("current_folder", "f_all"),
            "current_note": cur.get("current_note"),
            "settings": cur.get("settings", {}),
            "styles": cur.get("styles", {})}
