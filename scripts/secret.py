# -*- coding: utf-8 -*-
"""素笺 —— 笔记加密（纯标准库，不 import tkinter）。

为什么不引第三方库：本项目的卖点是「零第三方依赖 + 单目录 + python scripts/notes_app.py
直接跑」，给笔记加个口令不值得为此破例。拆成独立模块是为了它能在 tests/ 里不建 GUI 直接断言
——加密代码最怕「看上去能跑、其实每次都用同一段密钥流」。

方案（口令 → 密文）：
    salt     每次新建口令时随机 16 字节，之后**固定不变**（换 salt 等于换密钥，
             已存的密文就再也解不开了，所以重新加密时必须复用同一组 salt/iter）
    key      PBKDF2-HMAC-SHA256(口令, salt, iter=20 万) → 32 字节
    enc_key  HMAC-SHA256(key, b"enc")     密钥流用
    mac_key  HMAC-SHA256(key, b"mac")     认证用
    密文     = 明文 XOR SHA256(enc_key || nonce || 计数器)，即 SHA256 当伪随机函数的 CTR 模式
    认证     = HMAC-SHA256(mac_key, v || iter || salt || nonce || 密文)

先验认证再解密：口令错、密文被改动，都会在解密之前就报 BadPassword，
不会把一段乱码当成正文渲染出来。

这当然不是"军用级"加密，但它是**正确拼装**的对称加密：随机 nonce、先认证后解密、
口令与派生密钥都不落盘、篡改可发现。威胁模型是"捡到 notes_data.db 的人"，
不是能离线跑几亿次 PBKDF2 的国家级攻击者。
"""
import os
import hmac
import base64
import hashlib
import struct
import re

VERSION = 1
ITERATIONS = 200000          # 20 万次：本机约 60~80ms，解锁时一次性付出，可接受
SALT_LEN = 16
NONCE_LEN = 12
_KEY_LEN = 32


class BadPassword(Exception):
    """口令错误，或密文被改动 / 截断（两者对外无法区分，也不该区分）。"""


def _b64e(b):
    return base64.b64encode(b).decode("ascii")


def _b64d(s):
    return base64.b64decode((s or "").encode("ascii"))


def _subkeys(key):
    enc = hmac.new(key, b"enc", hashlib.sha256).digest()
    mac = hmac.new(key, b"mac", hashlib.sha256).digest()
    return enc, mac


def _keystream(enc_key, nonce, n):
    """生成 n 字节密钥流：每块 = SHA256(enc_key || nonce || 计数器)。"""
    out = bytearray()
    counter = 0
    while len(out) < n:
        out += hashlib.sha256(enc_key + nonce + struct.pack(">Q", counter)).digest()
        counter += 1
    return bytes(out[:n])


def _xor(data, stream):
    return bytes(a ^ b for a, b in zip(data, stream))


def _auth(mac_key, blob):
    """认证标签覆盖全部头部字段：改版本号 / 迭代数 / salt / nonce 都会被识破。"""
    return hmac.new(mac_key, b"|".join([
        b"v1",
        str(blob["iter"]).encode("ascii"),
        _b64d(blob["salt"]),
        _b64d(blob["nonce"]),
        _b64d(blob["data"]),
    ]), hashlib.sha256).digest()


def derive_key(password, salt, iterations=ITERATIONS):
    """口令 + salt → 32 字节主密钥。"""
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                               salt, int(iterations), _KEY_LEN)


def seal_and_key(plain_text, password):
    """新建密文，同时把派生出的密钥交回去 —— 解锁之后的每次编辑都要复用它，
    不能再跑一遍 20 万次 PBKDF2。"""
    salt = os.urandom(SALT_LEN)
    key = derive_key(password, salt, ITERATIONS)
    blob = reseal_with_key(plain_text, {"salt": _b64e(salt), "iter": ITERATIONS}, key)
    return blob, key


def reseal_with_key(plain_text, old_blob, key):
    """用**已缓存的密钥**重新加密（编辑时每敲几个字就要做一次，不能再跑 PBKDF2）。

    salt / iter 必须沿用旧 blob：密钥是从「口令 + 那个 salt」推出来的，
    换一组 salt 只是让以后用口令再也解不开（密钥没变，密文仍然对）。
    """
    salt_b64 = old_blob["salt"]
    iters = int(old_blob.get("iter") or ITERATIONS)
    nonce = os.urandom(NONCE_LEN)
    enc_key, mac_key = _subkeys(key)
    data = plain_text.encode("utf-8")
    ct = _xor(data, _keystream(enc_key, nonce, len(data)))
    blob = {"v": VERSION, "iter": iters, "salt": salt_b64,
            "nonce": _b64e(nonce), "data": _b64e(ct), "tag": ""}
    blob["tag"] = _b64e(_auth(mac_key, blob))
    # ⚠️ 恢复槽必须跟着走：它封装的是「这把 key」，不是这一版正文密文。
    # 每次编辑 / 切走笔记都会走到这里，漏掉这一句 = 用户刚抄下的码当场作废、
    # 两个「用恢复码恢复」入口因为 has_recovery() 变 False 而一起消失。
    # v2 槽的 AAD 只绑沿用不变的主 salt，所以原样带过来验签依然成立。
    old_rc = old_blob.get(RECOVERY_KEY)
    if isinstance(old_rc, dict):
        blob[RECOVERY_KEY] = dict(old_rc)
    return blob


