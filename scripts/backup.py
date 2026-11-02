# -*- coding: utf-8 -*-
"""素笺 —— 备份格式：导出编码 / 导入解码 / 合并策略（纯函数，不碰 Tk）。

为什么要有这一层：以前「导出」就是把内存里的 data 字典原样 json.dump 出去，
「导入」再用白名单挑几个字段重建 —— 导出端和导入端各写各的，中间没有契约。
表现就是（都是实际发生过的）：

  * `_version` 只有导出方在写，导入方从来不读 → 未来加的字段会被老版本静默丢弃；
  * settings / styles 导出了却从不采纳（覆盖导入传进去的是本机的）→ 换机后
    字号、字体、工具栏全不回来，还没人告诉你；
  * 合并时笔记 id 撞了就换新 id，但 `folder` 这个引用不跟着改 → 笔记落错文件夹；
    `settings.tabs`（存的是 note id）、`alarm_fired`（键是 `<note_id>|<alarm_id>`）
    更是彻底悬空 → 标签页丢、提醒重响；
  * 同一个备份导两次 = 全库翻倍（第二次导入时 id 本机已经没有，原样追加）。

这一版把「格式」正式定义出来（schema 2），并把这些坑逐条堵上：

  * 顶层 `format` / `schema` / `checksum`；`exported_at` 带时区（ISO 8601）。
    schema 比本程序新就明确报错，而不是硬着头皮读一半。
  * `settings` 拆成 `prefs`（外观：字号 / 字体 / 工具栏按钮，随人走）与
    `state`（会话：开着的标签、两栏宽度、折叠、`alarm_fired`，随机器走）。
    **state 不导出** —— 它存的全是本机的 id 和像素值，搬去另一台机器毫无意义，
    还会制造悬空引用。覆盖导入采纳 prefs，合并导入保留本机外观。
  * 每条笔记带 `rev`（正文 + 链接 + 密文的指纹）。合并时 rev 相同 = 同一篇，
    跳过 → 「同一个备份导两次」变成幂等操作；id 撞了但内容不同才换新 id。
  * 内置说明笔记（`sample`）**不导出**：它是程序自带的使用说明，不是用户的内容；
    导入时按当前版本重建，免得旧备份里那张过时的快捷键表跟着到处跑。
  * 文件夹 id 冲突不再「直接跳过丢掉备份里的那个」，而是建一张
    「备份 id → 本机 id」的映射表，`notes.folder` 和 `parent` 都按它重写。
  * 正文从「逐字符 runs」压成 `text` + `spans` + `blocks`：一个字符一条
    `["t", ch, [tags]]` 的写法实测 20~40 字节/字，4 万字的长篇就是 1MB+ 的
    JSON；换成「一段文本 + 区间」后只有 3~5 字节/字。压缩只在导出/导入两侧做，
    **库内存储不变**，所以对编辑器零影响。
  * 笔记 / 文件夹里不认识的字段一律原样带回（以前是白名单剪裁，等于静默丢数据）。

导入仍然吃 v1：老备份、以及 `data/pending_*.json` 那种保存失败兜底快照，
都走同一条 decode 路径（v1 的 `settings` 按键分拣到 prefs / state）。
"""

import base64
import datetime
import gzip
import hashlib
import hmac
import io
import json
import os
import struct
import time

from store import (SMART_FOLDERS, SYSTEM_FOLDER, _ensure_smart_folders,
                   _ensure_system_folder, normalize_folder_parents,
                   new_sample_note, uid)

# 备份文件里的格式标识与 schema 版本。
# `schema` 每次改动**不兼容**的结构时才加一；加字段不用动它（老版本会忽略新键）。
FORMAT = "sujian.backup"
SCHEMA = 2

# 导出容器的格式标识与版本。容器是**另一层**，和上面的快照 schema 无关：
# 剥壳还原出来的仍是完完整整的 schema 2 快照 dict，decode() 一个字都不用改。
# 独立 format 还有个好处：谁要是把容器直接丢给 decode()，会被明确拒绝，
# 而不是「少了 folders / notes」这种让人摸不着头脑的报错。
CONTAINER_FORMAT = "sujian.backup.container"
CONTAINER_VERSION = 1
# 备份会被长期存档，离线爆破成本低，KDF 比单篇笔记（secret.py 的 20 万）更狠一档。
# 只在导出 / 导入各付一次，不落在任何热路径上。
CONTAINER_ITERATIONS = 600000
_MIN_ITER, _MAX_ITER = 100000, 5000000
_DOMAIN = b"sujian.backup.container.v1"
_SALT_LEN, _NONCE_LEN = 16, 12

# settings 里的两类键：
#   prefs —— 外观偏好，跟着人走。覆盖导入时采纳备份里的值。
#   state —— 本机会话状态，不导出（存的是本机 id / 像素，换机就是垃圾）。
PREFS_KEYS = ("font_family", "body_size", "toolbar")
STATE_KEYS = ("tabs", "side_w", "list_w", "alarm_fired", "current_folder", "current_note")

# 只存在于备份文件里的笔记字段（导入时展开成 content 后要剔掉，别流进内存数据）
_V2_ONLY = ("text", "spans", "blocks", "rev")
_NOTE_KNOWN = frozenset(("id", "folder", "content", "created", "updated",
                         "pinned", "starred", "sample", "links", "sec",
                         "color") + _V2_ONLY)


class BackupError(ValueError):
    """备份文件读不了 / 不是备份 / 版本太新。调用方直接把它的话术显示给用户。"""


def _clean_color(v):
    """笔记色条颜色：#rrggbb 就规范化成小写，其它一律当没有（返回 ""）。

    不按 `NOTE_COLORS` 白名单卡死：以后调色板换色时，旧备份里的颜色应当还能
    原样读回来，而不是被悄悄抹掉。真正的约束只有「能当 Canvas 的 fill 用」。
    """
    if not isinstance(v, str):
        return ""
    s = v.strip()
    if len(s) == 7 and s[0] == "#" and all(c in "0123456789abcdefABCDEF" for c in s[1:]):
        return s.lower()
    return ""


# ---------- 时间 / 摘要 ----------

def now_iso(dt=None):
    """本地时区的 ISO 8601，带偏移量（`2026-10-30T10:52:16+08:00`）。

    老版本写的是裸的本地时间字符串，没有时区：多台机器备份混在一起时，
    「导出时间」谁先谁后都排不出来。
    """
    dt = dt or datetime.datetime.now().astimezone()
    return dt.isoformat(timespec="seconds")


def human_time(s):
    """ISO 时间 → 本机时区的「2026-10-30 10:52」；认不出来就原样返回（v1 的裸串）。"""
    if not s:
        return ""
    try:
        dt = datetime.datetime.fromisoformat(str(s))
    except Exception:
        return str(s)
    try:
        if dt.tzinfo is not None:
            dt = dt.astimezone()
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(s)