def _verify(blob, key):
    """先认证后解密：拿已有主密钥验主 tag，错一字节就别去碰密文。"""
    _, mac_key = _subkeys(key)
    try:
        want = _b64d(blob.get("tag"))
        got = _auth(mac_key, blob)
    except Exception:
        raise BadPassword("密文头损坏")
    if not hmac.compare_digest(want, got):
        raise BadPassword("口令不正确")


def open_with_key(blob, key):
    """已有主密钥时校验并解密，返回明文。

    口令路径（open_sealed）与恢复码路径共用这一段：恢复码只是「另一种拿到
    同一把 key 的办法」，拿到之后仍然要过主 tag、走同一套解密，不能绕过去。
    """
    _verify(blob, key)
    enc_key, _ = _subkeys(key)
    try:
        ct = _b64d(blob["data"])
        nonce = _b64d(blob["nonce"])
        return _xor(ct, _keystream(enc_key, nonce, len(ct))).decode("utf-8")
    except BadPassword:
        raise
    except Exception:
        raise BadPassword("密文损坏")


def open_sealed(blob, password):
    """校验并解密，返回 (明文, 密钥)。口令错 / 密文被改动都抛 BadPassword。"""
    try:
        iters = int(blob.get("iter") or ITERATIONS)
        salt = _b64d(blob["salt"])
    except Exception:
        raise BadPassword("密文头损坏")
    key = derive_key(password, salt, iters)
    return open_with_key(blob, key), key


def is_blob(x):
    """判断某个字段像不像本模块产出的密文。"""
    return isinstance(x, dict) and bool(x.get("data")) and bool(x.get("salt"))


# 密文块里「标题首字」的字段名。锁着的笔记在列表里要显示「首字 + 星号」，
# 而那时正文一个字符都读不出来 —— 只能把这个字存在密文块里跟着密文走
# （换密文它跟着换、取消加密它跟着消失、导出备份也带着）。
# 这是全库唯一「刻意留下的明文痕迹」，泄漏量固定为一个汉字。
# 注意它不参与 _auth 认证：改它顶多让遮蔽标题显示错字，没有伪造明文的可能。
HINT_KEY = "hint"


def stamp_hint(blob, head):
    """把标题首字写进密文块（head 取正文第一行的第一个字符，可为空）。"""
    if isinstance(blob, dict):
        blob[HINT_KEY] = (head or "")[:1]
    return blob


def hint_of(blob):
    """读出密文块里存的标题首字（没有则空串，如升级前就加密的老笔记）。"""
    return (blob.get(HINT_KEY) or "") if isinstance(blob, dict) else ""


# ---------- 每篇独立的恢复码 ----------
# 恢复码是「同一把主密钥的第二份封装」：正文密钥 key 仍只由口令派生，这里用
# 恢复码派生一把 KEK，把 key 异或封进 sec["recovery"]。程序只落封装后的密文，
# 恢复码本身绝不落盘、绝不进 settings / 备份的 prefs。
#
# 与口令路径的关系：open_recovery() 只负责取回 key，取回后仍要 open_with_key()
# 过主 tag 才能得到明文。忘口令恢复完必须**当场重设新口令**，否则磁盘上还是
# 那把旧密文，下次打开照样要旧口令。
#
# 兼容性：_auth() 只 join 固定的 5 个字段，新增的 recovery 不参与主认证，
# 所以老版本程序读新 blob 照常能按口令解开；is_blob() 只看 data + salt，不变。
RECOVERY_KEY = "recovery"
RECOVERY_VERSION = 2              # v1 的 AAD 绑了主 tag/data，编辑一次就永久失效
RC_DOMAIN = b"sujian.note.recovery.v1"
RC_ITERATIONS = 600000       # 恢复码是高熵的，但验一次仍付出与导出容器同级的代价
RC_CODE_BYTES = 15           # 120 bit → 24 个 base32 字符，正好整除 5、无 padding
RC_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"   # Crockford：去掉易混的 I/L/O/U
RC_GROUP = 6