def _canonical(payload):
    """用于校验和的规范字节串：键排序、无多余空白 —— 与文件里的排版（indent）无关。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def digest(payload):
    """整份快照的 sha256（`sha256:…`）。用来发现截断 / 手改 / 传坏的文件。

    json.load 只保证语法完整：一个被截掉一半的文件照样可能解析成功，
    导入后就是「少了一半笔记」而毫无提示。
    """
    return "sha256:" + hashlib.sha256(_canonical(payload)).hexdigest()


def rev_of(note):
    """一篇笔记的正文指纹（正文 + 标签区间 + 链接 + 密文，任一不同即不同）。

    合并时用它对「同一篇」去重。故意不含 id：换机、换 id 之后内容一样就是同一篇，
    这正是「同一个备份导两次不该翻倍」需要的判据。

    指纹取的是**规范化形状**（text + spans + blocks），不是原始 runs：
    runs 允许把同一段文字切成好几种方式（库里是 `["t","\\u3000\\u3000",[]]` 这种
    一条多字，备份还原出来可能是一条字一条），拿 runs 直接哈希会让「内容一字不差」
    的两份笔记算出不同指纹 —— 合并时就会多出一份副本。
    """
    text, spans, blocks = content_to_spans(note.get("content") or [])
    sec = note.get("sec")
    payload = {
        "t": text,
        "s": sorted(spans),
        "b": blocks,
        "links": note.get("links") or {},
        "sec": (sec or {}).get("data") if isinstance(sec, dict) else "",
    }
    try:
        raw = _canonical(payload)
    except Exception:
        raw = repr(payload).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


# ---------- 正文：runs ⇄ text + spans + blocks ----------
# runs 是编辑器那边的存储形状（一个字符一条，带当时的标签快照）；
# text/spans/blocks 是备份里的形状（一段文本 + 区间 + 非文本元素），体积小一个量级。
# 两者必须**逐字符等价**地互转，否则「导出再导入」一次就会掉格式（见 tests/test_backup.py）。

def content_to_spans(runs):
    """runs → (text, spans, blocks)。

    spans 是 `[[tag, start, end], …]`，同一 tag 的连续区间合并成一条；
    blocks 是正文里那些不占字符的元素（代码块 / 分割线 / 闹钟 / 老式待办），
    用 `i` 记住它在内容序列里的位置（含这些元素一起数）。
    """
    chars = []
    spans = []
    open_span = {}          # tag -> 当前那条 span（还没断的话直接续上终点）
    blocks = []
    pos = 0
    for it in runs or []:
        if not isinstance(it, (list, tuple)) or not it:
            continue
        kind = it[0]
        if kind == "t":
            text = it[1] if len(it) > 1 else ""
            if not isinstance(text, str):
                text = "" if text is None else str(text)
            tags = it[2] if len(it) > 2 else ()
            if not isinstance(tags, (list, tuple)):
                tags = ()
            for ch in text:
                for tg in tags:
                    cur = open_span.get(tg)
                    if cur is not None and cur[2] == pos:
                        cur[2] = pos + 1
                    else:
                        cur = [tg, pos, pos + 1]
                        spans.append(cur)
                        open_span[tg] = cur
                chars.append(ch)
                pos += 1
        else:
            blocks.append(_block_encode(it, pos))
            pos += 1
    return "".join(chars), spans, blocks


def spans_to_runs(text, spans, blocks):
    """(text, spans, blocks) → runs。标签按名字排序，与 serialize() 一致。

    相邻且标签完全相同的字符合成**一条** run（`["t", "标题", ["title"]]`）：
    逐字符一条的写法语义没错，但落库后正文 JSON 会胖十几倍 —— serialize() 自己
    也是这么合并的，还原时跟着它走，导回来的笔记与导出前一模一样。
    """
    text = text if isinstance(text, str) else ""
    by_start = {}
    for sp in spans or []:
        try:
            tag, a, b = sp[0], int(sp[1]), int(sp[2])
        except Exception:
            continue
        if b <= a or a < 0:
            continue
        by_start.setdefault(a, []).append((tag, b))
    at_block = {}
    for b in blocks or []:
        if not isinstance(b, dict):
            continue
        try:
            at_block[int(b.get("i", -1))] = b
        except Exception:
            continue
    active = {}             # tag -> 结束位置
    out = []
    ti = 0                  # text 里的游标
    total = len(text) + len(at_block)
    for pos in range(total):
        for tg in [t for t, e in active.items() if e <= pos]:
            active.pop(tg, None)
        for tag, end in by_start.get(pos, ()):
            active[tag] = end
        blk = at_block.get(pos)
        if blk is not None:
            run = _block_decode(blk)
            if run is not None:
                out.append(run)
            continue
        if ti >= len(text):
            break
        tags = sorted(active)
        if out and out[-1][0] == "t" and out[-1][2] == tags:
            out[-1][1] += text[ti]      # 与上一条标签相同 → 接着往后写，别再切一条
        else:
            out.append(["t", text[ti], tags])
        ti += 1
    # blocks 的位置若超出文本、落在了循环之外（脏数据），也别把它们吞掉
    for pos in sorted(at_block):
        if pos >= total:
            run = _block_decode(at_block[pos])
            if run is not None:
                out.append(run)
    return out


def _block_encode(run, pos):
    """一个非文本元素 → 备份里的 block。

    不认识的元素（未来版本加的新内嵌控件）整条塞进 "raw"，导入时原样还原 ——
    宁可原样带过去，也不要「新版导出、老版导入就消失」。
    """
    kind = run[0]
    if kind == "code":
        return {"i": pos, "kind": "code",
                "lang": run[1] if len(run) > 1 else "",
                "src": run[2] if len(run) > 2 else ""}
    if kind == "hr":
        return {"i": pos, "kind": "hr"}
    if kind == "alarm":
        d = run[1] if len(run) > 1 and isinstance(run[1], dict) else {}
        return {"i": pos, "kind": "alarm", "id": d.get("id"),
                "text": d.get("text"), "at": d.get("at")}
    if kind == "w":          # 老数据里的待办嵌入控件（现在是字面字符，仅兼容读取）
        return {"i": pos, "kind": "todo",
                "checked": bool(run[1]) if len(run) > 1 else False,
                "tags": list(run[2]) if len(run) > 2 and isinstance(run[2], (list, tuple)) else []}
    return {"i": pos, "kind": "raw", "run": list(run)}


def _block_decode(b):
    kind = b.get("kind")
    if kind == "code":
        return ["code", b.get("lang") or "", b.get("src") or ""]
    if kind == "hr":
        return ["hr"]
    if kind == "alarm":
        d = {"id": b.get("id"), "text": b.get("text")}
        if b.get("at") is not None:
            d["at"] = b.get("at")
        return ["alarm", d]
    if kind == "todo":
        return ["w", 1 if b.get("checked") else 0, list(b.get("tags") or [])]
    if kind == "raw":
        run = b.get("run")
        if isinstance(run, (list, tuple)) and run:
            return list(run)
    return None


# ---------- 编码（导出） ----------

def _encode_note(n):
    text, spans, blocks = content_to_spans(n.get("content") or [])
    out = {"id": n.get("id"), "folder": n.get("folder"),
           "created": n.get("created"), "updated": n.get("updated"),
           "rev": rev_of(n), "text": text, "spans": spans, "blocks": blocks}
    for k in ("pinned", "starred", "sample"):
        if n.get(k):
            out[k] = True
    if isinstance(n.get("links"), dict) and n["links"]:
        out["links"] = n["links"]
    col = _clean_color(n.get("color"))
    if col:
        out["color"] = col
    sec = n.get("sec")
    if isinstance(sec, dict) and sec.get("data"):
        out["sec"] = sec
    for k, v in n.items():          # 不认识的字段原样带出，别做字段漏斗
        if k not in _NOTE_KNOWN and k not in out:
            out[k] = v
    return out


def _encode_folder(f):
    out = dict(f)
    out["id"] = f.get("id")
    out["name"] = f.get("name")
    if not f.get("parent"):
        out.pop("parent", None)
    if not f.get("collapsed"):
        out.pop("collapsed", None)
    if not f.get("smart"):
        out.pop("smart", None)
    return out


def encode(data, extra=None, exported_at=None):
    """内存数据 → schema 2 快照（含 checksum）。`extra` 里的键会一起算进校验和。

    **内置说明笔记（`sample`）不导出**：它是程序自带的一份使用说明，不是用户的内容
    —— 换机、存档、给别人一份，带过去都只是噪音（对面一导入就多出一篇讲过时的
    快捷键表，还得跟本机自带的那篇较劲）。导入后由 `overwrite` 按当前版本重建一篇，
    永远是最新的排版。判据只认 `sample` 这个标记：用户自己写的笔记哪怕标题也叫
    「欢迎使用素笺」，没打标记就照常导出（改名 = 收下它，保护解除的规则见
    store._mark_sample_note）。

    「系统」文件夹同理不导出：它和里面的说明笔记是一套的，导入时由
    `_ensure_system_folder` 补回来。把它写进备份只会让对面多出一格空文件夹。
    """
    settings = data.get("settings") or {}
    prefs = dict((k, settings[k]) for k in PREFS_KEYS if k in settings)
    payload = {
        "format": FORMAT,
        "schema": SCHEMA,
        "exported_at": exported_at or now_iso(),
        "prefs": prefs,
        "styles": dict(data.get("styles") or {}),
        "folders": [_encode_folder(f) for f in (data.get("folders") or [])
                    if isinstance(f, dict) and f.get("id") != SYSTEM_FOLDER],
        "notes": [_encode_note(n) for n in (data.get("notes") or [])
                  if isinstance(n, dict) and not n.get("sample")],
    }
    if extra:
        payload.update(extra)
    payload["checksum"] = digest(payload)
    return payload


def _write_file(path, data):
    """按后缀决定要不要套一层 gzip 落盘（`dump` 与容器导出共用）。

    `.gz` 后缀必须是**真的 gzip 文件**：文件管理器、解压工具、以及 backup.load
    的魔数识别都按这个约定来。gzip 头里 mtime 固定 0、文件名也不写 —— GzipFile
    默认会把 `fileobj.name` 塞进头里，那样「同样的数据导出两次」字节就不一样
    （连改个文件名存档都算新版本）。
    """
    if str(path).lower().endswith(".gz"):
        with open(path, "wb") as raw:
            with gzip.GzipFile(filename="", fileobj=raw, mode="wb",
                               compresslevel=6, mtime=0) as gz:
                gz.write(data)
    else:
        with open(path, "wb") as f:
            f.write(data)
    return os.path.getsize(path)


def dump(path, payload):
    """落盘。`.gz` 后缀走 gzip（体积通常只有 1/3~1/5），其它按明文写。

    内部自动快照（pending / 升级前备份）、以及用户显式要的明文 `.plain.json`
    都走这里，写出来的仍是原来的老格式，不套容器。
    """
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    return _write_file(path, data)


# ---------- 导出容器：编码（空口令）/ 可选口令加密 ----------
#
# 需求：导出的备份即使被文本编辑器打开，也不该是一眼能读的笔记正文；导入时
# 程序自己认出来、自己还原，用户不用记任何开关。
#
#   * 空口令 = **编码态**（mode="coded"）：密钥从一个公开常量 + 文件头里的 salt
#     推出来，任何人拿着程序都能还原。它的作用是「不让内容裸奔」，不是保密。
#   * 非空口令 = **加密态**（mode="sealed"）：PBKDF2 派生，口令错了打不开。
#     允许留空就是上面那条路径，导出时不需要被迫设一个口令。
#
# 两种模式共用同一套结构，差别只有「密钥怎么来」，所以还原逻辑只有一份：
# 先验 tag（Encrypt-then-MAC，硬拦），再解 data，再 gunzip，最后 json.loads
# 交回原来的 decode()。encode / decode / overwrite / merge 全都不用动。
#
# 顺序是「先压后加」：密文的字节熵很高，压了也是白压（还会白涨 base64 的 33%）。
# gzip 沿用 dump() 的 filename="" / mtime=0，压缩结果可复现。


class NeedPassword(BackupError):
    """容器是加密态，导入前得先问用户要口令。单独一类，界面才知道要弹框。"""


def _b64e(b):
    return base64.b64encode(b).decode("ascii")


def _b64d(s):
    return base64.b64decode((s or "").encode("ascii"))


def _codec_subkeys(key):
    """加解密 / 认证用两把不同的子密钥，别把 PBKDF2 输出直接当密钥流。"""
    return (hmac.new(key, b"sujian.enc", hashlib.sha256).digest(),
            hmac.new(key, b"sujian.mac", hashlib.sha256).digest())


def _codec_stream(enc_key, nonce, n):
    """SHA256 当伪随机函数的 CTR 模式：每块 = SHA256(enc_key || nonce || 计数器)。"""
    out = bytearray()
    counter = 0
    while len(out) < n:
        out += hashlib.sha256(enc_key + nonce + struct.pack(">Q", counter)).digest()
        counter += 1
    return bytes(out[:n])


def _codec_xor(a, b):
    """大整数异或，比 Python 逐字节循环快一个数量级（长备份差别明显）。"""
    return (int.from_bytes(a, "big") ^ int.from_bytes(b, "big")).to_bytes(len(a), "big")


def _codec_key(blob, password):
    salt = _b64d(blob.get("salt"))
    if blob.get("mode") == "coded":
        # 公开常量派生：salt 就在文件头里，谁都能算出同一把 key —— 这正是「编码」
        # 而非「加密」的定义。留着 salt 是为了两种模式共用一套头结构。
        return hashlib.sha256(_DOMAIN + b"|coded|" + salt).digest()
    iters = int(blob.get("iter") or CONTAINER_ITERATIONS)
    if not (_MIN_ITER <= iters <= _MAX_ITER):
        # 头里的迭代数在验 tag 之前就能被改：改到 10 亿会把导入直接卡死。
        # 这里设上下限，纯粹是防这种DoS，不是安全边界（伪造 tag 仍需要口令）。
        raise BackupError("备份头里的派生参数不合法，文件可能已损坏。")
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                               salt, iters, 32)


def _codec_aad(blob):
    """认证串覆盖**全部**头字段 + 密文，含 format / mode / iter / salt / nonce。

    用长度前缀而不是 b"|".join：头里一旦有变长字段（口令提示之类），拼接就会有
    「a|bc」和「ab|c」撞车的歧义。长度前缀把每个字段的边界写死，改一个字节、
    改模式、把 iter 调小、把 format 改成明文格式，tag 都会对不上。
    """
    parts = (_DOMAIN,
             str(blob.get("format") or "").encode("utf-8"),
             str(blob.get("container") or "").encode("ascii"),
             str(blob.get("mode") or "").encode("ascii"),
             str(blob.get("iter") or "").encode("ascii"),
             _b64d(blob.get("salt")), _b64d(blob.get("nonce")),
             _b64d(blob.get("data")))
    return b"".join(struct.pack(">I", len(x)) + x for x in parts)


def seal_payload(payload, password=""):
    """快照 dict → 容器 dict。`password` 为空就是编码态，导入无需口令。"""
    if not isinstance(payload, dict):
        raise BackupError("要导出的快照不是对象。")
    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", fileobj=buf, mode="wb",
                       compresslevel=6, mtime=0) as gz:
        gz.write(raw)
    gz_bytes = buf.getvalue()

    mode = "sealed" if password else "coded"
    salt = os.urandom(_SALT_LEN)
    nonce = os.urandom(_NONCE_LEN)
    blob = {"format": CONTAINER_FORMAT, "container": CONTAINER_VERSION,
            "mode": mode,
            "iter": CONTAINER_ITERATIONS if mode == "sealed" else 0,
            "salt": _b64e(salt), "nonce": _b64e(nonce),
            "data": "", "tag": ""}
    enc_key, mac_key = _codec_subkeys(_codec_key(blob, password or ""))
    blob["data"] = _b64e(_codec_xor(
        gz_bytes, _codec_stream(enc_key, nonce, len(gz_bytes))))
    blob["tag"] = _b64e(hmac.new(mac_key, _codec_aad(blob),
                                 hashlib.sha256).digest())
    return blob


def dump_container(path, payload, password=""):
    """导出一份编码 / 加密备份，`.gz` 路径下整份文件是真 gzip（默认的 `.json.gz`）。

    两层 gzip 各管一件事，别合并：
      * **内层**（seal_payload 里）：把快照明文压小了再编码 —— 密文压不动，
        所以必须「先压后加」，这层决定备份体积；
      * **外层**（这里）：用户要的 `.json.gz` 文件格式。解压后是容器 JSON，
        只有 format / mode / 一串 data，看不到笔记正文。外层压的是 base64
        高熵字节、省不了多少体积，但后缀写了 `.gz` 就得是真 gzip，否则资源
        管理器、解压工具和 load() 的魔数识别会一起错位。

    剥壳顺序由 load() 负责：先按 `\x1f\x8b` 魔数 gunzip → json.loads → 认出
    容器 → unseal_payload 验 tag → 内层 gunzip。所以旧版 `.json.gz`（gzip 里
    直接是明文快照 JSON）和新的容器格式共用同一个后缀，靠内容区分。
    """
    blob = seal_payload(payload, password)
    return _write_file(path, json.dumps(blob, ensure_ascii=False,
                                       indent=2).encode("utf-8"))


def unseal_payload(blob, password=None):
    """容器 dict → 快照 dict。先验认证再解密；任何一步不过都抛 BackupError。"""
    if not isinstance(blob, dict) or blob.get("format") != CONTAINER_FORMAT:
        raise BackupError("不是素笺的备份容器。")
    try:
        ver = int(blob.get("container") or 0)
    except Exception:
        ver = 0
    if ver != CONTAINER_VERSION:
        raise BackupError(
            "这份备份来自更新的程序版本（容器 v%s，本程序只认到 v%d）。\n"
            "请先升级程序，再用它导入。" % (blob.get("container"), CONTAINER_VERSION))
    mode = blob.get("mode")
    if mode not in ("coded", "sealed"):
        raise BackupError("备份容器的模式不认识，文件可能已损坏。")
    if mode == "sealed" and not password:
        raise NeedPassword("这份备份设了口令，请输入口令后重试。")
    try:
        key = _codec_key(blob, password or "")
        enc_key, mac_key = _codec_subkeys(key)
        want = _b64d(blob.get("tag"))
        got = hmac.new(mac_key, _codec_aad(blob), hashlib.sha256).digest()
    except BackupError:
        raise
    except Exception:
        raise BackupError("备份文件头损坏。")
    if not hmac.compare_digest(want, got):
        # 口令错和文件被改对攻击者必须是同一句话，别给区分的线索。
        # 且这是**硬拦**：decode() 的 checksum 只警告、允许强行继续，那是完整性
        # 提示，跟这里的认证完全不是一回事。
        raise BackupError("口令不正确，或备份文件已损坏。")
    try:
        ct = _b64d(blob["data"])
        gz = _codec_xor(ct, _codec_stream(enc_key, _b64d(blob["nonce"]), len(ct)))
        return json.loads(gzip.decompress(gz).decode("utf-8-sig"))
    except Exception:
        raise BackupError("备份内容已损坏，无法还原。")


def is_container(raw):
    """文件里读出来的东西是不是本模块的容器（识别按内容，不按后缀）。"""
    return isinstance(raw, dict) and raw.get("format") == CONTAINER_FORMAT


def load(path, password=None):
    """读文件 → dict。压缩与否看**魔数**而不是后缀（用户改过名也照样能读）。

    容器（编码 / 加密）在这里自动剥壳还原成普通快照 dict，调用方拿到的形状
    和读一份老明文备份完全一样 —— 所以 decode() 及后面所有逻辑都不用改。
    `password` 只加密态用得着；加密态没给口令时抛 NeedPassword。
    """
    try:
        with open(path, "rb") as f:
            head = f.read(2)
    except Exception as e:
        raise BackupError("文件打不开：\n%s" % e)
    try:
        if head == b"\x1f\x8b":
            with gzip.open(path, "rb") as f:
                raw = f.read()
        else:
            with open(path, "rb") as f:
                raw = f.read()
        obj = json.loads(raw.decode("utf-8-sig"))
    except UnicodeDecodeError:
        raise BackupError("文件不是 UTF-8 文本，可能不是素笺的备份文件。")
    except ValueError as e:
        raise BackupError("不是有效的 JSON：\n%s" % e)
    except Exception as e:
        raise BackupError("读取失败：\n%s" % e)
    if is_container(obj):
        return unseal_payload(obj, password)
    return obj


# ---------- 解码（导入） ----------

def decode(raw):
    """任意版本的备份 → (统一形状, warnings)。

    统一形状：`{exported_at, prefs, styles, folders, notes}`，
    其中 notes 的 `content` 已经还原成 runs，可以直接落库。
    v1（老备份 / pending 兜底）、v2 都走这里；已经是统一形状的 dict 再解一次也不出错。
    结构性问题抛 BackupError，可容忍的问题（校验和不符）放进 warnings 由界面问用户。
    """
    if not isinstance(raw, dict):
        raise BackupError("不是有效的备份文件（顶层不是对象）。")
    fmt = raw.get("format")
    if isinstance(fmt, str) and fmt and fmt != FORMAT:
        # 别的程序的 JSON 恰好也有 folders / notes 时，别把它当自己的备份读进来
        raise BackupError("这不是素笺的备份文件（format 是 %r，应为 %r）。"
                          % (fmt, FORMAT))
    folders = raw.get("folders")
    notes = raw.get("notes")
    if not isinstance(folders, list) or not isinstance(notes, list):
        raise BackupError("不是有效的备份文件（缺少 folders / notes 列表）。")

    schema = raw.get("schema") or raw.get("_version") or 1
    try:
        schema = int(schema)
    except Exception:
        schema = 1
    if schema > SCHEMA:
        raise BackupError(
            "这份备份来自更新的程序版本（schema %d，本程序只认到 %d）。\n"
            "请先升级程序，再用它导入 —— 硬读下去会丢掉新版本才有的内容。"
            % (schema, SCHEMA))

    warnings = []
    chk = raw.get("checksum")
    if isinstance(chk, str) and chk:
        probe = dict((k, v) for k, v in raw.items() if k != "checksum")
        if digest(probe) != chk:
            warnings.append("备份文件的校验和对不上：文件可能在传输中被截断，"
                            "或被手工改过。内容不保证完整。")

    prefs = raw.get("prefs")
    if not isinstance(prefs, dict):
        # v1：settings 里外观和会话混在一起，按键名分拣，state 一概不要
        settings = raw.get("settings")
        settings = settings if isinstance(settings, dict) else {}
        prefs = dict((k, settings[k]) for k in PREFS_KEYS if k in settings)
    styles = raw.get("styles")
    styles = styles if isinstance(styles, dict) else {}

    out_folders = [_decode_folder(f) for f in folders if isinstance(f, dict)]
    out_notes = []
    for n in notes:
        rec = _decode_note(n)
        if rec is not None:
            out_notes.append(rec)
    return {"exported_at": raw.get("exported_at") or raw.get("_exported_at") or "",
            "schema": schema, "prefs": dict(prefs), "styles": dict(styles),
            "folders": out_folders, "notes": out_notes}, warnings


def _decode_folder(f):
    out = dict(f)
    out["id"] = f.get("id")
    out["name"] = f.get("name")
    if not isinstance(out.get("parent"), str) or not out["parent"]:
        out.pop("parent", None)
    if not f.get("smart"):
        out.pop("smart", None)
    if not f.get("collapsed"):
        out.pop("collapsed", None)
    return out


def _decode_note(n):
    if not isinstance(n, dict):
        return None
    content = n.get("content")
    if isinstance(content, list):
        runs = content
    elif "text" in n or "spans" in n or "blocks" in n:
        runs = spans_to_runs(n.get("text"), n.get("spans"), n.get("blocks"))
    else:
        return None             # 既没有正文也没有正文片段 → 空壳，别导进来
    out = dict(n)
    for k in _V2_ONLY:
        out.pop(k, None)
    out["content"] = runs
    if not isinstance(out.get("id"), str) or not out["id"]:
        out["id"] = uid()
    return out


# ---------- 落库前整理：覆盖 / 合并 ----------

def _clean_note(n, folder_id, now):
    """一条（已解码的）笔记 → 可直接落库的笔记（类型都规范化，未知字段留着）。"""
    out = dict(n)
    for k in _V2_ONLY:
        out.pop(k, None)
    out["folder"] = folder_id
    out["content"] = list(n.get("content") or [])
    out["pinned"] = bool(n.get("pinned"))
    out["starred"] = bool(n.get("starred"))
    out["created"] = n.get("created") or now
    out["updated"] = n.get("updated") or now
    if n.get("sample"):
        out["sample"] = True
    else:
        out.pop("sample", None)
    links = n.get("links")
    if isinstance(links, dict) and links:
        out["links"] = links
    else:
        out.pop("links", None)
    col = _clean_color(n.get("color"))
    if col:
        out["color"] = col
    else:
        out.pop("color", None)
    sec = n.get("sec")
    if isinstance(sec, dict) and sec.get("data"):
        out["sec"] = sec
        out["content"] = []          # 以密文为准，别被顺手塞进来的明文覆盖
    else:
        out.pop("sec", None)
    return out


def merge_prefs(cur_settings, prefs, adopt=True):
    """把外观偏好并进当前 settings。

    只碰 PREFS_KEYS：`tabs` / `side_w` / `alarm_fired` 这些是本机的（存本机 id 与
    像素值），从别的机器搬过来只会得到悬空引用 —— 这就是它们不导出的理由。
    合并导入不采纳备份外观（adopt=False）：顺手改用户字号很意外，
    「我要恢复成备份的样子」应该走覆盖导入。
    """
    out = dict(cur_settings or {})
    if adopt and isinstance(prefs, dict):
        for k in PREFS_KEYS:
            if k in prefs:
                out[k] = prefs[k]
    return out


def merge_styles(cur_styles, styles):
    """样式表取并集（备份优先）：正文里的 c_/u_ 标签名引用了它，缺一条就掉色。

    颜色标签的名字就是颜色本身（`c_#ff0000`），两边同名的定义必然相同，
    所以「备份优先」在冲突时也不会改掉本机的什么。
    """
    out = dict(cur_styles or {})
    if isinstance(styles, dict):
        out.update(styles)
    return out


def overwrite(d, cur=None):
    """覆盖式导入：内容全替换，外观采用备份里的值。

    说明笔记单独补：`encode` 不导出它（见那里的理由），所以「覆盖」之后库里不能真的
    没有这篇 —— 它是唯一一份使用说明，缺了就再也找不回来。新备份里没有、老备份里
    有，两种都照顾：已经带着（老备份）就原样用，没带就当场按**当前版本**重建一篇，
    排版和快捷键表跟着程序走。
    """
    cur = cur or {}
    now = time.time() * 1000
    folders = [dict(f) for f in (d.get("folders") or [])
               if isinstance(f, dict) and f.get("id") and f.get("name")]
    box = {"folders": folders}
    _ensure_smart_folders(box)          # 三个智能分类补齐（含顺序）
    folders = box["folders"]
    normalize_folder_parents(folders)   # 悬空 / 成环 / 过深的 parent 就地清掉
    ids = set(f["id"] for f in folders) | set(SMART_FOLDERS)
    if "f_work" not in ids:
        folders.append({"id": "f_work", "name": "工作"})
        ids.add("f_work")
    notes = []
    for n in d.get("notes") or []:
        if not isinstance(n, dict) or not isinstance(n.get("content"), list):
            continue
        fid = n.get("folder") if n.get("folder") in ids else "f_work"
        rec = _clean_note(n, fid, now)
        if not rec.get("id"):
            rec["id"] = uid()
        notes.append(rec)
    if not any(n.get("sample") for n in notes):
        notes.insert(0, new_sample_note(now))
    out = {"folders": folders, "notes": notes,
           "current_folder": "f_all", "current_note": None,
           "settings": merge_prefs(cur.get("settings"), d.get("prefs"), adopt=True),
           "styles": merge_styles(cur.get("styles"), d.get("styles"))}
    # 备份里没有「系统」文件夹（encode 不导出），说明笔记也由上面重建 ——
    # 这一句把它俩接上：补出文件夹、把 sample 收进去，钉在第一个真实文件夹位
    _ensure_system_folder(out)
    return out


def merge(d, cur):
    """合并式导入：保留现有内容，只把备份里「本机还没有的」追加进来。

    三条防呆：
      1. folder id 冲突**不再**直接丢弃备份里那个文件夹，而是建映射：
         id 相同 → 复用；同名 → 视为同一个；都没有 → 追加。`notes.folder`
         和 `parent` 一律按映射重写（以前只换笔记 id 不改引用，笔记会落错文件夹）。
      2. 笔记按 `rev`（正文指纹）去重：同一个备份导两次不会翻倍。
      3. 外观保留本机（见 merge_prefs）；样式表取并集。
    """
    cur = cur or {}
    now = time.time() * 1000
    folders = [dict(f) for f in (cur.get("folders") or []) if isinstance(f, dict)]
    by_id = dict((f.get("id"), f) for f in folders)
    by_name = {}
    for f in folders:
        if f.get("name"):
            by_name.setdefault(f["name"], f)
    fmap = {}           # 备份 folder id -> 本机 folder id
    for f in d.get("folders") or []:
        if not isinstance(f, dict) or not f.get("id"):
            continue
        fid = f["id"]
        if fid in SMART_FOLDERS or fid in by_id:
            fmap[fid] = fid
            continue
        same = by_name.get(f.get("name"))
        if same is not None:
            fmap[fid] = same["id"]
            continue
        nf = dict(f)
        nf.pop("smart", None)        # 导入的普通文件夹不保留智能标记
        folders.append(nf)
        by_id[fid] = nf
        if nf.get("name"):
            by_name[nf["name"]] = nf
        fmap[fid] = fid
    for f in folders:                # 父引用跟着映射走
        p = f.get("parent")
        if p and p in fmap:
            f["parent"] = fmap[p]
    box = {"folders": folders}
    _ensure_smart_folders(box)
    folders = box["folders"]
    normalize_folder_parents(folders)
    ids = set(f["id"] for f in folders) | set(SMART_FOLDERS)

    notes = [dict(n) for n in (cur.get("notes") or []) if isinstance(n, dict)]
    have_ids = set(n.get("id") for n in notes)
    have_rev = {}
    for n in notes:
        r = rev_of(n)
        if r not in have_rev:
            have_rev[r] = n.get("id")
    for n in d.get("notes") or []:
        if not isinstance(n, dict) or not isinstance(n.get("content"), list):
            continue
        r = rev_of(n)
        if r in have_rev:
            continue                 # 同一篇（内容一字不差）→ 跳过，导两次也只算一次
        fid = n.get("folder")
        fid = fmap.get(fid, fid)
        if fid not in ids:
            fid = "f_work"
        rec = _clean_note(n, fid, now)
        nid = rec.get("id")
        if not nid or nid in have_ids:
            nid = uid()              # id 撞了但内容不同：另起一个 id，两边都留
        rec["id"] = nid
        notes.append(rec)
        have_ids.add(nid)
        have_rev[r] = nid
    out = {"folders": folders, "notes": notes,
           "current_folder": cur.get("current_folder", "f_all"),
           "current_note": cur.get("current_note"),
           "settings": merge_prefs(cur.get("settings"), d.get("prefs"), adopt=False),
           "styles": merge_styles(cur.get("styles"), d.get("styles"))}
    _ensure_system_folder(out)      # 本机老库里可能还没这一格（备份也不会带过来）
    return out


def summary(d):
    """导入确认框要用的两行字：(内容行, 时间行)。"""
    return ("%d 个文件夹、%d 条笔记" % (len(d.get("folders") or []),
                                       len(d.get("notes") or [])),
            human_time(d.get("exported_at")))