def _rc_chars(raw):
    """15 字节按 5 bit 一组映到自定义字母表，产出 24 个字符。"""
    bits = int.from_bytes(raw, "big")
    return "".join(RC_ALPHABET[(bits >> (i * 5)) & 31]
                   for i in range(RC_CODE_BYTES * 8 // 5 - 1, -1, -1))


def new_recovery_code():
    """现场生成一份恢复码，返回带连字符的显示串。生成 ≠ 保存：调用方负责展示。"""
    s = _rc_chars(os.urandom(RC_CODE_BYTES))
    return "-".join(s[i:i + RC_GROUP] for i in range(0, len(s), RC_GROUP))


def norm_recovery_code(text):
    """去连字符 / 空格、大写、把易混的 I/L/O 归一；格式不合法返回 None。"""
    if not isinstance(text, str):
        return None
    s = text.upper().replace("-", "").replace(" ", "")
    s = s.replace("I", "1").replace("L", "1").replace("O", "0")
    if len(s) != RC_CODE_BYTES * 8 // 5 or any(c not in RC_ALPHABET for c in s):
        return None
    return s


def recovery_id(code):
    """公开、非秘密的编号：只用于对照 / 分组，绝不参与密钥派生。

    128 bit 熵下公开 8 个十六进制字符不会让离线爆破变得可行；它的作用是让
    用户把「哪张码属于哪篇笔记」对上，以及输错码时立刻明确报错、连 KDF 都不跑。
    """
    c = norm_recovery_code(code)
    if not c:
        return ""
    return hashlib.sha256(b"sujian.rcid.v1" + c.encode("ascii")).hexdigest()[:8].upper()


def recovery_id_display(rid):
    rid = str(rid or "")
    return (rid[:4] + " " + rid[4:]).strip()


# 「复制恢复码」给用户的整条凭证：[笔记名称]-[凭证编号]-[恢复码]。
# 三段各自包在方括号 [ ] 里、用连字符 - 连接，是为了让笔记名称里出现的空格 /
# 下划线 / 连字符不至于被误当分隔符；它们只影响剪贴板文本，跟密封 / 验签 /
# 落盘格式一律无关。
RC_RECORD_SEP = "-"
RC_RECORD_L = "["
RC_RECORD_R = "]"


def _rc_record_field(value):
    """凭证串的一段：压掉换行与多余空白，并把方括号换成圆括号。

    笔记名称是用户随便起的，可能自带 [ ] 或下划线；不净化就会把「三段」撑成
    四段，整条凭证粘回输入框时再也拆不回来。这里只动展示文本，不碰任何密文。
    """
    s = " ".join(str(value or "").split())
    return s.replace(RC_RECORD_L, "(").replace(RC_RECORD_R, ")")


def format_recovery_record(note_title, rid, code):
    """生成 [笔记名称]-[凭证编号]-[恢复码] 三段凭证串，供复制按钮使用。

    凭证编号用弹框里印给用户看的展示形式（前 4 + 空格 + 后 4），方便对照；
    恢复码保留带连字符的显示串，抄到密码管理器里仍按 6 位一组好认。
    """
    fields = (_rc_record_field(note_title),
              _rc_record_field(recovery_id_display(rid)),
              _rc_record_field(code))
    return RC_RECORD_SEP.join(RC_RECORD_L + f + RC_RECORD_R for f in fields)


def parse_recovery_record(text):
    """从整条凭证串里取回恢复码那段；不是凭证串就原样返回，交给 norm 判。

    复制按钮给的是三段串，用户多半会整条贴进输入框；不在这里拆开的话，
    「复制」出来的东西反而粘不回去。只认方括号里能被 norm_recovery_code
    认下的段，取最后一段（恢复码在末尾）；纯码 / 乱码都原样放行，由 norm
    给出原来的「格式不对」提示，不新增一条误判路径。
    """
    if not isinstance(text, str) or RC_RECORD_L not in text:
        return text
    for seg in reversed(re.findall(r"\[([^\[\]]*)\]", text)):
        if norm_recovery_code(seg):
            return seg
    return text


def recovery_of(blob):
    rc = blob.get(RECOVERY_KEY) if isinstance(blob, dict) else None
    return rc if isinstance(rc, dict) else None


def has_recovery(blob):
    rc = recovery_of(blob)
    return bool(rc and rc.get("wrap") and rc.get("tag"))


def _rc_parts(blob, rc, tail):
    """长度前缀拼接：salt / wrap 变长，b"|".join 会有「a|bc」「ab|c」歧义。"""
    parts = (RC_DOMAIN,
             str(rc.get("rid") or "").encode("ascii"),
             str(rc.get("v") or "").encode("ascii"),
             str(rc.get("iter") or "").encode("ascii"),
             _b64d(rc.get("salt")),
             _b64d(rc.get("wrap"))) + tuple(tail)
    return b"".join(struct.pack(">I", len(x)) + x for x in parts)


def _rc_aad(blob, rc):
    """v2 槽的认证串：域 / 编号 / 版本 / iter / 槽 salt / wrap / **主 salt**。

    ⚠️ 只绑主 salt，绝不绑主 tag / data：每次编辑或切走笔记都会 reseal（换 nonce
    → 换 data → 换 tag），绑上去等于让恢复槽在第一次敲字之后就永久失效，用户
    手上那张码再也用不了。主 salt 是 reseal_with_key() 明确沿用的字段，重加密
    前后不变，既能区分不同笔记，又跟得住正文更新。

    防「把 A 的槽搬到 B」靠主 salt 不同；防主密文篡改由主 tag 负责；取回 key
    之后还要过 open_with_key() 的主 tag 校验，三处各管一件事，不靠 AAD 兜底。
    不绑 note_id：备份合并遇到同 id 冲突会换新 id，绑上去会让导入后的槽失效。
    """
    return _rc_parts(blob, rc, [_b64d(blob.get("salt"))])


def _rc_aad_legacy(blob, rc):
    """v1 槽（本功能早期版本）的 AAD，仅用于兼容读取，新槽不再生成。"""
    return _rc_parts(blob, rc, [_b64d(blob.get("tag")),
                                _b64d(blob.get("data"))])


def seal_recovery(blob, key, code):
    """给 blob 挂一份恢复槽，返回公开编号。**每次调用都换 salt**。

    key 必须是刚 seal_and_key 生成、或刚用口令 / 旧恢复码验证过的那把；调用方
    绝不能拿编辑缓存里的 key 静默换码（那是把恢复通道换成别人码的入口）。
    """
    c = norm_recovery_code(code)
    if not c:
        raise ValueError("恢复码格式不对")
    if not isinstance(blob, dict) or not blob.get("tag") or not blob.get("data"):
        raise ValueError("正文密文还没生成")
    if not isinstance(key, (bytes, bytearray)) or len(key) != _KEY_LEN:
        raise ValueError("主密钥长度不对")
    salt = os.urandom(SALT_LEN)
    kek = hashlib.pbkdf2_hmac("sha256", c.encode("ascii"), salt,
                              RC_ITERATIONS, _KEY_LEN)
    wrap = _xor(bytes(key), kek)
    rc = {"v": RECOVERY_VERSION,
          "rid": recovery_id(c),
          "iter": RC_ITERATIONS,
          "salt": _b64e(salt),
          "wrap": _b64e(wrap)}
    _, rc_mac = _subkeys(kek)
    rc["tag"] = _b64e(hmac.new(rc_mac, _rc_aad(blob, rc),
                               hashlib.sha256).digest())
    blob[RECOVERY_KEY] = rc
    return rc["rid"]


def open_recovery(blob, code):
    """用恢复码取回主密钥：先验编号 → 先验槽 tag → 最后才解 wrap。"""
    rc = recovery_of(blob)
    if not has_recovery(blob):
        raise BadPassword("这篇笔记没有设置恢复码")
    c = norm_recovery_code(code)
    if not c:
        raise BadPassword("恢复码格式不对")
    if not hmac.compare_digest(str(rc.get("rid") or ""), recovery_id(c)):
        raise BadPassword("恢复码不属于这篇笔记")
    try:
        salt = _b64d(rc.get("salt"))
        iters = int(rc.get("iter") or RC_ITERATIONS)
        wrap = _b64d(rc.get("wrap"))
        want = _b64d(rc.get("tag"))
    except Exception:
        raise BadPassword("恢复槽已损坏")
    if len(wrap) != _KEY_LEN:
        raise BadPassword("恢复槽已损坏")
    kek = hashlib.pbkdf2_hmac("sha256", c.encode("ascii"), salt, iters, _KEY_LEN)
    _, rc_mac = _subkeys(kek)
    # v1 槽用旧 AAD（绑主 tag/data），v2 用新 AAD（绑主 salt）；另一版兜底再
    # 试一次，免得老库里版本字段被改过就误判成「码不对」。
    makers = ((_rc_aad_legacy, _rc_aad) if str(rc.get("v") or "1") == "1"
              else (_rc_aad, _rc_aad_legacy))
    if not any(hmac.compare_digest(
            want, hmac.new(rc_mac, make(blob, rc), hashlib.sha256).digest())
            for make in makers):
        raise BadPassword(
            "恢复码不正确，或恢复槽已被改动；若是旧版恢复槽，"
            "请解锁后用右键「重新生成本篇恢复码…」补建")
    return _xor(wrap, kek)


def drop_recovery(blob):
    """取消本篇的恢复能力（关闭恢复 / 槽损坏时用）。"""
    if isinstance(blob, dict):
        blob.pop(RECOVERY_KEY, None)
    return blob
